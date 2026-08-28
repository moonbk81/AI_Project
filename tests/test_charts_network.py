import pandas as pd

from core.charts import (
    DNS_SPIKE_THRESHOLD_MS,
    INTERNET_STALL_LAYER_TABS,
    build_data_usage_profile,
    build_data_usage_top_by_time,
    build_dns_error_breakdown,
    build_dns_health_warnings,
    build_dns_issue_summary,
    build_internet_stall_report,
    build_network_timeline_stats,
)


# ------------------------------------------------------------------ DNS errors


def _dns_query(app_name, return_code):
    return {"log_type": "DNS_Query", "app_name": app_name, "return_code": return_code}


def test_successful_lookups_are_not_errors():
    df = pd.DataFrame(
        [
            _dns_query("com.a", "0"),
            _dns_query("com.a", "SUCCESS"),
            _dns_query("com.a", "NXDOMAIN"),
            _dns_query("com.b", "NXDOMAIN"),
            _dns_query("com.a", "NXDOMAIN"),
        ]
    )

    breakdown = build_dns_error_breakdown(df)

    assert breakdown.status == "ok"
    assert breakdown.counts.set_index(["app_name", "return_code"])["count"].to_dict() == {
        ("com.a", "NXDOMAIN"): 2,
        ("com.b", "NXDOMAIN"): 1,
    }
    assert breakdown.pivot.loc["com.a", "NXDOMAIN"] == 2


def test_dns_states_without_errors_or_fields():
    clean = pd.DataFrame([_dns_query("com.a", "0")])
    assert build_dns_error_breakdown(clean).status == "no_errors"

    without_fields = pd.DataFrame([{"log_type": "DNS_Query"}])
    assert build_dns_error_breakdown(without_fields).status == "unavailable"
    assert build_dns_error_breakdown(pd.DataFrame()).status == "unavailable"


# ---------------------------------------------------------- DNS server health


def test_health_warnings_carry_the_server_that_stopped_answering():
    df = pd.DataFrame(
        [{"log_type": "DNS_Health_Warning", "net_id": 100, "server_ip": "8.8.8.8", "score": 12, "timeout_count": 7, "description": "재부팅 권장"}]
    )

    warning = build_dns_health_warnings(df)[0]

    assert (warning.net_id, warning.server_ip, warning.score, warning.timeout_count) == (100, "8.8.8.8", 12, 7)
    assert warning.description == "재부팅 권장"


def test_health_warnings_fall_back_when_the_parser_emitted_no_such_field():
    """Defaults cover an absent column; a present-but-empty value stays as it is."""
    df = pd.DataFrame([{"log_type": "DNS_Health_Warning"}])

    warning = build_dns_health_warnings(df)[0]

    assert (warning.net_id, warning.server_ip, warning.score) == ("Unknown", "Unknown", 0)
    assert (warning.timeout_count, warning.description) == (0, "")


def test_no_health_warnings():
    assert build_dns_health_warnings(pd.DataFrame([{"log_type": "DNS_Query"}])) == []
    assert build_dns_health_warnings(pd.DataFrame()) == []


# ------------------------------------------------------------------ DNS issues


def test_dns_issue_table_uses_display_headers():
    df = pd.DataFrame(
        [
            {"log_type": "Network_DNS_Issue", "time": "08-25 10:00:00", "net_id": 100, "package": "com.a", "result": "EAI_NODATA", "suspected_reason": "BLOCKED"},
            {"log_type": "Network_DNS_Issue", "time": "08-25 10:00:01", "net_id": 100, "package": "com.a", "result": "TIMEOUT", "suspected_reason": "TIMEOUT"},
        ]
    )

    summary = build_dns_issue_summary(df)

    assert summary.status == "ok"
    assert summary.reasons == ["BLOCKED", "TIMEOUT"]  # log order drives the pie
    assert summary.package_counts.to_dict("records") == [{"package": "com.a", "count": 2}]
    assert list(summary.table.columns) == ["Time", "NetID", "Package", "Result/Error Code", "Suspected Reason"]


def test_no_dns_issues():
    assert build_dns_issue_summary(pd.DataFrame([{"log_type": "DNS_Query"}])).status == "no_data"


# ------------------------------------------------------- network timeline stat


def _stat(time, dns_avg, **overrides):
    row = {
        "log_type": "Network_Timeline_Stat",
        "time": time,
        "netId": 100,
        "transport": "CELLULAR",
        "dns_avg": dns_avg,
        "dns_err_rate": 0,
    }
    row.update(overrides)
    return row


def test_timeline_stats_coerce_strings_and_label_net_ids():
    df = pd.DataFrame([_stat("08-25 10:00:00", "250")])

    stats = build_network_timeline_stats(df, year=2026)

    assert stats.status == "ok"
    assert stats.frame["dns_avg"].tolist() == [250.0]
    assert stats.frame["netId"].tolist() == ["100"]  # an id is a label, not a number
    assert stats.frame["time_dt"].tolist() == [pd.Timestamp("2026-08-25 10:00:00")]


def test_tcp_metric_is_offered_only_when_it_was_measured():
    without = build_network_timeline_stats(pd.DataFrame([_stat("08-25 10:00:00", 10)]), year=2026)
    assert [metric.column for metric in without.metrics] == ["dns_avg", "dns_err_rate"]

    with_tcp = build_network_timeline_stats(
        pd.DataFrame([_stat("08-25 10:00:00", 10, tcp_avg_loss=1.5)]), year=2026
    )
    assert [metric.column for metric in with_tcp.metrics] == ["dns_avg", "dns_err_rate", "tcp_avg_loss"]

    all_missing = build_network_timeline_stats(
        pd.DataFrame([_stat("08-25 10:00:00", 10, tcp_avg_loss=None)]), year=2026
    )
    assert [metric.column for metric in all_missing.metrics] == ["dns_avg", "dns_err_rate"]


def test_spikes_come_from_latency_or_a_flagged_delay():
    df = pd.DataFrame(
        [
            _stat("08-25 10:00:00", 10),  # healthy
            _stat("08-25 10:00:01", DNS_SPIKE_THRESHOLD_MS),
            _stat("08-25 10:00:02", 20, dns_delayed_cnt=3),
            _stat("08-25 10:00:03", 5000),
        ]
    )

    stats = build_network_timeline_stats(df, year=2026)

    assert stats.spikes["dns_avg"].tolist() == [5000.0, 1000.0, 20.0]  # worst first
    assert stats.spike_threshold_ms == DNS_SPIKE_THRESHOLD_MS


def test_timeline_stats_without_usable_time():
    df = pd.DataFrame([_stat("nonsense", 10)])
    assert build_network_timeline_stats(df).status == "unparsable_time"
    assert build_network_timeline_stats(pd.DataFrame([{"log_type": "DNS_Query"}])).status == "no_data"


# ------------------------------------------------------------------ data usage


def _usage(app_name, total_mb, rat="LTE", time="2026-08-25 10:00:00"):
    return {"log_type": "Data_Usage", "app_name": app_name, "total_mb": total_mb, "rat": rat, "time": time}


def test_usage_totals_are_summed_per_app_and_rat():
    df = pd.DataFrame(
        [
            _usage("YouTube", 100),
            _usage("YouTube", "50"),  # Chroma hands numbers back as strings
            _usage("Chrome", 10, rat="5G (NR)"),
        ]
    )

    profile = build_data_usage_profile(df, year=2026)

    assert profile.app_totals.to_dict("records") == [
        {"app_name": "YouTube", "total_mb": 150.0},
        {"app_name": "Chrome", "total_mb": 10.0},
    ]
    assert profile.rat_totals.set_index("rat")["total_mb"].to_dict() == {"LTE": 150.0, "5G (NR)": 10.0}


def test_usage_pie_is_capped_at_the_top_ten_apps():
    df = pd.DataFrame([_usage(f"app{i:02d}", i) for i in range(1, 15)])

    profile = build_data_usage_profile(df, year=2026)

    assert len(profile.app_totals) == 10
    assert profile.app_totals["app_name"].tolist()[0] == "app14"


def test_usage_timeline_states():
    dated = build_data_usage_profile(pd.DataFrame([_usage("YouTube", 1)]), year=2026)
    assert dated.timeline_status == "ok"

    unparsable = build_data_usage_profile(pd.DataFrame([_usage("YouTube", 1, time="시간 미상")]), year=2026)
    assert unparsable.timeline_status == "empty"

    undated = pd.DataFrame([{"log_type": "Data_Usage", "app_name": "YouTube", "total_mb": 1, "rat": "LTE"}])
    assert build_data_usage_profile(undated).timeline_status == "absent"


def test_usage_profile_states():
    assert build_data_usage_profile(pd.DataFrame()).status == "unavailable"
    assert build_data_usage_profile(pd.DataFrame([{"log_type": "DNS_Query"}])).status == "no_data"


def test_usage_top_by_time_keeps_top_seven_per_hour():
    rows = [
        _usage("YouTube", 100, time="2026-08-25 10:10:00"),
        _usage("Chrome", 80, time="2026-08-25 10:20:00"),
        _usage("Maps", 60, time="2026-08-25 10:30:00"),
        _usage("Mail", 40, time="2026-08-25 10:40:00"),
        _usage("Store", 30, time="2026-08-25 10:41:00"),
        _usage("Music", 20, time="2026-08-25 10:42:00"),
        _usage("Chat", 10, time="2026-08-25 10:43:00"),
        _usage("Tiny", 1, time="2026-08-25 10:44:00"),
        _usage("YouTube", 5, time="2026-08-25 11:01:00"),
    ]

    series = build_data_usage_top_by_time(pd.DataFrame(rows), year=2026)

    assert series.status == "ok"
    assert series.top_n == 7
    assert series.frame[series.frame["bucket"] == "08-25 10:00"]["app_name"].tolist() == [
        "YouTube",
        "Chrome",
        "Maps",
        "Mail",
        "Store",
        "Music",
        "Chat",
    ]
    assert series.frame[series.frame["bucket"] == "08-25 11:00"]["app_name"].tolist() == ["YouTube"]
    assert series.table.iloc[0].to_dict() == {
        "bucket": "08-25 10:00",
        "rank": 1,
        "app_name": "YouTube",
        "total_mb": 100,
    }


def test_usage_top_by_time_states():
    assert build_data_usage_top_by_time(pd.DataFrame()).status == "unavailable"
    assert build_data_usage_top_by_time(pd.DataFrame([{"log_type": "DNS_Query"}])).status == "no_data"
    assert build_data_usage_top_by_time(pd.DataFrame([_usage("YouTube", 1, time="시간 미상")])).status == "unparsable_time"


# -------------------------------------------------------------- internet stall


def _event(layer="DNS", event_type="DNS_TIMEOUT", time="08-25 10:00:00", **overrides):
    event = {"time": time, "layer": layer, "event_type": event_type, "severity": "critical", "raw": "line"}
    event.update(overrides)
    return event


def test_stall_kpi_falls_back_for_fields_the_report_omitted():
    report = build_internet_stall_report({"kpi": {"stall_window_count": 3}})

    assert report.kpi.stall_window_count == 3
    assert report.kpi.primary_root_cause_candidate == "UNKNOWN"
    assert report.kpi.tcp_tls_timeout_count == 0


def test_root_cause_rows_dash_out_missing_examples():
    report = build_internet_stall_report(
        {
            "root_cause_summary": {
                "DNS": {"count": 2, "confidence": {"high": 1}, "examples": [{"time": "08-25 10:00:00", "trigger": "t"}]},
                "RF": {"count": 1},
            }
        }
    )

    rows = report.root_causes.set_index("category").to_dict("index")
    assert rows["DNS"]["high"] == 1 and rows["DNS"]["example_time"] == "08-25 10:00:00"
    assert rows["RF"]["medium"] == 0 and rows["RF"]["example_trigger"] == "-"


def test_timeline_defaults_are_filled_for_the_chart_axes():
    report = build_internet_stall_report(
        {"timeline": [{"time": "08-25 10:00:00", "event_type": "DNS_TIMEOUT"}]}, year=2026
    )

    assert report.timeline_status == "ok"
    assert report.timeline["layer"].tolist() == ["UNKNOWN"]
    assert report.timeline["severity"].tolist() == ["info"]
    assert report.timeline["time_dt"].tolist() == [pd.Timestamp("2026-08-25 10:00:00")]


def test_timeline_states_without_usable_events():
    assert build_internet_stall_report({"timeline": []}).timeline_status == "empty"
    assert build_internet_stall_report({"timeline": [{"time": "nonsense"}]}).timeline_status == "unparsable_time"


def test_windows_are_ranked_worst_first_but_keep_their_index():
    report = build_internet_stall_report(
        {
            "stall_windows": [
                {"center_time": "08-25 10:00:00", "trigger": "mild", "severity_score": 1, "layer_counts": {"DNS": 1}},
                {
                    "center_time": "08-25 10:05:00",
                    "trigger": "severe",
                    "severity_score": 9,
                    "root_cause_candidates": [{"category": "DNS", "confidence": "high"}],
                    "related_events": [_event()],
                },
            ]
        },
        year=2026,
    )

    frame = report.windows_frame()
    assert frame["idx"].tolist() == [1, 0]  # ranked by severity, index still points into windows
    assert frame["primary_category"].tolist() == ["DNS", "UNKNOWN"]
    assert frame["layer_counts"].tolist() == ["{}", '{"DNS": 1}']

    severe = report.windows[1]
    assert severe.confidence == "high"
    assert severe.related_table["event_type"].tolist() == ["DNS_TIMEOUT"]
    assert severe.related_events[0]["raw"] == "line"  # raw events feed the log view


def test_layer_view_groups_the_tabs_parser_layers():
    report = build_internet_stall_report(
        {
            "timeline": [
                _event(layer="DATA_CALL", event_type="SETUP_FAIL"),
                _event(layer="DATA_STALL", event_type="STALL", time="08-25 10:00:01"),
                _event(layer="DATA_STALL", event_type="STALL", time="08-25 10:00:02"),
            ]
        },
        year=2026,
    )

    tabs = {tab.title: tab for tab in INTERNET_STALL_LAYER_TABS}
    view = report.layer_view(tabs["DataCall/Stall"].layers)

    assert view.status == "ok"
    assert view.counts.set_index("event_type")["count"].to_dict() == {"STALL": 2, "SETUP_FAIL": 1}
    assert "layer" not in view.table.columns  # the tab already says which layer

    assert report.layer_view(tabs["RF"].layers).status == "empty"


def test_missing_stall_report():
    assert build_internet_stall_report(None).status == "no_data"
    assert build_internet_stall_report({}).status == "no_data"
