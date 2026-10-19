import subprocess
import time

import pytest

from backend.live_device import SAFE_COMMANDS, LiveDeviceMonitor, list_devices
from parsers.rtos.device_status import cpu_total, parse_free, parse_ps, parse_uptime

# 2026-09-29 실제 워치 출력에서 발췌
PS = """  PID GROUP PRI POLICY   TYPE    NPX STATE    EVENT     SIGMASK            STACK    USED FILLED    CPU COMMAND
    0     0   0 FIFO     Kthread   - Ready              0000000000000000 0002960 0001336  45.1%  62.0% Idle_Task
    2     0 100 RR       Kthread   - Waiting  Semaphore 0000000000000000 0001888 0001660  87.9%!  0.0% lpwork 0x1824c280 0x1824c2a4
   10     8 100 RR       pthread   - Waiting  MQ empty  8000000000000000 0003984 0001096  27.5%   0.0% eshell-session-0x18326e6c 0x106a9981
  158   158 100 RR       Task      - Ready              0000000000000000 0005968 0003444  57.7%  18.7% dbusdaemon --system --nopidfile --nofork
  164   164 100 RR       Task      - Waiting  Semaphore 0000000000000000 0008064 0006008  74.5%  32.3% rild
  792   792 100 RR       Task      - Waiting  Mutex:-1  0000000000000000 0008040 0001732  21.5%   6.2% sh -c ps
"""
FREE = """      total       used       free    maxused    maxfree  nused  nfree name
   43064732   17976084   25088648   18833904   23846968   1795    505 Umem
"""
UPTIME = "00:17:45 up  0:07, load average: 1.00, 1.00, 1.00"
DEVICES = "List of devices attached\n411E0A2E31781C7B       device usb:1-5 product:adb model:adb_board device:BES transport_id:1\n\n"
LOGCAT = [
    "[31/12/99 07:49:27.443400] [165] [ap] [0,0096]> RIL_REQUEST_OEM_HOOK_RAW\n",
    "[31/12/99 07:49:27.450000] [164] [ap] [RIL_CPP] [0096]> OEM_HOOK_RAW (raw_size=13)\n",
    "[31/12/99 07:49:27.460000] [165] [ap] [0,0096]< RIL_REQUEST_OEM_HOOK_RAW 1\n",
]


def test_parse_ps_handles_empty_and_two_word_events():
    tasks = {t["pid"]: t for t in parse_ps(PS)}
    assert len(tasks) == 6
    assert tasks[158]["event"] is None and tasks[158]["name"] == "dbusdaemon"
    assert tasks[10]["event"] == "MQ empty"
    assert tasks[792]["event"] == "Mutex:-1" and tasks[792]["command"] == "sh -c ps"
    assert tasks[2]["stack_warn"] is True and tasks[2]["stack_filled"] == 87.9
    assert tasks[164]["cpu"] == 32.3 and tasks[164]["stack_used"] == 6008
    assert cpu_total(list(tasks.values())) == 38.0


def test_parse_free_and_uptime():
    heap = parse_free(FREE)[0]
    assert heap["name"] == "Umem" and heap["used_pct"] == 41.7 and heap["max_used_pct"] == 43.7
    up = parse_uptime(UPTIME)
    assert up["uptime_min"] == 7 and up["load"] == [1.0, 1.0, 1.0]
    assert parse_uptime("12:00:00 up 2 days, 3:04, load average: 0.1, 0.2, 0.3")["uptime_min"] == 2 * 1440 + 184


class FakeAdb:
    """워치 대신 답한다. 보낸 명령을 모두 적어 둔다."""

    def __init__(self):
        self.calls = []
        self.online = True
        self.uptime = UPTIME

    def run(self, args, timeout):
        self.calls.append(args)
        if args[1:3] == ["devices", "-l"]:
            return subprocess.CompletedProcess(args, 0, DEVICES, "")
        if not self.online:
            return subprocess.CompletedProcess(args, 1, "", "error: no devices/emulators found")
        out = {"ps": PS, "free": FREE, "uptime": self.uptime}[args[-1]]
        return subprocess.CompletedProcess(args, 0, out, "")


class FakeLogcat:
    def __init__(self, args, **kwargs):
        self.args = args
        self.stdout = iter(LOGCAT + LOGCAT)  # 재연결하면 링 버퍼를 다시 보낸다
        self._done = False

    def poll(self):
        return 0 if self._done else None

    def terminate(self):
        self._done = True


def _monitor(tmp_path, adb):
    return LiveDeviceMonitor(runner=adb.run, popen=FakeLogcat, capture_dir=str(tmp_path))


def test_list_devices():
    devices = list_devices(runner=FakeAdb().run)
    assert devices == [{"serial": "411E0A2E31781C7B", "state": "device", "usb": "1-5", "product": "adb",
                        "model": "adb_board", "device": "BES", "transport_id": "1"}]


def test_poll_builds_sample_and_alerts(tmp_path):
    adb = FakeAdb()
    m = _monitor(tmp_path, adb)
    m.serial = "411E0A2E31781C7B"
    sample = m.poll_once()
    assert sample["total"] == 38.0
    assert sample["monitor_overhead"] == 6.2
    assert [t["pid"] for t in sample["tasks"]][:2] == [164, 158]
    assert 792 not in [t["pid"] for t in sample["tasks"]], "모니터 자신의 ps 는 태스크 목록에서 뺀다"
    assert [t["pid"] for t in sample["stack_warn"]] == [2]
    texts = [e["text"] for e in m.events]
    assert "단말 연결됨" in texts and any(t.startswith("스택 87.9%") for t in texts)

    adb.uptime = "00:00:10 up  0:00, load average: 0.0, 0.0, 0.0"
    m.poll_once()
    assert any(e["text"].startswith("재부팅 감지") for e in m.events)

    adb.online = False
    assert m.poll_once() is None
    assert m.connected is False and m.events[-1]["text"] == "단말 연결 끊김"


def test_only_safe_commands_reach_the_watch(tmp_path):
    adb = FakeAdb()
    m = _monitor(tmp_path, adb)
    m.serial = "X"
    m.poll_once()
    shell = [c[-1] for c in adb.calls if "shell" in c]
    assert shell and set(shell) <= set(SAFE_COMMANDS)
    with pytest.raises(ValueError):
        m._shell("top")


def test_log_stream_is_saved_deduped_and_analyzed(tmp_path):
    adb = FakeAdb()
    m = _monitor(tmp_path, adb)
    m.start("411E0A2E31781C7B", 3)
    deadline = time.time() + 5
    while time.time() < deadline and m.status()["line_count"] < 3:
        time.sleep(0.05)
    m.stop()
    m._analyze_if_new()

    status = m.status()
    assert status["line_count"] == 3, "다시 보낸 링 버퍼는 한 번만 받는다"
    with open(status["capture_path"], encoding="utf-8") as f:
        assert f.read().splitlines() == [line.rstrip("\n") for line in LOGCAT]
    assert status["analysis"]["oem_kpi"]["request_count"] == 1
    assert status["series"][0]["total"] == 38.0
    assert m.status(grep="RIL_CPP")["log_tail"] == [LOGCAT[1].rstrip("\n")]
    assert status["running"] is False


def test_stops_itself_when_device_stays_gone(tmp_path):
    adb = FakeAdb()
    adb.online = False
    m = LiveDeviceMonitor(runner=adb.run, popen=FakeLogcat, capture_dir=str(tmp_path), disconnect_stop_sec=0)
    m.start("411E0A2E31781C7B", 3)
    deadline = time.time() + 5
    while time.time() < deadline and m.running:
        time.sleep(0.05)
    status = m.status()
    assert status["running"] is False, "USB 가 빠진 채로 두면 시작 상태로 남지 않는다"
    assert "자동 중지" in status["stop_reason"]
    assert any(e["level"] == "critical" and "자동 중지" in e["text"] for e in status["events"])


def test_monitor_remembers_last_ims_registration(tmp_path):
    m = _monitor(tmp_path, FakeAdb())
    for registered in (0, 1):
        m._accept(f"[01/01/00 00:04:55.4{registered}0000] [160] [ap] [IPCT] [UNSL]< RegistrationStatus "
                  f"{{registered={registered} features=0x{registered} net={registered} err=0}}")
    reg = m.status()["ims_registration"]
    assert reg["registered"] is True and reg["time"] == "00:04:55.410000"
    assert reg["text"].startswith("[IPCT] [UNSL]< RegistrationStatus {registered=1")


IMS_ROW = ("  201   201 100 RR       Task      - Waiting  Semaphore 0000000000000000 0008064 0002008  24.9%   "
           "1.0% ims_service\n")


class ServiceAdb(FakeAdb):
    """`ims_service &` 를 받으면 그 뒤 ps 에 ims_service 가 보인다. hang=True 면 명령이 안 끝난다."""

    def __init__(self, hang=False, comes_up=True):
        super().__init__()
        self.started = False
        self.hang = hang
        self.comes_up = comes_up

    def run(self, args, timeout):
        if args[-1].endswith(" &"):
            self.calls.append(args)
            self.started = self.comes_up
            if self.hang:
                raise subprocess.TimeoutExpired(args, timeout)
            return subprocess.CompletedProcess(args, 0, "", "")
        result = super().run(args, timeout)
        if args[-1] == "ps" and self.started:
            result.stdout += IMS_ROW
        return result


def _service_monitor(tmp_path, adb, monkeypatch):
    m = _monitor(tmp_path, adb)
    m.serial = "411E0A2E31781C7B"
    m.connected = True
    monkeypatch.setattr(type(m), "running", property(lambda self: True))
    monkeypatch.setattr("backend.live_device.SERVICE_CHECK_SEC", 0)
    return m


def test_service_status_comes_from_latest_ps(tmp_path):
    m = _monitor(tmp_path, FakeAdb())
    assert m.status()["services"]["ims_service"] == {"running": None, "pid": None}, "ps 전에는 모른다"
    m.serial = "X"
    m.poll_once()
    assert m.status()["services"]["ims_service"] == {"running": False, "pid": None}


def test_start_service_sends_only_the_allowed_command_and_checks_ps(tmp_path, monkeypatch):
    adb = ServiceAdb()
    m = _service_monitor(tmp_path, adb, monkeypatch)
    result = m.start_service("ims_service")
    assert result == {"name": "ims_service", "command": "ims_service &", "running": True, "pid": 201}
    sent = [c[-1] for c in adb.calls if "shell" in c]
    assert set(sent) <= set(SAFE_COMMANDS) | {"ims_service &"} and sent.count("ims_service &") == 1
    assert any(e["text"] == "ims_service 시작됨 (PID 201)" for e in m.events)

    with pytest.raises(RuntimeError, match="이미 돌고"):
        m.start_service("ims_service")
    with pytest.raises(ValueError):
        m.start_service("reboot")
    assert [c[-1] for c in adb.calls].count("ims_service &") == 1


def test_start_service_that_holds_the_shell_is_checked_by_ps(tmp_path, monkeypatch):
    m = _service_monitor(tmp_path, ServiceAdb(hang=True), monkeypatch)
    assert m.start_service("ims_service")["running"] is True

    m2 = _service_monitor(tmp_path, ServiceAdb(comes_up=False), monkeypatch)
    assert m2.start_service("ims_service")["running"] is False
    assert any("ps 에 보이지 않습니다" in e["text"] for e in m2.events)


def test_start_service_needs_a_connected_monitor(tmp_path):
    with pytest.raises(RuntimeError, match="실시간 모니터"):
        _monitor(tmp_path, ServiceAdb()).start_service("ims_service")


class RebootAdb(FakeAdb):
    def run(self, args, timeout):
        if args[-1] == "reboot":
            self.calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")
        return super().run(args, timeout)


def test_reboot_sends_adb_reboot_and_starts_a_new_log(tmp_path, monkeypatch):
    adb = RebootAdb()
    m = _service_monitor(tmp_path, adb, monkeypatch)
    m.capture_path = str(tmp_path / "live_old.txt")
    m._accept(LOGCAT[0].rstrip("\n"))
    m.ims_registration = {"registered": True}

    result = m.reboot_device()
    assert ["adb", "-s", "411E0A2E31781C7B", "reboot"] in adb.calls
    assert not any("shell" in c and c[-1] == "reboot" for c in adb.calls), "셸 reboot 이 아니라 adb reboot"
    assert result["previous_capture_path"].endswith("live_old.txt")
    assert m.capture_path != result["previous_capture_path"] and m.capture_path.startswith(str(tmp_path))
    assert m.line_count == 0 and not m.lines and m.ims_registration is None
    # 앞 부팅과 같은 줄이 다시 와도 새 로그에서는 받는다 (RTC 가 없어 타임스탬프가 겹친다)
    assert m._accept(LOGCAT[0].rstrip("\n")) is True
    assert any(e["text"].startswith("재부팅 요청") for e in m.events)


def test_reboot_needs_a_connected_monitor(tmp_path):
    with pytest.raises(RuntimeError, match="실시간 모니터"):
        _monitor(tmp_path, RebootAdb()).reboot_device()


def test_monitor_keeps_last_nitz_per_source(tmp_path):
    m = _monitor(tmp_path, FakeAdb())
    m._accept("[01/01/00 00:05:01.100000] [151] [ap] [RILJ] [UNSL]< NITZ_TIME_RECEIVED {26/10/19,03:25:09+36,0}")
    m._accept("[01/01/00 00:05:01.120000] [210] [ap] sec = 9 min = 25 hour = 3                 "
              "mday = 19 mon = 10 year = 2026 dst = 0 utcoff = 32400 ")
    m._accept("[01/01/00 00:05:02.120000] [210] [ap] sec = 10 min = 25 hour = 3                 "
              "mday = 19 mon = 10 year = 2026 dst = 0 utcoff = 32400 ")
    nitz = m.status()["nitz"]
    assert nitz["telephonytool"]["count"] == 2 and nitz["telephonytool"]["local"] == "2026-10-19 12:25:10"
    assert nitz["telephonytool"]["time"] == "00:05:02.120000"
    assert nitz["rilj"]["utc"] == "2026-10-19 03:25:09" and nitz["rilj"]["utcoff_min"] == 540
