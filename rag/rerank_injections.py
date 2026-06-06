
"""Forced top-k injection rules for retrieval reranking.

This module keeps `_rerank_results()` in `retrieval.py` focused on generic scoring,
while domain-specific must-include evidence rules live here.
"""

import json

from rag.query_classifiers import (
    is_binder_proxy_count_query,
    is_binder_query,
    is_call_release_misclassification_query,
    is_datacall_failure_query,
    is_dns_policy_query,
    is_negative_binder_leak_check_query,
    is_nitz_query,
)

def _stable_item_id(item: dict) -> str:
    return (
        item.get("id")
        or json.dumps(item.get("meta", {}), ensure_ascii=False, default=str)
        + str(item.get("doc", ""))[:80]
    )

def _merge_top_results(forced_results: list, final_top_results: list, top_k: int) -> list:
    merged = []
    seen_ids = set()
    for item in forced_results + final_top_results:
        item_id = _stable_item_id(item)
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        merged.append(item)
    return merged[:top_k]

def apply_rerank_injections(
    reranked_results: list,
    final_top_results: list,
    query_lower: str,
    top_k: int,
) -> list:
    """Force critical evidence into final top-k results for known domain patterns."""

    if is_dns_policy_query(query_lower):
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
            if (
                meta.get("is_blocked") is True
                or (effective_policy and effective_policy != "NONE")
                or "BATTERY_SAVER" in raw_text
                or "REJECT" in raw_text
            ):
                dns_policy_candidates.append(result)

        if dns_policy_candidates and not any(
            (r.get("meta") or {}).get("log_type") == "Network_DNS_Issue"
            and (
                (r.get("meta") or {}).get("is_blocked") is True
                or str((r.get("meta") or {}).get("effective_policy", "")).upper()
                not in ("", "NONE")
            )
            for r in final_top_results
        ):
            final_top_results = [dns_policy_candidates[0]] + final_top_results[:max(0, top_k - 1)]

    if is_call_release_misclassification_query(query_lower):
        call_candidates = []
        for result in reranked_results:
            meta = result.get("meta") or {}
            log_type = str(meta.get("log_type", ""))
            text = " ".join([
                str(result.get("doc", "")),
                json.dumps(meta, ensure_ascii=False, default=str),
            ]).lower()
            if log_type == "Call_Session" or any(k in text for k in [
                "normal_release", "code_user_decline", "code_user_terminated",
                "sip_480", "temporarily unavailable", "is_user_reject", "user_reject"
            ]):
                call_candidates.append(result)

        if call_candidates and not any(
            (r.get("meta") or {}).get("log_type") == "Call_Session"
            or any(k in str(r.get("doc", "")).lower() for k in [
                "normal_release", "code_user_decline", "code_user_terminated", "sip_480", "is_user_reject"
            ])
            for r in final_top_results
        ):
            final_top_results = [call_candidates[0]] + final_top_results[:max(0, top_k - 1)]

    if is_datacall_failure_query(query_lower):
        datacall_candidates = []
        for result in reranked_results:
            meta = result.get("meta") or {}
            log_type = str(meta.get("log_type", ""))
            text = " ".join([
                str(result.get("doc", "")),
                json.dumps(meta, ensure_ascii=False, default=str),
            ]).lower()
            if log_type in ["Data_Call_Failure", "Data_Call_Event", "DataCall_Failure", "DataCall_Event"] or any(k in text for k in [
                "setupdatacall", "no carrier", "user authentication failed", "e-pdn", "epdn"
            ]):
                datacall_candidates.append(result)

        if datacall_candidates and not any(
            (r.get("meta") or {}).get("log_type") in ["Data_Call_Failure", "Data_Call_Event", "DataCall_Failure", "DataCall_Event"]
            or any(k in str(r.get("doc", "")).lower() for k in [
                "setupdatacall", "no carrier", "user authentication failed", "e-pdn", "epdn"
            ])
            for r in final_top_results
        ):
            final_top_results = [datacall_candidates[0]] + final_top_results[:max(0, top_k - 1)]

    if is_nitz_query(query_lower):
        nitz_candidates = [
            r for r in reranked_results
            if (r.get("meta") or {}).get("log_type") in ["NITZ_Event", "Nitz_Time_Event"]
        ]
        if nitz_candidates and not any(
            (r.get("meta") or {}).get("log_type") in ["NITZ_Event", "Nitz_Time_Event"]
            for r in final_top_results
        ):
            final_top_results = [nitz_candidates[0]] + final_top_results[:max(0, top_k - 1)]

    if is_binder_query(query_lower):
        binder_candidates = [
            r for r in reranked_results
            if (r.get("meta") or {}).get("log_type") in ["Binder_Warning", "Binder_Context"]
        ]
        if binder_candidates and not any(
            (r.get("meta") or {}).get("log_type") in ["Binder_Warning", "Binder_Context"]
            for r in final_top_results
        ):
            final_top_results = [binder_candidates[0]] + final_top_results[:max(0, top_k - 1)]

    if is_binder_proxy_count_query(query_lower):
        priority_candidates = []
        supporting_wtf_candidates = []

        for result in reranked_results:
            meta = result.get("meta") or {}
            log_type = str(meta.get("log_type", ""))
            rca_type = str(meta.get("rca_type", ""))
            text = " ".join([
                str(result.get("doc", "")),
                json.dumps(meta, ensure_ascii=False, default=str),
            ]).lower()

            if (
                log_type == "RCA_Event" and rca_type == "BINDER_PROXY_LEAK_RCA"
            ) or any(k in text for k in [
                "binder_proxy_histogram", "binder proxy histogram", "biner_proxy_histogram",
                "binder_proxy_leak_rca", "leaked_descriptor", "max_count", "max count",
                "iintentreceiver", "iserviceconnection", "icontentprovider"
            ]):
                priority_candidates.append(result)
            elif log_type == "System_Kill_Wtf_Event" and "am_wtf" in text:
                supporting_wtf_candidates.append(result)

        forced_results = []
        if priority_candidates:
            forced_results.append(priority_candidates[0])
        if len(forced_results) < top_k and supporting_wtf_candidates:
            forced_results.append(supporting_wtf_candidates[0])

        if forced_results:
            final_top_results = _merge_top_results(forced_results, final_top_results, top_k)

    if is_negative_binder_leak_check_query(query_lower):
        positive_candidates = []
        non_wtf_candidates = []

        for result in reranked_results:
            meta = result.get("meta") or {}
            log_type = str(meta.get("log_type", ""))
            rca_type = str(meta.get("rca_type", ""))
            text = " ".join([
                str(result.get("doc", "")),
                json.dumps(meta, ensure_ascii=False, default=str),
            ]).lower()

            if (
                log_type == "RCA_Event" and rca_type == "BINDER_PROXY_LEAK_RCA"
            ) or any(k in text for k in [
                "binder_proxy_histogram", "binder proxy histogram", "biner_proxy_histogram",
                "binder_proxy_leak_rca", "too many binders sent to system",
                "leaked_descriptor", "max_proxy_count", "max_count", "iintentreceiver",
                "am_kill", "system_kill"
            ]):
                positive_candidates.append(result)
            elif log_type != "System_Kill_Wtf_Event":
                non_wtf_candidates.append(result)

        if positive_candidates:
            final_top_results = _merge_top_results(positive_candidates, final_top_results, top_k)
        elif non_wtf_candidates:
            final_top_results = non_wtf_candidates[:top_k]

    return final_top_results
