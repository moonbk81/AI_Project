import json

from agent_toolkit.binder_tools import get_binder_warning_analytics


def test_proxy_histogram_only_counts_as_rca_signal_above_threshold(tmp_path):
    report = {
        "binder_warnings": [
            {
                "time": "06-01 15:13:17.000",
                "type": "BINDER_PROXY_HISTOGRAM",
                "max_count": 10,
                "desc": "Binder Proxy 객체 상태 덤프",
            }
        ]
    }
    (tmp_path / "low_report.json").write_text(json.dumps(report), encoding="utf-8")

    low = json.loads(get_binder_warning_analytics("low", result_dir=str(tmp_path)))

    assert low["has_binder_rca_signal"] is False
    assert low["proxy_leak_histograms"][0]["is_leak_candidate"] is False

    report["binder_warnings"][0]["max_count"] = 1001
    (tmp_path / "high_report.json").write_text(json.dumps(report), encoding="utf-8")

    high = json.loads(get_binder_warning_analytics("high", result_dir=str(tmp_path)))

    assert high["has_binder_rca_signal"] is True
    assert high["proxy_leak_histograms"][0]["is_leak_candidate"] is True
