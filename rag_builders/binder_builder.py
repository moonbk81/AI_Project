import json
import re

from rag_builders.common import append_payload, source_file_name

BINDER_LEAK_TYPES = (
    "BINDER_PROXY_HISTOGRAM",
    "BINDER_PROXY_LEAK",
    "BINDER_PROXY_LEAK_SUMMARY",
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

def build_binder_leak_rca_docs(report_data, input_file):
    """Build high-level RCA documents from Binder proxy leak + am_kill context."""
    rca_docs = []
    binder_warnings = report_data.get("binder_warnings", []) or []

    leak_warnings = [
        bw for bw in binder_warnings
        if isinstance(bw, dict) and bw.get("type") in BINDER_LEAK_TYPES
    ]
    if not leak_warnings:
        return rca_docs

    leak_warnings = sorted(leak_warnings, key=extract_proxy_count, reverse=True)
    top_leak = leak_warnings[0]

    leak_text = " ".join([
        str(top_leak.get("desc", "")),
        str(top_leak.get("raw", "")),
        str(top_leak.get("raw_info", "")),
        str(top_leak.get("details", "")),
    ])

    max_count = extract_proxy_count(top_leak)
    leaked_descriptor = extract_leaked_descriptor(leak_text)

    system_kills = [
        bw for bw in binder_warnings
        if isinstance(bw, dict)
        and bw.get("type") == "SYSTEM_KILL"
        and "Too many Binders sent to SYSTEM" in " ".join([
            str(bw.get("desc", "")),
            str(bw.get("raw", "")),
            str(bw.get("raw_info", "")),
        ])
    ]
    if not system_kills:
        return rca_docs

    phone_kill = next((c for c in system_kills if c.get("process") == "com.android.phone"), None)
    victim = phone_kill or system_kills[0]

    process = victim.get("process", "Unknown")
    time = victim.get("time") or top_leak.get("time") or "Unknown"
    trigger = victim.get("raw", victim.get("raw_info", ""))
    kill_reason = "Too many Binders sent to SYSTEM"

    wtf_events = [
        bw for bw in binder_warnings
        if isinstance(bw, dict)
        and (
            bw.get("type") == "SYSTEM_WTF"
            or "am_wtf" in str(bw.get("raw", ""))
            or "am_wtf" in str(bw.get("raw_info", ""))
        )
    ]

    wtf_count = 0
    for w in wtf_events:
        wtf_count += safe_int(w.get("count"), 0)

    if leaked_descriptor == "android.content.IIntentReceiver":
        root_cause = "IIntentReceiver Binder proxy leak"
        developer_action = "동적 BroadcastReceiver register 후 unregister 누락 여부를 점검해야 함"
    else:
        root_cause = "Binder proxy object leak"
        developer_action = "누수된 Binder interface의 acquire/release 또는 register/unregister 생명주기 점검 필요"

    metadata = {
        "source_file": source_file_name(input_file),
        "log_type": "RCA_Event",
        "rca_type": "BINDER_PROXY_LEAK_RCA",
        "time": time,
        "process": process,
        "kill_event": "am_kill",
        "kill_reason": kill_reason,
        "leaked_descriptor": leaked_descriptor,
        "max_proxy_count": max_count,
        "am_wtf_count_observed": wtf_count,
        "root_cause": root_cause,
        "developer_action": developer_action,
        "trigger": trigger,
        "symptom_keywords": "폰 죽음, 갑자기 죽음, 강제 종료, 시스템 크래시, SYSTEM_KILL, am_kill, crash, kill",
    }

    document = (
        f"[RCA: BINDER_PROXY_LEAK] 폰이 갑자기 죽음/강제 종료/시스템 크래시처럼 보이는 증상과 관련된 RCA 문서. "
        f"{process} 프로세스가 am_kill(SYSTEM_KILL)로 강제 종료됨. "
        f"강제 종료 사유는 '{kill_reason}'. "
        f"동시간대 Binder Proxy Histogram에서 {leaked_descriptor} 객체가 최대 {max_count}개까지 누수됨. "
        f"따라서 근본 원인은 단순 앱 크래시나 Native Crash가 아니라 {root_cause}에 따른 시스템 리소스 고갈로 판단됨. "
        f"개발 조치: {developer_action}."
    )

    append_payload(rca_docs, document, metadata)
    return rca_docs

def build_binder_payloads(report_data, input_file):
    rag_payload = []
    binder_warnings = report_data.get("binder_warnings", []) or []

    leak_warnings = [
        bw for bw in binder_warnings
        if isinstance(bw, dict) and bw.get("type") in BINDER_LEAK_TYPES
    ]

    for bw in leak_warnings:
        max_count = extract_proxy_count(bw)
        desc = bw.get("desc") or bw.get("raw") or bw.get("raw_info") or ""
        leaked_descriptor = extract_leaked_descriptor(desc)

        meta = {
            "source_file": source_file_name(input_file),
            "log_type": "Binder_Warning",
            "time": bw.get("time", "Unknown"),
            "type": "BINDER_PROXY_LEAK_SUMMARY",
            "leaked_descriptor": leaked_descriptor,
            "max_proxy_count": max_count,
            "raw_info": desc,
        }

        text_content = (
            f"심각한 바인더 프록시 객체 누수 감지. "
            f"누수 객체: {leaked_descriptor}, 최대 누수 개수: {max_count}개. "
            f"상세: {desc}"
        )
        append_payload(rag_payload, text_content, meta)

    normal_warnings = [
        bw for bw in binder_warnings
        if isinstance(bw, dict) and bw.get("type") not in BINDER_LEAK_TYPES
    ]

    system_kill_wtf_events = [
        bw for bw in normal_warnings
        if bw.get("type") in ("SYSTEM_KILL", "SYSTEM_WTF")
    ]
    normal_warnings = [
        bw for bw in normal_warnings
        if bw.get("type") not in ("SYSTEM_KILL", "SYSTEM_WTF")
    ]

    for bw in system_kill_wtf_events[::-1][:20]:
        meta = {
            "source_file": source_file_name(input_file),
            "log_type": "System_Kill_Wtf_Event",
            "time": bw.get("time", ""),
            "type": bw.get("type", ""),
            "process": bw.get("process", "Unknown"),
            "desc": bw.get("desc", ""),
            "raw_info": bw.get("raw", bw.get("raw_info", "")),
        }

        text_content = (
            f"[시스템 Kill/WTF 이벤트] 시간: {meta['time']}, "
            f"프로세스: {meta['process']}, 유형: {meta['type']}, 상세: {meta['desc']}"
        )
        append_payload(rag_payload, text_content, meta)

    for bw in normal_warnings[::-1][:10]:
        meta = {
            "source_file": source_file_name(input_file),
            "log_type": "Binder_Warning",
            "time": bw.get("time", ""),
            "type": bw.get("type", ""),
            "desc": bw.get("desc", ""),
            "raw_info": bw.get("raw", bw.get("raw_info", "")),
        }

        text_content = (
            f"[바인더 통신 이벤트] 시간: {meta['time']}, "
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
