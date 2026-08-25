"""Recorded analysis cases: what was wrong, and what fixed it.

A case is written from a chat answer's retrieved rows, so the device the log
came from and the log types involved are read back out of that metadata rather
than typed in again.

Pure functions, shared by Streamlit, the browser UI and the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# A case can cover the whole answer, or just one log type inside it.
WHOLE_REPORT = "Total_Report"

SEVERITIES = ("Critical", "Major", "Minor", "Info")

# Words that place a written-up case under a log type. First match wins, so the
# order is the priority: a comment mentioning both a crash and a call is filed
# under the call.
CATEGORY_KEYWORDS = {
    "Call_Session": ["call", "드랍", "drop", "통화", "fail", "ims", "volte"],
    "Battery_Drain_Report": ["배터리", "battery", "drain", "방전", "열", "thermal", "소모"],
    "OOS_Event": ["oos", "이탈", "서비스", "service", "reg", "등록"],
    "Signal_Level": ["신호", "signal", "안테나", "level", "수신"],
    "Network_DNS_Issue": ["dns", "차단", "block", "인터넷", "지연", "latency"],
    "Crash_Event": ["크래시", "crash", "죽었", "강제종료", "anr", "am_kill", "panic", "패닉"],
}

# Device fields carried on every retrieved row; a case records the build it
# was seen on so a later reader knows whether it still applies.
BUILD_FIELDS = ("model_name", "hardware", "android_sdk", "radio", "kernel")

_UNKNOWN = "Unknown"


def recommend_category(text: str, categories: Optional[List[str]] = None) -> str:
    """Which log type a written-up case belongs to, judged from its wording."""
    lowered = (text or "").lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if categories is not None and category not in categories:
            continue
        if any(keyword in lowered for keyword in keywords):
            return category
    return WHOLE_REPORT


def available_categories(metas: Optional[List[Dict[str, Any]]]) -> List[str]:
    """The whole answer, plus every log type it actually drew on."""
    seen = []
    for meta in metas or []:
        log_type = (meta or {}).get("log_type")
        if log_type and log_type not in seen:
            seen.append(log_type)
    return [WHOLE_REPORT] + seen


def target_ids(category: str, ids: Optional[List[str]], metas: Optional[List[Dict[str, Any]]]) -> List[str]:
    """The retrieved rows a case is filed against."""
    ids = ids or []
    if category == WHOLE_REPORT:
        return list(ids)

    metas = metas or []
    return [
        doc_id
        for doc_id, meta in zip(ids, metas)
        if meta and meta.get("log_type") == category
    ]


def build_info(metas: Optional[List[Dict[str, Any]]]) -> Dict[str, str]:
    """Device and build the retrieved rows came from."""
    first = next((meta for meta in (metas or []) if meta), {})
    return {field_name: first.get(field_name, _UNKNOWN) for field_name in BUILD_FIELDS}


@dataclass(frozen=True)
class KnowledgeCase:
    """One recorded case, as the case list shows it."""

    case_id: str
    short_id: str
    model_name: str
    hardware: str
    android_sdk: str
    radio: str
    kernel: str
    severity: str
    target_ids: str
    note: str  # the analysis / fix that was written down


def build_cases(data: Optional[Dict[str, Any]]) -> List[KnowledgeCase]:
    data = data or {}
    ids = data.get("ids") or []
    documents = data.get("documents") or []
    metadatas = data.get("metadatas") or []

    cases = []
    for case_id, document, meta in zip(ids, documents, metadatas):
        meta = meta or {}
        cases.append(
            KnowledgeCase(
                case_id=case_id,
                short_id=str(case_id)[:8],
                model_name=meta.get("model_name", _UNKNOWN),
                hardware=meta.get("hardware", "-"),
                android_sdk=meta.get("android_sdk", "-"),
                radio=meta.get("radio", "-"),
                kernel=meta.get("kernel", "-"),
                severity=meta.get("severity", "Normal"),
                target_ids=meta.get("target_ids", "-"),
                note=document or "",
            )
        )
    return cases


@dataclass(frozen=True)
class CaseFilters:
    """The distinct values a case list can be narrowed by."""

    model_name: List[str] = field(default_factory=list)
    hardware: List[str] = field(default_factory=list)
    android_sdk: List[str] = field(default_factory=list)
    severity: List[str] = field(default_factory=list)


def build_filters(cases: List[KnowledgeCase]) -> CaseFilters:
    def distinct(attribute: str) -> List[str]:
        return sorted({str(getattr(case, attribute)) for case in cases})

    return CaseFilters(
        model_name=distinct("model_name"),
        hardware=distinct("hardware"),
        android_sdk=distinct("android_sdk"),
        severity=distinct("severity"),
    )
