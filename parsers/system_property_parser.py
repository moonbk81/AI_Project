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

# 설정값은 getprop 이 아니라 다른 섹션에, 다른 모양으로 찍힌다:
#
#   SettingsHelper state:
#       ...
#       mobile_data_question = 0
#       ...
#       mobile_data = 0
#
# 이 섹션은 수백 줄이라 통째로 담지 않고 필요한 것만 고른다. 그리고
# `mobile_data_question` 처럼 이름이 앞부분을 공유하는 이웃이 실제로 있으므로
# 키는 정확히 끊어서 봐야 한다.
_SETTINGS_SECTION_START = "SettingsHelper state:"
_SETTING_LINE = re.compile(r'^([A-Za-z0-9_.]+)\s*=\s*(.*)$')

# 담을 설정과 그 뜻. 값은 로그에 찍힌 그대로 두고, 읽는 쪽에서 풀어 쓴다.
WANTED_SETTINGS = ("mobile_data",)


class SystemPropertyParser(BaseParser):
    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.properties = {}
        # 수집할 타겟 접두사 정의
        self.target_prefixes = ("ril.", "persist.radio.", "gsm.")

    def analyze(self, lines):
        is_prop_section = False  # 프로퍼티 구간 진입 여부를 알리는 플래그
        in_settings = False

        for line in lines:
            clean_line = line.strip()

            # 설정 구간은 프로퍼티 구간과 별개로 흐른다. dumpstate 는 섹션을
            # 뒤섞어 내보내므로 둘을 한 플래그로 묶지 않는다.
            if _SETTINGS_SECTION_START in clean_line:
                in_settings = True
                continue
            if in_settings:
                setting = _SETTING_LINE.match(clean_line)
                if setting:
                    key = setting.group(1)
                    if key in WANTED_SETTINGS:
                        self.properties[key] = setting.group(2).strip()
                elif clean_line.startswith("---") or (clean_line and clean_line.endswith(":")):
                    # 다음 섹션이 시작됐다. 빈 줄은 구간 안에서도 나오므로 끊지 않는다.
                    in_settings = False

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
