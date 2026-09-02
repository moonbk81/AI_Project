"""긴급호 시도 파서.

로그는 T-Mobile 망에서 정상 서비스가 없는(EMERGENCY_ONLY) 단말이 911 을 걸어
VoLTE 로 라우팅되고, 긴급 PDN(sos) 이 8초 뒤 실패하며 모뎀이 리셋되는 실제
덤프에서 줄여 온 것이다.
"""

from parsers.emergency_call_parser import EmergencyCallParser

FAILED_911 = [
    "09-01 07:51:48.943  3639  3639 D ImsPhoneCallTracker: [0] CallRoute - Emergency Search: Route to VoLTE. intentExtras: Bundle[{imsEmergencyRat=VoLTE}]",
    "09-01 07:51:48.947  3639  3639 D IPF     : [IPCT]- setEmergencyCallInfo {911/0(oD)}",
    "09-01 07:51:48.948  3639  3639 D ImsPhoneCallTracker: [0] CallRoute - Emergency call rat: VoLTE",
    "09-01 07:51:49.025  3639  3639 D IPF     : [IPCT]< makeCall {[ImsCall objId:149617640 onHold:N mute:N mCallProfile:{ serviceType=2 }]}",
    "09-01 07:51:49.029  3639  3639 D SEM_RILJ: [0271]> EMERGENCY_CONTROL command: DIALED(0) [PHONE0][GCCT0]",
    "09-01 07:51:49.030  2898  3698 E RILD    : EmergencyControl - state: RAT_READY(3), command: DIALED",
    "09-01 07:51:49.030  3639  3639 D SemCallTrackerHelper: updateIntentExtras - new: ImsDialArgs(videoState: 0, clirMode: 0, isEmergency: true, eccCategory: 0, intentExtras: Bundle[{latestDomain=PS, imsEmergencyRat=VoLTE}])",
    "09-01 07:51:49.074  2898  3698 D RILD    : DoEvent - E911 progress: DIALED_WITH_POSSIBLE_RETRY(5), isE911inCallList: 0, RAT: 20",
    "09-01 07:51:49.074  2898  3698 D RILD    : DoEvent - E911 progress: DIALED_WITH_POSSIBLE_RETRY(5), isE911inCallList: 0, RAT: 20",
    "09-01 07:51:49.119  3639  3639 D SST-0   : updateCarrierDisplayName: isWifiCallingEnabled=false combinedRegState=OUT_OF_SERVICE {mVoiceRegState=1(OUT_OF_SERVICE), mIsEmergencyOnly=true, availableServices=[EMERGENCY]}",
    "09-01 07:51:49.119  2898  3698 D RILD    : OnVendorConfigurationChanged - VendorConfig[4] - N: VOLTE_911_CALL, V: 0",
    "09-01 07:51:49.380  3639  4670 D RILJ    : [0278]> SETUP_DATA_CALL,reason=NORMAL,dataProfile=[DataProfile=[ApnSetting] DEFAULT EIMS, sos, emergency],trafficDescriptor=TrafficDescriptor={mDnn=sos} [PHONE0]",
    "09-01 07:51:57.189  2898  4082 E RILD    : All Service is closed, Modem Reset!!",
    "09-01 07:51:57.195  3639  4965 D RILJ    : [0278]< SETUP_DATA_CALL DataCallResponse: { cause=NOT_SPECIFIED(0xfffffff9) retry=-1 cid=2 linkStatus=0 } [PHONE0]",
]


def test_a_failed_emergency_call_is_read_as_one_attempt():
    attempts = EmergencyCallParser().analyze(FAILED_911)

    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["time"] == "09-01 07:51:48.943"
    assert attempt["slot"] == "0"
    assert attempt["number"] == "911"
    assert attempt["ecc_category"] == "0"
    # 어느 도메인/RAT 으로 걸었는지 -- 일반 통화 이력에는 남지 않는 것들.
    assert attempt["route"] == "VoLTE"
    assert attempt["rat"] == "VoLTE"
    assert attempt["domain"] == "PS"
    # 통화 이력의 세션과 이어 붙일 키.
    assert attempt["ims_obj_id"] == "149617640"


def test_the_state_machines_are_kept_in_order_without_repeats():
    attempt = EmergencyCallParser().analyze(FAILED_911)[0]

    assert attempt["emergency_control"] == ["RAT_READY(3)/DIALED"]
    # 같은 값이 계속 다시 찍히지만 바뀔 때만 남는다.
    assert attempt["e911_progress"] == ["DIALED_WITH_POSSIBLE_RETRY(5)"]


def test_the_service_state_at_dial_time_is_carried():
    attempt = EmergencyCallParser().analyze(FAILED_911)[0]

    assert attempt["service_state"] == "OUT_OF_SERVICE"
    assert attempt["emergency_only"] == "true"
    assert attempt["volte_911_config"] == "0"


def test_the_emergency_pdn_answer_is_matched_by_its_rilj_sequence():
    """응답 줄에는 APN 이 없고, 8초 뒤 다른 로그 수백 줄 아래에 온다."""
    attempt = EmergencyCallParser().analyze(FAILED_911)[0]

    assert attempt["emergency_pdn"]["apn"] == "sos"
    assert attempt["emergency_pdn"]["requested_at"] == "09-01 07:51:49.380"
    assert attempt["emergency_pdn"]["cause"] == "NOT_SPECIFIED(0xfffffff9)"
    assert attempt["emergency_pdn"]["status"] == "FAIL"


def test_the_failure_names_every_thing_that_went_wrong():
    attempt = EmergencyCallParser().analyze(FAILED_911)[0]

    assert attempt["status"] == "FAIL"
    assert "긴급 PDN(sos) 설정 실패" in attempt["fail_reason"]
    assert "모뎀 리셋" in attempt["fail_reason"]
    assert "EMERGENCY_PDN_SETUP_FAILED" in attempt["root_cause_candidate"]
    assert "MODEM_RESET" in attempt["root_cause_candidate"]
    # 정상 서비스가 아니었다는 사실도 원인 후보에 함께 남는다.
    assert "NO_NORMAL_SERVICE_EMERGENCY_ONLY" in attempt["root_cause_candidate"]


def test_a_pdn_request_without_an_answer_is_not_called_a_success():
    attempts = EmergencyCallParser().analyze(FAILED_911[:-2])

    assert attempts[0]["emergency_pdn"]["status"] == "PENDING"
    assert "응답 없음" in attempts[0]["fail_reason"]


def test_a_retry_seconds_later_stays_one_attempt():
    """IMS 로 걸어 보고 CS 로 다시 거는 것은 사용자에게 한 번의 긴급호다."""
    lines = FAILED_911 + [
        "09-01 07:51:58.100  3639  3639 D ImsPhoneCallTracker: [0] CallRoute - Emergency Search: Route to CS. intentExtras: Bundle[{}]",
        "09-01 07:51:58.200  3639  3639 D IPF     : [IPCT]- setEmergencyCallInfo {911/0(oD)}",
    ]

    attempts = EmergencyCallParser().analyze(lines)

    assert len(attempts) == 1
    assert attempts[0]["route"] == "CS"


def test_a_separate_call_minutes_later_is_its_own_attempt():
    lines = FAILED_911 + [
        "09-01 07:55:10.100  3639  3639 D ImsPhoneCallTracker: [0] CallRoute - Emergency Search: Route to VoLTE. intentExtras: Bundle[{}]",
        "09-01 07:55:10.200  3639  3639 D IPF     : [IPCT]- setEmergencyCallInfo {112/0(oD)}",
        "09-01 07:55:11.000  3639  3639 D IPF     : [IPCT]< makeCall {[ImsCall objId:200000001]}",
        "09-01 07:55:12.000  3639  3639 D SemCallTrackerHelper: [0] setImsCallList - {Total: 1, [state:ACTIVE, type:volte_emergency, mo, norm, 112-Y, objId:1 (200000001)]}",
    ]

    attempts = EmergencyCallParser().analyze(lines)

    assert len(attempts) == 2
    assert attempts[1]["number"] == "112"
    assert attempts[1]["status"] == "SUCCESS"
    assert attempts[1]["fail_reason"] == ""


def test_a_log_without_emergency_calls_yields_nothing():
    lines = [
        "09-01 07:51:48.943  3639  3639 D ImsPhoneCallTracker: [0] dial - initialCallNetworkType",
        "09-01 07:51:49.380  3639  4670 D RILJ    : [0278]> SETUP_DATA_CALL,reason=NORMAL,dnn=fast.t-mobile.com [PHONE0]",
    ]

    assert EmergencyCallParser().analyze(lines) == []


def test_a_modem_reset_long_after_the_call_is_not_its_failure():
    """끝난 긴급호에 몇 분 뒤의 사고를 붙이면 원인을 잘못 짚는다."""
    lines = FAILED_911[:-2] + [
        "09-01 07:51:57.195  3639  4965 D RILJ    : [0278]< SETUP_DATA_CALL DataCallResponse: { cause=NONE(0x0) retry=-1 cid=2 } [PHONE0]",
        "09-01 07:51:58.000  3639  3639 D SemCallTrackerHelper: [0] setImsCallList - {Total: 1, [state:ACTIVE, type:volte_emergency, mo, norm, 911-Y, objId:1 (149617640)]}",
        "09-01 08:10:00.000  2898  4082 E RILD    : All Service is closed, Modem Reset!!",
    ]

    attempt = EmergencyCallParser().analyze(lines)[0]

    assert attempt["modem_reset"] is False
    assert attempt["emergency_pdn"]["status"] == "OK"
    assert attempt["status"] == "SUCCESS"


def test_the_call_history_is_marked_by_the_ims_session_key():
    """통화 목록만 보면 긴급호가 일반 MO 발신과 구분되지 않는다."""
    from log_orchestrator import LogOrchestrator

    attempts = EmergencyCallParser().analyze(FAILED_911)
    sessions = [
        {"id": "TC@1_1 (objId:149617640)", "status": "FAIL"},
        {"id": "TC@1_2 (objId:999)", "status": "SUCCESS"},
    ]

    LogOrchestrator._mark_emergency_calls(sessions, attempts)

    assert sessions[0]["is_emergency"] is True
    assert sessions[0]["emergency_number"] == "911"
    # 다른 통화에는 아무것도 붙지 않는다.
    assert "is_emergency" not in sessions[1]


def test_marking_a_call_history_without_emergency_calls_changes_nothing():
    from log_orchestrator import LogOrchestrator

    sessions = [{"id": "TC@1_1 (objId:1)", "status": "FAIL"}]

    LogOrchestrator._mark_emergency_calls(sessions, [])
    LogOrchestrator._mark_emergency_calls(sessions, None)

    assert sessions == [{"id": "TC@1_1 (objId:1)", "status": "FAIL"}]
