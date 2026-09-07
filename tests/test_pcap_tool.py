"""LLM 이 패킷 캡처 결과를 읽을 때 쓰는 도구."""

import json

from agent_toolkit import get_pcap_analytics


def _write(tmp_path, base_name, analysis):
    (tmp_path / f"{base_name}_pcap.json").write_text(
        json.dumps(analysis, ensure_ascii=False), encoding="utf-8"
    )


def _capture(**overrides):
    capture = {
        "status": "OK",
        "capture": {
            "file": "tcpdump_any.pcap",
            "packet_count": 19922,
            "start_time": "09-02 08:42:03.000",
            "end_time": "09-02 09:00:04.000",
            "duration_sec": 1081.6,
        },
        "timebase": {
            "source": "nitz",
            "confidence": "high",
            "alignment": {"checked": True, "overlaps": True},
        },
        "dns": {"unanswered": [], "errors": []},
        "tcp": {"connect_failures": []},
        "tls": {"handshake_failures": []},
        "silence_gaps": [],
        "top_flows": [],
        "kpi": {"tcp_reset_count": 89, "verdict": "RST 가 반복됩니다."},
    }
    capture.update(overrides)
    return capture


def _analysis(*captures, **overrides):
    analysis = {
        "capture_count": len(captures),
        "analyzed_count": len([item for item in captures if item.get("status") == "OK"]),
        "captures": list(captures),
        "tshark_version": "TShark (Wireshark) 3.6.2",
        "status": "OK",
    }
    analysis.update(overrides)
    return analysis


def test_no_capture_tells_the_model_to_answer_without_one(tmp_path):
    result = json.loads(get_pcap_analytics("radio", result_dir=str(tmp_path)))

    assert result["status"] == "NO_DATA"
    assert "pcap 없이" in result["message"]


def test_a_failed_analysis_is_never_offered_as_a_clean_capture(tmp_path):
    _write(
        tmp_path,
        "radio",
        _analysis(
            _capture(status="TSHARK_MISSING"),
            status="TSHARK_MISSING",
            message="tshark 가 설치돼 있지 않습니다.",
        ),
    )

    result = json.loads(get_pcap_analytics("radio", result_dir=str(tmp_path)))

    assert result["status"] == "TSHARK_MISSING"
    assert "tshark" in result["message"]
    # 분석 실패를 "이상 없음" 으로 쓰지 못하게 못을 박아 둔다.
    assert "근거로 쓰면 안 됩니다" in result["reading_guidance"]


def test_the_verdict_and_its_evidence_travel_together(tmp_path):
    busy = _capture(
        dns={
            "unanswered": [{"time": "09-02 08:50:00.000", "query": "lost.com"}] * 20,
            "errors": [],
        },
        tcp={"connect_failures": [{"time": "09-02 08:51:00.000", "dst": "203.0.113.9", "dst_port": "443"}]},
        silence_gaps=[{"start_time": "09-02 08:52:00.000", "end_time": "09-02 08:52:44.000", "duration_sec": 44.0}],
    )
    _write(tmp_path, "radio", _analysis(busy))

    result = json.loads(get_pcap_analytics("radio", result_dir=str(tmp_path)))

    assert result["status"] == "OK"
    assert result["analyzed_count"] == 1
    fact = result["captures"][0]
    assert fact["verdict"] == "RST 가 반복됩니다."
    assert fact["window"] == ["09-02 08:42:03.000", "09-02 09:00:04.000"]
    assert fact["kpi"]["tcp_reset_count"] == 89
    # 근거는 컨텍스트에 들어가므로 개수를 묶어 둔다.
    assert len(fact["evidence"]["dns_unanswered"]) == 10
    assert fact["evidence"]["tcp_connect_failures"][0]["dst"] == "203.0.113.9"
    assert fact["evidence"]["longest_silence_gaps"][0]["duration_sec"] == 44.0


def test_a_clean_capture_asks_the_model_to_compare_it_against_the_log(tmp_path):
    _write(tmp_path, "radio", _analysis(_capture()))

    result = json.loads(get_pcap_analytics("radio", result_dir=str(tmp_path)))

    assert result["caveats"] == []
    assert "망보다 위쪽" in result["reading_guidance"]


def test_a_capture_beside_the_log_window_is_read_as_not_comparable(tmp_path):
    misaligned = _capture(timebase={
        "source": "host",
        "confidence": "low",
        "alignment": {
            "checked": True,
            "overlaps": False,
            "warning": "pcap 캡처 구간이 로그 구간과 겹치지 않습니다.",
            "suggested_extra_offset_hours": -9,
        },
    })
    _write(tmp_path, "radio", _analysis(misaligned))

    result = json.loads(get_pcap_analytics("radio", result_dir=str(tmp_path)))

    assert "비교 불가" in result["reading_guidance"]
    assert any("겹치지 않습니다" in caveat for caveat in result["caveats"])
    assert result["captures"][0]["caveats"]


def test_a_broken_capture_beside_a_good_one_only_drops_itself(tmp_path):
    _write(
        tmp_path,
        "radio",
        _analysis(_capture(status="PARSE_FAILED"), _capture()),
    )

    result = json.loads(get_pcap_analytics("radio", result_dir=str(tmp_path)))

    assert result["status"] == "OK"
    assert result["capture_count"] == 2
    assert result["analyzed_count"] == 1
    assert len(result["captures"]) == 1
