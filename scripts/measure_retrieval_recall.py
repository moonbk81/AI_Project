#!/usr/bin/env python
"""골든셋으로 검색 단계만 측정한다. LLM 을 부르지 않는다.

세 층을 따로 보여준다.

  추출   파서와 payload 빌더가 근거를 남겼는가.        못 남겼으면 아래는 무의미하다.
  ANN    근사 검색이 정확 최근접을 재현하는가.          낮으면 리랭킹을 고쳐도 소용없다.
  랭킹   근거를 담은 문서가 top_k 에 올라왔는가.        여기가 리랭커의 성적표다.

운영 DB 는 건드리지 않는다. 기본값으로 ./chroma_eval 에 별도 컬렉션을 만들고,
이미 적재된 로그는 건너뛴다. 그래서 두 번째 실행부터는 검색만 다시 돈다.

    python scripts/measure_retrieval_recall.py --test-id TC-001
    python scripts/measure_retrieval_recall.py --top-k 5 --csv csv/recall.csv

임베딩이 CPU 로 떨어지면 적재가 매우 느리다(실측 0.7 docs/s). GPU 가 비어 있을 때
전체를 한 번 적재해 두고, 이후 튜닝 측정은 --skip-ingest 로 돌리면 된다.
"""

import argparse
import csv
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.recall_eval import (  # noqa: E402
    ann_recall,
    is_absence_case,
    measure_absence,
    measure_extraction,
    measure_retrieval,
)


def _load_golden(dataset_path, test_ids, categories):
    with open(dataset_path, "r", encoding="utf-8") as handle:
        cases = json.load(handle)

    if test_ids:
        wanted = {t.strip() for t in test_ids}
        cases = [c for c in cases if c.get("test_id") in wanted]
    if categories:
        wanted = {c.strip() for c in categories}
        cases = [c for c in cases if c.get("category") in wanted]
    return cases


def _payload_name(log_path):
    return os.path.splitext(os.path.basename(log_path))[0] + "_payload.json"


def _prepare_payload(log_path, result_dir, payload_dir, force=False):
    """로그 -> report.json -> payload.json. 이미 있으면 다시 만들지 않는다."""
    from log_orchestrator import LogOrchestrator
    from prepare_rag_payload import RagPayloadBuilder

    base_name = os.path.splitext(os.path.basename(log_path))[0]
    report_path = os.path.join(result_dir, f"{base_name}_report.json")
    payload_name = _payload_name(log_path)
    payload_path = os.path.join(payload_dir, payload_name)

    if os.path.exists(payload_path) and not force:
        return payload_path, payload_name

    os.makedirs(result_dir, exist_ok=True)
    os.makedirs(payload_dir, exist_ok=True)

    if not os.path.exists(report_path) or force:
        print(f"    파싱: {log_path} ({os.path.getsize(log_path) // (1024 * 1024)}MB)", flush=True)
        if not LogOrchestrator(log_path).run_batch(report_path):
            return None, payload_name

    print(f"    payload 생성: {payload_name}", flush=True)
    RagPayloadBuilder(report_path).build_payload(payload_name)

    # RagPayloadBuilder 는 저장 위치를 리포지토리의 payloads/ 로 고정한다.
    written = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "payloads", payload_name
    )
    if written != payload_path and os.path.exists(written):
        os.replace(written, payload_path)

    return (payload_path if os.path.exists(payload_path) else None), payload_name


def _open_collection(db_path, collection_name):
    import chromadb
    from chromadb.config import Settings

    from rag.collection_config import COLLECTION_CONFIGURATION

    client = chromadb.PersistentClient(
        path=db_path, settings=Settings(anonymized_telemetry=False)
    )
    return client.get_or_create_collection(
        name=collection_name, configuration=COLLECTION_CONFIGURATION
    )


def _already_ingested(collection, source_file):
    try:
        existing = collection.get(where={"source_file": source_file}, include=[], limit=1)
        return bool(existing and existing.get("ids"))
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="골든셋 검색 리콜 측정 (LLM 미사용)")
    parser.add_argument("--dataset", default="eval_golden_dataset.json")
    parser.add_argument("--top-k", type=int, default=4, help="운영 기본값과 맞출 것")
    parser.add_argument("--db-path", default="./chroma_eval", help="측정 전용 Vector DB")
    parser.add_argument("--collection", default="recall_eval")
    parser.add_argument("--result-dir", default="./eval_artifacts/result")
    parser.add_argument("--payload-dir", default="./eval_artifacts/payloads")
    parser.add_argument("--csv", default="", help="상세 결과를 쓸 CSV 경로")
    parser.add_argument("--test-id", action="append", default=[])
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--device", default="", help="cpu / cuda. 비우면 자동")
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="적재를 건너뛰고 이미 들어있는 것만 측정한다",
    )
    parser.add_argument("--force-parse", action="store_true", help="report/payload 를 다시 만든다")
    parser.add_argument(
        "--no-ann",
        action="store_true",
        help="정확 최근접 비교를 건너뛴다(임베딩 전체를 읽어야 해서 무겁다)",
    )
    args = parser.parse_args()

    cases = _load_golden(args.dataset, args.test_id, args.category)
    if not cases:
        print("조건에 맞는 테스트 케이스가 없습니다.")
        return 1

    missing_logs = [c for c in cases if not os.path.exists(c.get("target_log_file", ""))]
    for case in missing_logs:
        print(f"⚠️ 원본 로그 없음, 제외: {case.get('test_id')} {case.get('target_log_file')}")
    cases = [c for c in cases if c not in missing_logs]
    if not cases:
        return 1

    print(f"케이스 {len(cases)}개 / top_k={args.top_k} / DB={args.db_path}\n")

    # 1) payload 준비 (로그별로 한 번)
    payloads = {}
    for log_path in dict.fromkeys(c["target_log_file"] for c in cases):
        print(f"  [준비] {os.path.basename(log_path)}", flush=True)
        payload_path, payload_name = _prepare_payload(
            log_path, args.result_dir, args.payload_dir, force=args.force_parse
        )
        if payload_path:
            payloads[log_path] = (payload_path, payload_name)
        else:
            print(f"    ❌ payload 생성 실패, 이 로그의 케이스는 제외됩니다")

    cases = [c for c in cases if c["target_log_file"] in payloads]
    if not cases:
        print("측정할 수 있는 케이스가 없습니다.")
        return 1

    # 2) 적재
    collection = _open_collection(args.db_path, args.collection)
    from rag.ingest import ingest_file
    from sentence_transformers import SentenceTransformer

    device = args.device or None
    if device is None:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n임베딩 디바이스: {device}")
    embed_model = SentenceTransformer("./bge-m3-offline", device=device)

    for log_path, (payload_path, payload_name) in payloads.items():
        if _already_ingested(collection, payload_name):
            continue
        if args.skip_ingest:
            print(f"  [적재 생략] {payload_name}")
            continue
        print(f"  [적재] {payload_name}", flush=True)
        ingest_file(collection, embed_model, payload_path, force=True)

    # 3) 측정
    from rag.recall_eval import _entries_from_payload
    from rag.retrieval import retrieve_and_rerank

    all_embeddings = None
    if not args.no_ann:
        import numpy as np

        stored = collection.get(include=["embeddings", "metadatas"])
        all_embeddings = (
            np.array(stored["embeddings"], dtype="float32"),
            stored["ids"],
            stored["metadatas"],
        )

    rows = []
    for case in cases:
        test_id = case.get("test_id", "?")
        log_path = case["target_log_file"]
        payload_path, payload_name = payloads[log_path]
        query = case.get("query", "")

        extraction = measure_extraction(case, payload_path, payload_name)
        entries = _entries_from_payload(payload_path, payload_name)
        if is_absence_case(case):
            # 부재 케이스의 근거 목록은 대부분 결론 문장이라 추출률이 의미 없다.
            extraction = {**extraction, "evidence_total": 0, "evidence_in_corpus": 0,
                          "evidence_missing": []}

        if not _already_ingested(collection, payload_name):
            # 적재가 없어도 추출 층은 잴 수 있다. 임베딩이 몇 시간 걸리는 환경에서
            # 파싱만으로 26케이스의 근거 유무를 먼저 보는 것이 이 모드의 쓸모다.
            rows.append({
                "test_id": test_id, "category": case.get("category", ""),
                "query": query, "note": "적재 없음(추출만)",
                "case_kind": "absence" if is_absence_case(case) else "extraction_only",
                **extraction,
            })
            continue

        results = retrieve_and_rerank(
            collection=collection,
            embed_model=embed_model,
            search_query=query,
            top_k=args.top_k,
            current_file=payload_name,
        )
        retrieved = list(zip(
            results["ids"][0], results["documents"][0], results["metadatas"][0]
        ))
        # 적재 id 와 payload 순번 id 는 다르므로, 근거 매칭은 문서 본문으로 한다.
        retrieved_entries = [(doc_id, doc, meta) for doc_id, doc, meta in retrieved]
        corpus_entries = [(doc_id, doc, meta) for doc_id, doc, meta in entries]
        # top_k 문서에 대응하는 순번 id 를 찾아 first_relevant_rank 를 맞춘다.
        by_text = {doc: doc_id for doc_id, doc, _ in corpus_entries}
        retrieved_entries = [
            (by_text.get(doc, doc_id), doc, meta) for doc_id, doc, meta in retrieved_entries
        ]

        if is_absence_case(case):
            # 부재 확인 케이스는 "근거가 올라왔는가" 가 아니라
            # "없어야 할 것이 정말 없고, 검색이 그걸 들고 오지 않는가" 를 본다.
            retrieval = measure_absence(case, corpus_entries, retrieved_entries)
            retrieval["case_kind"] = "absence"
        else:
            retrieval = measure_retrieval(case, retrieved_entries, corpus_entries)
            retrieval["case_kind"] = "evidence"

        ann = None
        if all_embeddings is not None:
            import numpy as np

            matrix, ids, metas = all_embeddings
            scoped = [
                i for i, m in enumerate(metas) if m.get("source_file") == payload_name
            ]
            if scoped:
                vector = np.array(embed_model.encode(query), dtype="float32")
                distances = ((matrix[scoped] - vector) ** 2).sum(axis=1)
                order = np.argsort(distances)[: args.top_k]
                exact_ids = [ids[scoped[i]] for i in order]
                ann = ann_recall(exact_ids, results["ids"][0])

        rows.append({
            "test_id": test_id,
            "category": case.get("category", ""),
            "query": query,
            "note": "",
            **extraction,
            **retrieval,
            "ann_recall": ann,
            "best_distance": results.get("best_distance"),
            "evidence_is_weak": results.get("evidence_is_weak"),
            "retrieved_log_types": ",".join(
                str(m.get("log_type")) for m in results["metadatas"][0]
            ),
        })

    _report(rows, args)
    return 0


def _report(rows, args):
    print("\n" + "=" * 108)
    print(f"{'TC':8s}{'추출':>10s}{'ANN':>8s}{'근거리콜':>10s}{'1위순위':>8s}{'최근접':>8s}{'약근거':>7s}  검색된 유형")
    print("-" * 108)
    for row in rows:
        if row.get("case_kind") == "extraction_only":
            extraction = f"{row['evidence_in_corpus']}/{row['evidence_total']}"
            print(
                f"{row['test_id']:8s}{extraction:>10s}{'-':>8s}{'-':>10s}{'-':>8s}"
                f"{'':>8s}{'':>7s}  {row['note']} / 문서 {row.get('corpus_documents', 0)}건"
            )
            continue
        if row.get("note") and row.get("case_kind") != "absence":
            print(f"{row['test_id']:8s}  {row['note']}")
            continue
        if row.get("case_kind") == "absence":
            broken = row.get("absence_broken") or []
            false_evidence = row.get("false_evidence_in_top_k") or []
            verdict = "부재 확인" if not broken else f"기대 어긋남 {broken}"
            if false_evidence:
                verdict += f" / 검색에 섞임 {false_evidence}"
            ann_value = row.get("ann_recall")
            ann = "-" if ann_value is None else f"{ann_value * 100:.0f}%"
            if row.get("note"):
                verdict = row["note"]
            print(
                f"{row['test_id']:8s}{'부재':>10s}{ann:>8s}{'':>10s}{'':>8s}"
                f"{row.get('best_distance') or 0:8.3f}"
                f"{str(row.get('evidence_is_weak')):>7s}  {verdict}"
            )
            continue
        extraction = f"{row['evidence_in_corpus']}/{row['evidence_total']}"
        ann = "-" if row.get("ann_recall") is None else f"{row['ann_recall'] * 100:.0f}%"
        recall = "-" if row.get("evidence_recall") is None else f"{row['evidence_recall'] * 100:.0f}%"
        rank = row.get("first_relevant_rank") or "-"
        print(
            f"{row['test_id']:8s}{extraction:>10s}{ann:>8s}{recall:>10s}{str(rank):>8s}"
            f"{row.get('best_distance') or 0:8.3f}{str(row.get('evidence_is_weak')):>7s}"
            f"  {row.get('retrieved_log_types', '')[:40]}"
        )

    measured = [r for r in rows if not r.get("note") and r.get("case_kind") != "absence"]
    absence_rows = [r for r in rows if r.get("case_kind") == "absence"]
    extraction_rows = [
        r for r in rows
        if r.get("case_kind") in ("evidence", "extraction_only") and r.get("evidence_total")
    ]
    if extraction_rows:
        print("-" * 108)
        extraction_rate = sum(r["evidence_in_corpus"] for r in extraction_rows) / max(
            1, sum(r["evidence_total"] for r in extraction_rows)
        )
        print(f"  추출: 근거 {extraction_rate * 100:.0f}% 가 코퍼스에 존재 "
              f"({len(extraction_rows)}케이스)")
        missing = [
            (r["test_id"], r["evidence_missing"]) for r in extraction_rows if r["evidence_missing"]
        ]
        if missing:
            print("  코퍼스에 없는 근거 (파서/버킷을 봐야 하는 것):")
            for test_id, terms in missing:
                print(f"    {test_id}: {terms}")

    if measured:
        recalls = [r["evidence_recall"] for r in measured if r["evidence_recall"] is not None]
        anns = [r["ann_recall"] for r in measured if r.get("ann_recall") is not None]
        if anns:
            print(f"  ANN : 정확 최근접 재현율 평균 {statistics.mean(anns) * 100:.0f}%")
        if recalls:
            print(f"  랭킹: top_{args.top_k} 근거 리콜 평균 {statistics.mean(recalls) * 100:.0f}%")
        weak = [r for r in measured if r.get("evidence_is_weak")]
        if weak:
            print(
                f"  ⚠️ 근거 부족으로 판정된 골든 케이스 {len(weak)}건 "
                f"({', '.join(r['test_id'] for r in weak)}) - 임계값 오탐 가능"
            )

        missed = [(r["test_id"], r["missed_evidence"]) for r in measured if r.get("missed_evidence")]
        if missed:
            print("\n  코퍼스엔 있는데 top_k 에 못 올린 근거 (검색/리랭킹을 봐야 하는 것):")
            for test_id, terms in missed:
                print(f"    {test_id}: {terms}")

    if absence_rows:
        print("-" * 108)
        broken = [r for r in absence_rows if r.get("absence_broken")]
        polluted = [r for r in absence_rows if r.get("false_evidence_in_top_k")]
        print(f"  부재 확인 케이스 {len(absence_rows)}건: "
              f"기대 유지 {len(absence_rows) - len(broken)}건, 어긋남 {len(broken)}건, "
              f"검색에 섞여 나온 것 {len(polluted)}건")
        for row in polluted:
            print(f"    {row['test_id']}: top_k 에 {row['false_evidence_in_top_k']} 가 섞였다")

    if args.csv and rows:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        fieldnames = sorted({key for row in rows for key in row})
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    k: (json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v)
                    for k, v in row.items()
                })
        print(f"\n상세: {args.csv}")


if __name__ == "__main__":
    raise SystemExit(main())
