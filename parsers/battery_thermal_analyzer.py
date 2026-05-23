import re
from parsers.base import BaseParser

class BatteryThermalAnalyzer(BaseParser):
    """발열(Thermal) 센서 및 배터리 광탈 주범(Wakelock) 분석기"""
    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.re_thermal_new = re.compile(r'Temperature\{mValue=([-\d.]+).*?mName=([a-zA-Z0-9_]+)', re.I)
        self.re_thermal_old = re.compile(r'Temperature:\s*(\d+).*?Sensor:\s*([a-zA-Z0-9_]+)', re.I)
        # 앱 이름 부분에 u0a123, 10123, *alarm*, 콜론(:) 등이 올 수 있도록 정규식 확장
        self.re_wakelock = re.compile(r'Wake lock\s+([a-zA-Z0-9_.*:-]+).*?(\d+)\s*ms.*?(\d+)\s*times', re.I)

        # 패키지명 매핑용 정규식
        self.re_app_id = re.compile(r'App ID:\s*(\d+)', re.I)
        self.re_package = re.compile(r'Package:\s*([a-zA-Z0-9_.]+)', re.I)

        # 성능 최적화: 전체 dump를 받더라도 관심 없는 라인은 clean_line/regex 검사 자체를 건너뜁니다.
        self.uid_marker_keywords = ("App ID:", "Package:")
        self.thermal_marker_keywords = ("Temperature", "CurrentValue")
        self.wakelock_marker_keywords = ("Wake lock",)

    def analyze(self, lines):
        thermals = {}
        wakelocks = []
        uid_map = {}
        current_app_id = None

        for line in lines:
            raw_line = str(line)

            # 대부분의 dump 라인은 배터리/써멀 분석과 무관하므로 빠르게 skip합니다.
            has_uid_marker = any(marker in raw_line for marker in self.uid_marker_keywords)
            has_thermal_marker = any(marker in raw_line for marker in self.thermal_marker_keywords)
            has_wakelock_marker = any(marker in raw_line for marker in self.wakelock_marker_keywords)

            if not (has_uid_marker or has_thermal_marker or has_wakelock_marker):
                continue

            clean_line = self.clean_line(raw_line)
            # ==========================================
            # 1. 패키지명 <-> UID(App ID) 매핑 수집
            # ==========================================
            if has_uid_marker and "App ID:" in clean_line:
                m_app_id = self.re_app_id.search(clean_line)
                if m_app_id:
                    current_app_id = m_app_id.group(1)

            if has_uid_marker and "Package:" in clean_line and current_app_id:
                m_package = self.re_package.search(clean_line)
                if m_package:
                    uid_map[current_app_id] = m_package.group(1)
                    current_app_id = None # 매핑 후 상태 초기화 (오탐 방지)

            # ==========================================
            # 2. 써멀(발열) 분석
            # ==========================================
            if has_thermal_marker:

                # 1순위: 신형 포맷 매칭 시도
                m_new = self.re_thermal_new.search(clean_line)
                if m_new:
                    sensor_name = m_new.group(2)
                    thermals[sensor_name] = float(m_new.group(1))
                    continue # 매칭 성공 시 구형 포맷 검사는 스킵

                # 2순위: 구형 포맷 매칭 시도
                m_old = self.re_thermal_old.search(clean_line)
                if m_old:
                    sensor_name = m_old.group(2)
                    thermals[sensor_name] = float(m_old.group(1))

            # ==========================================
            # 3. 웨이크락(배터리 점유) 분석 및 UID 매핑
            # ==========================================
            if has_wakelock_marker:
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

        thermals = [{"sensor": k, "temperature": v} for k, v in thermals.items()]
        return {
            "thermal_stats": thermals,
            "wakelock_stats": wakelocks
        }

class CpuUsageParser(BaseParser):
    def analyze(self, lines):
        cpu_stats = []
        in_cpu_block = False

        for line in lines:
            line_str = line.strip()

            # 1. 블록 시작 감지 (더 넓은 키워드 적용)
            if "CPU usage from" in line_str or "dumpsys cpuinfo" in line_str:
                in_cpu_block = True
                continue

            if in_cpu_block:
                # 2. 블록 종료 조건 (빈 줄이거나 TOTAL/--- 라인)
                if not line_str or line_str.startswith("TOTAL") or line_str.startswith("---"):
                    if len(cpu_stats) > 0:
                        break
                    continue

                # 3. 1차 시도: 정규식 추출 (PID 유무 상관없이 모두 캡처)
                # 패턴 매칭 예: "66% 1404/system_server:"
                m = re.search(r'^([0-9.]+)%\s+(?:[A-Za-z0-9_-]+)?(?:[0-9]+/)?([^:]+):', line_str)
                pct, proc = None, None

                if m:
                    pct = float(m.group(1))
                    proc = m.group(2).strip()
                else:
                    # 4. 2차 시도: 정규식이 실패하면 Split으로 강제 추출 (방어 로직)
                    try:
                        if '%' in line_str and ':' in line_str:
                            parts = line_str.split('%', 1)
                            pct = float(parts[0].strip())
                            proc_part = parts[1].split(':')[0].strip()
                            proc = proc_part.split('/')[-1] if '/' in proc_part else proc_part
                    except:
                        pass

                # 5. 데이터 적재 (0.5% 이상 점유한 프로세스만 최대 10개 수집)
                if pct is not None and proc and pct >= 0.5:
                    cpu_stats.append({
                        "process": proc,
                        "cpu_percent": pct
                    })

                    if len(cpu_stats) >= 10:
                        break

        # 점유율(%) 기준 내림차순 정렬하여 반환
        return sorted(cpu_stats, key=lambda x: x['cpu_percent'], reverse=True)