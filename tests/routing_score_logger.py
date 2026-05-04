import csv
import json
import os
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "test_reports"
REPORT_DIR.mkdir(exist_ok=True)

RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")

CSV_PATH = REPORT_DIR / f"routing_scores_{RUN_ID}.csv"
JSONL_PATH = REPORT_DIR / f"routing_scores_{RUN_ID}.jsonl"


def extract_scores(raw_result):
    """
    _get_semantic_routing() 결과에서 score 정보를 최대한 뽑는다.

    현재 라우터가 tuple 형태로:
        (tools, log_types)
    만 반환하면 점수는 추출 불가.

    앞으로 라우터가 dict로:
        {
            "intents": [...],
            "tools": [...],
            "log_types": [...],
            "scores": {"Call_Analysis": 0.67, ...},
            "top_matches": [...]
        }
    형태를 반환하면 점수 저장 가능.
    """
    scores = {}

    if isinstance(raw_result, dict):
        if isinstance(raw_result.get("scores"), dict):
            scores.update(raw_result["scores"])

        if isinstance(raw_result.get("routing_scores"), dict):
            scores.update(raw_result["routing_scores"])

        if isinstance(raw_result.get("top_matches"), list):
            for item in raw_result["top_matches"]:
                if isinstance(item, dict):
                    intent = item.get("intent") or item.get("name") or item.get("category")
                    score = item.get("score") or item.get("similarity")
                    if intent is not None and score is not None:
                        scores[intent] = score

    return scores


def get_top_scores(scores):
    sorted_items = sorted(scores.items(), key=lambda item: float(item[1]), reverse=True)

    top1_intent, top1_score = "", ""
    top2_intent, top2_score = "", ""

    if len(sorted_items) >= 1:
        top1_intent, top1_score = sorted_items[0]

    if len(sorted_items) >= 2:
        top2_intent, top2_score = sorted_items[1]

    return top1_intent, top1_score, top2_intent, top2_score


def append_routing_score_log(
    *,
    suite,
    case_id,
    query,
    passed,
    raw_result,
    routed,
    expected_tools=None,
    expected_log_types=None,
    acceptable_tools=None,
    acceptable_log_types=None,
    error_message="",
):
    scores = extract_scores(raw_result)
    top1_intent, top1_score, top2_intent, top2_score = get_top_scores(scores)

    row = {
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "suite": suite,
        "case_id": case_id,
        "query": query,
        "passed": passed,
        "top1_intent": top1_intent,
        "top1_score": top1_score,
        "top2_intent": top2_intent,
        "top2_score": top2_score,
        "actual_tools": json.dumps(sorted(list(routed.get("tools", []))), ensure_ascii=False),
        "actual_log_types": json.dumps(sorted(list(routed.get("log_types", []))), ensure_ascii=False),
        "actual_intents": json.dumps(sorted(list(routed.get("intents", []))), ensure_ascii=False),
        "expected_tools": json.dumps(sorted(list(expected_tools or [])), ensure_ascii=False),
        "expected_log_types": json.dumps(sorted(list(expected_log_types or [])), ensure_ascii=False),
        "acceptable_tools": json.dumps(sorted(list(acceptable_tools or [])), ensure_ascii=False),
        "acceptable_log_types": json.dumps(sorted(list(acceptable_log_types or [])), ensure_ascii=False),
        "error_message": error_message,
        "raw_result": repr(raw_result),
    }

    file_exists = CSV_PATH.exists()

    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)

    with open(JSONL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")