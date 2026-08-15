"""FastAPI wrapper for the existing RAG engine.

Start with:
    uvicorn backend.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI
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


app = FastAPI(title="AI Project RAG Backend")
_engine = None


def get_engine():
    global _engine
    if _engine is None:
        from ril_rag_chat import RilRagChat

        _engine = RilRagChat(model_name=get_default_llm_model("gemma4:12b"))
    return _engine


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
