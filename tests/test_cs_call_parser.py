from parsers.telephony_parser import TelephonyParser


def test_standalone_radio_mo_call_drop_keeps_tc_slot_and_vendor_cause():
    lines = [
        "03-03 09:44:03.772 radio  2490  2490 D RILJ    : [2342]> DIAL [PHONE0][GCCT0]",
        "03-03 09:44:03.930 radio  2490  2490 D GsmCdmaCallTracker: [0] poll: pendingMO= callId: TC@3_1 objId: 49903947 incoming: false state: DIALING",
        "03-03 09:44:05.919 radio  2490  2565 D RILJ    : [2379]< GET_CURRENT_CALLS {} [PHONE0][GCCT0]",
        "03-03 09:44:05.935 radio  2490  2565 D RILJ    : [2387]< LAST_CALL_FAIL_CAUSE com.android.internal.telephony.LastCallFailCause@964ec64 causeCode: 34 vendorCause: 157 [PHONE0][GCCT0]",
        "03-03 09:44:05.937 radio  2490  2490 D GsmCdmaCallTracker: [0] EVENT_GET_LAST_CALL_FAIL_CAUSE - CALL_DROP - causeCode: 34",
    ]

    session = TelephonyParser().analyze(lines)[0]

    assert session["type"] == "CS"
    assert session["slot"] == "0"
    assert session["id"] == "TC@3_1"
    assert session["direction"] == "MO"
    assert session["status"] == "CALL DROP"
    assert session["fail_reason"] == "callFailCause: 34, vendorCause: 157"


def test_standalone_radio_mt_not_accepted_is_single_canceled_session():
    lines = [
        "03-13 11:21:15.934 radio  1793  1860 D RILJ    : [UNSL]< UNSOL_CALL_RING  [PHONE1]",
        "03-13 11:21:15.962 radio  1793  1860 D RILJ    : [4611]< GET_CURRENT_CALLS {[id=1,INCOMING,toa=145,norm,mt,0,voc,noevp,,cli=1,,1,audioQuality=1] } [PHONE1][GCCT1]",
        "03-13 11:21:36.385 radio  1793  1793 D GsmCdmaCallTracker: [1] poll: conn[i=0]= callId: TC@1185_1 objId: 242800155 incoming: true state: INCOMING, dc=null",
        "03-13 11:21:36.386 radio  1793  1860 D RILJ    : [4628]< GET_CURRENT_CALLS {} [PHONE1][GCCT1]",
        "03-13 11:21:36.391 radio  1793  1793 I Connection: notifyDisconnect: callId=TC@1185_1, reason=1",
    ]

    sessions = TelephonyParser().analyze(lines)

    assert len(sessions) == 1
    session = sessions[0]
    assert session["slot"] == "1"
    assert session["id"] == "TC@1185_1"
    assert session["direction"] == "MT"
    assert session["status"] == "CANCELED"
    assert session["is_user_reject"] is True
    assert session["fail_reason"] == "0"


def test_dump_dial_hangup_before_active_is_canceled_not_drop():
    lines = [
        "       Call Log:",
        "        2026-03-25T17:32:06.367214 - [7860]> DIAL",
        "        2026-03-25T17:32:06.376193 - [7860]< DIAL ",
        "        2026-03-25T17:32:06.387424 - [7863]< GET_CURRENT_CALLS {[id=1,DIALING,toa=129,norm,mo,0,voc,noevp,,cli=1,,1,audioQuality=1] }",
        "        2026-03-25T17:32:41.687033 - [7890]> HANGUP gsmIndex = 1",
        "        2026-03-25T17:32:41.704104 - [7890]< HANGUP ",
        "        2026-03-25T17:32:41.709449 - [7892]< GET_CURRENT_CALLS {}",
    ]

    session = TelephonyParser().analyze(lines)[0]

    assert session["direction"] == "MO"
    assert session["status"] == "CANCELED"
    assert session["is_user_reject"] is True
    assert session["fail_reason"] == "0"


def test_standalone_radio_mo_active_then_hangup_is_success():
    lines = [
        "03-25 13:48:51.283 radio  2072  2072 D RILJ    : [9919]> DIAL [PHONE0][GCCT0]",
        "03-25 13:48:51.337 radio  2072  2987 D RILJ    : [9922]< GET_CURRENT_CALLS {[id=1,DIALING,toa=129,norm,mo,0,voc,noevp,,cli=1,,1,audioQuality=1] } [PHONE0][GCCT0]",
        "03-25 13:48:51.400 radio  2072  2072 D GsmCdmaCallTracker: [0] poll: pendingMO= callId: TC@9_1 objId: 100 incoming: false state: DIALING",
        "03-25 13:48:59.975 radio  2072  2987 D RILJ    : [9936]< GET_CURRENT_CALLS {[id=1,ACTIVE,toa=129,norm,mo,0,voc,noevp,,cli=1,,1,audioQuality=1] } [PHONE0][GCCT0]",
        "03-25 13:49:59.996 radio  2072  2072 D RILJ    : [9945]> HANGUP gsmIndex = 1 [PHONE0][GCCT0]",
        "03-25 13:50:00.012 radio  2072  2987 D RILJ    : [9948]< GET_CURRENT_CALLS {} [PHONE0][GCCT0]",
    ]

    session = TelephonyParser().analyze(lines)[0]

    assert session["slot"] == "0"
    assert session["id"] == "TC@9_1"
    assert session["direction"] == "MO"
    assert session["status"] == "SUCCESS"
    assert session["is_user_reject"] is False
    assert session["fail_reason"] == "0"
