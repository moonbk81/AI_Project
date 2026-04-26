from abc import ABC, abstractmethod

class BaseParser(ABC):
    """
    모든 로그 파서가 반드시 따라야 하는 추상 베이스 클래스 (인터페이스)
    이 클래스를 상속받는 파서는 무조건 analyze() 메서드를 구현해야 합니다.
    """

    def __init__(self, context_getter=None):
        # Time-Window Glue 로직 등 공통으로 필요한 의존성을 기본으로 세팅합니다.
        self.get_context_fn = context_getter

    @abstractmethod
    def analyze(self, lines):
        """
        [필수 구현] 각 파서의 메인 분석 로직
        :param lines: Orchestrator가 넘겨준 로그 라인 리스트 (또는 버킷)
        :return: 파싱된 결과 데이터 (dict 또는 list)
        """
        pass

    # ==========================================
    # 🛠️ 공통 유틸리티 (모든 하위 파서가 그냥 가져다 쓸 수 있는 함수들)
    # ==========================================
    def clean_line(self, line):
        """기본적인 줄바꿈 및 공백 제거"""
        return line.replace('\r', '').replace('\n', '').strip()

    def safe_to_int(self, value, default=0):
        """안전한 형변환 (에러 방지)"""
        try:
            return int(value)
        except (ValueError, TypeError):
            return default