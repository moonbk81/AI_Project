
import gc
import json
import os
from tqdm import tqdm

from core.config import MODEL_CONFIG

from rag.chroma_utils import sanitize_chroma_metadata


def _log_info(msg):
    """ℹ️ INFO 레벨 로그"""
    print(f"ℹ️ {msg}")


def _log_success(msg):
    """✅ SUCCESS 레벨 로그"""
    print(f"✅ {msg}")


def _log_warning(msg):
    """⚠️ WARNING 레벨 로그"""
    print(f"⚠️ {msg}")


def _log_error(msg):
    """❌ ERROR 레벨 로그"""
    print(f"❌ {msg}")


def ingest_file(collection, embed_model, file_path, force=False, model_name="default"):
    """
    RAG 페이로드를 임베딩하여 Vector DB에 저장합니다.

    Returns:
        dict: {"added": int, "skipped": int, "errors": int}
    """
    stats = {"added": 0, "skipped": 0, "errors": 0}

    if not os.path.exists(file_path):
        _log_error(f"payload 파일 없음: {file_path}")
        return stats

    filename = os.path.basename(file_path)
    base_id = os.path.splitext(filename)[0]

    if force:
        old = collection.get(where={"source_file": filename}, include=[])
        if old and old.get("ids"):
            collection.delete(ids=old["ids"])
            _log_info(f"기존 데이터 삭제됨 ({len(old['ids'])} items): {filename}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except json.JSONDecodeError as e:
        _log_error(f"JSON 파싱 실패: {filename} - {e}")
        return stats
    except Exception as e:
        _log_error(f"파일 읽기 실패: {filename} - {e}")
        return stats

    if not loaded:
        _log_warning(f"비어있는 payload: {filename}")
        return stats

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
        _log_error(f"지원하지 않는 payload 형식: {filename} ({type(loaded).__name__})")
        return stats

    if not data:
        _log_warning(f"payload 항목 없음: {filename}")
        return stats

    model_cfg = MODEL_CONFIG.get(model_name, MODEL_CONFIG["default"])

    max_doc_chars = int(model_cfg.get("max_doc_chars", 1200))
    max_meta_chars = int(model_cfg.get("max_meta_chars", 2000))

    EMBED_BATCH_SIZE = int(model_cfg.get("embed_batch_size", 32))
    ADD_BATCH_SIZE = int(model_cfg.get("add_batch_size", 128))

    docs, metas, ids = [], [], []
    skipped_indices = []

    for i, item in enumerate(data):
        if not isinstance(item, dict) or "document" not in item:
            _log_warning(f"잘못된 payload 항목 스킵: {filename} index={i}")
            stats["skipped"] += 1
            skipped_indices.append(i)
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
        _log_error(f"유효한 payload 항목 없음: {filename}")
        return stats

    _log_info(f"'{filename}' 배치 임베딩 시작... (총 {len(docs)} docs, embed={EMBED_BATCH_SIZE}, add={ADD_BATCH_SIZE})")

    # 배치 처리 전 중복 ID 감지
    existing_ids_result = collection.get(ids=ids, include=[])
    existing_ids_set = set(existing_ids_result.get("ids", []))

    if existing_ids_set:
        _log_warning(f"기존 ID 감지됨 ({len(existing_ids_set)} items): {filename}")
        # 기존 ID 필터링
        filtered_indices = [j for j in range(len(ids)) if ids[j] not in existing_ids_set]
        if not filtered_indices:
            _log_warning(f"모든 ID가 이미 존재함: {filename}")
            return stats

        docs = [docs[j] for j in filtered_indices]
        metas = [metas[j] for j in filtered_indices]
        ids = [ids[j] for j in filtered_indices]
        stats["skipped"] += len(existing_ids_set)

    # 배치 내 중복 감지
    seen_ids = set()
    deduplicated_indices = []
    for j, id_ in enumerate(ids):
        if id_ not in seen_ids:
            deduplicated_indices.append(j)
            seen_ids.add(id_)
        else:
            _log_warning(f"배치 내 중복 ID 스킵: {id_}")
            stats["skipped"] += 1

    if deduplicated_indices:
        docs = [docs[j] for j in deduplicated_indices]
        metas = [metas[j] for j in deduplicated_indices]
        ids = [ids[j] for j in deduplicated_indices]

    for i in tqdm(range(0, len(docs), ADD_BATCH_SIZE), desc=f"임베딩 진행 중 ({filename})"):
        batch_docs = docs[i:i+ADD_BATCH_SIZE]
        batch_metas = metas[i:i+ADD_BATCH_SIZE]
        batch_ids = ids[i:i+ADD_BATCH_SIZE]

        try:
            embeddings = embed_model.encode(
                batch_docs,
                batch_size=EMBED_BATCH_SIZE,
                convert_to_numpy=True,
                show_progress_bar=False,
            ).tolist()
        except Exception as e:
            _log_error(f"임베딩 실패 (배치 {i}-{i+ADD_BATCH_SIZE}): {e}")
            stats["errors"] += len(batch_docs)
            continue

        try:
            collection.add(
                documents=batch_docs,
                embeddings=embeddings,
                metadatas=batch_metas,
                ids=batch_ids,
            )
            stats["added"] += len(batch_ids)
        except Exception as e:
            _log_error(f"ChromaDB add 실패 (배치 {i}-{i+ADD_BATCH_SIZE}): {e}")
            stats["errors"] += len(batch_ids)

        gc.collect()

    _log_success(f"'{filename}' 임베딩 완료 - 추가됨: {stats['added']}, 스킵됨: {stats['skipped']}, 에러: {stats['errors']}")
    return stats


def get_all_files(collection):
    """Vector DB에 적재된 모든 파일 목록 조회"""
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
            _log_error(f"파일 목록 조회 중 에러: {e}")
            break

    _log_info(f"적재된 파일 목록 조회 완료: {len(files)} files")
    return sorted(list(files))


def reset_db(collection):
    """Vector DB 전체 초기화"""
    try:
        results = collection.get()
        if results and results.get("ids"):
            collection.delete(ids=results["ids"])
            _log_success(f"DB 초기화 완료: {len(results['ids'])} items 삭제됨")
        else:
            _log_info("DB가 이미 비어있음")
        return True
    except Exception as e:
        _log_error(f"DB 초기화 중 오류: {e}")
        return False
