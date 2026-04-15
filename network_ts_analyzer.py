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
        self.re_dns_event = re.compile(r'DNS\s+requested\s+by\s+(\d+),\s+(\d+)\((.*?)\),\s+(\d+)\((.*?)\),\s+isBlocked=(\w+)', re.I)
        # [신규] UID별 차단 상태 상세 패턴
        # self.re_uid_state = re.compile(
        #     r'UID=(?P<uid>\d+)\s+state=.*\s+blocked_state=\{blocked=(?P<blocked>[^|]*),\s*allowed=.*effective=(?P<effective>[^|]*)\}',
        #     re.I
        # )
        self.re_uid_state = re.compile(
            r'UID=(?P<uid>\d+).*?blocked_state=\{.*?effective=(?P<effective>[^}]+)\}',
            re.I
        )

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
        uid_block_map = {} # UID별 상세 차단 원인 저장소
        device_config = {"private_dns": "off", "data_saver": "off", "vpn": "inactive"}
        # 시계열 분석을 위해 시간(Time)을 키로 사용하는 딕셔너리
        timeline = defaultdict(lambda: {"net_stats": []})

        with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

            # 1단계: UID별 blocked_state 정보 사전 수집
            for line in lines:
                uid_m = self.re_uid_state.search(line)
                if uid_m:
                    uid_block_map[uid_m.group('uid')] = uid_m.group('effective')

            # 2단계: 메인 분석 루프
            for line in lines:
                clean_line = line.strip()
                tag_m = self.re_tag.search(line)
                tag = tag_m.group(1).strip() if tag_m else None

                if tag == "NetdEventListenerService":
                    dns_m = self.re_dns_event.search(clean_line)
                    if dns_m:
                        net_id, uid, pkg, res_code, res_str, blocked = dns_m.groups()
                        if "FAIL" in res_str or "NODATA" in res_str or blocked.lower() == 'true':
                            # 수집된 UID 상태 매핑
                            effective_policy = uid_block_map.get(uid, "SYSTEM_POLICY")
                            dns_issues.append({
                                "time": line[:18],
                                "net_id": net_id,
                                "uid": uid,
                                "package": pkg,
                                "result": res_str,
                                "is_blocked": blocked.lower() == 'true',
                                "suspected_reason": f"Blocked by {effective_policy}" if blocked.lower() == 'true' else "Network Timeout/Fail",
                                "effective_policy": effective_policy
                            })

                if self.stats_start.search(clean_line):
                    in_stats = True; continue
                if in_stats:
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
                            "dns_blocked_cnt": int(groups[8]),
                            "tcp_avg_loss": float(groups[13])
                        })
                if self.stats_end.search(clean_line):
                    in_stats = False

        return {
            "sorted_timeline": dict(sorted(timeline.items())),
            "dns_issues": dns_issues,
            "device_config": device_config
        }
