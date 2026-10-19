from parsers.rtos.nitz import parse_nitz


def test_telephonytool_line_is_utc_plus_offset():
    nitz = parse_nitz("sec = 59 min = 30 hour = 23                 mday = 31 mon = 12 year = 2026 dst = 1 "
                      "utcoff = -18000 \n")
    assert nitz == {"source": "telephonytool", "utc": "2026-12-31 23:30:59", "local": "2026-12-31 18:30:59",
                    "utcoff_min": -300, "dst": 1}


def test_rilj_raw_1008_uses_quarter_hours():
    nitz = parse_nitz('[RILJ] [UNSL]< UNSOL_1008 {"26/10/19,15:00:00+22"}')
    assert nitz["source"] == "rilj" and nitz["local"] == "2026-10-19 20:30:00" and nitz["dst"] == 0


def test_other_lines_and_bad_dates_are_ignored():
    assert parse_nitz("[RILJ] [UNSL]< SIGNAL_STRENGTH {1, 2}") is None
    assert parse_nitz("sec = 0 min = 0 hour = 0 mday = 0 mon = 0 year = 2026 dst = 0 utcoff = 0") is None
