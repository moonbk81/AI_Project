"""adb 로 붙은 RTOS 워치를 실시간으로 본다.

두 줄기로 모은다.
- 폴링: 몇 초마다 `ps`, `free`, `uptime`. 셋 다 바로 끝나는 읽기 명령이다.
  `top` 은 끝나지 않고, 남은 셸에 신호를 보냈다가 워치가 크래시한 적이 있다 (2026-09-29).
  그래서 워치에 보내는 명령은 SAFE_COMMANDS 밖으로 나가지 않는다.
- 로그: `adb logcat` 을 계속 읽는다. 링 버퍼를 먼저 다시 보내고 새 줄을 이어 보낸다(읽어도 지워지지 않는다).
  받는 대로 파일에 적는다 — 워치가 죽어도 죽기 직전까지의 로그가 PC 에 남는다.
  줄 형식이 로그 파일과 같아서 OEM_HOOK_RAW·콜 흐름 파서를 그대로 돌린다.

백엔드는 여러 사람이 쓰지만 단말은 이 PC 에 꽂힌 것뿐이라 모니터는 하나다.
"""

from __future__ import annotations

import collections
import hashlib
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable, Deque, Dict, List, Optional

from parsers.rtos import RtosCallFlowParser, RtosOemHookParser
from parsers.rtos.device_status import cpu_total, parse_free, parse_ps, parse_uptime

SAFE_COMMANDS = ("ps", "free", "uptime")
COMMAND_TIMEOUT_SEC = 10
MIN_INTERVAL_SEC = 3
DEFAULT_INTERVAL_SEC = 5
MAX_SAMPLES = 720           # 5초 간격이면 1시간
MAX_LOG_LINES = 50000       # 화면·분석에 쓰는 최근 줄. 파일에는 전부 남는다
MAX_EVENTS = 200
DEDUP_WINDOW = 20000        # 재연결하면 logcat 이 링 버퍼를 다시 보낸다 — 최근 줄 해시로 거른다
LOG_RETRY_SEC = 3
BUSY_THRESHOLD = 90.0
TOP_TASKS = 15
CAPTURE_DIR = os.path.join("./temp_logs", "live")
# 모니터 자신이 부른 ps 가 워치에서 `sh -c ps` 로 잠깐 CPU 를 쓴다. 단말 부하와 섞지 않는다
MONITOR_COMMAND_RE = re.compile(r'^sh -c (?:%s)$' % "|".join(SAFE_COMMANDS))

Runner = Callable[[List[str], int], subprocess.CompletedProcess]


def _run(args: List[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout)


def list_devices(adb: str = "adb", runner: Runner = _run) -> List[Dict[str, str]]:
    try:
        out = runner([adb, "devices", "-l"], COMMAND_TIMEOUT_SEC).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    devices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            info = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
            devices.append({"serial": parts[0], "state": parts[1], **info})
    return devices


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class LiveDeviceMonitor:
    def __init__(self, adb: str = "adb", runner: Runner = _run, popen=subprocess.Popen,
                 capture_dir: str = CAPTURE_DIR, analyze_every_sec: float = 5.0):
        self.adb = adb
        self.runner = runner
        self.popen = popen
        self.capture_dir = capture_dir
        self.analyze_every_sec = analyze_every_sec
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._proc = None
        self._reset(None, DEFAULT_INTERVAL_SEC)

    def _reset(self, serial, interval):
        self.serial = serial
        self.interval = interval
        self.started_at: Optional[str] = None
        self.connected = False
        self.samples: Deque[Dict] = collections.deque(maxlen=MAX_SAMPLES)
        self.latest: Optional[Dict] = None
        self.events: Deque[Dict] = collections.deque(maxlen=MAX_EVENTS)
        self.lines: Deque[str] = collections.deque(maxlen=MAX_LOG_LINES)
        self.line_count = 0
        self._seen: Deque[str] = collections.deque(maxlen=DEDUP_WINDOW)
        self._seen_set: set = set()
        self.capture_path: Optional[str] = None
        self.log_streaming = False
        self.analysis: Dict = {}
        self._analyzed_count = -1
        self._last_uptime: Optional[int] = None

    # ---- 제어 ----

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def start(self, serial: str, interval: int = DEFAULT_INTERVAL_SEC) -> None:
        if self.running:
            self.stop()
        with self._lock:
            self._reset(serial, max(MIN_INTERVAL_SEC, int(interval or DEFAULT_INTERVAL_SEC)))
            self.started_at = _now()
            os.makedirs(self.capture_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_serial = re.sub(r"[^A-Za-z0-9_.-]", "_", serial)
            self.capture_path = os.path.join(self.capture_dir, f"live_{safe_serial}_{stamp}.txt")
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._poll_loop, name="live-poll", daemon=True),
            threading.Thread(target=self._log_loop, name="live-log", daemon=True),
        ]
        for t in self._threads:
            t.start()
        self._event("info", f"모니터 시작 ({serial}, {self.interval}초 간격)")

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            # 호스트 쪽 adb 프로세스만 끝낸다. 워치에는 아무것도 보내지 않는다
            proc.terminate()
        for t in self._threads:
            t.join(timeout=COMMAND_TIMEOUT_SEC + 2)
        self._threads = []
        self._event("info", "모니터 중지")

    # ---- 폴링 ----

    def _shell(self, command: str) -> Optional[str]:
        if command not in SAFE_COMMANDS:
            raise ValueError(f"워치에 보낼 수 없는 명령: {command}")
        try:
            result = self.runner([self.adb, "-s", self.serial, "shell", command], COMMAND_TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 or "error:" in (result.stderr or ""):
            return None
        return result.stdout

    def poll_once(self) -> Optional[Dict]:
        ps_out = self._shell("ps")
        if ps_out is None:
            self._set_connected(False)
            return None
        self._set_connected(True)
        free_out = self._shell("free") or ""
        up_out = self._shell("uptime") or ""
        sample = self._make_sample(parse_ps(ps_out), parse_free(free_out), parse_uptime(up_out))
        with self._lock:
            self.samples.append(sample)
            self.latest = sample
        self._check_alerts(sample)
        return sample

    def _make_sample(self, tasks, heaps, uptime) -> Dict:
        overhead = [t for t in tasks if MONITOR_COMMAND_RE.match(t["command"])]
        device_tasks = [t for t in tasks if t not in overhead]
        total = cpu_total(tasks)
        overhead_cpu = round(sum(t["cpu"] for t in overhead), 1)
        return {
            "time": _now(),
            "total": total,
            # 100 - Idle. 태스크별 CPU% 는 그 태스크가 산 동안의 값이라, 방금 뜬 `sh -c ps` 몫을
            # 이 값에서 빼면 틀린다 (2.6% 인데 셸이 10.7% 로 찍힌 적이 있다). 따로 보여주기만 한다
            "device_total": total,
            "monitor_overhead": overhead_cpu,
            "task_count": len(device_tasks),
            "tasks": sorted((t for t in device_tasks if t["pid"] != 0), key=lambda t: -t["cpu"]),
            "stack_warn": sorted((t for t in device_tasks if t["stack_warn"]), key=lambda t: -t["stack_filled"]),
            "heaps": heaps,
            "uptime": uptime,
        }

    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            self._event("info" if connected else "critical", "단말 연결됨" if connected else "단말 연결 끊김")

    def _check_alerts(self, sample: Dict) -> None:
        up = sample["uptime"].get("uptime_min")
        if up is not None and self._last_uptime is not None and up < self._last_uptime:
            self._event("critical", f"재부팅 감지 (uptime {self._last_uptime}분 → {up}분)")
        if up is not None:
            self._last_uptime = up
        prev = self.samples[-2] if len(self.samples) >= 2 else None
        total = sample["device_total"]
        if total is not None and total >= BUSY_THRESHOLD and not (prev and (prev["device_total"] or 0) >= BUSY_THRESHOLD):
            top = ", ".join(f"{t['name']}({t['pid']}) {t['cpu']}%" for t in sample["tasks"][:3])
            self._event("warning", f"CPU {total}% — {top}")
        prev_warn = {t["pid"] for t in (prev or {}).get("stack_warn", [])}
        for t in sample["stack_warn"]:
            if t["pid"] not in prev_warn:
                self._event("warning", f"스택 {t['stack_filled']}% — {t['name']}({t['pid']}) {t['stack_used']}/{t['stack']}B")

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._analyze_if_new()
            self._stop.wait(self.interval)

    # ---- 로그 ----

    def _log_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._proc = self.popen([self.adb, "-s", self.serial, "logcat"], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, errors="replace", bufsize=1)
            except OSError as exc:
                self._event("critical", f"adb logcat 실행 실패: {exc}")
                return
            # 단말이 빠져 있으면 logcat 은 바로 끝난다. 3초마다 다시 부르므로 줄이 실제로 온 경우만 알린다
            received = 0
            with open(self.capture_path, "a", encoding="utf-8") as capture:
                for raw in self._proc.stdout:
                    line = raw.rstrip("\r\n")
                    if not received:
                        self.log_streaming = True
                        self._event("info", "로그 스트림 연결")
                    received += 1
                    if line and self._accept(line):
                        capture.write(line + "\n")
                        capture.flush()
                    if self._stop.is_set():
                        break
            self.log_streaming = False
            if self._proc.poll() is None:
                self._proc.terminate()
            if not self._stop.is_set():
                if received:
                    self._event("warning", "로그 스트림 끊김 — 다시 붙는 중")
                self._stop.wait(LOG_RETRY_SEC)

    def _accept(self, line: str) -> bool:
        key = hashlib.md5(line.encode("utf-8", "replace")).hexdigest()
        with self._lock:
            if key in self._seen_set:
                return False
            if len(self._seen) == self._seen.maxlen:
                self._seen_set.discard(self._seen[0])
            self._seen.append(key)
            self._seen_set.add(key)
            self.lines.append(line)
            self.line_count += 1
        return True

    def _analyze_if_new(self) -> None:
        with self._lock:
            if self.line_count == self._analyzed_count:
                return
            lines = list(self.lines)
            self._analyzed_count = self.line_count
        oem = RtosOemHookParser().analyze(lines)
        calls = RtosCallFlowParser().analyze(lines)
        with self._lock:
            self.analysis = {
                "oem_kpi": oem["kpi"],
                "oem_recent": [
                    {k: r.get(k) for k in ("token", "req_time", "func_id", "func_name", "path", "status", "verdict")}
                    | {"ipc": f"{r['tx']['main']}/{r['tx']['sub']}/{r['tx']['type']}" if r.get("tx") else None}
                    for r in oem["requests"][-15:]
                ],
                "call_kpi": calls["kpi"],
                "call_recent": [
                    {k: c.get(k) for k in ("direction", "start_time", "status", "connected")}
                    | {"broken": (c.get("broken_at") or {}).get("label")}
                    for c in calls["calls"][-10:]
                ],
            }

    # ---- 조회 ----

    def _event(self, level: str, text: str) -> None:
        with self._lock:
            self.events.append({"time": _now(), "level": level, "text": text})

    def status(self, samples: int = 120, tail: int = 80, grep: str = "") -> Dict:
        with self._lock:
            lines = list(self.lines)
            recent = list(self.samples)[-samples:]
            if grep:
                needle = grep.lower()
                lines = [l for l in lines if needle in l.lower()]
            return {
                "running": self.running,
                "serial": self.serial,
                "interval": self.interval,
                "started_at": self.started_at,
                "connected": self.connected,
                "log_streaming": self.log_streaming,
                "line_count": self.line_count,
                "capture_path": self.capture_path,
                "latest": dict(self.latest, tasks=self.latest["tasks"][:TOP_TASKS]) if self.latest else None,
                "series": [
                    {"time": s["time"], "total": s["device_total"],
                     "top": {str(t["pid"]): t["cpu"] for t in s["tasks"][:8]},
                     "names": {str(t["pid"]): t["name"] for t in s["tasks"][:8]},
                     "heap_used_pct": s["heaps"][0]["used_pct"] if s["heaps"] else None}
                    for s in recent
                ],
                "events": list(self.events)[-50:][::-1],
                "log_tail": lines[-tail:],
                "analysis": dict(self.analysis),
            }


monitor = LiveDeviceMonitor()
