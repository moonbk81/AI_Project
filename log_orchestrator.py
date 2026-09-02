import os
import json
import argparse
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

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
from parsers.battery_thermal_analyzer import BatteryThermalAnalyzer
from parsers.battery_thermal_analyzer import CpuUsageParser
from parsers.internet_stall_parser import InternetStallParser
from parsers.native_crash_parser import NativeCrashParser
from parsers.diagnostic_parser import BinderWarningParser
from parsers.rilj_parser import RiljParser
from parsers.system_property_parser import SystemPropertyParser
from parsers.emergency_call_parser import EmergencyCallParser
from parsers.analysis_bucket_builder import AnalysisBucketBuilder

ProgressCallback = Optional[Callable[[str, int], None]]


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
        self.native_crash_parser = NativeCrashParser(self._get_surrounding_context_logs)
        self.binder_parser = BinderWarningParser(self._get_surrounding_context_logs)
        self.rilj_parser = RiljParser()
        self.sys_prop_parser = SystemPropertyParser()
        self.build_info_parser = BuildInfoParser()
        self.emergency_call_parser = EmergencyCallParser(self._get_surrounding_context_logs)

        self.bucket_builder = AnalysisBucketBuilder(self._add_context_window)
        self._time_index = None

    def _build_time_index(self, lines):
        """Build the shared time index before parallel parsers read it."""
        time_index = {}
        for line in lines:
            if len(line) > 15:
                t_str = line[:14]
                if t_str[2] == '-' and t_str[5] == ' ':
                    time_index.setdefault(t_str, []).append(line.strip())
        self._time_index = time_index

    def _get_surrounding_context_logs(self, lines, target_time_str, window_seconds=3, max_lines=150):
        """O(1) 인덱싱 기반 초고속 주변 로그 스캐너 (Time-Window Glue)"""
        if self._time_index is None:
            self._build_time_index(lines)

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
        """이벤트성 로그는 핵심 라인 주변 context를 함께 포함합니다.

        버킷에는 라인 문자열이 아니라 원본 줄 번호를 넣습니다. 겹치는 window 가
        같은 줄을 여러 번 담아도 집합이 한 번으로 모아주고, 로그에 실제로 여러 번
        찍힌 같은 문자열은 줄 번호가 달라 각각 살아남습니다.
        """
        start = max(0, idx - window)
        end = min(len(lines), idx + window + 1)
        buckets[bucket_name].update(range(start, end))

    @staticmethod
    def _mark_emergency_calls(call_sessions, emergency_calls):
        """통화 이력에 긴급호 표시를 붙인다.

        통화 목록만 보면 긴급호가 일반 MO 발신과 구분되지 않는다. IMS 세션 키
        (`objId`)가 양쪽에 다 있으므로 그걸로 잇는다. 키를 못 찾은 긴급호는 통화
        이력에 표시가 안 붙지만, 긴급호 자체는 따로 남으므로 잃지 않는다.
        """
        by_obj_id = {
            str(attempt.get("ims_obj_id")): attempt
            for attempt in (emergency_calls or [])
            if attempt.get("ims_obj_id")
        }
        if not by_obj_id:
            return

        for session in call_sessions or []:
            session_id = str(session.get("id") or "")
            for obj_id, attempt in by_obj_id.items():
                if f"objId:{obj_id}" in session_id:
                    session["is_emergency"] = True
                    session["emergency_number"] = attempt.get("number", "")
                    break

    def _enrich_dns_queries(self, dns_queries, network_timeseries):
        """Network_DNS_Issue에서 확인한 package/policy를 DNS_Query에도 반영한다."""
        if not dns_queries:
            return dns_queries

        issues = (network_timeseries or {}).get("dns_issues", []) or []
        issue_by_key = {
            (i.get("time"), str(i.get("net_id")), str(i.get("uid"))): i
            for i in issues
            if isinstance(i, dict)
        }

        for query in dns_queries:
            if not isinstance(query, dict):
                continue
            key = (query.get("time"), str(query.get("net_id")), str(query.get("uid")))
            issue = issue_by_key.get(key)
            if not issue:
                continue

            package = issue.get("package")
            if package:
                query["app_name"] = package
            if issue.get("is_blocked"):
                query["return_code"] = "BLOCKED"
            query["is_blocked"] = issue.get("is_blocked", query.get("is_blocked"))
            query["effective_policy"] = issue.get("effective_policy", query.get("effective_policy"))
            query["suspected_reason"] = issue.get("suspected_reason", query.get("suspected_reason"))
        return dns_queries

    def run_batch(self, output_path, progress_callback: ProgressCallback = None):
        """모든 파서를 무조건 가동하는 메인 파이프라인"""
        try:
            def report_progress(message, progress):
                if progress_callback:
                    progress_callback(message, progress)

            report_progress("로그 파일 읽는 중...", 3)
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            report_progress(f"{len(lines)}개 로그 라인 시간 인덱스 생성 중...", 6)
            self._build_time_index(lines)

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

            report_progress("파서 후보 라인 분류 중...", 10)
            # 1. 1회 스캔 기반 parser별 후보 라인 버킷 생성
            buckets = self.bucket_builder.build(lines)
            report_progress("파서 후보 라인 분류 완료. 병렬 분석 시작...", 15)

            result = {}
            total_steps = 25
            completed_steps = 0

            def mark_step(label):
                nonlocal completed_steps
                completed_steps += 1
                progress = 15 + int(35 * min(completed_steps, total_steps) / total_steps)
                report_progress(f"로그 분석 중... ({label}, {completed_steps}/{total_steps})", progress)

            # ========== 1단계: 병렬 처리 가능한 parser들 실행 ==========
            # 의존성이 없는 파서들을 ThreadPoolExecutor로 병렬 실행
            parallel_tasks = []

            def run_parser_with_key(key, parser_func, *args):
                """Parser 실행 후 (key, result) 튜플 반환"""
                try:
                    res = parser_func(*args)
                    return (key, res)
                except Exception as e:
                    print(f"⚠️ {key} 파서 오류: {e}")
                    return (key, None)

            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = {
                    executor.submit(run_parser_with_key, 'nitz_history', self.nitz_parser.analyze, buckets['nitz']): 'nitz',
                    executor.submit(run_parser_with_key, 'crash_context', self.crash_parser.analyze, buckets['crash']): 'crash',
                    executor.submit(run_parser_with_key, 'native_crash_context', self.native_crash_parser.analyze, buckets['native_crash']): 'native_crash',
                    executor.submit(run_parser_with_key, 'anr_context', self.anr_parser.analyze, buckets['anr']): 'anr',
                    executor.submit(run_parser_with_key, 'radio_power', self.radio_power_parser.analyze, buckets['radio_power']): 'radio_power',
                    executor.submit(run_parser_with_key, 'ntn_data', self.ntn_processor.analyze, buckets['ntn']): 'ntn',
                    executor.submit(run_parser_with_key, 'boot_stats', self.boot_parser.analyze, buckets['boot']): 'boot',
                    executor.submit(run_parser_with_key, 'signal_level_history', self.signal_parser.analyze, buckets['signal']): 'signal',
                    executor.submit(run_parser_with_key, 'data_usage_stats', self.data_usage_parser.analyze, buckets['usage'], global_uid_map): 'usage',
                    executor.submit(run_parser_with_key, 'battery_stats', self.battery_parser.analyze, lines): 'battery',
                    executor.submit(run_parser_with_key, 'cpu_usage_stats', self.cpu_usage_parser.analyze, lines): 'cpu',
                    executor.submit(run_parser_with_key, 'battery_thermal_stats', self.battery_thermal_parser.analyze, lines): 'thermal',
                    executor.submit(run_parser_with_key, 'binder_warnings', self.binder_parser.analyze, buckets['binder']): 'binder',
                    executor.submit(run_parser_with_key, 'rilj_transactions', self.rilj_parser.analyze, buckets['rilj']): 'rilj',
                    executor.submit(run_parser_with_key, 'system_properties', self.sys_prop_parser.analyze, lines): 'sysprop',
                    # 긴급호는 발신부터 긴급 PDN 응답까지 흐름을 따라가야 하므로
                    # 버킷이 아니라 전체 lines 를 본다. 파서가 값싼 문자열 검사로
                    # 볼 줄을 먼저 걸러 낸다.
                    executor.submit(run_parser_with_key, 'emergency_calls', self.emergency_call_parser.analyze, lines): 'emergency',
                    executor.submit(run_parser_with_key, 'build_info', self.build_info_parser.analyze, lines): 'build',
                    executor.submit(run_parser_with_key, 'ims_sip_data', self.ims_sip_parser.analyze, buckets['ims_sip']): 'ims_sip',
                }

                # 결과 수집 (조건부 저장)
                for future in as_completed(futures):
                    key, value = future.result()
                    if value is not None:
                        result[key] = value
                    # binder의 경우 추가 처리
                    if key == 'binder_warnings' and value is not None:
                        if binder_ctx := self.binder_parser.build_context_summary(buckets.get('binder_context', [])):
                            result['binder_context_summary'] = binder_ctx
                    mark_step(futures[future])

            # ========== 2단계: full lines 필요한 순차 파서들 ==========
            result['call_sessions'] = self.tel_parser.analyze(lines)
            self._mark_emergency_calls(result['call_sessions'], result.get('emergency_calls'))
            mark_step("call")
            result['oos_events'] = self.oos_parser.analyze(lines)
            mark_step("oos")
            result['network_timeseries'] = self.net_ts_analyzer.analyze(lines)
            mark_step("network_timeseries")

            # ========== 3단계: 의존성 있는 parser들 (순차) ==========
            datacall_lines = buckets.get('datacall') or lines
            internet_stall_lines = buckets.get('internet_stall') or lines
            result['datacall_data'] = self.datacall_parser.analyze(datacall_lines)
            mark_step("datacall")

            if dns_res := self.dns_parser.analyze(
                buckets['dns'],
                global_uid_map=global_uid_map
            ):
                result['dns_queries'] = self._enrich_dns_queries(
                    dns_res.get('queries', []),
                    result.get('network_timeseries', {}),
                )
                if health_warnings := dns_res.get('health_warnings', []):
                    result['dns_health_warnings'] = health_warnings
            mark_step("dns")

            result['internet_stall'] = self.internet_stall_parser.analyze(
                internet_stall_lines,
                data_call_events=result.get('datacall_data', []),
                dns_events=result.get('dns_queries', []),
                report_data=result)
            mark_step("internet_stall")

            # ========== 4단계: UI 리포트 생성 (병렬) ==========
            with ThreadPoolExecutor(max_workers=4) as executor:
                executor.submit(self.ntn_processor.save_ui_report, "./result", self.base_name)
                executor.submit(self.ims_sip_parser.save_ui_report, "./result", self.base_name)
                executor.submit(self.datacall_parser.save_ui_report, "./result", self.base_name)
                executor.submit(self.ntn_processor.build_and_save_payloads, "./payloads")
                executor.submit(self.internet_stall_parser.save_ui_report, "./result", self.base_name, result['internet_stall'])
                # 모든 작업 완료 대기
            mark_step("ui_report")

            # 4. JSON 저장
            with open(output_path, "w", encoding="utf-8") as j:
                json.dump(result, j, indent=4, ensure_ascii=False)
            report_progress("로그 분석 리포트 생성 완료.", 50)
            return True

        except Exception as e:
            print(f"Error in LogOrchestrator run_batch: {e}")
            return False
