"""여럿이 한 서버를 함께 쓸 때의 규칙: 무엇이 나란히 돌고 무엇이 줄을 서는가."""

import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return TestClient(backend_main.app)


# ------------------------------------------------------------- 분석은 한 줄로


def test_analyses_never_run_at_the_same_time(monkeypatch, tmp_path):
    """GPU 가 하나뿐이라 분석 본체는 겹치면 안 된다."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backend_main, "get_engine", lambda: None)

    inside = []
    peak = []

    def fake_core(paths, **kwargs):
        inside.append(paths)
        peak.append(len(inside))
        time.sleep(0.05)
        inside.pop()
        return SimpleNamespace(current_file="f.log", report_path=None, payload_path=None)

    monkeypatch.setattr("core.analysis_pipeline.run_analysis_core", fake_core)

    jobs = [backend_main._new_job("t") for _ in range(3)]
    threads = [
        threading.Thread(target=backend_main._run_analyze_job, args=(job, ["a.log"], False, "", ""))
        for job in jobs
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert max(peak) == 1  # 셋이 동시에 시작해도 한 번에 하나
    assert [backend_main._get_job(job)["status"] for job in jobs] == ["done"] * 3


def test_a_queued_analysis_says_it_is_waiting(monkeypatch, tmp_path):
    """말없이 멈춰 있으면 사용자는 서버가 죽은 줄 안다."""
    monkeypatch.chdir(tmp_path)
    job_id = backend_main._new_job("t")
    entered = threading.Event()

    backend_main._ANALYSIS_SLOT.acquire()  # 다른 사람이 쓰는 중
    worker = threading.Thread(target=lambda: _enter_slot(job_id, entered))
    try:
        worker.start()
        deadline = time.time() + 3
        while time.time() < deadline and "기다리는" not in backend_main._get_job(job_id)["message"]:
            time.sleep(0.02)

        assert "기다리는" in backend_main._get_job(job_id)["message"]
        assert not entered.is_set()
    finally:
        backend_main._ANALYSIS_SLOT.release()

    worker.join(timeout=3)
    assert entered.is_set()  # 자리가 나면 이어서 돈다


def _enter_slot(job_id, entered):
    with backend_main._analysis_slot(job_id):
        entered.set()


def test_attachment_jobs_do_not_wait_behind_an_analysis():
    """내려받기·압축 해제는 분석과 다른 실행기에서 돈다."""
    assert backend_main._executor is not backend_main._analysis_executor
    assert backend_main._executor._max_workers > 1


# --------------------------------------------------------------- 질문 동시 실행


def test_questions_are_turned_away_when_every_slot_is_taken(client, monkeypatch):
    monkeypatch.setattr(backend_main, "_ASK_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(
        backend_main, "get_engine", lambda: pytest.fail("자리가 없으면 엔진을 건드리면 안 된다")
    )

    taken = 0
    while backend_main._ASK_SLOT.acquire(blocking=False):
        taken += 1
    try:
        response = client.post("/ask", json={"question": "왜 끊겼나"})
    finally:
        for _ in range(taken):
            backend_main._ASK_SLOT.release()

    assert response.status_code == 503
    assert "잠시 후" in response.json()["detail"]


def test_the_queue_lengths_are_visible_on_health(client):
    body = client.get("/health").json()

    assert body["analysis_queue"] == 0 and body["ask_queue"] == 0


# ------------------------------------------------------------------- 업로드


def test_a_large_upload_is_written_whole_without_being_held_in_memory(client, monkeypatch):
    submitted = {}
    monkeypatch.setattr(
        backend_main._analysis_executor,
        "submit",
        lambda fn, job_id, paths, *args: submitted.update(paths=list(paths)),
    )

    payload = b"x" * (backend_main.UPLOAD_CHUNK_BYTES * 2 + 7)  # 청크 경계를 넘긴다
    response = client.post("/jobs/analyze", files={"files": ("big.log", payload, "text/plain")})

    assert response.status_code == 200
    with open(submitted["paths"][0], "rb") as handle:
        assert handle.read() == payload
