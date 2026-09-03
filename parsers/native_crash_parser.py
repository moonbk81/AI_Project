import os
import re
from parsers.base import BaseParser


class NativeCrashParser(BaseParser):
    """네이티브 크래시를 로그에서 뽑아낸다.

    같은 크래시를 두 곳이 기록한다. libc 가 남기는 ``Fatal signal`` 한 줄과,
    debuggerd 가 그 뒤에 찍는 tombstone 블록(``F DEBUG :``)이다. 한 줄짜리는
    시그널과 pid 만 알려주고, 정작 왜 죽었는지(abort 메시지)와 어디서 죽었는지
    (백트레이스)는 tombstone 에만 있다.

    그런데 둘이 늘 함께 오지는 않는다. dumpstate 안에 담겨 오거나 버퍼가 잘리면
    tombstone 만 남는 경우가 있고, 그때 ``Fatal signal`` 만 찾으면 크래시 자체를
    통째로 놓친다. 그러면 남는 건 그 프로세스가 죽어서 생긴 binder 실패들뿐이라,
    화면에는 "후속 증상만 있고 원인은 모름" 으로 보인다.
    """

    # debuggerd 가 tombstone 을 시작하며 찍는 구분선.
    TOMBSTONE_START = "*** *** ***"

    # `signal 6 (SIGABRT), code -1 (SI_QUEUE), fault addr --------`
    # `Fatal signal 6 (SIGABRT), code -1 ...` 도 같은 모양이라 함께 걸린다.
    RE_SIGNAL = re.compile(r'\bsignal\s+(\d+)\s+\((SIG[^)]*)\)')
    # `pid: 10647, ppid: 1085, tid: 10663, name: ReferenceQueueD  >>> com.android.phone <<<`
    RE_PID_LINE = re.compile(r'\bpid:\s*(\d+).*?\btid:\s*(\d+),\s*name:\s*(\S+)')
    # 프로세스 이름은 `>>> ... <<<` 안이 가장 정확하다. Fatal signal 줄의 괄호
    # 이름은 15자에서 잘려(`ndroid.phone`) 패키지명으로 쓸 수 없다.
    RE_TOMBSTONE_PROCESS = re.compile(r'>>>\s*(\S+)\s*<<<')
    RE_CMDLINE = re.compile(r'\bCmdline:\s*(\S+)')
    RE_TIME = re.compile(r'((?:\d{4}-)?\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}(?:\.\d{3,6})?)')
    RE_FATAL_SIGNAL = re.compile(r'Fatal signal (\d+) \((.*?)\).*?pid (\d+) \((.*?)\)')
    # 프레임 한 줄. 함수 이름 뒤 괄호까지 통째로 잡아야 한다 -- C++ 시그니처에는
    # 괄호가 또 들어 있어서(`reportHeaderCorruption(void*, void const*)+188`)
    # 첫 닫는 괄호에서 끊으면 이름이 잘린 채 나온다.
    RE_FRAME = re.compile(r'#(\d{2})\s+pc\s+[0-9a-fA-F]+\s+(\S+)\s+\((.*)\)\s*$')
    # 끝에 붙는 `(BuildId: ...)` 는 프레임 정보가 아니라 잘라내고 본다.
    RE_BUILD_ID = re.compile(r'\s*\(BuildId:\s*[^)]*\)\s*$')

    MAX_FRAMES = 15

    def __init__(self, context_getter=None):
        super().__init__(context_getter)

    def _new_crash(self, lines, timestamp):
        crash = {
            "time": timestamp,
            "timestamp": timestamp,
            "crash_type": "NATIVE_CRASH",
            "process": "unknown",
            "pid": "",
            "thread": "",
            "signal": "unknown",
            "abort_message": "none",
            "callstack": [],
            # 프로세스/시그널/abort 는 tombstone 머리에만 있다. `backtrace:` 를
            # 지나면 그만 읽는다 -- 블록이 어디서 끝나는지 로그가 알려주지 않아,
            # 계속 읽으면 뒤따르는 남의 줄이 필드를 덮어쓴다. 실제 dumpstate 에서
            # 프로세스가 com.android.phone 대신 한참 뒤의 다른 이름으로 나왔다.
            "_header": True,
        }
        # Time-Window Glue 로직 연동 (크래시 주변 로그 확보)
        if self.get_context_fn and timestamp != "Unknown Time":
            crash["cross_context_logs"] = self.get_context_fn(
                lines, timestamp, window_seconds=2, max_lines=50
            )
        return crash

    def _timestamp_of(self, line):
        match = self.RE_TIME.search(line)
        return match.group(1) if match else "Unknown Time"

    def analyze(self, lines):
        if not lines:
            return []

        crash_list = []
        current_crash = None

        for line in lines:
            if "Fatal signal" in line:
                if current_crash:
                    crash_list.append(current_crash)
                current_crash = self._new_crash(lines, self._timestamp_of(line))

                signal_pattern = self.RE_FATAL_SIGNAL.search(line)
                if signal_pattern:
                    current_crash["signal"] = signal_pattern.group(2)
                    current_crash["pid"] = signal_pattern.group(3)
                    current_crash["process"] = signal_pattern.group(4)

            elif self.TOMBSTONE_START in line and "DEBUG" in line:
                # tombstone 머리. 바로 앞의 Fatal signal 로 열린 크래시라면 같은
                # 사건이므로 이어서 채운다(그 줄에는 백트레이스가 없다). 이미
                # 프레임을 모은 크래시가 열려 있으면 다음 tombstone 이 시작된 것이다.
                if current_crash and current_crash["callstack"]:
                    crash_list.append(current_crash)
                    current_crash = None
                if current_crash is None:
                    current_crash = self._new_crash(lines, self._timestamp_of(line))

            if not current_crash:
                continue

            if "backtrace:" in line:
                current_crash["_header"] = False

            if current_crash["_header"] and "Abort message:" in line:
                abort_pattern = re.search(r'Abort message: \'(.*?)\'', line)
                if abort_pattern:
                    current_crash["abort_message"] = abort_pattern.group(1)

            if current_crash["_header"] and current_crash["signal"] == "unknown":
                signal_match = self.RE_SIGNAL.search(line)
                if signal_match:
                    current_crash["signal"] = signal_match.group(2)

            pid_match = self.RE_PID_LINE.search(line) if current_crash["_header"] else None
            if pid_match:
                current_crash["pid"] = pid_match.group(1)
                current_crash["thread"] = pid_match.group(3)

            # `>>> 이름 <<<` 이 가장 정확하고, 없으면 Cmdline 으로 채운다.
            process_match = None
            if current_crash["_header"]:
                process_match = self.RE_TOMBSTONE_PROCESS.search(line) or self.RE_CMDLINE.search(line)
            if process_match:
                # /system/bin/rild 처럼 경로로 적히기도 한다. 이름만 남긴다.
                current_crash["process"] = os.path.basename(process_match.group(1))

            if " pc " in line:
                frame_pattern = self.RE_FRAME.search(self.RE_BUILD_ID.sub("", line.rstrip()))
                if frame_pattern and len(current_crash["callstack"]) < self.MAX_FRAMES:
                    library_name = frame_pattern.group(2).split('/')[-1]
                    function_info = frame_pattern.group(3).split('+')[0].strip()

                    current_crash["callstack"].append({
                        "frame_level": frame_pattern.group(1),
                        "library": library_name,
                        "function": function_info,
                    })

        if current_crash:
            crash_list.append(current_crash)

        for crash in crash_list:
            crash.pop("_header", None)
        return crash_list
