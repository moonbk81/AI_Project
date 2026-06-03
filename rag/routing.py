"""Routing utilities extracted from ril_rag_chat.py."""

import json
import re

import numpy as np
import ollama


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

    if any(keyword in query_lower for keyword in ["cs call", "cs 통화", "cs 발신", "cs 수신", "cs csfb"]):
        selected_intents = {"Call_Analysis"}
        selected_tools = {"get_cs_call_analytics"}
        selected_log_types = {"Call_Session", "RILJ_Transaction"}
        is_hard_matched = True

    elif any(keyword in query_lower for keyword in ["ps call", "ps 통화", "volte", "ims", "sip", "보이스오버"]):
        selected_intents = {"Call_Analysis"}
        selected_tools = {"get_ps_ims_call_analytics"}
        selected_log_types = {"Call_Session", "IMS_SIP_Message", "RILJ_Transaction"}
        is_hard_matched = True

    if any(keyword in query_lower for keyword in [
        "anr", "crash/anr", "crash", "크래시", "강제종료", "강제 종료", "앱 죽음", "폰 죽음", "죽었",
        "응답 없음", "응답없음", "application not responding", "fatal exception", "watchdog", "프리징",
        "바인더", "binder", "transaction", "am_kill", "am_wtf", "proxy leak", "프록시 누수", "바인더 누수"
    ]):
        selected_intents = {"Crash_ANR"}
        if "Crash_ANR" in routing_map:
            selected_tools = set(routing_map["Crash_ANR"].get("tools", []))
            selected_log_types = set(routing_map["Crash_ANR"].get("log_types", []))
        is_hard_matched = True

    elif any(keyword in query_lower for keyword in ["인터넷", "먹통", "웹페이지", "데이터 안됨", "데이터가 안", "데이터 안 되고", "데이터가 안 되고", "데이터 멈춤", "데이터가 멈", "데이터 먹통", "데이터 접속 안", "data stall", "스톨", "validation", "validation failed", "no internet", "partial connectivity", "private dns", "tcp timeout", "tls handshake", "라우팅", "default network", "setupdatacall"]):
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

    elif any(keyword in query_lower for keyword in ["dns", "패킷", "ping", "핑", "네트워크 지연", "데이터 느림"]):
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

    elif any(keyword in query_lower for keyword in ["nitz", "타임존", "시간대", "시간 변경", "핑퐁"]):
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
        any(keyword in query_lower for keyword in ["비행기 모드", "airplane", "airplane_mode", "radio power", "라디오 전원", "모뎀 전원"])
        and any(keyword in query_lower for keyword in ["통화", "call", "call_session", "종료", "끊", "시간순", "12:", "code_user_terminated"])
    ):
        selected_intents.update(["Radio_Power", "Call_Analysis", "Network_OOS"])
        selected_tools.update(["get_radio_power_analytics", "get_ps_ims_call_analytics", "get_network_oos_analytics"])
        selected_log_types.update(["Device_Property_State", "Call_Session", "Radio_Power_Event", "OOS_Event", "IMS_SIP_Message"])

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
    return {
        "intents": sorted(merged_intents),
        "tools": sorted(merged_tools),
        "log_types": sorted(merged_log_types),
        "semantic_routing": semantic,
        "llm_routing": llm_route,
        "routing_mode": "hybrid",
    }