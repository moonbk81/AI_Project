"""Chart series over HTTP, for a browser frontend."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
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


def test_the_spike_page_and_its_plotly_bundle_are_served(client):
    page = client.get("/ui/")
    bundle = client.get("/vendor/plotly.min.js")

    assert page.status_code == 200 and "로그 분석 대시보드" in page.text
    assert bundle.status_code == 200 and len(bundle.content) > 100_000
