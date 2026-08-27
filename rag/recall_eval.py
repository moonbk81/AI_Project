"""검색 단계만 떼어내어 재는 측정 로직. LLM 을 부르지 않는다.

왜 필요한가: 지금까지 품질은 골든셋 end-to-end 점수 하나로만 봤다. 그 점수가
떨어져도 원인이 (1) 파서가 근거를 아예 못 뽑았는지, (2) 근사 검색이 후보에 못
올렸는지, (3) 리랭킹이 순위를 잘못 줬는지, (4) LLM 이 근거를 보고도 틀렸는지
구분되지 않았다. 그래서 튜닝의 효과를 확인할 수 없었다.

여기서는 (1)(2)(3)만 재고 (4)는 건드리지 않는다. 세 층을 따로 보여주면 어디를
고쳐야 하는지가 바로 나온다.

정답 라벨은 새로 만들지 않는다. 골든셋의 ``evidence_should_include`` 는 답변이
근거로 인용해야 하는 문자열 목록이므로, "그 문자열을 담은 문서가 검색되는가" 가
곧 검색 품질이다. 라벨을 따로 두면 데이터셋과 어긋나 썩는다.
"""

import json
import re

_WHITESPACE = re.compile(r"\s+")


def normalise(text) -> str:
    """비교용 정규화. 소문자 + 공백 축약.

    골든셋의 근거는 "callFailCause: 49" 처럼 사람이 읽는 형태로 적혀 있고, 문서
    쪽은 "- callFailCause: 49" 나 JSON 덤프로 들어 있다. 공백만 맞춰도 대부분
    붙는다. 그 이상 손대면(구두점 제거 등) 서로 다른 값이 같아 보일 수 있다.
    """
    return _WHITESPACE.sub(" ", str(text or "")).strip().lower()


def document_haystack(document, metadata) -> str:
    """문서 하나에서 근거를 찾을 대상 텍스트.

    metadata 는 JSON 덤프가 아니라 "키: 값" 줄로 편다. 골든셋이 근거를
    ``is_user_reject: false`` 처럼 적어두기 때문이다.
    """
    lines = [str(document or "")]
    for key, value in (metadata or {}).items():
        lines.append(f"{key}: {value}")
    return normalise("\n".join(lines))


def evidence_terms(golden_case) -> list:
    """이 케이스에서 검색으로 올라와야 하는 근거 문자열.

    v2 의 evidence_should_include 를 우선 쓰고, 없으면 v1 의 eval_keywords,
    그 다음 must_have 로 내려간다.
    """
    for field in ("evidence_should_include", "eval_keywords", "must_have"):
        terms = golden_case.get(field) or []
        terms = [t for t in terms if str(t).strip()]
        if terms:
            return list(dict.fromkeys(terms))
    return []


# 근거 목록에 섞여 있는 "결론" 표현. 로그에 이런 문자열이 있을 리 없다.
# 예: "Crash_Event 없음", "존재하지 않", "음영지역/기지국 장애가 아님".
_NEGATIVE_MARKERS = (
    "없음", "없다", "없는", "없이", "존재하지 않", "확인되지 않",
    "아님", "아니", "할 수 없", "불가",
)

_LOG_TYPE_NAME = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+)\b")


def is_absence_case(golden_case) -> bool:
    """부재 확인 케이스인가.

    "없는 것을 없다고 말하는가" 를 보는 케이스라서, 근거가 검색되는지로 재면
    항상 0점이 나온다. 측정 방식을 뒤집어야 한다.
    """
    return "absence" in str(golden_case.get("trap_type", "")).lower()


def scored_evidence_terms(golden_case) -> list:
    """실제로 로그에서 찾아볼 수 있는 근거만 남긴다."""
    return [
        term for term in evidence_terms(golden_case)
        if not any(marker in str(term) for marker in _NEGATIVE_MARKERS)
    ]


def denied_log_types(golden_case) -> list:
    """부재 확인 케이스가 '없어야 한다' 고 말하는 log_type 이름."""
    names = []
    fields = (
        golden_case.get("evidence_should_include") or [],
        golden_case.get("must_have") or [],
        [golden_case.get("root_cause", "")],
    )
    for field in fields:
        for term in field:
            names.extend(_LOG_TYPE_NAME.findall(str(term)))
    return list(dict.fromkeys(names))


def measure_absence(golden_case, entries, retrieved) -> dict:
    """부재 확인 케이스: 없어야 할 유형이 정말 없는지, 검색이 그걸 들고 오지 않는지.

    코퍼스에 그 유형이 있으면 골든셋 기대가 틀렸거나 파서가 뭔가 새로 뽑은 것이다.
    코퍼스엔 없는데 검색 결과에 섞여 나오면, LLM 이 "있다" 고 말할 빌미가 된다.
    """
    denied = denied_log_types(golden_case)
    in_corpus = {
        name for name in denied
        if any(str((meta or {}).get("log_type")) == name for _, _, meta in entries)
    }
    in_top_k = {
        name for name in denied
        if any(str((meta or {}).get("log_type")) == name for _, _, meta in retrieved)
    }
    return {
        "denied_log_types": denied,
        "absence_holds": sorted(set(denied) - in_corpus),
        "absence_broken": sorted(in_corpus),
        "false_evidence_in_top_k": sorted(in_top_k),
    }


_HANGUL = re.compile(r"[가-힣]")
_ASCII_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*")


def _prose_fallback_needles(term) -> list:
    """서술형 근거를 붙잡을 ASCII 토큰들. 모두 만족해야 찾은 것으로 본다.

    "am_wtf 대량 발생" 처럼 한글이 섞인 항목은 로그 문자열이 아니라 사람이 쓴
    설명이라, 적힌 그대로는 어떤 문서에도 없다. 이럴 때만 ASCII 토큰(am_wtf)으로
    완화한다. 완화 대상이 아닌 경우 빈 목록을 돌려준다.

    ASCII 만으로 된 근거("BINDER_PROXY_HISTOGRAM", "callFailCause: 49")는 완화하지
    않는다. 풀어주면 키 이름만 가진 문서가 통과해 측정이 무의미해진다.
    """
    text = str(term)
    if not _HANGUL.search(text):
        return []
    return [normalise(t) for t in _ASCII_TOKEN.findall(text) if len(t) >= 2]


def index_evidence(terms, entries) -> dict:
    """근거 문자열 -> 그 문자열을 담은 문서 id 집합.

    entries: [(doc_id, document, metadata), ...]

    적힌 그대로 먼저 찾는다. 별칭 헤더와 desc 필드 덕에 한글 근거도 문서에 그대로
    들어 있는 경우가 많다. 그래도 못 찾았을 때만 서술문으로 보고 완화한다.
    """
    haystacks = [(doc_id, document_haystack(doc, meta)) for doc_id, doc, meta in entries]
    found = {}
    for term in terms:
        verbatim = normalise(term)
        hits = {doc_id for doc_id, hay in haystacks if verbatim and verbatim in hay}

        if not hits:
            fallback = [n for n in _prose_fallback_needles(term) if n]
            if fallback:
                hits = {
                    doc_id for doc_id, hay in haystacks
                    if all(needle in hay for needle in fallback)
                }

        found[term] = hits
    return found


def _entries_from_payload(payload_path, source_file):
    """payload JSON 을 (id, document, metadata) 목록으로 읽는다.

    id 는 적재 때와 같은 규칙으로 만들지 않는다. 이 단계는 "코퍼스에 근거가
    존재하는가" 만 보므로 순번으로 충분하다.
    """
    with open(payload_path, "r", encoding="utf-8") as handle:
        loaded = json.load(handle)

    items = loaded.get("payloads", []) if isinstance(loaded, dict) else (loaded or [])
    entries = []
    for index, item in enumerate(items):
        metadata = dict(item.get("metadata") or {})
        metadata.setdefault("source_file", source_file)
        entries.append((f"{source_file}#{index}", item.get("document", ""), metadata))
    return entries


def measure_extraction(golden_case, payload_path, source_file) -> dict:
    """1층: 파서와 payload 빌더가 근거를 남겼는가.

    여기서 빠진 근거는 검색이나 리랭킹으로 되찾을 수 없다. 키워드 버킷이나
    파서를 봐야 한다는 신호다.
    """
    terms = scored_evidence_terms(golden_case)
    entries = _entries_from_payload(payload_path, source_file)
    found = index_evidence(terms, entries)

    present = [term for term in terms if found[term]]
    return {
        "evidence_total": len(terms),
        "evidence_in_corpus": len(present),
        "evidence_missing": [term for term in terms if not found[term]],
        "corpus_documents": len(entries),
    }


def measure_retrieval(golden_case, retrieved, all_entries) -> dict:
    """2층/3층: 검색이 근거를 담은 문서를 top_k 에 올렸는가.

    retrieved: [(doc_id, document, metadata), ...] 최종 top_k 순서
    all_entries: 같은 로그의 전체 문서. 근거가 코퍼스에 있는지 대비해서 봐야
                 "검색이 놓쳤다" 와 "애초에 없다" 를 구분할 수 있다.
    """
    terms = scored_evidence_terms(golden_case)
    in_corpus = index_evidence(terms, all_entries)
    in_top_k = index_evidence(terms, retrieved)

    # 코퍼스에 있는 근거만 검색의 책임으로 센다.
    answerable = [term for term in terms if in_corpus[term]]
    retrieved_terms = [term for term in answerable if in_top_k[term]]

    first_hit_rank = None
    for rank, (doc_id, _, _) in enumerate(retrieved, start=1):
        if any(doc_id in in_corpus[term] for term in answerable):
            first_hit_rank = rank
            break

    return {
        "answerable_evidence": len(answerable),
        "retrieved_evidence": len(retrieved_terms),
        "evidence_recall": (len(retrieved_terms) / len(answerable)) if answerable else None,
        "missed_evidence": [term for term in answerable if term not in retrieved_terms],
        "first_relevant_rank": first_hit_rank,
    }


def ann_recall(exact_ids, approx_ids) -> float:
    """근사 검색이 정확 최근접을 얼마나 재현했는지.

    낮으면 리랭킹을 아무리 고쳐도 소용이 없다. HNSW 색인 설정을 봐야 한다.
    """
    exact = set(exact_ids)
    if not exact:
        return None
    return len(exact & set(approx_ids)) / len(exact)
