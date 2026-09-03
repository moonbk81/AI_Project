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


# ------------------------------------------------------------------ tombstone

# debuggerd 가 남기는 블록. libc 의 "Fatal signal" 한 줄 없이 이것만 남는
# 로그가 있고, 죽은 이유(abort 메시지)와 위치(백트레이스)는 여기에만 있다.
TOMBSTONE = [
    "08-27 19:39:20.735 radio 21831 21831 F DEBUG   : *** *** *** *** *** *** *** *** *** *** *** *** *** *** *** ***",
    "08-27 19:39:20.735 radio 21831 21831 F DEBUG   : Build fingerprint: 'samsung/b8sksx/b8s:17/CP2A.260605.016/F776NKSS2AZH7_OKR2AZH7:userdebug/test-keys'",
    "08-27 19:39:20.735 radio 21831 21831 F DEBUG   : Executable: /system/bin/app_process64",
    "08-27 19:39:20.735 radio 21831 21831 F DEBUG   : Cmdline: com.android.phone",
    "08-27 19:39:20.735 radio 21831 21831 F DEBUG   : pid: 10647, ppid: 1085, tid: 10663, name: ReferenceQueueD  >>> com.android.phone <<<",
    "08-27 19:39:20.735 radio 21831 21831 F DEBUG   : uid: 1001",
    "08-27 19:39:20.735 radio 21831 21831 F DEBUG   : signal 6 (SIGABRT), code -1 (SI_QUEUE), fault addr --------",
    "08-27 19:39:20.735 radio 21831 21831 F DEBUG   : Abort message: 'Scudo ERROR: corrupted chunk header at address 0x2000076c1285050: most likely due to memory corruption'",
    "08-27 19:39:20.735 radio 21831 21831 F DEBUG   : backtrace:",
    "08-27 19:39:20.735 radio 21831 21831 F DEBUG   :       #00 pc 000000000007ba70  /apex/com.android.runtime/lib64/bionic/libc.so (abort+160) (BuildId: 3c40a3f379b085efaaa6d495d43b79ac)",
    "08-27 19:39:20.736 radio 21831 21831 F DEBUG   :       #04 pc 0000000000062408  /apex/com.android.runtime/lib64/bionic/libc.so (scudo::reportHeaderCorruption(void*, void const*)+188) (BuildId: 3c40a3f379b085efaaa6d495d43b79ac)",
    "08-27 19:39:20.736 radio 21831 21831 F DEBUG   :       #08 pc 000000000021f7dc  /apex/com.android.i18n/lib64/libicui18n.so (icu_78::RegexMatcher::~RegexMatcher()+36) (BuildId: 57d55205b24a9169fb269fc5cf043b4e)",
]


def test_a_tombstone_without_the_libc_line_is_still_a_crash():
    """dumpstate 에 담겨 오거나 버퍼가 잘리면 tombstone 만 남는다.

    그때 크래시를 못 잡으면 남는 건 그 프로세스가 죽어서 생긴 binder 실패들뿐이라,
    "후속 증상만 있고 원인은 모름" 으로 끝난다.
    """
    crashes = NativeCrashParser().analyze(TOMBSTONE)

    assert len(crashes) == 1
    crash = crashes[0]
    assert crash["process"] == "com.android.phone"
    assert crash["pid"] == "10647"
    assert crash["thread"] == "ReferenceQueueD"
    assert crash["signal"] == "SIGABRT"
    assert crash["abort_message"].startswith("Scudo ERROR: corrupted chunk header")
    assert crash["time"] == "08-27 19:39:20.735"


def test_the_libc_line_and_its_tombstone_are_one_crash():
    """둘은 같은 사건이다. 따로 세면 크래시가 두 번 난 것처럼 보인다."""
    lines = [
        "08-27 19:39:20.730 radio 10647 10663 F libc : Fatal signal 6 (SIGABRT), code -1 (SI_QUEUE), fault addr -------- in tid 10663 (ReferenceQueueD), pid 10647 (ndroid.phone)",
        *TOMBSTONE,
    ]

    crashes = NativeCrashParser().analyze(lines)

    assert len(crashes) == 1
    # 15자에서 잘린 `ndroid.phone` 이 아니라 tombstone 의 온전한 이름을 쓴다.
    assert crashes[0]["process"] == "com.android.phone"
    assert crashes[0]["abort_message"].startswith("Scudo ERROR")


def test_two_tombstones_are_two_crashes():
    crashes = NativeCrashParser().analyze(TOMBSTONE + TOMBSTONE)

    assert len(crashes) == 2
    assert all(c["process"] == "com.android.phone" for c in crashes)


def test_a_c_plus_plus_frame_keeps_its_whole_signature():
    """C++ 이름 안에도 괄호가 있어서, 첫 닫는 괄호에서 끊으면 이름이 잘린다."""
    crash = NativeCrashParser().analyze(TOMBSTONE)[0]
    functions = [frame["function"] for frame in crash["callstack"]]

    assert "scudo::reportHeaderCorruption(void*, void const*)" in functions
    assert "icu_78::RegexMatcher::~RegexMatcher()" in functions
    # BuildId 는 프레임 정보가 아니다.
    assert not any("BuildId" in name for name in functions)


def test_lines_after_the_backtrace_do_not_overwrite_the_crash():
    """tombstone 이 어디서 끝나는지는 로그가 알려주지 않는다.

    계속 읽으면 한참 뒤 남의 줄이 프로세스 이름을 덮어쓴다. 실제 dumpstate
    250만 줄에서 com.android.phone 이 FeatureStore 로 바뀌어 나왔다.
    """
    lines = TOMBSTONE + [
        "08-27 19:41:00.000  1000  2222  2222 I ActivityManager: Cmdline: FeatureStore",
        "08-27 19:41:00.001  1000  2222  2222 I SomethingElse: pid: 9999, ppid: 1, tid: 8888, name: Other  >>> FeatureStore <<<",
        "08-27 19:41:00.002  1000  2222  2222 I SomethingElse: Abort message: 'unrelated'",
    ]

    crash = NativeCrashParser().analyze(lines)[0]

    assert crash["process"] == "com.android.phone"
    assert crash["pid"] == "10647"
    assert crash["abort_message"].startswith("Scudo ERROR")


def test_a_path_style_process_name_is_reduced_to_the_binary():
    lines = [
        "04-28 08:23:59.700 radio 9999 9999 F DEBUG : *** *** *** *** *** *** ***",
        "04-28 08:23:59.700 radio 9999 9999 F DEBUG : Cmdline: /system/bin/rild",
        "04-28 08:23:59.700 radio 9999 9999 F DEBUG : pid: 2235, ppid: 1, tid: 2290, name: ESAR2  >>> /system/bin/rild <<<",
        "04-28 08:23:59.700 radio 9999 9999 F DEBUG : signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0",
    ]

    crash = NativeCrashParser().analyze(lines)[0]

    # rild 상관관계 검사가 프로세스 이름으로 짝을 맞춘다.
    assert crash["process"] == "rild"
    assert crash["signal"] == "SIGSEGV"


def test_no_crash_lines_yield_nothing():
    assert NativeCrashParser().analyze([]) == []
    assert NativeCrashParser().analyze(["08-27 19:39:21.202 D ActivityManager: nothing here"]) == []
