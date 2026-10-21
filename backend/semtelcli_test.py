"""워치의 semtelcli 로 imsservice 의 IIL 을 secril 에 넣어 본다 (`semtelcli iil ...`, CL 10300841).

secril 은 IIL 을 OEM_HOOK_RAW(OEM_FUNCTION_ID_CP_IMS) 로 받는다 (CL 10300842 HalIoChannel).
망이 IMS 등록을 거절해 imsservice 가 IIL 을 안 보낼 때도 secril IIL 경로를 볼 수 있다.

- 명령은 화면에서 고른 값으로만 만든다. 값은 아래 IIL_PRESETS 의 선택지 밖으로 못 나간다 — 자유 입력은 없다
  (`iil raw` 도 그래서 뺐다).
- semtelcli 는 혼자 끝난다: 응답을 35초까지 기다리고, 받으면 secril 이 되돌려 보내는 IIL
  (UNSOL_OEM_HOOK_RAW) 을 3초 더 보여 준다. 워치에 신호를 보낼 일이 없다.
  시간이 넘으면 끊는 건 호스트의 adb 뿐이다.
- 결과는 semtelcli 출력(REQ/RSP/UNSOL 줄)과, 그 사이 실시간 모니터가 받은 secril 로그(IMS-MGR, HalIoChannel 등)다.

출력 형식 (telephony/tools/cli.cpp):
  REQ   serial=1 OEM_HOOK_RAW iil reg: len=279 ipc(main=0x70 sub=0x01 type=0x03 len=275)
  RSP   serial=1 id=59 status=0 len=0
  UNSOL id=1028 len=12 ipc(len=8 main=0x70 sub=0x06 type=0x02 IIL): 01
"""

from __future__ import annotations

import collections
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable, Deque, Dict, List, Optional

RIL_UNSOL_OEM_HOOK_RAW = 1028
RUN_TIMEOUT_SEC = 45        # semtelcli 는 35초 + 3초면 끝난다
LOG_SETTLE_SEC = 1.0        # 끝난 뒤 secril 로그가 logcat 으로 넘어올 틈
MAX_HISTORY = 20
MAX_LOG_LINES = 200

# 실시간 모니터가 받은 줄 중 IIL 경로에서 찍히는 것 (secril imsmgr.cpp, haliochannel.cpp)
LOG_KEYWORDS = ("IMS-MGR", "HalIoChannel", "IIL", "OEM_HOOK_RAW", "Mask:", "Enable SMS", "SetImsRegState",
                "CP_IMS")

# ipciil.h / ipcdefv41.h
MAIN_CMDS = {0x70: "IIL", 0x04: "SMS", 0x08: "NET", 0x0D: "GPRS"}
IIL_SUBS = {0x01: "REGISTRATION", 0x02: "ISIMAUTH", 0x03: "EXPLICIT_DETACH", 0x04: "DEREGISTER",
            0x05: "MOBILEDATA", 0x06: "PREFERENCE", 0x07: "PARAMETERS", 0x08: "REGISTRATION_E911",
            0x09: "DEREGISTRATION_E911", 0x0A: "PDN_INFO_E911", 0x0B: "SET_DEREGISTRATION", 0x0E: "SSAC_INFO",
            0x11: "ISIM_LOADED", 0x12: "IIL_CONNECTED", 0x13: "PB_INIT_COMPLETED", 0x14: "IMS_CALL_STATE_NOTI",
            0x15: "CHANGE_PREFERRED_NETWORK_TYPE", 0x20: "EMC_ATTACH_AUTH", 0x21: "VONR_USER_STATUS",
            0x22: "EMERGENCY_CALL_DATA"}
# 타입 값은 방향마다 다르다 (ipcv41.h). imsservice 가 폰 자리, secril 이 PDA 자리다
TYPES_TO_RIL = {0x01: "INDI", 0x02: "RESP", 0x03: "NOTI"}                                   # imsservice → secril
TYPES_FROM_RIL = {0x01: "EXEC", 0x02: "GET", 0x03: "SET", 0x04: "CFRM", 0x05: "EVENT"}       # secril → imsservice

# 화면이 그리는 선택지. 각 인자는 (키, 이름, [(값, 표시)...], 기본값). 값이 semtelcli 인자 그대로다
IIL_PRESETS = [
    {
        "id": "connected", "label": "IIL 연결 (IIL_CONNECTED)",
        "description": "imsservice 가 붙었다고 알린다. secril 은 첫 IIL 을 받아야 imsservice 쪽 쓰기를 연다.",
        "args": [],
    },
    {
        "id": "reg", "label": "IMS 등록 통지 (REGISTRATION)",
        "description": "275바이트 ipc_imsframework_ims_regi_noti_type. 망이 거절해도 등록된 것처럼 secril 을 움직여 본다.",
        "args": [
            {"key": "state", "label": "상태", "default": "1", "choices": [
                ("0", "0 NOT_REGISTERED"), ("1", "1 REGISTERED"), ("2", "2 LIMITED"),
                ("3", "3 NOT_REGISTERED_E911"), ("4", "4 REGISTERED_E911")]},
            {"key": "features", "label": "기능", "default": "1", "choices": [
                ("0", "없음"), ("1", "VoLTE"), ("2", "SMS over IP"), ("3", "VoLTE + SMS"),
                ("4", "RCS"), ("5", "VoLTE + RCS"), ("7", "VoLTE + SMS + RCS")]},
            {"key": "rat", "label": "RAT", "default": "13", "choices": [("13", "13 LTE"), ("20", "20 NR")]},
            {"key": "sip_err", "label": "SIP 오류", "default": "0", "choices": [
                ("0", "0 없음"), ("403", "403 Forbidden"), ("404", "404 Not Found"), ("408", "408 Timeout"),
                ("480", "480 Unavailable"), ("500", "500 Server Error"), ("503", "503 Service Unavailable")]},
        ],
    },
    {
        "id": "pref", "label": "IMS 설정 (PREFERENCE)",
        "description": "SMS over IMS 켜기/끄기. 나머지는 3GPP 형식, VoLTE EUTRAN PS / UTRAN CS, USSD CS 로 고정.",
        "args": [
            {"key": "sms", "label": "SMS over IMS", "default": "1", "choices": [("1", "켜기"), ("0", "끄기")]},
            {"key": "ims_support", "label": "ims_support_type", "default": "1",
             "choices": [("0", "0"), ("1", "1"), ("2", "2")]},
        ],
    },
]
PRESETS = {p["id"]: p for p in IIL_PRESETS}

REQ_RE = re.compile(r'^REQ\s+serial=(\d+) OEM_HOOK_RAW iil \w+: len=(\d+) '
                    r'ipc\(main=0x([0-9a-f]{2}) sub=0x([0-9a-f]{2}) type=0x([0-9a-f]{2}) len=(\d+)\)')
RSP_RE = re.compile(r'^RSP\s+serial=(\d+) id=(-?\d+) status=(-?\d+) len=(\d+)')
UNSOL_IPC_RE = re.compile(r'^UNSOL id=(\d+) len=(\d+) ipc\(len=(\d+) main=0x([0-9a-f]{2}) sub=0x([0-9a-f]{2}) '
                          r'type=0x([0-9a-f]{2})(?: IIL)?\):((?: [0-9a-f]{2})*)( \.\.\.)?')


def build_command(preset_id: str, params: Optional[Dict[str, str]] = None) -> str:
    """고른 값으로 `semtelcli iil ...` 를 만든다. 선택지에 없는 값이면 ValueError."""
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise ValueError(f"없는 IIL 시험: {preset_id}")
    params = params or {}
    unknown = set(params) - {a["key"] for a in preset["args"]}
    if unknown:
        raise ValueError(f"{preset_id} 에 없는 인자: {', '.join(sorted(unknown))}")
    words = ["semtelcli", "iil", preset_id]
    for arg in preset["args"]:
        value = str(params.get(arg["key"], arg["default"]))
        if value not in {v for v, _ in arg["choices"]}:
            raise ValueError(f"{arg['label']} 에 고를 수 없는 값: {value!r}")
        words.append(value)
    return " ".join(words)


def _ipc_name(main: int, sub: int, cmd_type: int, types: Dict[int, str]) -> str:
    main_name = MAIN_CMDS.get(main, f"0x{main:02x}")
    sub_name = IIL_SUBS.get(sub, f"0x{sub:02x}") if main == 0x70 else f"0x{sub:02x}"
    return f"{main_name} {sub_name} {types.get(cmd_type, f'0x{cmd_type:02x}')}"


def parse_output(text: str) -> Dict:
    """semtelcli 출력에서 보낸 IPC, 응답, secril 이 돌려 보낸 IPC 를 뽑는다."""
    sent = response = None
    answers: List[Dict] = []
    timed_out = "no response within" in text
    send_failed = "send failed" in text
    for line in text.splitlines():
        line = line.strip()
        m = REQ_RE.match(line)
        if m:
            main, sub, cmd_type = (int(m.group(i), 16) for i in (3, 4, 5))
            sent = {"serial": int(m.group(1)), "len": int(m.group(2)), "ipc_len": int(m.group(6)),
                    "ipc": _ipc_name(main, sub, cmd_type, TYPES_TO_RIL)}
            continue
        m = RSP_RE.match(line)
        if m and response is None:
            response = {"serial": int(m.group(1)), "id": int(m.group(2)), "status": int(m.group(3)),
                        "len": int(m.group(4))}
            continue
        m = UNSOL_IPC_RE.match(line)
        if m and int(m.group(1)) == RIL_UNSOL_OEM_HOOK_RAW:
            main, sub, cmd_type = (int(m.group(i), 16) for i in (4, 5, 6))
            answers.append({"ipc": _ipc_name(main, sub, cmd_type, TYPES_FROM_RIL), "ipc_len": int(m.group(3)),
                            "data": m.group(7).strip() + (" ..." if m.group(8) else ""), "iil": main == 0x70})
    if send_failed:
        verdict, reason = "fail", "semtelcli 가 요청을 보내지 못했다 (telephonyd 소켓?)"
    elif timed_out:
        verdict, reason = "fail", "35초 안에 응답이 없다"
    elif response is None:
        verdict, reason = "fail", "응답(RSP) 줄이 없다"
    elif response["status"] != 0:
        verdict, reason = "fail", f"RSP status={response['status']}"
    else:
        verdict = "pass"
        reason = f"secril 이 IPC {len(answers)}개를 돌려 보냄" if answers else "응답 OK, 돌려 보낸 IPC 없음 (3초)"
    return {"sent": sent, "response": response, "answers": answers, "verdict": verdict, "reason": reason}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


Runner = Callable[[List[str], int], subprocess.CompletedProcess]


def _run(args: List[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout)


class SemtelcliIilRunner:
    """한 번에 하나씩 `adb shell semtelcli iil ...` 를 돌리고 결과를 남긴다."""

    def __init__(self, monitor, adb: str = "adb", runner: Runner = _run, settle_sec: float = LOG_SETTLE_SEC):
        self.monitor = monitor
        self.adb = adb
        self.runner = runner
        self.settle_sec = settle_sec
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self.running: Optional[str] = None
        self.history: Deque[Dict] = collections.deque(maxlen=MAX_HISTORY)

    def run(self, preset_id: str, params: Optional[Dict[str, str]] = None) -> Dict:
        command = build_command(preset_id, params)
        if not (self.monitor.running and self.monitor.connected):
            raise RuntimeError("실시간 모니터가 단말에 붙어 있어야 합니다.")
        if not self._busy.acquire(blocking=False):
            raise RuntimeError(f"다른 semtelcli 시험이 도는 중입니다: {self.running}")
        logs: List[str] = []

        def on_line(line: str) -> None:
            if any(k in line for k in LOG_KEYWORDS) and len(logs) < MAX_LOG_LINES:
                logs.append(line)

        started = _now()
        t0 = time.monotonic()
        self.running = command
        self.monitor.add_listener(on_line)
        try:
            try:
                result = self.runner([self.adb, "-s", self.monitor.serial, "shell", command], RUN_TIMEOUT_SEC)
                output = (result.stdout or "") + (result.stderr or "")
                error = None
                if result.returncode != 0 and "REQ" not in output:
                    error = f"adb shell 실패 (exit {result.returncode}) {output.strip()[:200]}"
            except subprocess.TimeoutExpired as exc:
                out = exc.stdout or ""
                output = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
                error = f"{RUN_TIMEOUT_SEC}초 안에 semtelcli 가 끝나지 않았다"
            except OSError as exc:
                output, error = "", f"adb 를 실행하지 못했다: {exc}"
            if self.settle_sec:
                time.sleep(self.settle_sec)
        finally:
            self.monitor.remove_listener(on_line)
            self.running = None
            self._busy.release()

        parsed = parse_output(output)
        if "not found" in output and "semtelcli" in output:
            error = error or "워치에 semtelcli 가 없다 (CL 10300841 이 들어간 빌드인지 확인)"
        if error:
            parsed["verdict"], parsed["reason"] = "fail", error
        record = {"time": started, "preset": preset_id, "label": PRESETS[preset_id]["label"], "command": command,
                  "elapsed_ms": int((time.monotonic() - t0) * 1000), **parsed,
                  "output": output.splitlines()[-200:], "logs": logs}
        with self._lock:
            self.history.append(record)
        return record

    def status(self) -> Dict:
        with self._lock:
            return {"running": self.running, "presets": IIL_PRESETS, "history": list(self.history)[::-1]}
