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


def test_mobile_data_is_read_from_the_settings_dump_with_its_last_change():
    """settings 덤프는 현재 값과 변경 이력을 함께 낸다.

    이름이 앞부분을 공유하는 이웃(`mobile_data_question`)이 같은 모양으로 찍히고,
    한 줄에 `value` 와 `default` 가 함께 있어 -- 서로 다를 수 있다 -- 지금 값은
    `value` 쪽이다.
    """
    lines = [
        "_id:3266 name:mobile_data_question pkg:com.android.phone value:1 default:1",
        "_id:3267 name:mobile_data pkg:com.android.phone value:0 default:1 defaultSystemSet:true",
        "  History (mobile_data_question)",
        "    time:01-01 00:00:00.000 mode:insert oldValue:null newValue:1 package:android",
        "  History (mobile_data)",
        "    time:01-02 12:26:18.872 mode:insert oldValue:null newValue:1 package:android",
        "    time:05-26 14:21:16.840 mode:update oldValue:1 newValue:0 package:com.android.phone",
        "    time:08-23 10:01:51.098 mode:update oldValue:1 newValue:0 package:com.android.phone",
    ]

    assert SystemPropertyParser().analyze(lines) == {
        "mobile_data": "0",
        # 이력의 마지막 것이 지금 값을 만든 변경이다.
        "mobile_data_changed_at": "08-23 10:01:51.098",
        "mobile_data_changed_by": "com.android.phone",
    }


def test_a_setting_without_history_still_reports_its_value():
    lines = ["_id:3267 name:mobile_data pkg:com.android.phone value:1 default:0"]

    assert SystemPropertyParser().analyze(lines) == {"mobile_data": "1"}


def test_the_settings_dump_and_the_property_dump_are_read_independently():
    """dumpstate 는 섹션을 뒤섞는다. 두 모양이 서로를 삼키면 안 된다."""
    lines = [
        "_id:3267 name:mobile_data pkg:com.android.phone value:1 default:0",
        "  History (mobile_data)",
        "    time:08-23 10:01:51.098 mode:update oldValue:0 newValue:1 package:com.android.phone",
        "------ SYSTEM PROPERTIES (getprop) ------",
        "[gsm.sim.state]: [LOADED]",
        "[ro.build.id]: [IGNORED]",
        "------ 0.050s was the duration of 'SYSTEM PROPERTIES' ------",
    ]

    assert SystemPropertyParser().analyze(lines) == {
        "mobile_data": "1",
        "mobile_data_changed_at": "08-23 10:01:51.098",
        "mobile_data_changed_by": "com.android.phone",
        "gsm.sim.state": "LOADED",
    }
