

import gc
import json
import os

from rag.chroma_utils import sanitize_chroma_metadata


def ingest_file(collection, embed_model, file_path, force=False):
    if not os.path.exists(file_path):
        print(f"❌ payload 파일 없음: {file_path}")
        return

    filename = os.path.basename(file_path)
    base_id = os.path.splitext(filename)[0]

    if force:
        old = collection.get(where={"source_file": filename}, include=[])
        if old and old.get("ids"):
            collection.delete(ids=old["ids"])

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data:
        print(f"⚠️ 비어있는 payload: {filename}")
        return

    MAX_DOC_CHARS = 4000
    MAX_META_CHARS = 5000
    BATCH_SIZE = 100

    docs, metas, ids = [], [], []
    for i, item in enumerate(data):
        docs.append(str(item["document"])[:MAX_DOC_CHARS])
        meta = item.get("metadata", {}).copy()
        meta["source_file"] = filename
        metas.append(sanitize_chroma_metadata(meta, max_chars=MAX_META_CHARS))
        ids.append(f"{base_id}_{i}")

    print(f"'{filename}' 배치 임베딩 시작... (총 {len(docs)} docs)")
    for i in range(0, len(docs), BATCH_SIZE):
        batch_docs = docs[i:i+BATCH_SIZE]
        batch_metas = metas[i:i+BATCH_SIZE]
        batch_ids = ids[i:i+BATCH_SIZE]
        embeddings = embed_model.encode(batch_docs).tolist()
        collection.add(
            documents=batch_docs,
            embeddings=embeddings,
            metadatas=batch_metas,
            ids=batch_ids,
        )
        gc.collect()


def get_all_files(collection):
    files = set()
    offset = 0
    limit = 5000

    while True:
        try:
            results = collection.get(
                include=["metadatas"],
                limit=limit,
                offset=offset,
            )

            if not results or not results.get("metadatas"):
                break

            for m in results["metadatas"]:
                if m and "source_file" in m:
                    files.add(m["source_file"])

            if len(results["metadatas"]) < limit:
                break

            offset += limit

        except Exception as e:
            print(f"파일 목록 조회 중 에러: {e}")
            break

    return sorted(list(files))


def reset_db(collection):
    try:
        results = collection.get()
        if results and results.get("ids"):
            collection.delete(ids=results["ids"])
            print("[DEBUG] DB 초기화 완료: 기존 데이터 삭제됨")
        else:
            print("[DEBUG] DB가 이미 비어있어 삭제를 건너뜁니다.")
        return True
    except Exception as e:
        print(f"[ERROR] DB 초기화 중 오류 발생: {e}")
        return False