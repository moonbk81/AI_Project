"""Optional HTTP client for the extracted RAG backend."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple


def is_backend_api_enabled() -> bool:
    return os.getenv("USE_BACKEND_API", "0").strip().lower() in {"1", "true", "yes", "on"}


def get_backend_api_url() -> str:
    return os.getenv("BACKEND_API_URL", "http://localhost:8080").rstrip("/")


def ask_via_backend(
    question: str,
    current_file: Optional[str] = None,
    chat_history: Optional[List[Dict[str, str]]] = None,
    top_k: Optional[int] = None,
    health_kpi: Optional[str] = None,
) -> Tuple[str, List[str], List[Dict[str, Any]], str]:
    import requests

    response = requests.post(
        f"{get_backend_api_url()}/ask",
        json={
            "question": question,
            "current_file": current_file,
            "chat_history": chat_history,
            "top_k": top_k,
            "health_kpi": health_kpi,
        },
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    response.raise_for_status()
    data = response.json()
    return (
        data.get("answer", ""),
        data.get("ids", []),
        data.get("metas", []),
        data.get("thinking", ""),
    )


def ask_with_optional_backend(engine, *args, **kwargs):
    if is_backend_api_enabled():
        return ask_via_backend(*args, **kwargs)
    if engine is None:
        raise RuntimeError("Local RAG engine is not available.")
    return engine.ask(*args, **kwargs)


def get_backend_health() -> Dict[str, Any]:
    import requests

    response = requests.get(
        f"{get_backend_api_url()}/health",
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    response.raise_for_status()
    return response.json()


def get_files_via_backend() -> List[str]:
    import requests

    response = requests.get(
        f"{get_backend_api_url()}/files",
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    response.raise_for_status()
    return response.json().get("files", [])


def reset_db_via_backend() -> bool:
    import requests

    response = requests.post(
        f"{get_backend_api_url()}/db/reset",
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    response.raise_for_status()
    return bool(response.json().get("success", False))


def get_files_with_optional_backend(engine) -> List[str]:
    if is_backend_api_enabled():
        return get_files_via_backend()
    if engine is None:
        raise RuntimeError("Local RAG engine is not available.")
    return engine.get_all_files()


def reset_db_with_optional_backend(engine) -> bool:
    if is_backend_api_enabled():
        return reset_db_via_backend()
    if engine is None:
        raise RuntimeError("Local RAG engine is not available.")
    return engine.reset_db()


def create_analyze_job_via_backend(
    uploaded_files,
    use_slice: bool = False,
    start_t: str = "",
    end_t: str = "",
) -> str:
    import requests

    files = []
    for file in uploaded_files or []:
        files.append(
            (
                "files",
                (
                    getattr(file, "name", "uploaded.log"),
                    bytes(file.getbuffer()),
                    "application/octet-stream",
                ),
            )
        )

    response = requests.post(
        f"{get_backend_api_url()}/jobs/analyze",
        files=files,
        data={
            "use_slice": str(bool(use_slice)).lower(),
            "start_t": start_t or "",
            "end_t": end_t or "",
        },
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    response.raise_for_status()
    return response.json()["job_id"]


def get_job_status_via_backend(job_id: str) -> Dict[str, Any]:
    import requests

    response = requests.get(
        f"{get_backend_api_url()}/jobs/{job_id}",
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    response.raise_for_status()
    return response.json()


def wait_for_job_via_backend(job_id: str, poll_interval: float = 1.0) -> Dict[str, Any]:
    while True:
        status = get_job_status_via_backend(job_id)
        if status.get("status") in {"done", "error"}:
            return status
        time.sleep(poll_interval)
