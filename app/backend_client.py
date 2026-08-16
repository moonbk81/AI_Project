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


def get_metadata_via_backend(source_file: Optional[str] = None) -> Dict[str, Any]:
    import requests

    params = {}
    if source_file:
        params["source_file"] = source_file
    response = requests.get(
        f"{get_backend_api_url()}/metadata",
        params=params,
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    response.raise_for_status()
    return response.json()


def get_metadata_with_optional_backend(engine, source_file: Optional[str] = None) -> Dict[str, Any]:
    if is_backend_api_enabled():
        return get_metadata_via_backend(source_file=source_file)
    if engine is None:
        raise RuntimeError("Local RAG engine is not available.")
    from app.helpers import get_collection_metadatas_batched

    where = {"source_file": source_file} if source_file else None
    return get_collection_metadatas_batched(engine.collection, batch_size=500, where=where)


def get_result_json_via_backend(base_name: str, artifact: str, default=None):
    import requests

    response = requests.get(
        f"{get_backend_api_url()}/results/{base_name}/{artifact}",
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    if response.status_code == 404:
        return default
    response.raise_for_status()
    return response.json()


def get_result_json_with_optional_backend(base_name: str, artifact: str, default=None):
    if is_backend_api_enabled():
        return get_result_json_via_backend(base_name, artifact, default=default)

    import json

    path = os.path.join("./result", f"{base_name}_{artifact}.json")
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_knowledge_via_backend() -> Dict[str, Any]:
    import requests

    response = requests.get(
        f"{get_backend_api_url()}/knowledge",
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    response.raise_for_status()
    return response.json()


def save_knowledge_via_backend(
    target_ids: List[str],
    feedback: str,
    severity: str = "Normal",
    build_info: Optional[Dict[str, Any]] = None,
) -> bool:
    import requests

    response = requests.post(
        f"{get_backend_api_url()}/knowledge",
        json={
            "target_ids": target_ids,
            "feedback": feedback,
            "severity": severity,
            "build_info": build_info or {},
        },
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    response.raise_for_status()
    return bool(response.json().get("success", False))


def get_knowledge_with_optional_backend(engine) -> Dict[str, Any]:
    if is_backend_api_enabled():
        return get_knowledge_via_backend()
    if engine is None:
        raise RuntimeError("Local RAG engine is not available.")
    return engine.knowledge_collection.get()


def save_knowledge_with_optional_backend(
    engine,
    target_ids: List[str],
    feedback: str,
    severity: str = "Normal",
    build_info: Optional[Dict[str, Any]] = None,
) -> bool:
    if is_backend_api_enabled():
        return save_knowledge_via_backend(
            target_ids,
            feedback,
            severity=severity,
            build_info=build_info,
        )
    if engine is None:
        raise RuntimeError("Local RAG engine is not available.")
    return bool(
        engine.save_knowledge(
            target_ids,
            feedback,
            severity=severity,
            build_info=build_info,
        )
    )


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


def list_jobs_via_backend(limit: int = 20) -> List[Dict[str, Any]]:
    import requests

    response = requests.get(
        f"{get_backend_api_url()}/jobs",
        params={"limit": limit},
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    response.raise_for_status()
    return response.json().get("jobs", [])


def wait_for_job_via_backend(job_id: str, poll_interval: float = 1.0) -> Dict[str, Any]:
    while True:
        status = get_job_status_via_backend(job_id)
        if status.get("status") in {"done", "error"}:
            return status
        time.sleep(poll_interval)


def plm_quick_search_via_backend(
    division_code: str,
    main_owner_id: str,
    status: str,
    search_type: str = "main",
    limit: int = 50,
) -> Dict[str, Any]:
    import requests

    response = requests.post(
        f"{get_backend_api_url()}/plm/quick-search",
        json={
            "division_code": division_code,
            "main_owner_id": main_owner_id,
            "status": status,
            "search_type": search_type,
            "limit": limit,
        },
        timeout=float(os.getenv("BACKEND_API_TIMEOUT", "300")),
    )
    response.raise_for_status()
    return response.json()


def plm_quick_search_with_optional_backend(
    client,
    division_code: str,
    main_owner_id: str,
    status: str,
    search_type: str = "main",
    limit: int = 50,
) -> Dict[str, Any]:
    if is_backend_api_enabled():
        return plm_quick_search_via_backend(
            division_code=division_code,
            main_owner_id=main_owner_id,
            status=status,
            search_type=search_type,
            limit=limit,
        )
    if client is None:
        raise RuntimeError("PLM API not configured")

    from plm.service import quick_search_defects

    return quick_search_defects(
        division_code=division_code,
        main_owner_id=main_owner_id,
        status=status,
        search_type=search_type,
        limit=limit,
        client=client,
    )
