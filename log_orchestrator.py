import os
import json
import argparse
from datetime import datetime, timedelta
from parsers.telephony_parser import TelephonyParser, OosParser
from parsers.diagnostic_parser import (
    BootParser, SignalParser, DataUsageParser, DnsParser, CrashParser,
    AnrParser, BatteryParser, RadioPowerParser, NitzParser
)
from parsers.network_ts_analyzer import NetworkTimeSeriesAnalyzer
from parsers.ntn_processor import NtnProcessor
from parsers.data_call_processor import DataCallProcessor
from parsers.ims_sip_processor import ImsSipProcessor
from parsers.sat_at_parser import SatAtProcessor
from parsers.battery_thermal_analyzer import BatteryThermalAnalyzer
from parsers.internet_stall_parser import InternetStallParser

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
        self.nitz_parser = NitzParser()
        self.radio_power_parser = RadioPowerParser(self._get_surrounding_context_logs)
        self.net_ts_analyzer = NetworkTimeSeriesAnalyzer()
        self.ntn_processor = NtnProcessor(filename=self.base_name)
        self.datacall_parser = DataCallProcessor(context_getter=self._get_surrounding_context_logs)
        self.internet_stall_parser = InternetStallParser()
        self.ims_sip_parser = ImsSipProcessor(context_getter=self._get_surrounding_context_logs)
        self.sat_at_parser = SatAtProcessor(context_getter=self._get_surrounding_context_logs)
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

    def _build_analysis_buckets(self, lines):
        """
        parser마다 전체 dump를 반복 순회하지 않도록 1회 스캔으로 후보 라인 버킷을 만듭니다.
        call/oos parser는 상태 흐름 누락 위험이 있어 run_batch에서 계속 전체 lines를 사용합니다.
        """
        buckets = {
            'boot': [],
            'signal': [],
            'dns': [],
            'usage': [],
            'net_ts': [],
            'crash': [],
            'anr': [],
            'radio_power': [],
            'battery': [],
            'battery_thermal': [],
            'ntn': [],
            'datacall': [],
            'ims_sip': [],
            'sat_at': [],
            'internet_stall': [],
            'nitz': [],
        }

        crash_keywords = [
            "FATAL EXCEPTION", "Fatal signal", "AndroidRuntime", "am_crash",
            "force close", "Tombstone written to", "Build fingerprint:", "Abort message:",
        ]
        anr_keywords = [
            "ANR", "am_anr", "Application Not Responding", "Input dispatching timed out",
            "Broadcast of Intent", "executing service", "traces.txt", "CPU usage from",
        ]
        radio_power_keywords = [
            "setRadioPower", "setRadioPowerForReason", "RADIO_POWER", "RIL_REQUEST_RADIO_POWER",
            "RADIO_OFF", "RADIO_ON", "airplane_mode_on", "AIRPLANE_MODE",
        ]
        battery_keywords = [
            "Battery", "battery", "batterystats", "BatteryService", "HealthInfo",
            "level=", "plugged=", "temperature=", "voltage=",
        ]
        thermal_context_keywords = [
            "ThermalEvent", "ThermalService", "ThermalManager", "thermalservice",
            "overheat", "throttling", "cooling device", "CoolingDevice",
        ]
        thermal_line_keywords = [
            "temperature mValue", "skin-therm", "battery-therm", "sec-battery",
            "WakeLock", "Wakelock", "wakelock held", "wake_lock",
        ]
        ntn_keywords = [
            "NTN", "ntn", "satellite", "Satellite", "NonTerrestrial", "non-terrestrial",
        ]
        datacall_keywords = [
            "DataCall", "data call", "SetupDataCall", "SETUP_DATA_CALL", "DEACTIVATE_DATA_CALL",
            "DcTracker", "DataNetwork", "TelephonyNetworkFactory", "ApnContext",
        ]
        ims_sip_keywords = [
            "SIP/2.0", "REGISTER sip:", "INVITE sip:", "BYE sip:", "CANCEL sip:",
            "P-CSCF", "ImsReasonInfo", "ImsPhoneConnection", "ImsCallSession",
            "createCallProfile", "onCallStarted", "onCallStartFailed",
        ]
        sat_at_keywords = [
            "AT+", "AT^", "AT$", "> AT", "< AT",
            "+CEREG", "+CREG", "+CGREG", "+COPS", "+CSQ",
        ]
        internet_stall_context_keywords = [
            "Data Stall", "data stall", "validation failed",
            "PARTIAL_CONNECTIVITY", "NO_INTERNET", "EVENT_NETWORK_TESTED",
            "default network changed", "network lost",
        ]
        internet_stall_line_keywords = [
            "TcpSocketTracker", "PrivateDns", "NET_CAPABILITY_VALIDATED", "NetworkAgentInfo",
        ]

        for idx, line in enumerate(lines):
            if line.startswith("!@Boot"):
                buckets['boot'].append(line)

            if "EVENT_SIGNAL_LEVEL_INFO_CHANGED" in line:
                buckets['signal'].append(line)

            if any(k in line for k in ["transports={0}", "metered=true", "st=", "rb=", "DNS Requested", "pkg,"]):
                buckets['usage'].append(line)

            if "DNS Requested" in line:
                buckets['dns'].append(line)

            if any(k in line for k in ["NetId", "DnsEvent", "TcpStats", "NetworkMonitor", "ConnectivityService"]):
                buckets['net_ts'].append(line)

            if any(k in line for k in crash_keywords):
                self._add_context_window(buckets, 'crash', lines, idx, window=80)

            if any(k in line for k in anr_keywords):
                self._add_context_window(buckets, 'anr', lines, idx, window=180)

            if any(k in line for k in radio_power_keywords):
                self._add_context_window(buckets, 'radio_power', lines, idx, window=40)

            if any(k in line for k in battery_keywords):
                buckets['battery'].append(line)
                buckets['battery_thermal'].append(line)

            if any(k in line for k in thermal_context_keywords):
                self._add_context_window(buckets, 'battery_thermal', lines, idx, window=8)
            elif any(k in line for k in thermal_line_keywords):
                buckets['battery_thermal'].append(line)

            if any(k in line for k in ntn_keywords):
                buckets['ntn'].append(line)

            if any(k in line for k in datacall_keywords):
                self._add_context_window(buckets, 'datacall', lines, idx, window=60)
                buckets['internet_stall'].append(line)

            if any(k in line for k in ims_sip_keywords):
                self._add_context_window(buckets, 'ims_sip', lines, idx, window=30)

            if any(k in line for k in sat_at_keywords):
                buckets['sat_at'].append(line)

            if any(k in line for k in internet_stall_context_keywords):
                self._add_context_window(buckets, 'internet_stall', lines, idx, window=10)
            elif any(k in line for k in internet_stall_line_keywords):
                buckets['internet_stall'].append(line)

            if "nitz_status" in line:
                buckets['nitz'].append(line)

        for name, bucket_lines in buckets.items():
            seen = set()
            deduped = []
            for bucket_line in bucket_lines:
                if bucket_line not in seen:
                    seen.add(bucket_line)
                    deduped.append(bucket_line)
            buckets[name] = deduped

        print(
            "📊 [PreFilter] "
            + ", ".join(f"{name}={len(bucket_lines)}" for name, bucket_lines in buckets.items())
        )
        return buckets

    def run_batch(self, output_path):
        """모든 파서를 무조건 가동하는 메인 파이프라인"""
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            # 1. 1회 스캔 기반 parser별 후보 라인 버킷 생성
            buckets = self._build_analysis_buckets(lines)

            result = {}

            result['call_sessions'] = self.tel_parser.analyze(lines)
            result['oos_events'] = self.oos_parser.analyze(lines)
            result['nitz_history'] = self.nitz_parser.analyze(buckets['nitz'])
            result['crash_context'] = self.crash_parser.analyze(buckets['crash'])
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
            if boot_res := self.boot_parser.analyze(buckets['boot']): result['boot_stats'] = boot_res
            if sig_res := self.signal_parser.analyze(buckets['signal']): result['signal_level_history'] = sig_res
            if net_usage := self.data_usage_parser.analyze(buckets['usage']): result['data_usage_stats'] = net_usage
            if dns_res := self.dns_parser.analyze(buckets['dns']): result['dns_queries'] = dns_res
            if battery_thermal_res := self.battery_thermal_parser.analyze(lines):
                result["battery_thermal_stats"] = battery_thermal_res

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