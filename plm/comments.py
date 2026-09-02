"""PLM 코멘트 텍스트 규칙.

What this tool writes into a defect comment, and how to tell those
auto-written comments back apart from developer input. Pure text; no web
framework involved.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

_CHAT_COMMENT_HEADER = "💬 **AI Chat 분석 결과"
_ANALYSIS_COMMENT_HEADER = "🤖 AI 분석 결과"

# Prefixes of comments auto-registered by this tool, taken from the headers
# format_analysis_as_comment() writes so the two cannot drift apart.
_AI_COMMENT_SIGNATURES = (_CHAT_COMMENT_HEADER, _ANALYSIS_COMMENT_HEADER)

# System/automated registrants whose comments are not developer input.
_EXCLUDED_COMMENT_USERS = ("utopia", "mx ax development", "CPP1 MAP PORTAL", "SYSTEM")


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


_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)


def render_comment_for_plm(comment: str) -> Tuple[str, bool]:
    """Prepare comment text for PLM, and say whether it needs editor mode.

    PLM collapses a comment into a single line unless it is sent as markup, so
    an analysis written over several lines arrives as one run-on paragraph.
    Line breaks therefore become `<br>` and the markdown bold this tool writes
    becomes `<b>`.

    Returns `(text, needs_editor)`. A plain one-liner is returned untouched:
    without tags there is nothing for editor mode to render, and escaping it
    would only risk showing entities to the reader.
    """
    comment = comment or ""
    if "\n" not in comment and "**" not in comment:
        return comment, False

    # Escape first: log excerpts and stack traces carry <, > and & of their own.
    escaped = comment.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    marked_up = _BOLD.sub(r"<b>\1</b>", escaped)
    return marked_up.replace("\r\n", "\n").replace("\n", "<br>"), True


def build_comment_payload(
    division_code: str,
    defect_code: str,
    comment: str,
    create_user: str,
    system_code: str = "AI_ANALYSIS",
    change_type: str = "S",
    comment_id: str = "",
) -> Dict[str, Any]:
    """Request body for submit_comment(). `change_type` is S/M/D."""
    text, needs_editor = render_comment_for_plm(comment)

    payload = {
        "divisionCode": division_code,
        "systemCode": system_code,
        "defectCode": defect_code,
        "defectComment": text,
        "createUser": create_user,
        "changeType": change_type,
        "docAttachedYn": "N",
    }
    if needs_editor:
        # Per the PLM API guide: tags only reach the screen in editor mode.
        payload["isCommentEditorYn"] = "Y"
    if comment_id:
        payload["defectCommentId"] = comment_id
    return payload


# Comments this tool wrote sit on PLM in two shapes: markdown from before the
# editor-mode switch, HTML after it. Strip both so one rule matches either.
_MARKUP = re.compile(r"</?b>|<br\s*/?>|\*\*")


def _without_markup(text: str) -> str:
    return _MARKUP.sub("", text or "").lstrip()


def is_ai_generated_comment(text: str) -> bool:
    stripped = _without_markup(text)
    return any(stripped.startswith(_without_markup(sig)) for sig in _AI_COMMENT_SIGNATURES)


def is_excluded_comment_user(history_user: str) -> bool:
    name = (history_user or "").lower()
    return any(excluded in name for excluded in _EXCLUDED_COMMENT_USERS)


def format_comment_line(comment: Dict[str, Any]) -> str:
    """One developer comment as a bullet, prefixed with author and date."""
    header = " · ".join(x for x in [comment.get("user", ""), comment.get("date", "")] if x)
    text = comment.get("text", "")
    return f"- ({header}) {text}" if header else f"- {text}"
