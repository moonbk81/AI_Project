import pandas as pd

from core.charts import ICON_OFF, ICON_ON_HYSTERESIS, ICON_ON_REAL, build_ntn_overview


def _ntn_event(event_type, time, **overrides):
    event = {
        "time": time,
        "event_type": event_type,
        "ntn_plmn": "45001",
        "data_policy": "RESTRICTED",
        "power_state": "ON",
        "ntn_mode": "OFF",
        "last_ntn_mode": "OFF",
        "is_hysteresis": False,
    }
    event.update(overrides)
    return event


# ------------------------------------------------------------ NTN (SpaceX) FW


def test_repeated_plmn_matches_collapse():
    data = [
        _ntn_event("PLMN_MATCH", "08-25 10:00:00", ntn_plmn="45001"),
        _ntn_event("PLMN_MATCH", "08-25 10:00:01", ntn_plmn="45001"),
        _ntn_event("PLMN_MATCH", "08-25 10:00:02", ntn_plmn="45002"),
    ]

    overview = build_ntn_overview(data, year=2026)

    assert overview.status == "ok"
    assert overview.table["ntn_plmn"].tolist() == ["45001", "45002"]
    assert overview.ntn_status.plmn == "45002"  # the last match wins


def test_mode_notify_counts_only_when_it_changes_something():
    data = [
        # Repeats what the modem last reported, so it tells the UI nothing new.
        _ntn_event("NTN_MODE_NOTIFY", "08-25 10:00:00", ntn_mode="OFF", last_ntn_mode="OFF"),
        _ntn_event("NTN_MODE_NOTIFY", "08-25 10:00:01", ntn_mode="ON", last_ntn_mode="OFF"),
        # Same mode as the previous notify: a re-announcement, not a transition.
        _ntn_event("NTN_MODE_NOTIFY", "08-25 10:00:02", ntn_mode="ON", last_ntn_mode="OFF"),
    ]

    overview = build_ntn_overview(data, year=2026)

    assert overview.table["time"].tolist() == ["08-25 10:00:01"]


def test_radio_power_toggles_collapse_but_do_not_hide_ntn_events():
    data = [
        _ntn_event("RADIO_POWER", "08-25 10:00:00", power_state="ON"),
        _ntn_event("RADIO_POWER", "08-25 10:00:01", power_state="ON"),
        _ntn_event("RADIO_POWER", "08-25 10:00:02", power_state="OFF"),
        _ntn_event("PLMN_MATCH", "08-25 10:00:03"),
    ]

    overview = build_ntn_overview(data, year=2026)

    assert overview.table["event_type"].tolist() == ["RADIO_POWER", "RADIO_POWER", "PLMN_MATCH"]


def test_status_bar_icon_follows_the_last_event_that_touched_it():
    hysteresis = build_ntn_overview(
        [
            _ntn_event("NTN_MODE_NOTIFY", "08-25 10:00:00", ntn_mode="ON", last_ntn_mode="OFF"),
            _ntn_event("HYSTERESIS_ICON_ON", "08-25 10:00:01"),
        ],
        year=2026,
    )
    assert hysteresis.ntn_status.icon_status == ICON_ON_HYSTERESIS

    real = build_ntn_overview(
        [
            _ntn_event("HYSTERESIS_ICON_ON", "08-25 10:00:00"),
            _ntn_event("NTN_MODE_NOTIFY", "08-25 10:00:01", ntn_mode="ON", last_ntn_mode="OFF"),
        ],
        year=2026,
    )
    assert real.ntn_status.icon_status == ICON_ON_REAL

    off = build_ntn_overview(
        [_ntn_event("NTN_MODE_NOTIFY", "08-25 10:00:01", ntn_mode="OFF", last_ntn_mode="ON")], year=2026
    )
    assert off.ntn_status.icon_status == ICON_OFF


def test_data_policy_is_a_setting_not_a_transition():
    data = [
        _ntn_event("DATA_POLICY", "08-25 10:00:00", data_policy="RESTRICTED"),
        _ntn_event("PLMN_MATCH", "08-25 10:00:01"),
    ]

    overview = build_ntn_overview(data, year=2026)

    assert overview.ntn_status.data_policy == "RESTRICTED"
    assert overview.transitions["event_type"].tolist() == ["PLMN_MATCH"]  # chart
    assert len(overview.table) == 2  # table keeps it
    assert overview.transitions["time_dt"].tolist() == [pd.Timestamp("2026-08-25 10:00:01")]


def test_ntn_states_without_events():
    assert build_ntn_overview(None).status == "no_data"
    assert build_ntn_overview([]).status == "no_data"
    only_radio = [_ntn_event("RADIO_POWER", "08-25 10:00:00")]
    assert build_ntn_overview(only_radio).status == "no_ntn_events"


def test_missing_fields_show_up_as_dashes():
    overview = build_ntn_overview([{"time": "08-25 10:00:00", "event_type": "PLMN_MATCH"}], year=2026)

    assert overview.table["ntn_mode"].tolist() == ["-"]
    assert overview.ntn_status.data_policy == "N/A"
