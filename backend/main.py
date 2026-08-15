"""FastAPI wrapper for the existing RAG engine.

Start with:
    uvicorn backend.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
import threading
from typing import Any, Dict, List, Optional
import uuid

from fastapi import File, Form, UploadFile, FastAPI, HTTPException
from pydantic import BaseModel, Field

from rag.llm_provider import get_default_llm_model, get_llm_runtime_label


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    current_file: Optional[str] = None
    chat_history: Optional[List[Dict[str, str]]] = None
    top_k: Optional[int] = None
    health_kpi: Optional[str] = None


class AskResponse(BaseModel):
    answer: str
    ids: List[str]
    metas: List[Dict[str, Any]]
    thinking: str = ""


class FilesResponse(BaseModel):
    files: List[str]


class ResetResponse(BaseModel):
    success: bool


class MetadataResponse(BaseModel):
    metadatas: List[Dict[str, Any]]
    ids: List[str]


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
    created_at: str
    updated_at: str


app = FastAPI(title="AI Project RAG Backend")
_engine = None
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1)


def get_engine():
    global _engine
    if _engine is None:
        from ril_rag_chat import RilRagChat

        _engine = RilRagChat(model_name=get_default_llm_model("gemma4:12b"))
    return _engine


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
    from app.pipeline import run_analysis_core

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
    except Exception as e:
        _set_job(job_id, status="error", error=str(e), message="분석 실패")


@app.get("/health")
def health() -> Dict[str, str]:
    model_name = get_default_llm_model("gemma4:12b")
    return {
        "status": "ok",
        "model": model_name,
        "runtime": get_llm_runtime_label(model_name),
    }


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    answer, ids, metas, thinking = get_engine().ask(
        req.question,
        current_file=req.current_file,
        chat_history=req.chat_history,
        top_k=req.top_k,
        health_kpi=req.health_kpi,
    )
    return AskResponse(
        answer=answer,
        ids=ids,
        metas=metas,
        thinking=thinking or "",
    )


@app.get("/files", response_model=FilesResponse)
def files() -> FilesResponse:
    return FilesResponse(files=get_engine().get_all_files())


@app.post("/db/reset", response_model=ResetResponse)
def reset_db() -> ResetResponse:
    return ResetResponse(success=bool(get_engine().reset_db()))


@app.get("/metadata", response_model=MetadataResponse)
def metadata(source_file: Optional[str] = None, batch_size: int = 500) -> MetadataResponse:
    from app.helpers import get_collection_metadatas_batched

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


@app.post("/jobs/analyze", response_model=AnalyzeJobResponse)
async def create_analyze_job(
    files: List[UploadFile] = File(...),
    use_slice: bool = Form(False),
    start_t: str = Form(""),
    end_t: str = Form(""),
) -> AnalyzeJobResponse:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    job_id = uuid.uuid4().hex
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

    now = datetime.now().isoformat(timespec="seconds")
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "progress": 0,
            "message": "작업 대기 중",
            "current_file": None,
            "report_path": None,
            "payload_path": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }

    _executor.submit(_run_analyze_job, job_id, file_paths, use_slice, start_t, end_t)
    return AnalyzeJobResponse(job_id=job_id)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    return JobStatusResponse(**_get_job(job_id))
