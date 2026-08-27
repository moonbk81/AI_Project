"""Retrieval and reranking utilities for RAG search."""

import json
import os

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
    extract_metadata_filters,
)

from rag.domain_boosts import apply_domain_boosts
from rag.keyword_match import build_keyword_scorer
from rag.rerank_injections import apply_rerank_injections

# 질문에 가장 가까운 문서조차 이 거리보다 멀면 근거로 취급하지 않는다.
#
# 실측 근거 (bge-m3, Chroma 기본 space=l2 이므로 squared L2, 960문서 코퍼스):
#   로그와 관련된 한글 질의 10개의 최근접 거리  0.643 ~ 0.933
#   로그와 무관한 질의 8개의 최근접 거리        1.067 ~ 1.319
# 그 사이를 잡아 양쪽에 0.067 여유를 둔다. 코퍼스와 임베딩 모델에 딸린 값이라
# 환경변수로 덮을 수 있게 둔다.
WEAK_EVIDENCE_DISTANCE = float(os.getenv("RAG_WEAK_EVIDENCE_DISTANCE", "1.0"))

# 라우팅이 고른 log_type 에 주는 가점.
#
# 예전에는 이 유형만 Chroma where 로 남겼다. 그래서 키워드 조합으로 만든 분류기가
# 한 번 오판하면 정답 문서가 후보에서 아예 사라져 리콜이 0 이 됐다. 유한한 가점으로
# 바꾸면 오판했을 때 순위가 밀리기만 하고 후보에서 빠지지는 않는다.
# 키워드 항의 최대 기여가 0.6, 벡터 항이 약 0.4 인 것에 맞춰 그보다 작게 잡는다.
ROUTED_LOG_TYPE_BONUS = 0.25


def build_where_filter(current_file=None, target_log_types=None, filters_dict=None):
    conditions = []
    if current_file:
        conditions.append({"source_file": current_file})
    if target_log_types:
        if len(target_log_types) == 1:
            conditions.append({"log_type": target_log_types[0]})
        else:
            conditions.append({"log_type": {"$in": target_log_types}})

    # Regex로 추출한 수치 조건이 있다면 ChromaDB '$gte' 연산자 추가
    if filters_dict and 'min_dns_avg' in filters_dict:
        conditions.append({"dns_avg": {"$gte": filters_dict['min_dns_avg']}})

    if len(conditions) == 1:
        return conditions[0]
    if len(conditions) > 1:
        return {"$and": conditions}
    return None


def _result_rows(results: dict):
    """Chroma 결과를 (id, doc, meta, dist) 튜플 리스트로 편다."""
    if not results or not results.get("documents") or not results["documents"][0]:
        return []

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    ids = results["ids"][0]
    if results.get("distances") and results["distances"][0] is not None:
        distances = results["distances"][0]
    else:
        distances = [0] * len(docs)

    return list(zip(ids, docs, metas, distances))


def merge_query_results(primary: dict, secondary: dict) -> dict:
    """두 검색 결과를 id 기준으로 합친다. primary 에 있던 문서를 앞에 둔다.

    log_type 을 좁힌 검색과 좁히지 않은 검색을 함께 쓰기 위한 것이다. 좁힌 검색은
    소수 유형(문서 4건짜리 SetupDataCall_Failed 같은)을 건져 올리는 데 필요하고,
    좁히지 않은 검색은 분류기가 틀렸을 때의 안전망이다.
    """
    merged = {}
    for source in (primary, secondary):
        for doc_id, doc, meta, dist in _result_rows(source):
            if doc_id not in merged:
                merged[doc_id] = (doc, meta, dist)

    if not merged:
        return primary if primary else {
            "ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]],
        }

    ids = list(merged.keys())
    return {
        "ids": [ids],
        "documents": [[merged[i][0] for i in ids]],
        "metadatas": [[merged[i][1] for i in ids]],
        "distances": [[merged[i][2] for i in ids]],
    }


def _rerank_results(
    results: dict,
    search_query: str,
    top_k: int,
    preferred_log_types=None,
) -> dict:
    rows = _result_rows(results)
    if not rows:
        return results

    query_lower = search_query.lower()
    preferred = {str(t) for t in (preferred_log_types or [])}

    candidates = []
    for doc_id, doc, meta, dist in rows:
        doc_lower = (doc or "").lower()
        # 부스트 규칙은 metadata 의 키 이름까지 보고 판단하는 것이 있어(is_user_reject 처럼
        # 키 존재 자체가 신호) 기존 텍스트를 그대로 쓴다.
        meta_dump = json.dumps(meta or {}, ensure_ascii=False, default=str).lower()
        # 반면 키워드 점수에는 값만 넣는다. 키 이름을 같이 넣으면 root_cause 라는 키를
        # 가진 유형이 "원인"(-> cause) 이 들어간 모든 질의에서 만점을 받는다. 실제로
        # "앱이 죽은 근본 원인이 뭐야" 에서 Binder_Warning 이 그렇게 1.00 을 받아
        # 벡터 최근접인 System_Kill_Wtf_Event 를 밀어냈다. 내용이 아니라 스키마가
        # 순위를 정하는 셈이라 값만 본다.
        meta_values = " ".join(str(v) for v in (meta or {}).values()).lower()
        candidates.append((
            doc, meta, doc_id, dist,
            f"{doc_lower}\n{meta_dump}",
            f"{doc_lower}\n{meta_values}",
        ))

    # 키워드 가중치는 후보 집합 전체를 봐야 정해지므로(IDF) 점수 루프 전에 만든다.
    keyword_scorer = build_keyword_scorer(search_query, [c[5] for c in candidates])

    reranked_results = []
    for doc, meta, doc_id, dist, combined_text, keyword_text in candidates:
        keyword_score = keyword_scorer(keyword_text)
        vector_score = 1.0 / (1.0 + dist)
        hybrid_score = (vector_score * 0.4) + (keyword_score * 0.6)

        log_type = str((meta or {}).get("log_type", ""))
        hybrid_score = apply_domain_boosts(hybrid_score, log_type, meta, combined_text, query_lower)

        if log_type in preferred:
            hybrid_score += ROUTED_LOG_TYPE_BONUS

        reranked_results.append({
            "doc": doc,
            "meta": meta,
            "id": doc_id,
            "dist": dist,
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

    # 근거 강도는 리랭킹 점수가 아니라 원본 거리로 판단한다. 점수에는 도메인 부스트가
    # 얹혀 있어서 아무 관련 없는 문서도 규칙에 걸리면 높은 점수를 받을 수 있다.
    best_distance = min((r["dist"] for r in reranked_results), default=None)

    results['documents'] = [[r["doc"] for r in final_top_results]]
    results['metadatas'] = [[r["meta"] for r in final_top_results]]
    results['ids'] = [[r["id"] for r in final_top_results]]
    # 예전에는 fetch_k 개짜리 distances 가 top_k 개짜리 documents 옆에 그대로 남아
    # 길이가 어긋났다. 최종 순서에 맞춰 다시 채운다.
    results['distances'] = [[r["dist"] for r in final_top_results]]
    results['best_distance'] = best_distance
    results['evidence_is_weak'] = (
        best_distance is not None and best_distance > WEAK_EVIDENCE_DISTANCE
    )
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

    # 1. 정규표현식으로 필터 조건 가볍게 추출
    extracted_filters = extract_metadata_filters(query_lower)

    # 아래에서 append 로 유형을 덧붙이므로 반드시 사본으로 시작한다. 호출자가 넘기는
    # 리스트는 config.yaml 의 routing_map 에 들어 있는 그 객체라서, 그대로 붙이면
    # 프로세스가 사는 동안 라우팅 설정이 조용히 오염된다.
    effective_target_log_types = list(target_log_types) if target_log_types else None

    if is_crash_absence_check(query_lower):
        effective_target_log_types = [
            "Native_Crash_Event",
            "Crash_Event",
            "ANR_Context",
        ]
    elif is_call_release_misclassification_query(query_lower):
        effective_target_log_types = ["Call_Session"]
    elif is_time_context_inference_query(query_lower):
        effective_target_log_types = [
            "Call_Session",
            "Radio_Power_Event",
            "OOS_Event",
            "Device_Property_State",
        ]
    if is_datacall_failure_query(query_lower):
        if effective_target_log_types is None:
            effective_target_log_types = []
        if "Data_Call_Setup_Event" not in effective_target_log_types:
            effective_target_log_types.append("Data_Call_Setup_Event")
        if "Internet_Stall_Analysis" not in effective_target_log_types:
            effective_target_log_types.append("Internet_Stall_Analysis")

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
        where=build_where_filter(
            current_file=current_file,
            target_log_types=effective_target_log_types,
            filters_dict=extracted_filters,
        ),
    )

    if effective_target_log_types:
        # log_type 을 걸지 않은 같은 크기의 검색을 한 번 더 해서 후보에 합친다.
        # 분류기가 오판해도 정답 문서가 후보에서 사라지지 않게 하는 안전망이다.
        # 비용은 Chroma 검색 한 번(수 ~ 수십 ms)이고, 순위는 리랭커가 정한다.
        open_results = collection.query(
            query_embeddings=[query_embedding],
            n_results=fetch_k,
            where=build_where_filter(
                current_file=current_file,
                target_log_types=None,
                filters_dict=extracted_filters,
            ),
        )
        results = merge_query_results(results, open_results)

    return _rerank_results(
        results,
        search_query,
        top_k,
        preferred_log_types=effective_target_log_types,
    )
