import json
import os
from datetime import datetime

def log_rag_for_evaluation(query: str, context: str, answer: str, guideline: str = "", log_dir: str = "./eval_logs"):
    """
    TruLens 오프라인 평가를 위해 RAG 파이프라인의 I/O를 JSONL 포맷으로 저장합니다.
    """
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "rag_eval_dataset.jsonl")

    combined_context = context
    if guideline and guideline.strip():
        combined_context = f"=== [시스템 도메인 가이드라인] ===\n{guideline.strip()}\n\n=== [검색된 팩트 데이터 및 로그] ===\n{context}"

    log_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "query": query,
        "context": combined_context,
        "answer": answer
    }

    # JSONL 형식으로 한 줄씩 추가 (메모리 부하 0, 파일 I/O 부하 최소화)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
