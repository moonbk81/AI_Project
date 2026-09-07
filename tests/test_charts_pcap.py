"""패킷 캡처 차트 시리즈."""

import pandas as pd

from core.charts import build_pcap_overview


def capture(file="tcpdump_any.pcap", status="OK", **overrides):
    data = {
        "status": status,
        "capture": {
            "file": file,
            "packet_count": 1000,
            "byte_count": 500_000,
            "start_time": "09-02 08:00:00.000",
            "end_time": "09-02 08:10:00.000",
            "duration_sec": 600.0,
            "truncated": False,
        },
        "timebase": {
            "tz_offset_hours": 9.0,
            "source": "nitz",
            "confidence": "high",
            "note": "로그의 NITZ 에서 가져왔습니다.",
            "alignment": {"checked": True, "overlaps": True},
        },
        "flow_count": 12,
        "dns": {"query_count": 10, "unanswered_count": 0, "unanswered": [], "error_count": 0, "errors": []},
        "tcp": {
            "connect_attempts": 5,
            "connect_failures": [],
            "retransmission_count": 0,
            "retransmissions": [],
            "zero_window_count": 0,
            "zero_window": [],
            "reset_count": 0,
            "resets": [],
        },
        "tls": {"client_hello_count": 3, "top_server_names": [], "handshake_failures": []},
        "icmp": {"unreachable_count": 0, "unreachable": []},
        "silence_gaps": [],
        "silence_gap_count": 0,
        "throughput_timeline": {
            "bucket_sec": 10,
            "buckets": [
                {"time": "09-02 08:00:00.000", "packets": 100, "bytes": 12_500},
                {"time": "09-02 08:00:10.000", "packets": 50, "bytes": 2_500},
            ],
        },
        "kpi": {
            "packet_count": 1000,
            "dns_unanswered_count": 0,
            "dns_error_count": 0,
            "tcp_connect_failure_count": 0,
            "tls_handshake_failure_count": 0,
            "tcp_reset_count": 0,
            "tcp_retransmission_count": 0,
            "icmp_unreachable_count": 0,
            "longest_silence_sec": 0,
            "silence_gap_count": 0,
            "verdict": "패킷 수준에서 두드러진 이상은 보이지 않습니다.",
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    return data


def analysis(*captures, **overrides):
    data = {
        "capture_count": len(captures),
        "analyzed_count": len([item for item in captures if item.get("status") == "OK"]),
        "captures": list(captures),
        "tshark_version": "TShark (Wireshark) 3.6.2",
        "status": "OK",
    }
    data.update(overrides)
    return data


# ------------------------------------------------------------------ 없음/실패


def test_a_log_with_no_capture_has_no_section():
    assert build_pcap_overview({}).status == "no_data"
    assert build_pcap_overview(None).status == "no_data"
    assert build_pcap_overview([]).status == "no_data"


def test_a_failed_capture_is_not_drawn_as_a_clean_one():
    failed = capture(status="TSHARK_MISSING", message="tshark 가 설치돼 있지 않습니다.")

    series = build_pcap_overview(
        analysis(failed, status="TSHARK_MISSING", message="tshark 가 설치돼 있지 않습니다.")
    )

    assert series.status == "TSHARK_MISSING"
    assert "tshark" in series.message
    assert series.capture_count == 1
    assert series.analyzed_count == 0
    assert series.timeline.empty
    assert series.anomalies.empty


# ---------------------------------------------------------------------- 정상


def test_the_capture_row_carries_the_window_and_the_verdict():
    series = build_pcap_overview(analysis(capture()))

    assert series.status == "ok"
    assert series.analyzed_count == 1
    assert series.tshark_version.startswith("TShark")
    row = series.captures.iloc[0]
    assert row["file"] == "tcpdump_any.pcap"
    assert row["packet_count"] == 1000
    assert row["duration_sec"] == 600.0
    assert row["flow_count"] == 12
    assert row["verdict"].startswith("패킷 수준에서")


def test_the_timeline_is_per_second_so_captures_can_be_overlaid():
    series = build_pcap_overview(analysis(capture()))

    assert list(series.timeline["kbps"]) == [10.0, 2.0]  # 12500B/10s = 10kbps
    assert series.bucket_sec == 10
    assert series.timeline["time_dt"].tolist() == [
        pd.Timestamp(f"{pd.Timestamp.now().year}-09-02 08:00:00"),
        pd.Timestamp(f"{pd.Timestamp.now().year}-09-02 08:00:10"),
    ]


def test_two_captures_become_two_series_and_one_set_of_counts():
    first = capture("phone.pcap", kpi={"tcp_reset_count": 3})
    second = capture("server.pcap", kpi={"tcp_reset_count": 4})

    series = build_pcap_overview(analysis(first, second))

    assert series.capture_count == 2
    assert series.analyzed_count == 2
    assert sorted(series.timeline["file"].unique()) == ["phone.pcap", "server.pcap"]
    counts = series.anomalies.set_index("label")["count"]
    assert counts["TCP RST"] == 7


def test_a_broken_capture_beside_a_good_one_only_drops_itself():
    series = build_pcap_overview(
        analysis(capture("broken.pcap", status="PARSE_FAILED"), capture("good.pcap"))
    )

    assert series.status == "ok"
    assert series.capture_count == 2
    assert series.analyzed_count == 1
    assert series.captures["file"].tolist() == ["good.pcap"]


def test_kinds_that_never_happened_stay_in_the_breakdown_at_zero():
    series = build_pcap_overview(analysis(capture()))

    counts = series.anomalies.set_index("label")["count"]
    assert counts["TCP 재전송"] == 0
    # "0건" 은 대답이다. 행을 지우면 재본 것인지 아닌지 알 수 없다.
    assert len(series.anomalies) == 8


def test_zero_window_is_counted_from_the_tcp_section_it_lives_in():
    series = build_pcap_overview(analysis(capture(tcp={"zero_window_count": 6})))

    counts = series.anomalies.set_index("label")["count"]
    assert counts["TCP zero window"] == 6


# ------------------------------------------------------------------ 침묵/근거


def test_silence_gaps_are_ranked_by_duration_across_captures():
    first = capture("phone.pcap", silence_gaps=[
        {"start_time": "09-02 08:01:00.000", "end_time": "09-02 08:01:08.000", "duration_sec": 8.0},
    ])
    second = capture("server.pcap", silence_gaps=[
        {"start_time": "09-02 08:02:00.000", "end_time": "09-02 08:02:44.000", "duration_sec": 44.0},
    ])

    gaps = build_pcap_overview(analysis(first, second)).gaps

    assert gaps["duration_sec"].tolist() == [44.0, 8.0]
    assert gaps["file"].tolist() == ["server.pcap", "phone.pcap"]
    # 차트가 띠로 얹으려면 시각이 필요하다. 표에 쓸 원래 문자열은 그대로 남는다.
    assert gaps["start_dt"].iloc[0] == pd.Timestamp(f"{pd.Timestamp.now().year}-09-02 08:02:00")
    assert gaps["end_dt"].iloc[0] == pd.Timestamp(f"{pd.Timestamp.now().year}-09-02 08:02:44")


def test_the_examples_behind_the_counts_are_flattened_with_their_kind():
    busy = capture(
        dns={
            "unanswered": [{"time": "09-02 08:00:05.000", "query": "lost.com"}],
            "errors": [{"time": "09-02 08:00:07.000", "query": "bad.com", "rcode": "NXDOMAIN"}],
        },
        tcp={
            "connect_failures": [
                {"time": "09-02 08:00:06.000", "dst": "203.0.113.9", "dst_port": "443"}
            ],
        },
        tls={
            "handshake_failures": [
                {"time": "09-02 08:00:08.000", "server_name": "stalled.example.com"}
            ]
        },
    )

    events = build_pcap_overview(analysis(busy)).events

    assert events["time"].tolist() == [
        "09-02 08:00:05.000",
        "09-02 08:00:06.000",
        "09-02 08:00:07.000",
        "09-02 08:00:08.000",
    ]
    assert events["kind"].tolist() == ["DNS 무응답", "TCP 연결 실패", "DNS 에러응답", "TLS 실패"]
    assert events["detail"].tolist() == [
        "lost.com",
        "203.0.113.9:443",
        "bad.com → NXDOMAIN",
        "stalled.example.com",
    ]


def test_an_empty_capture_still_answers_with_the_columns_the_card_reads():
    series = build_pcap_overview(analysis(capture()))

    assert list(series.gaps.columns) == ["file", "start_time", "end_time", "duration_sec"]
    assert list(series.events.columns) == ["file", "kind", "time", "detail"]
    assert series.gaps.empty and series.events.empty


# ------------------------------------------------------------------- 경고 문구


def test_a_guessed_timezone_is_surfaced_as_a_warning():
    guessed = capture(timebase={
        "confidence": "low",
        "source": "host",
        "note": "로그에 NITZ 가 없어 분석 서버의 시간대를 썼습니다.",
        "alignment": {"checked": False, "reason": "로그 구간을 알 수 없습니다."},
    })

    warnings = build_pcap_overview(analysis(guessed)).warnings

    assert any("NITZ 가 없어" in warning for warning in warnings)


def test_a_capture_that_misses_the_log_window_says_how_far_off_it_looks():
    misaligned = capture(timebase={
        "alignment": {
            "checked": True,
            "overlaps": False,
            "warning": "pcap 캡처 구간이 로그 구간과 겹치지 않습니다.",
            "suggested_extra_offset_hours": -9,
        },
    })

    warnings = build_pcap_overview(analysis(misaligned)).warnings

    assert any("겹치지 않습니다" in warning and "-9시간" in warning for warning in warnings)


def test_a_truncated_walk_and_encrypted_dns_are_both_admitted():
    partial = capture(
        capture={"truncated": True, "truncated_note": "패킷 5,000,000개에서 읽기를 멈췄습니다."},
        dns={"encrypted_dns": {"packets": 40, "note": "Private DNS 가 켜져 있어 질의 내용은 보이지 않습니다."}},
    )

    warnings = build_pcap_overview(analysis(partial)).warnings

    assert any("읽기를 멈췄습니다" in warning for warning in warnings)
    assert any("Private DNS" in warning for warning in warnings)


def test_a_clean_capture_with_a_known_zone_has_nothing_to_warn_about():
    assert build_pcap_overview(analysis(capture())).warnings == []


def test_timestamps_that_do_not_parse_leave_the_timeline_empty_not_wrong():
    undated = capture(throughput_timeline={
        "bucket_sec": 10,
        "buckets": [{"time": "때 미상", "packets": 1, "bytes": 100}],
    })

    series = build_pcap_overview(analysis(undated))

    assert series.status == "ok"
    assert series.timeline.empty
    assert series.captures.iloc[0]["packet_count"] == 1000
