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

def apply_domain_boosts(hybrid_score: float, log_type: str, meta: dict, combined_text: str, query_lower: str) -> float:
    rca_type = str((meta or {}).get("rca_type", ""))
    crash_absence_check = is_crash_absence_check(query_lower)
    dns_policy_query = is_dns_policy_query(query_lower)
    datacall_failure_query = is_datacall_failure_query(query_lower)
    call_release_misclassification_query = is_call_release_misclassification_query(query_lower)
    time_context_inference_query = is_time_context_inference_query(query_lower)
    nitz_query = is_nitz_query(query_lower)
    binder_query = is_binder_query(query_lower)
    binder_proxy_count_query = is_binder_proxy_count_query(query_lower)
    negative_binder_leak_check_query = is_negative_binder_leak_check_query(query_lower)

    if call_release_misclassification_query:
        call_user_reject_text_hit = any(k in combined_text for k in [
            "normal_release", "code_user_decline", "code_user_terminated",
            "sip_480", "temporarily unavailable", "is_user_reject", "user_reject",
            "user decline", "수신 거부", "통화 거절", "사용자 종료", "정상 종료"
        ])

        if log_type == "Call_Session":
            hybrid_score += 1.00
        elif log_type in ["IMS_SIP_Message", "RILJ_Transaction"] and call_user_reject_text_hit:
            hybrid_score += 0.30
        elif log_type in ["Network_Timeline_Stat", "Network_DNS_Issue", "OOS_Event", "Signal_Level", "Data_Usage"]:
            hybrid_score -= 0.35

        if call_user_reject_text_hit:
            hybrid_score += 0.55

    if log_type == "RCA_Event":
        if not crash_absence_check and any(k in query_lower for k in [
            "root cause", "근본 원인", "원인", "왜", "죽", "강제 종료", "강제종료",
            "크래시", "crash", "am_kill", "system_kill", "system_wtf", "am_wtf",
            "wtf", "system kill", "바인더", "binder", "프록시", "proxy", "누수", "leak"
        ]):
            hybrid_score += 0.60

        if not crash_absence_check and rca_type == "BINDER_PROXY_LEAK_RCA" and any(k in query_lower for k in [
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
        if crash_absence_check:
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

    if time_context_inference_query:
        if log_type == "Call_Session":
            hybrid_score += 0.60
            if any(k in combined_text for k in ["code_user_terminated", "code_user_decline", "normal_release"]):
                hybrid_score += 0.35
        elif log_type == "Radio_Power_Event":
            hybrid_score += 0.60
        elif log_type == "OOS_Event":
            hybrid_score += 0.45
        elif log_type == "Device_Property_State":
            hybrid_score += 0.45
            if "airplane_mode" in combined_text:
                hybrid_score += 0.25
        elif log_type in ["RILJ_Transaction", "Network_Timeline_Stat", "Network_DNS_Issue"]:
            hybrid_score -= 0.20

    if datacall_failure_query:
        datacall_text_hit = any(k in combined_text for k in [
            "setupdatacall", "setup data call", "data_call", "datacall",
            "no carrier", "user authentication failed", "authentication failed",
            "not_specified", "not specified", "e-pdn", "epdn", "apn"
        ])

        if log_type in ["Data_Call_Failure", "Data_Call_Event", "DataCall_Failure", "DataCall_Event"]:
            hybrid_score += 0.85
        elif log_type == "RILJ_Transaction" and datacall_text_hit:
            hybrid_score += 0.45
        elif log_type in ["Network_DNS_Issue", "OOS_Event", "Signal_Level"]:
            hybrid_score -= 0.15

        if datacall_text_hit:
            hybrid_score += 0.35

    if nitz_query:
        if log_type in ["NITZ_Event", "Nitz_Time_Event"]:
            hybrid_score += 0.80
        elif log_type in ["RILJ_Transaction", "Network_DNS_Issue", "Data_Usage"]:
            hybrid_score -= 0.10


    if binder_query:
        if log_type == "Binder_Warning":
            hybrid_score += 0.55
        elif log_type == "Binder_Context":
            hybrid_score += 0.35
        elif log_type == "RCA_Event" and rca_type == "BINDER_PROXY_LEAK_RCA":
            hybrid_score += 0.45


    if binder_proxy_count_query:
        histogram_or_rca_hit = any(k in combined_text for k in [
            "binder_proxy_histogram", "binder proxy histogram", "biner_proxy_histogram",
            "binder_proxy_leak_rca", "leaked_descriptor", "max_count", "max count",
            "iintentreceiver", "iserviceconnection", "icontentprovider"
        ])
        raw_wtf_hit = any(k in combined_text for k in [
            "am_wtf", "system_wtf", "what a terrible failure"
        ])

        if log_type == "RCA_Event" and rca_type == "BINDER_PROXY_LEAK_RCA":
            hybrid_score += 1.20
        elif histogram_or_rca_hit:
            hybrid_score += 1.00
        elif log_type in ["Binder_Warning", "Binder_Context"]:
            hybrid_score += 0.65
        elif log_type == "System_Kill_Wtf_Event" and raw_wtf_hit:
            hybrid_score += 0.10

        if log_type == "System_Kill_Wtf_Event" and not histogram_or_rca_hit:
            hybrid_score -= 0.25

    if negative_binder_leak_check_query:
        positive_leak_evidence_hit = any(k in combined_text for k in [
            "binder_proxy_histogram", "binder proxy histogram", "biner_proxy_histogram",
            "binder_proxy_leak_rca", "too many binders sent to system",
            "leaked_descriptor", "max_proxy_count", "max_count", "iintentreceiver"
        ])
        am_kill_hit = "am_kill" in combined_text or "system_kill" in combined_text
        raw_wtf_only_hit = any(k in combined_text for k in ["am_wtf", "system_wtf"]) and not positive_leak_evidence_hit

        if log_type == "RCA_Event" and rca_type == "BINDER_PROXY_LEAK_RCA":
            hybrid_score += 1.30
        elif positive_leak_evidence_hit:
            hybrid_score += 1.00
        elif am_kill_hit:
            hybrid_score += 0.40

        # Negative leak checks must not let raw am_wtf rows dominate.
        # am_wtf alone is not evidence of Binder proxy leak or Too many Binders kill.
        if log_type == "System_Kill_Wtf_Event" and raw_wtf_only_hit:
            hybrid_score -= 0.90

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

        if dns_policy_query:
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
            if dns_policy_query:
                hybrid_score -= 0.25

    return hybrid_score