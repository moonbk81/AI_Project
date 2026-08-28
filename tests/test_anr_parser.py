from parsers.diagnostic_parser import AnrParser


def test_activity_manager_anr_exposes_context_analysis_and_trace_details():
    lines = [
        "03-25 00:05:58.700  1000  1  2 D ActivityManager: pre line",
        "03-25 00:05:58.802  1000  1  2 E ActivityManager: ANR in com.example.app",
        "03-25 00:05:58.802  1000  1  2 E ActivityManager: PID: 1234",
        "03-25 00:05:58.802  1000  1  2 E ActivityManager: Reason: Broadcast of Intent { act=com.example.ACTION flg=0x10 }",
        "03-25 00:05:58.803  1000  1  2 E ActivityManager: Load: 1.0 / 0.5 / 0.2",
        "------ VM TRACES AT LAST ANR (/data/anr/anr_1234) ------",
        "----- pid 1234 at 2026-03-25 00:05:58 -----",
        "Cmd line: com.example.app",
        '"main" prio=5 tid=1 Blocked',
        "  at com.example.Main.wait(Main.java:10)",
        "  - waiting to lock <0xabc> held by thread 7",
        '"worker" prio=5 tid=7 Runnable',
        "  at com.example.Worker.run(Worker.java:20)",
        "03-25 00:05:59.100  1000  1  2 D ActivityManager: Completed ANR of com.example.app in 1200ms",
    ]

    anrs = AnrParser().analyze(lines)

    assert len(anrs) == 1
    anr = anrs[0]
    assert anr["process"] == "com.example.app"
    assert anr["process_info"]["pid"] == "1234"
    assert anr["intent_action"] == "com.example.ACTION"
    assert anr["main"]["tid"] == "1"
    assert anr["lock_chain"]["blocker_thread"] == "7"
    assert anr["context_analysis"]["cpu_logs"]
    assert anr["raw_context_analysis"] is anr["context_analysis"]


def test_am_anr_eventlog_is_enough_to_create_event_only_anr():
    lines = [
        "03-25 00:05:57.741  1000  3146  9642 I am_anr  : [0,4529,com.android.phone,952647245,Broadcast of Intent { act=com.samsung.intent.action.IMEI_STATE_CHANGED flg=0x10000010 }]",
    ]

    anrs = AnrParser().analyze(lines)

    assert len(anrs) == 1
    assert anrs[0]["process"] == "com.android.phone"
    assert anrs[0]["process_info"]["pid"] == "4529"
    assert anrs[0]["intent_action"] == "com.samsung.intent.action.IMEI_STATE_CHANGED"
    assert anrs[0]["analysis_summary"]["evidence_level"] == "EVENT_ONLY"


def test_eventlog_and_activity_manager_records_for_same_anr_are_merged():
    lines = [
        "03-25 00:05:57.741  1000  3146  9642 I am_anr  : [0,4529,com.android.phone,952647245,Broadcast of Intent { act=com.samsung.intent.action.IMEI_STATE_CHANGED flg=0x10000010 }]",
        "03-25 00:05:58.802  1000  3146  9642 E ActivityManager: ANR in com.android.phone",
        "03-25 00:05:58.802  1000  3146  9642 E ActivityManager: PID: 4529",
        "03-25 00:05:58.802  1000  3146  9642 E ActivityManager: Reason: Broadcast of Intent { act=com.samsung.intent.action.IMEI_STATE_CHANGED flg=0x10000010 }",
        "03-25 00:05:58.803  1000  3146  9642 E ActivityManager: Load: 11.4 / 2.79 / 0.93",
        "03-25 00:05:58.814  1000  3146  9642 D ActivityManager: Completed ANR of com.android.phone in 5210ms",
    ]

    anrs = AnrParser().analyze(lines)

    assert len(anrs) == 1
    assert anrs[0]["time"] == "03-25 00:05:57.741"
    assert anrs[0]["process_info"]["pid"] == "4529"
    assert anrs[0]["context_analysis"]["cpu_logs"] == [
        "03-25 00:05:58.803  1000  3146  9642 E ActivityManager: Load: 11.4 / 2.79 / 0.93"
    ]


def test_application_not_responding_extracts_package_from_window_message():
    lines = [
        "03-25 01:02:03.004  1000  10  11 W InputDispatcher: Application is not responding: Window{123 u0 com.example.app/.Main}. Waited 5000ms",
    ]

    anrs = AnrParser().analyze(lines)

    assert len(anrs) == 1
    assert anrs[0]["process"] == "com.example.app"


def test_anr_context_hints_are_limited_to_nearby_timestamps():
    lines = [
        "03-25 00:00:00.000  1000  1  2 E ActivityManager: ANR in com.example.app",
        "03-25 00:00:00.001  1000  1  2 E ActivityManager: PID: 1234",
        "03-25 00:00:01.000  1000  1  2 E ActivityManager: Load: 3.0 / 1.0 / 0.5",
        "03-25 00:01:00.000  1000  1  2 E ActivityManager: Load: 99.0 / 99.0 / 99.0",
    ]

    anr = AnrParser().analyze(lines)[0]

    assert anr["context_analysis"]["cpu_logs"] == [
        "03-25 00:00:01.000  1000  1  2 E ActivityManager: Load: 3.0 / 1.0 / 0.5"
    ]
