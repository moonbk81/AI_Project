"""RTOS 로그 분석 도구."""

import json
import os

from agent_toolkit.common import _load_report_json


def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_rtos_call_flow_analytics(base_name: str, result_dir: str = "./result") -> str:
    """RTOS 콜을 계층별 체크포인트로 요약한다. 끊긴 지점과 그 직전 근거 줄을 함께 준다."""
    path = os.path.join(result_dir, f"{base_name}_rtos_call_flow.json")
    data = _load_json(path)
    if not data:
        return json.dumps({
            "status": "NO_DATA",
            "message": "RTOS 콜 흐름 분석 결과 파일이 없습니다.",
            "expected_file": path,
        }, ensure_ascii=False)

    calls = []
    for call in data.get("calls", []) or []:
        checkpoints = call.get("checkpoints", {})
        reached = [c for c in call.get("chain", []) if c.get("reached")]
        last = checkpoints.get(reached[-1]["stage"]) if reached else None
        calls.append({
            "direction": call.get("direction"),
            "sid": call.get("sid"),
            "start_time": call.get("start_time"),
            "end_time": call.get("end_time"),
            "status": call.get("status"),
            "states": [f"{s['state']}@{s['time']}" for s in call.get("states", [])],
            "chain": [
                {"stage": c["label"], "reached": c["reached"], "time": c.get("time")}
                for c in call.get("chain", [])
            ],
            "broken_at": (call.get("broken_at") or {}).get("label"),
            "last_reached_evidence": last,
            "fail_cause": call.get("fail_cause"),
            "fail_reason": call.get("fail_reason"),
            "ril_requests": [
                {k: r.get(k) for k in ("name", "token", "req_time", "resp_time", "latency_ms", "response")}
                for r in call.get("ril_requests", [])
            ],
        })

    report = _load_report_json(base_name, result_dir)
    return json.dumps({
        "status": "OK",
        "build_info": report.get("rtos_build_info", {}),
        "kpi": data.get("kpi", {}),
        "calls": calls,
        "unanswered_requests": data.get("unanswered_requests", []),
        "analysis_rule": (
            "RTOS 콜은 UI/TAPI → ofono → rild → RIL-IMSCALL → IMS 계층을 거친다. "
            "broken_at 은 로그가 처음으로 비는 계층이다. 그 계층이 원인이라고 단정하지 말고 "
            "'직전 계층까지는 진행됐고 이 계층의 로그가 없다'고 말하라. "
            "ACTIVE 없이 종료됐는데 broken_at 이 없으면 망/상대방 쪽(무응답, 거절)을 fail_cause 로 설명하라."
        ),
    }, ensure_ascii=False)
