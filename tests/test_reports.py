import pytest

from agent_toolkit.satellite_tools import detect_satellite_type
from core import reports


class FakeEngine:
    def __init__(self, result):
        self.result = result
        self.asked = []

    def ask(self, question, **kwargs):
        self.asked.append((question, kwargs))
        return self.result


@pytest.fixture(autouse=True)
def stub_health_kpi(monkeypatch):
    monkeypatch.setattr(reports, "_health_kpi", lambda base_name: f"KPI({base_name})")


def test_session_report_splices_kpi_into_the_prompt():
    engine = FakeEngine(("답변", ["id1"], [{"m": 1}], "생각"))

    report = reports.generate_session_report(engine, "radio", current_file="radio.log")

    assert report == {"answer": "답변", "ids": ["id1"], "metas": [{"m": 1}], "thinking": "생각"}
    question, kwargs = engine.asked[0]
    assert "KPI(radio)" in question
    assert kwargs == {"current_file": "radio.log"}


def test_satellite_report_unescapes_newlines():
    engine = FakeEngine(("첫줄\\n둘째줄", [], [], ""))

    report = reports.generate_satellite_report(engine, "radio", "Tiantong")

    assert report["answer"] == "첫줄\n둘째줄"
    assert "Tiantong" in engine.asked[0][0]


def test_satellite_report_rejects_unknown_constellation():
    with pytest.raises(ValueError, match="satellite_prompts.Nope"):
        reports.generate_satellite_report(FakeEngine(("", [], [], "")), "radio", "Nope")


def test_non_tuple_answer_is_still_normalized():
    report = reports.generate_session_report(FakeEngine("문자열 답변"), "radio")
    assert report == {"answer": "문자열 답변", "ids": [], "metas": [], "thinking": ""}


def test_short_tuple_is_padded():
    report = reports.generate_session_report(FakeEngine(("답변", ["id"])), "radio")
    assert report == {"answer": "답변", "ids": ["id"], "metas": [], "thinking": ""}


@pytest.mark.parametrize(
    "sat_at, ntn, expected",
    [
        ({"call_flow": [{"t": 1}]}, None, "Tiantong"),
        ({"call_flow": []}, {}, None),
        (None, {"policy": [1]}, "SpaceX"),
        (None, [1, 2], "SpaceX"),
        (None, {"policy": None}, None),
        (None, [], None),
        # Tiantong wins when a log somehow carries both.
        ({"call_flow": [{"t": 1}]}, [1], "Tiantong"),
        # A list where a dict was expected must not raise.
        ([], {}, None),
    ],
)
def test_detect_satellite_type(sat_at, ntn, expected):
    assert detect_satellite_type(sat_at_data=sat_at, ntn_data=ntn) == expected
