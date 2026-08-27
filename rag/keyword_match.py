"""Keyword scoring for the hybrid rerank in ``rag/retrieval.py``.

이전 구현은 질의에서 ``[a-zA-Z0-9]+`` 만 토큰으로 뽑았다. 그래서 "통화가 갑자기
끊긴 원인" 같은 순수 한글 질의의 키워드 점수가 항상 0.0 이었고,
``hybrid = vector*0.4 + keyword*0.6`` 에서 0.6 이 통째로 죽었다. 남은 것은 0.4 로
눌린 벡터 점수뿐이라, 그 위에 얹히는 도메인 부스트(+0.45~1.00)가 순위를 지배했다.

로그 본문은 대부분 영문이라 한글 토큰을 그대로 찾아봐도 잡히지 않는다(실측: 13k
문서에서 "통화"/"먹통"/"누수" 0건). 그래서 한글 어휘를 로그에 실제로 등장하는
영문 토큰으로 확장한다.

가중치는 후보 집합 안에서의 IDF 로 준다. 리랭킹은 fetch_k(16~32)개 후보만 보므로
후보 전체에 깔린 토큰("dns", "network" 처럼 흔한 것)은 변별력이 없고, 두세 문서에만
있는 토큰이 결정적이다. 단순 등장 개수로 세면 이 둘이 같은 값을 받는다.
"""

import math
import re

_LATIN_TOKEN = re.compile(r"[a-zA-Z0-9]+")
_HANGUL_TOKEN = re.compile(r"[가-힣]+")

# 조사/어미. 긴 것부터 떼어내야 "에서" 가 "에" 로 먼저 잘리지 않는다.
_PARTICLES = (
    "에서는", "에서도", "으로도", "까지도", "부터도",
    "에서", "에게", "으로", "까지", "부터", "이랑", "라고", "이라", "인가",
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "로", "랑", "야",
)

# 한글 어휘 -> 로그 원문에 실제로 등장하는 토큰.
# 값은 payload 코퍼스에서 등장 여부를 확인한 것만 넣는다. 후보 집합에 한 번도
# 없는 토큰은 아래 build_keyword_scorer 에서 가중치 계산 시 자동으로 빠진다.
_SYNONYMS = {
    "통화": ("call", "ims", "sip"),
    "전화": ("call", "ims"),
    "콜": ("call",),
    "발신": ("call", "dial"),
    "착신": ("call",),
    "수신": ("call",),
    "끊": ("drop", "disconnect", "release", "callfailcause"),
    "드랍": ("drop", "disconnect", "callfailcause"),
    "종료": ("release", "disconnect", "kill"),
    "거절": ("reject", "decline"),
    "인터넷": ("internet", "stall", "validation"),
    "웹": ("internet", "stall"),
    "먹통": ("stall", "validation", "oos"),
    "스톨": ("stall",),
    "멈": ("stall", "anr", "watchdog"),
    "프리징": ("anr", "watchdog"),
    "데이터": ("data", "datacall"),
    "데이터콜": ("datacall", "data_call", "setup_data_call"),
    "데이터호": ("datacall", "data_call", "setup_data_call", "apn"),
    "크래시": ("crash", "fatal"),
    "충돌": ("crash", "fatal"),
    "죽": ("crash", "kill", "fatal"),
    "강제종료": ("kill", "crash"),
    "네이티브": ("native", "sigsegv", "tombstone"),
    "응답": ("anr", "watchdog"),
    "재부팅": ("boot", "reboot"),
    "부팅": ("boot",),
    "리부팅": ("boot", "reboot"),
    "신호": ("signal", "level"),
    "세기": ("signal", "level"),
    "음영": ("oos", "signal"),
    "망이탈": ("oos", "network"),
    "망장애": ("oos", "network"),
    "기지국": ("network", "signal"),
    "배터리": ("battery", "drain"),
    "소모": ("drain", "battery", "wakelock"),
    "발열": ("thermal", "temperature"),
    "온도": ("thermal", "temperature"),
    "문자": ("sms",),
    "메시지": ("sms", "sip"),
    "등록": ("register", "ims"),
    "위성": ("satellite", "ntn"),
    "바인더": ("binder",),
    "누수": ("leak", "proxy"),
    "프록시": ("proxy", "binder"),
    "실패": ("fail", "error"),
    "에러": ("error", "fail"),
    "오류": ("error", "fail"),
    "장애": ("fail", "error", "oos"),
    "원인": ("cause",),
    "이유": ("cause",),
    "사유": ("cause", "reason"),
    "지연": ("latency", "delay", "slow"),
    "느": ("latency", "delay", "slow"),
    "전원": ("radio_power", "power"),
    "비행기": ("airplane", "radio_power"),
    "도메인": ("dns",),
    "시간": ("time",),
    "시각": ("time",),
}


def _strip_particles(token: str) -> str:
    for particle in _PARTICLES:
        if len(token) - len(particle) >= 2 and token.endswith(particle):
            return token[: -len(particle)]
    return token


def extract_query_tokens(search_query: str) -> set:
    """질의를 로그 원문에서 찾아볼 토큰 집합으로 바꾼다."""
    query_lower = (search_query or "").lower()
    tokens = set(_LATIN_TOKEN.findall(query_lower))

    for hangul in _HANGUL_TOKEN.findall(query_lower):
        # 로그에 한글이 섞여 있는 필드(desc, root_cause)도 있으므로 원형은 남긴다.
        stem = _strip_particles(hangul)
        if len(stem) >= 2:
            tokens.add(stem)

    # 조사가 붙어도 걸리게 원본 질의에 대한 부분 문자열로 확장한다.
    # "콜드랍이" 는 "끊"/"드랍" 키에, "통화가" 는 "통화" 키에 걸린다.
    for keyword, expansions in _SYNONYMS.items():
        if keyword in query_lower:
            tokens.update(expansions)

    return {token for token in tokens if len(token) >= 2}


def _idf(document_frequency: int, candidate_count: int) -> float:
    """BM25 계열 IDF. 후보 전체에 깔린 토큰은 0 에 가까워진다."""
    return math.log(
        1 + (candidate_count - document_frequency + 0.5) / (document_frequency + 0.5)
    )


def build_keyword_scorer(search_query: str, candidate_texts: list):
    """후보 집합 기준으로 IDF 가중치를 잡고, 텍스트 -> [0,1] 점수 함수를 돌려준다."""
    tokens = extract_query_tokens(search_query)
    candidate_count = len(candidate_texts or [])

    weights = {}
    for token in tokens:
        document_frequency = sum(1 for text in candidate_texts or [] if token in text)
        # 후보에 한 번도 없는 토큰은 변별에 기여하지 못한다. 분모에 남겨두면
        # 모든 후보의 점수만 균일하게 깎여서 부스트 대비 키워드 항이 약해진다.
        if document_frequency == 0:
            continue
        weight = _idf(document_frequency, candidate_count)
        if weight > 0:
            weights[token] = weight

    total_weight = sum(weights.values())
    if not total_weight:
        return lambda combined_text: 0.0

    def score(combined_text: str) -> float:
        matched = sum(
            weight for token, weight in weights.items() if token in combined_text
        )
        return matched / total_weight

    return score
