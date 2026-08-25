"""PLM endpoints a browser talks to: groups, form-shaped writes, attachment jobs."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return TestClient(backend_main.app)


def make_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# ------------------------------------------------------------------- groups


def test_search_groups_come_from_the_plm_config(client, monkeypatch):
    class FakeConfig:
        def get_groups_by_division(self, division_code):
            return {"cp_solution": "RIL2"} if division_code == "25" else {}

        def get_users_for_search(self, group_key):
            return ["a.kim", "b.lee"]

    monkeypatch.setattr("plm.plm_rag_integration.PLMConfigManager", FakeConfig)

    assert client.get("/plm/groups", params={"division_code": "25"}).json() == {
        "groups": {"cp_solution": "RIL2"}
    }
    assert client.get("/plm/groups/cp_solution/users").json() == {
        "group": "cp_solution",
        "users": ["a.kim", "b.lee"],
    }


# -------------------------------------------------------------- form writes


def test_a_comment_can_be_sent_as_typed_text(client, monkeypatch):
    sent = {}
    monkeypatch.setattr("plm.service.submit_comment", lambda payload: sent.update(payload) or {"success": True})

    response = client.post(
        "/plm/comment",
        json={"form": {"division_code": "25", "defect_code": "D-1", "comment": "첫 줄\n둘째 줄", "create_user": "knox"}},
    )

    assert response.status_code == 200
    # The caller never sees PLM's field names, and the line break survives.
    assert sent["defectComment"] == "첫 줄<br>둘째 줄"
    assert sent["isCommentEditorYn"] == "Y"
    assert sent["defectCode"] == "D-1"


def test_an_empty_comment_is_refused_before_it_reaches_plm(client):
    response = client.post("/plm/comment", json={"form": {"defect_code": "D-1", "comment": "  "}})

    assert response.status_code == 400


def test_a_defect_form_is_turned_into_the_plm_body(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(
        "plm.service.register_defect", lambda payload: sent.update(payload) or {"success": True, "result": {}}
    )

    response = client.post(
        "/plm/defects/register",
        json={
            "form": {
                "division_code": "25",
                "title": "통화 끊김",
                "content": "핸드오버 중 끊김",
                "create_user": "knox",
                "importance": "A",
            }
        },
    )

    assert response.status_code == 200
    assert sent["title"] == "통화 끊김" and sent["Content"] == "핸드오버 중 끊김"
    assert sent["importance"] == "A"
    assert sent["externalDefectId"].startswith("AI_")  # generated when not supplied


def test_a_defect_form_missing_a_required_field_is_refused(client):
    response = client.post("/plm/defects/register", json={"form": {"title": "제목만 있음"}})

    assert response.status_code == 400
    assert "문제 내용" in response.json()["detail"]


# --------------------------------------------------------- attachment jobs


def test_attachment_logs_are_extracted_then_handed_to_the_analyzer(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "plm.service.list_attached_files",
        lambda **kwargs: {"success": True, "files": [{"title": "logs.zip", "docId": "D", "fileId": "F"}]},
    )
    monkeypatch.setattr(
        "plm.service.download_attached_file",
        lambda **kwargs: {"success": True, "data": make_zip({"dumpstate.log": b"log body"})},
    )

    analyzed = {}
    monkeypatch.setattr(
        backend_main,
        "_run_analyze_job",
        lambda job_id, paths, *args: analyzed.update(job_id=job_id, paths=list(paths)),
    )

    job_id = backend_main._new_job("test")
    backend_main._run_plm_attachment_job(job_id, "25", "D-1")

    assert [path.rsplit("/", 1)[-1] for path in analyzed["paths"]] == ["dumpstate.log"]
    assert open(analyzed["paths"][0], "rb").read() == b"log body"


def test_an_attachment_without_logs_finishes_without_an_analysis(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "plm.service.list_attached_files",
        lambda **kwargs: {"success": True, "files": [{"title": "shots.zip", "docId": "D", "fileId": "F"}]},
    )
    monkeypatch.setattr(
        "plm.service.download_attached_file",
        lambda **kwargs: {"success": True, "data": make_zip({"screenshot.png": b"img"})},
    )
    monkeypatch.setattr(backend_main, "_run_analyze_job", lambda *args: pytest.fail("분석을 시작하면 안 된다"))

    job_id = backend_main._new_job("test")
    backend_main._run_plm_attachment_job(job_id, "25", "D-1")

    job = backend_main._get_job(job_id)
    assert job["status"] == "done"
    assert "찾지 못했습니다" in job["message"]


def test_a_plm_failure_lands_on_the_job_rather_than_the_request(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "plm.service.list_attached_files", lambda **kwargs: {"success": False, "message": "권한 없음"}
    )

    job_id = backend_main._new_job("test")
    backend_main._run_plm_attachment_job(job_id, "25", "D-1")

    job = backend_main._get_job(job_id)
    assert job["status"] == "error" and job["error"] == "권한 없음"


def test_the_endpoint_answers_with_a_job_to_poll(client, monkeypatch):
    monkeypatch.setattr(backend_main._executor, "submit", lambda *args, **kwargs: None)

    body = client.post("/plm/attachments/analyze", json={"division_code": "25", "defect_code": "D-1"}).json()

    assert len(body["job_id"]) == 32
    assert backend_main._get_job(body["job_id"])["status"] == "pending"
