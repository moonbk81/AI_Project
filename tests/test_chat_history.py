"""이어지는 대화 — 모델이 앞서 무슨 말이 오갔는지 알고 답하는가.

예전에는 화면이 보낸 chat_history 가 검색 쿼리를 넓히는 데만 쓰이고(그것도 질문이
15자 미만일 때만) 모델에게는 전달되지 않았다. 채팅처럼 보였지만 매 질문이 첫
질문이었고, "방금 말한 그 MNR" 같은 후속 질문은 가리키는 것이 없는 채로 답을
받았다.

정리 규칙이 함께 있는 이유는 chat template 이다. Gemma 계열은 user/assistant 가
번갈아 user 로 시작하지 않으면 답변 대신 에러를 낸다 -- 답을 못 받은 질문 하나가
기록에 섞이면 그 다음 질문부터 전부 실패한다.
"""

import pytest

import rag.llm_client as llm_client
from rag.llm_client import MAX_HISTORY_MESSAGES, build_history


def turn(question, answer):
    return [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]


class TestBuildHistory:
    def test_주고받은_대화는_그대로_실린다(self):
        history = turn("왜 끊겼어?", "MNR 이후 CP crash 입니다.")

        assert build_history(history) == history

    def test_기록이_없으면_비어_있다(self):
        assert build_history(None) == []
        assert build_history([]) == []

    def test_답을_못_받은_질문은_빠진다(self):
        # 빈 assistant 를 그대로 실으면 user 가 잇달아 두 번 나온다.
        history = [
            {"role": "user", "content": "왜 끊겼어?"},
            {"role": "assistant", "content": ""},
            *turn("OOS 는?", "18:02 에 한 번 있습니다."),
        ]

        assert build_history(history) == turn("OOS 는?", "18:02 에 한 번 있습니다.")

    def test_기록은_user_로_시작해_assistant_로_끝난다(self):
        # 앞이 잘려 답부터 시작하거나, 뒤에 답 없는 질문이 달려 있을 수 있다.
        history = [
            {"role": "assistant", "content": "앞 질문이 잘린 답"},
            *turn("OOS 는?", "18:02 에 한 번 있습니다."),
            {"role": "user", "content": "아직 답을 못 받은 질문"},
        ]

        assert build_history(history) == turn("OOS 는?", "18:02 에 한 번 있습니다.")

    def test_오래된_대화는_잘라도_짝이_맞는다(self):
        history = [msg for index in range(20) for msg in turn(f"q{index}", f"a{index}")]

        built = build_history(history)

        assert len(built) <= MAX_HISTORY_MESSAGES
        assert built[0]["role"] == "user"
        assert built[-1]["role"] == "assistant"
        # 자르는 쪽은 앞이다. 방금 오간 말이 남아야 한다.
        assert built[-1]["content"] == "a19"

    def test_모양이_이상한_기록은_건너뛴다(self):
        history = ["문자열", None, {"content": "역할이 없다"}, {"role": "system", "content": "끼어든 지시"},
                   *turn("OOS 는?", "18:02 에 한 번 있습니다.")]

        assert build_history(history) == turn("OOS 는?", "18:02 에 한 번 있습니다.")


def test_지난_대화가_시스템_프롬프트와_이번_질문_사이에_들어간다(monkeypatch):
    captured = {}

    def fake_chat(model, messages, options, think):
        captured["messages"] = messages
        return {"message": {"content": "답", "reasoning": ""}}

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    llm_client.call_llm(
        system_prompt="[검색된 로그] ...",
        user_query="그 MNR 은 몇 시에 났어?",
        model_name="gemma4:12b",
        model_config_registry={"gemma4:12b": {}},
        chat_history=turn("왜 끊겼어?", "MNR 이후 CP crash 입니다."),
    )

    assert [msg["role"] for msg in captured["messages"]] == ["system", "user", "assistant", "user"]
    assert captured["messages"][0]["content"].startswith("[검색된 로그]")
    assert captured["messages"][1]["content"] == "왜 끊겼어?"
    assert captured["messages"][2]["content"] == "MNR 이후 CP crash 입니다."
    assert captured["messages"][-1]["content"] == "그 MNR 은 몇 시에 났어?"


def test_기록이_없으면_예전과_같은_두_메시지다(monkeypatch):
    captured = {}
    monkeypatch.setattr(llm_client, "chat",
                        lambda model, messages, options, think: captured.update(messages=messages)
                        or {"message": {"content": "답", "reasoning": ""}})

    llm_client.call_llm(
        system_prompt="시스템",
        user_query="왜 끊겼어?",
        model_name="gemma4:12b",
        model_config_registry={"gemma4:12b": {}},
    )

    assert [msg["role"] for msg in captured["messages"]] == ["system", "user"]


def test_엔진은_화면이_보낸_대화를_그대로_넘긴다(monkeypatch):
    """ask() 가 chat_history 를 _call_llm 까지 들고 간다 -- 끊기면 아무도 모른다."""
    from tests.test_prompt_config_flow import make_engine

    engine = make_engine(monkeypatch)
    captured = {}

    def fake_call_llm(system_prompt, user_query, is_bench=False, chat_history=None):
        captured["chat_history"] = chat_history
        return "answer", ""

    engine._call_llm = fake_call_llm
    history = turn("왜 끊겼어?", "MNR 이후 CP crash 입니다.")

    engine.ask("그 MNR 은 몇 시에 났어?", current_file="radio_payload.json", chat_history=history)

    assert captured["chat_history"] == history


def test_짧은_후속_질문은_검색까지_앞_질문으로_넓힌다(monkeypatch):
    """LLM 에 대화를 넘겨도 검색 쿼리 보정은 그대로 남아야 한다.

    "왜?" 한 마디로는 임베딩이 아무 로그에도 가까이 가지 못한다.
    """
    from tests.test_prompt_config_flow import make_engine

    import ril_rag_chat

    engine = make_engine(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        ril_rag_chat,
        "retrieve_and_rerank",
        lambda **kwargs: captured.update(search_query=kwargs["search_query"])
        or {"ids": [["doc-1"]], "documents": [["doc"]], "metadatas": [[{"log_type": "Call_Session"}]]},
    )
    engine._call_llm = lambda **kwargs: ("answer", "")

    engine.ask("왜?", current_file="radio_payload.json",
               chat_history=turn("통화가 왜 끊겼어?", "MNR 입니다."))

    assert captured["search_query"] == "통화가 왜 끊겼어? 관련 후속 질문: 왜?"
