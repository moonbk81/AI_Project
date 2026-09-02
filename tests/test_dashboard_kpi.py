from core.dashboard_kpi import compute_session_kpi


def test_headline_numbers_from_metadata():
    kpi = compute_session_kpi(
        [
            {"log_type": "Data_Usage", "app_name": "YouTube", "total_mb": "123.5"},
            {"log_type": "Data_Usage", "app_name": "Chrome", "total_mb": "9"},
            {"log_type": "Call_Session", "status": "OK"},
            {"log_type": "Call_Session", "status": "CALL_DROP"},
            {"log_type": "Call_Session", "status": "fail"},
            {"log_type": "OOS_Event", "voice_reg": "OUT_OF_SERVICE", "data_reg": "IN_SERVICE"},
            {"log_type": "OOS_Event", "voice_reg": "IN_SERVICE", "data_reg": "IN_SERVICE"},
            {"log_type": "Signal_Level", "level": 3},
            {"log_type": "Signal_Level", "level": 1},
            {"log_type": "Build_Info", "model_name": "q7q", "build_id": "BP4A", "radio": "R1", "network": "TMB"},
            {
                "log_type": "System_Property",
                "gsm.sim.state": "LOADED,ABSENT",
                "gsm.sim.operator.numeric": "310260,",
                "gsm.sim.operator.alpha": "T-Mobile,",
                "gsm.operator.numeric": "310260,",
                "gsm.operator.alpha": "T-Mobile,",
            },
        ]
    )

    assert kpi["top_app_name"] == "YouTube"
    assert kpi["top_app_mb"] == 123.5
    assert kpi["call_success_rate"] == 33.3
    assert kpi["call_drop_count"] == 2
    assert kpi["oos_count"] == 1
    assert kpi["avg_signal_level"] == 2.0
    assert kpi["device_context"]["model_name"] == "q7q"
    sim0, sim1 = kpi["device_context"]["sim_slots"]
    assert sim0["state"] == "LOADED"
    assert sim0["carrier"] == "T-Mobile"
    assert sim0["mcc_mnc"] == "310260"
    # The trailing comma means slot 1 is present but empty.
    assert sim1["state"] == "ABSENT"
    assert sim1["carrier"] == "N/A"
    assert sim1["mcc_mnc"] == "N/A"

    net0, net1 = kpi["device_context"]["network_slots"]
    assert net0["plmn"] == "310260"
    assert net0["network_name"] == "T-Mobile"
    assert net1["plmn"] == "N/A"
    assert net1["network_name"] == "N/A"


def test_empty_session_reports_neutral_numbers():
    assert compute_session_kpi([]) == {
        "top_app_name": "N/A",
        "top_app_mb": 0.0,
        "call_success_rate": 100.0,
        "call_drop_count": 0,
        "oos_count": 0,
        "avg_signal_level": 0.0,
        "device_context": {
            "model_name": "N/A",
            "build_id": "N/A",
            "radio": "N/A",
            "network": "N/A",
            "mobile_data": "N/A",
            "mobile_data_changed_at": "",
            "mobile_data_changed_by": "",
            "sim_slots": [],
            "network_slots": [],
        },
    }


def test_signal_level_stored_as_string_still_averages():
    """Chroma hands metadata values back as strings, which used to raise."""
    kpi = compute_session_kpi(
        [
            {"log_type": "Signal_Level", "level": "4"},
            {"log_type": "Signal_Level", "level": "2"},
        ]
    )
    assert kpi["avg_signal_level"] == 3.0


def test_missing_columns_do_not_raise():
    kpi = compute_session_kpi(
        [
            {"log_type": "Signal_Level"},
            {"log_type": "Call_Session"},
            {"log_type": "OOS_Event"},
            {"log_type": "Data_Usage"},
            {"no_log_type_at_all": 1},
        ]
    )
    assert kpi["avg_signal_level"] == 0.0
    assert kpi["call_drop_count"] == 0
    assert kpi["call_success_rate"] == 100.0
    assert kpi["oos_count"] == 0
    assert kpi["top_app_name"] == "N/A"


def test_unparseable_data_usage_counts_as_zero_mb():
    kpi = compute_session_kpi(
        [{"log_type": "Data_Usage", "app_name": "X", "total_mb": "not-a-number"}]
    )
    assert kpi == {
        "top_app_name": "X",
        "top_app_mb": 0.0,
        "call_success_rate": 100.0,
        "call_drop_count": 0,
        "oos_count": 0,
        "avg_signal_level": 0.0,
        "device_context": {
            "model_name": "N/A",
            "build_id": "N/A",
            "radio": "N/A",
            "network": "N/A",
            "mobile_data": "N/A",
            "mobile_data_changed_at": "",
            "mobile_data_changed_by": "",
            "sim_slots": [],
            "network_slots": [],
        },
    }


def test_oos_counted_from_either_registration_column():
    assert compute_session_kpi([{"log_type": "OOS_Event", "data_reg": "OOS"}])["oos_count"] == 1
    assert compute_session_kpi([{"log_type": "OOS_Event", "voice_reg": "OOS"}])["oos_count"] == 1
    assert compute_session_kpi([{"log_type": "OOS_Event", "data_reg": "POWER_OFF"}])["oos_count"] == 1
    # One row flagged on both columns is still one event.
    both = [{"log_type": "OOS_Event", "voice_reg": "OOS", "data_reg": "OUT_OF_SERVICE"}]
    assert compute_session_kpi(both)["oos_count"] == 1


def test_single_sim_property_has_no_comma():
    """A single-SIM dump writes a bare value, not a comma-joined pair."""
    kpi = compute_session_kpi(
        [
            {
                "log_type": "System_Property",
                "gsm.sim.state": "LOADED",
                "gsm.sim.operator.numeric": "310260",
                "gsm.sim.operator.alpha": "T-Mobile",
                "gsm.operator.numeric": "310260",
                "gsm.operator.alpha": "T-Mobile",
            }
        ]
    )
    ctx = kpi["device_context"]
    assert len(ctx["sim_slots"]) == 1
    assert ctx["sim_slots"][0] == {
        "slot": "0",
        "state": "LOADED",
        "carrier": "T-Mobile",
        # 가입 정보가 없는 덤프라 SIM 종류는 알 수 없다.
        "sim_type": "N/A",
        "mcc": "310",
        "mnc": "260",
        "mcc_mnc": "310260",
    }
    assert len(ctx["network_slots"]) == 1
    assert ctx["network_slots"][0]["plmn"] == "310260"
    assert ctx["network_slots"][0]["network_name"] == "T-Mobile"


def test_mobile_data_setting_is_read_as_words():
    """로그의 0/1 을 그대로 타일에 띄우면 무슨 뜻인지 알 수 없다."""
    on = compute_session_kpi([{"log_type": "System_Property", "mobile_data": "1"}])
    off = compute_session_kpi([{"log_type": "System_Property", "mobile_data": "0"}])

    assert on["device_context"]["mobile_data"] == "사용"
    assert off["device_context"]["mobile_data"] == "사용 안 함"


def test_mobile_data_carries_the_change_that_made_it_so():
    """꺼져 있을 때 사용자 조작인지 앱이 바꾼 것인지가 갈린다."""
    kpi = compute_session_kpi([{
        "log_type": "System_Property",
        "mobile_data": "0",
        "mobile_data_changed_at": "08-23 10:01:51.098",
        "mobile_data_changed_by": "com.android.phone",
    }])
    ctx = kpi["device_context"]

    assert ctx["mobile_data"] == "사용 안 함"
    assert ctx["mobile_data_changed_at"] == "08-23 10:01:51.098"
    assert ctx["mobile_data_changed_by"] == "com.android.phone"


def test_a_session_without_the_setting_says_nothing_about_it():
    # 설정이 찍히기 전에 적재한 세션. 0 으로 읽어 "꺼져 있었다"고 하면 안 된다.
    kpi = compute_session_kpi([{"log_type": "Build_Info", "model_name": "q7q"}])

    assert kpi["device_context"]["mobile_data"] == "N/A"


def test_sim_slots_carry_the_esim_flag_from_the_subscription_dump():
    """LOADED 만으로는 그 슬롯이 eSIM 인지 알 수 없다. 가입 정보에서 온 종류를
    슬롯 번호로 맞춰 붙인다.
    """
    kpi = compute_session_kpi(
        [
            {
                "log_type": "System_Property",
                "gsm.sim.state": "LOADED,LOADED",
                "gsm.sim.operator.alpha": "SKTelecom,SKTelecom",
                "sim_slot0_type": "pSIM",
                "sim_slot1_type": "eSIM",
            }
        ]
    )

    sim0, sim1 = kpi["device_context"]["sim_slots"]
    assert sim0["sim_type"] == "pSIM"
    assert sim1["sim_type"] == "eSIM"
