import pandas as pd

from core.charts import (
    RILJ_SLOW_THRESHOLD_MS,
    build_call_history_summary,
    build_data_call_summary,
    build_nitz_timeline,
    build_rf_call_timeline,
    build_rilj_overview,
    build_service_state_series,
    build_signal_level_series,
    build_sip_flow,
    map_registration_state,
)


def _oos_row(**overrides):
    row = {
        "log_type": "OOS_Event",
        "time": "08-25 10:00:00.000",
        "slot": "0",
        "voice_reg": "0",
        "data_reg": "0",
        "operator": "SKT",
        "rat": "LTE",
        "event": "REG_STATE_CHANGED",
        "candidate_reason": "None",
    }
    row.update(overrides)
    return row


def test_registration_codes_map_by_leading_digit():
    assert map_registration_state("0") == "IN_SERVICE"
    assert map_registration_state("1 (denied)") == "OUT_OF_SERVICE"
    assert map_registration_state("2") == "EMERGENCY_ONLY"
    assert map_registration_state("3") == "POWER_OFF"
    assert map_registration_state("POWER_OFF") == "POWER_OFF"
    assert map_registration_state("OUT_OF_SERVICE") == "OUT_OF_SERVICE"
    assert map_registration_state("Unknown") == "UNKNOWN"
    assert map_registration_state(float("nan")) == "UNKNOWN"
    assert map_registration_state(None) == "UNKNOWN"


def test_session_without_metadata_renders_nothing():
    series = build_service_state_series(pd.DataFrame())
    assert series.status == "unavailable"
    assert series.points == []


def test_metadata_without_oos_events_means_service_held():
    df = pd.DataFrame([{"log_type": "Call_Session", "status": "OK"}])
    assert build_service_state_series(df).status == "no_events"


def test_repeated_states_collapse_to_transitions():
    df = pd.DataFrame(
        [
            _oos_row(time="08-25 10:00:00.000"),
            _oos_row(time="08-25 10:00:01.000"),  # same state, dropped
            _oos_row(time="08-25 10:00:02.000", voice_reg="1", data_reg="0"),
        ]
    )

    series = build_service_state_series(df, year=2026)

    assert series.status == "ok"
    assert [(p.time, p.conn_type, p.state) for p in series.points] == [
        ("08-25 10:00:00.000", "Data", "IN_SERVICE"),
        ("08-25 10:00:00.000", "Voice", "IN_SERVICE"),
        ("08-25 10:00:02.000", "Voice", "OUT_OF_SERVICE"),
    ]


def test_points_carry_parsed_time_and_registered_label():
    df = pd.DataFrame([_oos_row(voice_reg="0", data_reg="1")])

    points = {p.conn_type: p for p in build_service_state_series(df, year=2026).points}

    assert points["Voice"].time_dt == pd.Timestamp("2026-08-25 10:00:00")
    assert points["Voice"].label == "[LTE] SKT"
    # Only a registered point can name the network, so the OOS one stays blank.
    assert points["Data"].state == "OUT_OF_SERVICE"
    assert points["Data"].label == ""
    assert points["Data"].raw_reg == "1"


def test_slots_are_tracked_independently():
    df = pd.DataFrame(
        [
            _oos_row(slot="0", voice_reg="0", data_reg="0"),
            _oos_row(slot="1", voice_reg="0", data_reg="0"),
            # Slot 1 drops while slot 0 keeps the state it already reported.
            _oos_row(time="08-25 10:00:05.000", slot="0", voice_reg="0", data_reg="0"),
            _oos_row(time="08-25 10:00:05.000", slot="1", voice_reg="3", data_reg="3"),
        ]
    )

    series = build_service_state_series(df, year=2026)

    assert series.slot_count == 2
    assert sorted({p.slot for p in series.points}) == ["Slot 0", "Slot 1"]
    assert [p.state for p in series.points if p.slot == "Slot 0"] == ["IN_SERVICE"] * 2
    assert sorted(p.state for p in series.points if p.slot == "Slot 1") == [
        "IN_SERVICE",
        "IN_SERVICE",
        "POWER_OFF",
        "POWER_OFF",
    ]


def test_frame_uses_the_column_names_plotly_shows():
    df = pd.DataFrame([_oos_row()])

    frame = build_service_state_series(df, year=2026).to_frame()

    assert list(frame.columns) == [
        "time",
        "time_dt",
        "Slot",
        "Type",
        "State",
        "Raw_Reg",
        "Event",
        "Cause",
        "Operator",
        "Radio_Tech",
        "Label",
    ]
    assert str(frame["time_dt"].dtype).startswith("datetime64")


def test_unparsable_timestamps_keep_a_datetime_column():
    df = pd.DataFrame([_oos_row(time="not-a-time")])

    series = build_service_state_series(df, year=2026)

    assert all(p.time_dt is None for p in series.points)
    assert str(series.to_frame()["time_dt"].dtype).startswith("datetime64")


# --------------------------------------------------------------- call sessions


def test_call_table_is_newest_first_and_gaps_are_dashed():
    df = pd.DataFrame(
        [
            {"log_type": "Call_Session", "time": "08-25 10:00:00.000", "status": "OK", "fail_reason": None},
            {"log_type": "Call_Session", "time": "08-25 11:00:00.000", "status": "CALL_DROP", "fail_reason": "31"},
        ]
    )

    summary = build_call_history_summary(df)

    assert summary.status == "ok"
    assert summary.call_count == 2
    assert summary.table["time"].tolist() == ["08-25 11:00:00.000", "08-25 10:00:00.000"]
    assert summary.table["fail_reason"].tolist() == ["31", "-"]
    # Pie input keeps log order, not table order.
    assert summary.statuses == ["OK", "CALL_DROP"]


def test_calls_without_a_status_field_report_no_breakdown():
    df = pd.DataFrame([{"log_type": "Call_Session", "time": "08-25 10:00:00.000"}])
    assert build_call_history_summary(df).statuses is None


def test_call_history_can_use_report_sessions_directly():
    sessions = [
        {
            "id": "TC@4_1",
            "start_time": "04-09 12:07:59.968",
            "status": "FAIL",
            "fail_reason": "504_CODE_USER_DECLINE (SIP_480_Temporarily Unavailable)",
            "release_reason": "0",
        }
    ]

    summary = build_call_history_summary(sessions)

    assert summary.status == "ok"
    assert summary.call_count == 1
    assert summary.statuses == ["FAIL"]
    assert summary.table["time"].tolist() == ["04-09 12:07:59.968"]
    assert summary.table["id"].tolist() == ["TC@4_1"]
    assert summary.table["fail_reason"].tolist() == ["504_CODE_USER_DECLINE (SIP_480_Temporarily Unavailable)"]


def test_session_without_calls_is_distinct_from_no_metadata():
    assert build_call_history_summary(pd.DataFrame()).status == "unavailable"
    assert build_call_history_summary(pd.DataFrame([{"log_type": "OOS_Event"}])).status == "no_calls"


# ---------------------------------------------------------------- signal level


def test_signal_hover_lists_reported_rats_only():
    df = pd.DataFrame(
        [
            {
                "log_type": "Signal_Level",
                "time": "08-25 10:00:00.000",
                "level": "3",
                "rat": "LTE",
                "slot": "0",
                "raw_info": "raw",
                "details_LTE": "RSRP:-95",
                "details_NR": None,
                "details_GSM": "None",
            }
        ]
    )

    point = build_signal_level_series(df).points[0]

    assert point.level == 3.0  # Chroma hands levels back as strings
    assert point.hover_detail == "<b>LTE</b>: RSRP:-95"


def test_signal_frame_carries_the_axis_column_names():
    df = pd.DataFrame(
        [{"log_type": "Signal_Level", "time": "08-25 10:00:00.000", "level": 2, "rat": "NR", "slot": "1"}]
    )

    frame = build_signal_level_series(df).to_frame()

    assert list(frame.columns) == ["time", "Level", "rat", "slot", "hover_detail", "raw_info"]
    assert frame["Level"].tolist() == [2.0]


def test_signal_states_are_distinct_when_missing():
    assert build_signal_level_series(pd.DataFrame()).status == "unavailable"
    assert build_signal_level_series(pd.DataFrame([{"log_type": "Call_Session"}])).status == "no_data"


# ------------------------------------------------------------------- data call


def _setup(**overrides):
    row = {
        "event_type": "DATA_SETUP",
        "status": "SUCCESS",
        "latency_ms": 100,
        "req_time": "08-25 10:00:00.000",
        "apn": "internet",
    }
    row.update(overrides)
    return row


def test_data_call_kpi_ignores_unmeasured_latency():
    summary = build_data_call_summary(
        [
            _setup(latency_ms=100),
            _setup(latency_ms=0),  # never measured, must not drag the average down
            _setup(status="FAIL", latency_ms=300),
        ],
        year=2026,
    )

    assert summary.kpi.attempt_count == 3
    assert summary.kpi.fail_count == 1
    assert round(summary.kpi.success_rate, 1) == 66.7
    assert summary.kpi.avg_setup_latency_ms == 200.0


def test_unchanged_unsol_updates_stay_out_of_the_chart():
    rows = [_setup(), {"event_type": "UNSOL_UPDATE", "status": "ACTIVE", "req_time": "08-25 10:00:01.000", "is_changed": False}]

    summary = build_data_call_summary(rows, year=2026)

    assert [point["event_type"] for point in summary.points] == ["DATA_SETUP"]
    assert len(summary.table) == 2  # the table still lists every event


def test_data_call_fills_in_fields_the_parser_never_emitted():
    summary = build_data_call_summary([{"event_type": "DATA_SETUP", "req_time": "08-25 10:00:00.000"}], year=2026)

    assert summary.table["latency_ms"].tolist() == [0]
    assert summary.table["apn"].tolist() == ["UNKNOWN"]
    assert summary.kpi.avg_setup_latency_ms == 0.0


def test_empty_data_call_history():
    assert build_data_call_summary([]).status == "no_data"
    assert build_data_call_summary(None).status == "no_data"


# --------------------------------------------------------------------- IMS SIP


def _sip(method, time, direction="Tx", is_error=False):
    return {
        "time": time,
        "direction": direction,
        "msg_type": "req",
        "method_code": method,
        "tid": "t1",
        "cseq": "1 INVITE",
        "is_error": is_error,
        "raw_log": "raw",
    }


def test_sip_setup_latency_spans_invite_to_final_ok():
    flow = build_sip_flow(
        [
            _sip("INVITE", "08-25 10:00:00.000"),
            _sip("100 Trying", "08-25 10:00:00.500", direction="Rx"),
            _sip("200 OK", "08-25 10:00:02.500", direction="Rx"),
        ]
    )

    assert flow.kpi.transaction_count == 3
    assert flow.kpi.setup_latency_ms == 2500
    assert [m.kind for m in flow.messages] == ["normal", "normal", "success"]
    assert [m.is_outgoing for m in flow.messages] == [True, False, False]


def test_sip_errors_outrank_success_codes_when_coloring():
    flow = build_sip_flow([_sip("486 Busy Here", "08-25 10:00:01.000", direction="Rx", is_error=True)])

    assert flow.kpi.error_count == 1
    assert flow.messages[0].kind == "error"
    assert flow.messages[0].time_label == "10:00:01.000"


def test_sip_call_without_an_answer_has_no_setup_latency():
    flow = build_sip_flow([_sip("INVITE", "08-25 10:00:00.000")])
    assert flow.kpi.setup_latency_ms is None


def test_sip_ladder_is_ordered_by_time():
    flow = build_sip_flow([_sip("BYE", "08-25 10:00:09.000"), _sip("INVITE", "08-25 10:00:01.000")])
    assert [m.method_code for m in flow.messages] == ["INVITE", "BYE"]


def test_no_sip_messages():
    assert build_sip_flow([]).status == "no_data"


# ------------------------------------------------------------ RILJ transaction


def _completed(**overrides):
    request = {
        "start_time": "08-25 10:00:00.000",
        "latency_ms": 10,
        "command": "RIL_REQUEST_GET_SIM_STATUS",
        "is_error": False,
        "error_msg": "",
        "req_details": "req",
        "resp_details": "resp",
    }
    request.update(overrides)
    return request


def test_rilj_flags_timeouts_errors_and_slow_requests():
    overview = build_rilj_overview(
        {
            "rilj_transactions": {
                "completed": [
                    _completed(latency_ms=10),  # healthy, stays out of the table
                    _completed(latency_ms=501, start_time="08-25 10:00:02.000"),
                    _completed(is_error=True, error_msg="NETWORK_ERR", start_time="08-25 10:00:03.000"),
                ],
                "timeouts": [{"time": "08-25 10:00:01.000", "command": "RIL_REQUEST_DIAL", "details": "d"}],
                "unsol": [],
            }
        }
    )

    assert (overview.kpi.request_count, overview.kpi.timeout_count, overview.kpi.error_count) == (4, 1, 1)
    assert overview.abnormal["Status"].tolist() == ["TIMEOUT", "SLOW", "ERROR"]
    latencies = overview.abnormal["Latency(ms)"].tolist()
    assert pd.isna(latencies[0])  # a request without a response has no latency
    assert latencies[1:] == [501, 10]


def test_rilj_slow_threshold_is_exclusive():
    """A request landing exactly on the threshold is still a healthy one."""
    overview = build_rilj_overview(
        {
            "rilj_transactions": {
                "completed": [_completed(latency_ms=RILJ_SLOW_THRESHOLD_MS)],
                "timeouts": [],
                "unsol": [],
            }
        }
    )

    assert overview.slow_threshold_ms == RILJ_SLOW_THRESHOLD_MS
    assert overview.abnormal.empty


def test_rilj_unsol_events_are_tabulated_by_time():
    overview = build_rilj_overview(
        {
            "rilj_transactions": {
                "completed": [],
                "timeouts": [],
                "unsol": [
                    {"time": "08-25 10:00:05.000", "command": "UNSOL_NITZ", "details": "b"},
                    {"time": "08-25 10:00:01.000", "command": "UNSOL_SIGNAL", "details": "a"},
                ],
            }
        }
    )

    assert list(overview.unsol.columns) == ["Time", "Command", "Details"]
    assert overview.unsol["Command"].tolist() == ["UNSOL_SIGNAL", "UNSOL_NITZ"]


def test_report_without_rilj_data():
    assert build_rilj_overview({}).status == "no_data"
    assert build_rilj_overview(None).status == "no_data"


# ----------------------------------------------------- RF / call joint timeline


def _signal_sample(details, time="08-25 10:00:00"):
    return {"time": time, "level": 3, "slot": "0", "rat": "LTE", "details": details}


def test_rf_timeline_prefers_lte_and_falls_back_to_nr():
    timeline = build_rf_call_timeline(
        {
            "signal_level_history": [
                _signal_sample({"LTE": {"RSRP": "-95dBm", "RSRQ": "-11", "SINR": "5"}}),
                _signal_sample({"LTE": {"RSRP": "Unknown"}, "NR": {"RSRP": "-110dBm"}}, time="08-25 10:00:01"),
            ]
        },
        year=2026,
    )

    assert [p.rsrp_dbm for p in timeline.rsrp_points] == [-95, -110]
    assert "RAT: NR" in timeline.rsrp_points[1].hover_text
    assert "SINR: Unknown" in timeline.rsrp_points[1].hover_text


def test_rf_timeline_survives_a_malformed_sample():
    timeline = build_rf_call_timeline(
        {
            "signal_level_history": [
                _signal_sample("not-a-dict"),
                _signal_sample({"LTE": {"RSRP": "-90dBm"}}, time="08-25 10:00:02"),
            ]
        },
        year=2026,
    )

    assert [p.rsrp_dbm for p in timeline.rsrp_points] == [-90]


def test_unfinished_call_still_gets_a_visible_span():
    timeline = build_rf_call_timeline(
        {
            "signal_level_history": [_signal_sample({"LTE": {"RSRP": "-90dBm"}})],
            "call_sessions": [
                {"start_time": "08-25 10:00:00", "end_time": None, "status": "ACTIVE", "type": "MO", "id": "TC@1"},
                {"start_time": "08-25 10:00:10", "end_time": "08-25 10:00:20", "status": "CALL_DROP", "type": "MT", "id": "TC@2"},
            ],
        },
        year=2026,
    )

    ongoing, dropped = timeline.call_spans
    assert (ongoing.end_dt - ongoing.start_dt) == pd.Timedelta(seconds=5)
    assert ongoing.is_drop is False and ongoing.label == "MO 완료"
    assert dropped.is_drop is True and dropped.label == "MT 실패/Drop (TC@2)"


def test_rf_timeline_needs_signal_history():
    assert build_rf_call_timeline({"call_sessions": [{"start_time": "08-25 10:00:00"}]}).status == "no_signal_history"


# ------------------------------------------------------------------------ NITZ


def _nitz(minute, timezone):
    return {
        "log_time": f"2026-03-26 10:{minute:02d}:00",
        "nitz_raw": "NITZ: 26/03/26",
        "timezone": timezone,
        "dst_status": "미적용",
    }


def test_nitz_ignores_timezones_that_never_settled():
    """A zone held for a minute is a glitch; the same hop held for half an hour counts."""
    glitch = build_nitz_timeline([_nitz(0, "UTC+9시간"), _nitz(20, "UTC+1시간"), _nitz(21, "UTC+9시간")])
    genuine = build_nitz_timeline([_nitz(0, "UTC+9시간"), _nitz(20, "UTC+1시간"), _nitz(50, "UTC+9시간")])

    assert glitch.kpi.change_count == 1
    assert genuine.kpi.change_count == 2

    assert glitch.kpi.first_timezone == "UTC+9시간"
    assert glitch.kpi.last_timezone == "UTC+9시간"
    assert glitch.kpi.stability == "stable"
    # The detail table still shows every change, including the glitch.
    assert len(glitch.changes) == 3


def test_nitz_ping_pong_is_reported_as_unstable():
    timeline = build_nitz_timeline(
        [_nitz(0, "UTC+9시간"), _nitz(15, "UTC+1시간"), _nitz(30, "UTC+9시간"), _nitz(45, "UTC+1시간")]
    )

    assert timeline.kpi.change_count == 3
    assert timeline.kpi.stability == "unstable"


def test_nitz_maps_offsets_onto_the_nearest_known_region():
    timeline = build_nitz_timeline([_nitz(0, "UTC+9시간"), _nitz(15, "UTC+9시간"), _nitz(30, "UTC-5시간")])

    geo = {point.offset_label: point for point in timeline.geo}
    assert geo["UTC+9.0"].region == "Korea/Japan (UTC+9)"
    assert geo["UTC+9.0"].count == 2
    assert geo["UTC-5.0"].region == "US Eastern (UTC-5)"
    assert list(timeline.changes.columns) == ["변경 시간", "타임존", "원본 NITZ 데이터"]


def test_unparsable_nitz_history_is_treated_as_missing():
    assert build_nitz_timeline([]).status == "no_data"
    assert build_nitz_timeline([{"log_time": "nonsense", "timezone": "UTC+9시간", "nitz_raw": "x"}]).status == "no_data"


def test_half_hour_timezones_keep_their_fraction():
    """India sits on UTC+5.5; truncating to 5 moved the offset line by 30 minutes."""
    timeline = build_nitz_timeline([_nitz(0, "UTC+5.5시간"), _nitz(20, "UTC+5.5시간")])

    assert [point.offset for point in timeline.offsets] == [5.5, 5.5]
    assert timeline.geo[0].offset_label == "UTC+5.5"
    assert timeline.geo[0].region == "India (UTC+5.5)"


def test_timeout_rows_leave_the_latency_cell_empty_not_nan():
    overview = build_rilj_overview(
        {
            "rilj_transactions": {
                "completed": [_completed(latency_ms=501, start_time="08-25 10:00:02.000")],
                "timeouts": [{"time": "08-25 10:00:01.000", "command": "RIL_REQUEST_DIAL", "details": "d"}],
                "unsol": [],
            }
        }
    )

    latency = overview.abnormal["Latency(ms)"]
    assert str(latency.dtype) == "Int64"  # a float column would print "NaN"
    assert latency.tolist()[1] == 501
