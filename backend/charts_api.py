"""Chart series over HTTP.

The Streamlit UI imports `core.charts` directly — a rerun must not pay a round
trip. A browser frontend cannot, so the same builders are exposed here.

What comes back is the builder's contract as JSON: a `status` saying whether
there is anything to draw, plus the series itself. It is deliberately not a
plotly figure, so the caller is free to draw it with whatever it likes.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
import json
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
import pandas as pd

from core.charts import (
    build_call_history_summary,
    build_data_usage_profile,
    build_dns_error_breakdown,
    build_dns_issue_summary,
    build_network_timeline_stats,
    build_power_thermal_panel,
    build_service_state_series,
    build_signal_level_series,
)

router = APIRouter(prefix="/charts", tags=["charts"])

# Chart name -> builder over the session's metadata frame.
CHART_BUILDERS: Dict[str, Callable[[pd.DataFrame], Any]] = {
    "service-state": build_service_state_series,
    "signal-level": build_signal_level_series,
    "call-history": build_call_history_summary,
    "data-usage": build_data_usage_profile,
    "dns-errors": build_dns_error_breakdown,
    "dns-issues": build_dns_issue_summary,
    "network-timeline": build_network_timeline_stats,
    "power-thermal": build_power_thermal_panel,
}


def jsonable(value: Any) -> Any:
    """Convert a builder's result into something a browser can read.

    Frames become row lists with real nulls and ISO timestamps; NaN would be
    invalid JSON, and a raw `Timestamp` is not serialisable at all.
    """
    if isinstance(value, pd.DataFrame):
        return json.loads(value.to_json(orient="records", date_format="iso"))
    if isinstance(value, pd.Series):
        return json.loads(value.to_json(orient="records", date_format="iso"))
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if isinstance(value, float) and pd.isna(value):
        return None
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"  # log payloads never belong in a chart
    return value


def session_frame(source_file: Optional[str]) -> pd.DataFrame:
    """The analyzed session's metadata, as the builders expect it."""
    from backend.main import get_engine
    from core.chroma_helpers import get_collection_metadatas_batched

    where = {"source_file": source_file} if source_file else None
    data = get_collection_metadatas_batched(get_engine().collection, batch_size=500, where=where)
    rows = [row for row in data.get("metadatas", []) if row]
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@router.get("")
def list_charts() -> Dict[str, List[str]]:
    return {"charts": sorted(CHART_BUILDERS)}


@router.get("/{name}")
def chart(name: str, source_file: Optional[str] = None) -> Dict[str, Any]:
    builder = CHART_BUILDERS.get(name)
    if builder is None:
        raise HTTPException(status_code=404, detail=f"Unknown chart: {name}")

    series = builder(session_frame(source_file))
    return {"chart": name, "source_file": source_file, "series": jsonable(series)}
