from parsers.battery_thermal_analyzer import BatteryThermalAnalyzer


def _thermal_by_sensor(lines):
    parsed = BatteryThermalAnalyzer().analyze(lines)
    return {item["sensor"]: item["temperature"] for item in parsed["thermal_stats"]}


def test_parses_existing_android_temperature_formats():
    thermals = _thermal_by_sensor(
        [
            "Temperature{mValue=38.5, mName=skin}",
            "Temperature: 42 Sensor: battery",
        ]
    )

    assert thermals["skin"] == 38.5
    assert thermals["battery"] == 42.0


def test_parses_samsung_dumpstate_temperature_formats():
    thermals = _thermal_by_sensor(
        [
            "03-03 09:50:45.286  1000  6757  6757 I cs40l26 54-0040: current_temp_store temperature : 26",
            "03-03 20:45:56.542  root 31979 31979 D max77775_fg_write_temp: temperature to (202, 0x1433)",
            "03-03 20:45:56.725  1000  2795  3913 D BatteryService: Sending ACTION_BATTERY_CHANGED: level:97, temperature:202",
            "03-03 20:45:56.566  1000  1917  1917 I sec-battery samsung_mobile_device: battery: sec_bat_store_attrs: LRP(242)",
            "03-03 20:46:11.565  root 22013 22013 I synaptics_ts spi1.0: [sec_input] sec_input_set_temperature_data set temperature:20",
        ]
    )

    assert thermals["cs40l26"] == 26.0
    assert thermals["max77775_fg"] == 20.2
    assert thermals["battery"] == 20.2
    assert thermals["battery_lrp"] == 24.2
    assert thermals["sec_input"] == 20.0


def test_keeps_highest_temperature_per_sensor():
    thermals = _thermal_by_sensor(
        [
            "03-03 09:50:45.286  1000  6757  6757 I cs40l26 54-0040: current_temp_store temperature : 17",
            "03-03 09:51:45.535  1000  6757  6757 I cs40l26 54-0040: current_temp_store temperature : 26",
            "03-03 20:45:25.341  1000  6757  6757 I cs40l26 54-0040: current_temp_store temperature : 18",
        ]
    )

    assert thermals["cs40l26"] == 26.0
