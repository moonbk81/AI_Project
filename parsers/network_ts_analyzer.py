import re
import os
import json
import argparse
from collections import defaultdict
from parsers.base import BaseParser

class NetworkTimeSeriesAnalyzer(BaseParser):
    def __init__(self, context_getter=None):
        super().__init__(context_getter)

        self.stats_start = re.compile(r'network statistics:', re.I)
        self.stats_end = re.compile(r'packet wakeup events:', re.I)
        # 1. 상세 DNS 차단 로그 패턴 (isBlocked)
        self.re_time = re.compile(r'\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}')
        self.re_tag = re.compile(r'[VDIWE]\s+([a-zA-Z0-9_\-]+)\s*(?=:)', re.I)
        self.re_dns_event = re.compile(r'DNS\s+requested\s+by\s+(\d+),\s+(\d+)\((.*?)\),\s+(\d+)\((.*?)\),\s+isBlocked=(\w+)', re.I)

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
            r'delayed\s+rsp=(\d+),\s*(?:blocked\s+rsp=(\d+),\s*)?'
            r'connect\s+avg=(\d+)ms\s+max=(\d+)ms\s+err=(\d+(?:\.\d+)?)%\s+tot=(\d+),\s*'
            r'tcp\s+avg_loss=(\d+(?:\.\d+)?)%', re.I
        )

        # 3. 차단 원인 추적 설정값

    def analyze(self, lines):
        in_stats = False
        dns_issues = []
        uid_block_map = {} # UID별 상세 차단 원인 저장소
        # 시계열 분석을 위해 시간(Time)을 키로 사용하는 딕셔너리
        timeline = defaultdict(lambda: {"net_stats": []})

        current_netid = None
        private_dns_status = {}

        # 1단계: UID별 blocked_state 정보 사전 수집
        for line in lines:
            clean_line = self.clean_line(line)
            uid_m = self.re_uid_state.search(clean_line)
            if uid_m:
                uid_block_map[uid_m.group('uid')] = uid_m.group('effective')

        # 2단계: 메인 분석 루프
        for line in lines:
            clean_line = self.clean_line(line)
            if "NetId:" in clean_line:
                netid_m = re.search(r'NetId:\s*(\d+)', clean_line)
                if netid_m:
                    current_netid = netid_m.group(1)
                    if current_netid not in private_dns_status:
                        private_dns_status[current_netid] = {
                            "mode": "UNKNOWN",
                            "fail_count": 0,
                            "failed_ips": []
                        }

            if current_netid:
                if "Private DNS mode:" in clean_line:
                    mode_m = re.search(r'Private DNS mode:\s*([a-zA-Z]+)', clean_line, re.I)
                    if mode_m:
                        private_dns_status[current_netid]["mode"] = mode_m.group(1).upper()

                # DoT configuration 세션 붕괴 감지
                if "status{fail}" in clean_line:
                    private_dns_status[current_netid]["fail_count"] += 1
                    # IPv4 / IPv6 주소 추출
                    ip_m = re.search(r'([a-fA-F0-9:]+|\d+\.\d+\.\d+\.\d+)\s+name', clean_line)
                    if ip_m:
                        private_dns_status[current_netid]["failed_ips"].append(ip_m.group(1))

            tag_m = self.re_tag.search(clean_line)
            tag = tag_m.group(1).strip() if tag_m else None

            if tag == "NetdEventListenerService":
                dns_m = self.re_dns_event.search(clean_line)
                if dns_m:
                    net_id, uid, pkg, res_code, res_str, blocked = dns_m.groups()
                    if "FAIL" in res_str or "NODATA" in res_str or blocked.lower() == 'true':
                        # 수집된 UID 상태 매핑
                        effective_policy = uid_block_map.get(uid, "SYSTEM_POLICY")
                        dns_issues.append({
                            "time": clean_line[:18],
                            "net_id": net_id,
                            "uid": uid,
                            "package": pkg,
                            "result": res_str,
                            "is_blocked": blocked.lower() == 'true',
                            "suspected_reason": f"Blocked by {effective_policy}" if blocked.lower() == 'true' else "Network Timeout/Fail",
                            "effective_policy": effective_policy
                        })

            if self.stats_start.search(clean_line):
                in_stats = True
                continue

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
                        "dns_blocked_cnt": int(groups[8]) if groups[8] is not None else 0,
                        "tcp_avg_loss": float(groups[13])
                    })
            if self.stats_end.search(clean_line):
                in_stats = False

        return {
            "sorted_timeline": dict(sorted(timeline.items())),
            "dns_issues": dns_issues,
        }
