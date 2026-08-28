from parsers.native_crash_parser import NativeCrashParser


def test_native_crash_parser_exposes_time_and_timestamp_aliases():
    lines = [
        "04-28 08:23:59.610 radio 2235 2290 F libc : Fatal signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0 in tid 2290 (ESAR2), pid 2235 (rild)",
        "04-28 08:23:59.700 radio 9999 9999 F DEBUG : backtrace:",
        "04-28 08:23:59.701 radio 9999 9999 F DEBUG :       #00 pc 0000000000284e88  /vendor/lib64/libsec-ril.so (DataCallManager::NotifyDataCallState(Dca*, DataCall*, int, int)+1192)",
    ]

    crashes = NativeCrashParser().analyze(lines)

    assert len(crashes) == 1
    assert crashes[0]["time"] == "04-28 08:23:59.610"
    assert crashes[0]["timestamp"] == "04-28 08:23:59.610"
    assert crashes[0]["process"] == "rild"
    assert crashes[0]["signal"] == "SIGSEGV"
