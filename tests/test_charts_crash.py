import json

from core.charts import (
    BINDER_EVENT_DISPLAY_CAP,
    BINDER_PROXY_LEAK_THRESHOLD,
    build_binder_events,
    build_binder_proxy_histograms,
    build_crash_overview,
    build_system_kills,
    build_system_wtf_summary,
    normalize_anr_list,
)


def _warning(warning_type, **overrides):
    warning = {"type": warning_type, "time": "08-25 10:00:00", "process": "system_server", "desc": "d", "raw": "r"}
    warning.update(overrides)
    return warning


# ----------------------------------------------------------- system kill / wtf


def test_kill_table_falls_back_through_the_fields_the_parser_may_use():
    kills = build_system_kills(
        [
            _warning("SYSTEM_KILL", desc="oom", raw="raw line"),
            {"type": "SYSTEM_KILL", "top_method": "onCreate", "trigger": "trigger line"},
            _warning("BINDER_DELAY"),
        ]
    )

    assert list(kills.columns) == ["발생 시간", "대상 프로세스", "종료 사유", "원본 로그"]
    assert kills["종료 사유"].tolist() == ["oom", "onCreate"]
    assert kills["원본 로그"].tolist() == ["raw line", "trigger line"]
    assert kills["대상 프로세스"].tolist()[1] == "Unknown"


def test_wtf_events_are_grouped_per_process_with_a_first_and_last_time():
    summary = build_system_wtf_summary(
        [
            _warning("SYSTEM_WTF", process="p1", time="08-25 10:00:00"),
            _warning("SYSTEM_WTF", process="p1", time="08-25 10:05:00"),
            _warning("SYSTEM_WTF", process="p2", time="Unknown"),
        ]
    )

    assert summary.total == 3
    rows = summary.by_process.set_index("대상 프로세스").to_dict("index")
    assert rows["p1"]["발생 횟수"] == "2회"
    assert (rows["p1"]["최초 발생"], rows["p1"]["최근 발생"]) == ("08-25 10:00:00", "08-25 10:05:00")
    # An unknown timestamp must not overwrite a known one.
    assert rows["p2"]["최근 발생"] == "Unknown"
    assert len(summary.recent) == 3


def test_no_wtf_events():
    assert build_system_wtf_summary([]).total == 0
    assert build_system_wtf_summary([_warning("BINDER_DELAY")]).total == 0


# --------------------------------------------------------------- binder events


def test_binder_events_split_spam_from_the_general_table():
    binder = build_binder_events(
        {
            "binder_warnings": [
                _warning("BINDER_ONEWAY_SPAM", desc="com.a floods oneway"),
                _warning("BINDER_DELAY"),
                _warning("SYSTEM_KILL"),  # has its own section
            ]
        }
    )

    assert binder.status == "ok"
    assert [spam.desc for spam in binder.spam] == ["com.a floods oneway"]
    # Spam stays in the table too, so the full history is visible in one place.
    assert binder.event_count == 2
    assert binder.events["type"].tolist() == ["BINDER_ONEWAY_SPAM", "BINDER_DELAY"]
    assert binder.truncated is False


def test_long_binder_histories_are_capped_at_the_most_recent_rows():
    warnings = [_warning("BINDER_DELAY", desc=f"d{i}") for i in range(BINDER_EVENT_DISPLAY_CAP + 5)]

    binder = build_binder_events({"binder_warnings": warnings})

    assert binder.event_count == BINDER_EVENT_DISPLAY_CAP + 5
    assert len(binder.events) == BINDER_EVENT_DISPLAY_CAP
    assert binder.truncated is True
    assert binder.events["desc"].tolist()[-1] == f"d{BINDER_EVENT_DISPLAY_CAP + 4}"


def test_binder_context_summary_becomes_a_table_and_a_checklist():
    binder = build_binder_events(
        {
            "binder_warnings": [_warning("BINDER_DELAY")],
            "binder_context_summary": {"signals": {"oneway": 12}, "checklist": ["버퍼 사용량 확인"]},
        }
    )

    assert binder.signals.to_dict("records") == [{"구분": "oneway", "매칭 라인 수": 12}]
    assert binder.checklist == ["버퍼 사용량 확인"]


def test_report_without_binder_warnings_skips_the_section():
    assert build_binder_events({}).status == "none"
    assert build_binder_events(None).status == "none"


# -------------------------------------------------------- binder proxy leakage


_HISTOGRAM_RAW = "android.os.IBinder x 1500\ncom.foo.Bar$Stub x 12\nnot a histogram line\n"


def test_proxy_histogram_is_parsed_and_ordered_for_a_horizontal_chart():
    histogram = build_binder_proxy_histograms(
        [
            _warning("SYSTEM_WTF", process="com.android.phone"),
            _warning("SYSTEM_KILL", desc="Too many Binders sent to SYSTEM", raw="am_kill"),
            _warning("BINDER_PROXY_HISTOGRAM", max_count=1500, raw=_HISTOGRAM_RAW),
        ]
    )[0]

    assert histogram.counts["Count"].tolist() == [12, 1500]  # ascending: biggest bar on top
    assert histogram.counts["Class"].tolist() == ["Bar$Stub", "IBinder"]
    assert histogram.counts["FullClass"].tolist()[1] == "android.os.IBinder"
    assert histogram.top_descriptor == "android.os.IBinder"
    assert histogram.top_count == 1500
    assert histogram.threshold_ratio == 1.5
    assert histogram.related_too_many_binders_kill_count == 1
    assert histogram.related_wtf_count == 1
    assert histogram.related_wtf_processes == ["com.android.phone"]


def test_leak_threshold_is_exclusive():
    at_threshold = build_binder_proxy_histograms(
        [_warning("BINDER_PROXY_HISTOGRAM", max_count=BINDER_PROXY_LEAK_THRESHOLD, raw="")]
    )[0]
    over = build_binder_proxy_histograms(
        [_warning("BINDER_PROXY_HISTOGRAM", max_count=BINDER_PROXY_LEAK_THRESHOLD + 1, raw="")]
    )[0]

    assert at_threshold.is_leak is False
    assert over.is_leak is True


def test_proxy_warnings_may_arrive_as_json_text():
    payload = json.dumps([_warning("BINDER_PROXY_LEAK", max_count=2000, raw=_HISTOGRAM_RAW)])

    assert len(build_binder_proxy_histograms(payload)) == 1
    assert build_binder_proxy_histograms("not json") == []
    assert build_binder_proxy_histograms([_warning("BINDER_DELAY")]) == []


# ------------------------------------------------------------------------ ANR


def test_a_single_anr_may_arrive_as_a_bare_dict():
    assert normalize_anr_list({"time": "t"}) == [{"time": "t"}]
    assert normalize_anr_list({}) == []
    assert normalize_anr_list(None) == []
    assert normalize_anr_list([{"time": "t"}]) == [{"time": "t"}]


def test_anr_log_dumps_are_truncated_to_their_tails():
    overview = build_crash_overview(
        {
            "anr_context": {
                "time": "08-25 10:00:00",
                "process": "com.a",
                "reason": "Input dispatching timed out",
                "process_info": {"pid": 4321},
                "pre_anr_logcat": [f"l{i}" for i in range(200)],
                "context_analysis": {"cpu_logs": [f"c{i}" for i in range(100)], "io_logs": []},
                "main": {"stack": ["at a.b()"]},
            }
        }
    )

    anr = overview.anr_events[0]
    assert (anr.process, anr.pid) == ("com.a", 4321)
    assert len(anr.pre_logcat) == 120 and anr.pre_logcat[-1] == "l199"
    assert len(anr.cpu_logs) == 80 and anr.cpu_logs[0] == "c20"
    assert anr.has_context_logs is True
    assert anr.main_stack == ["at a.b()"]


def test_lock_chain_needs_a_blocking_thread():
    without = build_crash_overview({"anr_context": [{"lock_chain": {"lock_address": "0x1"}}]})
    assert without.anr_events[0].lock_chain is None

    with_blocker = build_crash_overview(
        {"anr_context": [{"lock_chain": {"lock_address": "0x1", "blocker_thread": 12, "blocker_stack": ["at c()"]}}]}
    )
    chain = with_blocker.anr_events[0].lock_chain
    assert (chain.lock_address, chain.blocker_thread, chain.blocker_stack) == ("0x1", 12, ["at c()"])


def test_pending_binder_transactions_dash_out_missing_ids():
    overview = build_crash_overview(
        {"anr_context": [{"active_binder_transactions": [{"from_pid": 1, "raw": "outgoing transaction"}]}]}
    )

    row = overview.anr_events[0].binder_transactions.iloc[0]
    assert row["from_pid"] == 1
    assert row["to_pid"] == "-"
    assert row["raw"] == "outgoing transaction"


def test_anr_summary_is_absent_when_the_parser_ran_no_checks():
    overview = build_crash_overview({"anr_context": [{"analysis_summary": {}}]})
    assert overview.anr_events[0].summary is None

    checked = build_crash_overview({"anr_context": [{"analysis_summary": {"has_main_stack": True}}]})
    summary = checked.anr_events[0].summary
    assert summary.has_main_stack is True and summary.has_io_hint is False


def test_anr_event_includes_rule_based_triage():
    overview = build_crash_overview(
        {
            "anr_context": [
                {
                    "time": "03-25 00:05:58.802",
                    "process": "com.android.phone",
                    "reason": "Broadcast of Intent { act=com.example.ACTION }",
                    "intent_action": "com.example.ACTION",
                    "process_info": {"pid": 4529},
                    "analysis_summary": {"has_main_stack": True, "has_lock_contention": True},
                    "main": {
                        "stack": [
                            '"main" tid=1 Blocked',
                            "at com.android.providers.telephony.TelephonyProvider.insertSynchronized(TelephonyProvider.java:7145)",
                        ]
                    },
                    "lock_chain": {
                        "lock_address": "0x1",
                        "blocker_thread": 83,
                        "blocker_stack": [
                            '"owner" tid=83 Runnable',
                            "at com.android.providers.telephony.TelephonyProvider.updateApnDb(TelephonyProvider.java:8689)",
                        ],
                    },
                    "context_analysis": {
                        "cpu_logs": ["03-25 00:05:58.802 ActivityManager: Load: 11.4 / 2.79 / 0.93"],
                        "io_logs": ["03-25 00:05:58.802 ActivityManager: 82% TOTAL: 0.6% iowait"],
                    },
                }
            ]
        }
    )

    triage = overview.anr_events[0].triage
    assert triage["primary_signal"] == "Lock contention"
    assert triage["facts"][3] == {"label": "Intent action", "value": "com.example.ACTION"}
    assert triage["main_thread"]["check_target"] == "TelephonyProvider.insertSynchronized"
    assert triage["lock_owner"]["check_target"] == "TelephonyProvider.updateApnDb"
    assert triage["signals"][0]["strength"] == "강함"
    assert triage["signals"][1]["strength"] == "근거 약함"
    assert "TID 83" in triage["next_check"]


# --------------------------------------------------------------------- crashes


def test_kernel_panics_get_their_own_title():
    overview = build_crash_overview(
        {"crash_context": [{"is_kernel": True, "crash_type": "FATAL EXCEPTION", "process": "cp"}]}
    )

    assert overview.java_crashes[0].crash_type == "KERNEL PANIC / MODEM CRASH"
    assert overview.java_crashes[0].is_kernel is True


def test_unknown_top_method_is_not_worth_showing():
    overview = build_crash_overview(
        {"crash_context": [{"top_method": "Unknown"}, {"top_method": "MainActivity.onCreate"}]}
    )

    assert overview.java_crashes[0].top_method is None
    assert overview.java_crashes[1].top_method == "MainActivity.onCreate"


def test_oversized_intents_are_flagged_from_the_raw_logs():
    overview = build_crash_overview(
        {
            "crash_context": [
                {"cross_context_logs": ["android.os.TransactionTooLargeException: data parcel size"]},
                {"trigger": "java.lang.NullPointerException"},
            ]
        }
    )

    assert overview.java_crashes[0].suspects_transaction_too_large is True
    assert overview.java_crashes[1].suspects_transaction_too_large is False


def test_java_crash_event_includes_rule_based_triage():
    overview = build_crash_overview(
        {
            "crash_context": [
                {
                    "timestamp": "12-29 08:42:12.830",
                    "process": "system_server",
                    "crash_type": "FATAL EXCEPTION",
                    "exception_info": "java.lang.ArrayIndexOutOfBoundsException: length=0; index=0",
                    "top_method": "CoverAuthenticator$CoverAuthHandler.handleMessage",
                    "call_stack": [
                        "at com.samsung.accessory.manager.authentication.cover.CoverAuthenticator$CoverAuthHandler.handleMessage(qb/104131572:108)",
                    ],
                }
            ]
        }
    )

    triage = overview.java_crashes[0].triage
    assert triage["primary_signal"] == "Java exception"
    assert triage["facts"][2] == {"label": "Exception", "value": "java.lang.ArrayIndexOutOfBoundsException"}
    assert triage["check_target"] == "CoverAuthenticator$CoverAuthHandler.handleMessage"
    assert triage["signals"][0]["strength"] == "강함"
    assert "입력값/상태값" in triage["next_check"]


def test_dead_system_exception_is_labeled_as_follow_up_signal():
    overview = build_crash_overview(
        {
            "crash_context": [
                {
                    "process": "com.android.phone",
                    "exception_info": "android.os.DeadSystemException: The system died; earlier logs will point to the root cause",
                    "cross_context_logs": ["DeadSystemException: The system died; earlier logs will point to the root cause"],
                }
            ]
        }
    )

    triage = overview.java_crashes[0].triage
    assert triage["primary_signal"] == "System died follow-up"
    assert triage["signals"][2]["strength"] == "강함"
    assert "system_server FATAL" in triage["next_check"]


def test_transaction_too_large_gets_binder_triage_signal():
    overview = build_crash_overview(
        {
            "crash_context": [
                {
                    "process": "com.example.app",
                    "exception_info": "android.os.TransactionTooLargeException: data parcel size 1048576 bytes",
                    "cross_context_logs": ["android.os.TransactionTooLargeException: data parcel size"],
                }
            ]
        }
    )

    triage = overview.java_crashes[0].triage
    assert triage["primary_signal"] == "Oversized Binder payload"
    assert triage["signals"][1]["strength"] == "강함"


def test_system_events_do_not_repeat_as_app_crashes():
    overview = build_crash_overview(
        {
            "crash_context": [
                {"type": "SYSTEM_KILL", "process": "p"},
                {"type": "FATAL EXCEPTION", "process": "com.a"},
            ]
        }
    )

    assert [crash.process for crash in overview.java_crashes] == ["com.a"]


def test_native_crash_callstack_becomes_a_frame():
    overview = build_crash_overview(
        {
            "native_crash_context": [
                {"timestamp": "08-25 10:00:00", "process": "cp", "signal": "SIGSEGV", "callstack": [{"frame": 0, "pc": "0x1"}]}
            ]
        }
    )

    crash = overview.native_crashes[0]
    assert (crash.process, crash.signal, crash.abort_message) == ("cp", "SIGSEGV", "none")
    assert crash.callstack["pc"].tolist() == ["0x1"]


def test_native_crash_time_falls_back_to_parser_time_key():
    overview = build_crash_overview(
        {
            "native_crash_context": [
                {
                    "time": "04-28 08:23:59.610",
                    "process": "rild",
                    "signal": "SIGSEGV",
                    "callstack": [],
                }
            ]
        }
    )

    assert overview.native_crashes[0].time == "04-28 08:23:59.610"


def test_native_crash_event_includes_rule_based_triage():
    overview = build_crash_overview(
        {
            "native_crash_context": [
                {
                    "time": "04-28 08:23:59.610",
                    "process": "rild",
                    "signal": "SIGSEGV",
                    "abort_message": "none",
                    "callstack": [
                        {
                            "frame_level": "00",
                            "library": "libsec-ril.so",
                            "function": "DataCallManager::NotifyDataCallState(Dca*, DataCall*, int, int)",
                        }
                    ],
                }
            ]
        }
    )

    triage = overview.native_crashes[0].triage
    assert triage["primary_signal"] == "Native memory fault"
    assert triage["facts"][3] == {"label": "Top library", "value": "libsec-ril.so"}
    assert triage["top_frame"]["function"].startswith("DataCallManager::NotifyDataCallState")
    assert triage["signals"][0]["strength"] == "강함"
    assert triage["signals"][1]["strength"] == "근거 약함"
    assert "libsec-ril.so" in triage["next_check"]


def test_a_session_with_nothing_wrong():
    assert build_crash_overview({}).status == "clean"
    assert build_crash_overview(None).status == "clean"
    assert build_crash_overview({"crash_context": [], "binder_warnings": []}).status == "clean"
    assert build_crash_overview({"binder_warnings": [_warning("BINDER_DELAY")]}).status == "ok"
