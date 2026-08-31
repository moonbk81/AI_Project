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


def test_benign_am_kill_reasons_are_not_rca_evidence():
    events = BinderWarningParser().analyze(
        [
            "08-22 15:24:10.921  1000  2837  5011 I am_kill : [0,13938,com.android.chrome:sandboxed_process0,0,isolated not needed,0]",
            "08-22 15:24:11.000  1000  2837  5011 I am_kill : [0,13556,com.example.app,0,remove task,0]",
        ]
    )

    assert [event["evidence_role"] for event in events] == ["benign_event", "benign_event"]
    assert [event["rca_candidate"] for event in events] == [False, False]
    assert "장애/강제 종료 근거나 Root Cause 로 인용하지 않습니다" in events[0]["desc"]


def test_kill_reasons_that_name_a_failure_stay_rca_candidates():
    events = BinderWarningParser().analyze(
        [
            "03-30 22:58:17.454  1000  2837  2853 I am_kill : [0,4529,com.android.phone,-800,Too many Binders sent to SYSTEM,0]",
            "03-30 22:58:20.000  1000  2837  2853 I am_kill : [0,4600,com.example.app,0,bg anr,0]",
        ]
    )

    assert [event["rca_candidate"] for event in events] == [True, True]
    assert events[0]["kill_reason"].startswith("Too many Binders sent to SYSTEM")


def test_kill_reason_we_do_not_recognise_stays_undecided():
    event = _events_by_type(
        ["03-30 22:59:35.098  1000  2837  2853 I am_kill : [0,7788,com.example.app,0,Cant deliver broadcast,0]"]
    )["SYSTEM_KILL"]

    assert event["evidence_role"] == "event"
    assert event["rca_candidate"] is False


def test_lone_transaction_too_large_is_a_local_symptom():
    event = _events_by_type(
        [
            "03-30 22:59:42.103  1000  2837  2853 E BroadcastQueue: Failure sending broadcast Intent { act=android.net.action.RECOMMEND_NETWORKS }"
            " android.os.TransactionTooLargeException: data parcel size 1049112 bytes"
        ]
    )["BINDER_BUFFER_ERROR"]

    assert event["rca_candidate"] is False
    assert event["evidence_role"] == "secondary_symptom"
    assert "단독 Root Cause 로 단정하지 않습니다" in event["desc"]


def test_transaction_too_large_is_promoted_when_a_kill_lands_beside_it():
    events = BinderWarningParser().analyze(
        [
            "03-30 22:58:17.454  1000  2837  2853 I am_kill : [0,4529,com.android.phone,-800,Too many Binders sent to SYSTEM,0]",
            "03-30 22:58:30.000  1000  2837  2853 E BroadcastQueue: android.os.TransactionTooLargeException: data parcel size 1049112 bytes",
            "03-30 23:40:00.000  1000  2837  2853 E BroadcastQueue: android.os.TransactionTooLargeException: data parcel size 524288 bytes",
        ]
    )
    buffer_errors = [event for event in events if event["type"] == "BINDER_BUFFER_ERROR"]

    assert [event["rca_candidate"] for event in buffer_errors] == [True, False]
    assert "원인 후보로 올립니다" in buffer_errors[0]["desc"]


def test_a_benign_kill_alone_does_not_promote_transaction_too_large():
    events = BinderWarningParser().analyze(
        [
            "08-22 15:24:10.921  1000  2837  5011 I am_kill : [0,13938,com.android.chrome:sandboxed_process0,0,isolated not needed,0]",
            "08-22 15:24:12.000  1000  2837  2853 E BroadcastQueue: android.os.TransactionTooLargeException: data parcel size 1049112 bytes",
        ]
    )
    buffer_error = [event for event in events if event["type"] == "BINDER_BUFFER_ERROR"][0]

    assert buffer_error["rca_candidate"] is False


def test_real_buffer_exhaustion_stays_an_rca_candidate():
    event = _events_by_type(
        ["[ 1245.411590] binder: 2910:2910 transaction failed 29189/-28, size 76-0 line 3269 No space left on device"]
    )["BINDER_BUFFER_ERROR"]

    assert event["rca_candidate"] is True
    assert event["evidence_role"] == "rca_candidate"


def test_null_binder_guard_log_is_never_a_buffer_root_cause():
    events = BinderWarningParser().analyze(
        [
            "03-30 22:59:42.103 1041 1308 1308 E libbinder.IPCThreadState: binder thread pool (1 threads) starved for 2512 ms",
            "03-30 22:59:13.752 10266  4946 10300 I NullBinder: NullBinder for android.net.action.RECOMMEND_NETWORKS triggering remote TransactionTooLargeException",
        ]
    )
    buffer_error = [event for event in events if event["type"] == "BINDER_BUFFER_ERROR"][0]

    assert buffer_error["rca_candidate"] is False
    assert buffer_error["evidence_role"] == "secondary_symptom"
    assert "parcel 크기나 buffer 고갈과 무관" in buffer_error["desc"]


def test_native_proxy_limit_warning_becomes_its_own_event():
    event = _events_by_type(
        [
            "06-01 15:13:16.733  3179  6884 E libbinder.BpBinder: Too many binder proxy objects"
            " sent to uid 1000 from uid 1001 (6000 proxies held)"
        ]
    )["BINDER_PROXY_LIMIT"]

    assert event["rca_candidate"] is True
    assert (event["from_uid"], event["to_uid"]) == ("1001", "1000")
    assert event["max_count"] == 6000


def test_the_wtf_that_names_a_proxy_leak_is_not_a_plain_wtf():
    events = BinderWarningParser().analyze(
        [
            "06-01 15:13:16.733  3179  3387 I am_wtf  : [0,3179,system_server,-1,ActivityManager,"
            "Uid 1001 [android.uid.phone:1001] sent too many Binders to uid 1000]",
            "06-01 15:13:20.000  3179  3387 I am_wtf  : [0,3179,system_server,-1,ActivityManager,something else]",
        ]
    )

    assert [event["type"] for event in events] == ["BINDER_PROXY_LIMIT", "SYSTEM_WTF"]
    assert events[0]["process"] == "android.uid.phone:1001"
    assert events[0]["rca_candidate"] is True
