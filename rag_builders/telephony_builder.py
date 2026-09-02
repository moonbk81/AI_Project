"""Telephony-related RAG payload builders.

This module converts telephony-domain sections from report_data into RAG payload
documents. It intentionally receives build_markdown_doc/extract_metadata callbacks
from RagPayloadBuilder so the first refactor keeps existing document/metadata
behavior unchanged.
"""

import json
import os
from rag_builders.common import append_callback_payload, append_payload, source_file_name

def _is_failed_call_session(session):
    status = str((session or {}).get("status") or "").upper()
    reason = str((session or {}).get("fail_reason") or "").lower()
    return (
        any(k in status for k in ["FAIL", "DROP"])
        or any(k in reason for k in ["callfailcause", "vendorcause", "ims_fail"])
    ) and not any(k in status for k in ["SUCCESS", "NORMAL_RELEASE", "CANCELED", "CANCELLED"])

def _recent_calls_with_failures(call_sessions, limit=10):
    recent = list((call_sessions or [])[::-1][:limit])
    seen = {id(session) for session in recent}
    for session in call_sessions or []:
        if _is_failed_call_session(session) and id(session) not in seen:
            recent.append(session)
            seen.add(id(session))
    return recent

def build_radio_power_payloads(report_data, build_markdown_doc, extract_metadata):
    rag_payload = []
    for rp in report_data.get("radio_power", []) or []:
        append_callback_payload(rag_payload, rp, "Radio_Power_Event", build_markdown_doc, extract_metadata)
    return rag_payload

def build_call_session_payloads(report_data, build_markdown_doc, extract_metadata):
    rag_payload = []
    call_sessions = report_data.get("call_sessions", []) or []
    for session in _recent_calls_with_failures(call_sessions):
        append_callback_payload(rag_payload, session, "Call_Session", build_markdown_doc, extract_metadata)
    return rag_payload

def build_oos_payloads(report_data, build_markdown_doc, extract_metadata):
    rag_payload = []
    oos_events = report_data.get("oos_events", []) or []
    for oos in oos_events[::-1][:5]:
        append_callback_payload(rag_payload, oos, "OOS_Event", build_markdown_doc, extract_metadata)
    return rag_payload

def _emergency_call_document(attempt):
    """긴급호 한 건을 사람이 읽는 순서로 적는다.

    실패 사유 한 줄만으로는 왜 못 걸렸는지 알 수 없다. 어디로 걸었고, 그때 망이
    어땠고, 모뎀 상태 기계가 어디까지 갔고, 긴급 PDN 이 어떻게 됐는지를 함께
    적어야 다음 질문("그럼 왜 PDN 이 안 올라왔나")으로 넘어갈 수 있다.
    """
    pdn = attempt.get("emergency_pdn") or {}
    lines = [
        f"### [Type: Emergency_Call] 긴급호 {attempt.get('number', 'Unknown')} "
        f"({attempt.get('status', 'UNKNOWN')})",
        f"- 시각: {attempt.get('time', '')} ~ {attempt.get('end_time', '')}"
        f" (slot {attempt.get('slot', 'Unknown')})",
        f"- 발신 경로: {attempt.get('route', 'Unknown')}"
        f" (RAT: {attempt.get('rat', 'Unknown')}, 도메인: {attempt.get('domain', 'Unknown')})",
        f"- 긴급 서비스 카테고리: {attempt.get('ecc_category') or '미확인'}",
        f"- 발신 시점 서비스 상태: {attempt.get('service_state', 'Unknown')}"
        f" (긴급 통화만 가능: {attempt.get('emergency_only') or '미확인'})",
    ]

    if attempt.get("ecc_list_matched") or attempt.get("ecc_list"):
        lines.append(
            "- 긴급번호 확인: "
            + ("단말 긴급번호 목록에서 확인됨" if attempt.get("ecc_list_matched") else "확인 로그 없음")
            + (f" (목록: {attempt['ecc_list']})" if attempt.get("ecc_list") else "")
        )
    if search := attempt.get("search_result"):
        determiner = attempt.get("rat_determiner")
        lines.append(
            f"- 모뎀 도메인 선택: {search}"
            + (f" (RatDeterminer: {determiner})" if determiner else "")
        )
    if progressed := attempt.get("dialed_domain"):
        lines.append(f"- 진행된 도메인: {progressed}")
    if fallback := attempt.get("fallback"):
        lines.append(f"- 도메인 이동(재발신): {fallback}")
    if attempt.get("cs_dialed_at"):
        lines.append(f"- CS 긴급 발신(EMERGENCY_DIAL): {attempt['cs_dialed_at']}")
    if attempt.get("ims_emergency_barring"):
        lines.append(f"- IMS 긴급호 barring 값: {attempt['ims_emergency_barring']}")
    if control := attempt.get("emergency_control"):
        lines.append(f"- 모뎀 긴급호 제어: {' → '.join(control)}")
    if progress := attempt.get("e911_progress"):
        lines.append(f"- E911 진행 상태: {' → '.join(progress)}")
    if pdn:
        lines.append(
            f"- 긴급 PDN(APN {pdn.get('apn', 'sos')}): {pdn.get('status', 'Unknown')}"
            + (f", cause={pdn['cause']}" if pdn.get("cause") else "")
            + (f", 요청 {pdn['requested_at']}" if pdn.get("requested_at") else "")
            + (f" → 응답 {pdn['answered_at']}" if pdn.get("answered_at") else "")
        )
    if attempt.get("end_cause_text"):
        lines.append(f"- 통화 종료 원인: {attempt['end_cause_text']}")
    if attempt.get("modem_reset"):
        lines.append("- 통화 도중 모뎀이 리셋되었습니다(All Service is closed, Modem Reset).")
    if attempt.get("ims_fail_reason"):
        lines.append(f"- IMS 실패 코드: {attempt['ims_fail_reason']}")
    if attempt.get("volte_911_config"):
        lines.append(f"- 단말 설정 VOLTE_911_CALL: {attempt['volte_911_config']}")
    if attempt.get("fail_reason"):
        lines.append(f"- 실패 사유: {attempt['fail_reason']}")
    if attempt.get("root_cause_candidate"):
        lines.append(f"- 원인 후보: {attempt['root_cause_candidate']}")

    return "\n".join(lines)


def build_emergency_call_payloads(report_data, extract_metadata):
    rag_payload = []
    for attempt in report_data.get("emergency_calls", []) or []:
        meta = extract_metadata(attempt, "Emergency_Call")
        append_payload(rag_payload, _emergency_call_document(attempt), meta)
    return rag_payload


def build_ims_sip_payloads(report_data, build_markdown_doc, extract_metadata):
    rag_payload = []
    sip_events = report_data.get("ims_sip_data", []) or []
    for sip in sip_events[::-1][:10]:
        meta = extract_metadata(sip, "IMS_SIP_Message")
        if "raw_log" in sip:
            meta["raw_logs"] = json.dumps([sip["raw_log"]], ensure_ascii=False)
        text_content = sip.get("document", build_markdown_doc(sip, "IMS_SIP_Message"))
        append_payload(rag_payload, text_content, meta)
    return rag_payload

def build_rilj_payloads(report_data, input_file):
    rag_payload = []
    rilj_data = report_data.get("rilj_transactions") or {}
    if not isinstance(rilj_data, dict):
        return rag_payload

    source_file = source_file_name(input_file)

    recent_timeouts = rilj_data.get("timeouts", [])[::-1][:5]
    for t in recent_timeouts:
        meta = {
            "source_file": source_file,
            "log_type": "RILJ_Transaction",
            "status": "TIMEOUT",
            "command": t.get("command", "Unknown"),
            "time": t.get("time", ""),
        }
        doc = (
            f"[모뎀 응답 먹통(TIMEOUT)] 시간: {t.get('time', '')}, "
            f"명령어: {t.get('command', 'Unknown')} 에 대해 모뎀이 응답하지 않았습니다."
        )
        append_payload(rag_payload, doc, meta)

    bad_responses = [
        c for c in rilj_data.get("completed", [])
        if c.get("is_error") or c.get("latency_ms", 0) > 500
    ]
    recent_bad = bad_responses[::-1][:5]
    for c in recent_bad:
        status = "ERROR" if c.get("is_error") else "SLOW"
        meta = {
            "source_file": source_file,
            "log_type": "RILJ_Transaction",
            "status": status,
            "command": c.get("command", "Unknown"),
            "latency_ms": c.get("latency_ms", 0),
            "time": c.get("start_time", ""),
            "error_msg": c.get("error_msg", ""),
        }
        doc = (
            f"[모뎀 응답 이상({status})] 시간: {c.get('start_time', '')}, "
            f"명령어: {c.get('command', 'Unknown')}, "
            f"지연시간: {c.get('latency_ms', 0)}ms, "
            f"에러내용: {c.get('error_msg', '')}"
        )
        append_payload(rag_payload, doc, meta)

    return rag_payload

def build_telephony_payloads(report_data, input_file, build_markdown_doc, extract_metadata):
    rag_payload = []
    rag_payload.extend(build_radio_power_payloads(report_data, build_markdown_doc, extract_metadata))
    rag_payload.extend(build_call_session_payloads(report_data, build_markdown_doc, extract_metadata))
    rag_payload.extend(build_emergency_call_payloads(report_data, extract_metadata))
    rag_payload.extend(build_oos_payloads(report_data, build_markdown_doc, extract_metadata))
    rag_payload.extend(build_ims_sip_payloads(report_data, build_markdown_doc, extract_metadata))
    rag_payload.extend(build_rilj_payloads(report_data, input_file))
    return rag_payload
