from parsers.data_call_processor import DataCallProcessor


def test_data_evaluation_disallowed_request_is_reported():
    line = (
        "04-18 14:08:16.029 radio 4198 4198 D DNC-0 : Data evaluation: "
        "evaluation reason:NEW_REQUEST, Data disallowed reasons: DATA_DISABLED, "
        "candidate profile=null, time=14:08:16.027, network type=LTE, reg state=HOME, "
        "[NetworkRequest [ REQUEST id=4401, [ Transports: CELLULAR Capabilities: INTERNET "
        "Uid: 10516 RequestorUid: 10516 RequestorPkg: com.eg.android.AlipayGphone "
        "UnderlyingNetworks: Null] ], mPriority=20]"
    )

    events = DataCallProcessor().analyze([line])

    assert events == [
        {
            "event_type": "DATA_EVALUATION",
            "req_time": "04-18 14:08:16.029",
            "res_time": "04-18 14:08:16.029",
            "token": "DNC",
            "cid": "-1",
            "apn": "UNKNOWN",
            "network": "LTE",
            "protocol": "UNKNOWN",
            "status": "FAIL",
            "cause": "evaluation=NEW_REQUEST, disallowed=DATA_DISABLED",
            "latency_ms": 0,
            "requestor_pkg": "com.eg.android.AlipayGphone",
            "uid": "10516",
            "reg_state": "HOME",
            "candidate_profile": "null",
        }
    ]


def test_data_evaluation_allowed_profile_extracts_dnn():
    line = (
        "2026-04-18T10:31:55.453281 - onNetworkUnneeded: phoneId=0, "
        "[NetworkRequest [ REQUEST id=844, [ Capabilities: INTERNET Uid: 10400 "
        "RequestorUid: 1000 RequestorPkg: android UnderlyingNetworks: Null] ], "
        "evaluation result=Data evaluation: evaluation reason:DATA_ENABLED_CHANGED, "
        "Data allowed reason: NORMAL, candidate profile=[DataProfile=[ApnSetting] "
        "SK Telecom, 6821, 45005, 5g.sktelecom.com, , null, TrafficDescriptor={mDnn=5g.sktelecom.com, null}, "
        "preferred=true, cid=2], time=09:41:07.632]"
    )

    event = DataCallProcessor().analyze([line])[0]

    assert event["status"] == "SUCCESS"
    assert event["req_time"] == "04-18 10:31:55.453"
    assert event["apn"] == "5g.sktelecom.com"
    assert event["cid"] == "2"
    assert event["cause"] == "evaluation=DATA_ENABLED_CHANGED, allowed=NORMAL"
