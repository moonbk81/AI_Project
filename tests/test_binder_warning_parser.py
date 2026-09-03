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


# ------------------------------------------------------------- 프로세스 사망

# 한 번의 죽음이 네 군데에 나눠 적힌다. 실제 dumpstate 에서 가져온 순서 그대로.
DEATH_LINES = [
    "08-27 19:39:21.205  root  1085  1085 I Zygote  : Process 10647 exited due to signal 6 (Aborted); core dumped",
    "08-27 19:39:21.213  1000  1350  2059 I ActivityManager: Process com.android.phone (pid 10647) has died: pers PER (535,3264)",
    "08-27 19:39:21.216  1000  1350  2059 I Watchdog: Interesting Java process com.android.phone died. Pid 10647",
    "08-27 19:39:21.154  1000   652   652 I servicemanager: 'telephony_phone_number' died",
    "08-27 19:39:21.160  1000   652   652 I servicemanager: 'phone' died",
    "08-27 19:39:21.215  1000  1350  2059 W ActivityManager: Scheduling restart of crashed service com.android.phone/com.android.services.telephony.TelephonyConnectionService in 0ms for persistent",
]


def _deaths(lines):
    return [w for w in BinderWarningParser().analyze(lines) if w["type"] == "PROCESS_DIED"]


def test_four_scattered_lines_become_one_death():
    """죽음은 Zygote·ActivityManager·Watchdog·servicemanager 에 나눠 적힌다."""
    deaths = _deaths(DEATH_LINES)

    assert len(deaths) == 1
    death = deaths[0]
    assert death["process"] == "com.android.phone"
    assert death["pid"] == "10647"
    assert death["signal"] == "6 (Aborted)"
    assert death["core_dumped"] is True
    assert death["persistent"] is True
    assert death["rca_candidate"] is True


def test_the_death_carries_what_went_down_with_it():
    death = _deaths(DEATH_LINES)[0]

    assert death["lost_services"] == ["phone", "telephony_phone_number"]
    assert death["restarted_services"] == [
        "com.android.phone/com.android.services.telephony.TelephonyConnectionService"
    ]


def test_the_death_points_at_the_tombstone_not_at_itself():
    """시그널로 죽었으면 사인은 tombstone 에 있다. 여기서 단정하면 안 된다."""
    desc = _deaths(DEATH_LINES)[0]["desc"]

    assert "tombstone" in desc
    assert "결과이지 원인이 아닙니다" in desc


def test_a_service_death_far_from_the_process_death_is_not_attributed():
    """servicemanager 줄에는 pid 가 없어 시간으로만 이을 수 있다. 창을 넘으면 남의 것이다."""
    lines = DEATH_LINES + [
        "08-27 19:45:00.000  1000   652   652 I servicemanager: 'unrelated_service' died",
    ]

    assert "unrelated_service" not in _deaths(lines)[0]["lost_services"]


def test_a_restart_for_another_package_is_not_attributed():
    """재시작 줄에는 패키지가 적혀 있어 시간이 아니라 이름으로 잇는다."""
    lines = DEATH_LINES + [
        "08-27 19:39:21.215  1000  1350  2059 W ActivityManager: Scheduling restart of crashed service com.samsung.sec.android.application.csc/.service.CscUpdateService in 10000ms for start-requested",
    ]

    restarted = _deaths(lines)[0]["restarted_services"]

    assert all(component.startswith("com.android.phone/") for component in restarted)


def test_two_processes_dying_stay_two_events():
    lines = DEATH_LINES + [
        "08-27 19:39:22.000  root  1085  1085 I Zygote  : Process 20001 exited due to signal 9 (Killed)",
        "08-27 19:39:22.010  1000  1350  2059 I ActivityManager: Process com.example.other (pid 20001) has died: cch CRE",
    ]

    deaths = _deaths(lines)

    assert {d["process"] for d in deaths} == {"com.android.phone", "com.example.other"}
    other = next(d for d in deaths if d["process"] == "com.example.other")
    assert other["persistent"] is False


def test_binder_failures_around_the_death_stay_secondary():
    """죽음이 원인 후보로 올라와도 그 결과들은 여전히 후속 증상이어야 한다."""
    lines = DEATH_LINES + [
        "08-27 19:39:21.202  1000  1350  1976 E libbinder.IPCThreadState: Binder transaction failure. id: 258690560, cmd: BR_DEAD_REPLY (29189), error: -3 (No such process)",
        "08-27 19:39:21.141  1000  1976  1976 I binder_alloc: 10647: binder_alloc_buf, no vma",
    ]

    warnings = BinderWarningParser().analyze(lines)
    fallout = [w for w in warnings if w["type"] != "PROCESS_DIED"]

    assert fallout
    assert all(w["evidence_role"] == "secondary_symptom" for w in fallout)
    assert not any(w["type"] == "BINDER_PROXY_LIMIT" for w in warnings)


def test_a_service_goes_to_the_nearest_death_not_to_every_one():
    """잇따라 죽으면 시간 창이 겹친다.

    창 안의 모든 죽음에 다 붙이면, 0.4초 뒤 죽은 남의 프로세스가 radio HAL
    스무 개를 똑같이 가져간다. 실제 dumpstate 에서 그렇게 나왔다.
    """
    lines = [
        "08-27 19:39:21.154  1000   652   652 I servicemanager: 'phone' died",
        "08-27 19:39:21.205  root  1085  1085 I Zygote  : Process 10647 exited due to signal 6 (Aborted)",
        "08-27 19:39:21.213  1000  1350  2059 I ActivityManager: Process com.android.phone (pid 10647) has died: pers PER",
        "08-27 19:39:21.560  1000   652   652 I servicemanager: 'usim_manager' died",
        "08-27 19:39:21.565  root  1085  1085 I Zygote  : Process 22161 exited due to signal 9 (Killed)",
        "08-27 19:39:21.570  1000  1350  2059 I ActivityManager: Process com.kt.usim (pid 22161) has died: cch CRE",
    ]

    by_process = {d["process"]: d["lost_services"] for d in _deaths(lines)}

    assert by_process["com.android.phone"] == ["phone"]
    assert by_process["com.kt.usim"] == ["usim_manager"]


def test_no_death_lines_yield_no_death_events():
    assert _deaths(["08-27 19:39:21.202 D ActivityManager: nothing here"]) == []
