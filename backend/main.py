"""FastAPI wrapper for the existing RAG engine.

Start with:
    uvicorn backend.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import logging
import os
import re
import shutil
import sys
import threading
from typing import Any, Dict, List, Optional
import uuid

from fastapi import File, Form, UploadFile, FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rag.llm_provider import get_default_llm_model, get_llm_provider, get_llm_runtime_label


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    current_file: Optional[str] = None
    chat_history: Optional[List[Dict[str, Any]]] = None
    top_k: Optional[int] = None
    health_kpi: Optional[str] = None


class AskResponse(BaseModel):
    answer: str
    ids: List[str]
    metas: List[Dict[str, Any]]
    thinking: str = ""
    # The retrieved rows, already shaped into the blocks a reader checks the
    # answer against, so every UI renders the same references.
    references: List[Dict[str, Any]] = Field(default_factory=list)


class FilesResponse(BaseModel):
    files: List[str]


class QuickPromptsResponse(BaseModel):
    prompts: Dict[str, str]


class ResetResponse(BaseModel):
    success: bool


class MetadataResponse(BaseModel):
    metadatas: List[Dict[str, Any]]
    ids: List[str]


class HealthKpiResponse(BaseModel):
    base_name: str
    # get_device_health_kpi() returns a JSON *string* that callers splice into an
    # LLM prompt verbatim. Passing it through untouched keeps the prompt text
    # byte-identical to what the in-process caller used to build.
    kpi_json: str


class SessionKpiResponse(BaseModel):
    top_app_name: str
    top_app_mb: float
    avg_signal_level: float
    call_success_rate: float
    call_drop_count: int
    oos_count: int


class SatelliteOverviewResponse(BaseModel):
    base_name: str
    # None when the log carries no NTN traffic at all.
    sat_type: Optional[str] = None
    ntn: Any = Field(default_factory=dict)


class ReportRequest(BaseModel):
    base_name: str = Field(min_length=1)
    current_file: Optional[str] = None


class SatelliteReportRequest(ReportRequest):
    sat_type: str = Field(min_length=1)


class ReportResponse(BaseModel):
    answer: str
    ids: List[str] = Field(default_factory=list)
    metas: List[Dict[str, Any]] = Field(default_factory=list)
    thinking: str = ""


class KnowledgeCaseFilters(BaseModel):
    model_name: List[str] = Field(default_factory=list)
    hardware: List[str] = Field(default_factory=list)
    android_sdk: List[str] = Field(default_factory=list)
    severity: List[str] = Field(default_factory=list)


class KnowledgeResponse(BaseModel):
    ids: List[str]
    documents: List[str]
    metadatas: List[Dict[str, Any]]
    # The same rows shaped into cases, plus the values they can be filtered by.
    cases: List[Dict[str, Any]] = Field(default_factory=list)
    filters: "KnowledgeCaseFilters" = Field(default_factory=lambda: KnowledgeCaseFilters())


class KnowledgeSaveRequest(BaseModel):
    feedback: str = Field(min_length=1)
    severity: str = "Normal"
    # Either the rows to file the case against...
    target_ids: List[str] = Field(default_factory=list)
    build_info: Optional[Dict[str, Any]] = None
    # ...or an answer's retrieved rows, from which both are derived.
    ids: List[str] = Field(default_factory=list)
    metas: List[Dict[str, Any]] = Field(default_factory=list)
    category: str = "Total_Report"


class KnowledgeSaveResponse(BaseModel):
    success: bool


class AnalyzeJobResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int = 0
    message: str = ""
    current_file: Optional[str] = None
    report_path: Optional[str] = None
    payload_path: Optional[str] = None
    error: Optional[str] = None
    # 로그 목록 잡이 찾아 낸 후보들. 다른 잡에서는 비어 있다.
    log_candidates: Optional[List[Dict[str, Any]]] = None
    # 꺼내지 못해 분석에서 빠진 로그들. 나머지는 그대로 분석한다.
    skipped_logs: Optional[List[str]] = None
    created_at: str
    updated_at: str


class JobsResponse(BaseModel):
    jobs: List[JobStatusResponse]


class PlmQuickSearchRequest(BaseModel):
    division_code: str = "25"
    main_owner_id: str = Field(min_length=1)
    status: str = "open"
    search_type: str = "main"
    limit: int = 50


class PlmQuickSearchResponse(BaseModel):
    success: bool
    message: str = ""
    defects: List[Dict[str, Any]] = Field(default_factory=list)
    defect_codes: List[str] = Field(default_factory=list)
    total_codes: int = 0
    truncated: bool = False


class PlmDefectDetailsRequest(BaseModel):
    division_code: str = "25"
    defect_codes: Optional[List[str]] = None
    defect_ids: Optional[List[str]] = None


class PlmDefectDetailsResponse(BaseModel):
    success: bool
    message: str = ""
    defects: List[Dict[str, Any]] = Field(default_factory=list)


class PlmFileListRequest(BaseModel):
    division_code: str = "25"
    defect_code: str = Field(min_length=1)
    attach_type: str = "OP_DEFECT_ATTACH"


class PlmFileListResponse(BaseModel):
    success: bool
    message: str = ""
    files: List[Dict[str, Any]] = Field(default_factory=list)


class PlmFileDownloadRequest(BaseModel):
    division_code: str = "25"
    doc_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    file_id: str = Field(min_length=1)


class PlmGroupsResponse(BaseModel):
    # group key -> display name, as configured in plm/plm_config.yaml
    groups: Dict[str, str] = Field(default_factory=dict)


class PlmGroupUsersResponse(BaseModel):
    group: str
    users: List[str] = Field(default_factory=list)


class PlmAnalysisQueryRequest(BaseModel):
    division_code: str = "25"
    defect_code: str = Field(min_length=1)
    # Developer comments the user ticked on the detail screen.
    comments: List[Dict[str, Any]] = Field(default_factory=list)


class PlmAnalysisQueryResponse(BaseModel):
    success: bool
    message: str = ""
    # The chat question, ready to send.
    query: str = ""
    defect_title: str = ""
    refined_content: str = ""
    original_content: str = ""


class PlmAttachmentScanRequest(BaseModel):
    division_code: str = "25"
    defect_code: str = Field(min_length=1)
    # 훑어 볼 첨부. 비우면 결함의 압축 첨부 전부.
    file_ids: Optional[List[str]] = None


class PlmLogSelection(BaseModel):
    """사용자가 고른 로그 하나. `route` 는 압축 바깥에서 안으로 가는 멤버 이름들."""

    file_id: str = Field(min_length=1)
    route: List[str] = Field(min_length=1)


class PlmAttachmentAnalyzeRequest(BaseModel):
    division_code: str = "25"
    defect_code: str = Field(min_length=1)
    # Only these attachments are downloaded. Empty/None means every archive
    # attachment, which is what the button did before the picker existed.
    file_ids: Optional[List[str]] = None
    # 고른 로그만 꺼내 분석한다. 비우면 첨부 안의 로그를 예전처럼 전부 꺼낸다.
    logs: Optional[List[PlmLogSelection]] = None


class PlmCommentRequest(BaseModel):
    # Either the finished PLM body, or what the user typed.
    payload: Optional[Dict[str, Any]] = None
    form: Optional[Dict[str, Any]] = None


class PlmCommentResponse(BaseModel):
    success: bool
    message: str = ""
    result: Dict[str, Any] = Field(default_factory=dict)


class PlmDefectRegisterRequest(BaseModel):
    # Either the finished PLM body, or the form fields to build it from — a
    # caller should not have to know the API's field names.
    payload: Optional[Dict[str, Any]] = None
    form: Optional[Dict[str, Any]] = None


class PlmDefectRegisterResponse(BaseModel):
    success: bool
    message: str = ""
    result: Dict[str, Any] = Field(default_factory=dict)


class PlmHumanCommentsRequest(BaseModel):
    division_code: str = "25"
    defect_code: str = Field(min_length=1)


class PlmHumanCommentsResponse(BaseModel):
    success: bool
    message: str = ""
    comments: List[Dict[str, Any]] = Field(default_factory=list)


class PlmAnalyzeRequest(BaseModel):
    division_code: str = "25"
    defect_code: str = Field(min_length=1)


class PlmAnalyzeResponse(BaseModel):
    success: bool
    message: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)


class PlmRefineDescriptionRequest(BaseModel):
    content: str = ""
    # Empty means "let the gateway config decide" (RAG_LLM_MODEL).
    model: str = ""


class PlmRefineDescriptionResponse(BaseModel):
    refined: str


app = FastAPI(title="AI Project RAG Backend")

# OpenAI-compatible chat endpoint, so Open WebUI can use this RAG as a model.
from backend.openai_api import router as openai_router  # noqa: E402  (needs `app`)
from backend.charts_api import router as charts_router  # noqa: E402

app.include_router(openai_router)
app.include_router(charts_router)


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class UiStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Browsers ask for this on every page, including /docs."""
    from fastapi.responses import FileResponse

    return FileResponse(os.path.join(_STATIC_DIR, "favicon.svg"), media_type="image/svg+xml")


@app.get("/vendor/plotly.min.js", include_in_schema=False)
def plotly_bundle():
    """Serve the plotly.js that ships with the installed plotly package.

    Keeps the browser frontend working without reaching a CDN.
    """
    import plotly
    from fastapi.responses import FileResponse

    bundle = os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js")
    if not os.path.exists(bundle):
        raise HTTPException(status_code=404, detail="plotly.min.js not found")
    return FileResponse(bundle, media_type="application/javascript")


@app.get("/", include_in_schema=False)
def root():
    """Open the built-in browser UI by default."""
    return RedirectResponse(url="/ui/")


# Browser frontend: the chart contracts from core/charts, drawn client side.
app.mount("/ui", UiStaticFiles(directory=_STATIC_DIR, html=True), name="ui")

# Exception handler for detailed validation errors
from fastapi.exceptions import RequestValidationError

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    import sys
    print(f"[VALIDATION_ERROR] {exc}", file=sys.stderr)
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
            "body": str(exc)
        },
    )

# Global exception handler for 500 errors

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    import sys
    import traceback
    print(f"[500_ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc),
            "type": type(exc).__name__
        },
    )

_engine = None
_engine_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1)
_RESULT_ARTIFACTS = {
    "report",
    "datacall",
    "ims_sip",
    "ntn",
    "internet_stall",
}
_ARTIFACT_DIRS = ("./payloads", "./result", "./temp_logs")
_CHROMA_DB_PATH = "./chroma_db"


def get_engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                from ril_rag_chat import RilRagChat

                _engine = RilRagChat(model_name=get_default_llm_model("gemma4:12b"))
    return _engine


def _get_engine_status() -> str:
    if _engine is not None:
        return "loaded"
    if _engine_lock.locked():
        return "initializing"
    return "not_loaded"


def _metadata_collection():
    if _engine is not None:
        return _engine.collection

    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=_CHROMA_DB_PATH,
        settings=Settings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(name="ril_logs")


def _list_files_without_loading_engine() -> List[str]:
    if _engine is not None:
        return _engine.get_all_files()

    try:
        from rag.ingest import get_all_files as get_all_ingested_files

        return get_all_ingested_files(_metadata_collection())
    except Exception as exc:
        print(f"[FILES] lightweight listing failed: {exc}", file=sys.stderr)
        return []


def _set_job(job_id: str, **updates):
    with _jobs_lock:
        job = _jobs[job_id]
        job.update(updates)
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _get_job(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        return dict(job)


def _run_analyze_job(job_id: str, file_paths: List[str], use_slice: bool, start_t: str, end_t: str):
    from core.analysis_pipeline import run_analysis_core

    def progress(message, value=None):
        updates: Dict[str, Any] = {}
        if message:
            updates["message"] = message
        if value is not None:
            updates["progress"] = int(value)
        if updates:
            _set_job(job_id, **updates)

    try:
        _set_job(job_id, status="running", message="분석 작업을 시작합니다.", progress=0)
        result = run_analysis_core(
            file_paths,
            use_slice=use_slice,
            start_t=start_t,
            end_t=end_t,
            ai_engine=get_engine(),
            progress_callback=progress,
        )
        _set_job(
            job_id,
            status="done",
            progress=100,
            message="분석 완료",
            current_file=result.current_file,
            report_path=result.report_path,
            payload_path=result.payload_path,
        )
        # The session frames cached for the charts are stale the moment new
        # rows land in Chroma.
        from backend.charts_api import clear_frame_cache

        clear_frame_cache()
    except Exception as e:
        _set_job(job_id, status="error", error=str(e), message="분석 실패")


_ATTACHMENT_CACHE_DIR = os.path.join("./temp_logs", "plm_attachments", "cache")


def _attachment_cache_path(division_code: str, defect_code: str, file_id: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{division_code}_{defect_code}_{file_id}")
    return os.path.join(_ATTACHMENT_CACHE_DIR, key)


def _attachment_bytes(division_code: str, defect_code: str, attachment: Dict[str, Any]) -> bytes:
    """첨부 원본 바이트. 한 번 받아 두면 목록 만들기와 분석이 다시 받지 않는다."""
    from plm.service import download_attached_file

    title = str(attachment.get("title") or "첨부")
    path = _attachment_cache_path(division_code, defect_code, str(attachment.get("fileId")))
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as handle:
            return handle.read()

    response = download_attached_file(
        division_code=division_code,
        doc_id=attachment.get("docId"),
        title=attachment.get("title"),
        file_id=attachment.get("fileId"),
    ) or {}
    if not response.get("success"):
        raise RuntimeError(f"{title} 내려받기 실패: {response.get('message') or '알 수 없는 오류'}")

    data = response.get("data")
    if not data:
        raise RuntimeError(f"{title} 의 내용이 비어 있습니다.")

    os.makedirs(_ATTACHMENT_CACHE_DIR, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return data


def _selected_attachments(
    division_code: str, defect_code: str, file_ids: Optional[List[str]]
) -> List[Dict[str, Any]]:
    """결함의 첨부 목록에서 사용자가 고른 것만. 비어 있으면 전부."""
    from plm.service import list_attached_files

    listing = list_attached_files(division_code=division_code, defect_code=defect_code)
    if not listing.get("success"):
        raise RuntimeError(listing.get("message") or "첨부 파일 목록 조회 실패")

    files = listing.get("files") or []
    if not file_ids:
        return files

    wanted = {str(file_id) for file_id in file_ids}
    picked = [f for f in files if str(f.get("fileId")) in wanted]
    if not picked:
        raise RuntimeError("선택한 첨부 파일을 목록에서 찾지 못했습니다.")
    return picked


def _run_plm_log_scan_job(
    job_id: str,
    division_code: str,
    defect_code: str,
    file_ids: Optional[List[str]] = None,
):
    """고른 첨부 안에서 고를 만한 로그를 찾아 목록으로 만든다.

    본문은 꺼내지 않는다. 압축의 목록만 읽고, 이름에 로그 힌트가 붙은 중첩
    압축만 열어 본다. 실제 추출은 사용자가 고른 뒤에 그 파일만 한다.
    """
    from core.log_archive import find_log_candidates, list_archive_contents
    from plm import log_pipeline

    try:
        _set_job(job_id, status="running", message="첨부 파일 목록 조회 중...", progress=3)
        archives = log_pipeline.select_archive_attachments(
            _selected_attachments(division_code, defect_code, file_ids)
        )
        if not archives:
            _set_job(
                job_id, status="done", progress=100, log_candidates=[],
                message="압축 첨부(ZIP/7z)가 없습니다.",
            )
            return

        candidates: List[Dict[str, Any]] = []
        failures: List[str] = []
        inside: List[str] = []
        for index, attachment in enumerate(archives, 1):
            title = str(attachment.get("title") or "첨부")
            share = int(90 * index / len(archives))
            try:
                _set_job(job_id, message=f"[{index}/{len(archives)}] {title} 내려받는 중...", progress=5 + share)
                data = _attachment_bytes(division_code, defect_code, attachment)

                _set_job(job_id, message=f"{title} 안에서 로그 파일 찾는 중...")
                found = find_log_candidates(data)
                for candidate in found:
                    candidates.append({
                        "file_id": str(attachment.get("fileId")),
                        "title": title,
                        "path": candidate.path,
                        "route": list(candidate.route),
                        "size": candidate.size,
                        "group": candidate.group,
                        "kind": candidate.kind,
                    })
                if not found:
                    # 왜 못 찾았는지는 안에 무엇이 있었는지를 봐야 안다.
                    inside.extend(list(list_archive_contents(data))[:12])
            except Exception as e:
                # 첨부 하나가 실패해도 나머지 목록은 쓸모가 있다.
                logging.getLogger(__name__).error("Scan failed for %s: %s", title, e)
                failures.append(title)

        note = f" ({', '.join(failures)} 는 읽지 못했습니다)" if failures else ""
        if candidates:
            message = f"로그 파일 {len(candidates)}개를 찾았습니다.{note}"
        else:
            # 이름만 봐도 다음에 무엇을 해야 할지 알 수 있게, 안에 있던 것을 적는다.
            seen = ", ".join(inside[:8]) if inside else "(목록을 읽지 못했습니다)"
            message = f"분석할 만한 로그 파일을 찾지 못했습니다.{note} 안에 있던 파일: {seen}"

        _set_job(job_id, status="done", progress=100, log_candidates=candidates, message=message)
    except Exception as e:
        _set_job(job_id, status="error", error=str(e), message="로그 목록 만들기 실패")


def _run_plm_selected_logs_job(
    job_id: str,
    division_code: str,
    defect_code: str,
    selections: List[Dict[str, Any]],
):
    """사용자가 고른 로그만 압축에서 꺼내 분석한다."""
    from core.log_archive import read_by_route

    try:
        _set_job(job_id, status="running", message="첨부 파일 목록 조회 중...", progress=3)
        files = _selected_attachments(
            division_code, defect_code, [str(item["file_id"]) for item in selections]
        )
        by_id = {str(f.get("fileId")): f for f in files}

        upload_dir = os.path.join("./temp_logs", "plm_attachments", job_id)
        os.makedirs(upload_dir, exist_ok=True)

        file_paths: List[str] = []
        skipped: List[str] = []
        for index, item in enumerate(selections, 1):
            route = list(item["route"])
            label = "/".join(route)
            name = os.path.basename(route[-1])

            # 하나가 실패해도 나머지는 꺼낸다. 로그 스무 개를 고른 사람이 한
            # 파일 때문에 처음부터 다시 하게 둘 이유가 없다.
            try:
                attachment = by_id.get(str(item["file_id"]))
                if attachment is None:
                    raise RuntimeError("이 로그가 든 첨부를 목록에서 찾지 못했습니다")

                _set_job(
                    job_id,
                    message=f"[{index}/{len(selections)}] {name} 꺼내는 중...",
                    progress=3 + int(7 * index / len(selections)),
                )
                content = read_by_route(
                    _attachment_bytes(division_code, defect_code, attachment), route
                )
                if not content:
                    raise RuntimeError("내용이 비어 있습니다")

                # 폴더가 달라도 이름이 같을 수 있다(ap_silentlog 안이 특히 그렇다).
                path = os.path.join(upload_dir, name)
                if os.path.exists(path):
                    path = os.path.join(upload_dir, re.sub(r"[^A-Za-z0-9_.-]", "_", label))
                with open(path, "wb") as handle:
                    handle.write(content)
                file_paths.append(path)
            except Exception as e:
                logging.getLogger(__name__).error("Could not extract %s: %s", label, e)
                skipped.append(f"{name} ({e})")

        if not file_paths:
            _set_job(
                job_id,
                status="error",
                message="선택한 로그를 하나도 꺼내지 못했습니다.",
                error="; ".join(skipped) or "꺼낼 로그가 없습니다.",
                skipped_logs=skipped,
            )
            return

        _set_job(
            job_id,
            skipped_logs=skipped,
            progress=10,
            message=(f"{len(file_paths)}개 LOG 파일 분석 시작"
                     + (f" ({len(skipped)}개는 건너뜀)" if skipped else "")),
        )
        _run_analyze_job(job_id, file_paths, False, "", "")

    except Exception as e:
        _set_job(job_id, status="error", error=str(e), message="선택한 로그 분석 실패")


def _run_plm_attachment_job(
    job_id: str,
    division_code: str,
    defect_code: str,
    file_ids: Optional[List[str]] = None,
):
    """Fetch a defect's ZIP attachments, pull the logs out, then analyze them.

    This used to run in the UI process and leave the files in session state.
    Here the whole thing is one job, so a browser only has to poll for
    progress.

    `file_ids` narrows the work to the attachments the user ticked; downloading
    every archive on a defect that carries several is what made this slow.
    """
    from plm import log_pipeline
    from plm.service import download_attached_file

    try:
        _set_job(job_id, status="running", message="첨부 파일 목록 조회 중...", progress=2)

        files = _selected_attachments(division_code, defect_code, file_ids)

        def download(doc_id, title, file_id):
            return download_attached_file(
                division_code=division_code, doc_id=doc_id, title=title, file_id=file_id
            )

        upload_dir = os.path.join("./temp_logs", "plm_attachments", job_id)
        os.makedirs(upload_dir, exist_ok=True)

        file_paths: List[str] = []
        for event in log_pipeline.extract_logs_from_attachments(files, download):
            if event.kind == log_pipeline.DOWNLOADING:
                _set_job(
                    job_id,
                    message=f"[{event.index}/{event.total}] {event.title} 내려받는 중...",
                    progress=2 + int(8 * event.index / max(1, event.total)),
                )
            elif event.kind == log_pipeline.EXTRACTING:
                _set_job(job_id, message=f"{event.title} 에서 LOG 파일 추출 중...")
            elif event.kind == log_pipeline.LOG_READY:
                path = os.path.join(upload_dir, os.path.basename(event.filename))
                with open(path, "wb") as handle:
                    handle.write(event.content)
                file_paths.append(path)
                _set_job(job_id, message=f"{event.filename} 추출 완료")

        if not file_paths:
            _set_job(
                job_id,
                status="done",
                progress=100,
                message="첨부에서 분석 가능한 LOG 파일을 찾지 못했습니다.",
            )
            return

        _set_job(job_id, message=f"{len(file_paths)}개 LOG 파일 분석 시작", progress=10)
        _run_analyze_job(job_id, file_paths, False, "", "")

    except Exception as e:
        _set_job(job_id, status="error", error=str(e), message="첨부 로그 분석 실패")


def _new_job(message: str) -> str:
    job_id = uuid.uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "progress": 0,
            "message": message,
            "current_file": None,
            "report_path": None,
            "payload_path": None,
            "error": None,
            "skipped_logs": None,
            "created_at": now,
            "updated_at": now,
        }
    return job_id


def _reset_artifact_dirs():
    for folder in _ARTIFACT_DIRS:
        if os.path.exists(folder):
            shutil.rmtree(folder)
        os.makedirs(folder, exist_ok=True)


@app.get("/health")
def health() -> Dict[str, Any]:
    model_name = get_default_llm_model("gemma4:12b")
    with _jobs_lock:
        active_jobs = len([job for job in _jobs.values() if job.get("status") in {"pending", "running"}])
    return {
        "status": "ok",
        "model": model_name,
        "provider": get_llm_provider(),
        "runtime": get_llm_runtime_label(model_name),
        "engine_status": _get_engine_status(),
        "engine_loaded": _engine is not None,
        "engine_initializing": _engine_lock.locked(),
        "chroma_db_path": _CHROMA_DB_PATH,
        "artifact_dirs": {folder: os.path.exists(folder) for folder in _ARTIFACT_DIRS},
        "active_jobs": active_jobs,
        "supported_artifacts": sorted(_RESULT_ARTIFACTS),
    }


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    from dataclasses import asdict

    from core.references import build_reference_blocks

    # A caller that does not track the device KPI should not have to: it is
    # derived from the same file the question is about.
    health_kpi = req.health_kpi
    if health_kpi is None and req.current_file:
        try:
            health_kpi = _health_kpi_for(req.current_file)
        except Exception as e:
            print(f"[ASK] health KPI unavailable: {e}", file=sys.stderr)

    answer, ids, metas, thinking = get_engine().ask(
        req.question,
        current_file=req.current_file,
        chat_history=req.chat_history,
        top_k=req.top_k,
        health_kpi=health_kpi,
    )
    return AskResponse(
        answer=answer,
        ids=ids,
        metas=metas,
        thinking=thinking or "",
        references=[asdict(block) for block in build_reference_blocks(metas)],
    )


@app.get("/files", response_model=FilesResponse)
def files() -> FilesResponse:
    return FilesResponse(files=_list_files_without_loading_engine())


@app.get("/quick-prompts", response_model=QuickPromptsResponse)
def quick_prompts() -> QuickPromptsResponse:
    from core.config import QUICK_PROMPTS

    return QuickPromptsResponse(prompts={key: str(value or "") for key, value in QUICK_PROMPTS.items()})


@app.post("/db/reset", response_model=ResetResponse)
def reset_db() -> ResetResponse:
    success = bool(get_engine().reset_db())
    if success:
        from backend.charts_api import clear_frame_cache

        clear_frame_cache()
        _reset_artifact_dirs()
        with _jobs_lock:
            _jobs.clear()
    return ResetResponse(success=success)


@app.get("/metadata", response_model=MetadataResponse)
def metadata(source_file: Optional[str] = None, batch_size: int = 500) -> MetadataResponse:
    from core.chroma_helpers import get_collection_metadatas_batched

    where = {"source_file": source_file} if source_file else None
    data = get_collection_metadatas_batched(
        get_engine().collection,
        batch_size=batch_size,
        where=where,
    )
    return MetadataResponse(
        metadatas=data.get("metadatas", []),
        ids=data.get("ids", []),
    )


@app.get("/results/{base_name}/{artifact}")
def result_json(base_name: str, artifact: str):
    if artifact not in _RESULT_ARTIFACTS:
        raise HTTPException(status_code=400, detail=f"Unsupported artifact: {artifact}")
    safe_base = os.path.basename(base_name)
    path = os.path.join("./result", f"{safe_base}_{artifact}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Result artifact not found: {artifact}")
    import json

    with open(path, "r", encoding="utf-8") as f:
        return JSONResponse(content=json.load(f))


def _health_kpi_for(current_file: str) -> str:
    """KPI JSON for the analyzed file a question is about."""
    from agent_toolkit.kpi_tools import get_device_health_kpi

    base = os.path.basename(current_file).replace("_payload.json", "")
    return get_device_health_kpi(base) if base else ""


@app.get("/health-kpi/{base_name}", response_model=HealthKpiResponse)
def health_kpi(base_name: str) -> HealthKpiResponse:
    """Device health KPI summary derived from the backend's ./result artifacts.

    Note: get_device_health_kpi() reports a missing report by returning
    {"error": ...} as its JSON string rather than raising, and callers splice
    that straight into the prompt. We preserve that instead of returning 404,
    so serving this over HTTP instead of in process does not change behavior.
    """
    from agent_toolkit.kpi_tools import get_device_health_kpi

    safe_base = os.path.basename(base_name)
    return HealthKpiResponse(
        base_name=safe_base,
        kpi_json=get_device_health_kpi(safe_base),
    )


@app.get("/dashboard/kpi", response_model=SessionKpiResponse)
def dashboard_kpi(source_file: Optional[str] = None) -> SessionKpiResponse:
    """Headline device-state numbers for one analyzed session."""
    from core.chroma_helpers import get_collection_metadatas_batched
    from core.dashboard_kpi import compute_session_kpi

    where = {"source_file": source_file} if source_file else None
    data = get_collection_metadatas_batched(_metadata_collection(), batch_size=500, where=where)
    return SessionKpiResponse(**compute_session_kpi(data.get("metadatas", [])))


@app.get("/satellite/{base_name}", response_model=SatelliteOverviewResponse)
def satellite_overview(base_name: str) -> SatelliteOverviewResponse:
    """Satellite artifacts plus the detected constellation for one log."""
    from agent_toolkit.satellite_tools import load_satellite_overview

    safe_base = os.path.basename(base_name)
    return SatelliteOverviewResponse(base_name=safe_base, **load_satellite_overview(safe_base))


@app.post("/reports/session", response_model=ReportResponse)
def session_report(req: ReportRequest) -> ReportResponse:
    """Current-session diagnostic report."""
    from core.reports import generate_session_report

    return ReportResponse(
        **generate_session_report(
            get_engine(),
            os.path.basename(req.base_name),
            current_file=req.current_file,
        )
    )


@app.post("/reports/satellite", response_model=ReportResponse)
def satellite_report(req: SatelliteReportRequest) -> ReportResponse:
    """Satellite (NTN) report for the given constellation."""
    from core.reports import generate_satellite_report

    try:
        report = generate_satellite_report(
            get_engine(),
            os.path.basename(req.base_name),
            req.sat_type,
            current_file=req.current_file,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ReportResponse(**report)


@app.get("/knowledge", response_model=KnowledgeResponse)
def knowledge() -> KnowledgeResponse:
    from dataclasses import asdict

    from core.knowledge import build_cases, build_filters

    data = get_engine().knowledge_collection.get() or {}
    cases = build_cases(data)

    return KnowledgeResponse(
        ids=data.get("ids", []),
        documents=data.get("documents", []),
        metadatas=data.get("metadatas", []),
        # Already shaped into cases, so every UI lists them the same way.
        cases=[asdict(case) for case in cases],
        filters=KnowledgeCaseFilters(**asdict(build_filters(cases))),
    )


class KnowledgeCategoryRequest(BaseModel):
    text: str = ""
    categories: Optional[List[str]] = None


class KnowledgeCategoryResponse(BaseModel):
    category: str


@app.post("/knowledge/recommend-category", response_model=KnowledgeCategoryResponse)
def recommend_knowledge_category(req: KnowledgeCategoryRequest) -> KnowledgeCategoryResponse:
    """Which log type a written-up case reads like it belongs to."""
    from core.knowledge import recommend_category

    return KnowledgeCategoryResponse(category=recommend_category(req.text, req.categories))


@app.post("/knowledge", response_model=KnowledgeSaveResponse)
def save_knowledge(req: KnowledgeSaveRequest) -> KnowledgeSaveResponse:
    from core.knowledge import build_info as build_info_from_metas, target_ids as ids_for_category

    # A caller that has an answer's retrieved rows should not also have to work
    # out which of them the case covers, or dig the build out of their metadata.
    ids = req.target_ids
    info = req.build_info
    if not ids and req.ids:
        ids = ids_for_category(req.category or "Total_Report", req.ids, req.metas)
    if info is None and req.metas:
        info = build_info_from_metas(req.metas)

    if not ids:
        raise HTTPException(status_code=400, detail="사례로 묶을 로그가 없습니다.")

    success = get_engine().save_knowledge(ids, req.feedback, severity=req.severity, build_info=info)
    return KnowledgeSaveResponse(success=bool(success))


@app.post("/jobs/analyze", response_model=AnalyzeJobResponse)
async def create_analyze_job(
    files: List[UploadFile] = File(...),
    use_slice: bool = Form(False),
    start_t: str = Form(""),
    end_t: str = Form(""),
) -> AnalyzeJobResponse:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    job_id = _new_job("작업 대기 중")
    upload_dir = os.path.join("./temp_logs", "backend_uploads", job_id)
    os.makedirs(upload_dir, exist_ok=True)
    file_paths: List[str] = []

    for uploaded in files:
        safe_name = os.path.basename(uploaded.filename or "uploaded.log")
        path = os.path.join(upload_dir, safe_name)
        content = await uploaded.read()
        with open(path, "wb") as f:
            f.write(content)
        file_paths.append(path)

    _executor.submit(_run_analyze_job, job_id, file_paths, use_slice, start_t, end_t)
    return AnalyzeJobResponse(job_id=job_id)


@app.get("/jobs", response_model=JobsResponse)
def list_jobs(limit: int = 20) -> JobsResponse:
    with _jobs_lock:
        jobs = sorted(
            (dict(job) for job in _jobs.values()),
            key=lambda job: job.get("created_at", ""),
            reverse=True,
        )[:limit]
    return JobsResponse(jobs=[JobStatusResponse(**job) for job in jobs])


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    return JobStatusResponse(**_get_job(job_id))


@app.post("/plm/quick-search", response_model=PlmQuickSearchResponse)
def plm_quick_search(req: PlmQuickSearchRequest) -> PlmQuickSearchResponse:
    from plm.service import quick_search_defects

    result = quick_search_defects(
        division_code=req.division_code,
        main_owner_id=req.main_owner_id,
        status=req.status,
        search_type=req.search_type,
        limit=req.limit,
    )
    return PlmQuickSearchResponse(**result)


@app.post("/plm/defects", response_model=PlmDefectDetailsResponse)
def plm_defect_details(req: PlmDefectDetailsRequest) -> PlmDefectDetailsResponse:
    from plm.service import get_defect_details

    result = get_defect_details(
        division_code=req.division_code,
        defect_codes=req.defect_codes,
        defect_ids=req.defect_ids,
    )
    return PlmDefectDetailsResponse(**result)


@app.post("/plm/files", response_model=PlmFileListResponse)
def plm_files(req: PlmFileListRequest) -> PlmFileListResponse:
    from plm.service import list_attached_files

    result = list_attached_files(
        division_code=req.division_code,
        defect_code=req.defect_code,
        attach_type=req.attach_type,
    )
    return PlmFileListResponse(**result)


@app.post("/plm/files/download")
def plm_file_download(req: PlmFileDownloadRequest):
    from plm.service import download_attached_file

    result = download_attached_file(
        division_code=req.division_code,
        doc_id=req.doc_id,
        title=req.title,
        file_id=req.file_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Download failed"))

    return Response(
        content=result.get("data") or b"",
        media_type="application/octet-stream",
        headers={
            "X-Filename": result.get("filename") or req.title,
            "X-File-Size": str(result.get("size") or 0),
        },
    )


class PlmLocalTestResponse(BaseModel):
    enabled: bool
    note: str = ""


class PlmLocalTestRequest(BaseModel):
    enabled: bool


@app.get("/plm/local-test", response_model=PlmLocalTestResponse)
def plm_local_test_state() -> PlmLocalTestResponse:
    from plm import local_test

    return PlmLocalTestResponse(enabled=local_test.is_enabled(), note=local_test.LOCAL_NOTE)


@app.post("/plm/local-test", response_model=PlmLocalTestResponse)
def plm_local_test_toggle(req: PlmLocalTestRequest) -> PlmLocalTestResponse:
    """Answer PLM calls from samples instead of the company network.

    Set PLM_LOCAL_TEST=1 to start this way; this endpoint flips it without a
    restart, for the trip between the office and home.
    """
    from plm import local_test

    return PlmLocalTestResponse(enabled=local_test.set_enabled(req.enabled), note=local_test.LOCAL_NOTE)


@app.get("/plm/groups", response_model=PlmGroupsResponse)
def plm_groups(division_code: str = "25") -> PlmGroupsResponse:
    """Search groups configured for a division, for the search form."""
    from plm.plm_rag_integration import PLMConfigManager

    return PlmGroupsResponse(groups=PLMConfigManager().get_groups_by_division(division_code))


@app.get("/plm/groups/{group_key}/users", response_model=PlmGroupUsersResponse)
def plm_group_users(group_key: str) -> PlmGroupUsersResponse:
    from plm.plm_rag_integration import PLMConfigManager

    return PlmGroupUsersResponse(group=group_key, users=PLMConfigManager().get_users_for_search(group_key))


@app.post("/plm/analysis-query", response_model=PlmAnalysisQueryResponse)
def plm_analysis_query(req: PlmAnalysisQueryRequest) -> PlmAnalysisQueryResponse:
    """Build the chat question that asks for a root cause of this defect.

    The problem description is refined by the LLM first — PLM content is long
    and written for a tracker, not for retrieval.
    """
    from plm.prompts import build_defect_analysis_query
    from plm.service import get_defect_details, refine_problem_description

    details = get_defect_details(division_code=req.division_code, defect_codes=[req.defect_code])
    if not details.get("success") or not details.get("defects"):
        return PlmAnalysisQueryResponse(
            success=False, message=details.get("message") or "결함 정보를 찾지 못했습니다."
        )

    defect = details["defects"][0]
    original = defect.get("content") or ""
    refined = refine_problem_description(original)

    problem = {
        "content": refined,
        "defect_code": defect.get("defectCode"),
        "defect_title": defect.get("plmTitle", ""),
        "reason": defect.get("reason", ""),
        "countermeasure": defect.get("countermeasure", ""),
        "status": defect.get("plmStatus", ""),
        "priority": defect.get("plmPriority", ""),
        "owner": defect.get("mainOwnerName", ""),
    }

    return PlmAnalysisQueryResponse(
        success=True,
        query=build_defect_analysis_query(problem, comments=req.comments),
        defect_title=problem["defect_title"],
        refined_content=refined,
        original_content=original,
    )


@app.post("/plm/attachments/logs", response_model=AnalyzeJobResponse)
def plm_attachment_logs(req: PlmAttachmentScanRequest) -> AnalyzeJobResponse:
    """고른 첨부 안에 어떤 로그가 있는지만 훑는다. 결과는 잡의 log_candidates."""
    job_id = _new_job("PLM 첨부 훑기 대기 중")
    _executor.submit(
        _run_plm_log_scan_job, job_id, req.division_code, req.defect_code, req.file_ids
    )
    return AnalyzeJobResponse(job_id=job_id)


@app.post("/plm/attachments/analyze", response_model=AnalyzeJobResponse)
def plm_attachment_analyze(req: PlmAttachmentAnalyzeRequest) -> AnalyzeJobResponse:
    """Download a defect's attachments, extract the logs and analyze them.

    `logs` 가 오면 그 파일들만 꺼낸다(사용자가 목록에서 고른 경우).
    """
    job_id = _new_job("PLM 첨부 처리 대기 중")
    if req.logs:
        _executor.submit(
            _run_plm_selected_logs_job, job_id, req.division_code, req.defect_code,
            [item.model_dump() for item in req.logs],
        )
        return AnalyzeJobResponse(job_id=job_id)

    _executor.submit(
        _run_plm_attachment_job, job_id, req.division_code, req.defect_code, req.file_ids
    )
    return AnalyzeJobResponse(job_id=job_id)


@app.post("/plm/comment", response_model=PlmCommentResponse)
def plm_comment(req: PlmCommentRequest) -> PlmCommentResponse:
    from plm.comments import build_comment_payload
    from plm.service import submit_comment

    from plm.comments import format_analysis_as_comment

    payload = req.payload
    if payload is None:
        form = req.form or {}

        # A chat answer is registered under this tool's own header, which is
        # also how those comments are recognised again later.
        answer = str(form.get("answer") or "").strip()
        comment = format_analysis_as_comment({"from_chat": True, "answer": answer}) if answer else form.get("comment")

        if not str(comment or "").strip():
            raise HTTPException(status_code=400, detail="코멘트 내용이 비어 있습니다.")
        if not str(form.get("create_user") or "").strip():
            raise HTTPException(status_code=400, detail="작성자 Knox ID 는 필수입니다.")

        payload = build_comment_payload(
            division_code=form.get("division_code", "25"),
            defect_code=form.get("defect_code", ""),
            comment=comment,
            create_user=form["create_user"],
            system_code=form.get("system_code", "AI_ANALYSIS"),
        )

    result = submit_comment(payload)
    # Hand back what was registered, so a UI can show it rather than guess.
    result.setdefault("result", {})["defectComment"] = payload.get("defectComment", "")
    return PlmCommentResponse(**result)


@app.post("/plm/defects/register", response_model=PlmDefectRegisterResponse)
def plm_defect_register(req: PlmDefectRegisterRequest) -> PlmDefectRegisterResponse:
    from plm.registration import build_defect_payload, missing_required
    from plm.service import register_defect

    payload = req.payload
    if payload is None:
        form = req.form or {}
        missing = missing_required(form.get("title"), form.get("content"), form.get("create_user"))
        if missing:
            raise HTTPException(status_code=400, detail=f"{missing} 은(는) 필수입니다.")
        payload = build_defect_payload(**form)

    return PlmDefectRegisterResponse(**register_defect(payload))


@app.post("/plm/defect-history/comments", response_model=PlmHumanCommentsResponse)
def plm_human_comments(req: PlmHumanCommentsRequest) -> PlmHumanCommentsResponse:
    from plm.service import get_human_comments

    result = get_human_comments(
        division_code=req.division_code,
        defect_code=req.defect_code,
    )
    return PlmHumanCommentsResponse(**result)


@app.post("/plm/analyze", response_model=PlmAnalyzeResponse)
def plm_analyze(req: PlmAnalyzeRequest) -> PlmAnalyzeResponse:
    from plm.service import build_defect_analysis_context

    return PlmAnalyzeResponse(
        **build_defect_analysis_context(
            division_code=req.division_code,
            defect_code=req.defect_code,
        )
    )


@app.post("/plm/refine-description", response_model=PlmRefineDescriptionResponse)
def plm_refine_description(req: PlmRefineDescriptionRequest) -> PlmRefineDescriptionResponse:
    """Condense a PLM problem description before it is used as an analysis query.

    Never fails the caller: refine_problem_description() falls back to plain line
    extraction if the LLM gateway is unreachable.
    """
    from plm.service import refine_problem_description

    return PlmRefineDescriptionResponse(
        refined=refine_problem_description(req.content, model=req.model)
    )
