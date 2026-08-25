"""The OpenAI-compatible surface Open WebUI talks to."""

import json

import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
from backend.openai_api import message_text, split_conversation, target_file


class FakeEngine:
    def __init__(self, files=("radio.log", "boot.log"), thinking="생각 과정"):
        self.files = list(files)
        self.thinking = thinking
        self.asked = None

    def ask(self, question, **kwargs):
        self.asked = {"question": question, **kwargs}
        return (f"answer: {question}", ["doc-1"], [{}], self.thinking)

    def get_all_files(self):
        return self.files


@pytest.fixture()
def fake_engine():
    return FakeEngine()


@pytest.fixture()
def client(monkeypatch, fake_engine):
    monkeypatch.setattr(backend_main, "_engine", fake_engine)
    return TestClient(backend_main.app)


# ------------------------------------------------------------------- helpers


def test_model_id_carries_the_log_to_answer_about():
    assert target_file("ril-rag:radio.log") == "radio.log"
    assert target_file("ril-rag") is None
    assert target_file("ril-rag:") is None


def test_content_may_arrive_as_typed_parts():
    assert message_text("plain") == "plain"
    assert message_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"
    assert message_text([{"type": "image_url", "image_url": {}}]) == ""
    assert message_text(None) == ""


def test_the_last_user_message_is_the_question():
    from backend.openai_api import ChatMessage

    question, history = split_conversation(
        [
            ChatMessage(role="system", content="프롬프트는 이 프로젝트가 만든다"),
            ChatMessage(role="user", content="첫 질문"),
            ChatMessage(role="assistant", content="이전 답"),
            ChatMessage(role="user", content="후속 질문"),
        ]
    )

    assert question == "후속 질문"
    # The system turn is dropped, the rest becomes history.
    assert history == [
        {"role": "user", "content": "첫 질문"},
        {"role": "assistant", "content": "이전 답"},
    ]


# -------------------------------------------------------------------- models


def test_every_ingested_log_shows_up_as_a_model(client):
    models = [entry["id"] for entry in client.get("/v1/models").json()["data"]]

    assert models == ["ril-rag", "ril-rag:radio.log", "ril-rag:boot.log"]


def test_an_unreadable_database_still_lists_the_generic_model(client, monkeypatch):
    def boom():
        raise RuntimeError("chroma down")

    monkeypatch.setattr(backend_main._engine, "get_all_files", boom)

    assert [entry["id"] for entry in client.get("/v1/models").json()["data"]] == ["ril-rag"]


# --------------------------------------------------------------- completions


def test_completion_answers_the_last_user_message_about_the_chosen_log(client, fake_engine):
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ril-rag:radio.log",
            "messages": [
                {"role": "user", "content": "첫 질문"},
                {"role": "assistant", "content": "이전 답"},
                {"role": "user", "content": [{"type": "text", "text": "RSRP 왜 떨어졌어?"}]},
            ],
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert "answer: RSRP 왜 떨어졌어?" in body["choices"][0]["message"]["content"]
    assert fake_engine.asked["current_file"] == "radio.log"
    assert len(fake_engine.asked["chat_history"]) == 2


def test_the_generic_model_asks_across_every_log(client, fake_engine):
    client.post("/v1/chat/completions", json={"model": "ril-rag", "messages": [{"role": "user", "content": "요약"}]})

    assert fake_engine.asked["current_file"] is None
    assert fake_engine.asked["chat_history"] is None


def test_reasoning_is_folded_into_a_think_block(client):
    body = client.post(
        "/v1/chat/completions", json={"messages": [{"role": "user", "content": "왜?"}]}
    ).json()

    content = body["choices"][0]["message"]["content"]
    assert content.startswith("<think>\n생각 과정\n</think>")
    assert content.endswith("answer: 왜?")


def test_an_answer_without_reasoning_is_returned_as_is(monkeypatch, client):
    monkeypatch.setattr(backend_main._engine, "thinking", "")

    body = client.post(
        "/v1/chat/completions", json={"messages": [{"role": "user", "content": "왜?"}]}
    ).json()

    assert body["choices"][0]["message"]["content"] == "answer: 왜?"


def test_streaming_sends_the_answer_then_a_done_marker(client):
    response = client.post(
        "/v1/chat/completions",
        json={"model": "ril-rag", "stream": True, "messages": [{"role": "user", "content": "요약"}]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    chunks = [line[len("data: "):] for line in response.text.splitlines() if line.startswith("data: ")]
    assert chunks[-1] == "[DONE]"

    payloads = [json.loads(chunk) for chunk in chunks[:-1]]
    assert payloads[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert "answer: 요약" in payloads[1]["choices"][0]["delta"]["content"]
    assert payloads[-1]["choices"][0]["finish_reason"] == "stop"
    # Every chunk of one response shares its id.
    assert len({payload["id"] for payload in payloads}) == 1


def test_a_conversation_with_no_user_turn_does_not_crash(client, fake_engine):
    response = client.post(
        "/v1/chat/completions", json={"messages": [{"role": "system", "content": "안녕"}]}
    )

    assert response.status_code == 200
    assert fake_engine.asked["question"] == ""
