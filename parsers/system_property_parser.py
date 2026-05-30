import re
from parsers.base import BaseParser

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

            # 1. 헤더 감지: 프로퍼티 구간 시작
            if "------ SYSTEM PROPERTIES" in clean_line:
                is_prop_section = True
                continue

            # 2. 다른 로그 섹션(예: ------ SYSTEM LOG ------)으로 넘어가면 수집 중단
            # (만약 덤프스테이트 파일이 이어져 있다면 False로 바꾸고 대기합니다)
            if is_prop_section and clean_line.startswith("------"):
                is_prop_section = False
                continue

            # 3. 프로퍼티 구간 내부일 때만 정규식 파싱 수행
            if is_prop_section:
                # [key]: [value] 형태 추출 (유연한 search 사용)
                match = re.search(r'\[(.*?)\]:\s*\[(.*?)\]', clean_line)

                if match:
                    key = match.group(1).strip()
                    val = match.group(2).strip()

                    # 원하는 통신 관련 프로퍼티만 쏙쏙 필터링
                    if key.startswith(self.target_prefixes):
                        self.properties[key] = val

        return self.properties