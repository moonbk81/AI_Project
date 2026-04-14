import re
import os
import json
import argparse
from collections import defaultdict

class NetworkTimeSeriesAnalyzer:
    def __init__(self, file_path):
        self.file_path = file_path

        self.stats_start = re.compile(r'network statistics:', re.I)
        self.stats_end = re.compile(r'packet wakeup events:', re.I)
        # 1. 상세 DNS 차단 로그 패턴 (isBlocked)
        self.re_time = re.compile(r'\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}')
        self.re_tag = re.compile(r'[VDIWE]\s+([a-zA-Z0-9_\-]+)\s*(?=:)', re.I)
        self.re_dns_event = re.compile(r'DNS\s+requested\s+by\s+(\d+),\s+\d+\((.*?)\),\s+(\d+)\((.*?)\),\s+isBlocked=(\w+)', re.I)

        # 2. NetId별 성능 통계 패턴 (NetStats)
        self.re_net_perf = re.compile(
            r'(?:^|,\s*)'  # 시작 또는 콤마로 구분
            r'(?:((?:\d{2}:){2}\d{2}\.\d{3}):\s*)?'  # 시간 (선택적)
            r'\{netId=(\d+),\s*transports=\{(.*?)\},\s*'
            r'dns\s+avg=(\d+)ms\s+max=(\d+)ms\s+err=(\d+(?:\.\d+)?)%\s+tot=(\d+),\s*'
            r'delayed\s+rsp=(\d+),\s*blocked\s+rsp=(\d+),\s*'
            r'connect\s+avg=(\d+)ms\s+max=(\d+)ms\s+err=(\d+(?:\.\d+)?)%\s+tot=(\d+),\s*'
            r'tcp\s+avg_loss=(\d+(?:\.\d+)?)%', re.I
        )

        # 3. 차단 원인 추적 설정값
        self.private_dns_mode = re.compile(r'private_dns_mode\s*=\s*([^\s,]+)', re.I)
        self.data_saver = re.compile(r'mRestrictBackground\d+\s*:\s*(\w+)', re.I)
        self.vpn_active = re.compile(r'NetworkAgentInfo.*\[.*VPN.*\]\s+connected', re.I)

    def analyze(self):
        in_stats = False
        dns_issues = []
        device_config = {"private_dns": "off", "data_saver": "off", "vpn": "inactive"}
        # 시계열 분석을 위해 시간(Time)을 키로 사용하는 딕셔너리
        timeline = defaultdict(lambda: {"net_stats": []})

        with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                clean_line = line.strip()

                # NetdEventListenerService tag
                tag_m = self.re_tag.search(line)
                tag = tag_m.group(1).strip() if tag_m else None
                if tag == "NetdEventListenerService":
                    dns_m = self.re_dns_event.search(clean_line)
                    if dns_m:
                        uid, pkg, res_code, res_str, blocked = dns_m.groups()
                        if "FAIL" or "NODATA" in res_str or blocked.lower() == 'true':
                            # 차단 원인 추정
                            reason = "Network Issue (Timeout/Fail)"
                            if blocked.lower() == 'true':
                                if device_config["private_dns"] != "off": reason = f"Blocked by Private DNS ({device_config['private_dns']})"
                                elif device_config["data_saver"] == "on": reason = "Blocked by Data Saver"
                                elif device_config["vpn"] == "active": reason = "Blocked by VPN/Firewall"
                                else: reason = "Blocked by System Policy"

                            dns_issues.append({
                                "time": line[:18],
                                "package": pkg,
                                "result": res_str,
                                "is_blocked": blocked.lower() == 'true',
                                "suspected_reason": reason
                            })

                if self.stats_start.search(clean_line):
                    in_stats = True; continue
                if in_stats:
                    # [B] NetId별 성능 통계 추출
                    perf_m = self.re_net_perf.search(clean_line)
                    if perf_m:
                        groups = perf_m.groups()
                        ts, net_id = groups[0], groups[1]
                        timeline[ts]["net_stats"].append({
                            "netId": net_id,
                            "transport": "Wi-Fi" if groups[2] == "1" else "Cellular",
                            "dns_avg": int(groups[3]),
                            "dns_max": int(groups[4]),
                            "dns_err_rate": float(groups[5]),
                            "dns_tot": int(groups[6]),
                            "dns_delayed rsp": int(groups[7]),
                            "dns_blocked_cnt": int(groups[8]),
                            "conn_avg": int(groups[9]),
                            "conn_err_rate": float(groups[11]),
                            "conn_tot": int(groups[12]),
                            "tcp_avg_loss": float(groups[13])
                        })
                if self.stats_end.search(clean_line):
                    in_stats = False
        # 시간을 기준으로 정렬하여 최종 리포트 생성
        sorted_timeline = dict(sorted(timeline.items()))
        return {
            "sorted_timeline": sorted_timeline,
            "dns_issues":dns_issues,
            "device_config": device_config,
        }

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("file")
    args = p.parse_args()

    analyzer = NetworkTimeSeriesAnalyzer(args.file)
    result = analyzer.analyze()

    # 분석 결과 저장
    with open("network_timeseries_report.json", "w", encoding="utf-8") as j:
        json.dump(result, j, indent=4, ensure_ascii=False)

    print(f"✅ 시계열 분석 완료! 총 {len(result)}개의 타임라인 포인트 확보.")
