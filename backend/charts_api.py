"""Chart series over HTTP.

An in-process caller imports `core.charts` directly. A browser frontend
cannot, so the same builders are exposed here.

What comes back is the builder's contract as JSON: a `status` saying whether
there is anything to draw, plus the series itself. It is deliberately not a
plotly figure, so the caller is free to draw it with whatever it likes.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field, fields, is_dataclass
import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
import pandas as pd

from core.charts import (
    build_boot_sequence,
    build_binder_proxy_histograms,
    build_call_history_summary,
    build_crash_overview,
    build_data_call_summary,
    build_data_usage_profile,
    build_data_usage_top_by_time,
    build_dns_error_breakdown,
    build_dns_health_warnings,
    build_dns_issue_summary,
    build_internet_stall_report,
    build_network_timeline_stats,
    build_nitz_timeline,
    build_ntn_overview,
    build_power_thermal_panel,
    build_rf_call_timeline,
    build_rilj_overview,
    build_service_state_series,
    build_signal_level_series,
    build_sip_flow,
)

router = APIRouter(prefix="/charts", tags=["charts"])

# Where a chart's input comes from: the session's Chroma metadata, or one of
# the JSON artifacts the analysis pipeline wrote next to it.
METADATA = "metadata"

# Payload suffix the metadata rows are keyed by; artifacts drop it.
_PAYLOAD_SUFFIX = "_payload.json"


@dataclass(frozen=True)
class ChartSpec:
    """One entry of the registry.

    `field` pulls a single key out of the artifact first (several builders take
    one list of a bigger report). `as_items` marks the builders that answer with
    a plain list instead of a contract object, so the response still carries a
    status the frontend can branch on.
    """

    builder: Callable[[Any], Any]
    source: str = METADATA
    field: Optional[str] = None
    as_items: bool = False
    # Frames to slim down before they go over the wire: {contract field: columns}.
    project: Dict[str, List[str]] = dataclass_field(default_factory=dict)


CHART_BUILDERS: Dict[str, ChartSpec] = {
    # From the session's metadata rows.
    "service-state": ChartSpec(build_service_state_series),
    "signal-level": ChartSpec(build_signal_level_series),
    "call-history": ChartSpec(build_call_history_summary, source="report", field="call_sessions"),
    # The timeline frame carries every metadata column of every usage row; the
    # chart needs three of them, and the difference is a quarter of a megabyte.
    "data-usage": ChartSpec(
        build_data_usage_profile,
        project={"timeline": ["time_dt", "app_name", "total_mb"]},
    ),
    "data-usage-top-time": ChartSpec(
        build_data_usage_top_by_time,
        project={
            "frame": ["bucket", "bucket_dt", "app_name", "total_mb", "rank"],
            "table": ["bucket", "rank", "app_name", "total_mb"],
        },
    ),
    "dns-errors": ChartSpec(build_dns_error_breakdown),
    "dns-issues": ChartSpec(build_dns_issue_summary),
    "dns-health": ChartSpec(build_dns_health_warnings, as_items=True),
    "network-timeline": ChartSpec(build_network_timeline_stats),
    "power-thermal": ChartSpec(build_power_thermal_panel),
    # From the analysis report.
    "boot": ChartSpec(build_boot_sequence, source="report"),
    "crash": ChartSpec(build_crash_overview, source="report"),
    "rilj": ChartSpec(build_rilj_overview, source="report"),
    "rf-timeline": ChartSpec(build_rf_call_timeline, source="report"),
    "nitz": ChartSpec(build_nitz_timeline, source="report", field="nitz_history"),
    "binder-proxy": ChartSpec(
        build_binder_proxy_histograms, source="report", field="binder_warnings", as_items=True
    ),
    # From their own artifacts.
    "data-call": ChartSpec(build_data_call_summary, source="datacall"),
    "sip-flow": ChartSpec(build_sip_flow, source="ims_sip"),
    "internet-stall": ChartSpec(build_internet_stall_report, source="internet_stall"),
    "ntn": ChartSpec(build_ntn_overview, source="ntn"),
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


# A dashboard asks for a dozen charts at once and every one of them is built
# from the same metadata. Scanning Chroma once per card made a big session take
# seconds to fill, so the frame is held briefly between requests.
_FRAME_CACHE_SECONDS = 30
_frame_cache: Dict[str, Any] = {}
_frame_cache_lock = threading.Lock()


def _load_session_frame(source_file: Optional[str]) -> pd.DataFrame:
    from backend.main import _metadata_collection
    from core.chroma_helpers import get_collection_metadatas_batched

    where = {"source_file": source_file} if source_file else None
    data = get_collection_metadatas_batched(_metadata_collection(), batch_size=500, where=where)
    rows = [row for row in data.get("metadatas", []) if row]
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def session_frame(source_file: Optional[str]) -> pd.DataFrame:
    """The analyzed session's metadata, as the builders expect it."""
    key = source_file or ""
    now = time.monotonic()

    with _frame_cache_lock:
        cached = _frame_cache.get(key)
        if cached and now - cached[0] < _FRAME_CACHE_SECONDS:
            return cached[1]

    frame = _load_session_frame(source_file)
    with _frame_cache_lock:
        _frame_cache[key] = (now, frame)
    return frame


def clear_frame_cache() -> None:
    """Drop the cached frames — after an analysis or a database reset."""
    with _frame_cache_lock:
        _frame_cache.clear()


def artifact(source_file: Optional[str], name: str) -> Any:
    """One analysis artifact, or an empty payload when it was never written.

    A missing artifact is not an error here: the builders already answer "no
    data" for it, and that is what the caller wants to render.
    """
    from backend.main import _RESULT_ARTIFACTS

    if not source_file or name not in _RESULT_ARTIFACTS:
        return {}

    base = os.path.basename(source_file)
    candidates = [base]
    if base.endswith(_PAYLOAD_SUFFIX):
        candidates.append(base[: -len(_PAYLOAD_SUFFIX)])
    if base.endswith("_report.json"):
        candidates.append(base[: -len("_report.json")])
    if base.endswith(".json"):
        candidates.append(base[: -len(".json")])
    if "__" in base:
        candidates.append(base.split("__", 1)[0])

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        path = os.path.join("./result", f"{candidate}_{name}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
    return {}


def _project(result: Any, columns_by_field: Dict[str, List[str]]) -> Any:
    """Keep only the columns the caller draws, on the frames that are big."""
    for name, columns in columns_by_field.items():
        frame = getattr(result, name, None)
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            keep = [column for column in columns if column in frame.columns]
            object.__setattr__(result, name, frame[keep])
    return result


def chart_input(spec: ChartSpec, source_file: Optional[str]) -> Any:
    if spec.source == METADATA:
        return session_frame(source_file)

    data = artifact(source_file, spec.source)
    if spec.field is None:
        return data
    return (data or {}).get(spec.field, [])


@router.get("")
def list_charts() -> Dict[str, List[str]]:
    return {"charts": sorted(CHART_BUILDERS)}


@router.get("/{name}")
def chart(name: str, source_file: Optional[str] = None) -> Dict[str, Any]:
    spec = CHART_BUILDERS.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown chart: {name}")

    result = spec.builder(chart_input(spec, source_file))
    if spec.project:
        result = _project(result, spec.project)
    if spec.as_items:
        series = {"status": "ok" if result else "no_data", "items": jsonable(result)}
    else:
        series = jsonable(result)
        # A few contracts carry their status per section rather than at the top
        # (the power panel, the satellite KPI row). One rule for the caller:
        # look at `status` first, then look deeper.
        series.setdefault("status", "ok")
    return {"chart": name, "source_file": source_file, "series": series}
