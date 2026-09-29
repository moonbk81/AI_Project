import json

from agent_toolkit import get_rtos_cpu_usage_analytics
from core.charts import build_rtos_cpu_usage
from parsers.rtos import RtosCpuUsageParser


def _snapshot(time_prefix, ms0, rows):
    """rows: (pid, pri, name, cpu). 줄 간격 1.5ms 로 한 스냅샷을 찍는다."""
    out = []
    for i, (pid, pri, name, cpu) in enumerate(rows):
        ms = ms0 + i * 1.5
        out.append(f"[31/12/99 {time_prefix}.{int(ms * 1000):06d}] [480] [ap] PID {pid} PRI {pri} NAME {name} CPU: {cpu}%")
    return out


# 사용자가 붙여 준 07:49:31 스냅샷에서 일부 (Idle 0.9% → 전체 99.1%)
BUSY_ROWS = [
    (0, 0, "Idle_Task", 0.9), (1, 224, "hpwork", 2.5), (164, 100, "rild", 1.1), (165, 100, "ofonod", 2.6),
    (345, 100, "ESAR", 23.5), (373, 100, "ICR", 2.2), (460, 100, "ims_service", 27.7),
    (476, 100, "ims_service", 3.8), (480, 223, "netdev-wwan2", 6.8),
]
CALM_ROWS = [
    (0, 0, "Idle_Task", 25.0), (158, 100, "dbusdaemon", 6.0), (165, 100, "ofonod", 4.7),
    (345, 100, "ESAR", 2.8), (373, 100, "ICR", 14.3), (460, 100, "ims_service", 9.7),
]

LINES = (
    _snapshot("07:49:31", 49.9, BUSY_ROWS)
    # 같은 측정이 70ms 뒤에 한 번 더 찍힌다
    + _snapshot("07:49:31", 120.0, [(pid, pri, name, cpu + 0.1) for pid, pri, name, cpu in BUSY_ROWS])
    + _snapshot("07:49:32", 577.2, BUSY_ROWS)
    + ["[31/12/99 07:49:40.000000] [151] [ap] [RIL_CPP] unrelated"]
    + _snapshot("07:50:55", 850.9, CALM_ROWS)
    + _snapshot("07:50:57", 376.1, BUSY_ROWS)
)


def test_snapshots_total_and_duplicates():
    r = RtosCpuUsageParser().analyze(LINES)
    kpi = r["kpi"]
    assert kpi["sample_count"] == 4
    assert kpi["duplicate_sample_count"] == 1
    assert [s["total"] for s in r["samples"]] == [99.1, 99.1, 75.0, 99.1]
    assert kpi["max_total"] == 99.1 and kpi["min_idle"] == 0.9
    assert kpi["busy_sample_count"] == 3


def test_tasks_are_keyed_by_pid_and_missing_counts_as_zero():
    r = RtosCpuUsageParser().analyze(LINES)
    tasks = {t["pid"]: t for t in r["tasks"]}
    assert tasks[460]["name"] == tasks[476]["name"] == "ims_service"
    assert tasks[460]["avg"] == round((27.7 * 3 + 9.7) / 4, 2)
    # dbusdaemon 은 한 스냅샷에만 찍혔다 → 나머지 셋은 0%
    assert tasks[158]["avg"] == round(6.0 / 4, 2) and tasks[158]["seen"] == 1
    assert tasks[373]["max"] == 14.3 and tasks[373]["max_time"] == "07:50:55.850900"
    assert tasks[345]["role"].startswith("secril")
    assert r["tasks"][0]["pid"] == 460
    assert [t["pid"] for t in r["top_series"]] == [460, 345, 373]


def test_busy_windows_break_at_sampling_gaps():
    r = RtosCpuUsageParser().analyze(LINES)
    windows = r["busy_windows"]
    assert [(w["start_time"], w["end_time"], w["sample_count"]) for w in windows] == [
        ("07:49:31.049900", "07:49:32.577200", 2),
        ("07:50:57.376100", "07:50:57.376100", 1),
    ]
    assert windows[0]["top_tasks"][0] == {"pid": 460, "name": "ims_service", "avg": 27.7}


def test_no_cpu_lines():
    r = RtosCpuUsageParser().analyze(["[31/12/99 07:49:40.000000] [151] [ap] [RIL_CPP] x"])
    assert r["kpi"]["sample_count"] == 0
    assert build_rtos_cpu_usage(r).status == "no_cpu_samples"
    assert build_rtos_cpu_usage({}).status == "no_data"


def test_chart_breaks_lines_at_gaps_and_tool_reads_artifact(tmp_path):
    parser = RtosCpuUsageParser()
    data = parser.analyze(LINES)
    parser.save_ui_report(str(tmp_path), "crash", data)

    chart = build_rtos_cpu_usage(json.loads((tmp_path / "crash_rtos_cpu.json").read_text(encoding="utf-8")))
    assert chart.status == "ok"
    # 31s, 32.5s | 끊김 | 55.8s, 57.3s
    assert chart.x[0] == "2000-12-31 07:49:31.049900"
    assert chart.x[2] is None and chart.total[2] is None
    assert chart.total == [99.1, 99.1, None, 75.0, 99.1]
    assert [s["name"] for s in chart.series] == ["ims_service", "ESAR", "ICR"]
    assert chart.series[0]["values"] == [27.7, 27.7, None, 9.7, 27.7]

    fact = json.loads(get_rtos_cpu_usage_analytics("crash", str(tmp_path)))
    assert fact["status"] == "OK"
    assert fact["kpi"]["busy_window_count"] == 2
    assert json.loads(get_rtos_cpu_usage_analytics("none", str(tmp_path)))["status"] == "NO_DATA"


def test_pipeline_writes_artifact_and_rag_document(tmp_path, monkeypatch):
    from log_orchestrator import LogOrchestrator
    from rag_builders.builder import build_all_payloads

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "cpu.txt"
    log.write_text("\r\n".join(LINES * 1) + "\r\n", encoding="utf-8")
    report_path = tmp_path / "cpu_report.json"
    assert LogOrchestrator(str(log)).run_batch(str(report_path)) is True

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["rtos_cpu"]["kpi"]["sample_count"] == 4
    assert (tmp_path / "result" / "cpu_rtos_cpu.json").exists()

    payloads = build_all_payloads(report, "cpu_report.json", None, None)
    doc = next(p for p in payloads if p["metadata"]["log_type"] == "RTOS_CPU_Usage")
    assert "ims_service(PID 460, IMS)" in doc["document"]
    assert "과부하 07:49:31.049900 ~ 07:49:32.577200" in doc["document"]
    for meta in (p["metadata"] for p in payloads):
        assert all(isinstance(v, (str, int, float, bool)) for v in meta.values()), meta
