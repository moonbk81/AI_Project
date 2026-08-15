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
    return engine.ask(*args, **kwargs)
