import json

from core.references import RAW_LOG_PREVIEW_LINES, build_reference_blocks, parse_raw_logs


def test_raw_lines_survive_however_chroma_stored_them():
    """Parsers stringify lists on the way in; all these shapes come back out."""
    assert parse_raw_logs(["a", "b"]) == ["a", "b"]
    assert parse_raw_logs(json.dumps(["a", "b"])) == ["a", "b"]
    assert parse_raw_logs("['a', 'b']") == ["a", "b"]
    assert parse_raw_logs("first\nsecond") == ["first", "second"]
    assert parse_raw_logs("[unquoted single]") == ["unquoted single"]


def test_blank_lines_and_unusable_values_drop_out():
    assert parse_raw_logs(["a", "", "   ", "b"]) == ["a", "b"]
    assert parse_raw_logs("") == []
    assert parse_raw_logs(None) == []
    assert parse_raw_logs(42) == []


def test_a_reference_names_where_the_evidence_came_from():
    block = build_reference_blocks([{"time": "08-25 10:00:00", "slot": "1", "raw_logs": ["a"]}])[0]

    assert block.index == 1  # 1-based: the reader sees "자료 1"
    assert (block.time, block.slot) == ("08-25 10:00:00", "1")
    assert block.raw_logs == ["a"]
    assert block.truncated is False


def test_long_excerpts_are_previewed_with_the_total_kept():
    lines = [f"line {i}" for i in range(25)]

    block = build_reference_blocks([{"raw_logs": lines}])[0]

    assert len(block.raw_logs) == RAW_LOG_PREVIEW_LINES
    assert block.raw_log_total == 25
    assert block.truncated is True


def test_the_raw_field_falls_through_in_order():
    """A row filled by the crash parser keeps its lines under another key."""
    stack = build_reference_blocks([{"raw_stack": ["at a.b()"]}])[0]
    context = build_reference_blocks([{"raw_context": ["ctx"], "raw_stack": ["stack"]}])[0]

    assert stack.raw_logs == ["at a.b()"]
    assert context.raw_logs == ["ctx"]


def test_a_past_analysis_is_carried_next_to_the_evidence():
    with_solution = build_reference_blocks([{"known_solution": "APN 재설정"}])[0]
    without = build_reference_blocks([{"known_solution": ""}])[0]

    assert with_solution.known_solution == "APN 재설정"
    assert without.known_solution is None


def test_request_and_response_pairs_are_kept_apart_from_log_lines():
    block = build_reference_blocks([{"raw_request": "REQ 1", "raw_response": "RESP 1"}])[0]

    assert (block.raw_request, block.raw_response) == ("REQ 1", "RESP 1")
    assert block.raw_logs == []


def test_no_retrieved_rows():
    assert build_reference_blocks([]) == []
    assert build_reference_blocks(None) == []
