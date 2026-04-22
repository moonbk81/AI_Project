import re

class BatteryThermalAnalyzer:
    def __init__(self):
        # 1. Thermal(온도) 정규식
        self.re_temp = re.compile(r'Temperature\{mValue=([0-9.-]+).*?mName=([^,]+)', re.I)
        self.re_hal = re.compile(r'Name:\s*([^ ]+)\s+Type:.*?CurrentValue:\s*([0-9.-]+)', re.I)

        # 2. Wakelock (앱 매핑 및 배터리 점유) 정규식
        self.re_app_id = re.compile(r'App ID:\s*(\d+)')
        self.re_package = re.compile(r'Package:\s*([a-zA-Z0-9_.]+)')
        self.re_wl = re.compile(r'Wake lock\s+([a-zA-Z0-9_]+)\s+([^:]+):\s+(.*?)\s+\((\d+)\s+times\)', re.I)

    def analyze_thermals(self, lines):
        thermals = {}
        for line in lines:
            if "Temperature" in line or "CurrentValue" in line:
                m1 = self.re_temp.search(line)
                if m1:
                    val, name = float(m1.group(1)), m1.group(2).strip()
                    if 0 < val < 100: thermals[name] = val
                    continue

                m2 = self.re_hal.search(line)
                if m2:
                    name, val = m2.group(1).strip(), float(m2.group(2))
                    if 0 < val < 100: thermals[name] = val

        return [{"sensor": k, "temperature": v} for k, v in thermals.items()]

    def analyze_wakelocks(self, lines):
        wakelocks = []
        uid_map = {}
        current_app_id = None

        for line in lines:
            line_stripped = line.strip()

            # UID -> 패키지명 매핑 (호적등본 방식)
            m_app_id = self.re_app_id.search(line_stripped)
            if m_app_id:
                current_app_id = m_app_id.group(1)

            m_package = self.re_package.search(line_stripped)
            if m_package and current_app_id:
                uid_map[current_app_id] = m_package.group(1)
                current_app_id = None

            # Wakelock 추출
            if "Wake lock" in line_stripped and "realtime" in line_stripped:
                m = self.re_wl.search(line_stripped)
                if m:
                    uid_raw = m.group(1)
                    tag = m.group(2).strip()
                    duration = m.group(3).strip()
                    times = m.group(4)

                    # u0a151 같은 포맷을 UID 10151 로 치환
                    uid = str(10000 + int(uid_raw[3:])) if uid_raw.startswith("u0a") else uid_raw

                    app_name = uid_map.get(uid, f"App_UID_{uid}")
                    if uid == "1000": app_name = "Android System (OS)"
                    elif uid == "0": app_name = "Kernel (Root)"

                    wakelocks.append({
                        "uid": uid,
                        "app_name": app_name,
                        "tag": tag,
                        "duration": duration,
                        "times": int(times)
                    })

        # 문자열 시간을 초(Sec)로 변환하는 헬퍼 함수
        def parse_sec(d_str):
            sec = 0
            if h := re.search(r'(\d+)h', d_str): sec += int(h.group(1)) * 3600
            if m := re.search(r'(\d+)m\b', d_str): sec += int(m.group(1)) * 60
            if s := re.search(r'(\d+)s', d_str): sec += int(s.group(1))
            return sec

        # 최악의 Wakelock Top 10만 반환
        wakelocks.sort(key=lambda x: parse_sec(x["duration"]), reverse=True)
        return wakelocks[:10]
