import collections
import threading
import time

import pytest

from backend.call_test import CallTestRunner, validate_command, validate_number


def _log(msg):
    return f"[31/12/99 07:49:27.443400] [212] [ap] {msg}"


class FakeMonitor:
    def __init__(self):
        self.running = self.connected = self.log_streaming = True
        self.serial = "411E0A2E31781C7B"
        self.reboot_count = self.disconnect_count = 0
        self.lines = collections.deque(maxlen=50000)
        self.line_count = 0
        self.samples = collections.deque()
        self._listeners = []

    def add_listener(self, fn):
        self._listeners.append(fn)

    def remove_listener(self, fn):
        self._listeners.remove(fn)

    def emit(self, msg):
        line = _log(msg)
        self.lines.append(line)
        self.line_count += 1
        for fn in list(self._listeners):
            fn(line)


def _state(monitor, call_id, state):
    monitor.emit(f"call changed call_id : {call_id}")
    monitor.emit(f"call state: {state}")


class FakeSession:
    """telephonytool 대신 명령에 맞춰 로그 줄을 흘린다. behavior 로 단말 반응을 바꾼다."""

    def __init__(self, monitor, behavior):
        self.monitor = monitor
        self.behavior = behavior
        self.sent = []
        self.output = collections.deque()
        self.alive = True
        self.closed = False

    def later(self, delay, fn):
        threading.Timer(delay, fn).start()

    def send(self, command):
        validate_command(command)
        self.sent.append({"time": "", "command": command})
        self.behavior(self, command)

    def close(self):
        self.closed = True
        self.alive = False


def mo_ok(session, command):
    m = session.monitor
    cid = "/ril_0/voicecall01"
    if command == "get-ims-registration 0 1":
        session.later(0.01, lambda: m.emit("telephonytool_cmd_ims_get_registration: ims_registered: 0 - 1"))
    elif command.startswith("dial "):
        session.later(0.01, lambda: _state(m, cid, 2))
        session.later(0.03, lambda: _state(m, cid, 3))
        session.later(0.06, lambda: _state(m, cid, 0))
    elif command.startswith("hangup"):
        session.later(0.01, lambda: _state(m, cid, 6))


def ims_off(session, command):
    if command == "get-ims-registration 0 1":
        session.later(0.01, lambda: session.monitor.emit("telephonytool_cmd_ims_get_registration: ims_registered: 0 - 0"))


def mo_rejected(session, command):
    m = session.monitor
    if command == "get-ims-registration 0 1":
        mo_ok(session, command)
    elif command.startswith("dial "):
        # telephonytool 콜백 없이 RIL-IMSCALL 줄만 오는 빌드
        session.later(0.01, lambda: m.emit("[RIL-IMSCALL] call + sid=1 idx=5 mo DIALING"))
        session.later(0.03, lambda: m.emit("[RIL-IMSCALL] call - sid=1"))


def mt_ok(session, command):
    m = session.monitor
    cid = "/ril_0/voicecall02"
    if command == "get-ims-registration 0 1":
        mo_ok(session, command)
    elif command == "listen-call 0 0":
        session.later(0.05, lambda: _state(m, cid, 4))
    elif command == f"answer_0 0 {cid}":
        session.later(0.01, lambda: _state(m, cid, 0))
    elif command.startswith("hangup"):
        session.later(0.01, lambda: _state(m, cid, 6))


def _run(behavior, **kwargs):
    monitor = FakeMonitor()
    sessions = []

    def factory(serial):
        sessions.append(FakeSession(monitor, behavior))
        return sessions[-1]

    runner = CallTestRunner(monitor, session_factory=factory)
    runner.tool_ready_sec = 0
    params = dict(mode="mo", number="9560959279", count=2, hold_sec=0, gap_sec=0, setup_timeout_sec=10)
    params.update(kwargs)
    runner.start(**params)
    runner._thread.join(timeout=20)
    return runner.status(), sessions[0] if sessions else None


def test_validate_command_blocks_shell_escape_and_unknown():
    assert validate_command("dial 0 +919560959279 0")
    for bad in ("!reboot", "dial 0 1234 0; reboot", "top", "enable-ims 0 0", "hangup_0 0 x"):
        with pytest.raises(ValueError):
            validate_command(bad)


def test_validate_number():
    assert validate_number("010-1234-5678") == "01012345678"
    for bad in ("112", "+911", "100", "abc", "1"):
        with pytest.raises(ValueError):
            validate_number(bad)


def test_mo_repeats_and_passes():
    status, session = _run(mo_ok)
    assert status["ims_registered"] is True
    assert status["summary"] == {"done": 2, "passed": 2, "pass_rate": 100.0,
                                 "avg_active_ms": status["summary"]["avg_active_ms"]}
    first = status["iterations"][0]
    assert first["result"] == "pass" and first["call_id"] == "/ril_0/voicecall01"
    assert [t["state"] for t in first["timeline"]] == ["DIALING", "ALERTING", "ACTIVE", "DISCONNECTED"]
    assert first["alerting_ms"] < first["active_ms"]
    commands = [s["command"] for s in session.sent]
    assert commands[:3] == ["get-ims-registration 0 1", "listen-call 0 0", "dial 0 9560959279 0"]
    assert "hangup_0 0 /ril_0/voicecall01" in commands
    assert session.closed and status["phase"] == "끝남"


def test_ims_not_registered_places_no_call():
    status, session = _run(ims_off)
    assert status["ims_registered"] is False
    assert "IMS 미등록" in status["error"]
    assert not any(s["command"].startswith("dial") for s in session.sent)
    assert status["iterations"] == [] and session.closed


def test_mo_ended_before_connect_uses_imscall_fallback():
    status, _ = _run(mo_rejected, count=1)
    it = status["iterations"][0]
    assert it["result"] == "fail" and it["reason"] == "연결 전에 종료"
    assert [t["state"] for t in it["timeline"]] == ["DIALING", "DISCONNECTED"]


def test_mt_answers_incoming_call():
    status, session = _run(mt_ok, mode="mt", number="", count=1, incoming_timeout_sec=10, answer_delay_sec=0)
    it = status["iterations"][0]
    assert it["result"] == "pass" and it["incoming_ms"] is not None
    assert "answer_0 0 /ril_0/voicecall02" in [s["command"] for s in session.sent]


def test_mt_without_incoming_times_out():
    status, _ = _run(mo_ok, mode="mt", number="", count=1, incoming_timeout_sec=10, answer_delay_sec=0)
    # incoming_timeout 최소값이 10초라 테스트가 느려지지 않게 중지로 끊는 대신 결과만 본다
    assert status["iterations"][0]["result"] == "fail"
    assert status["iterations"][0]["reason"] == "착신이 오지 않음"


def test_requires_live_monitor():
    monitor = FakeMonitor()
    monitor.log_streaming = False
    with pytest.raises(RuntimeError):
        CallTestRunner(monitor, session_factory=lambda s: None).start(mode="mo", number="9560959279")


def test_device_lost_stops_the_test():
    def dies(session, command):
        mo_ok(session, command) if not command.startswith("dial") else None
        if command.startswith("dial"):
            def lose():
                session.monitor.connected = False
                session.monitor.disconnect_count += 1
            session.later(0.05, lose)

    status, session = _run(dies, count=3)
    assert status["error"] and "끊기거나" in status["error"]
    assert [i["result"] for i in status["iterations"]] == ["device_lost"]
    assert session.closed
