import pandas as pd

from core.charts import THERMAL_WARNING_C, build_power_thermal_panel


def _thermal(sensor, temperature):
    return {"log_type": "Thermal_Stat", "sensor": sensor, "temperature": temperature}


def test_hottest_sensors_come_first_and_unreadable_ones_drop_out():
    df = pd.DataFrame(
        [
            _thermal("BATTERY", "38.5"),  # Chroma hands numbers back as strings
            _thermal("BROKEN", "n/a"),
            _thermal("AP", 45.0),
        ]
    )

    panel = build_power_thermal_panel(df)

    assert panel.thermals.status == "ok"
    assert panel.thermals.frame["sensor"].tolist() == ["AP", "BATTERY"]
    assert panel.thermals.frame["temperature"].tolist() == [45.0, 38.5]
    assert panel.thermal_warning_c == THERMAL_WARNING_C


def test_each_panel_shows_at_most_ten_rows():
    df = pd.DataFrame(
        [{"log_type": "Wakelock_Stat", "app_name": f"com.app{i:02d}", "times": i} for i in range(14)]
    )

    panel = build_power_thermal_panel(df)

    assert len(panel.wakelocks.frame) == 10
    # The parser already ordered them, so the first ten survive as they are.
    assert panel.wakelocks.frame["app_name"].tolist()[0] == "com.app00"


def test_long_process_names_are_shortened_for_the_axis():
    df = pd.DataFrame(
        [
            {"log_type": "Cpu_Usage_Stat", "process": "com.samsung.android.something.long", "cpu_percent": "30"},
            {"log_type": "Cpu_Usage_Stat", "process": "kswapd0", "cpu_percent": 10},
        ]
    )

    panel = build_power_thermal_panel(df)

    assert panel.cpu.frame["process_label"].tolist() == ["com.samsung.androi...", "kswapd0"]
    assert panel.cpu.frame["cpu_percent"].tolist() == [30.0, 10.0]
    # The full name stays available for the hover box.
    assert panel.cpu.frame["process"].tolist()[1] == "kswapd0"


def test_panels_report_missing_data_independently():
    df = pd.DataFrame([_thermal("AP", 40.0)])

    panel = build_power_thermal_panel(df)

    assert panel.thermals.status == "ok"
    assert panel.wakelocks.status == "no_data"
    assert panel.cpu.status == "no_data"


def test_session_without_metadata():
    panel = build_power_thermal_panel(pd.DataFrame())
    assert (panel.wakelocks.status, panel.thermals.status, panel.cpu.status) == ("no_data",) * 3
