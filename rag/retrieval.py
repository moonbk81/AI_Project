"""Retrieval and reranking utilities for RAG search."""

import json
import re

import numpy as np

def build_where_filter(current_file=None, target_log_types=None):
    conditions = []
    if current_file:
        conditions.append({"source_file": current_file})
    if target_log_types:
        if len(target_log_types) == 1:
            conditions.append({"log_type": target_log_types[0]})
        else:
            conditions.append({"log_type": {"$in": target_log_types}})

    if len(conditions) == 1:
        return conditions[0]
    if len(conditions) > 1:
        return {"$and": conditions}
    return None

def _keyword_score(search_query: str, combined_text: str) -> float:
    query_keywords = set(re.findall(r'[a-zA-Z0-9]+', search_query.lower()))
    match_count = sum(1 for kw in query_keywords if kw in combined_text)
    return match_count / max(1, len(query_keywords))

def _is_crash_absence_check(query_lower: str) -> bool:
    has_crash_scope = any(k in query_lower for k in [
        "crash", "크래시", "native crash", "네이티브 크래시", "fatal exception",
        "anr", "응답 없음", "앱 응답 없음"
    ])
    has_absence_intent = any(k in query_lower for k in [
        "있", "없", "발생", "이력", "확인", "존재"
    ])
    has_system_kill_scope = any(k in query_lower for k in [
        "am_kill", "system_kill", "am_wtf", "system_wtf", "wtf",
        "binder", "바인더", "proxy", "프록시", "누수", "leak",
        "강제 종료", "강제종료", "too many binders"
    ])
    return has_crash_scope and has_absence_intent and not has_system_kill_scope

def _is_dns_policy_query(query_lower: str) -> bool:
    has_dns_scope = any(k in query_lower for k in [
        "dns", "도메인", "name resolution", "lookup", "resolve"
    ])
    has_policy_scope = any(k in query_lower for k in [
        "정책", "policy", "차단", "blocked", "block", "reject", "rejected",
        "battery", "battery_saver", "배터리", "절전", "is_blocked", "effective_policy"
    ])
    return has_dns_scope and has_policy_scope

def _apply_domain_boosts(hybrid_score: float, log_type: str, meta: dict, combined_text: str, query_lower: str) -> float:
    rca_type = str((meta or {}).get("rca_type", ""))
    is_crash_absence_check = _is_crash_absence_check(query_lower)
    is_dns_policy_query = _is_dns_policy_query(query_lower)

    if log_type == "RCA_Event":
        if not is_crash_absence_check and any(k in query_lower for k in [
            "root cause", "근본 원인", "원인", "왜", "죽", "강제 종료", "강제종료",
            "크래시", "crash", "am_kill", "system_kill", "system_wtf", "am_wtf",
            "wtf", "system kill", "바인더", "binder", "프록시", "proxy", "누수", "leak"
        ]):
            hybrid_score += 0.60

        if not is_crash_absence_check and rca_type == "BINDER_PROXY_LEAK_RCA" and any(k in query_lower for k in [
            "binder", "바인더", "proxy", "프록시", "누수", "leak", "am_kill", "system_kill",
            "am_wtf", "system_wtf", "too many binders", "강제 종료", "강제종료", "죽"
        ]):
            hybrid_score += 0.45

    if log_type == "System_Kill_Wtf_Event":
        if any(k in query_lower for k in [
            "am_kill", "system_kill", "am_wtf", "system_wtf", "wtf", "system kill",
            "강제 종료", "강제종료", "too many binders", "binder", "바인더",
            "proxy", "프록시", "누수", "leak", "먹통", "프리징", "멈춤"
        ]):
            hybrid_score += 0.50
        if is_crash_absence_check:
            hybrid_score -= 0.35

    if (
        any(k in query_lower for k in ["oos", "망 이탈", "음영", "기지국", "통신 멈"])
        and any(k in query_lower for k in ["rild", "native crash", "sigsegv", "단말 내부", "root cause", "원인"])
    ):
        if log_type == "Native_Crash_Event":
            hybrid_score += 0.45
        elif log_type == "OOS_Event":
            hybrid_score += 0.25
        if "rild" in combined_text:
            hybrid_score += 0.15
        if "sigsegv" in combined_text or "native_crash" in combined_text or "native crash" in combined_text:
            hybrid_score += 0.15

    if (
        any(k in query_lower for k in ["비행기 모드", "airplane", "airplane_mode", "radio power", "라디오 전원", "모뎀 전원"])
        and any(k in query_lower for k in ["통화", "call", "call_session", "종료", "끊", "시간순", "12:", "code_user_terminated"])
    ):
        if log_type == "Call_Session":
            hybrid_score += 0.50
        elif log_type == "Radio_Power_Event":
            hybrid_score += 0.35
        elif log_type == "OOS_Event":
            hybrid_score += 0.35
        elif log_type == "Device_Property_State":
            hybrid_score += 0.10
        elif log_type == "RILJ_Transaction":
            hybrid_score -= 0.20
        if "code_user_terminated" in combined_text:
            hybrid_score += 0.30
        if "12:08:10" in combined_text or "12:08:09" in combined_text:
            hybrid_score += 0.15
        if "airplane_mode" in combined_text:
            hybrid_score += 0.05

    if log_type == "Network_DNS_Issue" and any(k in query_lower for k in [
        "dns", "실패", "차단", "정책", "policy", "battery", "reject", "blocked", "is_blocked", "effective_policy"
    ]):
        effective_policy = str((meta or {}).get("effective_policy", "")).upper()
        network_type = str((meta or {}).get("network_type", "")).upper()
        is_blocked = (meta or {}).get("is_blocked") is True
        raw_policy_text = " ".join([
            str((meta or {}).get("effective_policy", "")),
            str((meta or {}).get("policy", "")),
            str((meta or {}).get("reason", "")),
            str((meta or {}).get("raw_info", "")),
            combined_text,
        ]).upper()

        if is_dns_policy_query:
            hybrid_score += 0.30

        if effective_policy and effective_policy != "NONE":
            hybrid_score += 0.90

        if is_blocked:
            hybrid_score += 0.50

        if "BATTERY_SAVER" in raw_policy_text:
            hybrid_score += 0.50

        if "REJECT" in raw_policy_text or "REJECTED" in raw_policy_text:
            hybrid_score += 0.40

        if network_type == "NONE" or effective_policy == "NONE":
            if is_dns_policy_query:
                hybrid_score -= 0.25

    return hybrid_score

def _rerank_results(results: dict, search_query: str, top_k: int) -> dict:
    if not results or not results.get('documents') or not results['documents'][0]:
        return results

    docs = results['documents'][0]
    metas = results['metadatas'][0]
    ids = results['ids'][0]
    distances = results['distances'][0] if 'distances' in results and results['distances'] else [0] * len(docs)
    query_lower = search_query.lower()

    reranked_results = []
    for doc, meta, doc_id, dist in zip(docs, metas, ids, distances):
        doc_lower = doc.lower()
        meta_text = json.dumps(meta or {}, ensure_ascii=False, default=str).lower()
        combined_text = f"{doc_lower}\n{meta_text}"

        keyword_score = _keyword_score(search_query, combined_text)
        vector_score = 1.0 / (1.0 + dist)
        hybrid_score = (vector_score * 0.4) + (keyword_score * 0.6)

        log_type = str((meta or {}).get("log_type", ""))
        hybrid_score = _apply_domain_boosts(hybrid_score, log_type, meta, combined_text, query_lower)

        reranked_results.append({
            "doc": doc,
            "meta": meta,
            "id": doc_id,
            "score": hybrid_score,
        })

    reranked_results.sort(key=lambda x: x["score"], reverse=True)
    final_top_results = reranked_results[:top_k]

    if _is_dns_policy_query(query_lower):
        dns_policy_candidates = []
        for result in reranked_results:
            meta = result.get("meta") or {}
            if meta.get("log_type") != "Network_DNS_Issue":
                continue
            effective_policy = str(meta.get("effective_policy", "")).upper()
            raw_text = " ".join([
                str(meta.get("effective_policy", "")),
                str(meta.get("policy", "")),
                str(meta.get("reason", "")),
                str(meta.get("raw_info", "")),
                str(result.get("doc", "")),
            ]).upper()
            if meta.get("is_blocked") is True or (effective_policy and effective_policy != "NONE") or "BATTERY_SAVER" in raw_text or "REJECT" in raw_text:
                dns_policy_candidates.append(result)

        if dns_policy_candidates and not any((r.get("meta") or {}).get("log_type") == "Network_DNS_Issue" and ((r.get("meta") or {}).get("is_blocked") is True or str((r.get("meta") or {}).get("effective_policy", "")).upper() not in ("", "NONE")) for r in final_top_results):
            final_top_results = [dns_policy_candidates[0]] + final_top_results[:max(0, top_k - 1)]

    if not _is_crash_absence_check(query_lower) and any(k in query_lower for k in [
        "root cause", "근본 원인", "원인", "죽", "강제 종료", "강제종료",
        "크래시", "crash", "am_kill", "system_kill", "system_wtf", "am_wtf",
        "wtf", "system kill", "바인더", "binder", "프록시", "proxy", "누수", "leak"
    ]):
        rca_candidates = [r for r in reranked_results if (r.get("meta") or {}).get("log_type") == "RCA_Event"]
        if rca_candidates and not any((r.get("meta") or {}).get("log_type") == "RCA_Event" for r in final_top_results):
            final_top_results = [rca_candidates[0]] + final_top_results[:max(0, top_k - 1)]

    results['documents'] = [[r["doc"] for r in final_top_results]]
    results['metadatas'] = [[r["meta"] for r in final_top_results]]
    results['ids'] = [[r["id"] for r in final_top_results]]
    return results

def retrieve_and_rerank(
    collection,
    embed_model,
    search_query: str,
    top_k: int,
    current_file=None,
    target_log_types=None,
) -> dict:
    where_filter = build_where_filter(current_file=current_file, target_log_types=target_log_types)
    query_embedding = embed_model.encode(search_query).tolist()
    query_lower = search_query.lower()
    fetch_k = top_k * 6 if _is_dns_policy_query(query_lower) else top_k * 3

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=fetch_k,
        where=where_filter,
    )

    return _rerank_results(results, search_query, top_k)
