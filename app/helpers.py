import hashlib

def generate_unique_key(prefix, data_string):
    hash_obj = hashlib.md5(data_string.encode('utf-8')).hexdigest()[:8]
    return f"{prefix}_{hash_obj}"

def get_collection_metadatas_batched(collection, batch_size=500, where=None):
    """
    ChromaDB 전체 metadata 조회를 batch 단위로 수행한다.
    collection.get(include=["metadatas"])를 한 번에 호출하면 문서 수가 많을 때
    SQLite 'too many SQL variables' 오류가 발생할 수 있다.
    """
    all_metadatas = []
    all_ids = []
    offset = 0

    while True:
        kwargs = {
            "include": ["metadatas"],
            "limit": batch_size,
            "offset": offset,
        }

        if where:
            kwargs["where"] = where

        batch = collection.get(**kwargs)

        batch_metas = batch.get("metadatas", []) if batch else []
        batch_ids = batch.get("ids", []) if batch else []

        if not batch_metas:
            break

        all_metadatas.extend(batch_metas)
        all_ids.extend(batch_ids)

        if len(batch_metas) < batch_size:
            break

        offset += batch_size

    return {
        "metadatas": all_metadatas,
        "ids": all_ids,
    }
