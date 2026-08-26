"""PLM API 호출 계층 — FastAPI 라우트가 쓴다.

텍스트 생성은 `plm/comments.py`(코멘트)와 `plm/prompts.py`(LLM 질의문)에
있고, 여기 있는 함수는 그것들을 PLM/LLM 호출과 엮는 일만 한다.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, List

from plm import local_test
from plm.comments import is_ai_generated_comment, is_excluded_comment_user
from plm.plm_api_client import CommentRegistrationRequest, DefectRegistrationRequest
from plm.plm_rag_integration import PLMDefectContextBuilder
from plm.plm_rag_integration import create_plm_integration
from plm.prompts import REFINE_MIN_CHARS, REFINE_SYSTEM_PROMPT, simplify_problem_description

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
    if local_test.is_enabled():
        return local_test.quick_search(status=status, limit=limit)

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
    if local_test.is_enabled():
        return local_test.defect_details(defect_codes)

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
    if local_test.is_enabled():
        return local_test.attached_files(defect_code)

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
    if local_test.is_enabled():
        return local_test.download_file(title)

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
    if local_test.is_enabled():
        return local_test.accept_write("Comment 등록")

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
    if local_test.is_enabled():
        return local_test.accept_write("Defect 등록")

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


def get_human_comments(
    division_code: str,
    defect_code: str,
    client=None,
) -> Dict[str, Any]:
    """Fetch developer-written comments for a defect history."""
    if local_test.is_enabled():
        return local_test.human_comments(defect_code)

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
            if is_excluded_comment_user(entry.get("historyUser", "")):
                continue
            text = (entry.get("comment") or "").strip()
            if not text or is_ai_generated_comment(text):
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
    if local_test.is_enabled():
        return local_test.analysis_context(defect_code)

    if integration is None:
        integration = get_plm_integration()

    builder = PLMDefectContextBuilder(integration)
    context = builder.build_defect_context(defect_code, division_code)
    return {
        "success": bool(context),
        "message": "" if context else "Defect not found",
        "context": context or {},
    }


def refine_problem_description(problem_content: str, model: str = "") -> str:
    """LLM-refine a PLM problem description for downstream intent recognition.

    Degrades to simplify_problem_description() when the LLM call fails, matching
    the behavior this had while it lived in the UI layer.
    """
    if not problem_content or not problem_content.strip():
        return problem_content
    if len(problem_content) < REFINE_MIN_CHARS:
        return problem_content

    try:
        from rag.llm_provider import chat

        response = chat(
            model=model,
            messages=[
                {"role": "system", "content": REFINE_SYSTEM_PROMPT},
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
