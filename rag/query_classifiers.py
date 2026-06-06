

"""Query classifier helpers for retrieval and routing.

These functions intentionally use lightweight keyword heuristics.
They should stay domain-generic and must not depend on Golden TC IDs or fixed test timestamps.
"""


def is_crash_absence_check(query_lower: str) -> bool:
    has_crash_scope = any(k in query_lower for k in [
        "crash", "크래시", "native crash", "네이티브 크래시", "fatal exception",
        "java crash", "java exception", "native_crash_event",
        "crash_event", "anr", "anr_context", "응답 없음", "앱 응답 없음"
    ])

    has_absence_intent = any(k in query_lower for k in [
        "없", "없으면", "없는", "없었", "없다",
        "동반", "동반되", "외에", "제외", "말고",
        "확인", "존재", "있는지", "있는지만"
    ])

    has_explicit_system_kill_query = any(k in query_lower for k in [
        "am_kill", "system_kill", "am_wtf", "system_wtf",
        "too many binders", "binder leak", "proxy leak"
    ])

    return has_crash_scope and has_absence_intent and not has_explicit_system_kill_query


def is_dns_policy_query(query_lower: str) -> bool:
    has_dns_scope = any(k in query_lower for k in [
        "dns", "도메인", "lookup", "resolve", "resolver"
    ])
    has_policy_scope = any(k in query_lower for k in [
        "정책", "policy", "차단", "blocked", "is_blocked", "effective_policy",
        "battery_saver", "reject", "app_standby", "background", "백그라운드", "절전"
    ])
    return has_dns_scope and has_policy_scope


def is_datacall_failure_query(query_lower: str) -> bool:
    has_datacall_scope = any(k in query_lower for k in [
        "setupdatacall", "setup data call", "data call", "datacall",
        "e-pdn", "epdn", "apn", "pdp", "데이터 연결", "데이터콜", "데이터 콜"
    ])
    has_failure_scope = any(k in query_lower for k in [
        "fail", "failed", "failure", "reject", "rejected", "거절", "실패",
        "no carrier", "authentication", "user authentication", "auth", "인증",
        "not_specified", "not specified", "원인", "사유"
    ])
    return has_datacall_scope or ("no carrier" in query_lower and has_failure_scope)


def is_call_release_misclassification_query(query_lower: str) -> bool:
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
        "단정", "오판"
    ])
    return has_call_scope and has_misclassification_check and has_release_or_reject_evidence

def is_call_drop_check_query(query_lower: str) -> bool:
    has_call_scope = any(k in query_lower for k in [
        "call_session", "call session", "volte", "ims call", "ps call",
        "통화", "호", "콜", "음성", "call", "voice call", "cs call", "cs 통화"
    ])
    has_drop_scope = any(k in query_lower for k in [
        "call drop", "콜드랍", "drop", "dropped", "끊김", "끊겼", "통화 끊김",
        "통화종료", "통화 종료", "normal release", "normal_release", "release cause",
        "sip", "거절 사유", "종료 사유", "drop 기록", "drop 이력"
    ])
    has_fact_only_scope = any(k in query_lower for k in [
        "지어내지", "팩트", "존재하는 사실", "간결", "확인", "요약", "출력"
    ])
    return has_call_scope and has_drop_scope and (
        has_fact_only_scope or "call drop" in query_lower or "콜드랍" in query_lower
    )

def is_time_context_inference_query(query_lower: str) -> bool:
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

def is_nitz_query(query_lower: str) -> bool:
    return any(k in query_lower for k in [
        "nitz", "network identity and time zone", "time zone", "timezone",
        "타임존", "시간대", "시간 변경", "시간 보정", "시각 보정", "핑퐁"
    ])

def is_binder_query(query_lower: str) -> bool:
    return any(k in query_lower for k in [
        "binder", "바인더", "proxy", "프록시", "누수", "leak",
        "too many binders", "binder proxy", "iintentreceiver", "iserviceconnection",
        "icontentprovider", "system_server", "system server"
    ])


def is_binder_proxy_count_query(query_lower: str) -> bool:
    has_binder_or_wtf_scope = any(k in query_lower for k in [
        "binder", "바인더", "proxy", "프록시", "binder proxy", "binder_proxy",
        "binder proxy histogram", "binder_proxy_histogram", "histogram", "히스토그램",
        "iintentreceiver", "iserviceconnection", "icontentprovider",
        "프록시 객체", "객체 누수", "누수", "leak",
        "am_wtf", "system_wtf", "wtf", "이상 징후"
    ])
    has_count_scope = any(k in query_lower for k in [
        "max_count", "max count", "최대", "개수", "몇 회", "몇회", "몇 개", "몇개",
        "대량", "count", "누수 최대", "발생 횟수", "총", "회나"
    ])
    return has_binder_or_wtf_scope and has_count_scope


def is_negative_binder_leak_check_query(query_lower: str) -> bool:
    has_binder_leak_scope = any(k in query_lower for k in [
        "binder proxy leak", "binder_proxy_leak", "binder proxy", "binder_proxy",
        "바인더 프록시", "프록시 누수", "proxy leak", "too many binders",
        "too many binders sent to system", "biner_proxy_histogram",
        "binder_proxy_histogram", "binder proxy histogram", "binder_proxy_leak_rca",
        "am_kill", "강제 종료", "강제종료"
    ])
    has_absence_scope = any(k in query_lower for k in [
        "없으면", "없다면", "없다고", "없음", "없는지", "있나요", "확인", "흔적",
        "확인되지", "근거 없음", "없다고 명확히", "없다고 답"
    ])
    return has_binder_leak_scope and has_absence_scope