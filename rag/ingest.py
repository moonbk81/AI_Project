
import gc
import json
import os
from tqdm import tqdm

from core.config import MODEL_CONFIG

from rag.chroma_utils import sanitize_chroma_metadata

def ingest_file(collection, embed_model, file_path, force=False, model_name="default"):
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
        loaded = json.load(f)

    if not loaded:
        print(f"⚠️ 비어있는 payload: {filename}")
        return

    # Backward/forward compatible payload handling:
    # - old format: [{"document": ..., "metadata": ...}, ...]
    # - new format: {"global_metadata": {...}, "payloads": [{...}, ...]}
    global_metadata = {}
    if isinstance(loaded, dict):
        global_metadata = loaded.get("global_metadata", {}) or {}
        data = loaded.get("payloads", []) or []
    elif isinstance(loaded, list):
        data = loaded
    else:
        print(f"⚠️ 지원하지 않는 payload 형식: {filename} ({type(loaded).__name__})")
        return

    if not data:
        print(f"⚠️ payload 항목 없음: {filename}")
        return

    model_cfg = MODEL_CONFIG.get(model_name, MODEL_CONFIG["default"])

    max_doc_chars = int(model_cfg.get("max_doc_chars", 1200))
    max_meta_chars = int(model_cfg.get("max_meta_chars", 2000))

    EMBED_BATCH_SIZE = int(model_cfg.get("embed_batch_size", 32))
    ADD_BATCH_SIZE = int(model_cfg.get("add_batch_size", 128))

    docs, metas, ids = [], [], []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or "document" not in item:
            print(f"⚠️ 잘못된 payload 항목 스킵: {filename} index={i}")
            continue

        docs.append(str(item["document"])[:max_doc_chars])

        # Keep event-level metadata as the primary metadata.
        # Do not re-inject long global metadata such as kernel into every document.
        meta = item.get("metadata", {}) or {}
        meta = meta.copy()
        meta["source_file"] = filename

        # Store only a compact pointer to global metadata for debugging/filtering,
        # without bloating every Chroma metadata row.
        if global_metadata:
            for key in ("model_name", "hardware", "android_sdk", "radio"):
                if key not in meta and key in global_metadata:
                    meta[key] = global_metadata[key]

        metas.append(sanitize_chroma_metadata(meta, max_chars=max_meta_chars))
        ids.append(f"{base_id}_{i}")

    if not docs:
        print(f"⚠️ 유효한 payload 항목 없음: {filename}")
        return

    print(
        f"'{filename}' 배치 임베딩 시작... (총 {len(docs)} docs, embed={EMBED_BATCH_SIZE}, add={ADD_BATCH_SIZE})"
    )
    for i in tqdm(range(0, len(docs), ADD_BATCH_SIZE), desc=f"임베딩 진행 중 ({filename})"):
        batch_docs = docs[i:i+ADD_BATCH_SIZE]
        batch_metas = metas[i:i+ADD_BATCH_SIZE]
        batch_ids = ids[i:i+ADD_BATCH_SIZE]
        embeddings = embed_model.encode(
            batch_docs,
            batch_size=EMBED_BATCH_SIZE,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).tolist()
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
