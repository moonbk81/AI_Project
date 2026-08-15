"""Optional HTTP client for the extracted RAG backend."""

from __future__ import annotations

import os
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
