import re
from parsers.base import BaseParser

_SECTION_START = "------ SYSTEM PROPERTIES"

# dumpstate 는 명령을 동시에 돌리기 때문에, 다른 섹션의 "------ ..." 줄이
# 프로퍼티 본문 한가운데로 섞여 들어온다. 실제로 아래처럼 나온다:
#
#   ------ SYSTEM PROPERTIES (getprop) ------
#   ------ 0.019s was the duration of 'mount debugfs' ------   <- 남의 줄
#   ------ chmod debugfs (...) ------                          <- 남의 줄
#   [aaudio.hw_burst_min_usec]: [2000]                         <- 여기서부터 본문
#   ...
#   ------ 0.014s was the duration of 'chmod debugfs' ------   <- 본문 한가운데
#   ...
#   ------ 0.050s was the duration of 'SYSTEM PROPERTIES' ------  <- 진짜 끝
#
# 그래서 아무 "------" 줄에서나 끊으면 첫 줄에서 바로 빠져나와 아무것도 못 읽는다.
# 믿을 수 있는 경계는 이 섹션 자신의 종료 표시뿐이다.
_SECTION_END = re.compile(r"duration of 'SYSTEM PROPERTIES'")

# getprop 출력 한 줄: [key]: [value]
_PROPERTY = re.compile(r'\[(.*?)\]:\s*\[(.*?)\]')


class SystemPropertyParser(BaseParser):
    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.properties = {}
        # 수집할 타겟 접두사 정의
        self.target_prefixes = ("ril.", "persist.radio.", "gsm.")

    def analyze(self, lines):
        is_prop_section = False  # 프로퍼티 구간 진입 여부를 알리는 플래그

        for line in lines:
            clean_line = line.strip()

            # 1. 구간 밖에서는 시작 헤더만 찾는다.
            if not is_prop_section:
                if _SECTION_START in clean_line:
                    is_prop_section = True
                continue

            # 2. 이 섹션 자신의 종료 표시에서만 빠져나온다.
            #    (여러 로그를 합친 파일이면 뒤에 또 나올 수 있어 break 하지 않는다)
            if _SECTION_END.search(clean_line):
                is_prop_section = False
                continue

            # 3. 구간 내부에서 [key]: [value] 추출
            match = _PROPERTY.search(clean_line)
            if match:
                key = match.group(1).strip()
                val = match.group(2).strip()

                # 원하는 통신 관련 프로퍼티만 쏙쏙 필터링
                if key.startswith(self.target_prefixes):
                    self.properties[key] = val

        return self.properties
