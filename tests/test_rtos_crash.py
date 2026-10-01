import json

from agent_toolkit import get_rtos_crash_analytics
from core.charts import build_rtos_crash
from parsers.rtos import RtosCrashParser, parse_line


def _t(ms):
    return f"[31/12/99 07:02:{ms:09.6f}]"


# lastword 의 ims_service assert 덤프 (일부 줄만, 값은 원본 그대로)
LINES = [
    "CHIP=best1700",
    f"{_t(21.8201)} [237] [ap] [I][IMS6.0] OemIpcHandler: onNuttxRawReqComplete, result->msg_id: 7",
    f"{_t(25.5201)} [237] [ap] on_modem_property_change - from 2 to 2",
    f"{_t(26.1000)} [151] [ap] [RIL_CPP] other task line",
    f"{_t(26.6362)} [237] [ap] assertion failed \"old_refcount >= 1\" file \"/external/dbus/dbus/dbus/dbus-message.c\""
    " line 1733 function dbus_message_unref",
    f"{_t(26.6390)} [237] [ap] dump_assert_info: Current Version: NuttX BES NuttX EVB 12.6.0  Sep 30 2026 19:04:46 arm",
    f"{_t(26.6404)} [237] [ap] dump_assert_info: Assertion failed : at file: /external/dbus/dbus/dbus/dbus-sysdeps.c:100"
    " task: ims_service process: ims_service 0x1107ac19",
    f"{_t(26.6423)} [237] [ap] dump_assert_info: Module load info (bininfo=0x1928d2b0):",
    f"{_t(26.6434)} [237] [ap] dump_assert_info:   entrypt:   0x1107ae9d",
    f"{_t(26.6450)} [237] [ap] up_dump_register: R0: 182bf8e0 R1: 00000064 R2: 20103f84  R3: 00000007",
    f"{_t(26.6460)} [237] [ap] up_dump_register: IP: 00000001 SP: 193108c8 LR: 10669f3b  PC: 10669f3b",
    f"{_t(26.6470)} [237] [ap] dump_stackinfo: User Stack:",
    f"{_t(26.6480)} [237] [ap] dump_stackinfo:   base: 0x19300d58",
    f"{_t(26.6490)} [237] [ap] dump_stackinfo:   size: 00065424",
    f"{_t(26.6500)} [237] [ap] dump_stackinfo:     sp: 0x193108c8",
    f"{_t(26.6564)} [237] [ap] stack_dump: 0x193108a8: 20103f10 10669d3b 192d9610 201031e0 19300d58 193108e0 193108c8 1066a0b7",
    f"{_t(26.7079)} [237] [ap] sched_dumpstack: backtrace|237: 0x1064a440 0x1066f480 0x1065b5b2",
    f"{_t(26.7096)} [237] [ap] sched_dumpstack: backtrace|237: 0x1092b48c 0x10ac728e",
    "[00/01/00 00:00:00.104675] [ 0] [ap] hal_trace_init_program_regions: g_program_regions 0: 0x120010 ~ 0x1a0000",
    "[31/12/99 07:02:32.019500] [ 3] [ap] board_late_initialize: 408, START",
]


def test_padded_task_id_is_an_rtos_line():
    assert parse_line(LINES[-2]).task == 0
    assert parse_line(LINES[-1]).task == 3


def test_assert_dump_is_grouped_into_one_crash():
    r = RtosCrashParser().analyze(LINES)
    assert r["kpi"]["crash_count"] == 1 and r["kpi"]["reboot_count"] == 1
    c = r["crashes"][0]
    assert (c["task_id"], c["task_name"], c["process"]) == (237, "ims_service", "ims_service")
    assert c["lib_assert"]["expr"] == "old_refcount >= 1"
    assert c["lib_assert"]["function"] == "dbus_message_unref"
    assert c["nuttx_assert"]["file"].endswith("dbus-sysdeps.c") and c["nuttx_assert"]["line"] == 100
    assert c["version"].startswith("NuttX BES NuttX EVB 12.6.0")
    assert c["module"] == {"bininfo": "0x1928d2b0", "entrypt": "0x1107ae9d"}
    assert c["registers"]["PC"] == "10669f3b" and c["registers"]["SP"] == "193108c8"
    # (0x19300d58 + 65424) - 0x193108c8
    assert c["stacks"][0]["used"] == 1056 and c["stacks"][0]["overflow"] is False
    assert c["backtraces"] == [{"task_id": 237, "addresses": [
        "0x1064a440", "0x1066f480", "0x1065b5b2", "0x1092b48c", "0x10ac728e"]}]
    assert len(c["stack_dump"]) == 1
    assert c["reboot"]["line_no"] == 19
    # 직전 로그는 5초 창 안의 같은 태스크 줄, 덤프 줄은 빠진다
    assert len(c["before_task"]) == 2 and "on_modem_property_change" in c["before_task"][-1]
    assert any("[151]" in l for l in c["before_all"])
    assert any("dbus-message.c:1733" in f for f in c["findings"])
    assert any("unref" in f for f in c["findings"])


def test_no_crash():
    r = RtosCrashParser().analyze(LINES[:4])
    assert r["kpi"]["crash_count"] == 0
    assert build_rtos_crash(r).status == "no_rtos_crash"
    assert build_rtos_crash({}).status == "no_data"


def test_chart_and_tool_read_artifact(tmp_path):
    parser = RtosCrashParser()
    parser.save_ui_report(str(tmp_path), "lw", parser.analyze(LINES))
    data = json.loads((tmp_path / "lw_rtos_crash.json").read_text(encoding="utf-8"))
    assert build_rtos_crash(data).status == "ok"

    fact = json.loads(get_rtos_crash_analytics("lw", str(tmp_path)))
    assert fact["status"] == "OK"
    assert fact["crashes"][0]["task"] == "ims_service"
    assert fact["crashes"][0]["registers"]["PC"] == "10669f3b"
    assert json.loads(get_rtos_crash_analytics("none", str(tmp_path)))["status"] == "NO_DATA"


def test_pipeline_writes_artifact_and_rag_document(tmp_path, monkeypatch):
    from log_orchestrator import LogOrchestrator
    from rag_builders.builder import build_all_payloads

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "lastword"
    log.write_text("\r\n".join(LINES) + "\r\n", encoding="utf-8")
    report_path = tmp_path / "lastword_report.json"
    assert LogOrchestrator(str(log)).run_batch(str(report_path)) is True

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["rtos_crash"]["kpi"]["crash_count"] == 1
    assert (tmp_path / "result" / "lastword_rtos_crash.json").exists()

    payloads = build_all_payloads(report, "lastword_report.json", None, None)
    doc = next(p for p in payloads if p["metadata"]["log_type"] == "RTOS_Crash")
    assert "old_refcount >= 1" in doc["document"]
    assert "ims_service" in doc["document"]
