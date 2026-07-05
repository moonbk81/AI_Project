"""Routing utilities extracted from ril_rag_chat.py."""

import json
import re

import numpy as np
import ollama

def _is_crash_absence_check_query(query_lower: str) -> bool:
    has_crash_scope = any(k in query_lower for k in [
        "crash", "크래시", "native crash", "네이티브 크래시", "fatal exception",
        "java crash", "java exception", "native_crash_event",
        "crash_event", "anr", "anr_context", "응답 없음", "앱 응답 없음", "시스템 크래시"
    ])
    has_absence_intent = any(k in query_lower for k in [
        "없", "없으면", "없는", "없었", "없다",
        "동반", "동반되", "외에", "제외", "말고",
        "확인", "존재", "있는지", "있는지만", "여부"
    ])
    has_rca_intent = any(k in query_lower for k in [
        "root cause", "근본 원인", "왜", "rca", "분석해", "분석"
    ])
    has_explicit_system_kill_query = any(k in query_lower for k in [
        "am_kill", "system_kill", "am_wtf", "system_wtf",
        "too many binders", "binder leak", "proxy leak"
    ])
    return (
        has_crash_scope
        and has_absence_intent
        and not has_explicit_system_kill_query
        and not has_rca_intent
    )

def _is_crash_rca_query(query_lower: str) -> bool:
    has_crash_scope = any(k in query_lower for k in [
        "crash", "크래시", "native crash", "네이티브 크래시", "fatal exception",
        "anr", "응답 없음", "앱 응답 없음", "시스템 크래시", "죽", "강제 종료", "강제종료"
    ])
    has_rca_intent = any(k in query_lower for k in [
        "root cause", "근본 원인", "원인", "왜", "rca", "분석해", "분석", "상관", "관련"
    ])
    return has_crash_scope and has_rca_intent and not _is_crash_absence_check_query(query_lower)

def _is_call_drop_trap_query(query_lower: str) -> bool:
    has_call_scope = any(k in query_lower for k in [
        "call_session", "call session", "volte", "ims call", "ps call",
        "call drop", "콜드랍", "통화", "호 종료", "수신", "착신", "발신"
    ])
    has_release_or_reject_evidence = any(k in query_lower for k in [
        "normal_release", "code_user_decline", "code_user_terminated",
        "is_user_reject", "user_reject", "user reject", "user decline",
        "수신 거부", "통화 거절", "사용자 종료", "정상 종료", "정상적인 호 종료"
    ])
    has_misclassification_check = any(k in query_lower for k in [
        "sip_480", "temporarily unavailable", "망 장애", "망장애",
        "call drop", "콜드랍", "장애로 판단", "판단해도", "만 보고",
        "단정", "오판", "trap"
    ])
    return has_call_scope and has_misclassification_check and has_release_or_reject_evidence



def _is_time_context_inference_query(query_lower: str) -> bool:
    has_call_scope = any(k in query_lower for k in [
        "call_session", "call session", "volte", "ims call", "ps call",
        "통화", "호 종료", "콜", "call"
    ])
    has_time_reasoning_scope = any(k in query_lower for k in [
        "시간순", "전후", "이전", "이후", "시점", "동시간", "타임라인",
        "교차 검증", "비교", "현재값만으로", "과거 원인", "과거 통화",
        "before", "after", "timeline", "correlate", "correlation"
    ])
    has_state_transition_scope = any(k in query_lower for k in [
        "radio_power_event", "radio power", "radio_power", "라디오 전원",
        "oos_event", "oos", "망 이탈", "비행기 모드", "airplane_mode_on",
        "airplane mode", "device_property_state", "device property"
    ])
    return has_call_scope and has_time_reasoning_scope and has_state_transition_scope

# DataCall Setup Failure explicit intent matcher
def _is_datacall_setup_query(query_lower: str) -> bool:
    has_datacall_scope = any(k in query_lower for k in [
        "setupdatacall", "setup_data_call", "data call", "datacall",
        "데이터 호", "데이터콜", "데이터 호 연결", "데이터 호 설정",
        "e-pdn", "epdn", "apn 연결", "pdp context"
    ])
    has_failure_or_reason_intent = any(k in query_lower for k in [
        "실패", "거절", "사유", "원인", "명시", "failed", "failure", "reject", "cause", "reason",
        "not_specified", "no carrier", "authentication failed", "user authentication failed"
    ])
    return has_datacall_scope and has_failure_or_reason_intent

def extract_json_object(text: str) -> dict:
    if not text:
        raise ValueError("empty LLM routing response")
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {text[:300]}")
    return json.loads(match.group(0))

def get_semantic_routing(query, routing_map, embed_model):
    chunks = [chunk.strip() for chunk in re.split(r'[\n\.]', query) if len(chunk.strip()) > 5]
    if not chunks:
        chunks = [query]

    category_scores = []
    for category, data in routing_map.items():
        intent_vec = embed_model.encode(data["desc"])
        max_sim = 0.0

        for chunk in chunks:
            chunk_vec = embed_model.encode(chunk)
            sim = np.dot(chunk_vec, intent_vec) / (
                np.linalg.norm(chunk_vec) * np.linalg.norm(intent_vec)
            )
            if sim > max_sim:
                max_sim = sim
        category_scores.append((category, float(max_sim), data))

    category_scores.sort(key=lambda x: x[1], reverse=True)

    selected_tools = set()
    selected_log_types = set()
    selected_intents = set()
    is_hard_matched = False
    query_lower = query.lower()

    if _is_call_drop_trap_query(query_lower):
        selected_intents = {"Call_Drop_Trap"}
        if "Call_Drop_Trap" in routing_map:
            selected_tools = set(routing_map["Call_Drop_Trap"].get("tools", []))
            selected_log_types = set(routing_map["Call_Drop_Trap"].get("log_types", []))
        else:
            selected_tools = {"get_ps_ims_call_analytics"}
            selected_log_types = {"Call_Session"}
        is_hard_matched = True

    elif _is_time_context_inference_query(query_lower):
        selected_intents = {"Time_Context_Inference"}
        if "Time_Context_Inference" in routing_map:
            selected_tools = set(routing_map["Time_Context_Inference"].get("tools", []))
            selected_log_types = set(routing_map["Time_Context_Inference"].get("log_types", []))
        else:
            selected_tools = {"get_ps_ims_call_analytics", "get_radio_power_analytics", "get_network_oos_analytics"}
            selected_log_types = {"Call_Session", "Radio_Power_Event", "OOS_Event", "Device_Property_State"}
        is_hard_matched = True


    elif any(keyword in query_lower for keyword in ["cs call", "cs 통화", "cs 발신", "cs 수신", "cs csfb"]):
        selected_intents = {"Call_Analysis"}
        selected_tools = {"get_cs_call_analytics"}
        selected_log_types = {"Call_Session", "RILJ_Transaction"}
        is_hard_matched = True

    elif _is_datacall_setup_query(query_lower):
        selected_intents = {"Data_Call_Setup"}
        if "Data_Call_Setup" in routing_map:
            selected_tools = set(routing_map["Data_Call_Setup"].get("tools", []))
            selected_log_types = set(routing_map["Data_Call_Setup"].get("log_types", []))
        else:
            selected_tools = {"get_datacall_setup_analytics"}
            selected_log_types = {"SetupDataCall_Failed"}
        is_hard_matched = True

    if any(keyword in query_lower for keyword in [
        "바인더", "binder", "binder transaction", "am_kill", "am_wtf", "system_kill", "system_wtf",
        "proxy leak", "프록시 누수", "바인더 누수", "too many binders", "system kill",
        "ipc", "병목", "bottleneck", "system server 강제 종료", "activitymanager 강제 종료"
    ]) or (
        any(keyword in query_lower for keyword in ["폰", "기기", "시스템", "화면", "터치"])
        and any(keyword in query_lower for keyword in ["먹통", "멈춤", "프리징", "멈췄", "멈춰"])
    ):
        selected_intents = {"System_Kill_WTF"}
        if "System_Kill_WTF" in routing_map:
            selected_tools = set(routing_map["System_Kill_WTF"].get("tools", []))
            selected_log_types = set(routing_map["System_Kill_WTF"].get("log_types", []))
        is_hard_matched = True

    elif any(keyword in query_lower for keyword in [
        "anr", "crash/anr", "crash", "크래시", "앱 죽음", "앱 강제종료", "앱 강제 종료", "죽었",
        "응답 없음", "응답없음", "application not responding", "fatal exception", "watchdog",
        "native crash", "네이티브 크래시", "tombstone", "fatal signal", "sigsegv", "sigabrt",
        "리부팅", "재부팅", "reboot"
    ]):
        selected_intents = {"Crash_ANR"}
        if "Crash_ANR" in routing_map:
            selected_tools = set(routing_map["Crash_ANR"].get("tools", []))
            selected_log_types = set(routing_map["Crash_ANR"].get("log_types", []))
        is_hard_matched = True

    elif any(keyword in query_lower for keyword in [
        "인터넷 먹통", "인터넷", "웹페이지", "데이터 안됨", "데이터가 안", "데이터 안 되고", "데이터가 안 되고", "데이터 멈춤", "데이터가 멈", "데이터 먹통", "데이터 접속 안",
        "data stall", "스톨", "validation", "validation failed", "no internet", "partial connectivity", "private dns", "tcp timeout", "tls handshake", "라우팅", "default network"
    ]):
        selected_intents = {"Internet_Stall"}
        if "Internet_Stall" in routing_map:
            selected_tools = set(routing_map["Internet_Stall"].get("tools", []))
            selected_log_types = set(routing_map["Internet_Stall"].get("log_types", []))
        is_hard_matched = True

    elif any(keyword in query_lower for keyword in ["send_sms", "문자", "sms"]):
        selected_intents = {"RILJ_Request_Failed"}
        if "RILJ_Request_Failed" in routing_map:
            selected_tools = set(routing_map["RILJ_Request_Failed"].get("tools", []))
            selected_log_types = set(routing_map["RILJ_Request_Failed"].get("log_types", []))
        else:
            selected_log_types = {"RILJ_Transaction"}
        is_hard_matched = True

    elif any(keyword in query_lower for keyword in ["비행기 모드", "airplane mode", "flight mode", "radio power", "모뎀 전원", "라디오 파워"]):
        selected_intents = {"Radio_Power"}
        if "Radio_Power" in routing_map:
            selected_tools = set(routing_map["Radio_Power"].get("tools", []))
            selected_log_types = set(routing_map["Radio_Power"].get("log_types", []))
        is_hard_matched = True

    elif any(keyword in query_lower for keyword in [
        "dns", "도메인", "name resolution", "resolve", "lookup", "패킷", "ping", "핑",
        "네트워크 지연", "데이터 느림", "dns 정책", "정책 차단", "effective_policy",
        "is_blocked", "battery_saver", "battery saver", "절전 정책", "백그라운드 데이터 제한",
        "app_standby", "app_background", "reject", "rejected"
    ]):
        selected_intents = {"DNS_Latency"}
        if "DNS_Latency" in routing_map:
            selected_tools = set(routing_map["DNS_Latency"].get("tools", []))
            selected_log_types = set(routing_map["DNS_Latency"].get("log_types", []))
        is_hard_matched = True

    elif any(keyword in query_lower for keyword in ["spacex", "starlink", "ntn", "스페이스엑스"]):
        selected_intents = {"NTN_SpaceX"}
        if "NTN_SpaceX" in routing_map:
            selected_tools = set(routing_map["NTN_SpaceX"].get("tools", []))
            selected_log_types = set(routing_map["NTN_SpaceX"].get("log_types", []))
        is_hard_matched = True

    elif any(keyword in query_lower for keyword in ["tiantong", "티엔통", "천통", "at command", "위성 모뎀"]):
        selected_intents = {"Tiantong_Satellite"}
        if "Tiantong_Satellite" in routing_map:
            selected_tools = set(routing_map["Tiantong_Satellite"].get("tools", []))
            selected_log_types = set(routing_map["Tiantong_Satellite"].get("log_types", []))
        is_hard_matched = True

    elif any(keyword in query_lower for keyword in [
        "nitz", "타임존", "timezone", "time zone", "시간대", "시간 변경",
        "시간 보정", "시간 동기화", "utc", "utc+", "utc offset", "offset",
        "핑퐁", "ping-pong", "pingpong", "네트워크 시간"
    ]):
        selected_intents = {"Nitz_Time_Analysis"}
        selected_tools = set()
        selected_log_types = {"Nitz_Time_Event"}
        is_hard_matched = True

    if not is_hard_matched:
        threshold = 0.52
        multi_threshold = 0.50

        if not category_scores or category_scores[0][1] < threshold:
            selected_intents.add("Fallback_General")
            selected_tools.update(["get_cs_call_analytics", "get_network_oos_analytics", "get_dns_latency_analytics"])
            selected_log_types.update(["Call_Session", "OOS_Event", "Signal_Level", "Network_Timeline_Stat", "Network_DNS_Issue"])
        else:
            top1_cat, top1_score, top1_data = category_scores[0]
            selected_intents.add(top1_cat)
            selected_tools.update(top1_data["tools"])
            selected_log_types.update(top1_data["log_types"])

            if len(category_scores) > 1 and category_scores[1][1] >= multi_threshold:
                top2_cat, top2_score, top2_data = category_scores[1]
                selected_intents.add(top2_cat)
                selected_tools.update(top2_data["tools"])
                selected_log_types.update(top2_data["log_types"])

    if (
        any(keyword in query_lower for keyword in ["oos", "망 이탈", "통신이 멈", "통신 멈춤", "음영", "기지국"])
        and any(keyword in query_lower for keyword in ["rild", "native crash", "sigsegv", "단말 내부", "root cause", "원인"])
    ):
        selected_intents.update(["Crash_ANR", "Network_OOS"])
        selected_tools.update(["get_crash_anr_analytics", "get_network_oos_analytics"])
        selected_log_types.update(["OOS_Event", "Native_Crash_Event", "RILJ_Transaction"])

    if (
        any(keyword in query_lower for keyword in ["oos", "망 이탈", "통신이 멈", "통신 멈춤", "음영", "기지국"])
        and any(keyword in query_lower for keyword in [
            "binder", "바인더", "am_kill", "am_wtf", "system_kill", "system_wtf",
            "proxy leak", "프록시 누수", "바인더 누수", "too many binders"
        ])
    ):
        selected_intents.update(["System_Kill_WTF", "Network_OOS"])
        selected_tools.update(["get_binder_warning_analytics", "get_network_oos_analytics"])
        selected_log_types.update(["System_Kill_Wtf_Event", "Binder_Warning", "RCA_Event", "OOS_Event", "RILJ_Transaction"])

    if _is_crash_rca_query(query_lower):
        selected_log_types.add("RCA_Event")
        if any(keyword in query_lower for keyword in [
            "binder", "바인더", "am_kill", "am_wtf", "system_kill", "system_wtf",
            "proxy leak", "프록시 누수", "바인더 누수", "too many binders", "ipc", "병목", "bottleneck"
        ]):
            selected_intents.add("System_Kill_WTF")
            selected_tools.add("get_binder_warning_analytics")
            selected_log_types.update(["System_Kill_Wtf_Event", "Binder_Warning"])

    if _is_time_context_inference_query(query_lower):
        selected_intents.difference_update(["Radio_Power", "Call_Analysis", "Network_OOS"])
        selected_intents.add("Time_Context_Inference")
        if "Time_Context_Inference" in routing_map:
            selected_tools.update(routing_map["Time_Context_Inference"].get("tools", []))
            selected_log_types.update(routing_map["Time_Context_Inference"].get("log_types", []))
        else:
            selected_tools.update(["get_radio_power_analytics", "get_ps_ims_call_analytics", "get_network_oos_analytics"])
            selected_log_types.update(["Device_Property_State", "Call_Session", "Radio_Power_Event", "OOS_Event"])
        selected_log_types.discard("IMS_SIP_Message")

    if any(keyword in query_lower for keyword in ["비행기 모드", "airplane mode", "flight mode"]):
        if "Radio_Power" in routing_map:
            selected_intents.add("Radio_Power")
            selected_tools.update(routing_map["Radio_Power"].get("tools", []))
            selected_log_types.update(routing_map["Radio_Power"].get("log_types", []))
        if any(keyword in query_lower for keyword in [
            "네트워크", "망", "복구", "안됨", "안 되고", "안되고", "되지 않", "실패"
        ]):
            selected_intents.add("Network_OOS")
            if "Network_OOS" in routing_map:
                selected_tools.update(routing_map["Network_OOS"].get("tools", []))
                selected_log_types.update(routing_map["Network_OOS"].get("log_types", []))

    if any(keyword in query_lower for keyword in ["ril", "rilj", "모뎀", "명령어", "타임아웃", "딜레이", "지연", "응답"]):
        selected_log_types.add("RILJ_Transaction")

    if not selected_tools and not selected_log_types:
        selected_intents.add("Fallback_General")
        selected_tools.update(["get_cs_call_analytics", "get_network_oos_analytics", "get_dns_latency_analytics"])
        selected_log_types.update(["Call_Session", "OOS_Event", "Signal_Level", "Network_Timeline_Stat", "Network_DNS_Issue"])

    routing_scores = {category: float(score) for category, score, _ in category_scores}
    top_matches = [{"intent": category, "score": float(score)} for category, score, _ in category_scores[:3]]

    return {
        "intents": sorted(list(selected_intents)),
        "tools": sorted(list(selected_tools)),
        "log_types": sorted(list(selected_log_types)),
        "scores": routing_scores,
        "top_matches": top_matches
    }

def get_llm_routing(query: str, routing_map: dict, llm_model_name: str) -> dict:
    allowed_tools = set()
    allowed_log_types = set()
    allowed_intents = set(routing_map.keys())

    for intent, data in routing_map.items():
        allowed_tools.update(data.get("tools", []))
        allowed_log_types.update(data.get("log_types", []))

    prompt = f"""
너는 Android Telephony 로그 분석 라우터다.
사용자 질문을 보고 필요한 intent/tools/log_types를 JSON으로만 반환하라.
사용 가능한 intent: {sorted(list(allowed_intents))}
사용 가능한 tools: {sorted(list(allowed_tools))}
사용 가능한 log_types: {sorted(list(allowed_log_types))}
반드시 JSON만 출력: {{"intents": [], "tools": [], "log_types": [], "reason": ""}}
사용자 질문: {query}
"""
    try:
        res = ollama.chat(
            model=llm_model_name,
            messages=[{"role": "user", "content": prompt}],
            format="json",
            options={"num_ctx": 4096, "temperature": 0.0},
        )
        content = res["message"]["content"].strip()
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL | re.IGNORECASE).strip()

        parsed = extract_json_object(content)
        return {
            "intents": sorted(list(set(parsed.get("intents", [])) & allowed_intents)),
            "tools": sorted(list(set(parsed.get("tools", [])) & allowed_tools)),
            "log_types": sorted(list(set(parsed.get("log_types", [])) & allowed_log_types)),
            "reason": parsed.get("reason", ""),
            "raw": content,
        }
    except Exception as e:
        return {"intents": [], "tools": [], "log_types": [], "reason": f"LLM routing failed: {e}", "raw": content if "content" in locals() else ""}

def get_hybrid_routing(query: str, routing_map: dict, embed_model, llm_model_name: str) -> dict:
    semantic = get_semantic_routing(query, routing_map, embed_model)
    llm_route = get_llm_routing(query, routing_map, llm_model_name)
    merged_intents = set(semantic.get("intents", []))
    merged_tools = set(semantic.get("tools", []))
    merged_log_types = set(semantic.get("log_types", []))
    merged_intents.update(llm_route.get("intents", []))
    merged_tools.update(llm_route.get("tools", []))
    merged_log_types.update(llm_route.get("log_types", []))

    # Hard override: SetupDataCall/DataCall explicit failure questions must not be diluted
    # by Internet_Stall/DNS routing. Retrieval and tool context should focus on setup failure facts.
    query_lower = query.lower()
    if _is_datacall_setup_query(query_lower):
        merged_intents = {"Data_Call_Setup"}
        if "Data_Call_Setup" in routing_map:
            merged_tools = set(routing_map["Data_Call_Setup"].get("tools", []))
            merged_log_types = set(routing_map["Data_Call_Setup"].get("log_types", []))
        else:
            merged_tools = {"get_datacall_setup_analytics"}
            merged_log_types = {"SetupDataCall_Failed"}

    return {
        "intents": sorted(merged_intents),
        "tools": sorted(merged_tools),
        "log_types": sorted(merged_log_types),
        "semantic_routing": semantic,
        "llm_routing": llm_route,
        "routing_mode": "hybrid",
    }
