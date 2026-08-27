"""ChromaDB 컬렉션의 HNSW 설정.

기본값(ef_construction=100, max_neighbors=16, ef_search=100)으로 만든 인덱스는
이 코퍼스에서 근사 검색 리콜이 무너졌다. 실측: 13,702 문서에서
"앱이 죽은 근본 원인이 뭐야" 의 정확 최근접 16건이 전부 System_Kill_Wtf_Event
(거리 0.946~0.998)인데, Chroma 는 Wakelock_Stat 만 16건(1.08~1.12) 돌려줬다.
동일 거리 문서 때문이 아니라 그래프에서 그 군집에 닿지 못한 것이다.

ef_search 를 512 로 올리면 일부 질의는 살아나지만(인터넷 먹통 0% -> 100%),
닿지 못하는 군집은 그대로다. 그건 그래프 자체의 품질 문제라서
ef_construction / max_neighbors 를 올려 다시 쌓아야 한다. 두 값은 기존 컬렉션에
적용할 수 없고(Chroma 가 갱신을 허용하지 않는다) 재적재가 필요하다.

이 코퍼스의 특징은 near-duplicate 군집이 거대하다는 것이다(Data_Usage 7,472건,
Wakelock_Stat 2,469건 대 Native_Crash_Event 1건). 이런 분포에서 이웃 수가 적으면
소수 유형이 그래프에서 고립된다. 그래서 max_neighbors 를 넉넉히 준다.
"""

# ef_search 는 질의 시점 탐색 폭이라 기존 컬렉션에도 modify 로 적용된다.
# ef_construction / max_neighbors 는 인덱스를 쌓을 때만 반영된다.
HNSW_CONFIG = {
    "space": "l2",
    "ef_construction": 400,
    "max_neighbors": 64,
    "ef_search": 512,
}

COLLECTION_CONFIGURATION = {"hnsw": dict(HNSW_CONFIG)}


def ensure_hnsw_settings(collection):
    """적용 가능한 값은 맞추고, 재구축이 필요한 값이 어긋나면 알린다.

    ef_construction / max_neighbors 는 이미 만들어진 컬렉션에 넣을 수 없다. 그래서
    구버전 코드가 만든 컬렉션은 약한 그래프를 계속 쓰게 되는데, 그 사실이 아무
    신호 없이 묻히면 "리콜을 고쳤다" 고 착각한 채로 운영하게 된다.
    """
    changed = ensure_search_ef(collection)

    hnsw = {}
    try:
        hnsw = (collection.configuration_json or {}).get("hnsw", {}) or {}
    except Exception:
        return changed

    weak = {
        key: (hnsw.get(key), HNSW_CONFIG[key])
        for key in ("ef_construction", "max_neighbors")
        if isinstance(hnsw.get(key), int) and hnsw[key] < HNSW_CONFIG[key]
    }
    if weak:
        detail = ", ".join(f"{key}={cur}(목표 {want})" for key, (cur, want) in weak.items())
        print(
            f"⚠️ [Chroma] {collection.name} 은 예전 설정으로 색인돼 있습니다: {detail}. "
            "이 값들은 기존 컬렉션에 적용할 수 없어, 전체 초기화 후 재적재해야 근사 검색 "
            "리콜 개선이 실제로 반영됩니다."
        )

    return changed


def ensure_search_ef(collection):
    """기존 컬렉션의 ef_search 를 목표값으로 끌어올린다.

    재적재 없이 적용되는 유일한 손잡이라서, 엔진이 뜰 때마다 맞춰준다.
    이미 목표값이면 아무것도 하지 않는다.
    """
    target = HNSW_CONFIG["ef_search"]
    try:
        current = (collection.configuration_json or {}).get("hnsw", {}).get("ef_search")
    except Exception:
        current = None

    if current == target:
        return False

    try:
        collection.modify(configuration={"hnsw": {"ef_search": target}})
        print(f"🔧 [Chroma] {collection.name} 의 ef_search 를 {current} -> {target} 로 올렸습니다.")
        return True
    except Exception as exc:
        print(f"⚠️ [Chroma] ef_search 조정 실패 ({collection.name}): {exc}")
        return False
