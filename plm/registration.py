"""Turning a filled-in defect form into the body PLM expects.

The form a person sees and the payload the API takes are not the same shape:
several fields are fixed for everything this tool files, and one is generated
when left blank. That mapping lives here rather than in a UI.

Shared by Streamlit and FastAPI; nothing here imports either.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

DIVISIONS = {"Mobile": "25", "Network": "26"}
CHANGE_TYPES = ("DRAFT", "OPEN")
PRIORITIES = ("A", "B", "C")
OCCUR_RATES = ("Always", "Sometimes", "Once")

# Fixed for every defect this tool files: a software defect found in
# development, against a manufacturing object.
_REF_OBJECT_TYPE = "MFG"
_DEFECT_CATEGORY = "SW"
_OCCUR_PHASE = "DV"

DEFAULTS = {
    "system_code": "AI_ANALYSIS",
    "change_type": "DRAFT",
    "importance": "B",
    "occur_rate": "Always",
    "project_name": "Galaxy S24",
    "test_unit": "S/W Engineering",
    "function_block": "General",
    "test_item": "Functional Test",
    "detail_function": "General Feature",
}


def build_defect_payload(
    *,
    division_code: str,
    title: str,
    content: str,
    create_user: str,
    external_id: str = "",
    system_code: str = DEFAULTS["system_code"],
    change_type: str = DEFAULTS["change_type"],
    importance: str = DEFAULTS["importance"],
    occur_rate: str = DEFAULTS["occur_rate"],
    project_name: str = DEFAULTS["project_name"],
    test_unit: str = DEFAULTS["test_unit"],
    function_block: str = DEFAULTS["function_block"],
    test_item: str = DEFAULTS["test_item"],
    detail_function: str = DEFAULTS["detail_function"],
    reappearance: str = "",
    forecast: str = "",
    sw_version: str = "",
    external_id_factory=None,
) -> Dict[str, Any]:
    """Request body for register_defect().

    `external_id` identifies the defect in the system it came from. Nothing
    upstream supplies one here, so a unique stand-in is generated; pass
    `external_id_factory` to keep that deterministic in a test.
    """
    if not external_id:
        factory = external_id_factory or _generated_external_id
        external_id = factory()

    return {
        "divisionCode": division_code,
        "systemCode": system_code,
        "changeType": change_type,
        "refObjectName": project_name,
        "refObjectType": _REF_OBJECT_TYPE,
        "externalDefectId": external_id,
        "defectCategory": _DEFECT_CATEGORY,
        "createUser": create_user,
        "title": title,
        "inChargeUser": create_user,
        "Content": content,
        "importance": importance,
        "occurRateType": occur_rate,
        "occurPhase": _OCCUR_PHASE,
        "testUnit": test_unit,
        "testItem": test_item,
        "functionBlock": function_block,
        "detailFunctionclass": detail_function,
        # PLM treats an empty optional field as "not provided".
        "reappearancePath": reappearance or None,
        "forecastResult": forecast or None,
        "swVersion": sw_version or None,
    }


def _generated_external_id() -> str:
    from datetime import datetime

    return f"AI_{datetime.now().timestamp()}"


def missing_required(title: str, content: str, create_user: str) -> Optional[str]:
    """What the form still needs before it can be filed, if anything."""
    for value, label in ((title, "제목"), (content, "문제 내용"), (create_user, "작성자 Knox ID")):
        if not str(value or "").strip():
            return label
    return None
