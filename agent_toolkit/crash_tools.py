import json

from agent_toolkit.common import _load_report_json

def get_crash_anr_analytics(base_name: str, result_dir: str = "./result") -> str:
    """시스템 크래시(FATAL) 및 응답없음(ANR) 발생 이력과 ANR 원인 분석 힌트를 추출합니다."""
    crashes = [
        c for c in _load_report_json(base_name, result_dir).get("crash_context", [])
        if isinstance(c, dict) and c.get("type") not in ("SYSTEM_KILL", "SYSTEM_WTF", "SYSTEM_WTF_SUMMARY")
    ]
    report_data = _load_report_json(base_name, result_dir)
    native_crashes = report_data.get("native_crash_context", [])
    anr = report_data.get("anr_context", [])

    crash_facts = []
    for c in crashes:
        raw_stack = str(c.get("stacktrace", "")).lower()
        is_binder_too_large = "transactiontoolargeexception" in raw_stack

        crash_facts.append({
            "time": c.get("timestamp") or c.get("time"),
            "process": c.get("process"),
            "type": "KERNEL_PANIC" if c.get("is_kernel") else (c.get("crash_type") or c.get("type") or "FATAL_EXCEPTION"),
            "binder_transaction_too_large": is_binder_too_large,
            "exception_reason": c.get("exception_name") or c.get("top_method", "Unknown"),
            # 💡 [핵심 추가] LLM이 MNR을 추론할 수 있도록 문맥과 정보를 제공!
            "exception_info": c.get("exception_info", ""),
            "pre_context": c.get("context", [])[-15:],
            "call_stack": c.get("call_stack", [])[:10]
        })


    native_crash_facts = []
    for n in native_crashes:
        native_crash_facts.append({
            "time": n.get("timestamp"),
            "process": n.get("process"),
            "type": "NATIVE_CRASH",
            "signal": n.get("signal"),
            "abort_message": n.get("abort_message"),
            "top_callstack": [
                f"#{c['frame_level']} {c['library']} ({c['function']})"
                for c in n.get("callstack", [])
            ][:5]
        })

    if isinstance(anr, dict):
        anr = [anr] if anr else []
    elif not isinstance(anr, list):
        anr = []

    anr_facts = []
    for a in anr:
        if not isinstance(a, dict):
            continue

        process_info = a.get("process_info", {}) or {}
        analysis_summary = a.get("analysis_summary", {}) or {}
        lock_chain = a.get("lock_chain", {}) or {}
        main_info = a.get("main", {}) or {}
        main_stack = main_info.get("stack", []) or []
        binder_txs = a.get("active_binder_transactions", []) or []
        context_analysis = a.get("context_analysis", {}) or {}
        pre_anr_logcat = a.get("pre_anr_logcat", []) or []

        blocker_stack = lock_chain.get("blocker_stack") or []

        anr_facts.append({
            "time": a.get("time"),
            "process": a.get("process") or process_info.get("name", "Unknown"),
            "pid": process_info.get("pid"),
            "reason": a.get("reason", ""),
            "main_thread": {
                "tid": main_info.get("tid"),
                "top_stack": main_stack[:12]
            },
            "lock_analysis": {
                "has_lock_contention": analysis_summary.get("has_lock_contention", False),
                "waiting_thread": lock_chain.get("waiting_thread"),
                "blocker_thread": lock_chain.get("blocker_thread"),
                "lock_address": lock_chain.get("lock_address"),
                "blocker_top_stack": blocker_stack[:12]
            },
            "binder_analysis": {
                "has_active_binder": analysis_summary.get("has_active_binder", False),
                "transactions": [
                    {
                        "from_pid": tx.get("from_pid"),
                        "from_tid": tx.get("from_tid"),
                        "to_pid": tx.get("to_pid"),
                        "to_tid": tx.get("to_tid"),
                        "code": tx.get("code"),
                        "raw": tx.get("raw")
                    }
                    for tx in binder_txs[:10]
                    if isinstance(tx, dict)
                ]
            },
            "context_hints": {
                "has_cpu_hint": analysis_summary.get("has_cpu_hint", False),
                "has_system_server_hint": analysis_summary.get("has_system_server_hint", False),
                "has_io_hint": analysis_summary.get("has_io_hint", False),
                "cpu_logs": (context_analysis.get("cpu_logs", []) or [])[-20:],
                "system_server_logs": (context_analysis.get("system_server_logs", []) or [])[-20:],
                "io_logs": (context_analysis.get("io_logs", []) or [])[-20:]
            },
            "pre_anr_logcat_tail": pre_anr_logcat[-40:]
        })

    return json.dumps({
        "crash_count": len(crashes),
        "crash_history": crash_facts,
        "native_crash_count": len(native_crashes),
        "native_crash_history": native_crash_facts,
        "anr_count": len(anr_facts),
        "anr_history": anr_facts
    }, ensure_ascii=False)
