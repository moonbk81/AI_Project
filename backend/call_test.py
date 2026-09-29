"""워치에서 telephonytool 로 발신(MO)·착신(MT) 콜 테스트를 반복한다.

telephonytool (rtos/frameworks/connectivity/telephony/tools/telephony_tool.c):
- `telephonytool>` 프롬프트에서 stdin 으로 한 줄씩 명령을 받는다 → `adb shell telephonytool` 에 흘려 넣는다
- `q` 로 끝난다. stdin 이 끊겨도 readline 이 실패해서 끝난다 — 워치에 신호를 보낼 일이 없다
  (남은 셸에 SIGINT 를 보냈다가 워치가 크래시한 적이 있다)
- 결과는 syslog 로 나간다 → 실시간 모니터의 logcat 으로 받는다
  - `listen-call 0 0` 뒤 콜 상태 콜백: `call changed call_id : /ril_0/voicecall01` 다음 줄 `call state: 3`
  - `get-ims-registration 0 1` → `telephonytool_cmd_ims_get_registration: ims_registered: 0 - 1` (ret - status)
- `!` 로 시작하는 줄은 워치에서 system() 을 부른다 → 여기서 보내는 명령은 COMMAND_PATTERNS 로만 만든다

콜 상태(tapi_call.h): 0 ACTIVE, 1 HELD, 2 DIALING, 3 ALERTING, 4 INCOMING, 5 WAITING, 6 DISCONNECTED.
콜 상태 콜백이 없는 빌드에 대비해 RIL-IMSCALL 의 `call + ... mo DIALING` / `call ~ ... A -> B` 도 본다.
콜은 IMS 등록 상태에서만 된다 → 시작 전에 등록을 확인한다.
"""

from __future__ import annotations

import collections
import queue
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Deque, Dict, List, Optional

from parsers.rtos import RtosCallFlowParser

COMMAND_PATTERNS = [
    re.compile(r'^dial 0 \+?[0-9*#]{2,20} 0$'),
    re.compile(r'^answer_0 0 /ril_\d+/voicecall\d+$'),
    re.compile(r'^hangup_0 0 /ril_\d+/voicecall\d+$'),
    re.compile(r'^hangup-all 0$'),
    re.compile(r'^listen-call 0 0$'),
    re.compile(r'^get-call 0$'),
    re.compile(r'^get-ims-registration 0 1$'),
    re.compile(r'^q$'),
]
NUMBER_RE = re.compile(r'^\+?[0-9*#]{2,20}$')
# 테스트로 긴급 번호를 걸면 안 된다. 망이 긴급호로 처리한다 (인도 망 로그가 많아서 인도 번호도 넣었다)
EMERGENCY_NUMBERS = {"112", "911", "999", "000", "08", "110", "118", "119", "120", "122",
                     "100", "101", "102", "108", "1098", "1091"}

CALL_ID_RE = re.compile(r'call changed call_id : (\S+)')
CALL_STATE_RE = re.compile(r'call state: (-?\d+)')
IMS_REG_RE = re.compile(r'ims_registered: (-?\d+) - (\d+)')
IMSCALL_NEW_RE = re.compile(r'call \+ sid=(\d+) idx=(\d+) (mo|mt) (\w+)')
IMSCALL_TRANS_RE = re.compile(r'call ~ sid=(\d+) idx=(\d+) (\w+) -> (\w+)')
IMSCALL_END_RE = re.compile(r'call - sid=(\d+)')

STATE_NAMES = {0: "ACTIVE", 1: "HELD", 2: "DIALING", 3: "ALERTING", 4: "INCOMING", 5: "WAITING", 6: "DISCONNECTED"}
NAME_STATES = {v: k for k, v in STATE_NAMES.items()}
ENDED = "DISCONNECTED"

TOOL_READY_SEC = 8        # telephonytool 이 tapi 에 붙는 데 걸리는 시간 (문서 예시로 약 0.5초)
IMS_CHECK_TIMEOUT_SEC = 8
HANGUP_TIMEOUT_SEC = 15
ANSWER_TIMEOUT_SEC = 20
QUIT_TIMEOUT_SEC = 8


def validate_command(command: str) -> str:
    if not any(p.match(command) for p in COMMAND_PATTERNS):
        raise ValueError(f"telephonytool 에 보낼 수 없는 명령: {command!r}")
    return command


def validate_number(number: str) -> str:
    number = (number or "").strip().replace("-", "").replace(" ", "")
    if not NUMBER_RE.match(number):
        raise ValueError("번호는 숫자(+, *, # 포함) 2~20자리여야 합니다.")
    if number.lstrip("+") in EMERGENCY_NUMBERS:
        raise ValueError(f"긴급 번호({number})로는 테스트할 수 없습니다.")
    return number


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class TelephonyToolSession:
    """`adb shell telephonytool` 하나. 명령은 검사를 거쳐 stdin 으로만 보낸다."""

    def __init__(self, serial: str, adb: str = "adb", popen=subprocess.Popen):
        self.proc = popen([adb, "-s", serial, "shell", "telephonytool"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
        self.output: Deque[str] = collections.deque(maxlen=500)
        self.sent: List[Dict[str, str]] = []
        self._reader = threading.Thread(target=self._read, name="telephonytool-out", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.output.append(line.rstrip("\r\n"))

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def send(self, command: str) -> None:
        validate_command(command)
        if not self.alive:
            raise RuntimeError("telephonytool 이 끝나 있습니다.")
        self.sent.append({"time": _now(), "command": command})
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        """`q` → stdin 닫기(EOF) 순서로 워치 쪽이 스스로 끝나게 한다. 호스트 adb 만 마지막에 정리한다."""
        for step in ("q", "eof"):
            if not self.alive:
                break
            try:
                if step == "q":
                    self.send("q")
                else:
                    self.proc.stdin.close()
            except (OSError, ValueError, RuntimeError):
                pass
            deadline = time.time() + QUIT_TIMEOUT_SEC
            while self.alive and time.time() < deadline:
                time.sleep(0.1)
        if self.alive:
            self.proc.terminate()


class CallTestRunner:
    def __init__(self, monitor, adb: str = "adb", session_factory=None):
        self.monitor = monitor
        self.adb = adb
        self.session_factory = session_factory or (lambda serial: TelephonyToolSession(serial, adb))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._pending_call_id: Optional[str] = None
        self.session: Optional[TelephonyToolSession] = None
        self.tool_ready_sec = TOOL_READY_SEC
        self._device_lost = lambda: False
        self._reset({})

    def _reset(self, config: Dict) -> None:
        self.config = config
        self.phase = "대기"
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.error: Optional[str] = None
        self.ims_registered: Optional[bool] = None
        self.iterations: List[Dict] = []
        self.current: Optional[Dict] = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---- 제어 ----

    def start(self, mode: str, number: str = "", count: int = 1, hold_sec: int = 10, gap_sec: int = 5,
              setup_timeout_sec: int = 60, incoming_timeout_sec: int = 120, answer_delay_sec: int = 2) -> None:
        if self.running:
            raise RuntimeError("콜 테스트가 이미 돌고 있습니다.")
        if mode not in ("mo", "mt"):
            raise ValueError("mode 는 mo(발신) 또는 mt(착신) 입니다.")
        if not (self.monitor.running and self.monitor.connected and self.monitor.log_streaming):
            raise RuntimeError("실시간 모니터가 단말에 붙어 로그를 받는 중이어야 합니다 (콜 결과를 로그로 판정한다).")
        config = {
            "mode": mode, "number": validate_number(number) if mode == "mo" else None,
            "count": max(1, min(100, int(count))), "hold_sec": max(0, min(600, int(hold_sec))),
            "gap_sec": max(0, min(600, int(gap_sec))), "setup_timeout_sec": max(10, min(300, int(setup_timeout_sec))),
            "incoming_timeout_sec": max(10, min(1800, int(incoming_timeout_sec))),
            "answer_delay_sec": max(0, min(60, int(answer_delay_sec))), "serial": self.monitor.serial,
        }
        with self._lock:
            self._reset(config)
            self.started_at = _now()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="call-test", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=HANGUP_TIMEOUT_SEC + QUIT_TIMEOUT_SEC * 2 + 5)

    # ---- 진행 ----

    def _set_phase(self, text: str) -> None:
        with self._lock:
            self.phase = text

    def _on_line(self, line: str) -> None:
        self._lines.put(line)

    def _run(self) -> None:
        reboot0, disconnect0 = self.monitor.reboot_count, self.monitor.disconnect_count
        self._device_lost = lambda: (not self.monitor.connected or self.monitor.reboot_count != reboot0
                                     or self.monitor.disconnect_count != disconnect0)
        self.monitor.add_listener(self._on_line)
        try:
            self._set_phase("telephonytool 시작")
            self.session = self.session_factory(self.config["serial"])
            self._sleep(self.tool_ready_sec)
            self._check_ims()
            if self.ims_registered is not True:
                return
            self.session.send("listen-call 0 0")
            for n in range(1, self.config["count"] + 1):
                if self._stopping():
                    break
                self._iteration(n)
                if n < self.config["count"] and not self._stopping():
                    self._set_phase(f"{self.config['gap_sec']}초 쉬는 중")
                    self._sleep(self.config["gap_sec"])
        except Exception as exc:  # noqa: BLE001 — 화면에 원인을 보여준다
            with self._lock:
                self.error = str(exc)
        finally:
            self.monitor.remove_listener(self._on_line)
            if self.session:
                if self.current and self.current.get("call_id") and self.session.alive:
                    self._safe_send("hangup-all 0")
                self.session.close()
            with self._lock:
                if self._device_lost():
                    self.error = self.error or "테스트 중 단말 연결이 끊기거나 재부팅됐습니다."
                self.phase = "끝남" if not self.error else "중단됨"
                self.finished_at = _now()
                self.current = None

    def _stopping(self) -> bool:
        return self._stop.is_set() or self._device_lost()

    def _sleep(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline and not self._stopping():
            self._drain(until=min(deadline, time.time() + 0.5))

    def _safe_send(self, command: str) -> None:
        try:
            self.session.send(command)
        except (OSError, ValueError, RuntimeError):
            pass

    def _check_ims(self) -> None:
        self._set_phase("IMS 등록 확인")
        self.session.send("get-ims-registration 0 1")
        deadline = time.time() + IMS_CHECK_TIMEOUT_SEC
        while time.time() < deadline and not self._stopping():
            try:
                line = self._lines.get(timeout=0.3)
            except queue.Empty:
                continue
            m = IMS_REG_RE.search(line)
            if m:
                with self._lock:
                    self.ims_registered = m.group(1) == "0" and m.group(2) == "1"
                    if not self.ims_registered:
                        self.error = f"IMS 미등록 (ret={m.group(1)}, registered={m.group(2)}) — 콜 테스트를 하지 않았습니다."
                return
        with self._lock:
            self.ims_registered = None
            self.error = "IMS 등록 상태를 확인하지 못했습니다 (telephonytool 응답 없음)."

    # 로그에서 콜 상태를 뽑는다. 콜백의 call_id 줄 뒤에 state 줄이 온다
    def _drain(self, until: float) -> List[Dict]:
        changes = []
        while True:
            remaining = until - time.time()
            if remaining <= 0:
                break
            try:
                line = self._lines.get(timeout=min(0.3, remaining))
            except queue.Empty:
                continue
            change = self._parse_state(line)
            if change:
                changes.append(change)
                self._apply(change)
        return changes

    def _parse_state(self, line: str) -> Optional[Dict]:
        m = CALL_ID_RE.search(line)
        if m:
            self._pending_call_id = m.group(1)
            return None
        m = CALL_STATE_RE.search(line)
        if m and self._pending_call_id:
            state = STATE_NAMES.get(int(m.group(1)), m.group(1))
            call_id, self._pending_call_id = self._pending_call_id, None
            return {"source": "tapi", "call_id": call_id, "state": state, "time": _now(), "line": line}
        m = IMSCALL_NEW_RE.search(line)
        if m:
            return {"source": "imscall", "call_id": None, "direction": m.group(3), "state": m.group(4),
                    "time": _now(), "line": line}
        m = IMSCALL_TRANS_RE.search(line)
        if m:
            return {"source": "imscall", "call_id": None, "state": m.group(4), "time": _now(), "line": line}
        if IMSCALL_END_RE.search(line):
            return {"source": "imscall", "call_id": None, "state": ENDED, "time": _now(), "line": line}
        return None

    def _apply(self, change: Dict) -> None:
        cur = self.current
        if not cur:
            return
        if change.get("call_id") and not cur.get("call_id"):
            cur["call_id"] = change["call_id"]
        if cur.get("call_id") and change.get("call_id") and change["call_id"] != cur["call_id"]:
            return  # 다른 콜 (대기 중 착신 등)
        state = change["state"]
        if state not in cur["reached"]:
            cur["reached"][state] = time.time()
            cur["timeline"].append({"time": change["time"], "state": state, "source": change["source"]})

    def _wait_for(self, states, timeout: float) -> Optional[str]:
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stopping():
            for state in states:
                if state in self.current["reached"]:
                    return state
            self._drain(until=min(deadline, time.time() + 0.5))
        for state in states:
            if state in self.current["reached"]:
                return state
        return None

    def _iteration(self, n: int) -> None:
        mode = self.config["mode"]
        cur = {"n": n, "mode": mode, "start": _now(), "t0": time.time(), "call_id": None,
               "reached": {}, "timeline": [], "result": None, "reason": None,
               "line_start": self.monitor.line_count}
        with self._lock:
            self.current = cur
        if mode == "mo":
            self._set_phase(f"{n}회차 발신 중 ({self.config['number']})")
            self.session.send(f"dial 0 {self.config['number']} 0")
            got = self._wait_for(["ACTIVE", ENDED], self.config["setup_timeout_sec"])
        else:
            self._set_phase(f"{n}회차 착신 기다리는 중 (최대 {self.config['incoming_timeout_sec']}초)")
            got = self._wait_for(["INCOMING", "WAITING", ENDED], self.config["incoming_timeout_sec"])
            if got in ("INCOMING", "WAITING"):
                self._set_phase(f"{n}회차 착신 — {self.config['answer_delay_sec']}초 뒤 응답")
                self._sleep(self.config["answer_delay_sec"])
                if cur.get("call_id") and not self._stopping():
                    self.session.send(f"answer_0 0 {cur['call_id']}")
                    got = self._wait_for(["ACTIVE", ENDED], ANSWER_TIMEOUT_SEC)
                elif not self._stopping():
                    cur["reason"] = "착신 call_id 를 받지 못해 응답하지 못함"
                    got = None
        self._finish_iteration(cur, got)

    def _finish_iteration(self, cur: Dict, got: Optional[str]) -> None:
        if got == "ACTIVE":
            self._set_phase(f"{cur['n']}회차 통화 중 ({self.config['hold_sec']}초 유지)")
            deadline = time.time() + self.config["hold_sec"]
            while time.time() < deadline and not self._stopping() and ENDED not in cur["reached"]:
                self._drain(until=min(deadline, time.time() + 0.5))
            if ENDED in cur["reached"]:
                cur["result"], cur["reason"] = "fail", "통화 중 끊김 (유지 시간 전에 종료)"
            else:
                cur["result"] = "pass"
        elif got == ENDED:
            cur["result"] = "fail"
            cur["reason"] = cur["reason"] or "연결 전에 종료"
        elif self._stop.is_set():
            cur["result"], cur["reason"] = "stopped", "사용자가 중지"
        elif self._device_lost():
            cur["result"], cur["reason"] = "device_lost", "단말 연결 끊김/재부팅"
        else:
            cur["result"] = "fail"
            cur["reason"] = cur["reason"] or ("착신이 오지 않음" if cur["mode"] == "mt" and not cur["reached"]
                                              else "제한 시간 안에 연결되지 않음")

        if ENDED not in cur["reached"] and not self._device_lost():
            self._set_phase(f"{cur['n']}회차 종료 중")
            self._safe_send(f"hangup_0 0 {cur['call_id']}" if cur.get("call_id") else "hangup-all 0")
            if self._wait_for([ENDED], HANGUP_TIMEOUT_SEC) is None:
                self._safe_send("hangup-all 0")
                self._wait_for([ENDED], HANGUP_TIMEOUT_SEC / 2)
        self._record(cur)

    def _record(self, cur: Dict) -> None:
        t0 = cur["t0"]
        at = {state: round((t - t0) * 1000) for state, t in cur["reached"].items()}
        lines = list(self.monitor.lines)[-(self.monitor.line_count - cur["line_start"]):] \
            if self.monitor.line_count > cur["line_start"] else []
        flow = RtosCallFlowParser().analyze(lines) if lines else {"calls": []}
        call = flow["calls"][-1] if flow.get("calls") else {}
        record = {
            "n": cur["n"], "mode": cur["mode"], "start": cur["start"], "end": _now(),
            "call_id": cur.get("call_id"), "result": cur["result"], "reason": cur["reason"],
            "alerting_ms": at.get("ALERTING"), "active_ms": at.get("ACTIVE"),
            "incoming_ms": at.get("INCOMING"), "ended_ms": at.get(ENDED),
            "timeline": cur["timeline"],
            "broken_at": (call.get("broken_at") or {}).get("label"),
            "fail_cause": call.get("fail_cause"), "fail_reason": call.get("fail_reason"),
            "sip_final_response": call.get("sip_final_response"), "sip_error": call.get("sip_error"),
            "cpu_max": self._cpu_max(cur["start"]),
            "log_lines": len(lines),
        }
        with self._lock:
            self.iterations.append(record)
            self.current = None

    def _cpu_max(self, since: str) -> Optional[float]:
        totals = [s["device_total"] for s in list(self.monitor.samples)
                  if s["time"] >= since and s.get("device_total") is not None]
        return max(totals) if totals else None

    # ---- 조회 ----

    def status(self) -> Dict:
        with self._lock:
            done = [i for i in self.iterations if i["result"] in ("pass", "fail", "device_lost")]
            passed = [i for i in done if i["result"] == "pass"]
            setup = [i["active_ms"] for i in passed if i.get("active_ms") is not None]
            cur = self.current
            return {
                "running": self.running,
                "config": dict(self.config),
                "phase": self.phase,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
                "ims_registered": self.ims_registered,
                "summary": {
                    "done": len(done), "passed": len(passed),
                    "pass_rate": round(len(passed) * 100.0 / len(done), 1) if done else None,
                    "avg_active_ms": round(sum(setup) / len(setup)) if setup else None,
                },
                "current": {"n": cur["n"], "call_id": cur.get("call_id"), "timeline": list(cur["timeline"])}
                if cur else None,
                "iterations": list(self.iterations),
                "sent": list(self.session.sent[-30:]) if self.session else [],
                "tool_output": list(self.session.output)[-30:] if self.session else [],
            }
