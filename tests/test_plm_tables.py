from plm.tables import (
    DEFECT_TITLE_CHARS,
    build_archive_rows,
    build_attachment_rows,
    build_defect_rows,
    defect_site_url,
)


def _defect(**overrides):
    defect = {
        "defectCode": "P260711-01",
        "defectId": "02FBN2PGBtPMWL1000",
        "plmTitle": "IMS registration retry failure",
        "plmStatus": "Open",
        "plmPriority": "A",
        "mainOwnerName": "moon",
        "createDate": "2026-07-11 10:00:00",
    }
    defect.update(overrides)
    return defect


def test_defect_row_carries_a_link_the_column_can_label():
    row = build_defect_rows([_defect()])[0]

    assert row["Code"] == f"{defect_site_url('02FBN2PGBtPMWL1000')}#P260711-01"
    assert row["Created"] == "2026-07-11"  # the time of day is noise in a list
    assert row["Owner"] == "moon"


def test_long_titles_are_cut_so_the_other_columns_stay_visible():
    row = build_defect_rows([_defect(plmTitle="x" * (DEFECT_TITLE_CHARS + 20))])[0]

    assert row["Title"] == "x" * DEFECT_TITLE_CHARS + "..."
    assert build_defect_rows([_defect(plmTitle="short")])[0]["Title"] == "short"


def test_defect_without_an_id_gets_no_link():
    assert build_defect_rows([_defect(defectId="")])[0]["Code"] == ""
    # A defect with an id but no code still links, just without the label anchor.
    assert build_defect_rows([_defect(defectCode="")])[0]["Code"].endswith("defectId=02FBN2PGBtPMWL1000")


def test_missing_defect_fields_read_as_na():
    row = build_defect_rows([{"defectId": "X"}])[0]

    assert (row["Status"], row["Priority"], row["Owner"]) == ("N/A", "N/A", "N/A")
    assert row["Title"] == ""


def test_no_defects():
    assert build_defect_rows([]) == []
    assert build_defect_rows(None) == []


def test_attachment_rows_show_sizes_in_kilobytes():
    rows = build_attachment_rows(
        [
            {"title": "log.zip", "fileSize": 1536, "createDate": "2026-07-11 10:00:00"},
            {"title": "empty.zip"},  # PLM did not report a size
        ]
    )

    assert rows[0] == {"Filename": "log.zip", "Size": "1.5 KB", "Created": "2026-07-11"}
    assert rows[1]["Size"] == "N/A"


def test_download_list_needs_the_file_id():
    rows = build_attachment_rows([{"title": "log.zip", "fileId": "F1"}], name_column="File", include_id=True)

    assert rows[0]["File"] == "log.zip"
    assert rows[0]["ID"] == "F1"


def test_archive_rows_always_show_a_size():
    rows = build_archive_rows({"dumpstate.log": 2048, "marker": 0})

    assert rows[0] == {"File": "dumpstate.log", "Size": "2.0 KB"}
    assert rows[1]["Size"] == "0.0 KB"  # a real, known, empty file
    assert build_archive_rows(None) == []


# ------------------------------------------------------- defect registration


def test_the_form_becomes_the_body_plm_expects():
    from plm.registration import build_defect_payload

    payload = build_defect_payload(
        division_code="25",
        title="통화 끊김",
        content="핸드오버 중",
        create_user="knox",
        external_id_factory=lambda: "AI_FIXED",
    )

    assert payload["title"] == "통화 끊김"
    assert payload["Content"] == "핸드오버 중"
    # The person filing it is also the one it is assigned to.
    assert payload["createUser"] == payload["inChargeUser"] == "knox"
    assert payload["externalDefectId"] == "AI_FIXED"
    # Fixed for everything this tool files.
    assert (payload["refObjectType"], payload["defectCategory"], payload["occurPhase"]) == ("MFG", "SW", "DV")


def test_optional_fields_left_blank_are_sent_as_nothing():
    from plm.registration import build_defect_payload

    payload = build_defect_payload(
        division_code="25", title="t", content="c", create_user="k", external_id_factory=lambda: "X"
    )

    assert payload["reappearancePath"] is None
    assert payload["forecastResult"] is None
    assert payload["swVersion"] is None


def test_a_supplied_external_id_is_kept():
    from plm.registration import build_defect_payload

    payload = build_defect_payload(
        division_code="25", title="t", content="c", create_user="k", external_id="JIRA-1"
    )

    assert payload["externalDefectId"] == "JIRA-1"


def test_the_form_says_what_it_still_needs():
    from plm.registration import missing_required

    assert missing_required("", "내용", "knox") == "제목"
    assert missing_required("제목", "  ", "knox") == "문제 내용"
    assert missing_required("제목", "내용", None) == "작성자 Knox ID"
    assert missing_required("제목", "내용", "knox") is None
