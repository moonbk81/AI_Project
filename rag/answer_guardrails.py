"""Deterministic answer guardrails for high-risk structured RAG evidence.

This module handles cases where retrieval already contains clear structured facts,
but a small/local LLM may collapse to a short answer or ignore RCA/absence rules.
Keep this file domain-generic: no Golden TC IDs, no fixed test-only timestamps, and no hard-coded expected counts.
"""

import ast
import re

from rag.query_classifiers import (
    is_binder_proxy_count_query,
    is_call_drop_check_query,
    is_crash_absence_check,
    is_negative_binder_leak_check_query,
)


def iter_result_meta(results) -> list:
    if not results or not results.get("metadatas"):
        return []
    return results.get("metadatas", [[]])[0] or []


def _contains_process_name(events: list[dict], process_name: str) -> bool:
    process_name = process_name.lower()
    return any(
        process_name in " ".join([
            str(meta.get("process", "")),
            str(meta.get("desc", "")),
            str(meta.get("raw_info", "")),
            str(meta.get("root_cause", "")),
        ]).lower()
        for meta in events
    )


def _extract_ms(meta: dict) -> int:
    text = " ".join([
        str(meta.get("desc", "")),
        str(meta.get("raw_info", "")),
    ])
    match = re.search(r"(\d+)\s*ms", text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _guess_requested_process(query_lower: str) -> str | None:
    for process_name in [
        "rild",
        "com.android.phone",
        "system_server",
        "system",
    ]:
        if process_name in query_lower:
            return process_name
    return None


def try_build_guardrail_answer(user_query: str, results, tool_facts=None) -> str | None:
    """Return a deterministic answer when retrieved metadata is decisive.

    Returns None when the normal LLM/structured renderer should handle the answer.
    """
    query_lower = user_query.lower()
    meta_list = iter_result_meta(results)

    # 💡 [개입 축소] 기존에 tool_facts만 보고 바로 리턴해버리던
    # _build_call_drop_answer_from_tool_facts 와 _is_general_call_issue_query 방어벽을 제거했습니다.
    # 이제 일반적인 Call Drop 질의는 LLM이 tool_facts를 읽고 직접 분석합니다.

    if not meta_list:
        return None

    # 1) Crash/ANR absence check: answer only the scoped absence question.
    if is_crash_absence_check(query_lower):
        native_crashes = [
            meta for meta in meta_list
            if meta.get("log_type") == "Native_Crash_Event"
        ]
        crash_events = [
            meta for meta in meta_list
            if meta.get("log_type") == "Crash_Event"
        ]
        anr_events = [
            meta for meta in meta_list
            if meta.get("log_type") == "ANR_Context"
        ]

        if native_crashes and not crash_events and not anr_events:
            requested_process = _guess_requested_process(query_lower)
            if requested_process and _contains_process_name(native_crashes, requested_process):
                subject = f"{requested_process} Native Crash"
            else:
                first_process = native_crashes[0].get("process") or native_crashes[0].get("process_name")
                subject = f"{first_process} Native Crash" if first_process else "Native Crash"
            return (
                f"{subject}만 확인됩니다. 일반 앱 Java Exception 기반 `Crash_Event`는 확인되지 않으며, "
                "일반 앱 응답 없음 `ANR_Context`도 확인되지 않습니다. 따라서 질문 범위 기준으로는 "
                "Java Crash 동반 없음, 일반 앱 ANR 동반 없음으로 판단됩니다. Binder/IPC 경고 등 다른 시스템 이벤트는 "
                "일반 앱 Crash/ANR 원인으로 확장하지 않습니다."
            )

    # 2) Call Drop fact-only check: (골든셋 함정 방어용 최소 개입)
    # LLM이 Normal Release를 Call Drop으로 환각하는 것만 잡아냅니다.
    if is_call_drop_check_query(query_lower):
        if any(k in query_lower for k in ["거절", "reject", "decline", "망 이슈", "sip", "에러", "원인", "사유"]):
            pass  # LLM에게 렌더링을 맡김 (return None)
        else:
            facts_str = str(tool_facts)
            # KPI 데이터 상에 드랍이 0건이거나 "드랍 없음"이 확정된 경우에만 환각 방어 발동
            if re.search(r"['\"]dropped_calls_count['\"]\s*:\s*0", facts_str) or "드랍 없음" in facts_str:
                return "명시적인 Call Drop 이력은 없으며 정상 종료(Normal Release)되었습니다."

    # 3) Binder THREAD_EXHAUSTION / IPC bottleneck
    thread_exhaustion_events = [
        meta for meta in meta_list
        if meta.get("log_type") == "Binder_Warning"
        and str(meta.get("type", "")).upper() == "THREAD_EXHAUSTION"
    ]
    if thread_exhaustion_events and any(k in query_lower for k in ["ipc", "병목", "bottleneck", "멈칫", "지연"]):
        max_event = max(thread_exhaustion_events, key=_extract_ms)
        max_ms = _extract_ms(max_event)
        times = [str(meta.get("time", "")) for meta in thread_exhaustion_events if meta.get("time")]
        time_summary = ", ".join(times[:3])
        return (
            "네, 단말 내부 IPC 병목 흔적이 확인됩니다. 검색된 `Binder_Warning`에서 "
            f"`THREAD_EXHAUSTION`이 감지되었고, Binder thread pool 고갈로 Starvation 대기가 발생했습니다. "
            f"발생 시간대는 {time_summary}이며, 최대 대기 시간은 {max_ms}ms입니다. "
            "이는 기지국/망 장애가 아니라 단말 내부 Binder IPC 처리 스레드 부족에 따른 시스템 성능 저하로 판단됩니다."
        )

    # 4) Binder proxy leak RCA
    binder_rca_events = [
        meta for meta in meta_list
        if meta.get("log_type") == "RCA_Event"
        and str(meta.get("rca_type", "")) == "BINDER_PROXY_LEAK_RCA"
    ]
    if binder_rca_events:
        rca = binder_rca_events[0]
        process = rca.get("process") or _guess_requested_process(query_lower) or "확인된 프로세스"
        leaked_descriptor = rca.get("leaked_descriptor") or rca.get("descriptor") or "Binder proxy"
        max_count = rca.get("max_proxy_count", rca.get("max_count", "확인 필요"))
        kill_event = rca.get("kill_event", "am_kill")
        kill_reason = rca.get("kill_reason", "Too many Binders sent to SYSTEM")
        developer_action = rca.get("developer_action", "동적 BroadcastReceiver register 후 unregister 누락 여부를 점검해야 함")

        if any(k in query_lower for k in ["연관", "관련", "가이드", "고쳐", "개발자", "root cause", "근본 원인", "죽", "강제 종료", "am_kill"]):
            return (
                f"네, 서로 연관되어 있습니다. `{process}`에서 `{leaked_descriptor}` Binder proxy leak이 발생했고, "
                f"최대 {max_count}개까지 누수되었습니다. 이 누수로 인해 시스템 리소스가 고갈되었고, "
                f"ActivityManager가 `{kill_reason}` 사유로 `{kill_event}` 강제 종료를 수행한 것으로 판단됩니다. "
                f"개발자는 {developer_action}."
            )

    # 5) Negative Binder leak check (환각 방지)
    if is_negative_binder_leak_check_query(query_lower):
        has_positive_leak = any(
            (meta.get("log_type") == "RCA_Event" and str(meta.get("rca_type", "")) == "BINDER_PROXY_LEAK_RCA")
            or "too many binders sent to system" in " ".join([str(meta.get("kill_reason", "")), str(meta.get("raw_info", "")), str(meta.get("desc", ""))]).lower()
            or "am_kill" in " ".join([str(meta.get("kill_event", "")), str(meta.get("raw_info", ""))]).lower()
            for meta in meta_list
        )
        if not has_positive_leak:
            return (
                "Binder proxy leak 확인되지 않음. 검색 결과에서 `BINDER_PROXY_LEAK_RCA`가 확인되지 않고, "
                "Binder Proxy Histogram 기반의 leak 근거도 확인되지 않습니다. 따라서 이 로그에서는 Binder proxy leak에 의한 "
                "시스템 리소스 고갈 또는 프로세스 강제 종료로 판단하면 안 됩니다."
            )

    # 💡 위 조건에 걸리지 않는 대부분의 질의는 LLM에게 넘어갑니다!
    return None