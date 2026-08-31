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
