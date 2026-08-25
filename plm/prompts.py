"""PLM 결함을 LLM 에 물어보기 위한 질의문.

Pure text building; the calls that use these live in `plm/service.py`.
"""

from __future__ import annotations

from typing import Any, Dict, List

from plm.comments import format_comment_line

_DEFECT_METADATA_LABELS = (
    ("defect_code", "결함 코드"),
    ("defect_title", "제목"),
    ("status", "상태"),
    ("priority", "우선순위"),
    ("owner", "담당자"),
)


def build_defect_analysis_query(
    problem: Dict[str, Any],
    comments: List[Dict[str, Any]] | None = None,
) -> str:
    """Chat query that asks for a root cause analysis of a PLM defect.

    `problem` is the defect payload the PLM tab hands to the chat tab; the
    optional registered reason/countermeasure and developer comments are only
    included when they carry text.
    """
    comments = comments or []

    header_lines = [
        f"**{label}:** {problem.get(key)}"
        for key, label in _DEFECT_METADATA_LABELS
        if problem.get(key)
    ]

    parts = [
        "## PLM 결함 분석 요청",
        "",
        "\n".join(header_lines),
        "",
        "### 문제 내용",
        problem.get("content", ""),
    ]

    reason = (problem.get("reason") or "").strip()
    if reason:
        parts.extend(["", "### 등록된 근본 원인", reason])

    countermeasure = (problem.get("countermeasure") or "").strip()
    if countermeasure:
        parts.extend(["", "### 등록된 해결방안", countermeasure])

    if comments:
        rendered = "\n".join(format_comment_line(c) for c in comments)
        parts.extend(["", "### 개발자 코멘트", f"\n\n{rendered}"])

    considering = " 개발자 코멘트를 고려하여" if comments else ""
    parts.extend(
        [
            "",
            f"위 정보를 기반으로{considering} 문제의 원인을 분석하고 해결 방안을 제시해 주세요.",
        ]
    )

    return "\n".join(parts)


# Length below which refining is pointless — the text is already terse.
REFINE_MIN_CHARS = 200

REFINE_SYSTEM_PROMPT = """You are an expert at refining technical problem descriptions for intent recognition.
Your task is to extract and refine the essential information while preserving critical intent signals.

Rules:
1. Preserve the specific symptom/behavior (e.g., "intermittent data drops", "call fails", "battery drain")
2. Preserve affected component/app/feature names (these are intent signals)
3. Preserve specific conditions when they occur (e.g., "during handover", "when using app X")
4. Remove redundant details and unnecessary explanations
5. Extract and include key technical details (error codes, version info, network info if present)
6. Make it concise but complete (aim for 2-3 sentences max)
7. Use bullet points only for multiple distinct issues
8. Return ONLY the refined description, no additional text or explanation"""


def simplify_problem_description(problem_content: str) -> str:
    """Dependency-free fallback: keep the first few meaningful lines."""
    lines = (problem_content or "").split("\n")
    meaningful = [line.strip() for line in lines if len(line.strip()) > 10]
    return "\n".join(meaningful[:3]) if meaningful else problem_content
