"""Telephony-related RAG payload builders.

This module converts telephony-domain sections from report_data into RAG payload
documents. It intentionally receives build_markdown_doc/extract_metadata callbacks
from RagPayloadBuilder so the first refactor keeps existing document/metadata
behavior unchanged.
"""

import json
import os
from rag_builders.common import append_callback_payload, append_payload, source_file_name

def build_radio_power_payloads(report_data, build_markdown_doc, extract_metadata):
    rag_payload = []
    for rp in report_data.get("radio_power", []) or []:
        append_callback_payload(rag_payload, rp, "Radio_Power_Event", build_markdown_doc, extract_metadata)
    return rag_payload

def build_call_session_payloads(report_data, build_markdown_doc, extract_metadata):
    rag_payload = []
    call_sessions = report_data.get("call_sessions", []) or []
    for session in call_sessions[::-1][:10]:
        append_callback_payload(rag_payload, session, "Call_Session", build_markdown_doc, extract_metadata)
    return rag_payload

def build_oos_payloads(report_data, build_markdown_doc, extract_metadata):
    rag_payload = []
    oos_events = report_data.get("oos_events", []) or []
    for oos in oos_events[::-1][:5]:
        append_callback_payload(rag_payload, oos, "OOS_Event", build_markdown_doc, extract_metadata)
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
    rag_payload.extend(build_oos_payloads(report_data, build_markdown_doc, extract_metadata))
    rag_payload.extend(build_ims_sip_payloads(report_data, build_markdown_doc, extract_metadata))
    rag_payload.extend(build_rilj_payloads(report_data, input_file))
    return rag_payload