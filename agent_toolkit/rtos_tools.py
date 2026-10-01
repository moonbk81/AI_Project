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
            "sip_call_id": call.get("sip_call_id"),
            "sip_flow": [
                {k: m.get(k) for k in ("time", "direction", "method_code", "cseq", "is_error", "key_headers")}
                for m in call.get("sip_messages") or []
            ],
            "sip_final_response": call.get("sip_final_response"),
            "sip_error": call.get("sip_error"),
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
            "ACTIVE 없이 종료됐는데 broken_at 이 없으면 망/상대방 쪽(무응답, 거절)을 fail_cause 로 설명하라. "
            "sip_flow 가 있으면 망이 실제로 무엇을 보냈는지(18x/4xx/BYE Reason)를 근거로 삼고, "
            "MO 인데 sip_flow 가 비어 있으면 INVITE 가 망으로 나가지 않은 것이다. "
            "401/407 은 IMS 등록의 정상 인증 절차이므로 오류로 말하지 마라."
        ),
    }, ensure_ascii=False)


def get_rtos_oem_hook_analytics(base_name: str, result_dir: str = "./result") -> str:
    """RIL_REQUEST_OEM_HOOK_RAW 가 ofono → rild → secril → cpif → 모뎀 → 응답까지 이어졌는지 요약한다."""
    path = os.path.join(result_dir, f"{base_name}_rtos_oem_hook.json")
    data = _load_json(path)
    if not data:
        return json.dumps({
            "status": "NO_DATA",
            "message": "RTOS OEM_HOOK_RAW 분석 결과 파일이 없습니다.",
            "expected_file": path,
        }, ensure_ascii=False)

    requests = []
    for req in data.get("requests", []) or []:
        tx = req.get("tx") or {}
        rx = req.get("modem_rx") or {}
        requests.append({
            "token": req.get("token"),
            "req_time": req.get("req_time"),
            "func": f"{req.get('func_id')} {req.get('func_name')}" if req.get("func_id") else None,
            "path": req.get("path"),
            "raw_size": req.get("raw_size"),
            "status": req.get("status"),
            "verdict": req.get("verdict"),
            "broken_at": (req.get("broken_at") or {}).get("label"),
            "chain": [{"stage": c["label"], "reached": c["reached"], "time": c.get("time")}
                      for c in req.get("chain", [])],
            "ipc_tx": {k: tx.get(k) for k in ("main", "sub", "type", "len", "seq", "time")} if tx else None,
            "modem_rx": {k: rx.get(k) for k in ("main", "sub", "type", "seq", "ack", "time", "gen")} if rx else None,
            "expected_response": req.get("expected_response"),
            "modem_error": req.get("modem_error_label"),
            "tx_rx_ms": req.get("tx_rx_ms"),
            "ril_error": req.get("ril_error"),
            "evidence": req.get("ofono_error_evidence") or (req.get("checkpoints") or {}).get("modem_rx"),
        })

    return json.dumps({
        "status": "OK",
        "kpi": data.get("kpi", {}),
        "requests": requests,
        "unmatched_unsol": data.get("unmatched_unsol", []),
        "analysis_rule": (
            "OEM_HOOK_RAW 는 ofono → rild(token) → secril → funcId 분기로 간다. "
            "path=raw_ipc(CP_IMS 0x0F, GPS, SMARTAS, TAS, MCPTT)만 cpif(/dev/umts_ipc0)로 IPC 를 쓰고, "
            "path=local(UICC 0x15, FACTORY 0x12, MISC 0x11)은 secril 이 안에서 처리하므로 모뎀 응답이 없는 게 정상이다. "
            "raw IPC 는 cpif 쓰기가 성공하면 곧바로 ofono 요청이 성공으로 완료되고, 모뎀 응답은 나중에 "
            "UNSOL_OEM_HOOK_RAW 로 따로 온다. 그러니 ofono 요청 성공만으로 모뎀이 받았다고 말하지 마라. "
            "모뎀 응답은 RX 의 ack_seq 가 TX 의 msg_seq 와 같은 것으로 이었다. "
            "SET/EXEC 는 General Response(GEN_CMD)로, GET 은 같은 MAIN/SUB 의 RESP 로 응답이 오고, "
            "GET 을 거절하면 에러 GR 이 온다. General Response 에러 0x8000 은 성공이다. "
            "EVENT/CFRM 타입 IPC 는 모뎀이 응답하지 않는 게 정상이다. "
            "MALFORMED_PARCEL 은 rild 가 응답을 보냈지만 ofono(ril_oem_request_raw_cb)가 parcel 을 못 읽은 것이다."
        ),
    }, ensure_ascii=False)


def get_rtos_cpu_usage_analytics(base_name: str, result_dir: str = "./result") -> str:
    """RTOS 태스크별 CPU 점유율 스냅샷을 요약한다. 전체 부하, 과부하 구간, 많이 쓰는 태스크."""
    path = os.path.join(result_dir, f"{base_name}_rtos_cpu.json")
    data = _load_json(path)
    if not data:
        return json.dumps({
            "status": "NO_DATA",
            "message": "RTOS CPU 점유율 분석 결과 파일이 없습니다.",
            "expected_file": path,
        }, ensure_ascii=False)

    return json.dumps({
        "status": "OK",
        "kpi": data.get("kpi", {}),
        "top_tasks": (data.get("tasks") or [])[:15],
        "busy_windows": data.get("busy_windows", []),
        "samples": [
            {"time": s.get("time"), "total": s.get("total"), "top": s.get("top")}
            for s in data.get("samples", []) or []
        ][:200],
        "analysis_rule": (
            "전체 CPU 점유율은 100 - Idle_Task 다. 0.1% 이상인 태스크만 찍히므로 목록에 없는 태스크는 0% 로 본다. "
            "스냅샷은 띄엄띄엄 찍힌다 — 스냅샷 사이의 시간은 모르는 구간이니 '계속 과부하였다'고 말하지 말고 "
            "'찍힌 스냅샷 N개 중 M개가 90% 이상'처럼 말하라. "
            "avg 는 전체 스냅샷 평균(안 찍힌 스냅샷 0%), max 는 한 스냅샷의 최댓값이다. "
            "태스크는 PID 로 구분한다 (ims_service, aero 는 PID 가 여럿이다). "
            "role 에 '(추정)' 이 붙은 것은 로그의 태스크 번호로 추정한 역할이다."
        ),
    }, ensure_ascii=False)


def get_rtos_crash_analytics(base_name: str, result_dir: str = "./result") -> str:
    """RTOS assert 덤프를 요약한다. 죽은 태스크, 실패한 조건, 레지스터, backtrace, 직전 로그, 재부팅."""
    path = os.path.join(result_dir, f"{base_name}_rtos_crash.json")
    data = _load_json(path)
    if not data:
        return json.dumps({
            "status": "NO_DATA",
            "message": "RTOS 크래시 분석 결과 파일이 없습니다.",
            "expected_file": path,
        }, ensure_ascii=False)

    crashes = []
    for c in data.get("crashes") or []:
        regs = c.get("registers") or {}
        crashes.append({
            "time": c.get("time"),
            "line_no": c.get("line_no"),
            "task": c.get("task_name") or c.get("task_id"),
            "task_id": c.get("task_id"),
            "process": c.get("process"),
            "lib_assert": c.get("lib_assert"),
            "nuttx_assert": c.get("nuttx_assert"),
            "version": c.get("version"),
            "registers": {k: regs.get(k) for k in ("PC", "LR", "SP", "xPSR", "EXC_RETURN") if k in regs},
            "stacks": c.get("stacks"),
            "backtraces": c.get("backtraces"),
            "reboot": c.get("reboot"),
            "findings": c.get("findings"),
            "before_task": c.get("before_task"),
            "before_all": c.get("before_all"),
        })
    return json.dumps({
        "status": "OK",
        "kpi": data.get("kpi", {}),
        "crashes": crashes,
        "analysis_rule": (
            "lib_assert 는 라이브러리(D-Bus 등)가 먼저 찍은 원래 실패 조건이고, nuttx_assert 는 그걸 abort 로 넘긴 자리다. "
            "원인을 말할 때는 lib_assert 의 조건·파일·함수를 앞세워라. "
            "backtrace 와 PC/LR 은 주소뿐이다 — 같은 빌드 ELF 없이 함수 이름을 지어내지 마라. "
            "before_task 는 죽은 태스크의 직전 로그, before_all 은 같은 시간대 전체 태스크 로그(크래시 5초 전부터)다. "
            "스택 used 는 (base + size) - sp 로 계산한 값이다. findings 는 텍스트로만 판단한 것이니 그대로 인용해도 된다."
        ),
    }, ensure_ascii=False)
