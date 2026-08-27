import json

import ril_rag_chat
from core.config import PROMPTS, ROUTING_MAP, SYSTEM_PROMPTS
from ril_rag_chat import RilRagChat


def make_engine(monkeypatch):
    engine = RilRagChat.__new__(RilRagChat)
    engine.llm_model_name = "gemma4:12b"
    engine.routing_mode = "semantic"
    engine.routing_map = ROUTING_MAP
    engine.system_role_prompt = SYSTEM_PROMPTS["main_engineer_role"]
    engine.prompts = PROMPTS
    engine.log_guidelines = {
        "Call_Drop_Rule": "CONFIG_SENTINEL_CALL_DROP_RULE",
        "Call_Session": "CONFIG_SENTINEL_CALL_SESSION_TEMPLATE",
    }
    engine.model_config_registry = {"gemma4:12b": {"top_k": 3}}
    engine.collection = object()
    engine.embed_model = object()
    engine.tool_registry = {
        "get_ps_ims_call_analytics": lambda base: "CONFIG_SENTINEL_TOOL_FACT",
    }
    engine._get_past_knowledge_context = lambda query, top_k=2: ""
    engine._get_semantic_routing = lambda query: {
        "intents": ["Call_Analysis"],
        "tools": ["get_ps_ims_call_analytics"],
        "log_types": ["Call_Session"],
    }

    monkeypatch.setattr(
        ril_rag_chat,
        "retrieve_and_rerank",
        lambda **kwargs: {
            "ids": [["doc-1"]],
            "documents": [["CONFIG_SENTINEL_RETRIEVED_DOC"]],
            "metadatas": [[{"log_type": "Call_Session", "time": "12:00:00"}]],
        },
    )
    monkeypatch.setattr(ril_rag_chat.StructuredEventRenderer, "render", lambda results, query: "")
    monkeypatch.setattr(ril_rag_chat, "try_build_guardrail_answer", lambda *args, **kwargs: "")
    return engine


def test_load_config_reads_yaml_sections():
    engine = RilRagChat.__new__(RilRagChat)
    engine._load_config()

    assert "Call_Analysis" in engine.routing_map
    assert "제공된 팩트와 로그 데이터만 기반" in engine.system_role_prompt
    assert "base_persona" in engine.prompts
    assert "Call_Session" in engine.log_guidelines
    assert "Call_Drop_Rule" in engine.log_guidelines


def test_ask_prompt_includes_config_sections(monkeypatch):
    engine = make_engine(monkeypatch)
    captured = {}

    def fake_call_llm(system_prompt, user_query, is_bench=False):
        captured["system_prompt"] = system_prompt
        captured["user_query"] = user_query
        return "answer", "thinking"

    engine._call_llm = fake_call_llm

    answer, ids, metas, thinking = engine.ask("통화 끊김 원인 분석", current_file="radio_payload.json")

    assert answer == "answer"
    assert ids == ["doc-1"]
    assert metas == [{"log_type": "Call_Session", "time": "12:00:00"}]
    assert thinking == "thinking"

    prompt = captured["system_prompt"]
    assert "제공된 팩트와 로그 데이터만 기반" in prompt
    assert "너는 제공된 [분석 대상 로그]만을 엄격하게 신뢰" in prompt
    assert "CONFIG_SENTINEL_CALL_DROP_RULE" in prompt
    assert "CONFIG_SENTINEL_CALL_SESSION_TEMPLATE" in prompt
    assert "CONFIG_SENTINEL_TOOL_FACT" in prompt
    assert "CONFIG_SENTINEL_RETRIEVED_DOC" in prompt
    assert "PLM 코멘트에 그대로 등록해도 어색하지 않은 개발자 코멘트체" in prompt
    assert "내부 라우팅명, intent 이름, 규칙명, 템플릿명은 사용자에게 노출하지 않는다" in prompt
    assert "Call_Analysis" in prompt  # 금지 예시로만 남아 있어야 한다.
    assert captured["user_query"] == "통화 끊김 원인 분석"


def _capture_prompt(engine):
    captured = {}

    def fake_call_llm(system_prompt, user_query, is_bench=False):
        captured["system_prompt"] = system_prompt
        return "answer", "thinking"

    engine._call_llm = fake_call_llm
    return captured


class TestToolFactsReachThePrompt:
    """도구가 돌려준 JSON 구조가 프롬프트까지 도달하는지.

    예전에는 프롬프트용으로 합치고 정제까지 끝낸 문자열을 다시 json.loads 하려
    했다. 그 문자열은 "[도구명 분석 팩트]:" 로 시작하므로 조건이 성립할 수 없어,
    SYSTEM_WTF 발생 횟수 통계 주입이 한 번도 실행되지 않았다.
    """

    def test_system_wtf_counts_are_injected(self, monkeypatch):
        engine = make_engine(monkeypatch)
        engine.tool_registry = {
            "get_ps_ims_call_analytics": lambda base: json.dumps(
                {
                    "status": "OK",
                    "wtf_stats_detailed": {
                        "system_server": {
                            "count": 27,
                            "first_time": "08-26 19:05:04.304",
                            "last_time": "08-26 19:12:53.275",
                            "desc": "무언가",
                        }
                    },
                },
                ensure_ascii=False,
            )
        }
        captured = _capture_prompt(engine)

        engine.ask("system_server 가 몇 번 죽었어?", current_file="radio_payload.json")

        prompt = captured["system_prompt"]
        assert "### [시스템 장애 통계]" in prompt
        assert "system_server" in prompt
        assert "총 27회 발생" in prompt
        assert "08-26 19:05:04.304" in prompt

    def test_plain_text_tool_output_is_harmless(self, monkeypatch):
        engine = make_engine(monkeypatch)  # 기본 도구는 평문을 돌려준다
        captured = _capture_prompt(engine)

        engine.ask("통화 끊김 원인 분석", current_file="radio_payload.json")

        prompt = captured["system_prompt"]
        assert "CONFIG_SENTINEL_TOOL_FACT" in prompt
        assert "### [시스템 장애 통계]" not in prompt

    def test_empty_value_does_not_clobber_an_earlier_tool(self, monkeypatch):
        """여러 도구가 같은 키를 쓸 때, 빈 값이 채워진 값을 덮지 않아야 한다."""
        engine = make_engine(monkeypatch)
        engine._get_semantic_routing = lambda query: {
            "intents": ["Call_Analysis"],
            "tools": ["filled", "empty"],
            "log_types": ["Call_Session"],
        }
        engine.tool_registry = {
            "filled": lambda base: json.dumps(
                {"wtf_stats_detailed": {"system": {"count": 3, "first_time": "a", "last_time": "b"}}}
            ),
            "empty": lambda base: json.dumps({"wtf_stats_detailed": {}}),
        }
        captured = _capture_prompt(engine)

        engine.ask("몇 번 발생했어?", current_file="radio_payload.json")

        assert "총 3회 발생" in captured["system_prompt"]

    def test_guidelines_do_not_read_stale_tool_facts_from_the_engine(self, monkeypatch):
        """동시 질문에서 다른 요청의 구조화 팩트가 섞이면 안 된다."""
        engine = make_engine(monkeypatch)
        engine._temp_tool_facts = {
            "wtf_stats_detailed": {
                "stale_process": {"count": 99, "first_time": "old", "last_time": "old"}
            }
        }
        captured = _capture_prompt(engine)

        engine.ask("통화 끊김 원인 분석", current_file="radio_payload.json")

        assert "stale_process" not in captured["system_prompt"]
        assert "총 99회 발생" not in captured["system_prompt"]


class TestParseToolFact:
    def test_json_string_becomes_a_dict(self):
        assert RilRagChat._parse_tool_fact('{"a": 1}') == {"a": 1}

    def test_dict_passes_through(self):
        assert RilRagChat._parse_tool_fact({"a": 1}) == {"a": 1}

    def test_non_json_and_non_object_return_empty(self):
        assert RilRagChat._parse_tool_fact("매칭된 도구 분석 결과가 없습니다.") == {}
        assert RilRagChat._parse_tool_fact("[1, 2, 3]") == {}
        assert RilRagChat._parse_tool_fact(None) == {}
