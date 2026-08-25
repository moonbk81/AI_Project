import ril_rag_chat
from core.config import PROMPTS, ROUTING_MAP, SYSTEM_PROMPTS
from ril_rag_chat import RilRagChat


class IdentityMatcher:
    def align_query(self, query):
        return query


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
    engine.golden_matcher = IdentityMatcher()
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


def test_load_config_reads_yaml_sections_without_streamlit():
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
    assert captured["user_query"] == "통화 끊김 원인 분석"
