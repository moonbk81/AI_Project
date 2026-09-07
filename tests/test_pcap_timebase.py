"""pcap 의 UTC epoch 을 로그 시계로 옮기는 규칙."""

from datetime import datetime, timedelta, timezone

from parsers.pcap_timebase import (
    check_overlap,
    log_time_window,
    resolve_timebase,
    to_log_time,
)


# 2026-09-02 00:00:00 UTC
BASE_EPOCH = 1788307200.0


def _nitz(*timezones):
    return {"nitz_history": [{"timezone": value} for value in timezones]}


def test_nitz_is_trusted_over_this_server_clock():
    base = resolve_timebase(_nitz("UTC+9시간"))

    assert base["tz_offset_hours"] == 9.0
    assert base["source"] == "nitz"
    assert base["confidence"] == "high"


def test_the_last_nitz_wins_because_roaming_changes_the_zone():
    base = resolve_timebase(_nitz("UTC+9시간", "UTC-3시간"))

    assert base["tz_offset_hours"] == -3.0


def test_nitz_entries_without_a_readable_zone_are_skipped():
    base = resolve_timebase(_nitz("UTC+9시간", "알 수 없음"))

    assert base["tz_offset_hours"] == 9.0


def test_an_explicit_offset_beats_everything():
    base = resolve_timebase(_nitz("UTC+9시간"), override_hours=5.5)

    assert base["tz_offset_hours"] == 5.5
    assert base["source"] == "override"


def test_falling_back_to_the_host_clock_says_so_and_lowers_confidence():
    host_offset = datetime.now().astimezone().utcoffset() or timedelta(0)

    base = resolve_timebase({})

    assert base["source"] == "host"
    assert base["confidence"] == "low"
    assert base["tz_offset_hours"] == host_offset.total_seconds() / 3600.0
    assert "NITZ" in base["note"]


def test_epochs_become_log_timestamps_in_the_devices_zone():
    assert to_log_time(BASE_EPOCH, 0) == "09-02 00:00:00.000"
    assert to_log_time(BASE_EPOCH, 9) == "09-02 09:00:00.000"
    assert to_log_time(BASE_EPOCH - 3600, 9) == "09-02 08:00:00.000"
    # 로그는 밀리초까지 찍는다. 마이크로초는 버린다.
    assert to_log_time(BASE_EPOCH + 1.2345, 0) == "09-02 00:00:01.234"


def test_the_log_window_is_the_first_and_last_index_key():
    assert log_time_window(["09-02 10:00:00", "09-02 08:00:00", "09-02 09:00:00"]) == (
        "09-02 08:00:00",
        "09-02 10:00:00",
    )
    assert log_time_window([]) is None
    assert log_time_window(None) is None
    assert log_time_window(["", "09-02 08:00:00"]) == ("09-02 08:00:00", "09-02 08:00:00")


def test_a_capture_inside_the_log_window_just_reports_the_overlap():
    alignment = check_overlap(
        "09-02 09:00:00.000", "09-02 09:30:00.000", ("09-02 08:00:00", "09-02 10:00:00")
    )

    assert alignment == {
        "checked": True,
        "overlaps": True,
        "log_window": ["09-02 08:00:00", "09-02 10:00:00"],
        "capture_window": ["09-02 09:00:00.000", "09-02 09:30:00.000"],
    }


def test_a_capture_beside_the_log_window_is_warned_about_not_silently_zeroed():
    alignment = check_overlap(
        "09-02 18:00:00.000", "09-02 18:30:00.000", ("09-02 08:00:00", "09-02 10:00:00")
    )

    assert alignment["overlaps"] is False
    assert "비교 불가" in alignment["warning"]
    # 시각을 몰래 밀지는 않는다. 몇 시간 어긋나 보이는지만 알려 준다.
    assert alignment["suggested_extra_offset_hours"] == -10


def test_without_a_log_window_the_check_is_skipped_and_says_why():
    alignment = check_overlap("09-02 09:00:00.000", "09-02 09:30:00.000", None)

    assert alignment["checked"] is False
    assert alignment["reason"]


def test_an_unreadable_capture_time_drops_the_suggestion_not_the_warning():
    alignment = check_overlap("때 미상", "때 미상", ("09-02 08:00:00", "09-02 10:00:00"))

    assert alignment["overlaps"] is False
    assert alignment["warning"]
    assert "suggested_extra_offset_hours" not in alignment
