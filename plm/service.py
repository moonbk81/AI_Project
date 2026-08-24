"""Small PLM service helpers shared by Streamlit and FastAPI."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, List

from plm.plm_api_client import CommentRegistrationRequest, DefectRegistrationRequest
from plm.plm_rag_integration import PLMDefectContextBuilder
from plm.plm_rag_integration import create_plm_integration

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_plm_integration():
    return create_plm_integration()


def _extract_defect_codes(result_data: Any) -> List[str]:
    defect_codes: List[str] = []

    if not isinstance(result_data, list):
        return defect_codes

    for result in result_data:
        if not isinstance(result, dict) or "defectCode" not in result:
            continue

        codes = result["defectCode"]
        if isinstance(codes, list):
            defect_codes.extend(str(code).strip() for code in codes if str(code).strip())
        elif isinstance(codes, str):
            defect_codes.extend(code.strip() for code in codes.split(",") if code.strip())

    return defect_codes


def quick_search_defects(
    division_code: str,
    main_owner_id: str,
    status: str,
    search_type: str = "main",
    limit: int = 50,
    client=None,
) -> Dict[str, Any]:
    """Search PLM defects by owner/group and load defect detail rows."""
    if client is None:
        client = get_plm_integration().client

    response = client.get_defect_list(
        division_code=division_code,
        main_owner_id=main_owner_id,
        status=status.lower(),
        search_type=search_type,
    )
    if not response.is_success():
        return {
            "success": False,
            "message": response.get_error_message(),
            "defects": [],
            "defect_codes": [],
            "total_codes": 0,
            "truncated": False,
        }

    result = response.result or {}
    defect_codes = _extract_defect_codes(result.get("resultData", []))
    if not defect_codes:
        return {
            "success": True,
            "message": "No defects found",
            "defects": [],
            "defect_codes": [],
            "total_codes": 0,
            "truncated": False,
        }

    codes_to_fetch = defect_codes[:limit]
    detail_response = client.get_defect_info(
        division_code=division_code,
        defect_codes=codes_to_fetch,
    )
    if not detail_response.is_success():
        return {
            "success": False,
            "message": detail_response.get_error_message(),
            "defects": [],
            "defect_codes": defect_codes,
            "total_codes": len(defect_codes),
            "truncated": len(defect_codes) > limit,
        }

    detail_result = detail_response.result or {}
    return {
        "success": True,
        "message": "",
        "defects": detail_result.get("defectList", []),
        "defect_codes": defect_codes,
        "total_codes": len(defect_codes),
        "truncated": len(defect_codes) > limit,
    }


def get_defect_details(
    division_code: str,
    defect_codes: List[str] | None = None,
    defect_ids: List[str] | None = None,
    client=None,
) -> Dict[str, Any]:
    """Load PLM defect detail rows by defect code or defect id."""
    if client is None:
        client = get_plm_integration().client

    response = client.get_defect_info(
        division_code=division_code,
        defect_codes=defect_codes,
        defect_ids=defect_ids,
    )
    if not response.is_success():
        return {"success": False, "message": response.get_error_message(), "defects": []}

    result = response.result or {}
    return {
        "success": True,
        "message": "",
        "defects": result.get("defectList", []),
    }


def list_attached_files(
    division_code: str,
    defect_code: str,
    attach_type: str = "OP_DEFECT_ATTACH",
    client=None,
) -> Dict[str, Any]:
    """List files attached to a PLM defect."""
    if client is None:
        client = get_plm_integration().client

    response = client.get_file_list(
        division_code=division_code,
        defect_code=defect_code,
        attach_type=attach_type,
    )
    if not response.is_success():
        return {"success": False, "message": response.get_error_message(), "files": []}

    result = response.result if response.result else []
    files: List[Dict[str, Any]] = []
    if isinstance(result, list) and result:
        data = result[0].get("data", []) if isinstance(result[0], dict) else []
        files = [file for file in data if file.get("title") and file.get("fileId")]
    elif isinstance(result, dict):
        data = result.get("data", [])
        files = [file for file in data if file.get("title") and file.get("fileId")]

    return {"success": True, "message": "", "files": files}


def download_attached_file(
    division_code: str,
    doc_id: str,
    title: str,
    file_id: str,
    client=None,
) -> Dict[str, Any]:
    """Download one PLM attached file."""
    if client is None:
        client = get_plm_integration().client

    result = client.download_file(
        division_code=division_code,
        doc_id=doc_id,
        title=title,
        file_id=file_id,
    )
    if not result.get("success"):
        return {
            "success": False,
            "message": result.get("message", "Download failed"),
            "data": None,
            "size": 0,
            "filename": title,
        }

    data = result.get("data") or b""
    return {
        "success": True,
        "message": "",
        "data": data,
        "size": result.get("size", len(data)),
        "filename": title,
    }


def submit_comment(
    payload: Dict[str, Any],
    client=None,
) -> Dict[str, Any]:
    """Register, modify, or delete a PLM defect comment."""
    if client is None:
        client = get_plm_integration().client

    request = CommentRegistrationRequest(**payload)
    response = client.register_comment(request)
    if response.is_success():
        return {
            "success": True,
            "message": "PLM Comment 등록 완료",
            "result": response.result or {},
        }
    return {
        "success": False,
        "message": response.get_error_message(),
        "result": response.result or {},
    }


def register_defect(
    payload: Dict[str, Any],
    client=None,
) -> Dict[str, Any]:
    """Register a new PLM defect."""
    if client is None:
        client = get_plm_integration().client

    request = DefectRegistrationRequest(**payload)
    response = client.register_defect(request)
    if response.is_success():
        return {
            "success": True,
            "message": "PLM Defect 등록 완료",
            "result": response.result or {},
        }
    return {
        "success": False,
        "message": response.get_error_message(),
        "result": response.result or {},
    }


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


def _is_ai_generated_comment(text: str) -> bool:
    stripped = (text or "").lstrip()
    return any(stripped.startswith(sig) for sig in _AI_COMMENT_SIGNATURES)


def _is_excluded_comment_user(history_user: str) -> bool:
    name = (history_user or "").lower()
    return any(excluded in name for excluded in _EXCLUDED_COMMENT_USERS)


def get_human_comments(
    division_code: str,
    defect_code: str,
    client=None,
) -> Dict[str, Any]:
    """Fetch developer-written comments for a defect history."""
    if client is None:
        client = get_plm_integration().client

    response = client.get_defect_history(
        division_code=division_code,
        defect_codes=[defect_code],
    )
    if not response.is_success():
        return {"success": False, "message": response.get_error_message(), "comments": []}

    comments: List[Dict[str, Any]] = []
    result = response.result or {}
    for arr in result.get("defectHistoryListArr", []) or []:
        for entry in arr.get("defectHistoryList", []) or []:
            if entry.get("historyType") != "C":
                continue
            if _is_excluded_comment_user(entry.get("historyUser", "")):
                continue
            text = (entry.get("comment") or "").strip()
            if not text or _is_ai_generated_comment(text):
                continue
            comments.append(
                {
                    "comment": text,
                    "historyDate": entry.get("historyDate", ""),
                    "historyUser": entry.get("historyUser", ""),
                    "commentId": entry.get("commentId", ""),
                }
            )

    return {"success": True, "message": "", "comments": comments}


def build_defect_analysis_context(
    division_code: str,
    defect_code: str,
    integration=None,
) -> Dict[str, Any]:
    """Build PLM defect analysis context."""
    if integration is None:
        integration = get_plm_integration()

    builder = PLMDefectContextBuilder(integration)
    context = builder.build_defect_context(defect_code, division_code)
    return {
        "success": bool(context),
        "message": "" if context else "Defect not found",
        "context": context or {},
    }


def format_comment_line(comment: Dict[str, Any]) -> str:
    """One developer comment as a bullet, prefixed with author and date."""
    header = " · ".join(x for x in [comment.get("user", ""), comment.get("date", "")] if x)
    text = comment.get("text", "")
    return f"- ({header}) {text}" if header else f"- {text}"


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
_REFINE_MIN_CHARS = 200

_REFINE_SYSTEM_PROMPT = """You are an expert at refining technical problem descriptions for intent recognition.
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


def refine_problem_description(problem_content: str, model: str = "") -> str:
    """LLM-refine a PLM problem description for downstream intent recognition.

    Degrades to simplify_problem_description() when the LLM call fails, matching
    the behavior this had while it lived in the Streamlit layer.
    """
    if not problem_content or not problem_content.strip():
        return problem_content
    if len(problem_content) < _REFINE_MIN_CHARS:
        return problem_content

    try:
        from rag.llm_provider import chat

        response = chat(
            model=model,
            messages=[
                {"role": "system", "content": _REFINE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Please refine this problem description:\n\n{problem_content}",
                },
            ],
        )
        refined = (response["message"]["content"] or "").strip()
        return refined if refined else problem_content
    except Exception as e:
        logger.warning("LLM refine failed (%s); falling back to line extraction", e)
        return simplify_problem_description(problem_content)
