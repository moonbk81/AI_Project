import subprocess

import pytest

from backend.semtelcli_test import IIL_PRESETS, SemtelcliIilRunner, build_command, parse_output

OK_OUTPUT = """REQ   serial=1 OEM_HOOK_RAW iil reg: len=279 ipc(main=0x70 sub=0x01 type=0x03 len=275)
RSP   serial=1 id=59 status=0 len=0
UNSOL id=1028 len=12 ipc(len=8 main=0x70 sub=0x06 type=0x02 IIL): 01
UNSOL id=1009 len=4
"""


class FakeMonitor:
    def __init__(self, running=True, connected=True):
        self.running = running
        self.connected = connected
        self.serial = "R3XX"
        self.listeners = []

    def add_listener(self, listener):
        self.listeners.append(listener)

    def remove_listener(self, listener):
        self.listeners.remove(listener)


def test_build_command_uses_defaults_and_choices():
    assert build_command("connected") == "semtelcli iil connected"
    assert build_command("reg") == "semtelcli iil reg 1 1 13 0"
    assert build_command("reg", {"state": "0", "sip_err": "403", "rat": "20"}) == "semtelcli iil reg 0 1 20 403"
    assert build_command("pref", {"sms": "0"}) == "semtelcli iil pref 0 1"


@pytest.mark.parametrize("preset, params", [
    ("raw", {}),                                   # raw 는 자유 입력이라 뺐다
    ("reg", {"state": "1; reboot"}),
    ("reg", {"state": "9"}),
    ("reg", {"extra": "1"}),
    ("pref", {"sms": "1 &"}),
])
def test_build_command_rejects_values_outside_choices(preset, params):
    with pytest.raises(ValueError):
        build_command(preset, params)


def test_every_preset_default_is_one_of_its_choices():
    for preset in IIL_PRESETS:
        for arg in preset["args"]:
            assert arg["default"] in {v for v, _ in arg["choices"]}, (preset["id"], arg["key"])


def test_parse_output_reads_request_response_and_iil_answers():
    parsed = parse_output(OK_OUTPUT)
    assert parsed["sent"] == {"serial": 1, "len": 279, "ipc_len": 275, "ipc": "IIL REGISTRATION NOTI"}
    assert parsed["response"]["status"] == 0
    # secril → imsservice 는 PDA 쪽 타입이다 (0x02 = GET)
    assert parsed["answers"] == [{"ipc": "IIL PREFERENCE GET", "ipc_len": 8, "data": "01", "iil": True}]
    assert parsed["verdict"] == "pass"


@pytest.mark.parametrize("text, reason", [
    ("REQ   serial=1 OEM_HOOK_RAW iil connected: len=11 ipc(main=0x70 sub=0x12 type=0x03 len=7)\n"
     "no response within 35 s\n", "35초"),
    ("REQ   serial=1 OEM_HOOK_RAW iil connected: len=11 ipc(main=0x70 sub=0x12 type=0x03 len=7)\n"
     "RSP   serial=1 id=59 status=2 len=0\n", "status=2"),
    ("send failed: 32\n", "보내지 못했다"),
])
def test_parse_output_failures(text, reason):
    parsed = parse_output(text)
    assert parsed["verdict"] == "fail"
    assert reason in parsed["reason"]


def test_run_sends_one_adb_shell_and_collects_secril_logs():
    monitor = FakeMonitor()
    calls = []

    def runner(args, timeout):
        calls.append(args)
        for line in ("10-21 16:00:00.000 I secril: IMS-MGR: EVENT_IMS_REGISTRATION_STATE",
                     "10-21 16:00:00.001 I other: unrelated"):
            for listener in list(monitor.listeners):
                listener(line)
        return subprocess.CompletedProcess(args, 0, OK_OUTPUT, "")

    runner_ = SemtelcliIilRunner(monitor, runner=runner, settle_sec=0)
    record = runner_.run("reg", {"state": "1"})
    assert calls == [["adb", "-s", "R3XX", "shell", "semtelcli iil reg 1 1 13 0"]]
    assert record["verdict"] == "pass"
    assert record["logs"] == ["10-21 16:00:00.000 I secril: IMS-MGR: EVENT_IMS_REGISTRATION_STATE"]
    assert monitor.listeners == []
    assert runner_.status()["history"][0]["command"] == "semtelcli iil reg 1 1 13 0"
    assert runner_.status()["running"] is None


def test_run_requires_connected_monitor():
    runner_ = SemtelcliIilRunner(FakeMonitor(connected=False), runner=lambda a, t: None, settle_sec=0)
    with pytest.raises(RuntimeError):
        runner_.run("connected")


def test_run_timeout_is_a_failed_record_not_an_exception():
    def runner(args, timeout):
        raise subprocess.TimeoutExpired(args, timeout, output=b"REQ   serial=1 ...\n")

    monitor = FakeMonitor()
    record = SemtelcliIilRunner(monitor, runner=runner, settle_sec=0).run("connected")
    assert record["verdict"] == "fail"
    assert "끝나지 않았다" in record["reason"]
    assert monitor.listeners == []


def test_missing_semtelcli_on_watch_is_reported():
    def runner(args, timeout):
        return subprocess.CompletedProcess(args, 0, "nsh: semtelcli: command not found\n", "")

    record = SemtelcliIilRunner(FakeMonitor(), runner=runner, settle_sec=0).run("connected")
    assert record["verdict"] == "fail"
    assert "semtelcli 가 없다" in record["reason"]
