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


def test_asking_for_a_chart_that_does_not_exist(client):
    assert client.get("/charts/nope").status_code == 404


def test_the_browser_ui_and_its_assets_are_served(client):
    page = client.get("/ui/")
    styles = client.get("/ui/styles.css")
    app = client.get("/ui/js/app.js")
    bundle = client.get("/vendor/plotly.min.js")

    assert page.status_code == 200 and "로그 분석" in page.text
    assert styles.status_code == 200 and "--series-1" in styles.text
    assert app.status_code == 200 and "renderDashboard" in app.text
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
