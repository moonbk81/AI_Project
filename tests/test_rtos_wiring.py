"""RTOS 분석이 파이프라인 끝(RAG 문서, 차트, 채팅 도구, 라우팅)까지 이어지는지."""

import json
from types import SimpleNamespace

from agent_toolkit import get_rtos_call_flow_analytics
from core.charts import build_rtos_call_flow
from log_orchestrator import LogOrchestrator
from rag_builders.builder import build_all_payloads
from tests.test_rtos_call_flow import MO_LINES, MT_LINES

HEADER = ["=" * 39, "CHIP=best1700", "KERNEL=NUTTX", "SEC_VERSION=L615XXU0ZI05", "=" * 39]


def _run(tmp_path, monkeypatch, lines, encoding="utf-8"):
    monkeypatch.chdir(tmp_path)
    log = tmp_path / "callMT.txt"
    log.write_text("\r\n".join(lines) + "\r\n", encoding=encoding)
    report = tmp_path / "callMT_report.json"
    assert LogOrchestrator(str(log)).run_batch(str(report)) is True
    return json.loads(report.read_text(encoding="utf-8"))


def test_utf16_rtos_log_goes_through_rtos_pipeline(tmp_path, monkeypatch):
    report = _run(tmp_path, monkeypatch, HEADER + MT_LINES, encoding="utf-16")

    assert report["log_domain"] == "rtos"
    assert report["rtos_build_info"]["SEC_VERSION"] == "L615XXU0ZI05"
    assert report["rtos_call_flow"]["kpi"]["mt_count"] == 1
    assert (tmp_path / "result" / "callMT_rtos_call_flow.json").exists()
    assert "call_sessions" not in report, "Android 파서가 돌면 안 된다"


def test_rag_payload_has_summary_and_call_documents(tmp_path, monkeypatch):
    report = _run(tmp_path, monkeypatch, MT_LINES)
    payloads = build_all_payloads(report, "callMT_report.json", None, None)

    types = [p["metadata"]["log_type"] for p in payloads]
    assert types == ["RTOS_Log_Summary", "RTOS_Call_Flow"]
    call_doc = payloads[1]
    assert call_doc["metadata"]["broken_stage"] == "TAPI/UI 착신 화면"
    assert "끊긴 지점: TAPI/UI 착신 화면" in call_doc["document"]
    for meta in (p["metadata"] for p in payloads):
        assert all(isinstance(v, (str, int, float, bool)) for v in meta.values()), meta


def test_rag_payload_never_empty_without_calls():
    report = {"log_domain": "rtos", "rtos_call_flow": {"kpi": {}, "calls": [], "unanswered_requests": []}}
    payloads = build_all_payloads(report, "x_report.json", None, None)
    assert [p["metadata"]["log_type"] for p in payloads] == ["RTOS_Log_Summary"]


def test_chart_and_tool_read_the_artifact(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch, MO_LINES)
    data = json.loads((tmp_path / "result" / "callMT_rtos_call_flow.json").read_text(encoding="utf-8"))

    chart = build_rtos_call_flow(data)
    assert chart.status == "ok"
    stages = chart.calls[0]["stages"]
    assert stages[0]["offset_ms"] == 0
    assert all(s["reached"] for s in stages[:5])
    assert build_rtos_call_flow({}).status == "no_data"

    fact = json.loads(get_rtos_call_flow_analytics("callMT", "./result"))
    assert fact["status"] == "OK"
    assert fact["calls"][0]["fail_cause"] == "16"


def test_chat_routes_rtos_logs_only_to_rtos_intents(tmp_path):
    from ril_rag_chat import RilRagChat

    (tmp_path / "a_rtos_call_flow.json").write_text("{}", encoding="utf-8")
    assert RilRagChat._log_domain("a", str(tmp_path)) == "rtos"
    assert RilRagChat._log_domain("b", str(tmp_path)) == "android"
    assert RilRagChat._log_domain("Unknown", str(tmp_path)) == "android"

    fake = SimpleNamespace(routing_map={
        "Call_Analysis": {"desc": "통화"},
        "RTOS_Call_Analysis": {"domain": "rtos", "desc": "워치 통화"},
    })
    assert list(RilRagChat._routing_map_for(fake, "rtos")) == ["RTOS_Call_Analysis"]
    assert list(RilRagChat._routing_map_for(fake, "android")) == ["Call_Analysis"]
