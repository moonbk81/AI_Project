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


def _sip_last(messages: List[Dict[str, Any]]) -> Optional[str]:
    """마지막 SIP 메시지. ↑ 는 단말 → 망, ↓ 는 망 → 단말."""
    if not messages:
        return None
    last = messages[-1]
    return f"{'↑' if last.get('is_outgoing') else '↓'} {last.get('method_code')}"


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
        "sip_final_response": call.get("sip_final_response"),
        "sip_error": call.get("sip_error"),
        "sip_count": len(call.get("sip_messages") or []),
        "sip_last": _sip_last(call.get("sip_messages") or []),
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


@dataclass(frozen=True)
class RtosOemHookOverview:
    status: str
    kpi: Dict[str, Any] = field(default_factory=dict)
    requests: List[Dict[str, Any]] = field(default_factory=list)
    unmatched_unsol: List[Dict[str, Any]] = field(default_factory=list)


def _ipc_label(ipc: Optional[Dict[str, Any]]) -> Optional[str]:
    if not ipc:
        return None
    return f"{ipc.get('main')} / {ipc.get('sub')} / {ipc.get('type')}"


def _oem_request_row(req: Dict[str, Any]) -> Dict[str, Any]:
    tx = req.get("tx") or {}
    rx = req.get("modem_rx") or {}
    gen = rx.get("gen") or {}
    broken = req.get("broken_at") or {}
    reached = [stage for stage in req.get("chain") or [] if stage.get("reached")]
    last = (req.get("checkpoints") or {}).get(reached[-1]["stage"]) if reached else None
    return {
        "token": req.get("token"),
        "req_time": req.get("req_time"),
        "func": f"{req.get('func_id')} {req.get('func_name')}" if req.get("func_id") else None,
        "path": req.get("path"),
        "raw_size": req.get("raw_size"),
        "ipc": _ipc_label(tx),
        "tx_seq": f"{tx['seq']:02x}" if tx.get("seq") is not None else None,
        "modem_rx": (f"{rx.get('main')} {rx.get('sub')} {rx.get('type')}" if rx and not gen
                     else (f"GR {gen.get('main')} {gen.get('sub')}" if gen else None)),
        "modem_error_label": req.get("modem_error_label"),
        "tx_rx_ms": req.get("tx_rx_ms"),
        "expects_modem_response": bool(req.get("expects_modem_response")),
        "expected_response": req.get("expected_response"),
        "verdict": req.get("verdict"),
        "status": req.get("status"),
        "broken_label": broken.get("label"),
        "chain": [{"label": s.get("label"), "reached": bool(s.get("reached")), "time": s.get("time")}
                  for s in req.get("chain") or []],
        "last_evidence": last,
        "error_evidence": req.get("ofono_error_evidence"),
    }


def build_rtos_oem_hook(data: Optional[Dict[str, Any]]) -> RtosOemHookOverview:
    if not data:
        return RtosOemHookOverview(status="no_data")
    requests = [_oem_request_row(req) for req in data.get("requests") or []]
    if not requests:
        return RtosOemHookOverview(status="no_oem_hook", kpi=dict(data.get("kpi") or {}))
    return RtosOemHookOverview(
        status="ok",
        kpi=dict(data.get("kpi") or {}),
        requests=requests,
        unmatched_unsol=list(data.get("unmatched_unsol") or []),
    )
