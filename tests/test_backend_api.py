import json
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor

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
    # 잡은 실행기 둘로 나뉜다: 앞단(내려받기 등)과 분석 본체.
    monkeypatch.setattr(backend_main, "_executor", fake_executor)
    monkeypatch.setattr(backend_main, "_analysis_executor", fake_executor)
    with backend_main._jobs_lock:
        backend_main._jobs.clear()
    # 쓰기 동작은 로그인(Knox ID)을 요구한다. 미로그인 거절은 따로 확인한다.
    return TestClient(backend_main.app, headers={"X-Knox-Id": "test.user"})


def test_get_engine_initializes_once_under_concurrent_requests(monkeypatch):
    created = []

    class FakeRilRagChat:
        def __init__(self, model_name):
            time.sleep(0.05)
            self.model_name = model_name
            created.append(self)

    monkeypatch.setattr(backend_main, "_engine", None)
    monkeypatch.setitem(sys.modules, "ril_rag_chat", type("FakeModule", (), {"RilRagChat": FakeRilRagChat}))

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            engines = list(executor.map(lambda _: backend_main.get_engine(), range(4)))
    finally:
        backend_main._engine = None

    assert len(created) == 1
    assert all(engine is created[0] for engine in engines)


def test_health_reports_backend_status(client):
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["engine_loaded"] is True
    assert data["engine_status"] == "loaded"
    assert data["engine_initializing"] is False
    assert data["chroma_db_path"] == "./chroma_db"
    assert "provider" in data
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
    body = response.json()
    assert {key: body[key] for key in ("answer", "ids", "metas", "thinking")} == {
        "answer": "answer: why did signal drop?",
        "ids": ["doc-1"],
        "metas": [{"source_file": "radio.log"}],
        "thinking": "thinking",
    }
    # Every UI gets the retrieved rows already shaped into reference blocks.
    assert [block["index"] for block in body["references"]] == [1]


def test_files_and_metadata_endpoints(client):
    files_response = client.get("/files")
    metadata_response = client.get("/metadata", params={"source_file": "radio.log"})

    assert files_response.status_code == 200
    assert files_response.json() == {"files": ["radio.log"], "uploaded_by": {}, "defect_code": {}}
    assert metadata_response.status_code == 200
    assert metadata_response.json() == {
        "ids": ["meta-1"],
        "metadatas": [{"source_file": "radio.log", "log_type": "Signal_Level"}],
    }


def test_files_endpoint_does_not_wake_the_full_rag_engine(monkeypatch):
    class FakeCollection:
        def get(self, **kwargs):
            return {"ids": ["1"], "metadatas": [{"source_file": "fast.log"}]}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def get_or_create_collection(self, name, **kwargs):
            assert name == "ril_logs"
            # 실제 chroma 는 configuration 인자를 받는다. HNSW 설정을 넘기는 쪽이
            # 깨지지 않게 가짜도 같은 모양을 유지한다.
            return FakeCollection()

    fake_chromadb = type("FakeChromaDb", (), {"PersistentClient": FakeClient})
    fake_config = type("FakeConfig", (), {"Settings": lambda **kwargs: kwargs})

    monkeypatch.setattr(backend_main, "_engine", None)
    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb)
    monkeypatch.setitem(sys.modules, "chromadb.config", fake_config)
    monkeypatch.setattr(backend_main, "get_engine", lambda: pytest.fail("full engine should stay cold"))

    response = TestClient(backend_main.app).get("/files")

    assert response.status_code == 200
    assert response.json() == {"files": ["fast.log"], "uploaded_by": {}, "defect_code": {}}
    assert backend_main._engine is None


def test_dashboard_kpi_does_not_wake_the_full_rag_engine(monkeypatch):
    class FakeCollection:
        def get(self, **kwargs):
            return {"ids": ["1"], "metadatas": [{"source_file": "fast.log", "log_type": "Call_Session", "status": "SUCCESS"}]}

    monkeypatch.setattr(backend_main, "_engine", None)
    monkeypatch.setattr(backend_main, "_metadata_collection", lambda: FakeCollection())
    monkeypatch.setattr(backend_main, "get_engine", lambda: pytest.fail("full engine should stay cold"))

    response = TestClient(backend_main.app).get("/dashboard/kpi", params={"source_file": "fast.log"})

    assert response.status_code == 200
    assert backend_main._engine is None


def test_quick_prompts_endpoint_uses_config(client):
    response = client.get("/quick-prompts")

    assert response.status_code == 200
    prompts = response.json()["prompts"]
    assert prompts["call_drop"].strip() == "통화 끊김(Call Drop) 발생 원인을 상세히 분석해 줘."
    assert prompts["internet_stall_analysis"].strip() == "인터넷 멈춤(Data Stall) 현상의 근본 원인을 분석해 줘."


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
    listed = list_response.json()
    assert {key: listed[key] for key in ("ids", "documents", "metadatas")} == {
        "ids": ["kb-1"],
        "documents": ["known fix"],
        "metadatas": [{"severity": "High"}],
    }
    # The rows also come back shaped into cases, with their filter values.
    assert listed["cases"][0]["note"] == "known fix"
    assert listed["cases"][0]["severity"] == "High"
    assert listed["filters"]["severity"] == ["High"]
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


def test_plm_defect_detail_endpoint(client, monkeypatch):
    calls = []

    def fake_get_defect_details(**kwargs):
        calls.append(kwargs)
        return {
            "success": True,
            "message": "",
            "defects": [{"defectCode": "P260711-001", "plmTitle": "Data stall"}],
        }

    monkeypatch.setattr("plm.service.get_defect_details", fake_get_defect_details)

    response = client.post(
        "/plm/defects",
        json={
            "division_code": "25",
            "defect_codes": ["P260711-001"],
            "defect_ids": None,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "",
        "defects": [{"defectCode": "P260711-001", "plmTitle": "Data stall"}],
    }
    assert calls == [
        {
            "division_code": "25",
            "defect_codes": ["P260711-001"],
            "defect_ids": None,
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
    comment_body = comment_response.json()
    assert comment_body["success"] is True and comment_body["message"] == "ok"
    assert comment_body["result"]["commentId"] == "c-1"
    # The response echoes the body that was registered, so a UI can show it.
    assert comment_body["result"]["defectComment"] == "analysis"
    assert analyze_response.status_code == 200
    assert analyze_response.json()["context"] == {"defect_code": "P260711-001", "problem": "Data stall"}


def test_plm_register_and_human_comments_endpoints(client, monkeypatch):
    def fake_register_defect(payload):
        return {
            "success": True,
            "message": "ok",
            "result": {"defectCode": payload["externalDefectId"], "defectId": "id-1"},
        }

    def fake_get_human_comments(**kwargs):
        return {
            "success": True,
            "message": "",
            "comments": [{"comment": "please check modem logs", "commentId": "c-1"}],
        }

    monkeypatch.setattr("plm.service.register_defect", fake_register_defect)
    monkeypatch.setattr("plm.service.get_human_comments", fake_get_human_comments)

    payload = {
        "divisionCode": "25",
        "systemCode": "AI_ANALYSIS",
        "externalDefectId": "AI-1",
        "title": "Data stall",
        "createUser": "tester",
        "Content": "Packet data stalled",
    }
    register_response = client.post("/plm/defects/register", json={"payload": payload})
    comments_response = client.post(
        "/plm/defect-history/comments",
        json={"division_code": "25", "defect_code": "P260711-001"},
    )

    assert register_response.status_code == 200
    assert register_response.json()["result"] == {"defectCode": "AI-1", "defectId": "id-1"}
    assert comments_response.status_code == 200
    assert comments_response.json()["comments"] == [{"comment": "please check modem logs", "commentId": "c-1"}]


def test_a_case_can_be_filed_from_an_answers_retrieved_rows(client, fake_engine):
    """The caller sends what it has — ids and metas — not PLM-shaped fields."""
    response = client.post(
        "/knowledge",
        json={
            "feedback": "Radio 펌웨어 업데이트 필요",
            "severity": "Critical",
            "category": "Call_Session",
            "ids": ["doc-1", "doc-2"],
            "metas": [
                {"log_type": "Call_Session", "model_name": "SM-S921N", "radio": "R1"},
                {"log_type": "Signal_Level"},
            ],
        },
    )

    assert response.status_code == 200
    saved = fake_engine.saved_knowledge
    # Only the rows of the chosen log type, and the build read off the metadata.
    assert saved["target_ids"] == ["doc-1"]
    assert saved["build_info"]["model_name"] == "SM-S921N"
    assert saved["build_info"]["kernel"] == "Unknown"


def test_a_case_with_no_matching_rows_is_refused(client):
    response = client.post(
        "/knowledge",
        json={"feedback": "메모", "category": "OOS_Event", "ids": ["doc-1"], "metas": [{"log_type": "Call_Session"}]},
    )

    assert response.status_code == 400
