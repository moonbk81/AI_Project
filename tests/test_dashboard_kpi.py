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
        ]
    )

    assert kpi == {
        "top_app_name": "YouTube",
        "top_app_mb": 123.5,
        "call_success_rate": 33.3,
        "call_drop_count": 2,
        "oos_count": 1,
        "avg_signal_level": 2.0,
    }


def test_empty_session_reports_neutral_numbers():
    assert compute_session_kpi([]) == {
        "top_app_name": "N/A",
        "top_app_mb": 0.0,
        "call_success_rate": 100.0,
        "call_drop_count": 0,
        "oos_count": 0,
        "avg_signal_level": 0.0,
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
    }


def test_oos_counted_from_either_registration_column():
    assert compute_session_kpi([{"log_type": "OOS_Event", "data_reg": "OOS"}])["oos_count"] == 1
    assert compute_session_kpi([{"log_type": "OOS_Event", "voice_reg": "OOS"}])["oos_count"] == 1
    assert compute_session_kpi([{"log_type": "OOS_Event", "data_reg": "POWER_OFF"}])["oos_count"] == 1
    # One row flagged on both columns is still one event.
    both = [{"log_type": "OOS_Event", "voice_reg": "OOS", "data_reg": "OUT_OF_SERVICE"}]
    assert compute_session_kpi(both)["oos_count"] == 1
