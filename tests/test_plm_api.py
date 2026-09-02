"""PLM endpoints a browser talks to: groups, form-shaped writes, attachment jobs."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # 쓰기 동작은 로그인(Knox ID)을 요구한다. 미로그인 거절은 따로 확인한다.
    return TestClient(backend_main.app, headers={"X-Knox-Id": "test.user"})


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
        lambda job_id, paths, *args, **kwargs: analyzed.update(job_id=job_id, paths=list(paths)),
    )

    job_id = backend_main._new_job("test")
    backend_main._run_plm_attachment_job(job_id, "25", "D-1")

    assert [path.rsplit("/", 1)[-1] for path in analyzed["paths"]] == ["dumpstate.log"]
    assert open(analyzed["paths"][0], "rb").read() == b"log body"


def test_only_the_picked_attachments_are_downloaded(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "plm.service.list_attached_files",
        lambda **kwargs: {
            "success": True,
            "files": [
                {"title": "first.zip", "docId": "D", "fileId": "F1"},
                {"title": "second.zip", "docId": "D", "fileId": "F2"},
            ],
        },
    )

    downloaded = []

    def download(**kwargs):
        downloaded.append(kwargs["file_id"])
        return {"success": True, "data": make_zip({"dumpstate.log": b"log body"})}

    monkeypatch.setattr("plm.service.download_attached_file", download)
    monkeypatch.setattr(backend_main, "_run_analyze_job", lambda *args, **kwargs: None)

    job_id = backend_main._new_job("test")
    backend_main._run_plm_attachment_job(job_id, "25", "D-1", ["F2"])

    assert downloaded == ["F2"]


def test_a_picked_attachment_that_is_gone_lands_on_the_job(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "plm.service.list_attached_files",
        lambda **kwargs: {"success": True, "files": [{"title": "a.zip", "docId": "D", "fileId": "F1"}]},
    )
    monkeypatch.setattr(
        "plm.service.download_attached_file", lambda **kwargs: pytest.fail("내려받으면 안 된다")
    )

    job_id = backend_main._new_job("test")
    backend_main._run_plm_attachment_job(job_id, "25", "D-1", ["사라진-파일"])

    job = backend_main._get_job(job_id)
    assert job["status"] == "error" and "찾지 못했습니다" in job["error"]


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
    monkeypatch.setattr(backend_main, "_run_analyze_job", lambda *args, **kwargs: pytest.fail("분석을 시작하면 안 된다"))

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


def _one_attachment(monkeypatch, payload, downloads=None, title="bugreport.zip"):
    monkeypatch.setattr(
        "plm.service.list_attached_files",
        lambda **kwargs: {
            "success": True,
            "files": [{"title": title, "docId": "D", "fileId": "F1"}],
        },
    )

    def download(**kwargs):
        if downloads is not None:
            downloads.append(kwargs["file_id"])
        return {"success": True, "data": payload}

    monkeypatch.setattr("plm.service.download_attached_file", download)


def test_scanning_lists_the_logs_a_user_can_pick(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _one_attachment(monkeypatch, make_zip({
        "dumpstate.log": b"main log",
        "ap_silentlog/SILENT_LOG_01.log": b"a",
        "ap_silentlog/SILENT_LOG_02.log": b"b",
        "screenshot.png": b"img",
    }))

    job_id = backend_main._new_job("test")
    backend_main._run_plm_log_scan_job(job_id, "25", "D-1", ["F1"])

    job = backend_main._get_job(job_id)
    assert job["status"] == "done"
    found = {c["path"]: c["group"] for c in job["log_candidates"]}
    assert found == {
        "dumpstate.log": "",
        "ap_silentlog/SILENT_LOG_01.log": "ap_silentlog",
        "ap_silentlog/SILENT_LOG_02.log": "ap_silentlog",
    }


def test_a_log_attached_without_an_archive_is_offered_as_one_candidate(monkeypatch, tmp_path):
    """압축 없이 dumpState 를 그대로 올린 결함. 열어 볼 안쪽이 없다.

    압축만 후보로 삼던 동안에는 이 결함에서 고를 것이 하나도 없어 분석 버튼까지
    사라졌다. 첨부 자체가 후보 하나이고, 빈 route 가 "이것을 그대로 쓴다" 는 뜻이다.
    """
    monkeypatch.chdir(tmp_path)
    downloads = []
    _one_attachment(monkeypatch, b"log body", downloads, title="dumpState_1787.log")

    job_id = backend_main._new_job("test")
    backend_main._run_plm_log_scan_job(job_id, "25", "D-1", ["F1"])

    job = backend_main._get_job(job_id)
    assert job["status"] == "done"
    assert job["log_candidates"] == [{
        "file_id": "F1",
        "title": "dumpState_1787.log",
        "path": "dumpState_1787.log",
        "route": [],
        "size": 0,
        "group": False,
        "kind": "log",
    }]
    # 안을 훑을 것이 없으니 목록을 만드는 동안 내려받지도 않는다.
    assert downloads == []


def test_a_plain_log_is_written_out_without_being_unpacked(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _one_attachment(monkeypatch, b"whole dumpstate", title="dumpState_1787.log")

    analyzed = {}
    monkeypatch.setattr(
        backend_main,
        "_run_analyze_job",
        lambda job_id, paths, *args, **kwargs: analyzed.update(paths=list(paths)),
    )

    job_id = backend_main._new_job("test")
    backend_main._run_plm_selected_logs_job(
        job_id, "25", "D-1",
        [{"file_id": "F1", "title": "dumpState_1787.log", "route": []}],
    )

    assert [path.rsplit("/", 1)[-1] for path in analyzed["paths"]] == ["dumpState_1787.log"]
    assert open(analyzed["paths"][0], "rb").read() == b"whole dumpstate"


def test_only_the_picked_logs_are_pulled_out_of_the_archive(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _one_attachment(monkeypatch, make_zip({
        "dumpstate.log": b"main log",
        "ap_silentlog/SILENT_LOG_01.log": b"silent one",
    }))

    analyzed = {}
    monkeypatch.setattr(
        backend_main,
        "_run_analyze_job",
        lambda job_id, paths, *args, **kwargs: analyzed.update(paths=list(paths)),
    )

    job_id = backend_main._new_job("test")
    backend_main._run_plm_selected_logs_job(
        job_id, "25", "D-1", [{"file_id": "F1", "route": ["ap_silentlog/SILENT_LOG_01.log"]}]
    )

    assert [path.rsplit("/", 1)[-1] for path in analyzed["paths"]] == ["SILENT_LOG_01.log"]
    assert open(analyzed["paths"][0], "rb").read() == b"silent one"


def test_a_log_that_cannot_be_pulled_out_does_not_stop_the_rest(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _one_attachment(monkeypatch, make_zip({
        "dumpstate.log": b"main log",
        "ap_silentlog/empty.log": b"",
    }))

    analyzed = {}
    monkeypatch.setattr(
        backend_main,
        "_run_analyze_job",
        lambda job_id, paths, *args, **kwargs: analyzed.update(paths=list(paths)),
    )

    job_id = backend_main._new_job("test")
    backend_main._run_plm_selected_logs_job(job_id, "25", "D-1", [
        {"file_id": "F1", "route": ["ap_silentlog/empty.log"]},   # 비어 있음
        {"file_id": "F1", "route": ["ap_silentlog/사라진.log"]},   # 압축에 없음
        {"file_id": "F1", "route": ["dumpstate.log"]},
    ])

    assert [path.rsplit("/", 1)[-1] for path in analyzed["paths"]] == ["dumpstate.log"]
    job = backend_main._get_job(job_id)
    assert job["status"] != "error"
    assert [line.split(" (")[0] for line in job["skipped_logs"]] == ["empty.log", "사라진.log"]


def test_the_job_only_fails_when_no_log_could_be_pulled_out(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _one_attachment(monkeypatch, make_zip({"dumpstate.log": b"main log"}))
    monkeypatch.setattr(backend_main, "_run_analyze_job", lambda *args, **kwargs: pytest.fail("분석을 시작하면 안 된다"))

    job_id = backend_main._new_job("test")
    backend_main._run_plm_selected_logs_job(
        job_id, "25", "D-1", [{"file_id": "F1", "route": ["없는파일.log"]}]
    )

    job = backend_main._get_job(job_id)
    assert job["status"] == "error"
    assert job["skipped_logs"] and "없는파일.log" in job["skipped_logs"][0]


def test_an_attachment_is_downloaded_once_for_both_steps(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    downloads = []
    _one_attachment(monkeypatch, make_zip({"dumpstate.log": b"main log"}), downloads)
    monkeypatch.setattr(backend_main, "_run_analyze_job", lambda *args, **kwargs: None)

    scan = backend_main._new_job("test")
    backend_main._run_plm_log_scan_job(scan, "25", "D-1", ["F1"])
    picked = backend_main._get_job(scan)["log_candidates"][0]

    analyze = backend_main._new_job("test")
    backend_main._run_plm_selected_logs_job(
        analyze, "25", "D-1", [{"file_id": picked["file_id"], "route": picked["route"]}]
    )

    assert downloads == ["F1"]  # 두 단계가 같은 원본을 다시 받지 않는다


def test_picking_logs_takes_the_selected_route(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        backend_main._executor, "submit", lambda fn, *args, **kwargs: seen.update(fn=fn.__name__)
    )

    client.post("/plm/attachments/analyze", json={
        "defect_code": "D-1",
        "logs": [{"file_id": "F1", "route": ["dumpstate.log"]}],
    })

    assert seen["fn"] == "_run_plm_selected_logs_job"


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


def test_the_file_list_says_what_can_be_analyzed(monkeypatch):
    """무엇이 로그인지 아는 것은 LOG_PATTERNS 뿐이고 그 목록은 계속 자란다.

    그 판단을 화면에 복제해 두면 목록이 자랄 때 한쪽만 자라므로, 서버가 붙여 준다.
    """
    monkeypatch.setattr(
        "plm.service.list_attached_files",
        lambda **kwargs: {"success": True, "files": [
            {"title": "logs.zip", "docId": "D", "fileId": "F1"},
            {"title": "dumpState_1787.log", "docId": "D", "fileId": "F2"},
            {"title": "결함내용.txt", "docId": "D", "fileId": "F3"},
        ]},
    )

    body = backend_main.plm_files(
        backend_main.PlmFileListRequest(division_code="25", defect_code="D-1")
    )
    flags = {f["title"]: (f["analyzable"], f["is_archive"]) for f in body.files}

    assert flags == {
        "logs.zip": (True, True),
        "dumpState_1787.log": (True, False),
        "결함내용.txt": (False, False),
    }


def test_a_split_archive_offers_one_row_and_says_how_many_pieces(monkeypatch):
    """`log.7z.001`, `log.7z.002` ... 는 압축 하나가 조각으로 올라온 것이다.

    조각 하나만 열면 압축이 아니므로 첫 조각에만 체크박스를 두고, 몇 조각인지와
    어느 묶음인지 알려 준다 -- 나머지 조각은 그 하나를 고르면 함께 받는다.
    """
    monkeypatch.setattr(
        "plm.service.list_attached_files",
        lambda **kwargs: {"success": True, "files": [
            {"title": "log.7z.002", "docId": "D", "fileId": "F2"},
            {"title": "log.7z.001", "docId": "D", "fileId": "F1"},
            {"title": "log.7z.003", "docId": "D", "fileId": "F3"},
            {"title": "shots.zip", "docId": "D", "fileId": "F4"},
        ]},
    )

    body = backend_main.plm_files(
        backend_main.PlmFileListRequest(division_code="25", defect_code="D-1")
    )
    rows = {f["title"]: f for f in body.files}

    assert rows["log.7z.001"]["analyzable"] is True
    assert rows["log.7z.001"]["multipart_parts"] == 3
    assert rows["log.7z.001"]["multipart_of"] == "log.7z"
    # 목록 순서와 무관하게 번호가 가장 낮은 조각이 첫 조각이다.
    for title in ("log.7z.002", "log.7z.003"):
        assert rows[title]["analyzable"] is False
        assert rows[title]["multipart_of"] == "log.7z"
    assert rows["shots.zip"]["analyzable"] is True
    assert "multipart_of" not in rows["shots.zip"]


def test_a_split_archive_is_assembled_before_it_is_opened(monkeypatch, tmp_path):
    """첫 조각만 골라도 나머지 조각을 목록에서 찾아 이어붙여야 열린다."""
    monkeypatch.chdir(tmp_path)

    archive = make_zip({"dumpstate.log": b"log body"})
    cut = len(archive) // 2 + 1
    chunks = {"log.zip.001": archive[:cut], "log.zip.002": archive[cut:]}
    listing = [
        {"title": title, "docId": "D", "fileId": title} for title in chunks
    ]
    monkeypatch.setattr(
        "plm.service.list_attached_files",
        lambda **kwargs: {"success": True, "files": listing},
    )
    downloaded = []

    def download(**kwargs):
        downloaded.append(kwargs["title"])
        return {"success": True, "data": chunks[kwargs["title"]]}

    monkeypatch.setattr("plm.service.download_attached_file", download)

    analyzed = {}
    monkeypatch.setattr(
        backend_main,
        "_run_analyze_job",
        lambda job_id, paths, *args, **kwargs: analyzed.update(paths=list(paths)),
    )

    # 사용자가 고른 것은 첫 조각 하나뿐이다.
    job_id = backend_main._new_job("test")
    backend_main._run_plm_attachment_job(job_id, "25", "D-1", ["log.zip.001"])

    assert downloaded == ["log.zip.001", "log.zip.002"]
    assert [path.rsplit("/", 1)[-1] for path in analyzed["paths"]] == ["dumpstate.log"]
