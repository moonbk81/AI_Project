from parsers.internet_stall_parser import InternetStallParser


def test_data_stall_lifecycle_events_are_grouped_into_flows():
    parser = InternetStallParser()

    result = parser.analyze(
        [
            "08-25 10:00:00.000  1000  1000 D DataStallRecoveryManager: data stall start",
            "08-25 10:00:05.000  1000  1000 D DataStallRecoveryManager: recovery start",
            "08-25 10:00:12.000  1000  1000 D DataStallRecoveryManager: recovery end",
            "08-25 10:00:12.000  1000  1000 D DataStallRecoveryManager: data stall end",
        ]
    )

    assert result["kpi"]["data_stall_flow_count"] == 1
    assert result["kpi"]["data_stall_count"] == 4
    assert result["data_stall_flows"][0]["start_time"] == "08-25 10:00:00.000"
    assert result["data_stall_flows"][0]["recovery_start_time"] == "08-25 10:00:05.000"
    assert result["data_stall_flows"][0]["recovery_end_time"] == "08-25 10:00:12.000"
    assert result["data_stall_flows"][0]["end_time"] == "08-25 10:00:12.000"
    assert result["data_stall_flows"][0]["duration_sec"] == 12
    assert result["data_stall_flows"][0]["status"] == "회복 완료"


def test_wifi_datastall_sampling_lines_do_not_create_lifecycle_flows():
    result = InternetStallParser().analyze(
        [
            "08-25 10:00:00.000  1000  1000 D WifiDataStall: tx tput in kbps: 441000",
            "08-25 10:00:00.000  1000  1000 D WifiDataStall: rx tput in kbps: 441000",
            "08-25 10:00:00.000  1000  1000 D WifiDataStall: ccaLevel = 38",
        ]
    )

    assert result["kpi"]["data_stall_flow_count"] == 0
    assert result["kpi"]["data_stall_count"] == 0
    assert result["data_stall_flows"] == []


def test_rf_warnings_are_preserved_outside_compacted_timeline():
    result = InternetStallParser().analyze(
        [],
        report_data={
            "signal_level_history": [
                {"time": "08-25 10:00:00.000", "slot": "0", "rat": "NR", "level": 1, "raw_info": "weak"},
                {"time": "08-25 10:00:01.000", "slot": "0", "rat": "LTE", "level": 4, "raw_info": "good"},
            ],
            "oos_events": [
                {"time": "08-25 10:00:02.000", "slotId": "1", "voice_reg": "OUT_OF_SERVICE", "data_reg": "OUT_OF_SERVICE"}
            ],
        },
    )

    assert result["kpi"]["rf_warning_count"] == 2
    assert [item["event_type"] for item in result["rf_warnings"]] == ["WEAK_SIGNAL", "OOS_OR_REG_STATE"]
