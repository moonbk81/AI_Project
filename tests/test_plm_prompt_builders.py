from plm.comments import (
    _AI_COMMENT_SIGNATURES,
    build_comment_payload,
    format_analysis_as_comment,
    is_ai_generated_comment,
    render_comment_for_plm,
)
from plm.prompts import build_defect_analysis_query


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


# ----------------------------------------------- comment rendering for PLM


def test_multi_line_comments_are_sent_as_markup():
    """PLM collapses a plain comment into one line unless it carries tags."""
    text, needs_editor = render_comment_for_plm("첫 줄\n둘째 줄")

    assert text == "첫 줄<br>둘째 줄"
    assert needs_editor is True


def test_bold_survives_as_a_tag():
    text, _ = render_comment_for_plm("**문제점:**\n데이터 끊김")

    assert text == "<b>문제점:</b><br>데이터 끊김"


def test_log_excerpts_do_not_become_markup():
    text, _ = render_comment_for_plm("오류: <null> & timeout\n재현됨")

    assert text == "오류: &lt;null&gt; &amp; timeout<br>재현됨"


def test_a_plain_one_liner_is_left_exactly_as_typed():
    text, needs_editor = render_comment_for_plm("재현 안 됨 (a<b)")

    assert text == "재현 안 됨 (a<b)"
    assert needs_editor is False


def test_editor_mode_is_requested_only_when_there_are_tags():
    multiline = build_comment_payload(division_code="25", defect_code="D-1", comment="a\nb", create_user="knox")
    plain = build_comment_payload(division_code="25", defect_code="D-1", comment="a", create_user="knox")

    assert multiline["isCommentEditorYn"] == "Y"
    assert "isCommentEditorYn" not in plain


def test_modify_and_delete_carry_the_comment_id():
    payload = build_comment_payload(
        division_code="25",
        defect_code="D-1",
        comment="수정본",
        create_user="knox",
        change_type="M",
        comment_id="01YJK98RTtPMWL1000",
    )

    assert payload["changeType"] == "M"
    assert payload["defectCommentId"] == "01YJK98RTtPMWL1000"


def test_our_own_comments_are_recognised_in_either_shape():
    """A comment we posted must not come back as developer input."""
    markdown = format_analysis_as_comment({"from_chat": True, "answer": "본문"})
    html, _ = render_comment_for_plm(markdown)

    assert is_ai_generated_comment(markdown)
    assert is_ai_generated_comment(html)
    assert not is_ai_generated_comment("실제 개발자가 남긴 코멘트")
