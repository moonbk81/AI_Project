from tests.routing_score_logger import append_routing_score_log
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ril_rag_chat import RilRagChat

TEST_CASE_PATH = Path(__file__).parent / "routing_test_cases.json"

def load_cases():
    with open(TEST_CASE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def normalize_routing_result(result):
    """
    _get_semantic_routing()의 반환 형식이 dict/tuple/list 중 무엇이든
    테스트에서 비교하기 쉽게 정규화한다.

    기대 정규화 결과:
    {
        "intents": set(),
        "tools": set(),
        "log_types": set(),
        "score": optional
    }
    """

    normalized = {
        "intents": set(),
        "tools": set(),
        "log_types": set(),
        "score": None,
        "raw": result,
    }

    if result is None:
        return normalized

    if isinstance(result, dict):
        for key in ["intent", "selected_intent", "top_intent", "category"]:
            value = result.get(key)
            if isinstance(value, str):
                normalized["intents"].add(value)

        for key in ["intents", "selected_intents", "categories"]:
            value = result.get(key)
            if isinstance(value, list):
                normalized["intents"].update(value)
            elif isinstance(value, set):
                normalized["intents"].update(value)
            elif isinstance(value, str):
                normalized["intents"].add(value)

        for key in ["tools", "selected_tools"]:
            value = result.get(key)
            if isinstance(value, list):
                normalized["tools"].update(value)
            elif isinstance(value, set):
                normalized["tools"].update(value)
            elif isinstance(value, str):
                normalized["tools"].add(value)

        for key in ["log_types", "target_log_types", "selected_log_types"]:
            value = result.get(key)
            if isinstance(value, list):
                normalized["log_types"].update(value)
            elif isinstance(value, set):
                normalized["log_types"].update(value)
            elif isinstance(value, str):
                normalized["log_types"].add(value)

        normalized["score"] = result.get("score") or result.get("confidence") or result.get("top_score")
        return normalized

    if isinstance(result, tuple):
        # 예시 대응:
        # return selected_tools, target_log_types
        # return intent, selected_tools, target_log_types
        # return intents, selected_tools, target_log_types, score
        for item in result:
            if isinstance(item, dict):
                nested = normalize_routing_result(item)
                normalized["intents"].update(nested["intents"])
                normalized["tools"].update(nested["tools"])
                normalized["log_types"].update(nested["log_types"])
            elif isinstance(item, list) or isinstance(item, set):
                values = set(item)
                normalized["tools"].update(v for v in values if isinstance(v, str) and v.startswith("get_"))
                normalized["log_types"].update(
                    v for v in values
                    if isinstance(v, str) and not v.startswith("get_") and "_" in v
                )
            elif isinstance(item, str):
                if item.startswith("get_"):
                    normalized["tools"].add(item)
                elif "_" in item:
                    # intent와 log_type 둘 다 underscore가 있어서 완벽하진 않지만,
                    # 아래 expected 비교에서 어느 쪽이든 잡히게 둘 다 후보로 넣는다.
                    normalized["intents"].add(item)
                    normalized["log_types"].add(item)
        return normalized

    return normalized

@pytest.fixture(scope="session")
def router():
    """
    RilRagChat 초기화가 무거우면 여기서 시간이 좀 걸릴 수 있음.
    bge-m3 모델 로딩과 ChromaDB 초기화가 들어가기 때문.
    """
    return RilRagChat()

@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_semantic_routing(router, case):
    result = router._get_semantic_routing(case["query"])
    routed = normalize_routing_result(result)

    expected_tools = set(case.get("expected_tools", []))
    expected_log_types = set(case.get("expected_log_types", []))
    expected_intents = set(case.get("expected_intents", []))
    allow_partial = case.get("allow_partial", False)

    passed = False
    error_message = ""

    try:
        if allow_partial:
            passed = bool(
                routed["tools"] & expected_tools
                or routed["log_types"] & expected_log_types
                or routed["intents"] & expected_intents
            )

            assert passed, (
                f"\n[PARTIAL FAIL] {case['id']}"
                f"\nQuery: {case['query']}"
                f"\nExpected intents: {expected_intents}"
                f"\nExpected tools: {expected_tools}"
                f"\nExpected log_types: {expected_log_types}"
                f"\nActual: {routed}"
            )

            return

        missing_tools = expected_tools - routed["tools"]
        missing_log_types = expected_log_types - routed["log_types"]

        assert not missing_tools, (
            f"\n[TOOL FAIL] {case['id']}"
            f"\nQuery: {case['query']}"
            f"\nMissing tools: {missing_tools}"
            f"\nActual tools: {routed['tools']}"
            f"\nRaw result: {routed['raw']}"
        )

        assert not missing_log_types, (
            f"\n[LOG_TYPE FAIL] {case['id']}"
            f"\nQuery: {case['query']}"
            f"\nMissing log_types: {missing_log_types}"
            f"\nActual log_types: {routed['log_types']}"
            f"\nRaw result: {routed['raw']}"
        )

        passed = True

    except AssertionError as e:
        error_message = str(e)
        raise

    finally:
        append_routing_score_log(
            suite="strict",
            case_id=case["id"],
            query=case["query"],
            passed=passed,
            raw_result=result,
            routed=routed,
            expected_tools=expected_tools,
            expected_log_types=expected_log_types,
            error_message=error_message,
        )