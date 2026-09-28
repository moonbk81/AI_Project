"""Presentation contract for the RTOS call-flow analysis.

A call is drawn as its layer chain: which layers logged it and when, and the
first layer that stayed silent.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class RtosCallFlowOverview:
    status: str
    kpi: Dict[str, Any] = field(default_factory=dict)
    calls: List[Dict[str, Any]] = field(default_factory=list)
    unanswered_requests: List[Dict[str, Any]] = field(default_factory=list)


def _to_ms(time_text: Optional[str]) -> Optional[float]:
    if not time_text:
        return None
    try:
        h, m, s = time_text.split(":")
        return (int(h) * 3600 + int(m) * 60 + float(s)) * 1000
    except ValueError:
        return None


def _call_row(call: Dict[str, Any]) -> Dict[str, Any]:
    chain = call.get("chain") or []
    times = [_to_ms(stage.get("time")) for stage in chain if stage.get("reached")]
    origin = min((t for t in times if t is not None), default=None)
    stages = []
    for stage in chain:
        at = _to_ms(stage.get("time"))
        stages.append({
            "label": stage.get("label"),
            "reached": bool(stage.get("reached")),
            "time": stage.get("time"),
            "offset_ms": round(at - origin, 1) if at is not None and origin is not None else None,
        })

    checkpoints = call.get("checkpoints") or {}
    reached = [stage for stage in chain if stage.get("reached")]
    last = checkpoints.get(reached[-1]["stage"]) if reached else None
    broken = call.get("broken_at") or {}
    return {
        "direction": call.get("direction"),
        "sid": call.get("sid"),
        "start_time": call.get("start_time"),
        "end_time": call.get("end_time"),
        "status": call.get("status"),
        "connected": bool(call.get("connected")),
        "broken_label": broken.get("label"),
        "fail_cause": call.get("fail_cause"),
        "fail_reason": call.get("fail_reason"),
        "stages": stages,
        "last_evidence": last,
    }


def build_rtos_call_flow(data: Optional[Dict[str, Any]]) -> RtosCallFlowOverview:
    if not data:
        return RtosCallFlowOverview(status="no_data")
    calls = [_call_row(call) for call in data.get("calls") or []]
    unanswered = list(data.get("unanswered_requests") or [])
    if not calls and not unanswered:
        return RtosCallFlowOverview(status="no_calls", kpi=dict(data.get("kpi") or {}))
    return RtosCallFlowOverview(
        status="ok",
        kpi=dict(data.get("kpi") or {}),
        calls=calls,
        unanswered_requests=unanswered,
    )
