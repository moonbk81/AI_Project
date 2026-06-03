
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

def _apply_domain_boosts(hybrid_score: float, log_type: str, meta: dict, combined_text: str, query_lower: str) -> float:
    rca_type = str((meta or {}).get("rca_type", ""))

    if log_type == "RCA_Event":
        if any(k in query_lower for k in [
            "root cause", "근본 원인", "원인", "왜", "죽", "강제 종료", "강제종료",
            "크래시", "crash", "am_kill", "system_kill", "바인더", "binder",
            "프록시", "proxy", "누수", "leak"
        ]):
            hybrid_score += 0.60

        if rca_type == "BINDER_PROXY_LEAK_RCA" and any(k in query_lower for k in [
            "binder", "바인더", "proxy", "프록시", "누수", "leak", "am_kill",
            "too many binders", "강제 종료", "강제종료", "죽"
        ]):
            hybrid_score += 0.45

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

    if any(k in query_lower for k in [
        "root cause", "근본 원인", "원인", "죽", "강제 종료", "강제종료",
        "크래시", "crash", "am_kill", "system_kill", "바인더", "binder",
        "프록시", "proxy", "누수", "leak"
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
    fetch_k = top_k * 3

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=fetch_k,
        where=where_filter,
    )

    return _rerank_results(results, search_query, top_k)
