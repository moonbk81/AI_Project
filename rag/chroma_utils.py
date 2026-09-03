import json

def to_chroma_meta_value(value, max_chars=5000):
    """
    ChromaDB metadata accepts only scalar/list values.
    Convert dict/tuple/set and oversized values to safe strings.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        out = value

    elif isinstance(value, list):
        safe_list = []

        for item in value:
            # 리스트 안의 None 도 같은 이유로 배치를 통째로 죽인다.
            if item is None:
                continue
            if isinstance(item, (str, int, float, bool)):
                safe_list.append(item)
            else:
                safe_list.append(
                    json.dumps(
                        item,
                        ensure_ascii=False,
                        default=str
                    )
                )

        out = safe_list

    else:
        out = json.dumps(
            value,
            ensure_ascii=False,
            default=str
        )

    if isinstance(out, str) and len(out) > max_chars:
        out = (
            out[:max_chars]
            + "\n...[TRUNCATED_BY_SYSTEM: TOO_LONG]"
        )

    return out

def sanitize_chroma_metadata(meta, max_chars=5000):
    """메타데이터를 Chroma 가 받아들이는 형태로 정리한다.

    None 인 키는 담지 않고 버린다. Chroma 의 Rust 백엔드가 None 을 거부하는데
    (`Cannot convert Python object to MetadataValue`), add() 는 문서 하나가
    아니라 배치 단위로 실패한다. 즉 None 하나가 같은 배치의 멀쩡한 문서
    100여 건까지 통째로 날려버린다. 그러고도 ingest_file 은 배치를 건너뛰고
    계속 진행하므로 "완료" 로 끝나서, 검색 단계에 가서야 빈손으로 드러난다.

    "값이 None" 과 "키가 없음" 은 어차피 조회 쪽에서 같은 뜻이다. pandas 로
    프레임을 만들면 없는 키는 NaN 이 되고, 차트 빌더들은 이미 그걸 다룬다.
    """
    safe = {}

    for k, v in (meta or {}).items():
        if v is None:
            continue
        safe[str(k)] = to_chroma_meta_value(
            v,
            max_chars=max_chars
        )

    return safe
