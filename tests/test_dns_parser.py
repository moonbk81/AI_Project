from parsers.diagnostic_parser import DnsParser


def test_blocked_dns_code_at_start_of_rest_is_not_unknown():
    lines = [
        "04-09 12:07:12.239  1000  3135  7135 D NetdEventListenerService: DNS Requested by 111, 10295, 4(FAIL), isBlocked=true, 0ms"
    ]

    result = DnsParser().analyze(
        lines,
        global_uid_map={"10295": "com.google.android.youtube"},
    )

    query = result["queries"][0]
    assert query["app_name"] == "com.google.android.youtube"
    assert query["return_code"] == "BLOCKED (Code:4)"
    assert query["latency_ms"] == 0
