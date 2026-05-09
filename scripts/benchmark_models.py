import argparse
import csv
import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List

from ril_rag_chat import RilRagChat


DEFAULT_MODELS = [
    "llama3.1:8b",
    "qwen2.5:7b",
    "gemma2:9b",
]


TEST_CASES = [
    {
        "id": "crash_anr_basic",
        "category": "Crash_ANR",
        "query": "Crash/ANR 발생 여부를 분석하고, 발생했다면 원인 후보와 근거 로그를 정리해줘.",
        "must_include_any": ["ANR", "Crash", "크래시", "응답"],
        "must_not_include": ["ANR 없음", "발생하지 않았"],
    },
    {
        "id": "internet_stall_radio_power",
        "category": "Internet_Stall",
        "query": "Internet Stall 발생 여부를 분석해줘. 비행기 모드 또는 Radio Power OFF 영향이 있는지도 반드시 같이 확인해줘.",
        "must_include_any": ["Internet", "Stall", "Radio", "비행기", "Power"],
        "must_not_include": [],
    },
    {
        "id": "oos_radio_power",
        "category": "Network_OOS",
        "query": "OOS 발생 원인을 분석해줘. 단순 망 품질 문제인지, 비행기 모드나 Radio Power OFF로 인한 정상 동작인지 구분해줘.",
        "must_include_any": ["OOS", "Radio", "Power", "비행기", "망"],
        "must_not_include": [],
    },
    {
        "id": "battery_crash",
        "category": "Battery_Thermal_Crash",
        "query": "Battery/Thermal 상태와 Crash/ANR 발생 여부를 함께 분석해줘. 둘 사이의 시간적 연관성이 있으면 설명해줘.",
        "must_include_any": ["Battery", "Thermal", "Crash", "ANR", "배터리", "발열"],
        "must_not_include": [],
    },
    {
        "id": "cs_call",
        "category": "CS_Call",
        "query": "CS Call 실패 또는 Call Drop 여부를 분석하고, 실패 원인과 RF/OOS 연관성을 정리해줘.",
        "must_include_any": ["CS", "Call", "Drop", "FAIL", "OOS", "RF"],
        "must_not_include": [],
    },
    {
        "id": "ps_ims_call",
        "category": "PS_IMS_Call",
        "query": "PS/IMS/VoLTE Call 이벤트를 분석해줘. SIP 또는 IMS 실패 원인이 있으면 함께 설명해줘.",
        "must_include_any": ["IMS", "VoLTE", "SIP", "PS", "Call"],
        "must_not_include": [],
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


def simple_score(answer: str, case: Dict[str, Any]) -> Dict[str, Any]:
    answer_lower = answer.lower()

    include_hits = []
    for keyword in case.get("must_include_any", []):
        if keyword.lower() in answer_lower:
            include_hits.append(keyword)

    forbidden_hits = []
    for keyword in case.get("must_not_include", []):
        if keyword.lower() in answer_lower:
            forbidden_hits.append(keyword)

    include_score = 1 if include_hits else 0
    forbidden_penalty = len(forbidden_hits)

    score = max(0, include_score - forbidden_penalty)

    return {
        "auto_score": score,
        "include_hits": include_hits,
        "forbidden_hits": forbidden_hits,
    }


def load_payload_files(payload_dir: str, selected_files: List[str] | None) -> List[str]:
    if selected_files:
        return selected_files

    files = []
    for name in os.listdir(payload_dir):
        if name.endswith("_payload.json"):
            files.append(name)

    return sorted(files)


def run_benchmark(
    models: List[str],
    payload_dir: str,
    payload_files: List[str],
    output_dir: str,
    repeat: int,
    reset_ingest_each_model: bool,
):
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(output_dir, f"model_benchmark_{timestamp}.csv")
    json_path = os.path.join(output_dir, f"model_benchmark_{timestamp}.json")

    all_results = []

    for model in models:
        print(f"\n==============================")
        print(f"MODEL: {model}")
        print(f"==============================")

        rag = RilRagChat(model_name=model)

        # 프로젝트 구조상 payload를 먼저 ingest
        # 이미 ingest된 DB를 재사용하는 구조라면 이 부분은 비용이 적거나 skip될 수 있음
        print("[INFO] ingest payload folder...")
        rag.ingest_folder(payload_dir)

        for payload_file in payload_files:
            print(f"\n[FILE] {payload_file}")

            for case in TEST_CASES:
                for run_idx in range(1, repeat + 1):
                    query = case["query"]

                    print(f"  - {case['id']} / run {run_idx}")

                    started = time.time()
                    error = None
                    answer = ""

                    try:
                        raw_answer = rag.ask(
                            query=query,
                            current_file=payload_file,
                        )
                        answer = normalize_answer(raw_answer)
                    except Exception as e:
                        error = repr(e)
                        answer = ""

                    elapsed = round(time.time() - started, 3)
                    score_info = simple_score(answer, case)

                    row = {
                        "timestamp": timestamp,
                        "model": model,
                        "payload_file": payload_file,
                        "case_id": case["id"],
                        "category": case["category"],
                        "run_idx": run_idx,
                        "latency_sec": elapsed,
                        "auto_score": score_info["auto_score"],
                        "include_hits": "|".join(score_info["include_hits"]),
                        "forbidden_hits": "|".join(score_info["forbidden_hits"]),
                        "error": error or "",
                        "query": query,
                        "answer": answer,
                    }

                    all_results.append(row)

    fieldnames = [
        "timestamp",
        "model",
        "payload_file",
        "case_id",
        "category",
        "run_idx",
        "latency_sec",
        "auto_score",
        "include_hits",
        "forbidden_hits",
        "error",
        "query",
        "answer",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n[SAVED] {csv_path}")
    print(f"[SAVED] {json_path}")

    print_summary(all_results)


def print_summary(results: List[Dict[str, Any]]):
    summary = {}

    for row in results:
        model = row["model"]
        summary.setdefault(model, {
            "count": 0,
            "error_count": 0,
            "latency_sum": 0.0,
            "score_sum": 0,
        })

        summary[model]["count"] += 1
        summary[model]["latency_sum"] += float(row["latency_sec"])
        summary[model]["score_sum"] += int(row["auto_score"])

        if row["error"]:
            summary[model]["error_count"] += 1

    print("\n===== SUMMARY =====")
    for model, s in summary.items():
        count = s["count"]
        avg_latency = round(s["latency_sum"] / count, 3) if count else 0
        avg_score = round(s["score_sum"] / count, 3) if count else 0

        print(
            f"{model} | "
            f"cases={count}, "
            f"avg_latency={avg_latency}s, "
            f"avg_auto_score={avg_score}, "
            f"errors={s['error_count']}"
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
        reset_ingest_each_model=False,
    )

if __name__ == "__main__":
    main()
