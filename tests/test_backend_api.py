import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main


class FakeCollection:
    def __init__(self, payload):
        self.payload = payload

    def get(self, **kwargs):
        if kwargs.get("offset", 0) > 0:
            return {"ids": [], "metadatas": []}
        return self.payload


class FakeEngine:
    def __init__(self):
        self.collection = FakeCollection(
            {
                "ids": ["meta-1"],
                "metadatas": [{"source_file": "radio.log", "log_type": "Signal_Level"}],
            }
        )
        self.knowledge_collection = FakeCollection(
            {
                "ids": ["kb-1"],
                "documents": ["known fix"],
                "metadatas": [{"severity": "High"}],
            }
        )
        self.saved_knowledge = None

    def ask(self, question, **kwargs):
        return (
            f"answer: {question}",
            ["doc-1"],
            [{"source_file": kwargs.get("current_file")}],
            "thinking",
        )

    def get_all_files(self):
        return ["radio.log"]

    def reset_db(self):
        return True

    def save_knowledge(self, target_ids, feedback, severity="Normal", build_info=None):
        self.saved_knowledge = {
            "target_ids": target_ids,
            "feedback": feedback,
            "severity": severity,
            "build_info": build_info,
        }
        return True


class FakeExecutor:
    def __init__(self):
        self.submissions = []

    def submit(self, fn, *args, **kwargs):
        self.submissions.append((fn, args, kwargs))
        return object()


@pytest.fixture()
def fake_engine():
    return FakeEngine()


@pytest.fixture()
def fake_executor():
    return FakeExecutor()


@pytest.fixture()
def client(monkeypatch, tmp_path, fake_engine, fake_executor):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backend_main, "_engine", fake_engine)
    monkeypatch.setattr(backend_main, "_executor", fake_executor)
    with backend_main._jobs_lock:
        backend_main._jobs.clear()
    return TestClient(backend_main.app)


def test_health_reports_backend_status(client):
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["engine_loaded"] is True
    assert "report" in data["supported_artifacts"]


def test_ask_endpoint_uses_engine_contract(client):
    response = client.post(
        "/ask",
        json={
            "question": "why did signal drop?",
            "current_file": "radio.log",
            "chat_history": [{"role": "user", "content": "hello"}],
            "top_k": 3,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "answer: why did signal drop?",
        "ids": ["doc-1"],
        "metas": [{"source_file": "radio.log"}],
        "thinking": "thinking",
    }


def test_files_and_metadata_endpoints(client):
    files_response = client.get("/files")
    metadata_response = client.get("/metadata", params={"source_file": "radio.log"})

    assert files_response.status_code == 200
    assert files_response.json() == {"files": ["radio.log"]}
    assert metadata_response.status_code == 200
    assert metadata_response.json() == {
        "ids": ["meta-1"],
        "metadatas": [{"source_file": "radio.log", "log_type": "Signal_Level"}],
    }


def test_knowledge_endpoints(client, fake_engine):
    list_response = client.get("/knowledge")
    save_response = client.post(
        "/knowledge",
        json={
            "target_ids": ["doc-1"],
            "feedback": "Use the known workaround.",
            "severity": "High",
            "build_info": {"model_name": "SM-Test"},
        },
    )

    assert list_response.status_code == 200
    assert list_response.json() == {
        "ids": ["kb-1"],
        "documents": ["known fix"],
        "metadatas": [{"severity": "High"}],
    }
    assert save_response.status_code == 200
    assert save_response.json() == {"success": True}
    assert fake_engine.saved_knowledge == {
        "target_ids": ["doc-1"],
        "feedback": "Use the known workaround.",
        "severity": "High",
        "build_info": {"model_name": "SM-Test"},
    }


def test_result_json_endpoint_reads_supported_artifact(client, tmp_path):
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    artifact_path = result_dir / "radio_report.json"
    artifact_path.write_text(json.dumps({"ok": True}), encoding="utf-8")

    response = client.get("/results/radio/report")
    unsupported_response = client.get("/results/radio/unknown")
    missing_response = client.get("/results/radio/datacall")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert unsupported_response.status_code == 400
    assert missing_response.status_code == 404


def test_create_and_list_analyze_job(client, fake_executor):
    response = client.post(
        "/jobs/analyze",
        files={"files": ("radio.log", b"log body", "text/plain")},
        data={"use_slice": "true", "start_t": "00:00:01", "end_t": "00:00:05"},
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert len(fake_executor.submissions) == 1

    status_response = client.get(f"/jobs/{job_id}")
    list_response = client.get("/jobs")

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "pending"
    assert status_response.json()["progress"] == 0
    assert list_response.status_code == 200
    assert list_response.json()["jobs"][0]["job_id"] == job_id

    uploaded_path = Path(fake_executor.submissions[0][1][1][0])
    assert uploaded_path.name == "radio.log"
    assert uploaded_path.read_bytes() == b"log body"


def test_plm_quick_search_endpoint(client, monkeypatch):
    calls = []

    def fake_quick_search_defects(**kwargs):
        calls.append(kwargs)
        return {
            "success": True,
            "message": "",
            "defects": [{"defectCode": "P260711-001", "plmTitle": "Data stall"}],
            "defect_codes": ["P260711-001"],
            "total_codes": 1,
            "truncated": False,
        }

    monkeypatch.setattr("plm.service.quick_search_defects", fake_quick_search_defects)

    response = client.post(
        "/plm/quick-search",
        json={
            "division_code": "25",
            "main_owner_id": "user.one,user.two",
            "status": "Open",
            "search_type": "main",
            "limit": 25,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "",
        "defects": [{"defectCode": "P260711-001", "plmTitle": "Data stall"}],
        "defect_codes": ["P260711-001"],
        "total_codes": 1,
        "truncated": False,
    }
    assert calls == [
        {
            "division_code": "25",
            "main_owner_id": "user.one,user.two",
            "status": "Open",
            "search_type": "main",
            "limit": 25,
        }
    ]


def test_plm_file_endpoints(client, monkeypatch):
    def fake_list_attached_files(**kwargs):
        return {
            "success": True,
            "message": "",
            "files": [{"title": "radio.zip", "fileId": "file-1", "docId": "doc-1"}],
        }

    def fake_download_attached_file(**kwargs):
        return {
            "success": True,
            "message": "",
            "data": b"zip-bytes",
            "size": 9,
            "filename": kwargs["title"],
        }

    monkeypatch.setattr("plm.service.list_attached_files", fake_list_attached_files)
    monkeypatch.setattr("plm.service.download_attached_file", fake_download_attached_file)

    list_response = client.post(
        "/plm/files",
        json={"division_code": "25", "defect_code": "P260711-001"},
    )
    download_response = client.post(
        "/plm/files/download",
        json={
            "division_code": "25",
            "doc_id": "doc-1",
            "title": "radio.zip",
            "file_id": "file-1",
        },
    )

    assert list_response.status_code == 200
    assert list_response.json()["files"] == [{"title": "radio.zip", "fileId": "file-1", "docId": "doc-1"}]
    assert download_response.status_code == 200
    assert download_response.content == b"zip-bytes"
    assert download_response.headers["X-Filename"] == "radio.zip"
    assert download_response.headers["X-File-Size"] == "9"


def test_plm_comment_and_analyze_endpoints(client, monkeypatch):
    def fake_submit_comment(payload):
        return {"success": True, "message": "ok", "result": {"commentId": "c-1"}}

    def fake_build_context(**kwargs):
        return {
            "success": True,
            "message": "",
            "context": {"defect_code": kwargs["defect_code"], "problem": "Data stall"},
        }

    monkeypatch.setattr("plm.service.submit_comment", fake_submit_comment)
    monkeypatch.setattr("plm.service.build_defect_analysis_context", fake_build_context)

    comment_response = client.post(
        "/plm/comment",
        json={
            "payload": {
                "divisionCode": "25",
                "systemCode": "AI_ANALYSIS",
                "defectCode": "P260711-001",
                "defectComment": "analysis",
                "createUser": "tester",
            }
        },
    )
    analyze_response = client.post(
        "/plm/analyze",
        json={"division_code": "25", "defect_code": "P260711-001"},
    )

    assert comment_response.status_code == 200
    assert comment_response.json() == {"success": True, "message": "ok", "result": {"commentId": "c-1"}}
    assert analyze_response.status_code == 200
    assert analyze_response.json()["context"] == {"defect_code": "P260711-001", "problem": "Data stall"}
