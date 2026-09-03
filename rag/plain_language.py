"""Turn this repo's own vocabulary into words the reader actually knows.

config.yaml names its routing intents, its tool functions and its document
categories, and the prompt hands those names to the model as instructions. The
model repeats them back -- "`Binder_Warning`에서 확인됩니다",
"get_crash_anr_analytics 결과" -- and the answer lands in a PLM comment where
nobody outside this repo can read it.

Only our own names are translated. Anything the device wrote is the reader's
evidence and stays verbatim: am_kill, THREAD_EXHAUSTION, SIP 403,
`Too many Binders sent to SYSTEM`, command names, error codes, timestamps.

`tests/test_plain_language.py` fails when config.yaml grows a name this file
has no wording for, so the two cannot drift apart silently.
"""

import re

# 검색 문서 분류 이름 -> 사람이 읽는 말
LOG_TYPE_WORDS = {
    "ANR_Context": "앱 응답 없음(ANR) 기록",
    "App_Network_Block_Window": "앱 프리즈로 인한 네트워크 차단 구간",
    "Battery_Drain_Report": "배터리 소모 리포트",
    "Binder_Context": "바인더 주변 상황 기록",
    "Binder_Warning": "바인더 경고",
    "Binder_Warning_Critical": "바인더 핵심 경고",
    "Call_Drop_Rule": "통화 끊김 판정 기준",
    "Call_Session": "통화 세션 기록",
    "Crash_Event": "앱 크래시 기록",
    "DNS_Health_Warning": "DNS 응답 지연 경고",
    "DNS_Query": "DNS 조회 기록",
    "DataCall_Event": "데이터 연결 이벤트",
    "Data_Stall_Recovery": "데이터 끊김 복구 기록",
    "Data_Usage": "데이터 사용량 기록",
    "Device_Property_State": "단말 속성 상태",
    "IMS_SIP_Message": "IMS SIP 메시지",
    "Internet_Stall_Analysis": "인터넷 끊김 분석",
    "NTN_Policy": "위성(NTN) 정책",
    "Native_Crash_Event": "네이티브 크래시 기록",
    "Network_DNS_Issue": "DNS 문제 기록",
    "Network_Timeline_Stat": "구간별 네트워크 품질 통계",
    "Network_Timeline_Summary": "네트워크 품질 요약",
    "Nitz_Time_Event": "망 시각 동기(NITZ) 기록",
    "OOS_Event": "권외(서비스 불가) 기록",
    "RCA_Event": "원인 분석 결과",
    "RILJ_Transaction": "모뎀 명령(RIL) 기록",
    "Radio_Power_Event": "무선 전원 이벤트",
    "SetupDataCall_Failed": "데이터 연결 설정 실패 기록",
    "Signal_Level": "신호 세기 기록",
    "System_Kill_Wtf_Event": "시스템 강제 종료·이상 징후 기록",
}

# 라우팅 intent 이름 -> 그 intent 가 하는 일
INTENT_WORDS = {
    "Battery_Thermal": "배터리·발열 분석",
    "Call_Analysis": "통화 분석",
    "Call_Drop_Trap": "통화 끊김 확인",
    "Crash_ANR": "크래시·ANR 분석",
    "DNS_Latency": "DNS 지연 분석",
    "Data_Call_Setup": "데이터 연결 설정 분석",
    "Data_Usage_Analysis": "데이터 사용량 분석",
    "Fallback_General": "일반 분석",
    "Internet_Stall": "인터넷 끊김 분석",
    "NTN_SpaceX": "위성 통신 분석",
    "Network_OOS": "권외 분석",
    "Nitz_Time_Analysis": "망 시각 동기 분석",
    "Radio_Power": "무선 전원 분석",
    "System_Kill_WTF": "시스템 강제 종료 분석",
    "Time_Context_Inference": "발생 시점 추론",
}

# 도구 함수 이름 -> 그 도구가 뽑아주는 것
TOOL_WORDS = {
    "get_battery_thermal_analytics": "배터리·발열 분석",
    "get_binder_warning_analytics": "바인더 경고 분석",
    "get_crash_anr_analytics": "크래시·ANR 분석",
    "get_cs_call_analytics": "음성 통화 분석",
    "get_data_stall_and_recovery_analytics": "데이터 끊김·복구 분석",
    "get_datacall_setup_analytics": "데이터 연결 설정 분석",
    "get_dns_latency_analytics": "DNS 지연 분석",
    "get_internet_stall_analytics": "인터넷 끊김 분석",
    "get_network_oos_analytics": "권외 분석",
    "get_ntn_spacex_analytics": "위성 통신 분석",
    "get_ps_ims_call_analytics": "VoLTE/IMS 통화 분석",
    "get_radio_power_analytics": "무선 전원 분석",
    "get_recent_data_usage_analytics": "최근 데이터 사용량 분석",
}

# 우리가 만든 RCA 문서 이름과 이벤트 분류 라벨. 로그에 찍힌 문자열이 아니라 이
# 저장소가 붙인 이름이므로 번역 대상이다. 반대로 이것들이 가리키는 로그 원문
# (am_kill, am_wtf, Too many Binders sent to SYSTEM) 은 근거이므로 손대지 않는다.
RCA_TYPE_WORDS = {
    "BINDER_PROXY_HISTOGRAM": "바인더 프록시 보유량 통계",
    "BINDER_PROXY_LEAK_RCA": "바인더 프록시 누수 원인 분석",
    "BINDER_PROXY_LEAK_SUMMARY": "바인더 프록시 누수 요약",
    "BINDER_PROXY_LEAK": "바인더 프록시 누수",
    "SYSTEM_KILL": "시스템 강제 종료",
    "SYSTEM_WTF_SUMMARY": "시스템 이상 징후 요약",
    "SYSTEM_WTF": "시스템 이상 징후",
}

DISPLAY_NAMES = {**LOG_TYPE_WORDS, **INTENT_WORDS, **TOOL_WORDS, **RCA_TYPE_WORDS}

# 긴 이름부터 봐야 Radio_Power 가 Radio_Power_Event 를 먼저 삼키지 않는다.
_ALTERNATION = "|".join(
    re.escape(name) for name in sorted(DISPLAY_NAMES, key=len, reverse=True)
)
# 백틱/따옴표로 감싼 경우는 감싼 것까지 걷어낸다. 한글 조사가 바로 뒤에 붙으므로
# \b 는 쓸 수 없다(한글도 단어 문자라 경계가 서지 않는다).
_WRAPPED = re.compile(r"[`'\"]({0})[`'\"]".format(_ALTERNATION))
_BARE = re.compile(r"(?<![A-Za-z0-9_])({0})(?![A-Za-z0-9_])".format(_ALTERNATION))


def display_name(name):
    """이름 하나를 사람이 읽는 말로. 모르는 이름은 그대로 돌려준다.

    `humanize` 는 문장 안을 훑지만, 이건 라벨 자리에 이미 이름 하나만 있을 때
    쓴다 -- 근거 자료의 제목 같은 곳.
    """
    return DISPLAY_NAMES.get(name, name)


def humanize(text):
    """답변에서 내부 이름을 사람이 읽는 말로 바꾼다."""
    if not text:
        return text
    replace = lambda match: DISPLAY_NAMES[match.group(1)]
    return _BARE.sub(replace, _WRAPPED.sub(replace, text))
