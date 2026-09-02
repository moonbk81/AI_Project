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


# ------------------------------------------------------- IMS → CS 폴백 긴급호

CS_FALLBACK = [
    "08-13 16:37:27.175  6288  6288 D SEM_RILJ: [1277]> EMERGENCY_SEARCH [PHONE1][GCCT1]",
    "08-13 16:37:27.177  6288  6288 D SemCallTrackerHelper: [1] setImsCallList - {Total: 1, [state:DIALING, type:vounknown(0)_emergency, mo, norm, <MASKED>, objId:110917786 (0)]}",
    "08-13 16:37:27.182  4896  4920 E RILD2   : SelectE911RatDeterminer - Type: QMI_SRCH (Reason: PS domain)",
    "08-13 16:37:27.190  4896  4920 E RILD2   : EmergencySearch_FWREQ - Complete search request (result: CS)",
    "08-13 16:37:27.191  6288  7036 D SEM_RILJ: [1277]< EMERGENCY_SEARCH {CS(1)} [PHONE1][GCCT1]",
    "08-13 16:37:27.212  6288  6288 D ImsPhoneCallTracker: [1] EVENT_EMERGENCY_SEARCH_RESULT - search result: CS(1)",
    "08-13 16:37:27.213  6288  6288 D ImsPhoneCallTracker: [1] CallRoute - Emergency Search: Route to CS call",
    "08-13 16:37:27.213  6288  6288 D ImsPhoneCallTracker: [1] CallRoute - redialToCs - Redial [telecomCallID: TC@7_1 objId: 110917786 incoming: false state: DIALING isEmergencyCall: true (source: 10001000)] to CS. reasonInfo: ImsReasonInfo :: {0 : CODE_UNSPECIFIED, 0, null}",
    "08-13 16:37:27.216  6288  6288 D GsmCdmaConnection: [GsmCdmaConn] New Connection (DialString):  callId: null objId: 195197401 isExternal: N incoming: false state: DIALING isEmergencyCall: true (source: 10001000)",
    "08-13 16:37:27.220  6288  6288 D RILJ    : [1280]> EMERGENCY_DIAL [PHONE1][GCCT1]",
    "08-13 16:37:27.225  6288  7024 D EmergencyNumberTracker: [0]Found in mEmergencyNumberList",
    "08-13 16:37:33.826  6288  6288 D SemEmergencyNumberTracker: [1] cacheVendorEmergencyDatabase - withSim: true, Network: null, SIM: 310280, ecclistFromDatabase: 112,911",
    "08-13 16:37:27.272  6288  6927 D SEM_RILJ: [UNSL]< UNSOL_EXTENDED_REGISTRATION_STATE SehExtendedRegStateResult{isValid: true, imsEmergencyCallBarring: 2, unprocessedVoiceRegState: REG_ROAMING, isPsOnlyReg: false} [PHONE1]",
    "08-13 16:37:27.279  6288  6288 D SST-1   : updateCarrierDisplayName: isWifiCallingEnabled=false combinedRegState=IN_SERVICE {mVoiceRegState=0(IN_SERVICE), mIsEmergencyOnly=false}",
    "08-13 16:37:41.207  6288  7024 D RILJ    : [1343]< GET_CURRENT_CALLS {[id=1,ACTIVE,toa=129,norm,mo,0,voc,noevp,,cli=1,,1,audioQuality=1] } [PHONE1][GCCT1]",
    "08-13 16:38:10.474  6288  6288 D GsmCdmaConnection: onDisconnect: cause=36",
    "08-13 16:38:10.474  6288  6934 D RILJ    : [1366]< LAST_CALL_FAIL_CAUSE com.android.internal.telephony.LastCallFailCause@d9e4f50 causeCode: 255 vendorCause: 22 [PHONE1][GCCT1]",
]


def test_the_attempt_starts_at_the_emergency_search_not_at_its_result():
    """`> EMERGENCY_SEARCH` 가 발신의 첫 줄이다. 이걸 놓치면 시작 시각이 뒤로 밀린다."""
    attempt = EmergencyCallParser().analyze(CS_FALLBACK)[0]

    assert attempt["time"] == "08-13 16:37:27.175"
    # RILJ 일련번호(`[1277]>`)를 슬롯으로 읽으면 안 된다.
    assert attempt["slot"] == "1"


def test_the_modem_domain_choice_and_the_cs_redial_are_kept():
    attempt = EmergencyCallParser().analyze(CS_FALLBACK)[0]

    assert attempt["search_result"] == "CS"
    assert attempt["rat_determiner"] == "QMI_SRCH (PS domain)"
    assert attempt["redials"] == ["CS"]
    assert attempt["fallback"] == "IMS → CS"
    assert attempt["cs_dialed_at"] == "08-13 16:37:27.220"
    assert attempt["ims_emergency_barring"] == "2"


def test_a_redial_reason_of_code_zero_is_not_an_ims_failure():
    """`redialToCs` 의 `ImsReasonInfo {0 : CODE_UNSPECIFIED}` 는 사유 없음이다.
    이것을 실패로 읽으면 CS 로 붙은 긴급호가 IMS 실패로 보고된다.
    """
    attempt = EmergencyCallParser().analyze(CS_FALLBACK)[0]

    assert attempt["ims_fail_reason"] == ""
    assert "IMS 실패" not in attempt["fail_reason"]


def test_a_call_that_connected_then_dropped_is_a_drop_with_its_cause():
    attempt = EmergencyCallParser().analyze(CS_FALLBACK)[0]

    assert attempt["status"] == "CALL DROP"
    assert attempt["end_cause"] == "255"
    assert attempt["end_vendor_cause"] == "22"
    # 숫자만 적어 두면 읽는 사람이 또 찾아야 한다.
    assert "FADE" in attempt["end_cause_text"]
    assert "통화 종료" in attempt["fail_reason"]
    assert "CALL_END_255" in attempt["root_cause_candidate"]
    assert "VENDOR_22" in attempt["root_cause_candidate"]


def test_a_cs_emergency_call_is_marked_in_the_history_by_its_telecom_id():
    """IMS 에서 CS 로 넘어간 긴급호는 CS 세션 하나로만 남는다."""
    from log_orchestrator import LogOrchestrator

    attempts = EmergencyCallParser().analyze(CS_FALLBACK)
    sessions = [{"id": "TC@7_1", "status": "CALL DROP"}, {"id": "TC@9_1", "status": "SUCCESS"}]

    LogOrchestrator._mark_emergency_calls(sessions, attempts)

    assert sessions[0]["is_emergency"] is True
    assert "is_emergency" not in sessions[1]


def test_a_normal_release_after_connecting_is_not_a_drop():
    lines = CS_FALLBACK[:-1] + [
        "08-13 16:38:10.474  6288  6934 D RILJ    : [1366]< LAST_CALL_FAIL_CAUSE causeCode: 16 [PHONE1][GCCT1]",
    ]

    attempt = EmergencyCallParser().analyze(lines)[0]

    assert attempt["status"] == "SUCCESS"
    assert attempt["fail_reason"] == ""


def _search(result, *extra):
    """Search 결과만 바꾼 최소 시도. 모뎀은 CS 말고 VoLTE·VoWiFi 도 내려 준다."""
    return [
        "08-13 16:37:27.175  6288  6288 D SEM_RILJ: [1277]> EMERGENCY_SEARCH [PHONE1][GCCT1]",
        f"08-13 16:37:27.191  6288  7036 D SEM_RILJ: [1277]< EMERGENCY_SEARCH {{{result}}} [PHONE1][GCCT1]",
        *extra,
    ]


def test_the_search_answer_is_kept_whatever_domain_the_modem_picks():
    for result in ("CS(1)", "VoLTE(0)", "VoWifi(2)"):
        attempt = EmergencyCallParser().analyze(_search(result))[0]
        assert attempt["search_result"] == result


def test_a_search_result_that_carries_parentheses_is_not_cut_short():
    """`(result: CS(1))` 를 `)` 에서 끊으면 `CS(1` 이 남는다."""
    lines = _search(
        "CS(1)",
        "08-13 16:37:27.190  4896  4920 E RILD2   : EmergencySearch_FWREQ - Complete search request (result: CS(1))",
    )
    # 먼저 본 값을 남기므로 Search 응답이 앞이면 그것이 유지된다.
    assert EmergencyCallParser().analyze(lines)[0]["search_result"] == "CS(1)"

    only_complete = [
        "08-13 16:37:27.175  6288  6288 D SEM_RILJ: [1277]> EMERGENCY_SEARCH [PHONE1][GCCT1]",
        "08-13 16:37:27.190  4896  4920 E RILD2   : EmergencySearch_FWREQ - Complete search request (result: CS(1))",
    ]
    assert EmergencyCallParser().analyze(only_complete)[0]["search_result"] == "CS(1)"


def test_a_redial_to_a_domain_other_than_cs_is_read_too():
    """대상은 함수 이름에서 읽는다. CS 만 알아보면 다른 폴백이 통째로 빠진다."""
    lines = _search(
        "VoLTE(0)",
        "08-13 16:37:27.213  6288  6288 D ImsPhoneCallTracker: [1] CallRoute - redialToIms - Redial [telecomCallID: TC@7_1 isEmergencyCall: true] to IMS. reasonInfo: ImsReasonInfo :: {0 : CODE_UNSPECIFIED, 0, null}",
    )

    attempt = EmergencyCallParser().analyze(lines)[0]

    assert attempt["redials"] == ["IMS"]
    # 어디서 옮겨 왔는지는 로그로 확인된 바가 없어 출발 도메인을 짐작하지 않는다.
    assert attempt["fallback"] == "IMS"


def test_the_fallback_chain_starts_at_the_domain_the_log_names():
    lines = _search(
        "CS(1)",
        "08-13 16:37:27.213  6288  6288 D SemCallTrackerHelper: updateIntentExtras - new: ImsDialArgs(isEmergency: true, eccCategory: 0, intentExtras: Bundle[{latestDomain=PS}])",
        "08-13 16:37:27.214  6288  6288 D ImsPhoneCallTracker: [1] CallRoute - redialToCs - Redial [isEmergencyCall: true] to CS. reasonInfo: ImsReasonInfo :: {0 : CODE_UNSPECIFIED, 0, null}",
    )

    assert EmergencyCallParser().analyze(lines)[0]["fallback"] == "PS → CS"


def test_the_flow_is_read_end_to_end_from_the_ecc_check_to_the_domain_taken():
    """흐름: 번호가 긴급번호인지 확인 -> Search -> 내려온 값대로 CS/PS 진행."""
    attempt = EmergencyCallParser().analyze(CS_FALLBACK)[0]

    assert attempt["ecc_list_matched"] is True
    assert attempt["ecc_list"] == "112,911"
    assert attempt["search_result"] == "CS"
    # IMS 로 먼저 걸렸다가 CS 로 넘어갔다.
    assert attempt["ims_dialed_at"] == "08-13 16:37:27.177"
    assert attempt["cs_dialed_at"] == "08-13 16:37:27.220"
    assert attempt["dialed_domain"] == "PS → CS"


def test_a_ps_only_emergency_call_says_ps():
    attempt = EmergencyCallParser().analyze(
        _search(
            "VoLTE(0)",
            "08-13 16:37:27.220  6288  6288 D IPF     : [IPCT]> makeCall {911, { serviceType=2, callType=2 }}",
        )
    )[0]

    assert attempt["dialed_domain"] == "PS"
    assert attempt["cs_dialed_at"] == ""


def test_the_ecc_check_repeats_do_not_stretch_the_attempt():
    """긴급번호 확인은 통화 내내 다시 찍힌다. 그 반복이 시도의 끝 시각이 되면 안 된다."""
    lines = _search("CS(1)") + [
        "08-13 16:37:28.000  6288  6288 D EmergencyNumberTracker: [0]Found in mEmergencyNumberList",
        "08-13 16:37:50.000  6288  6288 D EmergencyNumberTracker: [0]Found in mEmergencyNumberList",
    ]

    attempt = EmergencyCallParser().analyze(lines)[0]

    assert attempt["end_time"] == "08-13 16:37:28.000"
