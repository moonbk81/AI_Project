"""RTOS(NuttX) assert 덤프를 묶어 어느 태스크가 어디서 왜 죽었는지 정리한다.

lastword 에서 본 형식 (모두 죽은 태스크 번호로 찍힌다):
  `assertion failed "old_refcount >= 1" file "/external/dbus/.../dbus-message.c" line 1733 function dbus_message_unref`
    ← 라이브러리(D-Bus)가 먼저 찍는 원래 조건. NuttX assert 위치는 이걸 abort 로 넘긴 자리다
  `dump_assert_info: Current Version: NuttX BES NuttX EVB 12.6.0  Sep 30 2026 19:04:46 arm`
  `dump_assert_info: Assertion failed : at file: /external/.../dbus-sysdeps.c:100 task: ims_service process: ims_service 0x1107ac19`
  `dump_assert_info: Module load info (bininfo=0x1928d2b0):` + `entrypt: / textalloc: / dataalloc:`
  `up_dump_register: R0: 182bf8e0 R1: 00000064 ...` (여러 줄)
  `dump_stackinfo: User Stack:` + `base: 0x.. / size: 00065424 / sp: 0x..`  (size 는 10진수)
  `stack_dump: 0x193108a8: 20103f10 ...`
  `sched_dumpstack: backtrace|237: 0x1064a440 0x1066f480 ...` (여러 줄, | 뒤가 태스크 번호)
덤프 뒤에는 재부팅 로그가 이어진다. 초기 부팅 줄은 날짜가 `00/01/00`, 태스크가 `[ 0]` 이다.

주소를 함수 이름으로 바꾸려면 같은 빌드의 ELF 가 있어야 해서 여기서는 주소만 남긴다.
"""

import json
import os
import re

from parsers.base import BaseParser
from parsers.rtos.line import WORD_TAG_RE, parse_lines

LIB_ASSERT_RE = re.compile(
    r'assertion failed "(?P<expr>.*)" file "(?P<file>[^"]+)" line (?P<line>\d+)(?: function (?P<func>\S+))?'
)
NUTTX_ASSERT_RE = re.compile(
    r'^Assertion failed\s*(?P<msg>.*?)\s*: at file: (?P<file>.+?):(?P<line>\d+) '
    r'task(?:\(CPU(?P<cpu>\d+)\))?: (?P<task>\S+)(?: process: (?P<proc>\S+))?(?: (?P<entry>0x[0-9a-fA-F]+))?'
)
VERSION_RE = re.compile(r'^Current Version: (.+)$')
MODULE_RE = re.compile(r'^Module load info \(bininfo=(0x[0-9a-fA-F]+)\)')
MODULE_FIELD_RE = re.compile(r'^\s*(entrypt|textalloc|dataalloc):\s*(\S+)')
REGISTER_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*): ([0-9a-fA-F]{8})\b')
STACK_HEAD_RE = re.compile(r'^\s*(\w[\w ]*?) Stack:\s*$')
STACK_FIELD_RE = re.compile(r'^\s*(base|size|sp):\s*(\S+)')
BACKTRACE_RE = re.compile(r'^backtrace\|\s*(\d+):\s*(.*)$')
ADDRESS_RE = re.compile(r'0x[0-9a-fA-F]+')

DUMP_TAGS = {"dump_assert_info", "up_dump_register", "dump_stackinfo", "stack_dump", "sched_dumpstack",
             "dump_task", "dump_tasks"}
BOOT_TAGS = {"hal_trace_init_program_regions", "board_late_initialize"}
# 라이브러리 assert 와 NuttX 덤프 첫 줄은 수 ms 차이다. 이보다 멀면 다른 크래시로 본다
SAME_CRASH_SEC = 2.0
CONTEXT_WINDOW_SEC = 5.0
CONTEXT_TASK_LINES = 15
CONTEXT_ALL_LINES = 25


def _body(rec):
    m = WORD_TAG_RE.match(rec.msg)
    return m.group(2) if m and m.group(1) == rec.tag else rec.msg


def _hex(text):
    try:
        return int(text, 16)
    except (TypeError, ValueError):
        return None


def _new_crash(rec):
    return {
        "time": rec.time, "date": rec.date, "sec": rec.sec, "line_no": rec.line_no, "last_line": rec.line_no,
        "task_id": rec.task, "task_name": None, "process": None, "entry": None,
        "lib_assert": None, "nuttx_assert": None, "version": None,
        "module": {}, "registers": {}, "stacks": [], "backtraces": {}, "stack_dump": [], "task_dump": [],
    }


class RtosCrashParser(BaseParser):
    def analyze(self, lines):
        records = parse_lines(lines, sort=False)
        crashes = self._collect(records)
        by_time = sorted(records, key=lambda r: (r.sec, r.line_no))
        for crash in crashes:
            crash["stacks"] = [self._stack_usage(s) for s in crash["stacks"]]
            crash["backtraces"] = [{"task_id": tid, "addresses": addrs} for tid, addrs in crash["backtraces"].items()]
            crash["reboot"] = self._reboot_after(records, crash["last_line"])
            crash["before_task"], crash["before_all"] = self._context(by_time, crash)
            crash["findings"] = self._findings(crash)
            crash.pop("sec", None)
        return {
            "kpi": {
                "crash_count": len(crashes),
                "reboot_count": sum(1 for c in crashes if c["reboot"]),
                "first_time": crashes[0]["time"] if crashes else None,
                "first_task": (crashes[0]["task_name"] or crashes[0]["task_id"]) if crashes else None,
            },
            "crashes": crashes,
        }

    def save_ui_report(self, output_dir="./result", base_name="", analysis=None):
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{base_name}_rtos_crash.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(analysis or {}, f, indent=4, ensure_ascii=False)
        return out_path

    @staticmethod
    def _collect(records):
        """덤프는 파일 안에서 연달아 찍힌다 → 파일 순서로 읽으며 크래시마다 줄을 모은다."""
        crashes, current = [], None
        stack = None

        def open_crash(rec):
            nonlocal current, stack
            near = (current is not None and current["task_id"] == rec.task
                    and abs(rec.sec - current["sec"]) <= SAME_CRASH_SEC)
            if not near:
                current = _new_crash(rec)
                crashes.append(current)
                stack = None
            return current

        for rec in records:
            lib = LIB_ASSERT_RE.search(rec.msg)
            if lib:
                crash = open_crash(rec)
                if crash["lib_assert"] is None:
                    crash["lib_assert"] = {"expr": lib.group("expr"), "file": lib.group("file"),
                                           "line": int(lib.group("line")), "function": lib.group("func"),
                                           "line_no": rec.line_no, "text": rec.msg}
                crash["last_line"] = rec.line_no
                continue
            if rec.tag not in DUMP_TAGS:
                continue
            body = _body(rec)
            nuttx = NUTTX_ASSERT_RE.match(body) if rec.tag == "dump_assert_info" else None
            if nuttx:
                crash = open_crash(rec)
                crash["nuttx_assert"] = {"message": nuttx.group("msg") or None, "file": nuttx.group("file"),
                                         "line": int(nuttx.group("line")), "line_no": rec.line_no, "text": body}
                crash["task_name"] = nuttx.group("task")
                crash["process"] = nuttx.group("proc")
                crash["entry"] = nuttx.group("entry")
                crash["last_line"] = rec.line_no
                continue
            # 버전 줄은 Assertion failed 줄보다 먼저 찍힌다
            if current is None or (rec.task != current["task_id"] and rec.tag != "sched_dumpstack"):
                if rec.tag == "dump_assert_info" and VERSION_RE.match(body):
                    current = _new_crash(rec)
                    crashes.append(current)
                    stack = None
                else:
                    continue
            crash = current
            crash["last_line"] = max(crash["last_line"], rec.line_no)
            if rec.tag == "dump_assert_info":
                if VERSION_RE.match(body):
                    crash["version"] = VERSION_RE.match(body).group(1).strip()
                elif MODULE_RE.match(body):
                    crash["module"]["bininfo"] = MODULE_RE.match(body).group(1)
                elif MODULE_FIELD_RE.match(body):
                    key, value = MODULE_FIELD_RE.match(body).groups()
                    crash["module"][key] = value
            elif rec.tag == "up_dump_register":
                crash["registers"].update({k: v for k, v in REGISTER_RE.findall(body)})
            elif rec.tag == "dump_stackinfo":
                head = STACK_HEAD_RE.match(body)
                field = STACK_FIELD_RE.match(body)
                if head:
                    stack = {"kind": head.group(1)}
                    crash["stacks"].append(stack)
                elif field and stack is not None:
                    stack[field.group(1)] = field.group(2)
            elif rec.tag == "stack_dump":
                crash["stack_dump"].append(body)
            elif rec.tag == "sched_dumpstack":
                bt = BACKTRACE_RE.match(body)
                if bt:
                    crash["backtraces"].setdefault(int(bt.group(1)), []).extend(ADDRESS_RE.findall(bt.group(2)))
            else:
                crash["task_dump"].append(body)
        return crashes

    @staticmethod
    def _stack_usage(stack):
        """스택은 아래로 자란다: 사용량 = (base + size) - sp. sp 가 base 아래면 넘친 것이다."""
        base, sp = _hex(stack.get("base")), _hex(stack.get("sp"))
        try:
            size = int(stack.get("size"), 10)
        except (TypeError, ValueError):
            size = None
        out = dict(stack)
        if base is not None and sp is not None and size:
            used = base + size - sp
            out["used"] = used
            out["used_pct"] = round(used * 100.0 / size, 1)
            out["overflow"] = sp < base
        return out

    @staticmethod
    def _reboot_after(records, last_line):
        for rec in records:
            if rec.line_no <= last_line:
                continue
            if rec.date.endswith("-00") or rec.tag in BOOT_TAGS:
                return {"line_no": rec.line_no, "time": rec.time, "text": rec.msg}
        return None

    @staticmethod
    def _context(by_time, crash):
        start = crash["sec"] - CONTEXT_WINDOW_SEC
        before = [r for r in by_time if start <= r.sec < crash["sec"]
                  and r.tag not in DUMP_TAGS and not LIB_ASSERT_RE.search(r.msg)]
        task = [r for r in before if r.task == crash["task_id"]][-CONTEXT_TASK_LINES:]
        fmt = lambda r: f"[{r.time}] [{r.task}] {r.msg}  (line {r.line_no})"
        return [fmt(r) for r in task], [fmt(r) for r in before[-CONTEXT_ALL_LINES:]]

    @staticmethod
    def _findings(crash):
        """주소 없이 텍스트만으로 말할 수 있는 것."""
        out = []
        lib, nuttx = crash["lib_assert"], crash["nuttx_assert"]
        if lib and nuttx and os.path.basename(lib["file"]) != os.path.basename(nuttx["file"]):
            out.append(f"실제로 실패한 조건은 {os.path.basename(lib['file'])}:{lib['line']} "
                       f"{lib.get('function') or ''} 의 `{lib['expr']}` 이다. NuttX 가 가리키는 "
                       f"{os.path.basename(nuttx['file'])}:{nuttx['line']} 은 그 실패를 abort 로 넘긴 자리다.")
        expr = (lib or {}).get("expr") or ""
        if "refcount" in expr:
            out.append("참조 카운트가 이미 0 인 객체를 다시 해제하려 했다 — 같은 객체를 두 번 unref 했거나 "
                       "이미 해제된 객체를 쓴 것(use-after-free)으로 의심된다.")
        for s in crash["stacks"]:
            if s.get("overflow"):
                out.append(f"{s['kind']} 스택이 넘쳤다 (sp {s.get('sp')} < base {s.get('base')}).")
            elif s.get("used_pct") is not None and s["used_pct"] >= 90:
                out.append(f"{s['kind']} 스택을 {s['used_pct']}% 썼다 — 넘치기 직전이다.")
        if crash["reboot"]:
            out.append(f"덤프 뒤 재부팅 로그가 이어진다 (line {crash['reboot']['line_no']}).")
        return out
