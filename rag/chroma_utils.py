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
            if item is None or isinstance(item, (str, int, float, bool)):
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
    safe = {}

    for k, v in (meta or {}).items():
        safe[str(k)] = to_chroma_meta_value(
            v,
            max_chars=max_chars
        )

    return safe
