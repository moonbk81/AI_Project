"""
RAG Golden Dataset 자동 평가 파이프라인
"""
import os
import json
import csv
import argparse
import re
import statistics
from pathlib import Path

try:
    import litellm
except ImportError as e:
    raise SystemExit("Missing dependency: litellm\nInstall with: pip install -U litellm")

# 우리 RAG 시스템 임포트
try:
    from ril_rag_chat import RilRagChat
except ImportError as e:
    raise SystemExit("ril_rag_chat.py 파일을 찾을 수 없습니다. 동일한 디렉토리에 위치시켜 주세요.")


# =====================================================================
# 1. 안전한 JSON 파서 (사용자님의 <think> 태그 방어 로직 적용)
# =====================================================================
def extract_json_object(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r'<think>.*?(?:</think>|$)', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<\|channel>thought.*?(?:<channel\|>|<\/|\|>|$)', '', text, flags=re.DOTALL)
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"평가 LLM이 올바른 JSON을 반환하지 않았습니다: {text[:300]}")
    return json.loads(match.group(0))


# =====================================================================
# 2. 평가 LLM 호출 함수 (LiteLLM + Ollama)
# =====================================================================
def judge_with_litellm(judge_model, ollama_base, query, ground_truth, keywords, rag_answer, timeout=180) -> dict:
    os.environ.setdefault("OLLAMA_API_BASE", ollama_base)

    prompt = f"""
You are an expert evaluator for Android Telephony RAG systems.
Your task is to score the AI's generated [Answer] by comparing it strictly against the expert's [Ground Truth] and [Required Keywords].

[Question]: {query}
[Ground Truth]: {ground_truth}
[Required Keywords]: {', '.join(keywords)}

[AI's Answer to Evaluate]:
{rag_answer}

Evaluate the Answer based on the following criteria (Score 0.0 to 1.0, where 1.0 is excellent):
1. accuracy_score: How closely does the answer match the core technical facts of the Ground Truth?
2. keyword_coverage: Did the answer include or conceptually cover the Required Keywords?
3. hallucination_risk: Did the AI invent unsupported causes or events? (1.0 = VERY SAFE, 0.0 = COMPLETELY HALLUCINATED)
4. overall_score: The final weighted score of the answer.

Return ONLY valid JSON in the exact following format without markdown or extra text:
{{
  "accuracy_score": 0.0,
  "keyword_coverage": 0.0,
  "hallucination_risk": 0.0,
  "overall_score": 0.0,
  "reason": "Short explanation of the scores and missing elements"
}}
"""
    response = litellm.completion(
        model=judge_model,
        messages=[{"role": "user", "content": prompt}],
        api_base=ollama_base,
        temperature=0.0,
        timeout=timeout,
        format="json",
    )

    raw = response["choices"][0]["message"]["content"]
    return extract_json_object(raw), raw


# =====================================================================
# 3. 메인 실행 파이프라인
# =====================================================================
def evaluate_golden_dataset(dataset_path, output_csv, summary_csv, judge_model, rag_model, ollama_base):
    if not os.path.exists(dataset_path):
        print(f"❌ 골든 데이터세트를 찾을 수 없습니다: {dataset_path}")
        return

    with open(dataset_path, 'r', encoding='utf-8') as f:
        golden_data = json.load(f)

    print(f"🚀 [INIT] 총 {len(golden_data)}개의 테스트 케이스 로드 완료.")
    print(f"   - 생성용 RAG 모델: {rag_model}")
    print(f"   - 평가용 Judge 모델: {judge_model}")

    # RAG 시스템 부팅 (web_app.py와 완전히 동일한 인스턴스 초기화 구조)
    rag_system = RilRagChat(db_path="./chroma_db", model_name=rag_model)

    # =====================================================================
    # 💡 [STEP 0] 웹 파이프라인 컨벤션 기반 전처리 및 DB 적재
    # =====================================================================
    print("\n⏳ [STEP 0] 평가용 원본 로그 전처리 및 DB 적재 검사 중...")

    from log_orchestrator import LogOrchestrator
    from prepare_rag_payload import RagPayloadBuilder

    loaded_files = set()
    for item in golden_data:
        raw_log_path = item.get("target_log_file", "")
        if not raw_log_path or raw_log_path in loaded_files:
            continue

        if os.path.exists(raw_log_path):
            base_name = os.path.splitext(os.path.basename(raw_log_path))[0]
            report_path = f"./result/{base_name}_report.json"

            # 🚨 [웹 싱크 매칭 1] web_app.py 214줄 규칙에 따라 {base_name}_payload.json 으로 타겟팅
            payload_name = f"{base_name}_payload.json"
            payload_path = os.path.join("./payloads", payload_name)

            os.makedirs("./result", exist_ok=True)
            os.makedirs("./payloads", exist_ok=True)

            # 기존 페이로드가 있다면 중복 임베딩 생략하고 리트리버 성능 고정
            if os.path.exists(payload_path):
                print(f"   -> ♻️ 기존 페이로드 스캔 성공 (파싱 패스): {payload_path}")
                loaded_files.add(raw_log_path)
                continue

            print(f"   -> ⚙️ 최초 1회 통합 파이프라인 가동: {raw_log_path}")
            # log_orchestrator.py 컨벤션 매칭 (run_batch 호출)
            orchestrator = LogOrchestrator(raw_log_path)
            success = orchestrator.run_batch(report_path)

            if success and os.path.exists(report_path):
                # prepare_rag_payload.py 규격 매칭
                builder = RagPayloadBuilder(report_path)
                builder.build_payload(payload_name)

                if os.path.exists(payload_path):
                    # RilRagChat의 원천 덮어쓰기 로드 가동
                    rag_system.ingest_file(payload_path, force=True)

                loaded_files.add(raw_log_path)
            else:
                print(f"   -> ❌ 파서 에러 (run_batch 가동 불가): {raw_log_path}")
        else:
            print(f"   -> ⚠️ 원본 로그 소스 누락 (스킵): {raw_log_path}")

    print("✅ 데이터 적재 계층 정렬 완료. RAG 벤치마크 평가를 시작합니다.\n")
    # =====================================================================

    result_rows = []

    # 4) 평가 루프 가동
    for idx, item in enumerate(golden_data, start=1):
        test_id = item.get("test_id", f"TC-{idx}")
        category = item.get("category", "Unknown")
        target_file = item.get("target_log_file", "")
        query = item.get("query", "")
        ground_truth = item.get("ground_truth", "")
        keywords = item.get("eval_keywords", [])

        print(f"\n=======================================================")
        print(f"🔄 [EVAL] {idx}/{len(golden_data)} | {test_id} | {category}")
        print(f"❓ 질문: {query}")

        # 📌 STEP A: 우리 RAG 시스템에 질의하여 답변 생성 (프롬프트 가이드라인 주입 버전)
        try:
            rag_target_file = None
            if target_file:
                base_name = os.path.splitext(os.path.basename(target_file))[0]
                rag_target_file = f"{base_name}_payload.json"

            rag_answer, doc_ids, meta_list, thinking = rag_system.ask(
                user_query=query, # 👈 순수 질문 대신 가이드라인이 결합된 질문 투입!
                current_file=rag_target_file,
                chat_history=[],
                is_bench=False
            )
            print(f"✅ RAG 답변 생성 완료.")
        except Exception as e:
            rag_answer = f"RAG 파이프라인 내부 에러: {e}"
            print(f"❌ {rag_answer}")

        # 📌 STEP B: 평가용 로컬 LLM 호출 (심판 채점 계층)
        error_msg = ""
        judge_raw = ""
        scores = {}
        try:
            scores, judge_raw = judge_with_litellm(
                judge_model=judge_model,
                ollama_base=ollama_base,
                query=query,
                ground_truth=ground_truth,
                keywords=keywords,
                rag_answer=rag_answer
            )
            print(f"⚖️ 채점 완료: {scores.get('overall_score', 0.0)} | 사유: {scores.get('reason', '')}")
        except Exception as e:
            error_msg = repr(e)
            print(f"⚠️ 심판 API 응답 에러: {error_msg}")

        def safe_float(val):
            try: return float(val)
            except: return 0.0

        result_rows.append({
            "test_id": test_id,
            "category": category,
            "query": query,
            "ground_truth": str(ground_truth),
            "rag_answer": rag_answer,
            "accuracy_score": safe_float(scores.get("accuracy_score")),
            "keyword_coverage": safe_float(scores.get("keyword_coverage")),
            "hallucination_risk": safe_float(scores.get("hallucination_risk")),
            "overall_score": safe_float(scores.get("overall_score")),
            "reason": scores.get("reason", ""),
            "error": error_msg
        })

    # 5) 최종 CSV 통계 데이터 빌드
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    os.makedirs(os.path.dirname(summary_csv), exist_ok=True)

    with open(output_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=result_rows[0].keys())
        writer.writeheader()
        writer.writerows(result_rows)

    metrics = ["accuracy_score", "keyword_coverage", "hallucination_risk", "overall_score"]
    summary = {
        "total_test_cases": len(result_rows),
        "failed_evals": sum(1 for r in result_rows if r["error"])
    }

    for m in metrics:
        valid_vals = [r[m] for r in result_rows if not r["error"]]
        summary[f"avg_{m}"] = round(statistics.mean(valid_vals), 3) if valid_vals else 0.0

    with open(summary_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary.keys()))
        writer.writeheader()
        writer.writerow(summary)

    print(f"\n🎉 [평가 완료] 상세 내역: {output_csv} / 요약: {summary_csv}")
    print("===== SUMMARY =====")
    for k, v in summary.items():
        print(f"  {k}: {v}")

"""
# =====================================================================
# 4. 실행 가이드
# =====================================================================
1) 골든 데이터세트 준비
- eval_golden_dataset.json 파일을 준비합니다. 각 항목은 다음 필드를 포함
    - test_id: 고유 테스트 케이스 ID
    - category: 문제 유형 (예: "통화 끊김", "데이터 불안정")
    - target_log_file: RAG 시스템이 참조할 원본 로그 파일 경로 (예: "./logs/call_drop_001.log")
    - query: RAG 시스템에 투입할 질문 (예: "왜 통화가 끊겼나요?")
    - ground_truth: 전문가가 작성한 정답 텍스트
    - eval_keywords: 평가 시 반드시 포함되어야 할 핵심 키워드 리스트
2) Ollama 심판 모델 준비
- Ollama에서 평가용 모델을 준비합니다 (예: ollama/qwen2.5-coder:7b).
- Ollama 서버가 로컬에서 실행 중인지 확인합니다 (기본 http://localhost:11434).
3) RAG 모델 준비
- RilRagChat에서 사용할 RAG 모델을 준비합니다 (예: gemma4:e4b).
- RilRagChat이 해당 모델을 올바르게 로드할 수 있는지 확인합니다.
4) 실행
- 터미널에서 다음 명령어로 평가 스크립트를 실행합니다:
python run_golden_eval.py --judge-model ollama/gemma4:26b --rag-model gemma4:e2b
- 필요에 따라 --dataset, --output, --summary, --ollama-base 등의 인자를 조정할 수 있습니다.
python run_golden_eval.py --judge-model ollama/gemma4:26b --rag-model gemma4:e4b
"""

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local LLM RAG Golden Dataset 자동 평가기")
    parser.add_argument("--dataset", default="eval_golden_dataset.json", help="골든 데이터세트 경로")
    parser.add_argument("--output", default="csv/rag_golden_eval_details.csv", help="상세 결과 CSV")
    parser.add_argument("--summary", default="csv/rag_golden_eval_summary.csv", help="요약 결과 CSV")
    parser.add_argument("--judge-model", default="ollama/qwen2.5-coder:7b", help="로컬 심판 모델명")
    parser.add_argument("--rag-model", default="gemma4:e4b", help="우리 RAG 모델명")
    parser.add_argument("--ollama-base", default="http://localhost:11434", help="Ollama 주소")
    args = parser.parse_args()

    evaluate_golden_dataset(
        dataset_path=args.dataset,
        output_csv=args.output,
        summary_csv=args.summary,
        judge_model=args.judge_model,
        rag_model=args.rag_model,
        ollama_base=args.ollama_base
    )
