from parsers.analysis_bucket_builder import AnalysisBucketBuilder
from parsers.diagnostic_parser import BinderWarningParser


def _events_by_type(lines):
    return {event["type"]: event for event in BinderWarningParser().analyze(lines)}


def test_binder_transaction_delay_parses_case_insensitive_android_format():
    events = BinderWarningParser().analyze(
        [
            "06-05 07:17:57.130 wifi 3027 3121 W libbinder.Binder: Binder transaction to android.hardware.wifi.IWifiChip code 1 took 1147ms. Data bytes: 84 Reply bytes: 4 Flags: 18",
            "06-05 07:17:58.130 wifi 3027 3121 W libbinder.Binder: binder transaction to android.hardware.wifi.IWifiChip code 1 took 999ms. Data bytes: 84",
        ]
    )

    assert len(events) == 1
    assert events[0]["type"] == "TRANSACTION_DELAY"
    assert "[android.hardware.wifi.IWifiChip]" in events[0]["desc"]
    assert events[0]["rca_candidate"] is False


def test_short_binder_starvation_is_secondary_but_long_starvation_is_rca_candidate():
    events = BinderWarningParser().analyze(
        [
            "05-23 15:38:57.779 1041 1308 1308 E libbinder.IPCThreadState: binder thread pool (15 threads) starved for 133 ms",
            "05-23 15:38:58.779 1041 1308 1308 E libbinder.IPCThreadState: binder thread pool (1 threads) starved for 1330 ms",
        ]
    )

    assert [event["rca_candidate"] for event in events] == [False, True]
    assert [event["evidence_role"] for event in events] == ["secondary_signal", "rca_candidate"]


def test_binder_alloc_no_vma_is_secondary_symptom():
    event = _events_by_type(
        ["[ 1245.411590] [3:m.android.phone: 3946] binder_alloc: 2910: binder_alloc_buf, no vma"]
    )["BINDER_BUFFER_ERROR"]

    assert event["rca_candidate"] is False
    assert event["evidence_role"] == "secondary_symptom"
    assert "단독 Binder buffer Root Cause로 단정하지 않습니다" in event["desc"]


def test_repeated_binder_delay_requires_three_events_in_time_window():
    parser = BinderWarningParser()
    sparse = parser.analyze(
        [
            "06-05 07:17:00.000 wifi 1 1 W libbinder.Binder: Binder transaction to android.foo.IBar code 1 took 1200ms.",
            "06-05 07:19:00.000 wifi 1 1 W libbinder.Binder: Binder transaction to android.foo.IBar code 1 took 1200ms.",
            "06-05 07:21:00.000 wifi 1 1 W libbinder.Binder: Binder transaction to android.foo.IBar code 1 took 1200ms.",
        ]
    )
    dense = parser.analyze(
        [
            "06-05 07:17:00.000 wifi 1 1 W libbinder.Binder: Binder transaction to android.foo.IBar code 1 took 1200ms.",
            "06-05 07:17:20.000 wifi 1 1 W libbinder.Binder: Binder transaction to android.foo.IBar code 1 took 1300ms.",
            "06-05 07:17:40.000 wifi 1 1 W libbinder.Binder: Binder transaction to android.foo.IBar code 1 took 1400ms.",
        ]
    )

    assert "REPEATED_BINDER_DELAY" not in [event["type"] for event in sparse]
    assert "REPEATED_BINDER_DELAY" in [event["type"] for event in dense]


def test_binder_bucket_matching_is_case_insensitive():
    def add_context_window(buckets, name, lines, idx, window=0):
        buckets[name].add(idx)

    buckets = AnalysisBucketBuilder(add_context_window).build(
        ["06-05 07:17:57.130 wifi 1 1 W libbinder.Binder: binder transaction to android.foo.IBar code 1 took 1200ms."]
    )

    assert buckets["binder"]
