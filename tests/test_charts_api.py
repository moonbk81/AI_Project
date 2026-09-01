"""Chart series over HTTP, for a browser frontend."""

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
import backend.charts_api as charts_api
from backend.charts_api import jsonable


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows

    def get(self, **kwargs):
        if kwargs.get("offset", 0) > 0:
            return {"ids": [], "metadatas": []}
        where = kwargs.get("where") or {}
        source = where.get("source_file")
        rows = [row for row in self.rows if not source or row.get("source_file") == source]
        return {"ids": [str(index) for index in range(len(rows))], "metadatas": rows}


class FakeEngine:
    def __init__(self, rows):
        self.collection = FakeCollection(rows)


ROWS = [
    {
        "source_file": "radio.log",
        "log_type": "OOS_Event",
        "time": "08-25 10:00:00.000",
        "slot": "0",
        "voice_reg": "0",
        "data_reg": "1",
        "operator": "SKT",
        "rat": "LTE",
    },
    {"source_file": "radio.log", "log_type": "DNS_Query", "app_name": "com.a", "return_code": "NXDOMAIN"},
    {"source_file": "other.log", "log_type": "DNS_Query", "app_name": "com.b", "return_code": "NXDOMAIN"},
]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(backend_main, "_engine", FakeEngine(ROWS))
    return TestClient(backend_main.app)


def test_frames_become_rows_with_real_nulls():
    frame = pd.DataFrame([{"a": 1.0, "when": pd.Timestamp("2026-08-25 10:00")}, {"a": None, "when": pd.NaT}])

    rows = jsonable(frame)

    assert rows[0]["a"] == 1.0
    assert rows[0]["when"].startswith("2026-08-25T10:00")
    # NaN is not valid JSON, so it has to come out as null.
    assert rows[1]["a"] is None and rows[1]["when"] is None


def test_log_payloads_never_ride_along_in_a_chart():
    assert jsonable({"content": b"12345"}) == {"content": "<5 bytes>"}


def test_chart_list_is_the_registry(client):
    charts = client.get("/charts").json()["charts"]

    assert "service-state" in charts and "dns-errors" in charts


def test_a_chart_returns_its_builder_contract(client):
    body = client.get("/charts/service-state", params={"source_file": "radio.log"}).json()

    assert body["chart"] == "service-state"
    assert body["series"]["status"] == "ok"
    states = [point["state"] for point in body["series"]["points"]]
    assert sorted(states) == ["IN_SERVICE", "OUT_OF_SERVICE"]
    # Timestamps arrive as ISO strings the browser can parse.
    assert body["series"]["points"][0]["time_dt"].startswith("20")


def test_the_status_travels_so_the_frontend_need_not_guess(client):
    body = client.get("/charts/service-state", params={"source_file": "other.log"}).json()

    assert body["series"]["status"] == "no_events"
    assert body["series"]["points"] == []


def test_artifact_lookup_accepts_report_suffixed_source_file(monkeypatch, tmp_path):
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    artifact_path = result_dir / "ps_call_504_unavailable_ims_sip.json"
    artifact_path.write_text(json.dumps([{"time": "04-09 12:07:00.865"}]), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    data = charts_api.artifact("ps_call_504_unavailable__bongki.moon_report.json", "ims_sip")

    assert data == [{"time": "04-09 12:07:00.865"}]


def test_call_history_reads_report_artifact_not_stale_metadata(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "result").mkdir()
    (tmp_path / "result" / "ps_call_504_unavailable__bongki.moon_report.json").write_text(
        json.dumps(
            {
                "call_sessions": [
                    {
                        "id": "TC@4_1",
                        "start_time": "04-09 12:07:59.968",
                        "status": "FAIL",
                        "fail_reason": "504_CODE_USER_DECLINE (SIP_480_Temporarily Unavailable)",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    body = client.get(
        "/charts/call-history",
        params={"source_file": "ps_call_504_unavailable__bongki.moon_payload.json"},
    ).json()

    assert body["series"]["status"] == "ok"
    assert body["series"]["call_count"] == 1
    assert body["series"]["statuses"] == ["FAIL"]
    assert body["series"]["table"][0]["id"] == "TC@4_1"


def test_service_state_reads_report_but_keeps_only_registration_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "result").mkdir()
    (tmp_path / "result" / "radio_report.json").write_text(
        json.dumps(
            {
                "oos_events": [
                    {
                        "time": "08-25 10:00:00.000",
                        "slotId": "0",
                        "voice_reg": "IN_SERVICE",
                        "data_reg": "IN_SERVICE",
                        "rat": "LTE",
                        "event_type": "OOS_RECOVER",
                    },
                    {
                        "time": "08-25 10:00:05.000",
                        "slotId": "0",
                        "voice_reg": "IN_SERVICE",
                        "data_reg": "IN_SERVICE",
                        "rat": "NR",
                        "event_type": "REG_DETAIL_CHANGE",
                        "change_reason": "rat",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(backend_main, "_engine", None)
    charts_api.clear_frame_cache()

    body = TestClient(backend_main.app).get("/charts/service-state", params={"source_file": "radio_payload.json"}).json()

    assert body["series"]["status"] == "ok"
    assert {point["radio_tech"] for point in body["series"]["points"]} == {"LTE"}


def test_asking_for_a_chart_that_does_not_exist(client):
    assert client.get("/charts/nope").status_code == 404


def test_the_browser_ui_and_its_assets_are_served(client):
    page = client.get("/ui/")
    styles = client.get("/ui/styles.css")
    app = client.get("/ui/js/app.js")
    viz = client.get("/ui/js/viz.js")
    plm = client.get("/ui/js/views/plm.js")
    boot = client.get("/ui/js/views/boot.js")
    dashboard = client.get("/ui/js/views/dashboard.js")
    files = client.get("/ui/js/views/files.js")
    bundle = client.get("/vendor/plotly.min.js")

    assert page.status_code == 200 and "로그 분석" in page.text
    assert "/vendor/plotly.min.js" not in page.text
    assert styles.status_code == 200 and "--series-1" in styles.text
    assert app.headers["cache-control"] == "no-store"
    assert app.status_code == 200 and "renderDashboard" in app.text
    assert "await Promise.all([loadFiles(), fontsSettled])" not in app.text
    assert "async filesChanged({ select, redraw = false } = {})" in app.text
    assert "if (redraw) rerender();" in app.text
    assert viz.status_code == 200 and 'script.src = "/vendor/plotly.min.js"' in viz.text
    assert 'const copy = el("button", null, "복사");' in viz.text
    assert "window.Plotly.toImage" in viz.text
    assert "tableText(tableHost)" in viz.text
    assert 'new ClipboardItem({ "image/png": blob })' in viz.text
    assert plm.status_code == 200 and "ctx.plmState" in plm.text
    assert "attachmentJobs" in app.text
    # 로그인 전에는 첫 화면이 로그인 창이고, 파일 목록조차 부르지 않는다.
    assert "drawLoginGate" in app.text
    assert "if (rememberedKnoxId()) await loadFiles();" in app.text
    assert "scanned?.job_id" in plm.text and "display_message" in plm.text
    assert "PLM 번호" in plm.text and "searchByDefectCode" in plm.text
    assert "field(\"Division\"" not in plm.text
    assert dashboard.status_code == 200 and "LLM 분석 요청" in dashboard.text
    assert files.status_code == 200 and "ctx.filesChanged({ select: null, redraw: true })" in files.text
    assert "ctx.startChat(sectionAnalysisQuestion(\"대시보드\", spec, sourceFile))" in dashboard.text
    assert "통화 drop 구간 전후의 RSRP 변화" in dashboard.text
    assert boot.status_code == 200 and "scattergeo" in boot.text and "Asia/Seoul" in boot.text
    assert "LLM 분석 요청" in boot.text
    assert "ctx.startChat(sectionAnalysisQuestion(\"시스템 진단 탭\", spec, sourceFile))" in boot.text
    assert "main thread callstack" in boot.text
    # plotly.js ships with the installed package; the page must not need a CDN.
    assert bundle.status_code == 200 and len(bundle.content) > 100_000


def test_artifact_backed_charts_read_the_analysis_result(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "result").mkdir()
    (tmp_path / "result" / "radio_report.json").write_text(
        json.dumps({"boot_stats": [{"Event": "RIL ready", "Time_ms": 4200, "Delta_ms": 900}]}),
        encoding="utf-8",
    )

    body = client.get("/charts/boot", params={"source_file": "radio_payload.json"}).json()

    assert body["series"]["status"] == "ok"
    assert body["series"]["milestones"]["voice_ready_ms"] == 4200


def test_a_missing_artifact_reads_as_no_data_rather_than_an_error(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    body = client.get("/charts/boot", params={"source_file": "never_analyzed_payload.json"}).json()

    assert body["series"]["status"] == "no_events"


def test_heavy_frames_are_projected_to_the_columns_the_chart_draws(client, monkeypatch):
    usage = [
        {
            "source_file": "radio.log",
            "log_type": "Data_Usage",
            "app_name": "YouTube",
            "total_mb": 12.5,
            "rat": "LTE",
            "time": "2026-08-25 10:00:00",
            "rx_mb": 10.0,
            "tx_mb": 2.5,
            "uid": "10123",
        }
    ]
    monkeypatch.setattr(backend_main, "_engine", FakeEngine(usage))
    charts_api.clear_frame_cache()

    timeline = client.get("/charts/data-usage", params={"source_file": "radio.log"}).json()["series"]["timeline"]

    assert list(timeline[0]) == ["time_dt", "app_name", "total_mb"]


def test_the_session_frame_is_reused_across_a_dashboard_of_charts(client, monkeypatch):
    charts_api.clear_frame_cache()
    scans = []

    real_loader = charts_api._load_session_frame
    monkeypatch.setattr(charts_api, "_load_session_frame", lambda source: (scans.append(source), real_loader(source))[1])

    for name in ("service-state", "dns-errors", "call-history"):
        client.get(f"/charts/{name}", params={"source_file": "radio.log"})

    assert scans == ["radio.log"]  # one scan for the whole dashboard


def test_the_favicon_is_served_so_every_page_stops_404ing(client):
    """Browsers ask for /favicon.ico on any page, /docs included."""
    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
