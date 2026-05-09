import argparse
import csv
import json
import os
import sys
import time
import subprocess
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Set


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ril_rag_chat import RilRagChat


DEFAULT_MODELS = [
    "gemma2:2b",
    "gemma3:4b",
    "qwen2.5-coder:7b",
]

MODEL_CONTEXTS = {
    "gemma2:2b": 8192,
    "gemma3:4b": 8192,
    "qwen2.5-coder:7b": 8192,
}

TEST_CASES = [
    {
        "id": "crash_anr_basic",
        "category": "Crash_ANR",
        "query": "Crash/ANR 발생 여부를 분석하고, 발생했다면 원인 후보와 근거 로그를 정리해줘.",
        "expected_tools": ["get_crash_anr_analytics"],
        "expected_log_types": ["Crash_Event", "ANR_Context"],
        "must_include_any": ["ANR", "Crash", "크래시", "응답 없음"],
        "must_not_include": ["근거 없이", "추정됩니다만", "모뎀 로그가 필요"],
    },
    {
        "id": "internet_stall_radio_power",
        "category": "Internet_Stall",
        "query": "Internet Stall 발생 여부를 분석해줘. 비행기 모드 또는 Radio Power OFF 영향이 있는지도 반드시 같이 확인해줘.",
        "expected_tools": [
            "get_internet_stall_analytics",
            "get_radio_power_analytics",
        ],
        "expected_log_types": [
            "Internet_Stall",
            "Radio_Power_Event",
            "Data_Stall_Recovery",
            "Network_DNS_Issue",
        ],
        "must_include_any": ["Internet", "Stall", "Radio", "Power", "비행기"],
        "must_not_include": ["모뎀 로그가 필요", "확인 불가"],
    },
    {
        "id": "oos_radio_power",
        "category": "Network_OOS",
        "query": "OOS 발생 원인을 분석해줘. 단순 망 품질 문제인지, 비행기 모드나 Radio Power OFF로 인한 정상 동작인지 구분해줘.",
        "expected_tools": [
            "get_network_oos_analytics",
            "get_radio_power_analytics",
        ],
        "expected_log_types": ["OOS_Event", "Radio_Power_Event"],
        "must_include_any": ["OOS", "OUT_OF_SERVICE", "Radio", "Power", "비행기"],
        "must_not_include": ["모뎀 로그가 필요"],
    },
    {
        "id": "battery_crash",
        "category": "Battery_Thermal_Crash",
        "query": "Battery/Thermal 상태와 Crash/ANR 발생 여부를 함께 분석해줘. 둘 사이의 시간적 연관성이 있으면 설명해줘.",
        "expected_tools": [
            "get_battery_thermal_analytics",
            "get_crash_anr_analytics",
        ],
        "expected_log_types": [
            "Battery_Drain_Report",
            "Thermal_Stats",
            "Crash_Event",
            "ANR_Context",
        ],
        "must_include_any": ["Battery", "Thermal", "Crash", "ANR", "배터리", "발열"],
        "must_not_include": ["모뎀 로그가 필요"],
    },
    {
        "id": "cs_call_oos",
        "category": "CS_Call",
        "query": "CS Call 실패 또는 Call Drop 여부를 분석하고, 실패 원인과 RF/OOS 연관성을 정리해줘.",
        "expected_tools": [
            "get_cs_call_analytics",
            "get_network_oos_analytics",
        ],
        "expected_log_types": ["Call_Session", "OOS_Event", "Signal_Level"],
        "must_include_any": ["CS", "Call", "Drop", "OOS", "RF"],
        "must_not_include": ["임의의 Cause", "모뎀 로그가 필요"],
    },
    {
        "id": "ps_ims_call",
        "category": "PS_IMS_Call",
        "query": "PS/IMS/VoLTE Call 이벤트를 분석해줘. SIP 또는 IMS 실패 원인이 있으면 함께 설명해줘.",
        "expected_tools": [
            "get_ps_ims_call_analytics",
        ],
        "expected_log_types": ["Call_Session", "IMS_SIP_Message"],
        "must_include_any": ["IMS", "VoLTE", "SIP", "PS", "Call"],
        "must_not_include": ["임의의 SIP 코드", "모뎀 로그가 필요"],
    },
]


def normalize_answer(answer: Any) -> str:
    if answer is None:
        return ""

    if isinstance(answer, str):
        return answer

    try:
        return json.dumps(answer, ensure_ascii=False, indent=2)
    except Exception:
        return str(answer)


def safe_set(values: Optional[List[str]]) -> Set[str]:
    return set(values or [])


def calculate_set_metrics(expected: List[str], actual: List[str]) -> Dict[str, Any]:
    expected_set = safe_set(expected)
    actual_set = safe_set(actual)

    if not expected_set and not actual_set:
        return {
            "recall": 1.0,
            "precision": 1.0,
            "missing": [],
            "extra": [],
        }

    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)

    recall = (
        len(expected_set & actual_set) / len(expected_set)
        if expected_set else 1.0
    )

    precision = (
        len(expected_set & actual_set) / len(actual_set)
        if actual_set else 0.0
    )

    return {
        "recall": round(recall, 3),
        "precision": round(precision, 3),
        "missing": missing,
        "extra": extra,
    }


def simple_answer_score(answer: str, case: Dict[str, Any]) -> Dict[str, Any]:
    answer_lower = answer.lower()

    include_hits = [
        kw for kw in case.get("must_include_any", [])
        if kw.lower() in answer_lower
    ]

    forbidden_hits = [
        kw for kw in case.get("must_not_include", [])
        if kw.lower() in answer_lower
    ]

    include_score = 1 if include_hits else 0
    forbidden_penalty = len(forbidden_hits)

    hallucination_candidate = bool(forbidden_hits)

    auto_score = max(0, include_score - forbidden_penalty)

    return {
        "auto_score": auto_score,
        "include_hits": include_hits,
        "forbidden_hits": forbidden_hits,
        "hallucination_candidate": hallucination_candidate,
    }


def extract_routing_result(rag: RilRagChat, query: str) -> Dict[str, Any]:
    """
    _get_semantic_routing() 결과를 benchmark용으로 정규화.
    프로젝트 코드에서 반환 구조가 tuple/list/dict 중 무엇이든 최대한 대응.
    """
    # result = rag._get_semantic_routing(query)
    if rag.routing_mode == "llm":
        result = rag._get_llm_routing(query)
    elif rag.routing_mode == "hybrid":
        result = rag._get_hybrid_routing(query)
    else:
        result = rag._get_semantic_routing(query)

    tools = []
    log_types = []
    intents = []

    if isinstance(result, dict):
        tools = result.get("tools", []) or result.get("selected_tools", [])
        log_types = result.get("log_types", []) or result.get("selected_log_types", [])
        intents = result.get("intents", []) or result.get("selected_intents", [])

    elif isinstance(result, tuple):
        if len(result) >= 1:
            tools = result[0] or []
        if len(result) >= 2:
            log_types = result[1] or []
        if len(result) >= 3:
            intents = result[2] or []

    elif isinstance(result, list):
        tools = result

    return {
        "raw": str(result),
        "tools": sorted(list(set(tools))),
        "log_types": sorted(list(set(log_types))),
        "intents": sorted(list(set(intents))),
    }


def stop_ollama_model(model: str):
    try:
        subprocess.run(
            ["ollama", "stop", model],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        print(f"[INFO] ollama stop 완료: {model}")
    except Exception as e:
        print(f"[WARN] ollama stop 실패: {model} / {e}")


def load_payload_files(payload_dir: str, selected_files: Optional[List[str]]) -> List[str]:
    if selected_files:
        return selected_files

    files = [
        name for name in os.listdir(payload_dir)
        if name.endswith("_payload.json")
    ]

    return sorted(files)


def maybe_set_context(rag: RilRagChat, model: str, context_size: int):
    """
    프로젝트 RilRagChat 구현에 따라 속성명이 다를 수 있으므로
    가장 흔한 후보를 같이 세팅.
    """
    for attr in ["num_ctx", "context_size", "ollama_num_ctx"]:
        if hasattr(rag, attr):
            setattr(rag, attr, context_size)

    # 혹시 ask() 내부에서 options dict를 쓰는 구조라면 대응
    if hasattr(rag, "ollama_options") and isinstance(rag.ollama_options, dict):
        rag.ollama_options["num_ctx"] = context_size


def run_benchmark(
    models: List[str],
    payload_dir: str,
    payload_files: List[str],
    output_dir: str,
    repeat: int,
    stop_model_each_round: bool,
):
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(output_dir, f"model_benchmark_{timestamp}.csv")
    json_path = os.path.join(output_dir, f"model_benchmark_{timestamp}.json")
    summary_path = os.path.join(output_dir, f"model_benchmark_summary_{timestamp}.csv")

    all_results = []

    for model in models:
        context_size = MODEL_CONTEXTS.get(model, 8192)

        print("\n==============================")
        print(f"MODEL: {model}")
        print(f"CONTEXT: {context_size}")
        print("==============================")

        rag = RilRagChat(model_name=model, routing_mode="llm")
        maybe_set_context(rag, model, context_size)

        try:
            print("[INFO] ingest payload folder...")
            rag.ingest_folder(payload_dir)

            for payload_file in payload_files:
                print(f"\n[FILE] {payload_file}")

                for case in TEST_CASES:
                    for run_idx in range(1, repeat + 1):
                        query = case["query"]
                        print(f"  - {case['id']} / run {run_idx}")

                        routing = {
                            "tools": [],
                            "log_types": [],
                            "intents": [],
                            "raw": "",
                        }

                        routing_error = ""

                        try:
                            routing = extract_routing_result(rag, query)
                        except Exception:
                            routing_error = traceback.format_exc()

                        tool_metrics = calculate_set_metrics(
                            case.get("expected_tools", []),
                            routing.get("tools", []),
                        )

                        log_type_metrics = calculate_set_metrics(
                            case.get("expected_log_types", []),
                            routing.get("log_types", []),
                        )

                        started = time.time()
                        error = ""
                        answer = ""

                        try:
                            raw_answer = rag.ask(
                                user_query=query,
                                current_file=payload_file,
                            )
                            answer = normalize_answer(raw_answer)
                        except Exception:
                            error = traceback.format_exc()
                            answer = ""

                        elapsed = round(time.time() - started, 3)
                        answer_score = simple_answer_score(answer, case)

                        row = {
                            "timestamp": timestamp,
                            "model": model,
                            "context_size": context_size,
                            "payload_file": payload_file,
                            "case_id": case["id"],
                            "category": case["category"],
                            "run_idx": run_idx,
                            "routing_mode": routing.get("routing_mode", getattr(rag, "routing_mode", "semantic")),

                            # performance
                            "latency_sec": elapsed,
                            "error": error,
                            "routing_error": routing_error,

                            # expected vs actual routing
                            "expected_tools": "|".join(case.get("expected_tools", [])),
                            "actual_tools": "|".join(routing.get("tools", [])),
                            "missing_tools": "|".join(tool_metrics["missing"]),
                            "extra_tools": "|".join(tool_metrics["extra"]),
                            "routing_tool_recall": tool_metrics["recall"],
                            "routing_tool_precision": tool_metrics["precision"],

                            "expected_log_types": "|".join(case.get("expected_log_types", [])),
                            "actual_log_types": "|".join(routing.get("log_types", [])),
                            "missing_log_types": "|".join(log_type_metrics["missing"]),
                            "extra_log_types": "|".join(log_type_metrics["extra"]),
                            "routing_log_type_recall": log_type_metrics["recall"],
                            "routing_log_type_precision": log_type_metrics["precision"],

                            "actual_intents": "|".join(routing.get("intents", [])),
                            "routing_raw": routing.get("raw", ""),

                            # answer-level automatic score
                            "auto_score": answer_score["auto_score"],
                            "expected_keywords": "|".join(case.get("must_include_any", [])),
                            "include_hits": "|".join(answer_score["include_hits"]),
                            "forbidden_keywords": "|".join(case.get("must_not_include", [])),
                            "forbidden_hits": "|".join(answer_score["forbidden_hits"]),
                            "hallucination_candidate": answer_score["hallucination_candidate"],

                            # manual evaluation columns
                            "manual_accuracy_score_0_5": "",
                            "manual_rca_score_0_5": "",
                            "manual_temporal_score_0_5": "",
                            "manual_prompt_compliance_0_5": "",
                            "manual_hallucination": "",
                            "manual_comment": "",

                            # raw content
                            "query": query,
                            "answer": answer,
                        }

                        all_results.append(row)

        finally:
            if stop_model_each_round:
                stop_ollama_model(model)

    write_results(csv_path, json_path, all_results)
    write_summary(summary_path, all_results)

    print(f"\n[SAVED] {csv_path}")
    print(f"[SAVED] {json_path}")
    print(f"[SAVED] {summary_path}")


def write_results(csv_path: str, json_path: str, rows: List[Dict[str, Any]]):
    if not rows:
        return

    fieldnames = list(rows[0].keys())

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def write_summary(summary_path: str, rows: List[Dict[str, Any]]):
    summary = {}

    for row in rows:
        model = row["model"]
        s = summary.setdefault(model, {
            "model": model,
            "count": 0,
            "error_count": 0,
            "latency_sum": 0.0,
            "routing_tool_recall_sum": 0.0,
            "routing_tool_precision_sum": 0.0,
            "routing_log_type_recall_sum": 0.0,
            "routing_log_type_precision_sum": 0.0,
            "auto_score_sum": 0.0,
            "hallucination_candidate_count": 0,
        })

        s["count"] += 1
        s["latency_sum"] += float(row["latency_sec"])
        s["routing_tool_recall_sum"] += float(row["routing_tool_recall"])
        s["routing_tool_precision_sum"] += float(row["routing_tool_precision"])
        s["routing_log_type_recall_sum"] += float(row["routing_log_type_recall"])
        s["routing_log_type_precision_sum"] += float(row["routing_log_type_precision"])
        s["auto_score_sum"] += float(row["auto_score"])

        if row["error"] or row["routing_error"]:
            s["error_count"] += 1

        if str(row["hallucination_candidate"]).lower() == "true":
            s["hallucination_candidate_count"] += 1

    summary_rows = []

    for model, s in summary.items():
        count = max(1, s["count"])

        summary_rows.append({
            "model": model,
            "total_cases": s["count"],
            "error_count": s["error_count"],
            "avg_latency_sec": round(s["latency_sum"] / count, 3),
            "avg_routing_tool_recall": round(s["routing_tool_recall_sum"] / count, 3),
            "avg_routing_tool_precision": round(s["routing_tool_precision_sum"] / count, 3),
            "avg_routing_log_type_recall": round(s["routing_log_type_recall_sum"] / count, 3),
            "avg_routing_log_type_precision": round(s["routing_log_type_precision_sum"] / count, 3),
            "avg_auto_score": round(s["auto_score_sum"] / count, 3),
            "hallucination_candidate_count": s["hallucination_candidate_count"],
            "hallucination_candidate_rate": round(
                s["hallucination_candidate_count"] / count,
                3,
            ),
        })

    if not summary_rows:
        return

    with open(summary_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    print("\n===== SUMMARY =====")
    for row in summary_rows:
        print(
            f"{row['model']} | "
            f"avg_latency={row['avg_latency_sec']}s | "
            f"tool_recall={row['avg_routing_tool_recall']} | "
            f"tool_precision={row['avg_routing_tool_precision']} | "
            f"auto_score={row['avg_auto_score']} | "
            f"hallucination_rate={row['hallucination_candidate_rate']}"
        )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help="테스트할 Ollama 모델 목록",
    )

    parser.add_argument(
        "--payload-dir",
        default="./payloads",
        help="payload json 폴더",
    )

    parser.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="테스트할 payload 파일명. 생략하면 payload-dir의 *_payload.json 전체 사용",
    )

    parser.add_argument(
        "--output-dir",
        default="./benchmark_results",
        help="결과 저장 폴더",
    )

    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="동일 테스트 반복 횟수",
    )

    parser.add_argument(
        "--no-stop",
        action="store_true",
        help="모델별 테스트 후 ollama stop을 수행하지 않음",
    )

    args = parser.parse_args()

    payload_files = load_payload_files(args.payload_dir, args.files)

    if not payload_files:
        raise RuntimeError(f"No payload files found in {args.payload_dir}")

    print("[INFO] models:", args.models)
    print("[INFO] payload_files:", payload_files)

    run_benchmark(
        models=args.models,
        payload_dir=args.payload_dir,
        payload_files=payload_files,
        output_dir=args.output_dir,
        repeat=args.repeat,
        stop_model_each_round=not args.no_stop,
    )

if __name__ == "__main__":
    main()
