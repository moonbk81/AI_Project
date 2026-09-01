from parsers.system_property_parser import SystemPropertyParser


def test_interleaved_section_headers_do_not_end_the_property_block():
    """dumpstate runs commands concurrently, so other sections' "------" lines
    land inside the SYSTEM PROPERTIES body. Ending on the first one read zero
    properties out of real dumps.
    """
    lines = [
        "------ SYSTEM PROPERTIES (getprop) ------",
        "*** command '/system/xbin/su root mount -t debugfs debugfs /sys/kernel/debug' failed: exit code 1",
        "------ 0.019s was the duration of 'mount debugfs' ------",
        "------ chmod debugfs (/system/xbin/su root chmod 0755 /sys/kernel/debug) ------",
        "[gsm.sim.operator.alpha]: [AT&T,]",
        "[gsm.sim.operator.numeric]: [310280,]",
        "------ 0.014s was the duration of 'chmod debugfs' ------",
        "[gsm.sim.state]: [LOADED,NOT_READY]",
        "[gsm.operator.numeric]: [,]",
        "------ 0.050s was the duration of 'SYSTEM PROPERTIES' ------",
    ]

    props = SystemPropertyParser().analyze(lines)

    assert props == {
        "gsm.sim.operator.alpha": "AT&T,",
        "gsm.sim.operator.numeric": "310280,",
        "gsm.sim.state": "LOADED,NOT_READY",
        "gsm.operator.numeric": ",",
    }


def test_properties_after_the_section_are_ignored():
    lines = [
        "------ SYSTEM PROPERTIES (getprop) ------",
        "[gsm.sim.state]: [LOADED]",
        "------ 0.050s was the duration of 'SYSTEM PROPERTIES' ------",
        "------ SYSTEM LOG (logcat) ------",
        "[gsm.sim.state]: [ABSENT]",
    ]

    assert SystemPropertyParser().analyze(lines) == {"gsm.sim.state": "LOADED"}


def test_only_radio_prefixes_are_collected():
    lines = [
        "------ SYSTEM PROPERTIES (getprop) ------",
        "[aaudio.mmap_policy]: [2]",
        "[gsm.sim.state]: [LOADED]",
        "[ril.product_code]: [SM-L716UZKATMB]",
        "[persist.radio.multisim.config]: [ss]",
        "------ 0.050s was the duration of 'SYSTEM PROPERTIES' ------",
    ]

    assert SystemPropertyParser().analyze(lines) == {
        "gsm.sim.state": "LOADED",
        "ril.product_code": "SM-L716UZKATMB",
        "persist.radio.multisim.config": "ss",
    }


def test_no_property_section_yields_nothing():
    assert SystemPropertyParser().analyze(["[gsm.sim.state]: [LOADED]"]) == {}


def test_mobile_data_setting_is_picked_out_of_the_settings_dump():
    """설정값은 getprop 이 아니라 SettingsHelper 섹션에, key = value 로 찍힌다.

    `mobile_data_question` 처럼 앞부분을 공유하는 이웃이 실제 덤프에 있어서,
    키를 정확히 끊지 않으면 엉뚱한 값을 읽는다.
    """
    lines = [
        "    SettingsHelper state:",
        "        protect_battery = 3",
        "        mobile_data_question = 1",
        "        reduce_screen_running_info = null",
        "",  # 구간 안의 빈 줄에서 끊기면 뒤가 안 읽힌다
        "        mobile_data = 0",
    ]

    assert SystemPropertyParser().analyze(lines) == {"mobile_data": "0"}


def test_settings_outside_the_dump_are_ignored():
    lines = [
        "    SettingsHelper state:",
        "        mobile_data = 1",
        "------ SYSTEM LOG (logcat) ------",
        "        mobile_data = 0",
    ]

    assert SystemPropertyParser().analyze(lines) == {"mobile_data": "1"}


def test_the_settings_dump_and_the_property_dump_are_read_independently():
    """dumpstate 는 섹션을 뒤섞는다. 둘을 한 플래그로 묶으면 서로를 끊는다."""
    lines = [
        "    SettingsHelper state:",
        "        mobile_data = 1",
        "------ SYSTEM PROPERTIES (getprop) ------",
        "[gsm.sim.state]: [LOADED]",
        "------ 0.050s was the duration of 'SYSTEM PROPERTIES' ------",
    ]

    assert SystemPropertyParser().analyze(lines) == {
        "mobile_data": "1",
        "gsm.sim.state": "LOADED",
    }
