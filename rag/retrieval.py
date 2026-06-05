"""Retrieval and reranking utilities for RAG search."""

import json
import re

import numpy as np

from rag.query_classifiers import (
    is_binder_proxy_count_query,
    is_binder_query,
    is_call_release_misclassification_query,
    is_crash_absence_check,
    is_datacall_failure_query,
    is_dns_policy_query,
    is_negative_binder_leak_check_query,
    is_nitz_query,
    is_time_context_inference_query,
)

from rag.domain_boosts import apply_domain_boosts
from rag.rerank_injections import apply_rerank_injections

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
        hybrid_score = apply_domain_boosts(hybrid_score, log_type, meta, combined_text, query_lower)

        reranked_results.append({
            "doc": doc,
            "meta": meta,
            "id": doc_id,
            "score": hybrid_score,
        })

    reranked_results.sort(key=lambda x: x["score"], reverse=True)
    final_top_results = reranked_results[:top_k]

    final_top_results = apply_rerank_injections(
        reranked_results=reranked_results,
        final_top_results=final_top_results,
        query_lower=query_lower,
        top_k=top_k,
    )

    if not is_crash_absence_check(query_lower) and any(k in query_lower for k in [
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
    query_lower = search_query.lower()

    effective_target_log_types = target_log_types

    if is_crash_absence_check(query_lower):
        effective_target_log_types = [
            "Native_Crash_Event",
            "Crash_Event",
            "ANR_Context",
        ]
    elif is_call_release_misclassification_query(query_lower):
        effective_target_log_types = ["Call_Session", "CS_Call_Session", "PS_Call_Session"]
    elif is_time_context_inference_query(query_lower):
        effective_target_log_types = [
            "Call_Session",
            "CS_Call_Session",
            "PS_Call_Session",
            "Radio_Power_Event",
            "OOS_Event",
            "Device_Property_State",
        ]

    where_filter = build_where_filter(
        current_file=current_file,
        target_log_types=effective_target_log_types,
    )

    query_embedding = embed_model.encode(search_query).tolist()
    if is_dns_policy_query(query_lower):
        fetch_k = max(top_k * 6, 24)
    elif is_time_context_inference_query(query_lower):
        fetch_k = max(top_k * 6, 24)
    elif is_binder_proxy_count_query(query_lower) or is_negative_binder_leak_check_query(query_lower):
        fetch_k = max(top_k * 8, 32)
    elif is_call_release_misclassification_query(query_lower) or is_datacall_failure_query(query_lower) or is_nitz_query(query_lower) or is_binder_query(query_lower):
        fetch_k = max(top_k * 5, 20)
    else:
        fetch_k = max(top_k * 4, 16)

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=fetch_k,
        where=where_filter,
    )

    return _rerank_results(results, search_query, top_k)
