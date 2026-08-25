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


# ------------------------------------------------------- chat analysis query


def test_the_analysis_query_carries_the_refined_problem_and_picked_comments(client, monkeypatch):
    monkeypatch.setattr(
        "plm.service.get_defect_details",
        lambda **kwargs: {
            "success": True,
            "defects": [
                {
                    "defectCode": "P-1",
                    "plmTitle": "통화 끊김",
                    "content": "아주 길고 장황한 원본 설명",
                    "reason": "핸드오버 실패",
                    "plmStatus": "Open",
                    "mainOwnerName": "moon",
                }
            ],
        },
    )
    monkeypatch.setattr("plm.service.refine_problem_description", lambda content: "핸드오버 중 통화 끊김")

    body = client.post(
        "/plm/analysis-query",
        json={
            "division_code": "25",
            "defect_code": "P-1",
            "comments": [{"user": "kim", "date": "08-01", "text": "재현됨"}],
        },
    ).json()

    assert body["success"] is True
    assert body["refined_content"] == "핸드오버 중 통화 끊김"
    assert body["original_content"] == "아주 길고 장황한 원본 설명"
    # The question asks about the refined text, names the defect, and includes
    # the comment the user ticked.
    assert "핸드오버 중 통화 끊김" in body["query"]
    assert "P-1" in body["query"] and "통화 끊김" in body["query"]
    assert "재현됨" in body["query"] and "개발자 코멘트" in body["query"]


def test_a_defect_that_cannot_be_read_reports_why(client, monkeypatch):
    monkeypatch.setattr(
        "plm.service.get_defect_details", lambda **kwargs: {"success": False, "message": "권한 없음", "defects": []}
    )

    body = client.post("/plm/analysis-query", json={"defect_code": "P-1"}).json()

    assert body["success"] is False and body["message"] == "권한 없음"


# --------------------------------------------------------- 로컬 테스트 모드


@pytest.fixture()
def offline():
    """PLM 에 닿지 않는 환경을 흉내낸다."""
    from plm import local_test

    before = local_test.is_enabled()
    local_test.set_enabled(True)
    yield local_test
    local_test.set_enabled(before)


def test_the_mode_can_be_read_and_flipped_without_a_restart(client):
    from plm import local_test

    before = local_test.is_enabled()
    try:
        assert client.post("/plm/local-test", json={"enabled": True}).json()["enabled"] is True
        assert client.get("/plm/local-test").json()["enabled"] is True
        assert client.post("/plm/local-test", json={"enabled": False}).json()["enabled"] is False
    finally:
        local_test.set_enabled(before)


def test_offline_search_answers_from_samples(client, offline):
    body = client.post(
        "/plm/quick-search", json={"division_code": "25", "main_owner_id": "anyone", "status": "open"}
    ).json()

    assert body["success"] is True
    assert [defect["defectCode"] for defect in body["defects"]] == ["P260711-LOCAL01"]


def test_offline_attachments_can_still_be_extracted(offline):
    """The sample ZIP has to survive the real extraction pipeline."""
    from core.log_archive import extract_logs_from_archive
    from plm.service import download_attached_file, list_attached_files

    files = list_attached_files(division_code="25", defect_code="P260711-LOCAL01")["files"]
    archive = next(file for file in files if file["title"].endswith(".zip"))
    payload = download_attached_file(division_code="25", doc_id="d", title=archive["title"], file_id="f")

    assert list(extract_logs_from_archive(payload["data"])) == ["dumpstate.log"]


def test_offline_writes_are_accepted_but_never_sent(client, offline):
    body = client.post(
        "/plm/comment",
        json={"form": {"defect_code": "P260711-LOCAL01", "comment": "확인함", "create_user": "knox"}},
    ).json()

    assert body["success"] is True
    assert "전송하지 않았습니다" in body["message"]


def test_offline_comments_and_analysis_have_something_to_show(client, offline):
    comments = client.post(
        "/plm/defect-history/comments", json={"defect_code": "P260711-LOCAL01"}
    ).json()["comments"]
    context = client.post("/plm/analyze", json={"defect_code": "P260711-LOCAL01"}).json()["context"]

    assert len(comments) == 2
    assert context["title"].startswith("IMS registration")


# ------------------------------------------- 채팅 답변을 코멘트로 등록


def test_a_chat_answer_is_registered_under_this_tools_header(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(
        "plm.service.submit_comment", lambda payload: sent.update(payload) or {"success": True, "message": ""}
    )

    body = client.post(
        "/plm/comment",
        json={
            "form": {
                "division_code": "25",
                "defect_code": "P-1",
                "create_user": "knox",
                "answer": "핸드오버 직후 재등록이 반복됩니다.\n- 타이머 미동기",
            }
        },
    ).json()

    assert body["success"] is True
    # The header is what is_ai_generated_comment() looks for when these come back.
    assert sent["defectComment"].startswith("💬 <b>AI Chat 분석 결과</b>")
    assert "<br>- 타이머 미동기" in sent["defectComment"]
    # The caller gets back exactly what was registered.
    assert body["result"]["defectComment"] == sent["defectComment"]


def test_an_answer_registered_this_way_is_recognised_as_ours_later(client, monkeypatch):
    """Otherwise our own comments come back as 'developer input' next time."""
    from plm.comments import is_ai_generated_comment

    sent = {}
    monkeypatch.setattr(
        "plm.service.submit_comment", lambda payload: sent.update(payload) or {"success": True, "message": ""}
    )

    client.post(
        "/plm/comment",
        json={"form": {"defect_code": "P-1", "create_user": "knox", "answer": "분석 결과"}},
    )

    assert is_ai_generated_comment(sent["defectComment"])


def test_registering_an_answer_without_a_knox_id_is_refused(client):
    response = client.post("/plm/comment", json={"form": {"defect_code": "P-1", "answer": "분석 결과"}})

    assert response.status_code == 400
    assert "Knox ID" in response.json()["detail"]
