import pandas as pd

class BootStatAnalyzer:
    def __init__(self, metas):
        # 1. 텍스트 파싱 삭제! DB에서 올라온 메타데이터 중 Boot_Stat만 쏙 골라냅니다.
        valid_metas = [m for m in metas if m.get('log_type') == 'Boot_Stat']

        # 2. 곧바로 데이터프레임으로 변환
        self.df = pd.DataFrame(valid_metas)

        if not self.df.empty:
            # 안전하게 숫자형으로 형변환 후 시간순 정렬
            self.df['Time_ms'] = pd.to_numeric(self.df['Time_ms'], errors='coerce')
            self.df['Delta_ms'] = pd.to_numeric(self.df['Delta_ms'], errors='coerce')
            self.df = self.df.sort_values("Time_ms")

    def get_summary(self):
        if self.df.empty:
            return None

        # 헬퍼 함수: 특정 이벤트의 Time_ms를 가져옴
        def get_t(kw):
            row = self.df[self.df['Event'].str.contains(kw, case=False, na=False)]
            return int(row.iloc[0]['Time_ms']) if not row.empty else None

        start_t = get_t('Bootloader start')
        v_ready = get_t('Voice SVC is acquired')
        d_ready = get_t('Data SVC is acquired')

        return {
            "boot_complete": get_t('bootcomplete'),
            "total_voice_ms": (v_ready - start_t) if start_t and v_ready else None,
            "total_data_ms": (d_ready - start_t) if start_t and d_ready else None
        }