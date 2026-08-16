import sys
from types import SimpleNamespace

import pytest

from app import backend_client


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeUpload:
    name = "radio.log"

    def getbuffer(self):
        return b"log-body"


@pytest.fixture(autouse=True)
def backend_env(monkeypatch):
    monkeypatch.setenv("USE_BACKEND_API", "1")
    monkeypatch.setenv("BACKEND_API_URL", "http://backend.local:8080/")
    monkeypatch.setenv("BACKEND_API_TIMEOUT", "12.5")


def install_fake_requests(monkeypatch, *, get=None, post=None):
    fake_requests = SimpleNamespace(
        get=get or (lambda *args, **kwargs: FakeResponse()),
        post=post or (lambda *args, **kwargs: FakeResponse()),
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    return fake_requests


def test_backend_api_url_is_normalized():
    assert backend_client.is_backend_api_enabled() is True
    assert backend_client.get_backend_api_url() == "http://backend.local:8080"


def test_ask_via_backend_posts_question_payload(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            {
                "answer": "ok",
                "ids": ["id-1"],
                "metas": [{"source_file": "radio.log"}],
                "thinking": "trace",
            }
        )

    install_fake_requests(monkeypatch, post=fake_post)

    result = backend_client.ask_via_backend(
        "why did data stall?",
        current_file="radio.log",
        chat_history=[{"role": "user", "content": "hello"}],
        top_k=3,
        health_kpi="data",
    )

    assert result == ("ok", ["id-1"], [{"source_file": "radio.log"}], "trace")
    assert calls == [
        (
            "http://backend.local:8080/ask",
            {
                "json": {
                    "question": "why did data stall?",
                    "current_file": "radio.log",
                    "chat_history": [{"role": "user", "content": "hello"}],
                    "top_k": 3,
                    "health_kpi": "data",
                },
                "timeout": 12.5,
            },
        )
    ]


def test_result_json_returns_default_for_missing_backend_artifact(monkeypatch):
    def fake_get(url, **kwargs):
        return FakeResponse(status_code=404)

    install_fake_requests(monkeypatch, get=fake_get)

    assert backend_client.get_result_json_via_backend("radio", "report", default={}) == {}


def test_create_analyze_job_uploads_files_and_options(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({"job_id": "job-123"})

    install_fake_requests(monkeypatch, post=fake_post)

    job_id = backend_client.create_analyze_job_via_backend(
        [FakeUpload()],
        use_slice=True,
        start_t="00:00:01",
        end_t="00:00:05",
    )

    assert job_id == "job-123"
    url, kwargs = calls[0]
    assert url == "http://backend.local:8080/jobs/analyze"
    assert kwargs["data"] == {
        "use_slice": "true",
        "start_t": "00:00:01",
        "end_t": "00:00:05",
    }
    assert kwargs["timeout"] == 12.5
    assert kwargs["files"] == [
        (
            "files",
            ("radio.log", b"log-body", "application/octet-stream"),
        )
    ]


def test_plm_quick_search_via_backend_posts_search_payload(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            {
                "success": True,
                "defects": [{"defectCode": "P260711-001"}],
                "defect_codes": ["P260711-001"],
                "total_codes": 1,
                "truncated": False,
            }
        )

    install_fake_requests(monkeypatch, post=fake_post)

    result = backend_client.plm_quick_search_via_backend(
        division_code="25",
        main_owner_id="user.one,user.two",
        status="Open",
        search_type="main",
        limit=25,
    )

    assert result["defects"] == [{"defectCode": "P260711-001"}]
    assert calls == [
        (
            "http://backend.local:8080/plm/quick-search",
            {
                "json": {
                    "division_code": "25",
                    "main_owner_id": "user.one,user.two",
                    "status": "Open",
                    "search_type": "main",
                    "limit": 25,
                },
                "timeout": 12.5,
            },
        )
    ]


def test_plm_file_client_helpers(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/plm/files/download"):
            response = FakeResponse()
            response.content = b"file-bytes"
            response.headers = {"X-File-Size": "10", "X-Filename": "radio.zip"}
            return response
        return FakeResponse(
            {
                "success": True,
                "files": [{"title": "radio.zip", "fileId": "file-1", "docId": "doc-1"}],
            }
        )

    install_fake_requests(monkeypatch, post=fake_post)

    files = backend_client.plm_list_files_via_backend("25", "P260711-001")
    download = backend_client.plm_download_file_via_backend("25", "doc-1", "radio.zip", "file-1")

    assert files["files"][0]["title"] == "radio.zip"
    assert download == {
        "success": True,
        "message": "",
        "data": b"file-bytes",
        "size": 10,
        "filename": "radio.zip",
    }
    assert calls[0] == (
        "http://backend.local:8080/plm/files",
        {
            "json": {
                "division_code": "25",
                "defect_code": "P260711-001",
                "attach_type": "OP_DEFECT_ATTACH",
            },
            "timeout": 12.5,
        },
    )
    assert calls[1] == (
        "http://backend.local:8080/plm/files/download",
        {
            "json": {
                "division_code": "25",
                "doc_id": "doc-1",
                "title": "radio.zip",
                "file_id": "file-1",
            },
            "timeout": 12.5,
        },
    )


def test_plm_comment_and_analyze_client_helpers(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/plm/comment"):
            return FakeResponse({"success": True, "message": "ok", "result": {"commentId": "c-1"}})
        return FakeResponse(
            {
                "success": True,
                "message": "",
                "context": {"defect_code": "P260711-001", "problem": "Data stall"},
            }
        )

    install_fake_requests(monkeypatch, post=fake_post)

    payload = {
        "divisionCode": "25",
        "systemCode": "AI_ANALYSIS",
        "defectCode": "P260711-001",
        "defectComment": "analysis",
        "createUser": "tester",
    }

    comment = backend_client.plm_submit_comment_via_backend(payload)
    analysis = backend_client.plm_analyze_via_backend("25", "P260711-001")

    assert comment["result"] == {"commentId": "c-1"}
    assert analysis["context"] == {"defect_code": "P260711-001", "problem": "Data stall"}
    assert calls[0] == (
        "http://backend.local:8080/plm/comment",
        {"json": {"payload": payload}, "timeout": 12.5},
    )
    assert calls[1] == (
        "http://backend.local:8080/plm/analyze",
        {"json": {"division_code": "25", "defect_code": "P260711-001"}, "timeout": 12.5},
    )
