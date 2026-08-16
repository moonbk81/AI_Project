"""Small PLM service helpers shared by Streamlit and FastAPI."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List

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
