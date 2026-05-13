"""Stable offline RAG evaluation for Telephony RAG logs.

This script avoids TruLens VirtualApp/Leaderboard selector issues in recent
TruLens OTEL versions and produces a CSV report with RAG-style KPIs.

Input JSONL schema per line:
{
  "query": "...",
  "context": "..." | ["chunk1", "chunk2"],
  "answer": "..."
}

Example:
  python run_rag_eval_csv.py \
    --log-file eval_logs/rag_eval_dataset.jsonl \
    --judge-model ollama/gemma3:4b \
    --output rag_eval_results_gemma3_4b.csv \
    --summary-output rag_eval_results_gemma3_4b_summary.csv

    python run_rag_eval_csv.py \
      --log-file eval_logs/rag_eval_dataset.jsonl \
      --judge-model ollama/qwen2.5-coder:7b \
      --output rag_eval_results_qwen2_5coder_7b.csv \
      --summary-output rag_eval_results_qwen2_5coder_7b_summary.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import litellm
except ImportError as e:
    raise SystemExit(
        "Missing dependency: litellm\n"
        "Install with: pip install -U litellm"
    ) from e


DEFAULT_LOG_FILE = "eval_logs/rag_eval_dataset.jsonl"
DEFAULT_OUTPUT = "rag_eval_results.csv"
DEFAULT_SUMMARY = "rag_eval_summary.csv"
DEFAULT_JUDGE_MODEL = "ollama/gemma3:4b"
DEFAULT_OLLAMA_BASE = "http://localhost:11434"


def normalize_context(context: Any) -> List[str]:
    if context is None:
        return []
    if isinstance(context, list):
        out = []
        for item in context:
            if item is None:
                continue
            if isinstance(item, str):
                out.append(item)
            else:
                out.append(json.dumps(item, ensure_ascii=False))
        return out
    if isinstance(context, str):
        return [context]
    return [json.dumps(context, ensure_ascii=False)]


def join_contexts(contexts: List[str], max_chars: int = 12000) -> str:
    text = "\n\n--- retrieved context ---\n\n".join(contexts or [])
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[TRUNCATED]"
    return text


def load_eval_logs(log_file: str) -> List[Dict[str, Any]]:
    path = Path(log_file)
    if not path.exists():
        raise FileNotFoundError(f"Evaluation log file not found: {path}")

    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                # data = json.loads(line)
                data = extract_json_object(line)
                for key in [
                    "answer_relevance",
                    "context_relevance",
                    "groundedness",
                    "rca_quality",
                    "temporal_reasoning",
                    "hallucination_risk",
                    "overall_score",
                ]:
                    if key in data and isinstance(data[key], (int, float)):
                        if data[key] > 1:
                            data[key] = data[key] / 5
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON at line {line_no}: {e}") from e

            query = data.get("query") or data.get("prompt") or data.get("question")
            answer = data.get("answer") or data.get("response") or data.get("output")
            context = data.get("context") or data.get("contexts") or data.get("retrieved_context")

            if not query or answer is None:
                raise ValueError(
                    f"Line {line_no} must include query and answer fields. "
                    f"Available keys: {list(data.keys())}"
                )

            rows.append({
                "row_id": data.get("id") or line_no,
                "query": str(query),
                "answer": str(answer),
                "contexts": normalize_context(context),
                "metadata": data.get("metadata", {}),
            })
    return rows


def extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found in judge response: {text[:300]}")
    return json.loads(m.group(0))

def extract_json_object(text: str) -> dict:
    import json
    import re

    text = (text or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)

    if not match:
        raise ValueError(f"No JSON object found in judge response: {text[:300]}")

    return json.loads(match.group(0))


def judge_with_litellm(
    model: str,
    ollama_base: str,
    query: str,
    context: str,
    answer: str,
    timeout: int = 180,
) -> Tuple[Dict[str, Any], str]:
    os.environ.setdefault("OLLAMA_API_BASE", ollama_base)

    prompt = f"""
You are an expert evaluator for Android Telephony RAG (Retrieval-Augmented Generation) systems.

Your task is to evaluate the quality of the generated answer using the provided question and retrieved context.

Evaluate the answer based on:

1. Answer Relevance
   - Does the answer directly address the user's question?

2. Context Relevance
   - Does the retrieved context contain information relevant to the question?

3. Groundedness
   - Is the answer supported by the retrieved logs and context?
   - Avoid rewarding unsupported assumptions.

4. RCA (Root Cause Analysis) Quality
   - Does the answer correctly identify the most likely root cause?
   - Does it avoid incorrect or fabricated causes?

5. Temporal Reasoning
   - Does the answer correctly interpret event order and state transitions?
   - Especially important for:
     - OOS recovery
     - registration transitions
     - Radio Power events
     - DNS failures
     - IMS/VoLTE events

6. Hallucination Risk
   - Does the answer invent unsupported causes or events?
   - Lower score means lower hallucination risk.

IMPORTANT EVALUATION RULES:

1. "No issue detected" CAN be a HIGH-QUALITY answer
   if the logs genuinely indicate normal behavior.

2. Do NOT penalize short answers if they are factually correct.

3. If the answer correctly states:
   - no ANR occurred
   - no Crash occurred
   - no Call Drop occurred
   - recovery completed normally
   - no abnormal network behavior exists

   then Answer Relevance, Groundedness, and RCA Quality
   should still receive HIGH scores.

4. Do NOT reward verbosity alone.
   Long explanations without evidence should receive LOW scores.

5. Prefer:
   - factual correctness
   - grounding to retrieved logs
   - accurate temporal reasoning
   over answer length.

6. Penalize FALSE POSITIVES heavily.
   Incorrectly claiming a failure when logs show normal behavior
   should significantly reduce the score.

7. Telecom log analysis prioritizes:
   - factual correctness
   - recovery interpretation
   - state transition understanding
   over creative reasoning.

SCORING RULES:

- All scores MUST be between 0.0 and 1.0
- 1.0 means excellent
- 0.0 means completely incorrect

GOOD EXAMPLE:

Question:
"Analyze whether Call Drop occurred."

Retrieved Context:
"No abnormal call release cause observed."
"Call session terminated normally."

Answer:
"No explicit Call Drop event was detected. The call appears to have terminated normally."

Expected Evaluation:
- answer_relevance: HIGH
- groundedness: HIGH
- hallucination_risk: LOW

BAD EXAMPLE:

Answer:
"Possible modem instability caused RF degradation."

when no such evidence exists in the logs.

Expected Evaluation:
- groundedness: LOW
- hallucination_risk: HIGH

Question:
{query}

Retrieved Context:
{context}

Answer:
{answer}

Return ONLY valid JSON in the following format:

{{
  "answer_relevance": 0.0,
  "context_relevance": 0.0,
  "groundedness": 0.0,
  "rca_quality": 0.0,
  "temporal_reasoning": 0.0,
  "hallucination_risk": 0.0,
  "overall_score": 0.0,
  "reason": ""
}}
""".strip()

    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        api_base=ollama_base,
        temperature=0,
        timeout=timeout,
        format="json",
    )
    raw = response["choices"][0]["message"]["content"]
    return extract_json(raw), raw


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def evaluate(
    log_file: str,
    output: str,
    summary_output: str,
    judge_model: str,
    ollama_base: str,
    max_context_chars: int,
    timeout: int,
) -> None:
    rows = load_eval_logs(log_file)
    print(f"[INFO] Loaded {len(rows)} records from {log_file}")
    print(f"[INFO] Judge model: {judge_model}")

    result_rows: List[Dict[str, Any]] = []

    for idx, row in enumerate(rows, start=1):
        print(f"[EVAL] {idx}/{len(rows)} row_id={row['row_id']}")
        context = join_contexts(row["contexts"], max_chars=max_context_chars)
        error = ""
        judge_raw = ""
        scores: Dict[str, Any] = {}

        try:
            scores, judge_raw = judge_with_litellm(
                model=judge_model,
                ollama_base=ollama_base,
                query=row["query"],
                context=context,
                answer=row["answer"],
                timeout=timeout,
            )
        except Exception as e:
            error = repr(e)
            print(f"[WARN] judge failed: {error}")

        result_rows.append({
            "row_id": row["row_id"],
            "query": row["query"],
            "answer": row["answer"],
            "context_chars": len(context),
            "answer_relevance": safe_float(scores.get("answer_relevance")),
            "context_relevance": safe_float(scores.get("context_relevance")),
            "groundedness": safe_float(scores.get("groundedness")),
            "rca_quality": safe_float(scores.get("rca_quality")),
            "temporal_reasoning": safe_float(scores.get("temporal_reasoning")),
            "hallucination_risk": safe_float(scores.get("hallucination_risk")),
            "overall_score": safe_float(scores.get("overall_score")),
            "comment": scores.get("comment", ""),
            "error": error,
            "judge_raw": judge_raw,
        })

    fieldnames = list(result_rows[0].keys()) if result_rows else []
    with open(output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(result_rows)
    print(f"[SAVED] {output}")

    metric_names = [
        "answer_relevance",
        "context_relevance",
        "groundedness",
        "rca_quality",
        "temporal_reasoning",
        "hallucination_risk",
        "overall_score",
    ]
    summary: Dict[str, Any] = {
        "records": len(result_rows),
        "error_count": sum(1 for r in result_rows if r["error"]),
    }
    for metric in metric_names:
        values = [safe_float(r[metric]) for r in result_rows if not r["error"]]
        summary[f"avg_{metric}"] = round(statistics.mean(values), 3) if values else 0.0

    with open(summary_output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary.keys()))
        writer.writeheader()
        writer.writerow(summary)
    print(f"[SAVED] {summary_output}")

    print("\n===== SUMMARY =====")
    for k, v in summary.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-file", default=DEFAULT_LOG_FILE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", default=DEFAULT_SUMMARY)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--ollama-base", default=DEFAULT_OLLAMA_BASE)
    parser.add_argument("--max-context-chars", type=int, default=12000)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    evaluate(
        log_file=args.log_file,
        output=args.output,
        summary_output=args.summary_output,
        judge_model=args.judge_model,
        ollama_base=args.ollama_base,
        max_context_chars=args.max_context_chars,
        timeout=args.timeout,
    )