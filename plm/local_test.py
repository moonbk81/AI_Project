"""Offline PLM: sample data for when the company network is out of reach.

Working from home there is no route to the PLM server, so every screen that
touches it goes blank. With this mode on, the service layer answers from the
samples below instead — the UI flows stay exercisable and nothing is ever
written to a real defect.

The switch lives here rather than in a UI so that the browser view and any
other caller all see the same thing. It starts from `PLM_LOCAL_TEST` and can
be flipped at runtime.
"""

from __future__ import annotations

import io
import os
from typing import Any, Dict, List, Optional
import zipfile

_ENV_FLAG = "PLM_LOCAL_TEST"
_TRUE = {"1", "true", "yes", "on"}

_enabled = os.getenv(_ENV_FLAG, "").strip().lower() in _TRUE

# Marks every write so a sample answer is never mistaken for a real one.
LOCAL_NOTE = "로컬 테스트 모드: 실제 PLM 에는 전송하지 않았습니다."


def is_enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> bool:
    global _enabled
    _enabled = bool(value)
    return _enabled


SAMPLE_DEFECTS: List[Dict[str, Any]] = [
    {
        "defectCode": "P260711-LOCAL01",
        "defectId": "LOCAL_DEFECT_001",
        "plmTitle": "IMS registration retry failure after network handover",
        "plmStatus": "Open",
        "plmPriority": "A",
        "mainOwnerName": "local.tester",
        "createDate": "2026-07-11T09:15:00",
        "content": "After LTE to NR handover, IMS registration retries repeatedly and voice service is delayed.",
        "reason": "Local test root cause: retry timer and registration state are not synchronized after handover.",
        "countermeasure": "Local test solution: reset IMS registration state when handover completion is received.",
    },
    {
        "defectCode": "P260711-LOCAL02",
        "defectId": "LOCAL_DEFECT_002",
        "plmTitle": "Data stall observed after airplane mode toggle",
        "plmStatus": "Resolve",
        "plmPriority": "B",
        "mainOwnerName": "local.owner",
        "createDate": "2026-07-10T16:42:00",
        "content": "Packet data appears connected, but DNS and TCP connection attempts time out after airplane mode toggle.",
        "reason": "Local test root cause: stale network capabilities remain cached after radio reset.",
        "countermeasure": "Local test solution: invalidate network capabilities and trigger reconnect.",
    },
    {
        "defectCode": "P260711-LOCAL03",
        "defectId": "LOCAL_DEFECT_003",
        "plmTitle": "Battery drain during repeated modem recovery",
        "plmStatus": "Close",
        "plmPriority": "C",
        "mainOwnerName": "local.review",
        "createDate": "2026-07-09T11:05:00",
        "content": "Repeated modem recovery events keep radio components active and increase standby battery drain.",
        "reason": "Local test root cause: recovery retry interval is too short under persistent radio errors.",
        "countermeasure": "Local test solution: apply exponential backoff and stop retry after threshold.",
    },
]

# Only the first sample defect carries attachments, so both paths — "has
# attachments" and "has none" — can be walked offline.
SAMPLE_FILES: Dict[str, List[Dict[str, Any]]] = {
    "P260711-LOCAL01": [
        {"title": "dumpstate_local.zip", "fileId": "LOCAL_FILE_1", "docId": "LOCAL_DOC_1", "fileSize": 4096,
         "createDate": "2026-07-11T09:20:00"},
        {"title": "screenshot_local.png", "fileId": "LOCAL_FILE_2", "docId": "LOCAL_DOC_1", "fileSize": 20480,
         "createDate": "2026-07-11T09:21:00"},
    ],
}

SAMPLE_COMMENTS: Dict[str, List[Dict[str, Any]]] = {
    "P260711-LOCAL01": [
        {"comment": "핸드오버 직후에만 재현됩니다. IMS 재등록 로그 첨부했습니다.",
         "historyDate": "2026-07-11 10:02", "historyUser": "local.tester", "commentId": "LOCAL_CMT_1"},
        {"comment": "타이머를 2초로 줄이면 재현 빈도가 낮아집니다.",
         "historyDate": "2026-07-11 14:33", "historyUser": "local.dev", "commentId": "LOCAL_CMT_2"},
    ],
}

# A minimal log the extraction pipeline can actually pull out of the sample ZIP.
_SAMPLE_LOG = b"""04-29 09:40:56.287  1234  5678 D RILJ    : [0123]< RIL_REQUEST_SETUP_DATA_CALL
04-29 09:40:57.001  1234  5678 D ImsService: registration retry #3
04-29 09:40:58.512  1234  5678 E ImsService: registration failed (timeout)
"""


def _sample_zip() -> bytes:
    """실제 첨부와 같은 모양: 로그 하나 + ap_silentlog 폴더 + 로그가 아닌 파일."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("dumpstate.log", _SAMPLE_LOG)
        archive.writestr("ap_silentlog/SILENT_LOG_01.log", _SAMPLE_LOG)
        archive.writestr("ap_silentlog/SILENT_LOG_02.log", _SAMPLE_LOG)
        archive.writestr("screenshot.png", b"not a log")
    return buffer.getvalue()


def _defect(defect_code: str) -> Optional[Dict[str, Any]]:
    return next((d for d in SAMPLE_DEFECTS if d["defectCode"] == defect_code), None)


# --------------------------------------------------- shaped like the service


def quick_search(status: str = "", limit: int = 50) -> Dict[str, Any]:
    matching = [d for d in SAMPLE_DEFECTS if not status or d["plmStatus"].lower() == status.lower()]
    codes = [d["defectCode"] for d in matching]
    return {
        "success": True,
        "message": LOCAL_NOTE,
        "defects": matching[:limit],
        "defect_codes": codes,
        "total_codes": len(codes),
        "truncated": len(codes) > limit,
    }


def defect_details(defect_codes: Optional[List[str]]) -> Dict[str, Any]:
    wanted = set(defect_codes or [])
    defects = [d for d in SAMPLE_DEFECTS if not wanted or d["defectCode"] in wanted]
    return {"success": True, "message": LOCAL_NOTE, "defects": defects}


def attached_files(defect_code: str) -> Dict[str, Any]:
    return {"success": True, "message": LOCAL_NOTE, "files": SAMPLE_FILES.get(defect_code, [])}


def download_file(title: str) -> Dict[str, Any]:
    data = _sample_zip() if str(title).lower().endswith(".zip") else b"local test attachment"
    return {"success": True, "message": LOCAL_NOTE, "data": data, "size": len(data), "filename": title}


def human_comments(defect_code: str) -> Dict[str, Any]:
    return {"success": True, "message": LOCAL_NOTE, "comments": SAMPLE_COMMENTS.get(defect_code, [])}


def accept_write(what: str) -> Dict[str, Any]:
    """Answer a write as if it succeeded, without sending anything."""
    return {"success": True, "message": f"{what} — {LOCAL_NOTE}", "result": {"defectCode": "P260711-LOCAL99"}}


def analysis_context(defect_code: str) -> Dict[str, Any]:
    defect = _defect(defect_code) or SAMPLE_DEFECTS[0]
    return {
        "success": True,
        "message": LOCAL_NOTE,
        "context": {
            "defect_code": defect["defectCode"],
            "title": defect["plmTitle"],
            "status": defect["plmStatus"],
            "priority": defect["plmPriority"],
            "problem": defect["content"],
            "root_cause": defect["reason"],
            "solution": defect["countermeasure"],
            "main_owner": defect["mainOwnerName"],
        },
    }
