import re
from parsers.base import BaseParser

class NativeCrashParser(BaseParser):
    def __init__(self, context_getter=None):
        super().__init__(context_getter)

    def analyze(self, lines):
        if not lines:
            return []

        crash_list = []
        current_crash = None

        for line in lines:
            # 1. Fatal signal 라인 감지 및 시간 추출
            if "Fatal signal" in line:
                # 만약 이전 크래시를 수집 중이었다면 리스트에 확정(Append)하고 새로 시작
                if current_crash:
                    crash_list.append(current_crash)

                # 라인 맨 앞의 타임스탬프 추출 (예: 04-13 16:25:50.443)
                ts_match = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3})', line.strip())
                timestamp = ts_match.group(1) if ts_match else "Unknown Time"

                # 프로세스 및 시그널 정보 추출
                signal_pattern = re.search(r'Fatal signal (\d+) \((.*?)\).*?pid (\d+) \((.*?)\)', line)

                current_crash = {
                    "time": timestamp, # ✨ 드디어 정상적인 시간이 들어갑니다!
                    "crash_type": "NATIVE_CRASH",
                    "process": signal_pattern.group(4) if signal_pattern else "unknown",
                    "signal": signal_pattern.group(2) if signal_pattern else "unknown",
                    "abort_message": "none",
                    "callstack": []
                }

                # Time-Window Glue 로직 연동 (크래시 주변 로그 확보)
                if self.get_context_fn and timestamp != "Unknown Time":
                    current_crash['cross_context_logs'] = self.get_context_fn(
                        lines, timestamp, window_seconds=2, max_lines=50
                    )

            # 2. current_crash가 활성화된 상태(크래시 블록 내부)에서 부가 정보 수집
            if current_crash:
                if "Abort message:" in line:
                    abort_pattern = re.search(r'Abort message: \'(.*?)\'', line)
                    if abort_pattern:
                        current_crash["abort_message"] = abort_pattern.group(1)

                # 콜스택 프레임 수집 (토큰 최적화를 위해 최대 15개까지만 제한)
                if " pc " in line:
                    frame_pattern = re.search(r'#(\d{2})\s+pc\s+[0-9a-fA-F]+\s+([^\s]+)\s*\((.*?)\)', line)
                    if frame_pattern and len(current_crash["callstack"]) < 15:
                        library_name = frame_pattern.group(2).split('/')[-1] # 경로 제거
                        function_info = frame_pattern.group(3).split('+')[0].strip() # 오프셋 제거

                        current_crash["callstack"].append({
                            "frame_level": frame_pattern.group(1),
                            "library": library_name,
                            "function": function_info
                        })

        # 3. 루프가 끝난 후 마지막으로 수집 중이던 크래시가 있다면 추가
        if current_crash:
            crash_list.append(current_crash)

        return crash_list
