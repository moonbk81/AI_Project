"""RTOS 로그 분석 결과를 RAG 문서로 만든다.

키-값 덤프 대신 한글 서술문으로 쓴다. 질문은 "착신이 왜 안 떠?" 처럼 오는데
`broken_at_stage: ui_incoming` 같은 본문으로는 임베딩이 가까워지지 않는다.
요약 문서는 콜이 없어도 늘 하나 만든다 — 적재 문서가 0개면 파이프라인이 실패한다.
"""

from rag_builders.common import append_payload, source_file_name


def _call_document(call):
    direction = "발신(MO)" if call.get("direction") == "MO" else "착신(MT)"
    lines = [
        f"[RTOS 콜 흐름] {direction} 콜 sid={call.get('sid')} idx={call.get('idx')}",
        f"- 시작 {call.get('start_time')} / 종료 {call.get('end_time') or '로그 안에서 종료 확인 안 됨'}",
        f"- 결과: {call.get('status')}",
    ]
    states = " → ".join(f"{s['state']}({s['time']})" for s in call.get("states", []))
    lines.append(f"- 호 상태 전이: {states}")
    if call.get("fail_cause"):
        lines.append(f"- 종료 원인(LAST_CALL_FAIL_CAUSE): {call['fail_cause']} {call.get('fail_reason')}")

    reached = []
    for stage in call.get("chain", []):
        mark = f"✓ {stage['time']}" if stage.get("reached") else "✗ 로그 없음"
        reached.append(f"  - {stage['label']}: {mark}")
    lines.append("- 계층별 진행:")
    lines.extend(reached)

    sip = call.get("sip_messages") or []
    if sip:
        flow = " → ".join(
            f"{'UE→망' if m.get('is_outgoing') else '망→UE'} {m['method_code']}({m['time'].split(' ')[-1]})"
            for m in sip
        )
        lines.append(f"- IMS SIP 흐름 (Call-ID {call.get('sip_call_id')}): {flow}")
        if call.get("sip_final_response"):
            lines.append(f"- INVITE 최종 응답: {call['sip_final_response']}")
        if call.get("sip_error"):
            lines.append(f"- SIP 오류 응답: {call['sip_error']}")
        reasons = [m["key_headers"]["Reason"] for m in sip if (m.get("key_headers") or {}).get("Reason")]
        if reasons:
            lines.append(f"- SIP Reason 헤더: {'; '.join(reasons)}")
    elif call.get("direction") == "MO":
        lines.append("- IMS SIP: 이 콜의 INVITE 가 로그에 없다 (단말이 망으로 INVITE 를 보내지 않았다).")

    broken = call.get("broken_at")
    if broken:
        lines.append(f"- 끊긴 지점: {broken['label']} 단계의 로그가 없다. 바로 앞 단계까지는 진행됐다.")
        last = _last_reached_evidence(call)
        if last:
            lines.append(f"- 마지막으로 확인된 줄 (line {last['line_no']}): {last['text']}")

    slow = [r for r in call.get("ril_requests", []) if r.get("latency_ms") is not None]
    if slow:
        req_text = ", ".join(f"{r['name']}[{r['token']}] {r['latency_ms']}ms" for r in slow[:8])
        lines.append(f"- RIL 요청 응답 시간: {req_text}")
    pending = [r for r in call.get("ril_requests", []) if r.get("resp_time") is None]
    if pending:
        lines.append("- 응답 없는 RIL 요청: " + ", ".join(f"{r['name']}({r['req_time']})" for r in pending))
    return "\n".join(lines)


def _last_reached_evidence(call):
    checkpoints = call.get("checkpoints", {})
    reached = [c for c in call.get("chain", []) if c.get("reached")]
    if not reached:
        return None
    return checkpoints.get(reached[-1]["stage"])


def _summary_document(report_data):
    build = report_data.get("rtos_build_info") or {}
    kpi = (report_data.get("rtos_call_flow") or {}).get("kpi", {})
    lines = ["[RTOS 로그 요약]"]
    if build:
        keys = ("SEC_VERSION", "BUILD_DATE", "CHIP", "KERNEL", "SEC_BUILD_ID")
        lines.append("- 빌드: " + ", ".join(f"{k}={build[k]}" for k in keys if k in build))
    lines.append(
        f"- 콜 {kpi.get('call_count', 0)}건 (발신 {kpi.get('mo_count', 0)}, 착신 {kpi.get('mt_count', 0)}), "
        f"연결 성공 {kpi.get('connected_count', 0)}건, 중간에 끊긴 콜 {kpi.get('broken_count', 0)}건"
    )
    lines.append(f"- 응답 없는 RIL 요청 {kpi.get('unanswered_request_count', 0)}건")
    return "\n".join(lines)


def build_rtos_payloads(report_data, input_file):
    rag_payload = []
    source_file = source_file_name(input_file)
    flow = report_data.get("rtos_call_flow") or {}

    append_payload(rag_payload, _summary_document(report_data), {
        "log_type": "RTOS_Log_Summary",
        "source_file": source_file,
        "time": "로그 전체",
        **{k: v for k, v in (flow.get("kpi") or {}).items()},
    })

    for call in flow.get("calls", []) or []:
        broken = call.get("broken_at") or {}
        meta = {
            "log_type": "RTOS_Call_Flow",
            "source_file": source_file,
            "time": call.get("start_time"),
            "direction": call.get("direction"),
            "status": call.get("status"),
            "sid": call.get("sid"),
        }
        if broken:
            meta["broken_stage"] = broken.get("label")
        if call.get("sip_final_response"):
            meta["sip_final_response"] = call["sip_final_response"]
        if call.get("sip_error"):
            meta["sip_error"] = call["sip_error"]
        if call.get("fail_cause"):
            meta["fail_cause"] = call["fail_cause"]
            meta["fail_reason"] = call.get("fail_reason")
        append_payload(rag_payload, _call_document(call), meta)

    unanswered = flow.get("unanswered_requests") or []
    if unanswered:
        text = "[RTOS 응답 없는 RIL 요청] ofono 가 보낸 요청 중 로그가 끝날 때까지 응답(<)이 없는 것:\n" + "\n".join(
            f"- {r['name']} token={r['token']} 요청 {r['req_time']} (line {r['req_line']})" for r in unanswered
        )
        append_payload(rag_payload, text, {
            "log_type": "RTOS_RIL_Pending",
            "source_file": source_file,
            "time": unanswered[0]["req_time"],
            "count": len(unanswered),
        })

    oem = report_data.get("rtos_oem_hook") or {}
    if oem.get("requests"):
        append_payload(rag_payload, _oem_hook_document(oem), {
            "log_type": "RTOS_OemHook_Flow",
            "source_file": source_file,
            "time": oem["requests"][0].get("req_time"),
            **{k: v for k, v in (oem.get("kpi") or {}).items()},
        })

    cpu = report_data.get("rtos_cpu") or {}
    if cpu.get("samples"):
        append_payload(rag_payload, _cpu_document(cpu), {
            "log_type": "RTOS_CPU_Usage",
            "source_file": source_file,
            "time": cpu["samples"][0].get("time"),
            **{k: v for k, v in (cpu.get("kpi") or {}).items() if v is not None},
        })

    for crash in (report_data.get("rtos_crash") or {}).get("crashes") or []:
        append_payload(rag_payload, _crash_document(crash), {
            "log_type": "RTOS_Crash",
            "source_file": source_file,
            "time": crash.get("time"),
            "task": crash.get("task_name") or crash.get("task_id"),
            "line_no": crash.get("line_no"),
        })

    return rag_payload


def _crash_document(crash):
    task = crash.get("task_name") or crash.get("task_id")
    lines = [f"[RTOS 크래시] {crash.get('time')} 태스크 {task}({crash.get('task_id')}) assert 로 죽음 (line {crash.get('line_no')})"]
    lib, nuttx = crash.get("lib_assert"), crash.get("nuttx_assert")
    if lib:
        lines.append(f"- 실패한 조건: `{lib['expr']}` — {lib['file']}:{lib['line']} {lib.get('function') or ''}")
    if nuttx:
        lines.append(f"- NuttX assert 위치: {nuttx['file']}:{nuttx['line']} (process {crash.get('process')})")
    regs = crash.get("registers") or {}
    if regs:
        lines.append("- 레지스터: " + ", ".join(f"{k}={regs[k]}" for k in ("PC", "LR", "SP") if k in regs))
    for s in crash.get("stacks") or []:
        if s.get("used") is not None:
            lines.append(f"- {s['kind']} 스택 {s['used']} / {int(s['size'])} 바이트 사용 ({s['used_pct']}%)")
    for bt in crash.get("backtraces") or []:
        lines.append(f"- backtrace({bt['task_id']}): " + " ".join(bt["addresses"]))
    for finding in crash.get("findings") or []:
        lines.append(f"- 판단: {finding}")
    if crash.get("before_task"):
        lines.append("- 죽기 직전 같은 태스크 로그:")
        lines.extend(f"  {l}" for l in crash["before_task"])
    return "\n".join(lines)


def _cpu_document(cpu):
    kpi = cpu.get("kpi") or {}
    lines = [
        "[RTOS CPU 점유율] 태스크별 CPU 점유율 스냅샷 (전체 = 100 - Idle_Task)",
        f"- 스냅샷 {kpi.get('sample_count', 0)}개 ({kpi.get('first_time')} ~ {kpi.get('last_time')}), "
        f"전체 평균 {kpi.get('avg_total')}%, 최고 {kpi.get('max_total')}% ({kpi.get('max_total_time')}), "
        f"최저 Idle {kpi.get('min_idle')}%",
        f"- {kpi.get('busy_threshold')}% 이상 과부하 스냅샷 {kpi.get('busy_sample_count', 0)}개, "
        f"과부하 구간 {kpi.get('busy_window_count', 0)}개",
    ]
    tasks = (cpu.get("tasks") or [])[:8]
    if tasks:
        lines.append("- 많이 쓰는 태스크 (평균 / 최대): " + ", ".join(
            f"{t['name']}(PID {t['pid']}{', ' + t['role'] if t.get('role') else ''}) {t['avg']}% / {t['max']}%"
            for t in tasks))
    for w in cpu.get("busy_windows") or []:
        top = ", ".join(f"{t['name']}({t['pid']}) {t['avg']}%" for t in w.get("top_tasks") or [])
        lines.append(f"- 과부하 {w['start_time']} ~ {w['end_time']} 최고 {w['peak_total']}%: {top}")
    return "\n".join(lines)


def _oem_hook_document(oem):
    kpi = oem.get("kpi") or {}
    lines = [
        "[RTOS OEM_HOOK_RAW 전달] ofono → rild → secril → cpif → 모뎀 → 응답",
        f"- 요청 {kpi.get('request_count', 0)}건: cpif 로 IPC 를 쓰는 raw IPC {kpi.get('raw_ipc_count', 0)}건, "
        f"secril 내부 처리 {kpi.get('local_count', 0)}건",
        f"- 정상 {kpi.get('ok_count', 0)}건, 문제 {kpi.get('problem_count', 0)}건, "
        f"로그가 먼저 끝남 {kpi.get('pending_count', 0)}건, 모뎀 오류 응답 {kpi.get('modem_error_count', 0)}건",
    ]
    for req in oem.get("requests") or []:
        if req.get("verdict") == "ok":
            continue
        tx = req.get("tx") or {}
        ipc = f" IPC {tx.get('main')}/{tx.get('sub')}/{tx.get('type')} seq={tx.get('seq')}" if tx else ""
        lines.append(
            f"- token {req.get('token')} ({req.get('req_time')}) funcId {req.get('func_id')} {req.get('func_name')}{ipc}: "
            f"{req.get('status')}"
        )
        evidence = req.get("ofono_error_evidence")
        if evidence:
            lines.append(f"  - 근거 (line {evidence['line_no']}): {evidence['text']}")
    return "\n".join(lines)
