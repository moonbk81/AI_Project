from plm.service import (
    _AI_COMMENT_SIGNATURES,
    build_comment_payload,
    build_defect_analysis_query,
    format_analysis_as_comment,
)


def test_defect_query_includes_only_populated_sections():
    query = build_defect_analysis_query(
        {
            "defect_code": "D-1",
            "defect_title": "데이터 끊김",
            "status": "Open",
            "priority": "High",
            "owner": "someone",
            "content": "5G에서 간헐적 끊김",
            "reason": "  ",
            "countermeasure": "",
        }
    )

    assert "**결함 코드:** D-1" in query
    assert "**담당자:** someone" in query
    assert "### 문제 내용" in query
    # Blank reason/countermeasure must not produce empty headings.
    assert "### 등록된 근본 원인" not in query
    assert "### 등록된 해결방안" not in query
    assert "### 개발자 코멘트" not in query
    assert query.endswith("위 정보를 기반으로 문제의 원인을 분석하고 해결 방안을 제시해 주세요.")


def test_defect_query_with_reason_countermeasure_and_comments():
    query = build_defect_analysis_query(
        {"defect_code": "D-2", "content": "C", "reason": " R ", "countermeasure": " S "},
        comments=[
            {"user": "kim", "date": "2026-01-02", "text": "gNB 로그 확인"},
            {"text": "작성자 없음"},
        ],
    )

    assert "### 등록된 근본 원인\nR" in query
    assert "### 등록된 해결방안\nS" in query
    assert "- (kim · 2026-01-02) gNB 로그 확인" in query
    assert "- 작성자 없음" in query
    assert query.endswith(
        "위 정보를 기반으로 개발자 코멘트를 고려하여 문제의 원인을 분석하고 해결 방안을 제시해 주세요."
    )


def test_empty_problem_still_builds_a_query():
    query = build_defect_analysis_query({})
    assert query.startswith("## PLM 결함 분석 요청")
    assert "### 문제 내용" in query


def test_chat_comment_format():
    assert format_analysis_as_comment({"from_chat": True, "answer": "본문"}) == (
        "💬 **AI Chat 분석 결과**\n\n본문"
    )


def test_analysis_comment_format():
    comment = format_analysis_as_comment(
        {"problem": "P", "root_cause": "R", "solution": "S"}
    )
    assert comment == (
        "🤖 AI 분석 결과\n\n**문제점:**\nP\n\n**근본 원인:**\nR\n\n**해결 방안:**\nS"
    )


def test_missing_fields_fall_back_to_na():
    assert "N/A" in format_analysis_as_comment({"from_chat": True})
    assert format_analysis_as_comment({}).count("N/A") == 3


def test_ai_signatures_match_what_the_formatter_writes():
    """get_human_comments() filters on these prefixes, so they must not drift."""
    chat = format_analysis_as_comment({"from_chat": True, "answer": "x"})
    analysis = format_analysis_as_comment({"problem": "p"})
    assert any(chat.startswith(sig) for sig in _AI_COMMENT_SIGNATURES)
    assert any(analysis.startswith(sig) for sig in _AI_COMMENT_SIGNATURES)


def test_comment_payload_shape():
    assert build_comment_payload(
        division_code="25",
        defect_code="D-1",
        comment="본문",
        create_user="knox",
    ) == {
        "divisionCode": "25",
        "systemCode": "AI_ANALYSIS",
        "defectCode": "D-1",
        "defectComment": "본문",
        "createUser": "knox",
        "changeType": "S",
        "docAttachedYn": "N",
    }
