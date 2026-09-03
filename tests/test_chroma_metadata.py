"""메타데이터가 Chroma 에 실제로 들어가는지 지킨다.

None 하나가 add() 배치 전체를 거부시키고, ingest_file 은 그 배치를 건너뛴 뒤
"완료" 로 끝난다. 그래서 적재 실패가 검색/차트가 빌 때까지 안 보인다.
가짜 컬렉션으로는 이 계약을 확인할 수 없어서 실제 Chroma 에 넣어 본다.
"""

import uuid

import chromadb
import pytest
from chromadb.config import Settings

from rag.chroma_utils import sanitize_chroma_metadata


@pytest.fixture
def collection():
    # EphemeralClient 는 프로세스 안에서 상태를 공유해서 이름이 겹치면 터진다.
    client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
    return client.create_collection(f"probe-{uuid.uuid4().hex[:8]}")


def test_none_values_are_dropped_not_passed_through():
    meta = sanitize_chroma_metadata(
        {"package": "com.a", "freeze_at": None, "duration_sec": 61.5, "pid": None}
    )

    assert meta == {"package": "com.a", "duration_sec": 61.5}


def test_a_row_with_none_fields_still_reaches_chroma(collection):
    """App_Network_Block_Window 는 대부분의 칸이 비어 있는 채로 나온다."""
    meta = sanitize_chroma_metadata({
        "log_type": "App_Network_Block_Window",
        "package": "com.google.android.youtube",
        "uid": "10297",
        "blocked_at": "09-01 16:12:42.171",
        "unblocked_at": None,
        "freeze_at": None,
        "freeze_reason": None,
        "resumed_at": None,
        "pid": None,
        "is_recovered": False,
    })

    collection.add(documents=["blocked"], embeddings=[[0.1] * 8], metadatas=[meta], ids=["a"])

    assert collection.count() == 1


def test_one_bad_row_would_take_the_whole_batch_with_it(collection):
    """정리 안 한 None 은 같은 배치의 멀쩡한 문서까지 전부 떨어뜨린다."""
    good = {"log_type": "DNS_Query", "app_name": "com.a"}
    raw_none = {"log_type": "DNS_Query", "app_name": "com.b", "latency_ms": None}

    with pytest.raises(Exception):
        collection.add(
            documents=["a", "b"],
            embeddings=[[0.1] * 8, [0.2] * 8],
            metadatas=[good, raw_none],
            ids=["a", "b"],
        )
    assert collection.count() == 0

    # 정리를 거치면 둘 다 들어간다.
    collection.add(
        documents=["a", "b"],
        embeddings=[[0.1] * 8, [0.2] * 8],
        metadatas=[sanitize_chroma_metadata(good), sanitize_chroma_metadata(raw_none)],
        ids=["a", "b"],
    )
    assert collection.count() == 2


def test_none_inside_a_list_is_dropped_too(collection):
    meta = sanitize_chroma_metadata({"log_type": "X", "servers": ["8.8.8.8", None, "1.1.1.1"]})

    collection.add(documents=["x"], embeddings=[[0.1] * 8], metadatas=[meta], ids=["x"])

    assert meta["servers"] == ["8.8.8.8", "1.1.1.1"]
    assert collection.count() == 1
