"""PLM 코멘트 텍스트 규칙.

What this tool writes into a defect comment, and how to tell those
auto-written comments back apart from developer input. Pure text; shared by
Streamlit and FastAPI.
"""

from __future__ import annotations

from typing import Any, Dict

_CHAT_COMMENT_HEADER = "💬 **AI Chat 분석 결과"
_ANALYSIS_COMMENT_HEADER = "🤖 AI 분석 결과"

# Prefixes of comments auto-registered by this tool, taken from the headers
# format_analysis_as_comment() writes so the two cannot drift apart.
_AI_COMMENT_SIGNATURES = (_CHAT_COMMENT_HEADER, _ANALYSIS_COMMENT_HEADER)

# System/automated registrants whose comments are not developer input.
_EXCLUDED_COMMENT_USERS = ("utopia", "mx ax development")


def format_analysis_as_comment(context: Dict[str, Any]) -> str:
    """Render an analysis result as PLM comment text.

    `from_chat` selects the chat-answer shape; anything else is treated as the
    problem/root_cause/solution triple the PLM analyze tab produces.
    """
    if context.get("from_chat"):
        return f"{_CHAT_COMMENT_HEADER}**\n\n{context.get('answer', 'N/A')}"

    return "\n".join(
        [
            _ANALYSIS_COMMENT_HEADER,
            "",
            "**문제점:**",
            context.get("problem", "N/A"),
            "",
            "**근본 원인:**",
            context.get("root_cause", "N/A"),
            "",
            "**해결 방안:**",
            context.get("solution", "N/A"),
        ]
    )


def build_comment_payload(
    division_code: str,
    defect_code: str,
    comment: str,
    create_user: str,
    system_code: str = "AI_ANALYSIS",
) -> Dict[str, Any]:
    """Request body for submit_comment()."""
    return {
        "divisionCode": division_code,
        "systemCode": system_code,
        "defectCode": defect_code,
        "defectComment": comment,
        "createUser": create_user,
        "changeType": "S",
        "docAttachedYn": "N",
    }


def is_ai_generated_comment(text: str) -> bool:
    stripped = (text or "").lstrip()
    return any(stripped.startswith(sig) for sig in _AI_COMMENT_SIGNATURES)


def is_excluded_comment_user(history_user: str) -> bool:
    name = (history_user or "").lower()
    return any(excluded in name for excluded in _EXCLUDED_COMMENT_USERS)


def format_comment_line(comment: Dict[str, Any]) -> str:
    """One developer comment as a bullet, prefixed with author and date."""
    header = " · ".join(x for x in [comment.get("user", ""), comment.get("date", "")] if x)
    text = comment.get("text", "")
    return f"- ({header}) {text}" if header else f"- {text}"
