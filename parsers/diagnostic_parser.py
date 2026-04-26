import re
from collections import deque
from parsers.base import BaseParser
from core.constants import DIAG_PATTERNS, RE_TIME, RADIO_POWER_ERRORS
from core.telephony_constants import RAT_TYPE_MAP

class BootParser(BaseParser):
    def analyze(self, lines):
        boot_events = []
        for line in lines:
            clean_line = self.clean_line(line)
            if clean_line.startswith("!@Boot"):
                match = DIAG_PATTERNS['BOOT_EVENT'].search(clean_line)
                if match:
                    boot_events.append({
                        "Event": match.group(1).strip(),
                        "Time_ms": int(match.group(3)),
                        "Ktime_ms": int(match.group(4)),
                        "Delta_ms": int(match.group(5))
                    })
        return boot_events

class SignalParser(BaseParser):
    def analyze(self, lines):
        history = []
        for line in lines:
            if "EVENT_SIGNAL_LEVEL_INFO_CHANGED" in line:
                m = DIAG_PATTERNS['SIGNAL_LEVEL'].search(line)
                if m:
                    time_str, slot, info = m.group(1), m.group(2), m.group(3).strip()
                    if "no level" in info.lower():
                        history.append({
                            "time": time_str,
                            "slot": slot,
                            "rat": "NO_SVC",
                            "level": 0,
                            "raw_info": info
                            })
                    else:
                        for item in info.split():
                            if '=' in item:
                                k, v = item.split('=')
                                rat_name = k.replace('Level', '').upper()
                                history.append({
                                    "time": time_str,
                                    "slot": slot,
                                    "rat": rat_name,
                                    "level": self.safe_to_int(v),
                                    "raw_info": info
                                    })
        return history

class DataUsageParser(BaseParser):
    def analyze(self, lines):
        usage_by_key, uid_map = {}, {}
        current_app_id_in_log = None
        current_key = None  # 🚨 [핵심 픽스] 루프 진입 전 반드시 초기화!

        for line in lines:
            line_stripped = self.clean_line(line)
            if "NetdEventListenerService" in line_stripped or "DNS Requested by" in line_stripped:
                m_pkg = re.search(r'DNS Requested by\s+\d+,\s*(\d+)\(([^)]+)\)', line_stripped)
                if m_pkg: uid_map[m_pkg.group(1)] = m_pkg.group(2)

            if m_app_id := re.search(r'App ID:\s*(\d+)', line_stripped): current_app_id_in_log = m_app_id.group(1)
            if (m_package := re.search(r'Package:\s*([a-zA-Z0-9_.]+)', line_stripped)) and current_app_id_in_log:
                uid_map[current_app_id_in_log] = m_package.group(1)
                current_app_id_in_log = None

            if "transports={0}" in line_stripped and "metered=true" in line_stripped:
                m_uid, m_rat = re.search(r'uid=(-\d+|\d+)', line_stripped), re.search(r'ratType=(-\d+|\d+)', line_stripped)
                if m_uid and m_rat:
                    uid_val, rat_val = m_uid.group(1), m_rat.group(1)
                    if uid_val == "-1": continue
                    current_key = (uid_val, RAT_TYPE_MAP.get(rat_val, f"RAT_{rat_val}"))
                    if current_key not in usage_by_key: usage_by_key[current_key] = {"rx_bytes": 0, "tx_bytes": 0}
                continue

            # 이제 current_key가 None이라도 안전하게 넘어갑니다
            if current_key and line_stripped.startswith("st="):
                m_bytes = DIAG_PATTERNS['NETSTAT_BYTES'].search(line_stripped)
                if m_bytes:
                    usage_by_key[current_key]["rx_bytes"] += int(m_bytes.group(1))
                    usage_by_key[current_key]["tx_bytes"] += int(m_bytes.group(2))

        report_data = []
        for (uid, rat), data in usage_by_key.items():
            total_bytes = data["rx_bytes"] + data["tx_bytes"]
            if total_bytes > 0:
                app_name = {"-5": "모바일 핫스팟 (Tethering)", "-4": "삭제된 앱 (Removed)", "1000": "Android System (OS)", "0": "OS Kernel (Root)"}.get(uid, uid_map.get(uid, f"App_UID_{uid}"))
                report_data.append({
                    "uid": uid,
                    "app_name": app_name,
                    "rat": rat,
                    "total_mb": round(total_bytes / (1024 * 1024), 2),
                    "rx_mb": round(data["rx_bytes"] / (1024 * 1024), 2),
                    "tx_mb": round(data["tx_bytes"] / (1024 * 1024), 2)
                })
        return sorted(report_data, key=lambda x: x["total_mb"], reverse=True)

class DnsParser(BaseParser):
    def analyze(self, lines):
        dns_events = []
        for line in lines:
            if "DNS Requested by" in line:
                m = DIAG_PATTERNS['DNS_FULL'].search(line)
                if m:
                    time_str, uid, app_name, rest = m.group('time'), m.group('uid'), m.group('app_name'), m.group('rest')
                    if "SUCCESS" in rest.upper(): return_code = "SUCCESS"
                    else:
                        rc_match = re.search(r',\s*(\d+)\(([^)]+)\)', rest)
                        if rc_match:
                            raw_code, status_text = rc_match.group(1), rc_match.group(2)
                            return_code = "SUCCESS" if raw_code == "0" else f"{status_text} (Code:{raw_code})".strip()
                            if "isBlocked=true" in rest: return_code = f"BLOCKED (Code:{raw_code})"
                        else: return_code = "UNKNOWN"
                    dns_events.append({
                        "time": time_str,
                        "uid": uid,
                        "app_name": app_name,
                        "return_code": return_code,
                        "raw_info": rest.strip()
                        })
        return dns_events

class CrashParser(BaseParser):
    def analyze(self, lines):
        crashes, is_cap, step, tmp = [], False, 0, None
        pre_ctx = deque(maxlen=10)
        for line in lines:
            clean_line = self.clean_line(line)
            if not clean_line: continue
            ts_m = RE_TIME.search(clean_line)
            ts = ts_m.group(0) if ts_m else "00-00 00:00:00.000"

            is_fatal_app, is_fatal_sys = DIAG_PATTERNS['FATAL_APP'].search(clean_line), DIAG_PATTERNS['FATAL_SYS'].search(clean_line)

            if is_fatal_app or is_fatal_sys:
                if is_cap and tmp:
                    if self.get_context_fn: tmp["cross_context_logs"] = self.get_context_fn(lines, tmp["time"])
                    crashes.append(tmp)
                is_cap, step, fatal_info_count = True, (1 if is_fatal_app else 2), 0
                tmp = {"time": ts, "trigger": clean_line, "process": ("system_server" if is_fatal_sys else "Unknown"), "exception_info": "", "call_stack": [], "context": list(pre_ctx)[-5:]}
                continue

            if is_cap:
                if step == 1:
                    if DIAG_PATTERNS['PROC_PHONE'].search(clean_line): tmp["process"] = "com.android.phone"; step = 2; continue
                    elif "Process:" in clean_line: is_cap = False
                elif step == 2:
                    if DIAG_PATTERNS['STACK_LINE'].search(clean_line) or clean_line.startswith("at "): tmp["call_stack"].append(clean_line)
                    else:
                        if len(tmp["call_stack"]) > 0 or fatal_info_count >= 3:
                            if self.get_context_fn: tmp["cross_context_logs"] = self.get_context_fn(lines, tmp["time"])
                            crashes.append(tmp); is_cap = False
                        else: tmp["exception_info"] += clean_line + " "; fatal_info_count += 1
            pre_ctx.append(line.strip())
        return crashes

class BatteryParser(BaseParser):
    def analyze(self, lines):
        report = { "stats_period": "Unknown", "time_on_battery": "Unknown", "screen_off_time": "Unknown", "screen_on_battery_use": "Unknown", "signal_strength_distribution": {}, "mobile_radio_active": "Unknown", "telephony_drain_evaluation": "Unknown" }
        has_data, in_signal_levels, signal_line_count = False, False, 0

        for line in lines:
            clean_line = self.clean_line(line)
            if not clean_line: in_signal_levels = False; continue

            if clean_line.startswith("Phone signal levels:") or clean_line.startswith("Phone signal strength:"):
                in_signal_levels, signal_line_count, has_data = True, 0, True; continue

            if in_signal_levels:
                signal_line_count += 1
                if signal_line_count > 10 or ":" in clean_line: in_signal_levels = False
                else:
                    if level_match := re.match(r'^(none|poor|moderate|good|great)\s', clean_line, re.I):
                        if pct_match := re.search(r'\(([\d.]+)%\)', clean_line):
                            report["signal_strength_distribution"][level_match.group(1).lower()] = float(pct_match.group(1))
                continue

            if clean_line.startswith("Time on battery:"): report["time_on_battery"] = clean_line.split(":", 1)[1].strip(); has_data = True
            elif clean_line.startswith("Mobile radio active:"): report["mobile_radio_active"] = clean_line.split(":", 1)[1].strip(); has_data = True
            elif clean_line.startswith("Stats from ") and " to " in clean_line:
                if m := re.search(r'Stats from\s+(.*?)\s+to\s+(.*)', clean_line, re.I): report["stats_period"] = f"{m.group(1).strip()} ~ {m.group(2).strip()}"; has_data = True

        total_bad = report["signal_strength_distribution"].get("poor", 0.0) + report["signal_strength_distribution"].get("none", 0.0)
        if total_bad > 30.0: report["telephony_drain_evaluation"] = f"CRITICAL: 심각한 배터리 광탈 의심 (불량 신호 {total_bad}%)"
        elif total_bad > 15.0: report["telephony_drain_evaluation"] = f"WARNING: 모뎀 전력 소모 높음 (불량 신호 {total_bad}%)"
        else: report["telephony_drain_evaluation"] = f"NORMAL: 신호 불량 비중 {total_bad}% 양호"

        return report if has_data else None

class RadioPowerParser(BaseParser):
    def analyze(self, lines):
        # 파라미터가 1개(lines)로 강제되므로,
        # cross_context가 필요하다면 오케스트레이터가 lines 원본 전체를 넘겨주었다고 가정하고 작성
        requests, responses, results = {}, {}, []
        for line in lines:
            if req_match := DIAG_PATTERNS['RADIO_REQ'].search(line):
                seq = req_match.group('seq')
                requests[seq] = {
                    'timestamp': req_match.group('timestamp'),
                    'seq': seq,
                    'phone': req_match.group('phone'),
                    'on': req_match.group('on').lower() == 'true',
                    'raw_line': line.strip()
                }
                continue
            if resp_match := DIAG_PATTERNS['RADIO_RESP'].search(line):
                seq, content = resp_match.group('seq'), resp_match.group('content').strip()
                is_error = any(kw.upper() in content.upper() for kw in RADIO_POWER_ERRORS)
                error_msg = next((kw for kw in RADIO_POWER_ERRORS if kw.upper() in content.upper()), '')
                responses[seq] = {
                    'timestamp': resp_match.group('timestamp'),
                    'seq': seq,
                    'error_msg': error_msg,
                    'success': not is_error,
                    'raw_line': line.strip()
                }

        for seq, req in requests.items():
            resp = responses.get(seq)
            success = resp['success'] if resp else False
            result = {
                'seq': seq,
                'request_time': req['timestamp'],
                'response_time': resp['timestamp'] if resp else None,
                'success': success,
                'error_msg': resp['error_msg'] if resp else 'NO_RESPONSE'
            }
            if not success and self.get_context_fn:
                err_time = result['response_time'] or result['request_time']
                result['cross_context_logs'] = self.get_context_fn(lines, err_time)
            results.append(result)
        return results