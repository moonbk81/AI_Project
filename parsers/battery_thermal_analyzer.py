import re
from parsers.base import BaseParser

class BatteryThermalAnalyzer(BaseParser):
    """발열(Thermal) 센서 및 배터리 광탈 주범(Wakelock) 분석기"""
    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.re_thermal = re.compile(r'Temperature:\s*(\d+).*?Sensor:\s*([a-zA-Z0-9_]+)', re.I)
        # 앱 이름 부분에 u0a123, 10123, *alarm*, 콜론(:) 등이 올 수 있도록 정규식 확장
        self.re_wakelock = re.compile(r'Wake lock\s+([a-zA-Z0-9_.*:-]+).*?(\d+)\s*ms.*?(\d+)\s*times', re.I)

        # 패키지명 매핑용 정규식
        self.re_app_id = re.compile(r'App ID:\s*(\d+)', re.I)
        self.re_package = re.compile(r'Package:\s*([a-zA-Z0-9_.]+)', re.I)

    def analyze(self, lines):
        thermals = []
        wakelocks = []
        uid_map = {}
        current_app_id = None

        for line in lines:
            clean_line = self.clean_line(line)

            # ==========================================
            # 1. 패키지명 <-> UID(App ID) 매핑 수집
            # ==========================================
            if "App ID:" in clean_line:
                m_app_id = self.re_app_id.search(clean_line)
                if m_app_id:
                    current_app_id = m_app_id.group(1)

            if "Package:" in clean_line and current_app_id:
                m_package = self.re_package.search(clean_line)
                if m_package:
                    uid_map[current_app_id] = m_package.group(1)
                    current_app_id = None # 매핑 후 상태 초기화 (오탐 방지)

            # ==========================================
            # 2. 써멀(발열) 분석
            # ==========================================
            if "Temperature" in clean_line or "CurrentValue" in clean_line:
                m = self.re_thermal.search(clean_line)
                if m:
                    thermals.append({
                        "sensor": m.group(2),
                        "temperature": float(m.group(1))
                    })

            # ==========================================
            # 3. 웨이크락(배터리 점유) 분석 및 UID 매핑
            # ==========================================
            if "Wake lock" in clean_line or "times" in clean_line:
                m = self.re_wakelock.search(clean_line)
                if m:
                    raw_app = m.group(1) # 예: '10123' 또는 'u0a123' 또는 '*alarm*'
                    mapped_app = raw_app

                    # [디테일] 안드로이드 batterystats는 'u0a123' 포맷을 자주 사용함
                    if raw_app.startswith('u0a'):
                        try:
                            # u0a123 -> App ID 10123 변환
                            num = int(raw_app[3:])
                            app_id_str = str(10000 + num)
                            mapped_app = uid_map.get(app_id_str, raw_app)
                        except ValueError:
                            pass
                    else:
                        # 10123 처럼 순수 숫자로 올 경우
                        # 콜론(:)이 섞여있을 경우(예: 10123:WLAN) 숫자만 추출해서 매핑
                        pure_id_match = re.match(r'^(\d+)', raw_app)
                        if pure_id_match:
                            extracted_id = pure_id_match.group(1)
                            # 매핑된 패키지명이 있으면 패키지명으로, 없으면 원본 유지
                            mapped_app = raw_app.replace(extracted_id, uid_map.get(extracted_id, extracted_id))
                        else:
                            mapped_app = uid_map.get(raw_app, raw_app)

                    wakelocks.append({
                        "app_name": mapped_app,
                        "duration": f"{m.group(2)} ms",
                        "times": int(m.group(3))
                    })

        return {
            "thermal_stats": thermals,
            "wakelock_stats": wakelocks
        }