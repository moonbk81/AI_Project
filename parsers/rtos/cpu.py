"""RTOS 태스크별 CPU 점유율 스냅샷을 모아 전체 부하와 많이 잡아먹는 태스크를 찾는다.

한 스냅샷은 같은 태스크가 연달아 찍는 줄들이다 (사용자가 준 형식):
  `[31/12/99 07:49:31.049900] [480] [ap] PID 0 PRI 0 NAME Idle_Task CPU: 0.9%`
  `[31/12/99 07:49:31.051400] [480] [ap] PID 1 PRI 224 NAME hpwork CPU: 2.5%`
- 0.1% 이상인 태스크만 찍히고, 찍힌 값과 Idle_Task 를 더하면 약 100% 다 → 전체 점유율 = 100 - Idle.
- 스냅샷은 PID 0(Idle_Task) 줄에서 시작한다.
- 같은 스냅샷이 수십 ms 간격으로 두 번 찍힌다 (값이 거의 같다). 두 번째는 버리고 개수만 센다.
- 이름은 겹친다 (ims_service, aero 여러 개) → 태스크는 PID 로 구분한다.
"""

import json
import os
import re

from parsers.base import BaseParser
from parsers.rtos.line import parse_lines

CPU_LINE_RE = re.compile(r'^PID (\d+) PRI (\d+) NAME (.+?) CPU: ([\d.]+)%')

IDLE_PID = 0
# 이 간격 안에 다시 시작한 스냅샷은 같은 측정을 한 번 더 찍은 것이다
DUPLICATE_WINDOW_SEC = 0.5
# 스냅샷 안의 줄 간격은 수 ms 다. 이보다 벌어지면 다른 스냅샷으로 본다
SNAPSHOT_GAP_SEC = 0.5
BUSY_THRESHOLD = 90.0
# 스냅샷이 이보다 벌어지면 그 사이는 모르는 시간이다 — 과부하 구간을 이어 붙이지 않는다
WINDOW_MAX_GAP_SEC = 5.0
TOP_SERIES = 3
TOP_PER_SAMPLE = 5
MAX_SAMPLES = 3000

# 로그의 태스크 번호로 추정한 역할 (OEM_HOOK_RAW 추적에서 본 태스크: 345 가 IPC TX, 373 이 RX, 346 이 GetMessage)
TASK_ROLES = {
    "rild": "rild (libril)",
    "ofonod": "ofono",
    "ESAR": "secril 이벤트 처리·IPC 송신 (추정)",
    "RDAR": "secril 요청 분배 (추정)",
    "ICR": "cpif IPC 수신 (추정)",
    "ims_service": "IMS",
    "dbusdaemon": "D-Bus",
    "netdev-wwan2": "모뎀 데이터 netdev",
    "sdio_txrx": "SDIO 송수신",
    "at_distributor": "AT distributor",
    "hpwork": "커널 high-priority work queue",
    "lpwork": "커널 low-priority work queue",
    "sec_bat_wq": "배터리 work queue",
    "adbd": "adbd",
}


def _new_sample(rec):
    return {"time": rec.time, "date": rec.date, "sec": rec.sec, "line_no": rec.line_no,
            "task_id": rec.task, "last_sec": rec.sec, "idle": None, "tasks": {}}


class RtosCpuUsageParser(BaseParser):
    def analyze(self, lines):
        samples = self._collect(parse_lines(lines))
        kept, duplicates = [], 0
        for sample in samples:
            if kept and sample["sec"] - kept[-1]["sec"] < DUPLICATE_WINDOW_SEC:
                duplicates += 1
                continue
            kept.append(sample)
        samples = [s for s in kept if s["idle"] is not None]
        if not samples:
            return {"kpi": {"sample_count": 0}, "samples": [], "tasks": [], "busy_windows": [], "top_series": []}

        for s in samples:
            s["total"] = round(max(0.0, 100.0 - s["idle"]), 1)

        tasks = self._task_summary(samples)
        top_pids = [t["pid"] for t in tasks[:TOP_SERIES]]
        busiest = max(samples, key=lambda s: s["total"])
        totals = [s["total"] for s in samples]
        busy_windows = self._busy_windows(samples)
        return {
            "kpi": {
                "sample_count": len(samples),
                "duplicate_sample_count": duplicates,
                "avg_total": round(sum(totals) / len(totals), 1),
                "max_total": busiest["total"],
                "max_total_time": busiest["time"],
                "min_idle": round(min(s["idle"] for s in samples), 1),
                "busy_threshold": BUSY_THRESHOLD,
                "busy_sample_count": sum(1 for t in totals if t >= BUSY_THRESHOLD),
                "busy_window_count": len(busy_windows),
                "first_time": samples[0]["time"],
                "last_time": samples[-1]["time"],
                "top_task": tasks[0]["name"] if tasks else None,
                "top_task_pid": tasks[0]["pid"] if tasks else None,
                "top_task_avg": tasks[0]["avg"] if tasks else None,
            },
            "top_series": [{"pid": t["pid"], "name": t["name"]} for t in tasks[:TOP_SERIES]],
            "samples": [self._public_sample(s, top_pids) for s in samples[:MAX_SAMPLES]],
            "tasks": tasks,
            "busy_windows": busy_windows,
        }

    def save_ui_report(self, output_dir="./result", base_name="", analysis=None):
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{base_name}_rtos_cpu.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(analysis or {}, f, indent=4, ensure_ascii=False)
        return out_path

    @staticmethod
    def _collect(records):
        samples, current = [], None
        for rec in records:
            m = CPU_LINE_RE.match(rec.msg)
            if not m:
                continue
            pid, pri, name, cpu = int(m.group(1)), int(m.group(2)), m.group(3).strip(), float(m.group(4))
            starts_new = (
                current is None or pid == IDLE_PID or rec.task != current["task_id"]
                or rec.sec - current["last_sec"] > SNAPSHOT_GAP_SEC
            )
            if starts_new:
                current = _new_sample(rec)
                samples.append(current)
            current["last_sec"] = rec.sec
            if pid == IDLE_PID:
                current["idle"] = cpu
            else:
                current["tasks"][pid] = {"name": name, "pri": pri, "cpu": cpu}
        return samples

    @staticmethod
    def _task_summary(samples):
        """평균은 안 찍힌 스냅샷을 0% 로 본다 (0.1% 미만이라 안 찍힌 것)."""
        acc = {}
        for s in samples:
            for pid, t in s["tasks"].items():
                a = acc.setdefault(pid, {"pid": pid, "name": t["name"], "pri": t["pri"],
                                         "sum": 0.0, "max": 0.0, "max_time": None, "seen": 0})
                a["sum"] += t["cpu"]
                a["seen"] += 1
                if t["cpu"] > a["max"]:
                    a["max"], a["max_time"] = t["cpu"], s["time"]
        n = len(samples)
        tasks = []
        for a in acc.values():
            tasks.append({
                "pid": a["pid"], "name": a["name"], "pri": a["pri"],
                "role": TASK_ROLES.get(a["name"]),
                "avg": round(a["sum"] / n, 2), "max": a["max"], "max_time": a["max_time"],
                "seen": a["seen"],
            })
        tasks.sort(key=lambda t: (-t["avg"], -t["max"], t["pid"]))
        return tasks

    @staticmethod
    def _busy_windows(samples):
        """연속으로 BUSY_THRESHOLD 이상인 스냅샷을 한 구간으로 묶고, 그 구간에서 많이 쓴 태스크를 적는다."""
        windows, run = [], []
        for s in samples + [None]:
            busy = s is not None and s["total"] >= BUSY_THRESHOLD
            if busy and (not run or s["sec"] - run[-1]["sec"] <= WINDOW_MAX_GAP_SEC):
                run.append(s)
                continue
            if run:
                acc = {}
                for r in run:
                    for pid, t in r["tasks"].items():
                        a = acc.setdefault(pid, {"pid": pid, "name": t["name"], "sum": 0.0})
                        a["sum"] += t["cpu"]
                top = sorted(acc.values(), key=lambda a: -a["sum"])[:TOP_PER_SAMPLE]
                windows.append({
                    "start_time": run[0]["time"], "end_time": run[-1]["time"],
                    "line_no": run[0]["line_no"], "sample_count": len(run),
                    "peak_total": max(r["total"] for r in run),
                    "top_tasks": [{"pid": a["pid"], "name": a["name"], "avg": round(a["sum"] / len(run), 1)}
                                  for a in top],
                })
                run = [s] if busy else []
        return windows

    @staticmethod
    def _public_sample(s, top_pids):
        top = sorted(s["tasks"].items(), key=lambda kv: -kv[1]["cpu"])[:TOP_PER_SAMPLE]
        return {
            "time": s["time"], "date": s["date"], "line_no": s["line_no"],
            "total": s["total"], "idle": s["idle"],
            "series": {str(pid): s["tasks"].get(pid, {}).get("cpu", 0.0) for pid in top_pids},
            "top": [{"pid": pid, "name": t["name"], "cpu": t["cpu"]} for pid, t in top],
        }
