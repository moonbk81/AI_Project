from tests.routing_score_logger import append_routing_score_log
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ril_rag_chat import RilRagChat
from tests.test_semantic_routing import normalize_routing_result

TEST_CASE_PATH = Path(__file__).parent / "routing_fuzzy_cases.json"

def load_cases():
    with open(TEST_CASE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

@pytest.fixture(scope="session")
def router():
    return RilRagChat()

@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_semantic_routing_fuzzy(router, case):
    result = router._get_semantic_routing(case["query"])
    routed = normalize_routing_result(result)

    acceptable_tools = set(case.get("acceptable_tools", []))
    acceptable_log_types = set(case.get("acceptable_log_types", []))

    matched_tools = routed["tools"] & acceptable_tools
    matched_log_types = routed["log_types"] & acceptable_log_types

    passed = False
    error_message = ""

    try:
        passed = bool(matched_tools or matched_log_types)

        assert passed, (
            f"\n[FUZZY ROUTING FAIL] {case['id']}"
            f"\nQuery: {case['query']}"
            f"\nAcceptable tools: {acceptable_tools}"
            f"\nAcceptable log_types: {acceptable_log_types}"
            f"\nActual tools: {routed['tools']}"
            f"\nActual log_types: {routed['log_types']}"
            f"\nRaw result: {routed['raw']}"
        )

    except AssertionError as e:
        error_message = str(e)
        raise

    finally:
        append_routing_score_log(
            suite="fuzzy",
            case_id=case["id"],
            query=case["query"],
            passed=passed,
            raw_result=result,
            routed=routed,
            acceptable_tools=acceptable_tools,
            acceptable_log_types=acceptable_log_types,
            error_message=error_message,
        )