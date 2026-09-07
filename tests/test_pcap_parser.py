"""패킷 캡처 분석.

tshark 를 실제로 돌리지 않는다. 이 모듈이 책임지는 것은 tshark 가 뱉은 필드를
집계하는 규칙이고, 그 규칙만 검증하려면 필드 행을 직접 먹이는 편이 정확하다
(tshark 가 없는 CI 에서도 돌아야 한다).
"""

import pytest

from parsers import pcap_parser
from parsers.pcap_parser import PcapParser, TsharkMissing, analyze_pcaps, is_pcap_name


# 2026-09-02 00:00:00 UTC. 오프셋 0 으로 분석하므로 로그 시각도 그대로 09-02 다.
BASE_EPOCH = 1788307200.0

# tshark 필드 이름을 테스트에서 매번 적으면 무슨 행인지 안 읽힌다.
_ALIAS = {
    "epoch": "frame.time_epoch",
    "length": "frame.len",
    "src": "ip.src",
    "dst": "ip.dst",
    "sport": "tcp.srcport",
    "dport": "tcp.dstport",
    "flags": "tcp.flags",
    "stream": "tcp.stream",
    "usport": "udp.srcport",
    "udport": "udp.dstport",
    "dns_id": "dns.id",
    "is_response": "dns.flags.response",
    "qname": "dns.qry.name",
    "rcode": "dns.flags.rcode",
    "dns_time": "dns.time",
    "tls_type": "tls.handshake.type",
    "sni": "tls.handshake.extensions_server_name",
    "protocol": "_ws.col.protocol",
    "icmp_type": "icmp.type",
    "icmp_code": "icmp.code",
}

SYN = "0x0002"
SYN_ACK = "0x0012"


def _row(fields, values):
    row = [""] * len(fields)
    for alias, value in values.items():
        name = _ALIAS[alias]
        if name in fields:
            row[fields.index(name)] = "" if value is None else str(value)
    return row


def packet(offset_sec=0.0, length=100, protocol="TCP", **values):
    """1차 훑기 한 행."""
    values.update({"epoch": BASE_EPOCH + offset_sec, "length": length, "protocol": protocol})
    return _row(pcap_parser.WALK_FIELDS, values)


def anomaly(offset_sec=0.0, protocol="TCP", **values):
    """이상 징후 패스 한 행."""
    values.update({"epoch": BASE_EPOCH + offset_sec, "protocol": protocol})
    return _row(pcap_parser.ANOMALY_FIELDS, values)


@pytest.fixture()
def capture_file(tmp_path):
    path = tmp_path / "tcpdump_any.pcap"
    path.write_bytes(b"")  # 존재만 확인하고 tshark 는 가짜다
    return str(path)


def feed(monkeypatch, walk_rows=(), anomalies=None):
    """`_run_tshark` 자리에 준비한 행을 놓는다.

    표시 필터가 없는 호출은 1차 훑기, 있는 호출은 그 필터에 해당하는 이상 징후
    조회다 -- 파서가 tshark 를 부르는 방식이 그 둘뿐이다.
    """
    by_filter = {
        pcap_parser.ANOMALY_FILTERS[name]: rows for name, rows in (anomalies or {}).items()
    }

    def fake_run(path, fields, display_filter=None, timeout_sec=None, max_rows=None):
        if display_filter is None:
            return list(walk_rows)
        return list(by_filter.get(display_filter, []))

    monkeypatch.setattr(pcap_parser, "_run_tshark", fake_run)


def analyze(monkeypatch, capture_file, walk_rows, anomalies=None, log_window=None, **options):
    feed(monkeypatch, walk_rows, anomalies)
    parser = PcapParser(tz_offset_hours=0, **options)
    return parser.analyze(capture_file, {}, log_window)


# ------------------------------------------------------------------- 파일 이름


def test_pcap_names_include_pcapng_and_compressed_captures():
    assert is_pcap_name("tcpdump_any.pcap")
    assert is_pcap_name("capture.PCAPNG")
    assert is_pcap_name("old.cap")
    assert is_pcap_name("tcpdump_any.pcap.gz")
    assert not is_pcap_name("dumpstate.log")
    assert not is_pcap_name("radio.txt.gz")
    assert not is_pcap_name(None)


# ------------------------------------------------------------------------ DNS


def test_dns_queries_are_paired_with_their_answers(monkeypatch, capture_file):
    rows = [
        packet(0, protocol="DNS", qname="answered.com", dns_id="0x1", is_response=0),
        packet(0.2, protocol="DNS", qname="answered.com", dns_id="0x1", is_response=1, dns_time="0.2"),
        packet(1, protocol="DNS", qname="lost.com", dns_id="0x2", is_response=0),
    ]

    dns = analyze(monkeypatch, capture_file, rows)["dns"]

    assert dns["query_count"] == 2
    assert dns["response_count"] == 1
    assert dns["unanswered_count"] == 1
    assert dns["unanswered"] == [{"time": "09-02 00:00:01.000", "query": "lost.com"}]
    assert dns["slowest_queries"] == [
        {"time": "09-02 00:00:00.200", "query": "answered.com", "latency_ms": 200.0}
    ]
    assert dns["max_latency_ms"] == 200.0


def test_server_side_dns_errors_are_reported_under_dns_with_a_readable_code(
    monkeypatch, capture_file
):
    rows = [packet(0, protocol="DNS", qname="broken.com", dns_id="0x1", is_response=0)]
    errors = [anomaly(0.5, protocol="DNS", qname="broken.com", rcode="3", src="1.1.1.1", dst="10.0.0.2")]

    dns = analyze(monkeypatch, capture_file, rows, {"dns_errors": errors})["dns"]

    assert dns["error_count"] == 1
    assert dns["errors"][0]["rcode"] == "NXDOMAIN"
    assert dns["errors"][0]["query"] == "broken.com"


def test_encrypted_dns_is_reported_as_a_fact_because_the_names_are_not_visible(
    monkeypatch, capture_file
):
    rows = [
        packet(0, sport="41000", dport="853"),
        packet(1, sport="853", dport="41000"),
    ]

    dns = analyze(monkeypatch, capture_file, rows)["dns"]

    assert dns["encrypted_dns"]["packets"] == 2
    assert "Private DNS" in dns["encrypted_dns"]["note"]
    # 이름이 안 보이는 것이지 질의가 없던 것이 아니다.
    assert dns["query_count"] == 0


# ------------------------------------------------------------------------ TCP


def test_a_syn_without_its_syn_ack_is_a_connect_failure(monkeypatch, capture_file):
    rows = [
        packet(0, stream="0", flags=SYN, src="10.0.0.2", dst="93.184.216.34", dport="443"),
        packet(0.1, stream="0", flags=SYN_ACK, src="93.184.216.34", dst="10.0.0.2"),
        packet(1, stream="1", flags=SYN, src="10.0.0.2", dst="203.0.113.9", dport="80"),
    ]

    tcp = analyze(monkeypatch, capture_file, rows)["tcp"]

    assert tcp["connect_attempts"] == 2
    assert tcp["connect_failures"] == [
        {
            "time": "09-02 00:00:01.000",
            "dst": "203.0.113.9",
            "dst_port": "80",
            "reason": "SYN 을 보냈지만 SYN/ACK 이 오지 않음 (연결 수립 실패)",
        }
    ]


def test_anomaly_counts_are_the_total_even_when_the_event_list_is_capped(
    monkeypatch, capture_file
):
    monkeypatch.setattr(pcap_parser, "TOP_EVENTS", 2)
    resets = [anomaly(index, src="10.0.0.2", dst="93.184.216.34", dport="443") for index in range(5)]

    result = analyze(monkeypatch, capture_file, [packet(0)], {"tcp_resets": resets})

    assert result["tcp"]["reset_count"] == 5
    assert len(result["tcp"]["resets"]) == 2
    assert result["kpi"]["tcp_reset_count"] == 5


def test_a_failed_anomaly_query_does_not_take_the_whole_capture_down(
    monkeypatch, capture_file
):
    feed(monkeypatch, [packet(0)])
    working = pcap_parser._run_tshark

    def sometimes_broken(path, fields, display_filter=None, **kwargs):
        if display_filter == pcap_parser.ANOMALY_FILTERS["tcp_retransmissions"]:
            raise RuntimeError("tshark 실패: 표시 필터를 읽을 수 없음")
        return working(path, fields, display_filter, **kwargs)

    monkeypatch.setattr(pcap_parser, "_run_tshark", sometimes_broken)
    result = PcapParser(tz_offset_hours=0).analyze(capture_file, {})

    assert result["status"] == "OK"
    assert result["tcp"]["retransmission_count"] == 0


# ------------------------------------------------------------------------ TLS


def test_a_client_hello_without_a_server_hello_is_a_handshake_failure(
    monkeypatch, capture_file
):
    rows = [
        packet(0, stream="0", tls_type="1", sni="www.example.com", dst="93.184.216.34", dport="443"),
        packet(0.1, stream="0", tls_type="2", src="93.184.216.34"),
        packet(1, stream="1", tls_type="1", sni="stalled.example.com", dst="203.0.113.9", dport="443"),
    ]

    tls = analyze(monkeypatch, capture_file, rows)["tls"]

    assert tls["client_hello_count"] == 2
    # 이름은 건수 내림차순, 같으면 먼저 본 순서. JSON 으로는 [이름, 건수] 쌍이 된다.
    assert tls["top_server_names"] == [("www.example.com", 1), ("stalled.example.com", 1)]
    assert [failure["server_name"] for failure in tls["handshake_failures"]] == [
        "stalled.example.com"
    ]


def test_a_client_hello_without_sni_still_names_the_failure(monkeypatch, capture_file):
    rows = [packet(0, stream="0", tls_type="1", dst="203.0.113.9", dport="443")]

    tls = analyze(monkeypatch, capture_file, rows)["tls"]

    assert tls["handshake_failures"][0]["server_name"] == "(SNI 없음)"


# ------------------------------------------------------------- 조용한 구간 / 흐름


def test_silence_gaps_are_counted_in_full_even_though_only_the_longest_are_listed(
    monkeypatch, capture_file
):
    # 6초씩 벌어진 패킷 36개 -> 간격 35개. 목록은 상한(30)에서 잘린다.
    rows = [packet(index * 6) for index in range(36)]

    result = analyze(monkeypatch, capture_file, rows)

    assert len(result["silence_gaps"]) == pcap_parser.TOP_GAPS
    # 목록 길이를 건수로 읽으면 상한에 걸린 캡처가 전부 "30건" 으로 보인다.
    assert result["silence_gap_count"] == 35
    assert result["kpi"]["silence_gap_count"] == 35
    assert result["kpi"]["longest_silence_sec"] == 6.0


def test_traffic_closer_than_the_gap_threshold_is_not_a_silence(monkeypatch, capture_file):
    rows = [packet(0), packet(1), packet(2)]

    result = analyze(monkeypatch, capture_file, rows)

    assert result["silence_gaps"] == []
    assert result["kpi"]["longest_silence_sec"] == 0


def test_top_flows_rank_by_volume_and_carry_their_own_window(monkeypatch, capture_file):
    rows = [
        packet(0, length=1400, src="10.0.0.2", sport="41000", dst="93.184.216.34", dport="443"),
        packet(1, length=1400, src="10.0.0.2", sport="41000", dst="93.184.216.34", dport="443"),
        packet(2, length=60, src="10.0.0.2", sport="41001", dst="8.8.8.8", dport="53"),
    ]

    result = analyze(monkeypatch, capture_file, rows)

    assert result["flow_count"] == 2
    top = result["top_flows"][0]
    assert (top["dst"], top["packets"], top["bytes"]) == ("93.184.216.34", 2, 2800)
    assert (top["start_time"], top["end_time"]) == ("09-02 00:00:00.000", "09-02 00:00:01.000")
    # 흐름에는 epoch 을 남기지 않는다. 리포트의 시각은 전부 로그 시계다.
    assert not [key for key in top if key.endswith("_epoch")]


def test_ipv6_endpoints_are_read_when_there_is_no_ipv4_header(monkeypatch, capture_file):
    rows = [
        _row(
            pcap_parser.WALK_FIELDS,
            {
                "epoch": BASE_EPOCH,
                "length": 100,
                "protocol": "TCP",
                "sport": "41000",
                "dport": "443",
            },
        )
    ]
    rows[0][pcap_parser.WALK_FIELDS.index("ipv6.src")] = "2001:db8::2"
    rows[0][pcap_parser.WALK_FIELDS.index("ipv6.dst")] = "2001:db8::1"

    result = analyze(monkeypatch, capture_file, rows)

    assert result["top_flows"][0]["src"] == "2001:db8::2"
    assert result["top_flows"][0]["dst"] == "2001:db8::1"


def test_the_throughput_timeline_folds_instead_of_growing_without_end(
    monkeypatch, capture_file
):
    monkeypatch.setattr(pcap_parser, "MAX_TIMELINE_BUCKETS", 4)
    rows = [packet(index * 10, length=100) for index in range(40)]

    timeline = analyze(monkeypatch, capture_file, rows, min_gap_sec=100)["throughput_timeline"]

    assert timeline["bucket_sec"] > pcap_parser.MIN_BUCKET_SEC
    assert len(timeline["buckets"]) <= 4
    # 접는 것은 폭을 넓히는 일이지 버리는 일이 아니다.
    assert sum(bucket["packets"] for bucket in timeline["buckets"]) == 40
    assert sum(bucket["bytes"] for bucket in timeline["buckets"]) == 4000
    assert timeline["buckets"][0]["time"] == "09-02 00:00:00.000"


# ------------------------------------------------------------------ 캡처 메타


def test_the_capture_window_and_protocol_mix_come_from_the_walk(monkeypatch, capture_file):
    rows = [
        packet(0, protocol="TCP", length=100),
        packet(1, protocol="TCP", length=100),
        packet(2, protocol="DNS", length=80, qname="a.com", dns_id="0x1", is_response=0),
    ]

    result = analyze(monkeypatch, capture_file, rows)

    assert result["capture"]["packet_count"] == 3
    assert result["capture"]["byte_count"] == 280
    assert result["capture"]["start_time"] == "09-02 00:00:00.000"
    assert result["capture"]["end_time"] == "09-02 00:00:02.000"
    assert result["capture"]["duration_sec"] == 2.0
    assert result["capture"]["truncated"] is False
    assert list(result["protocols"]) == ["TCP", "DNS"]


def test_stopping_at_the_packet_limit_is_admitted_in_the_capture(monkeypatch, capture_file):
    rows = [packet(index) for index in range(5)]

    result = analyze(monkeypatch, capture_file, rows, min_gap_sec=100, max_packets=5)

    assert result["capture"]["truncated"] is True
    assert "포함되지 않았습니다" in result["capture"]["truncated_note"]


def test_the_alignment_against_the_log_window_travels_with_the_timebase(
    monkeypatch, capture_file
):
    rows = [packet(0), packet(1)]

    result = analyze(
        monkeypatch, capture_file, rows, log_window=("09-02 18:00:00", "09-02 19:00:00")
    )

    alignment = result["timebase"]["alignment"]
    assert alignment["overlaps"] is False
    assert "비교 불가" in alignment["warning"]


# -------------------------------------------------------------------- 소견/KPI


def _kpi(**overrides):
    kpi = {
        "packet_count": 1000,
        "tcp_reset_count": 0,
        "tcp_connect_attempts": 100,
        "longest_silence_sec": 0,
        "tcp_retransmission_count": 0,
    }
    kpi.update(overrides)
    return kpi


def test_the_verdict_names_the_worst_signal_it_can_see():
    assert "패킷이 없습니다" in PcapParser._verdict(_kpi(packet_count=0))
    assert "이름 해석" in PcapParser._verdict(_kpi(dns_unanswered_rate_pct=40))
    assert "SYN/ACK" in PcapParser._verdict(_kpi(tcp_connect_failure_rate_pct=40))
    assert "RST" in PcapParser._verdict(_kpi(tcp_reset_count=90, tcp_connect_attempts=77))
    assert "트래픽이 전혀 없는" in PcapParser._verdict(_kpi(longest_silence_sec=44))
    assert "재전송" in PcapParser._verdict(_kpi(tcp_retransmission_count=100))
    assert "두드러진 이상은 보이지 않습니다" in PcapParser._verdict(_kpi())


def test_rates_are_only_reported_when_there_was_something_to_divide_by(
    monkeypatch, capture_file
):
    quiet = analyze(monkeypatch, capture_file, [packet(0)])["kpi"]

    assert "tcp_connect_failure_rate_pct" not in quiet
    assert "dns_unanswered_rate_pct" not in quiet

    rows = [
        packet(0, stream="0", flags=SYN, src="10.0.0.2", dst="203.0.113.9", dport="80"),
        packet(1, protocol="DNS", qname="lost.com", dns_id="0x1", is_response=0),
    ]
    busy = analyze(monkeypatch, capture_file, rows)["kpi"]

    assert busy["tcp_connect_failure_rate_pct"] == 100.0
    assert busy["dns_unanswered_rate_pct"] == 100.0


# ------------------------------------------------------------------ 실패 처리


def test_a_missing_file_is_a_named_status_not_an_exception():
    result = PcapParser().analyze("/no/such/capture.pcap")

    assert result["status"] == "FILE_NOT_FOUND"
    assert result["capture"]["file"] == "capture.pcap"


def test_a_capture_with_nothing_readable_in_it_is_empty_not_ok(monkeypatch, capture_file):
    result = analyze(monkeypatch, capture_file, [])

    assert result["status"] == "EMPTY"


def test_rows_without_a_timestamp_are_not_packets(monkeypatch, capture_file):
    rows = [_row(pcap_parser.WALK_FIELDS, {"length": 100, "protocol": "TCP"})]

    result = analyze(monkeypatch, capture_file, rows)

    assert result["status"] == "EMPTY"


def test_a_missing_tshark_says_so_instead_of_reporting_a_clean_capture(
    monkeypatch, capture_file
):
    def missing(*args, **kwargs):
        raise TsharkMissing("tshark 가 설치돼 있지 않아 pcap 을 분석할 수 없습니다.")

    monkeypatch.setattr(pcap_parser, "_run_tshark", missing)
    result = PcapParser().analyze(capture_file, {})

    assert result["status"] == "TSHARK_MISSING"
    assert "tshark" in result["message"]


def test_a_broken_capture_is_reported_as_a_parse_failure(monkeypatch, capture_file):
    def broken(*args, **kwargs):
        raise RuntimeError("tshark 실패: The file appears to be damaged")

    monkeypatch.setattr(pcap_parser, "_run_tshark", broken)
    result = PcapParser().analyze(capture_file, {})

    assert result["status"] == "PARSE_FAILED"
    assert "damaged" in result["message"]


# --------------------------------------------------------------- 여러 캡처 묶기


def test_no_captures_is_no_section_at_all():
    assert analyze_pcaps([]) == {}
    assert analyze_pcaps(None) == {}
    assert analyze_pcaps(["", None]) == {}


def test_one_broken_capture_does_not_discard_the_others(monkeypatch):
    def canned(self, path, report_data=None, log_window=None):
        if "broken" in path:
            return {"status": "PARSE_FAILED", "message": "damaged", "capture": {"file": path}}
        return {"status": "OK", "capture": {"file": path}}

    monkeypatch.setattr(PcapParser, "analyze", canned)
    monkeypatch.setattr(pcap_parser, "tshark_version", lambda: "TShark 3.6.2")

    summary = analyze_pcaps(["broken.pcap", "good.pcap"])

    assert summary["status"] == "OK"
    assert summary["capture_count"] == 2
    assert summary["analyzed_count"] == 1
    assert summary["tshark_version"] == "TShark 3.6.2"
    assert [item["status"] for item in summary["captures"]] == ["PARSE_FAILED", "OK"]


def test_when_every_capture_fails_the_summary_carries_the_reason(monkeypatch):
    def always_broken(self, path, report_data=None, log_window=None):
        return {"status": "TSHARK_MISSING", "message": "tshark 가 없습니다", "capture": {}}

    monkeypatch.setattr(PcapParser, "analyze", always_broken)
    monkeypatch.setattr(pcap_parser, "tshark_version", lambda: "")

    summary = analyze_pcaps(["a.pcap", "b.pcap"])

    assert summary["status"] == "TSHARK_MISSING"
    assert summary["message"] == "tshark 가 없습니다"
    assert summary["analyzed_count"] == 0
