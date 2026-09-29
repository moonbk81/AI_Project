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
