import json
import re

from rag_builders.common import append_payload, source_file_name
from core.charts.crash import SYSTEM_EVENT_TYPES

BINDER_LEAK_TYPES = (
    "BINDER_PROXY_HISTOGRAM",
    "BINDER_PROXY_LEAK",
    "BINDER_PROXY_LEAK_SUMMARY",
)

# 💡 신규 추가: Payload 누락 방지를 위한 핵심 바인더 에러 타입 정의
CRITICAL_BINDER_TYPES = (
    "BINDER_ONEWAY_SPAM",
    "BINDER_BUFFER_ERROR",
    "THREAD_EXHAUSTION",
    "BINDER_PROXY_LIMIT",
)

def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(str(value).replace(",", "").strip())
    except Exception:
        return default

def extract_leaked_descriptor(text: str) -> str:
    text = text or ""
    if "IIntentReceiver" in text:
        return "android.content.IIntentReceiver"
    if "IContentProvider" in text:
        return "android.content.IContentProvider"
    if "IServiceConnection" in text:
        return "android.app.IServiceConnection"
    return "Unknown"

def extract_proxy_count(warning: dict) -> int:
    for key in ("max_count", "count", "proxy_count", "max_proxy_count"):
        if warning.get(key) is not None:
            return safe_int(warning.get(key), 0)

    text = " ".join([
        str(warning.get("desc", "")),
        str(warning.get("raw", "")),
        str(warning.get("raw_info", "")),
        str(warning.get("details", "")),
    ])

    nums = [safe_int(x, 0) for x in re.findall(r"\b\d{3,7}\b", text)]
    return max(nums) if nums else 0

def is_critical_binder_event(warning: dict) -> bool:
    """핵심 신호로 앞세울 바인더 이벤트인지 판단한다.

    buffer 에러는 파서가 원인 후보로 표시했을 때만 핵심이다. 단발
    TransactionTooLargeException 처럼 국소 증상인 경우는 일반 이벤트로 내려보낸다.
    """
    warning_type = warning.get("type")
    if warning_type not in CRITICAL_BINDER_TYPES:
        return False
    if warning_type == "BINDER_BUFFER_ERROR":
        return bool(warning.get("rca_candidate"))
    return True

def is_leak_evidence(warning: dict) -> bool:
    """히스토그램이 누수 근거인지. 임계값 판정은 파서가 이미 했다."""
    if warning.get("type") in ("BINDER_PROXY_LEAK", "BINDER_PROXY_LEAK_SUMMARY"):
        return True  # 이름부터 누수인 타입
    return bool(warning.get("rca_candidate"))

def build_binder_leak_rca_docs(report_data, input_file):
    """Binder proxy 누수 RCA 문서를 만든다.

    문서를 세울 수 있는 근거는 확신 순으로 셋이다. `Too many Binders sent to
    SYSTEM` am_kill, BpBinder/ActivityManager 의 proxy 한계 경고, 그리고 파서가
    임계 초과로 표시한 histogram. 예전에는 kill 을 필수로 요구해서, 7262개짜리
    histogram 이 있어도 kill 이 없으면 문서가 하나도 안 나왔다. 도메인 규칙은
    이 셋을 모두 근거로 요구하는데 그중 하나가 늘 비어 있던 셈이다.
    """
    rca_docs = []
    binder_warnings = [
        bw for bw in (report_data.get("binder_warnings", []) or []) if isinstance(bw, dict)
    ]

    leak_warnings = sorted(
        [bw for bw in binder_warnings if bw.get("type") in BINDER_LEAK_TYPES],
        key=extract_proxy_count,
        reverse=True,
    )
    proxy_limits = [bw for bw in binder_warnings if bw.get("type") == "BINDER_PROXY_LIMIT"]
    system_kills = [
        bw for bw in binder_warnings
        if bw.get("type") == "SYSTEM_KILL"
        and "Too many Binders sent to SYSTEM" in " ".join([
            str(bw.get("desc", "")),
            str(bw.get("raw", "")),
            str(bw.get("raw_info", "")),
        ])
    ]

    strong_leaks = [bw for bw in leak_warnings if is_leak_evidence(bw)]
    if not (strong_leaks or proxy_limits or system_kills):
        return rca_docs

    top_leak = leak_warnings[0] if leak_warnings else {}
    leak_text = " ".join([
        str(top_leak.get("desc", "")),
        str(top_leak.get("raw", "")),
        str(top_leak.get("raw_info", "")),
        str(top_leak.get("details", "")),
    ])
    leaked_descriptor = extract_leaked_descriptor(leak_text)
    max_count = max(
        [extract_proxy_count(bw) for bw in leak_warnings + proxy_limits] or [0]
    )

    kill = next(
        (k for k in system_kills if k.get("process") == "com.android.phone"),
        system_kills[0] if system_kills else None,
    )
    # 한계 경고는 두 줄로 나뉜다. 개수를 실은 네이티브 줄과 프로세스 이름을 실은 am_wtf 줄.
    limit = next((p for p in proxy_limits if extract_proxy_count(p)), None) or (
        proxy_limits[0] if proxy_limits else None
    )
    named_limit = next(
        (p for p in proxy_limits if not str(p.get("process", "")).startswith("uid ")), limit
    )

    if kill:
        anchor = "am_kill"
        process = kill.get("process", "Unknown")
        time = kill.get("time") or top_leak.get("time") or "Unknown"
        trigger = kill.get("raw", kill.get("raw_info", ""))
        kill_event, kill_reason = "am_kill", "Too many Binders sent to SYSTEM"
        headline = (
            f"{process} 프로세스가 am_kill 로 강제 종료됨. "
            f"강제 종료 사유는 '{kill_reason}'. "
        )
    elif limit:
        anchor = "BINDER_PROXY_LIMIT"
        process = (named_limit or limit).get("process", "Unknown")
        time = limit.get("time") or top_leak.get("time") or "Unknown"
        trigger = limit.get("raw", limit.get("raw_info", ""))
        kill_event, kill_reason = "", ""
        headline = (
            f"{process} 가 uid {limit.get('to_uid', '?')} 로 보낸 Binder proxy 가 한계에 도달했다는 "
            f"경고가 남음. 강제 종료로는 이어지지 않음. "
        )
    else:
        anchor = "BINDER_PROXY_HISTOGRAM"
        process = top_leak.get("process", "Unknown")
        time = top_leak.get("time") or "Unknown"
        trigger = top_leak.get("raw", top_leak.get("raw_info", ""))
        kill_event, kill_reason = "", ""
        headline = "강제 종료나 한계 경고 없이, Binder Proxy Histogram 만으로 누수 정황이 확인됨. "

    if leaked_descriptor == "android.content.IIntentReceiver":
        root_cause = "IIntentReceiver Binder proxy leak"
        developer_action = "동적 BroadcastReceiver register 후 unregister 누락 여부를 점검해야 함"
    else:
        root_cause = "Binder proxy object leak"
        developer_action = "누수된 Binder interface의 acquire/release 또는 register/unregister 생명주기 점검 필요"

    wtf_count = sum(
        safe_int(w.get("count"), 0)
        for w in binder_warnings
        if w.get("type") == "SYSTEM_WTF"
        or "am_wtf" in str(w.get("raw", ""))
        or "am_wtf" in str(w.get("raw_info", ""))
    )

    metadata = {
        "source_file": source_file_name(input_file),
        "log_type": "RCA_Event",
        "rca_type": "BINDER_PROXY_LEAK_RCA",
        "time": time,
        "process": process,
        "evidence_anchor": anchor,
        "kill_event": kill_event,
        "kill_reason": kill_reason,
        "leaked_descriptor": leaked_descriptor,
        "max_proxy_count": max_count,
        "am_wtf_count_observed": wtf_count,
        "root_cause": root_cause,
        "developer_action": developer_action,
        "trigger": trigger,
        "symptom_keywords": "폰 죽음, 갑자기 죽음, 강제 종료, 시스템 크래시, SYSTEM_KILL, am_kill, crash, kill",
    }
    if limit:
        metadata["proxy_limit_uid_pair"] = f"{limit.get('from_uid', '?')} -> {limit.get('to_uid', '?')}"

    evidence = (
        f"Binder Proxy Histogram에서 {leaked_descriptor} 객체가 최대 {max_count}개까지 누수됨. "
        if strong_leaks
        else f"보유 중인 proxy 는 최대 {max_count}개로 확인됨. "
        if max_count
        else ""
    )
    document = (
        f"[원인 분석: 바인더 프록시 누수] 폰이 갑자기 죽음/강제 종료/시스템 크래시처럼 보이는 증상과 관련된 원인 분석 문서. "
        f"{headline}{evidence}"
        f"따라서 근본 원인은 단순 앱 크래시나 Native Crash가 아니라 {root_cause}에 따른 시스템 리소스 고갈로 판단됨. "
        f"개발 조치: {developer_action}."
    )

    append_payload(rca_docs, document, metadata)
    return rca_docs

def build_binder_payloads(report_data, input_file):
    rag_payload = []
    binder_warnings = report_data.get("binder_warnings", []) or []

    # 1. 누수(Leak) 계열 처리
    leak_warnings = [
        bw for bw in binder_warnings
        if isinstance(bw, dict) and bw.get("type") in BINDER_LEAK_TYPES
    ]

    for bw in leak_warnings:
        max_count = extract_proxy_count(bw)
        desc = bw.get("desc") or bw.get("raw") or bw.get("raw_info") or ""
        leaked_descriptor = extract_leaked_descriptor(desc)

        # 원본 이벤트 타입을 남긴다. 이 문서는 히스토그램을 요약한 것이라
        # type 을 BINDER_PROXY_LEAK_SUMMARY 로 쓰는데, 그러면 payload 어디에도
        # BINDER_PROXY_HISTOGRAM 이라는 이름이 남지 않는다. config.yaml 의 도메인
        # 규칙은 "BINDER_PROXY_HISTOGRAM 에서 추출된 max_count 를 가장 정확한
        # 팩트로 간주하라" 처럼 그 이름으로 지시하므로, 이름이 사라지면 LLM 이
        # 따를 근거가 검색에도 프롬프트에도 없다.
        source_type = bw.get("type") or "BINDER_PROXY_HISTOGRAM"

        meta = {
            "source_file": source_file_name(input_file),
            "log_type": "Binder_Warning",
            "time": bw.get("time", "Unknown"),
            "type": "BINDER_PROXY_LEAK_SUMMARY",
            "source_type": source_type,
            "leaked_descriptor": leaked_descriptor,
            "max_proxy_count": max_count,
            "raw_info": desc,
        }

        text_content = (
            f"심각한 바인더 프록시 객체 누수 감지. "
            f"근거 이벤트: {source_type}. "
            f"누수 객체: {leaked_descriptor}, 최대 누수 개수: {max_count}개. "
            f"상세: {desc}"
        )
        append_payload(rag_payload, text_content, meta)

    # 누수가 아닌 나머지 경고들 분리 작업
    remaining_warnings = [
        bw for bw in binder_warnings
        if isinstance(bw, dict) and bw.get("type") not in BINDER_LEAK_TYPES
    ]

    # 2. 시스템 강제 종료 계열 (Kill / WTF / 프로세스 사망)
    system_kill_wtf_events = [
        bw for bw in remaining_warnings
        if bw.get("type") in SYSTEM_EVENT_TYPES
    ]

    # 3. 💡 핵심 단서 우선 처리 (Oneway Spam, Buffer Error 등)
    critical_events = [bw for bw in remaining_warnings if is_critical_binder_event(bw)]

    # 4. 짜잘한 일반 지연 이벤트들 (최종 10개 제한용)
    normal_warnings = [
        bw for bw in remaining_warnings
        if bw.get("type") not in SYSTEM_EVENT_TYPES and not is_critical_binder_event(bw)
    ]

    # --- Payload 조립 시작 ---

    for bw in system_kill_wtf_events[::-1][:20]:
        raw_info = bw.get("raw", bw.get("raw_info", ""))
        is_too_many_binders_kill = (
            bw.get("type") == "SYSTEM_KILL"
            and "Too many Binders sent to SYSTEM" in f"{bw.get('desc', '')} {raw_info}"
        )
        meta = {
            "source_file": source_file_name(input_file),
            "log_type": "System_Kill_Wtf_Event",
            "time": bw.get("time", ""),
            "type": bw.get("type", ""),
            "process": bw.get("process", "Unknown"),
            "kill_reason": bw.get("kill_reason", ""),
            "desc": bw.get("desc", ""),
            "raw_info": raw_info,
            # 사망 사건만 갖는 값들. 없는 유형에서는 빈 값으로 빠진다.
            "pid": bw.get("pid", ""),
            "signal": bw.get("signal", ""),
            "lost_services": ", ".join(bw.get("lost_services") or []),
            "restarted_services": ", ".join(bw.get("restarted_services") or []),
            "evidence_role": bw.get("evidence_role") or (
                "rca_candidate" if is_too_many_binders_kill else "event"
            ),
            "rca_candidate": bool(bw.get("rca_candidate", is_too_many_binders_kill)),
        }
        if meta["evidence_role"] == "benign_event":
            text_content = (
                f"[시스템 정상 프로세스 회수] 시간: {meta['time']}, "
                f"프로세스: {meta['process']}, 유형: {meta['type']}, 상세: {meta['desc']} "
                "이 이벤트는 장애가 아니므로 강제 종료 사유나 Root Cause 근거로 인용하지 마십시오."
            )
        else:
            text_content = (
                f"[시스템 Kill/WTF 이벤트] 시간: {meta['time']}, "
                f"프로세스: {meta['process']}, 유형: {meta['type']}, 상세: {meta['desc']}"
            )
        append_payload(rag_payload, text_content, meta)

    # 💡 신규: 핵심 이벤트들을 일반 이벤트보다 먼저, 그리고 더 넉넉하게(최대 30개) Payload에 추가합니다.
    for bw in critical_events[::-1][:30]:
        meta = {
            "source_file": source_file_name(input_file),
            "log_type": "Binder_Warning_Critical",
            "time": bw.get("time", ""),
            "type": bw.get("type", ""),
            "desc": bw.get("desc", ""),
            "raw_info": bw.get("raw", bw.get("raw_info", "")),
            "evidence_role": bw.get("evidence_role") or "rca_candidate",
            "rca_candidate": bool(bw.get("rca_candidate", True)),
        }
        text_content = (
            f"[바인더 핵심 이상 신호] 시간: {meta['time']}, "
            f"유형: {meta['type']}, 상세: {meta['desc']}"
        )
        append_payload(rag_payload, text_content, meta)

    for bw in normal_warnings[::-1][:10]:
        warning_type = bw.get("type", "")
        is_transaction_failure = warning_type == "BINDER_TRANSACTION_FAILURE"
        evidence_role = bw.get("evidence_role") or (
            "secondary_symptom" if is_transaction_failure else "secondary_signal"
        )
        meta = {
            "source_file": source_file_name(input_file),
            "log_type": "Binder_Warning",
            "time": bw.get("time", ""),
            "type": warning_type,
            "desc": bw.get("desc", ""),
            "raw_info": bw.get("raw", bw.get("raw_info", "")),
            "evidence_role": evidence_role,
            "rca_candidate": bool(bw.get("rca_candidate", False)),
        }
        if evidence_role == "secondary_symptom":
            text_content = (
                f"[바인더 보조 증상] 시간: {meta['time']}, 유형: {meta['type']}, "
                f"상세: {meta['desc']} "
                "이 이벤트 단독으로 Binder 병목, 리소스 고갈, proxy leak 또는 Root Cause를 확정하지 마십시오."
            )
        else:
            text_content = (
                f"[바인더 통신 지연/일반 이벤트] 시간: {meta['time']}, "
                f"유형: {meta['type']}, 상세: {meta['desc']}"
            )
        append_payload(rag_payload, text_content, meta)

    rag_payload.extend(build_binder_leak_rca_docs(report_data, input_file))
    return rag_payload

def build_binder_context_payloads(report_data, input_file):
    rag_payload = []
    ctx = report_data.get("binder_context_summary") or {}
    signals = ctx.get("signals", {})
    checklist = ctx.get("checklist", [])
    if signals or checklist:
        meta = {
            "source_file": source_file_name(input_file),
            "log_type": "Binder_Context",
            "signals": json.dumps(signals, ensure_ascii=False),
            "signal_keys": ",".join(sorted(signals.keys())) if isinstance(signals, dict) else "",
        }
        text_content = (
            f"[바인더 추가 확인 문맥] 감지된 주변 신호: {signals}. "
            f"추가 확인 항목: {' / '.join(checklist)}"
        )
        append_payload(rag_payload, text_content, meta)
    return rag_payload
