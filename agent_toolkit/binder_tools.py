from collections import Counter
import json
import re

from agent_toolkit.common import _load_report_json

# ==========================================
# Binder Warning Analytics
# ==========================================
def get_binder_warning_analytics(base_name: str, result_dir: str = "./result") -> str:
    """
    Binder IPC 상태 및 메모리 누수(Proxy), 그리고 이와 연관된
    시스템 강제 종료(am_kill, am_wtf) 정보를 종합적으로 반환합니다.
    """
    report_data = _load_report_json(base_name, result_dir)

    binder_warnings = report_data.get("binder_warnings", []) or []
    binder_context_summary = report_data.get("binder_context_summary", {}) or {}

    warning_facts = []
    type_counter = Counter()
    max_wait_ms = None
    proxy_leaks = [] # 💡 BINDER_PROXY_HISTOGRAM 수집용

    # 💡 [핵심 추가] SYSTEM_WTF 압축을 위한 딕셔너리
    wtf_groups = {}

    for warning in binder_warnings:
        # 문자열로 들어온 경우 안전하게 파싱
        if isinstance(warning, str):
            try: warning = json.loads(warning)
            except: continue
        if not isinstance(warning, dict):
            continue

        warning_type = warning.get("type") or warning.get("event_type") or "UNKNOWN"
        desc = warning.get("desc") or warning.get("message") or warning.get("raw") or ""
        raw = warning.get("raw") or desc

        type_counter[warning_type] += 1

        # 💡 히스토그램 누수 정보 분리 수집
        if warning_type in ("BINDER_PROXY_HISTOGRAM", "BINDER_PROXY_LEAK"):
            proxy_leaks.append({
                "time": warning.get("time", "Unknown"),
                "max_count": warning.get("max_count", 0),
                "desc": desc
            })
            continue

        if warning_type == "SYSTEM_KILL":
            warning_facts.append({
                "time": warning.get("time") or warning.get("timestamp"),
                "type": warning_type,
                "process": warning.get("process", "Unknown"),
                "desc": desc,
                "reason": desc,
                "raw": raw,
                "wait_ms": None,
            })
            continue

        # 💡 [핵심 수정] SYSTEM_WTF 무지성 append 방지 및 그룹화 압축
        if warning_type == "SYSTEM_WTF":
            proc = warning.get("process", "Unknown")
            time_val = warning.get("time") or warning.get("timestamp") or "Unknown"

            if proc not in wtf_groups:
                wtf_groups[proc] = {
                    "count": 0,
                    "first_time": time_val,
                    "last_time": time_val,
                    "desc": desc
                }

            wtf_groups[proc]["count"] += 1
            if time_val != "Unknown":
                if wtf_groups[proc]["first_time"] == "Unknown":
                    wtf_groups[proc]["first_time"] = time_val
                # 로그가 시간순이라고 가정하고 계속 덮어씌워서 마지막 시간을 구함
                wtf_groups[proc]["last_time"] = time_val
            continue

        else:
            wait_ms = None
            wait_match = re.search(r"(\d{2,6})\s*ms", str(raw)) or re.search(r"(\d{2,6})\s*ms", str(desc))
            if wait_match:
                try:
                    wait_ms = int(wait_match.group(1))
                    max_wait_ms = wait_ms if max_wait_ms is None else max(max_wait_ms, wait_ms)
                except ValueError:
                    wait_ms = None

            warning_facts.append({
                "time": warning.get("time") or warning.get("timestamp"),
                "type": warning_type,
                "desc": desc,
                "wait_ms": wait_ms,
                "raw": raw,
            })

    # 💡 [핵심 추가] 루프가 끝난 뒤 압축된 SYSTEM_WTF를 warning_facts에 한 줄씩만 삽입
    for proc, info in wtf_groups.items():
        count = info["count"]
        t_first = info["first_time"]
        t_last = info["last_time"]

        # 1건이면 단일 시간, 여러 건이면 구간으로 표시
        time_str = f"{t_first} ~ {t_last}" if count > 1 and t_first != t_last else t_first
        compressed_desc = f"SYSTEM_WTF ({proc}): 동일한 시스템 상태 이상 에러 {count}건 감지 (발생 구간: {time_str})"

        warning_facts.append({
            "time": time_str,
            "type": "SYSTEM_WTF",
            "process": proc,
            "desc": compressed_desc,
            "summary": compressed_desc,
            "raw": f"OMITTED_RAW_FOR_COMPRESSION (Count: {count})",
            "wait_ms": None,
        })

    signals = binder_context_summary.get("signals", {}) if isinstance(binder_context_summary, dict) else {}
    checklist = binder_context_summary.get("checklist", []) if isinstance(binder_context_summary, dict) else []
    total_context_lines = binder_context_summary.get("total_context_lines", 0) if isinstance(binder_context_summary, dict) else 0

    thread_exhaustion_events = [
        item for item in warning_facts
        if "THREAD_EXHAUSTION" in str(item.get("type", ""))
        or "THREAD_EXHAUSTION" in str(item.get("desc", ""))
        or "THREAD_EXHAUSTION" in str(item.get("raw", ""))
    ]

    transaction_failures = [
        item for item in warning_facts
        if "BINDER_TRANSACTION_FAILURE" in str(item.get("type", ""))
        or "BINDER_TRANSACTION_FAILURE" in str(item.get("desc", ""))
        or "BINDER_TRANSACTION_FAILURE" in str(item.get("raw", ""))
    ]

    system_kills = [
        {
            "time": item.get("time"),
            "process": item.get("process", "Unknown"),
            "reason": item.get("reason") or item.get("desc"),
            "raw_trigger": item.get("raw"),
        }
        for item in warning_facts
        if item.get("type") == "SYSTEM_KILL"
    ]

    # 위에서 압축된 SYSTEM_WTF 데이터가 여기서 자동으로 깔끔하게 필터링되어 들어갑니다.
    system_wtfs = [
        {
            "time": item.get("time"),
            "process": item.get("process", "Unknown"),
            "summary": item.get("summary") or item.get("desc"),
            "raw_trigger": item.get("raw"),
        }
        for item in warning_facts
        if item.get("type") == "SYSTEM_WTF"
    ]

    # JSON 응답으로 반환 (LLM이 키워드로 파싱)
    return json.dumps({
        "status": "OK" if warning_facts or proxy_leaks or system_kills or system_wtfs else "NO_DATA",
        "binder_warning_count": len(warning_facts),
        "proxy_leak_histograms": proxy_leaks, # 💡 LLM에게 바인더 누수 심각성 전달
        "system_kills_am_kill": system_kills, # 💡 LLM에게 강제 종료 원인 전달
        "system_wtfs_am_wtf": system_wtfs,    # 💡 LLM에게 이상징후 대량 발생 전달
        "warning_type_counts": dict(type_counter),
        "thread_exhaustion_count": len(thread_exhaustion_events),
        "binder_transaction_failure_count": len(transaction_failures),
        "max_wait_ms": max_wait_ms,
        "has_thread_exhaustion": len(thread_exhaustion_events) > 0,
        "has_binder_transaction_failure": len(transaction_failures) > 0,
        "binder_warnings": warning_facts[:50],
        "thread_exhaustion_events": thread_exhaustion_events[:20],
        "transaction_failure_events": transaction_failures[:20],
        "binder_context_summary": {
            "signals": signals,
            "checklist": checklist,
            "total_context_lines": total_context_lines,
        }
    }, ensure_ascii=False)