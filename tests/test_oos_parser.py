from parsers.telephony_parser import OosParser


def test_oos_parser_detects_power_off_from_standalone_radio_service_state():
    lines = [
        "08-25 10:00:00.000 radio  1000  1000 D SST-0   : Poll ServiceState done: oldSS={mVoiceRegState=0(IN_SERVICE), mDataRegState=0(IN_SERVICE)} newSS={mVoiceRegState=0(IN_SERVICE), mDataRegState=0(IN_SERVICE), getRilVoiceRadioTechnology=14(LTE), mOperatorAlphaLong=SKT, mOperatorAlphaShort=SKT, mIsEmergencyOnly=false, mReasonDataDenied=0}",
        "08-25 10:00:05.000 radio  1000  1000 D SST-0   : Poll ServiceState done: oldSS={mVoiceRegState=0(IN_SERVICE), mDataRegState=0(IN_SERVICE)} newSS={mVoiceRegState=3(POWER_OFF), mDataRegState=3(POWER_OFF), getRilVoiceRadioTechnology=0(Unknown), mOperatorAlphaLong=, mOperatorAlphaShort=, mIsEmergencyOnly=false, mReasonDataDenied=0}",
    ]

    events = OosParser().analyze(lines)

    assert [event["event_type"] for event in events] == ["OOS_RECOVER", "OOS_ENTER"]
    power_off = events[-1]
    assert power_off["slotId"] == "0"
    assert power_off["voice_reg"] == "POWER_OFF"
    assert power_off["data_reg"] == "POWER_OFF"
    assert power_off["raw_voice_reg"] == "3(POWER_OFF)"
    assert power_off["root_cause_candidate"] == "AIRPLANE_MODE_OR_RADIO_POWER_OFF"


def test_oos_parser_accepts_canonical_service_state_names():
    lines = [
        "--------- beginning of radio",
        "08-25 10:00:00.000 radio  1000  1000 D DNC-1   : onServiceStateChanged: changed to {mVoiceRegState=IN_SERVICE, mDataRegState=IN_SERVICE, mOperatorAlphaLong=KT, mOperatorAlphaShort=KT, mIsEmergencyOnly=false}",
        "08-25 10:00:05.000 radio  1000  1000 D DNC-1   : onServiceStateChanged: changed to {mVoiceRegState=OUT_OF_SERVICE, mDataRegState=POWER_OFF, mOperatorAlphaLong=, mOperatorAlphaShort=, mIsEmergencyOnly=false}",
        "08-25 10:00:10.000 radio  1000  1000 D DNC-1   : onServiceStateChanged: changed to {mVoiceRegState=IN_SERVICE, mDataRegState=IN_SERVICE, mOperatorAlphaLong=KT, mOperatorAlphaShort=KT, mIsEmergencyOnly=false}",
    ]

    events = OosParser().analyze(lines)

    assert [event["slotId"] for event in events] == ["1", "1", "1"]
    assert [event["event_type"] for event in events] == ["OOS_RECOVER", "OOS_ENTER", "OOS_RECOVER"]
    assert events[1]["voice_reg"] == "OUT_OF_SERVICE"
    assert events[1]["data_reg"] == "POWER_OFF"
    assert events[1]["root_cause_candidate"] == "AIRPLANE_MODE_OR_RADIO_POWER_OFF"


def test_initial_out_of_service_is_oos_enter():
    lines = [
        "08-25 10:00:00.000 radio  1000  1000 D SST-1   : Poll ServiceState done: newSS={mVoiceRegState=1(OUT_OF_SERVICE), mDataRegState=1(OUT_OF_SERVICE), getRilVoiceRadioTechnology=0(Unknown), mOperatorAlphaLong=, mOperatorAlphaShort=, mIsEmergencyOnly=true, mReasonDataDenied=0}",
    ]

    session = OosParser().analyze(lines)[0]

    assert session["slotId"] == "1"
    assert session["event_type"] == "OOS_ENTER"
    assert session["voice_reg"] == "OUT_OF_SERVICE"
    assert session["data_reg"] == "OUT_OF_SERVICE"


def test_in_service_rat_changes_do_not_create_oos_events():
    lines = [
        "08-25 10:00:00.000 radio  1000  1000 D SST-0   : Poll ServiceState done: newSS={mVoiceRegState=0(IN_SERVICE), mDataRegState=0(IN_SERVICE), getRilVoiceRadioTechnology=14(LTE), mOperatorAlphaLong=SKT, mOperatorAlphaShort=SKT, mIsEmergencyOnly=false, mReasonDataDenied=0}",
        "08-25 10:00:05.000 radio  1000  1000 D SST-0   : Poll ServiceState done: newSS={mVoiceRegState=0(IN_SERVICE), mDataRegState=0(IN_SERVICE), getRilVoiceRadioTechnology=20(NR), mOperatorAlphaLong=SKT, mOperatorAlphaShort=SKT, mIsEmergencyOnly=false, mReasonDataDenied=0}",
    ]

    events = OosParser().analyze(lines)

    assert [event["event_type"] for event in events] == ["OOS_RECOVER"]


def test_radio_power_requests_mark_airplane_mode_boundaries():
    lines = [
        "04-07 06:47:50.586 radio  3843  3843 D AirplaneModeStats: Airplane mode change. Value: true",
        "04-07 06:47:51.555 radio  3843  3843 D RILJ    : [1008]> RADIO_POWER on = false forEmergencyCall= false preferredForEmergencyCall=false [PHONE0][GCCT0]",
        "04-07 06:47:51.556 radio  3843  3843 D RILJ    : [1009]> RADIO_POWER on = false forEmergencyCall= false preferredForEmergencyCall=false [PHONE1][GCCT1]",
        "04-07 06:47:52.000 radio  3843  3843 D SST-0   : Poll ServiceState done: newSS={mVoiceRegState=3(POWER_OFF), mDataRegState=3(POWER_OFF), getRilVoiceRadioTechnology=0(Unknown), mOperatorAlphaLong=, mOperatorAlphaShort=, mIsEmergencyOnly=false, mReasonDataDenied=0}",
        "04-07 06:48:10.000 radio  3843  3843 D RILJ    : [1010]> RADIO_POWER on = true forEmergencyCall= false preferredForEmergencyCall=false [PHONE0][GCCT0]",
        "04-07 06:48:15.000 radio  3843  3843 D SST-0   : Poll ServiceState done: newSS={mVoiceRegState=0(IN_SERVICE), mDataRegState=0(IN_SERVICE), getRilVoiceRadioTechnology=14(LTE), mOperatorAlphaLong=SKT, mOperatorAlphaShort=SKT, mIsEmergencyOnly=false, mReasonDataDenied=0}",
    ]

    events = OosParser().analyze(lines)

    assert [event["event_type"] for event in events] == [
        "AIRPLANE_MODE_ON",
        "AIRPLANE_MODE_ON",
        "AIRPLANE_MODE_ON",
        "OOS_ENTER",
        "AIRPLANE_MODE_OFF",
        "OOS_RECOVER",
    ]
    assert events[0]["slotId"] == "device"
    assert events[0]["change_reason"] == "airplane_mode"
    assert events[1]["slotId"] == "0"
    assert events[2]["slotId"] == "1"
    assert events[1]["radio_power"] == "OFF"
    assert "voice_reg" not in events[1]
    assert events[4]["radio_power"] == "ON"
    assert "data_reg" not in events[4]
