from core.knowledge import (
    WHOLE_REPORT,
    available_categories,
    build_cases,
    build_filters,
    build_info,
    recommend_category,
    target_ids,
)


def test_the_wording_decides_where_a_case_is_filed():
    assert recommend_category("통화가 drop 됨") == "Call_Session"
    assert recommend_category("배터리 방전이 빠름") == "Battery_Drain_Report"
    assert recommend_category("DNS 가 차단됨") == "Network_DNS_Issue"
    assert recommend_category("ANR 발생") == "Crash_Event"
    # Nothing recognisable: the case covers the whole answer.
    assert recommend_category("그냥 메모") == WHOLE_REPORT


def test_a_category_the_answer_never_touched_is_not_recommended():
    """Only log types the retrieved rows actually contain are offered."""
    assert recommend_category("통화 drop", ["Total_Report", "Signal_Level"]) == WHOLE_REPORT
    assert recommend_category("통화 drop", ["Total_Report", "Call_Session"]) == "Call_Session"


def test_the_categories_on_offer_come_from_the_retrieved_rows():
    metas = [{"log_type": "Call_Session"}, {"log_type": "Signal_Level"}, {"log_type": "Call_Session"}, None]

    assert available_categories(metas) == [WHOLE_REPORT, "Call_Session", "Signal_Level"]
    assert available_categories(None) == [WHOLE_REPORT]


def test_a_case_is_filed_against_the_rows_of_its_log_type():
    ids = ["a", "b", "c"]
    metas = [{"log_type": "Call_Session"}, {"log_type": "Signal_Level"}, {"log_type": "Call_Session"}]

    assert target_ids("Call_Session", ids, metas) == ["a", "c"]
    assert target_ids(WHOLE_REPORT, ids, metas) == ids
    assert target_ids("OOS_Event", ids, metas) == []


def test_the_build_a_case_was_seen_on_is_read_off_the_rows():
    info = build_info([None, {"model_name": "SM-S921N", "radio": "R1"}, {"model_name": "other"}])

    assert info["model_name"] == "SM-S921N"  # the first row that has anything
    assert info["radio"] == "R1"
    assert info["kernel"] == "Unknown"
    assert build_info([])["model_name"] == "Unknown"


def test_cases_are_shaped_for_the_list_they_appear_in():
    case = build_cases(
        {
            "ids": ["0123456789abcdef"],
            "documents": ["Radio 펌웨어 업데이트로 해결"],
            "metadatas": [{"model_name": "SM-S921N", "severity": "Critical", "target_ids": "doc-1"}],
        }
    )[0]

    assert case.short_id == "01234567"
    assert case.note == "Radio 펌웨어 업데이트로 해결"
    assert case.severity == "Critical"
    assert case.hardware == "-"  # not recorded on that case


def test_filters_offer_each_value_once():
    cases = build_cases(
        {
            "ids": ["1", "2", "3"],
            "documents": ["a", "b", "c"],
            "metadatas": [
                {"model_name": "SM-A", "severity": "Major"},
                {"model_name": "SM-A", "severity": "Info"},
                {"model_name": "SM-B", "severity": "Major"},
            ],
        }
    )

    filters = build_filters(cases)

    assert filters.model_name == ["SM-A", "SM-B"]
    assert filters.severity == ["Info", "Major"]


def test_no_cases_recorded_yet():
    assert build_cases(None) == []
    assert build_filters([]).model_name == []
