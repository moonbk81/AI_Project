"""Small PLM service helpers shared by Streamlit and FastAPI."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List

from plm.plm_api_client import CommentRegistrationRequest
from plm.plm_rag_integration import PLMDefectContextBuilder
from plm.plm_rag_integration import create_plm_integration


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
