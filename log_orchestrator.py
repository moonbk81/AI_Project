import os
import json
import argparse
import re

from datetime import datetime, timedelta
from parsers.telephony_parser import TelephonyParser, OosParser
from parsers.diagnostic_parser import (
    BootParser, SignalParser, DataUsageParser, DnsParser, CrashParser,
    AnrParser, BatteryParser, RadioPowerParser, NitzParser, BuildInfoParser
)
from parsers.network_ts_analyzer import NetworkTimeSeriesAnalyzer
from parsers.ntn_processor import NtnProcessor
from parsers.data_call_processor import DataCallProcessor
from parsers.ims_sip_processor import ImsSipProcessor
from parsers.sat_at_parser import SatAtProcessor
from parsers.battery_thermal_analyzer import BatteryThermalAnalyzer
from parsers.battery_thermal_analyzer import CpuUsageParser
from parsers.internet_stall_parser import InternetStallParser
from parsers.native_crash_parser import NativeCrashParser
from parsers.diagnostic_parser import BinderWarningParser
from parsers.rilj_parser import RiljParser
from parsers.system_property_parser import SystemPropertyParser
from parsers.analysis_bucket_builder import AnalysisBucketBuilder

class LogOrchestrator:
    def __init__(self, file_path):
        self.file_path = file_path
        self.base_name = os.path.splitext(os.path.basename(file_path))[0]

        self.tel_parser = TelephonyParser(self._get_surrounding_context_logs)
        self.oos_parser = OosParser(self._get_surrounding_context_logs)
        self.boot_parser = BootParser()
        self.signal_parser = SignalParser()
        self.data_usage_parser = DataUsageParser()
        self.dns_parser = DnsParser()
        self.crash_parser = CrashParser(self._get_surrounding_context_logs)
        self.anr_parser = AnrParser()
        self.battery_parser = BatteryParser()
        self.battery_thermal_parser = BatteryThermalAnalyzer(
            context_getter=self._get_surrounding_context_logs
        )
        self.cpu_usage_parser = CpuUsageParser()
        self.nitz_parser = NitzParser()
        self.radio_power_parser = RadioPowerParser(self._get_surrounding_context_logs)
        self.net_ts_analyzer = NetworkTimeSeriesAnalyzer()
        self.ntn_processor = NtnProcessor(filename=self.base_name)
        self.datacall_parser = DataCallProcessor(context_getter=self._get_surrounding_context_logs)
        self.internet_stall_parser = InternetStallParser()
        self.ims_sip_parser = ImsSipProcessor(context_getter=self._get_surrounding_context_logs)
        self.sat_at_parser = SatAtProcessor(context_getter=self._get_surrounding_context_logs)
        self.native_crash_parser = NativeCrashParser(self._get_surrounding_context_logs)
        self.binder_parser = BinderWarningParser(self._get_surrounding_context_logs)
        self.rilj_parser = RiljParser()
        self.sys_prop_parser = SystemPropertyParser()
        self.build_info_parser = BuildInfoParser()

        self.bucket_builder = AnalysisBucketBuilder(self._add_context_window)
        self._time_index = None

    def _get_surrounding_context_logs(self, lines, target_time_str, window_seconds=3, max_lines=150):
        """O(1) 인덱싱 기반 초고속 주변 로그 스캐너 (Time-Window Glue)"""
        if self._time_index is None:
            self._time_index = {}
            for line in lines:
                if len(line) > 15:
                    t_str = line[:14]
                    if t_str[2] == '-' and t_str[5] == ' ':
                        if t_str not in self._time_index: self._time_index[t_str] = []
                        self._time_index[t_str].append(line.strip())

        if not target_time_str or target_time_str == "00-00 00:00:00.000": return []
        base_time_str = target_time_str.split('.')[0] if '.' in target_time_str else target_time_str
        current_year = datetime.now().year

        try: target_dt = datetime.strptime(f"{current_year}-{base_time_str}", "%Y-%m-%d %H:%M:%S")
        except ValueError: return []

        cross_context_logs = []
        for offset in range(-window_seconds, window_seconds + 1):
            win_str = (target_dt + timedelta(seconds=offset)).strftime("%m-%d %H:%M:%S")
            if win_str in self._time_index:
                cross_context_logs.extend(self._time_index[win_str])

        return cross_context_logs[-max_lines:] if len(cross_context_logs) > max_lines else cross_context_logs

    def _add_context_window(self, buckets, bucket_name, lines, idx, window=80):
        """이벤트성 로그는 핵심 라인 주변 context를 함께 포함합니다."""
        start = max(0, idx - window)
        end = min(len(lines), idx + window + 1)
        buckets[bucket_name].extend(lines[start:end])

    def run_batch(self, output_path):
        """모든 파서를 무조건 가동하는 메인 파이프라인"""
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            # ==========================================
            # 🚨 1. [PACKAGE INFO]에서 전역 UID 매핑 테이블(정답지) 추출
            # ==========================================
            global_uid_map = {}
            in_package_info = False
            pkg_pattern = re.compile(r"\[UID\]\s*(\d+),\s*\[PackageName\]\s*([^,\s]+)")

            for line in lines:
                if "[PACKAGE INFO]" in line:
                    in_package_info = True
                    continue

                if in_package_info:
                    # 블록이 끝나면 플래그 끄기
                    if not line.strip() or (line.startswith("[") and "UID" not in line and "INIDEX" not in line):
                        in_package_info = False
                    else:
                        match = pkg_pattern.search(line)
                        if match:
                            global_uid_map[match.group(1)] = match.group(2).strip()

            # 1. 1회 스캔 기반 parser별 후보 라인 버킷 생성
            buckets = self.bucket_builder.build(lines)

            result = {}

            result['call_sessions'] = self.tel_parser.analyze(lines)
            result['oos_events'] = self.oos_parser.analyze(lines)
            result['nitz_history'] = self.nitz_parser.analyze(buckets['nitz'])
            result['crash_context'] = self.crash_parser.analyze(buckets['crash'])
            result['native_crash_context'] = self.native_crash_parser.analyze(buckets['native_crash'])
            result['anr_context'] = self.anr_parser.analyze(buckets['anr'])
            result['radio_power'] = self.radio_power_parser.analyze(buckets['radio_power'])

            # section/state 기반 parser는 후보 라인만 주면 누락 위험이 커서 full lines를 유지합니다.
            result['network_timeseries'] = self.net_ts_analyzer.analyze(lines)
            result['ntn_data'] = self.ntn_processor.analyze(buckets['ntn'])
            result['datacall_data'] = self.datacall_parser.analyze(lines)
            result['ims_sip_data'] = self.ims_sip_parser.analyze(buckets['ims_sip'])
            result['sat_at_data'] = self.sat_at_parser.analyze(buckets['sat_at'])
            result['internet_stall'] = self.internet_stall_parser.analyze(
                lines,
                data_call_events=result.get('datacall_data', []),
                report_data=result)

            # 지표성 데이터 추가
            # battery 계열은 dump section 전체를 읽는 경우가 있어 full lines를 유지합니다.
            if battery_res := self.battery_parser.analyze(lines): result['battery_stats'] = battery_res
            if cpu_res := self.cpu_usage_parser.analyze(lines): result['cpu_usage_stats'] = cpu_res
            if boot_res := self.boot_parser.analyze(buckets['boot']): result['boot_stats'] = boot_res
            if sig_res := self.signal_parser.analyze(buckets['signal']): result['signal_level_history'] = sig_res
            if net_usage := self.data_usage_parser.analyze(buckets['usage'], global_uid_map=global_uid_map): result['data_usage_stats'] = net_usage
            if dns_res := self.dns_parser.analyze(buckets['dns'], global_uid_map=global_uid_map): result['dns_queries'] = dns_res
            if battery_thermal_res := self.battery_thermal_parser.analyze(lines):
                result["battery_thermal_stats"] = battery_thermal_res
            if binder_res := self.binder_parser.analyze(buckets['binder']):
                result['binder_warnings'] = binder_res
                # Binder 관련 추가 확인 사항은 UI 테이블에 넣지 않고 별도 요약으로만 보관합니다.
                if binder_ctx := self.binder_parser.build_context_summary(buckets.get('binder_context', [])):
                    result['binder_context_summary'] = binder_ctx
            if rilj_res := self.rilj_parser.analyze(buckets['rilj']):
                result['rilj_transactions'] = rilj_res
            result['system_properties'] = self.sys_prop_parser.analyze(lines)
            result['build_info'] = self.build_info_parser.analyze(lines)

            # 3. 개별 UI 리포트 파일 생성 (하위 호환성 유지)
            self.ntn_processor.save_ui_report("./result", self.base_name)
            self.ims_sip_parser.save_ui_report("./result", self.base_name)
            self.datacall_parser.save_ui_report("./result", self.base_name)
            self.sat_at_parser.save_ui_report("./result", self.base_name)

            self.ntn_processor.build_and_save_payloads("./payloads")
            self.internet_stall_parser.save_ui_report("./result", self.base_name, result['internet_stall'])

            # 4. JSON 저장
            with open(output_path, "w", encoding="utf-8") as j:
                json.dump(result, j, indent=4, ensure_ascii=False)
            return True

        except Exception as e:
            print(f"Error in LogOrchestrator run_batch: {e}")
            return False
