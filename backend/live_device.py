"""adb 로 붙은 RTOS 워치를 실시간으로 본다.

두 줄기로 모은다.
- 폴링: 몇 초마다 `ps`, `free`, `uptime`. 셋 다 바로 끝나는 읽기 명령이다.
  `top` 은 끝나지 않고, 남은 셸에 신호를 보냈다가 워치가 크래시한 적이 있다 (2026-09-29).
  그래서 워치에 보내는 명령은 SAFE_COMMANDS 밖으로 나가지 않는다.
- 로그: `adb logcat` 을 계속 읽는다. 링 버퍼를 먼저 다시 보내고 새 줄을 이어 보낸다(읽어도 지워지지 않는다).
  받는 대로 파일에 적는다 — 워치가 죽어도 죽기 직전까지의 로그가 PC 에 남는다.
  줄 형식이 로그 파일과 같아서 OEM_HOOK_RAW·콜 흐름 파서를 그대로 돌린다.
- 서비스 시작: 가끔 ims_service 가 안 떠 있다. 화면 버튼을 눌렀을 때만 `ims_service &` 처럼 백그라운드로 띄운다
  (adbd 가 `sh -c` 로 돌려서 셸은 바로 끝나고 서비스만 남는다). 명령은 START_COMMANDS 와 똑같은 것만 보낸다.
  자동으로 띄우지는 않는다. 떠 있는지는 폴링한 ps 의 태스크 이름으로 본다.
- 재부팅: 버튼으로 `adb reboot` 을 보내면 로그를 새 파일로 다시 시작한다 (받은 줄·분석·중복 거르기도 비운다).
  RTC 가 없어 재부팅 뒤 타임스탬프가 앞 부팅과 겹친다 — 한 파일에 섞으면 시간 순서가 꼬인다.
  스스로 재부팅한 경우(크래시)는 직전 로그와 이어 봐야 해서 파일을 그대로 잇는다.

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
from parsers.rtos.call_flow import parse_ims_registration
from parsers.rtos.nitz import parse_nitz
from parsers.rtos.line import parse_line
from parsers.rtos.device_status import cpu_total, parse_free, parse_ps, parse_uptime

SAFE_COMMANDS = ("ps", "free", "uptime")
# 워치 상태를 바꾸는 명령이라 SAFE_COMMANDS 와 따로 둔다. 키는 ps 에 보이는 태스크 이름이다
START_COMMANDS = {"ims_service": "ims_service &", "telephonyd": "telephonyd &"}
SERVICE_CHECK_SEC = 8     # 띄운 뒤 ps 에 보일 때까지 기다리는 시간
REBOOT_TIMEOUT_SEC = 15
COMMAND_TIMEOUT_SEC = 10
MIN_INTERVAL_SEC = 3
DEFAULT_INTERVAL_SEC = 5
MAX_SAMPLES = 720           # 5초 간격이면 1시간
MAX_LOG_LINES = 50000       # 화면·분석에 쓰는 최근 줄. 파일에는 전부 남는다
MAX_EVENTS = 200
DEDUP_WINDOW = 20000        # 재연결하면 logcat 이 링 버퍼를 다시 보낸다 — 최근 줄 해시로 거른다
LOG_RETRY_SEC = 3
# 이만큼 단말이 안 보이면 USB 가 빠졌다고 보고 모니터를 멈춘다. 재부팅은 이보다 짧아서 다시 붙어 로그를 잇는다
DISCONNECT_STOP_SEC = 60
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
                 capture_dir: str = CAPTURE_DIR, analyze_every_sec: float = 5.0,
                 disconnect_stop_sec: float = DISCONNECT_STOP_SEC):
        self.adb = adb
        self.runner = runner
        self.popen = popen
        self.capture_dir = capture_dir
        self.analyze_every_sec = analyze_every_sec
        self.disconnect_stop_sec = disconnect_stop_sec
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._proc = None
        # 받은 줄을 실시간으로 넘겨받을 곳 (콜 테스트). 링 버퍼를 다시 보낸 줄은 넘기지 않는다
        self._listeners: List[Callable[[str], None]] = []
        self._service_lock = threading.Lock()  # 같은 서비스를 두 번 띄우지 않는다
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
        # 콜 테스트가 "도중에 워치가 죽었나" 를 보는 눈금
        self.reboot_count = 0
        self.disconnect_count = 0
        # 마지막으로 받은 imsservice RegistrationStatus. 부팅 직후에 찍혀서 콜 테스트가 시작될 때는 이미 지나가 있다
        self.ims_registration: Optional[Dict] = None
        # 마지막 NITZ, 찍힌 자리별 (telephonytool = tapi 클라이언트까지 옴, rilj = secril 이 올림)
        self.nitz: Dict[str, Dict] = {}
        self._lost_since: Optional[float] = None
        self.stop_reason: Optional[str] = None

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
            self.capture_path = self._new_capture_path()
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._poll_loop, name="live-poll", daemon=True),
            threading.Thread(target=self._log_loop, name="live-log", daemon=True),
        ]
        for t in self._threads:
            t.start()
        self._event("info", f"모니터 시작 ({serial}, {self.interval}초 간격)")

    def _new_capture_path(self) -> str:
        os.makedirs(self.capture_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_serial = re.sub(r"[^A-Za-z0-9_.-]", "_", self.serial or "")
        path = os.path.join(self.capture_dir, f"live_{safe_serial}_{stamp}.txt")
        if path == self.capture_path:  # 같은 초 안에 다시 만들면 덮어 이어 쓰지 않게 한다
            path = path.replace(".txt", f"_{int(time.time() * 1000) % 1000:03d}.txt")
        return path

    def reboot_device(self) -> Dict:
        """`adb reboot` 을 보내고 로그를 새 파일로 다시 시작한다. 모니터는 계속 돌며 다시 붙는다."""
        if not (self.running and self.connected):
            raise RuntimeError("실시간 모니터가 단말에 붙어 있어야 합니다.")
        try:
            result = self.runner([self.adb, "-s", self.serial, "reboot"], REBOOT_TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._event("critical", f"재부팅 명령 실패: {exc}")
            raise RuntimeError(f"재부팅 명령을 보내지 못했습니다: {exc}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            self._event("critical", f"재부팅 명령 실패 (exit {result.returncode}) {detail}")
            raise RuntimeError(f"재부팅 명령이 실패했습니다 (exit {result.returncode}) {detail}")
        old = self.capture_path
        with self._lock:
            self.capture_path = self._new_capture_path()
            self.lines.clear()
            self.line_count = 0
            self._seen.clear()
            self._seen_set.clear()
            self.analysis = {}
            self._analyzed_count = -1
            self.ims_registration = None
            self.nitz = {}
            self._last_uptime = None  # 요청한 재부팅이라 "재부팅 감지" 경고로 세지 않는다
        # 지금 logcat 은 앞 부팅 것이다. 호스트 adb 만 끊으면 로그 루프가 새 파일로 다시 붙는다
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
        self._event("warning", f"재부팅 요청 (adb reboot) — 로그를 새 파일로 시작: {self.capture_path}")
        return {"previous_capture_path": old, "capture_path": self.capture_path}

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

    def _stop_lost(self) -> None:
        # 폴링 스레드에서 부른다. 자기 자신을 join 할 수 없으니 멈추라고만 알리고 두 스레드가 스스로 끝난다
        self.stop_reason = f"단말이 {int(self.disconnect_stop_sec)}초 넘게 안 보여 자동 중지 (USB 분리?)"
        self._event("critical", self.stop_reason)
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

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

    # ---- 서비스 ----

    @staticmethod
    def services(tasks: Optional[List[Dict]]) -> Dict[str, Dict]:
        """START_COMMANDS 의 서비스가 ps 태스크 목록에 있는지. 목록이 없으면(ps 전) running 은 None."""
        result = {}
        for name in START_COMMANDS:
            found = [t for t in tasks or [] if t["name"] == name]
            result[name] = {"running": bool(found) if tasks is not None else None,
                            "pid": found[0]["pid"] if found else None}
        return result

    def start_service(self, name: str) -> Dict:
        """서비스가 안 떠 있으면 백그라운드로 띄우고, ps 에 보일 때까지 확인한다."""
        command = START_COMMANDS.get(name)
        if command is None:
            raise ValueError(f"시작할 수 없는 서비스: {name}")
        if not (self.running and self.connected):
            raise RuntimeError("실시간 모니터가 단말에 붙어 있어야 합니다.")
        if not self._service_lock.acquire(blocking=False):
            raise RuntimeError("다른 서비스를 시작하는 중입니다.")
        try:
            ps_out = self._shell("ps")
            if ps_out is None:
                raise RuntimeError("ps 를 받지 못했습니다 (단말 연결 확인).")
            before = self.services(parse_ps(ps_out))[name]
            if before["running"]:
                raise RuntimeError(f"{name} 는 이미 돌고 있습니다 (PID {before['pid']}).")
            self._event("warning", f"{name} 가 안 떠 있어 시작합니다: {command}")
            try:
                result = self.runner([self.adb, "-s", self.serial, "shell", command], COMMAND_TIMEOUT_SEC)
            except OSError as exc:
                self._event("critical", f"{name} 시작 명령 실패: {exc}")
                raise RuntimeError(f"{name} 시작 명령을 보내지 못했습니다: {exc}")
            except subprocess.TimeoutExpired:
                # 백그라운드로 뜬 서비스가 셸 출력을 쥐고 있으면 adb shell 이 안 끝난다.
                # 끊기는 건 호스트 adb 뿐이고 워치의 서비스는 남는다 → ps 로 확인한다
                result = None
                self._event("warning", f"{name} 시작 명령이 {COMMAND_TIMEOUT_SEC}초 안에 안 끝나 ps 로 확인합니다")
            if result is not None and result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                self._event("critical", f"{name} 시작 명령 실패 (exit {result.returncode}) {detail}")
                raise RuntimeError(f"{name} 시작 명령이 실패했습니다 (exit {result.returncode}) {detail}")
            deadline = time.time() + SERVICE_CHECK_SEC
            while True:
                ps_out = self._shell("ps")
                now = self.services(parse_ps(ps_out))[name] if ps_out is not None else {"running": None, "pid": None}
                if now["running"] or time.time() >= deadline or self._stop.is_set():
                    break
                time.sleep(1)
            if now["running"]:
                self._event("info", f"{name} 시작됨 (PID {now['pid']})")
            else:
                self._event("critical", f"{name} 를 시작했지만 {SERVICE_CHECK_SEC}초 안에 ps 에 보이지 않습니다")
            return {"name": name, "command": command, **now}
        finally:
            self._service_lock.release()

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
            if not connected:
                self.disconnect_count += 1
            self._event("info" if connected else "critical", "단말 연결됨" if connected else "단말 연결 끊김")

    def _check_alerts(self, sample: Dict) -> None:
        up = sample["uptime"].get("uptime_min")
        if up is not None and self._last_uptime is not None and up < self._last_uptime:
            self.reboot_count += 1
            self.ims_registration = None  # 재부팅 전 상태다. 다시 붙으면 새 통지가 온다
            self.nitz = {}
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
            if self.poll_once() is None:
                self._lost_since = self._lost_since or time.monotonic()
                if time.monotonic() - self._lost_since >= self.disconnect_stop_sec:
                    self._stop_lost()
                    return
            else:
                self._lost_since = None
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
            path = self.capture_path
            with open(path, "a", encoding="utf-8") as capture:
                for raw in self._proc.stdout:
                    if self.capture_path != path:
                        break  # 재부팅으로 새 파일을 시작했다. 남은 줄은 앞 부팅 것이다
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
            listeners = list(self._listeners)
        if "RegistrationStatus" in line:
            self._track_ims_registration(line)
        if "utcoff = " in line or "NITZ" in line or "UNSOL_1008" in line:
            self._track_nitz(line)
        for listener in listeners:
            listener(line)
        return True

    def _track_ims_registration(self, line: str) -> None:
        rec = parse_line(line)
        reg = parse_ims_registration(rec.msg) if rec else None
        if reg:
            with self._lock:
                self.ims_registration = {**reg, "time": rec.time, "text": rec.msg}

    def _track_nitz(self, line: str) -> None:
        rec = parse_line(line)
        nitz = parse_nitz(rec.msg if rec else line)
        if nitz:
            with self._lock:
                count = self.nitz.get(nitz["source"], {}).get("count", 0) + 1
                self.nitz[nitz["source"]] = {**nitz, "time": rec.time if rec else None,
                                             "text": rec.msg if rec else line.strip(), "count": count}

    def add_listener(self, listener: Callable[[str], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[str], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

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
                "stop_reason": self.stop_reason,
                "connected": self.connected,
                "log_streaming": self.log_streaming,
                "ims_registration": self.ims_registration,
                "nitz": dict(self.nitz),
                "services": self.services(self.latest["tasks"] if self.latest else None),
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
