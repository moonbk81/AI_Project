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

        if is_cap and tmp and (len(tmp["call_stack"]) > 0 or fatal_info_count > 0):
            if self.get_context_fn: tmp["cross_context_logs"] = self.get_context_fn(lines, tmp["time"])
            crashes.append(tmp)

        # 💡 리스트만 안전하게 반환!
        return crashes

class AnrParser(BaseParser):
    def analyze(self, lines, target_package=None):
        anr_list = []
        current_anr = None

        anr_start_re = re.compile(
            r'(?:ActivityManager:\s)?ANR in\s+(\S+)|Application is not responding:\s+(\S+)',
            re.I
        )
        anr_reason_re = re.compile(r'ActivityManager:\s+Reason:\s+(.+)')
        cmd_line_re = re.compile(r'Cmd line:\s+(.+)')

        cpu_re = re.compile(r'CPU usage from|CPU usage since|Load:')
        system_server_re = re.compile(r'system_server|Watchdog|ActivityManager|InputDispatcher|WindowManager')
        io_re = re.compile(r'\biowait\b|\bblocked\b|slow operation|StrictMode|fsync|disk|I/O|io ', re.I)

        pre_context = deque(maxlen=120)

        all_threads = {}
        target_pid = None
        main_tid = None

        in_anr_trace = False
        in_target_process = False
        current_tid = None

        in_binder = False
        matched_tx = []

        cpu_logs = []
        system_server_logs = []
        io_logs = []

        def reset_trace_state():
            nonlocal all_threads, target_pid, main_tid
            nonlocal in_anr_trace, in_target_process, current_tid
            nonlocal in_binder, matched_tx
            nonlocal cpu_logs, system_server_logs, io_logs

            all_threads = {}
            target_pid = None
            main_tid = None
            in_anr_trace = False
            in_target_process = False
            current_tid = None
            in_binder = False
            matched_tx = []
            cpu_logs = []
            system_server_logs = []
            io_logs = []

        def find_cmd_line(start_idx):
            for k in range(1, 6):
                idx = start_idx + k
                if idx >= len(lines):
                    break

                m = cmd_line_re.search(lines[idx])
                if m:
                    return m.group(1).strip()

            return None

        def collect_context_hint(clean_line):
            if cpu_re.search(clean_line):
                cpu_logs.append(clean_line)

            if system_server_re.search(clean_line):
                if current_anr and current_anr.get("process") in clean_line:
                    system_server_logs.append(clean_line)
                elif "ANR" in clean_line or "InputDispatcher" in clean_line or "Watchdog" in clean_line:
                    system_server_logs.append(clean_line)

            if io_re.search(clean_line):
                io_logs.append(clean_line)

        def finalize_current_anr():
            if not current_anr:
                return

            lock_info = None
            main_stack = []

            if main_tid and main_tid in all_threads:
                main_stack = all_threads[main_tid]["stack"]

                for s_line in main_stack:
                    if lock_m := DIAG_PATTERNS['LOCK_HELD'].search(s_line):
                        lock_info = {
                            "addr": lock_m.group(1),
                            "owner_tid": lock_m.group(2)
                        }
                        break

            blocker_stack = None
            if lock_info:
                blocker_stack = all_threads.get(
                    lock_info["owner_tid"], {}
                ).get("stack")

            trace_level = "TRACE_INCLUDED" if len(main_stack) > 0 else "EVENT_ONLY"
            current_anr.update({
                "process_info": {
                    "name": current_anr.get("process"),
                    "pid": target_pid
                },
                "main": {
                    "tid": main_tid,
                    "stack": main_stack
                },
                "analysis_summary": {
                    "is_confirmed_anr": True,
                    "evidence_level": trace_level,
                    "has_lock_contention": lock_info is not None,
                    "has_active_binder": len(matched_tx) > 0,
                    "has_main_stack": len(main_stack) > 0,
                    "has_cpu_hint": len(cpu_logs) > 0,
                    "has_system_server_hint": len(system_server_logs) > 0,
                    "has_io_hint": len(io_logs) > 0,
                    "has_pre_anr_logcat": len(current_anr.get("pre_anr_logcat", [])) > 0
                },
                "lock_chain": {
                    "waiting_thread": main_tid,
                    "blocker_thread": lock_info["owner_tid"] if lock_info else None,
                    "lock_address": lock_info["addr"] if lock_info else None,
                    "blocker_stack": blocker_stack
                },
                "active_binder_transactions": matched_tx,
                "context_analysis": {
                    "cpu_logs": cpu_logs[-80:],
                    "system_server_logs": system_server_logs[-80:],
                    "io_logs": io_logs[-80:]
                }
            })

            anr_list.append(current_anr)

        reset_trace_state()

        for i, line in enumerate(lines):
            clean_line = self.clean_line(line)
            collect_context_hint(clean_line)

            # 1. ANR 시작 감지
            if anr_m := anr_start_re.search(clean_line):
                if current_anr:
                    finalize_current_anr()

                reset_trace_state()

                process_name = anr_m.group(1)

                if target_package and process_name != target_package:
                    current_anr = None
                    pre_context.append(clean_line)
                    continue

                current_anr = {
                    "time": "Unknown",
                    "process": process_name,
                    "reason": "Unknown",
                    "raw_log": clean_line + "\n",
                    "pre_anr_logcat": list(pre_context)
                }

                if ts_m := RE_TIME.search(clean_line):
                    current_anr["time"] = ts_m.group(0)

                pre_context.append(clean_line)
                continue

            if not current_anr:
                pre_context.append(clean_line)
                continue

            # 2. Reason 수집
            if reason_m := anr_reason_re.search(clean_line):
                current_anr["reason"] = reason_m.group(1)
                current_anr["raw_log"] += clean_line + "\n"

            # 3. ANR traces 진입
            if DIAG_PATTERNS['ANR_TRACES'].search(line):
                in_anr_trace = True
                pre_context.append(clean_line)
                continue

            # 4. 대상 PID / Cmd line 매칭
            if in_anr_trace:
                pid_m = DIAG_PATTERNS['PID_LINE'].search(line)

                if pid_m:
                    pid = pid_m.group(1)
                    cmd_name = find_cmd_line(i)

                    if cmd_name == current_anr["process"]:
                        target_pid = pid
                        in_target_process = True
                        current_tid = None
                    else:
                        in_target_process = False
                        current_tid = None

                    pre_context.append(clean_line)
                    continue

                # 5. main thread / thread stack 수집
                if in_target_process:
                    thread_m = DIAG_PATTERNS['THREAD_HEADER'].search(line.strip())

                    if thread_m:
                        thread_name = thread_m.group(1)
                        tid = thread_m.group(2)

                        current_tid = tid

                        is_main = thread_name.lower() == "main"
                        if is_main:
                            main_tid = tid

                        all_threads[tid] = {
                            "name": thread_name,
                            "stack": [clean_line],
                            "is_main": is_main
                        }

                    elif current_tid and clean_line:
                        all_threads[current_tid]["stack"].append(clean_line)

            # 6. Binder transaction 분석
            if "BINDER TRANSACTIONS" in line:
                in_binder = True
                pre_context.append(clean_line)
                continue

            if in_binder and "BINDER" in line and ":" not in line and "TRANSACTIONS" not in line:
                in_binder = False

            if in_binder:
                out_m = DIAG_PATTERNS['OUTGOING'].search(line)

                if (
                    out_m
                    and target_pid
                    and main_tid
                    and out_m.group(1) == target_pid
                    and out_m.group(2) == main_tid
                ):
                    matched_tx.append({
                        "from_pid": out_m.group(1),
                        "from_tid": out_m.group(2),
                        "to_pid": out_m.group(3),
                        "to_tid": out_m.group(4),
                        "code": out_m.group(5),
                        "raw": clean_line
                    })

            pre_context.append(clean_line)

        if current_anr:
            finalize_current_anr()

        return anr_list

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

class NitzParser(BaseParser):
    def analyze(self, lines):
        nitz_history = []

        # 정규식: NITZ: 26/05/03, 12:04:33-20,01 또는 nitz=26/05/03,12:04:33-20,01 패턴 캡처
        nitz_re = re.compile(r'(?:NITZ|nitz=)\s*(\d{2}/\d{2}/\d{2}[ ,]+\d{2}:\d{2}:\d{2}[-+]\d{2},\d{2})', re.I)

        for line in lines:
            clean_line = self.clean_line(line)
            match = nitz_re.search(clean_line)

            if match:
                log_time = "Unknown"
                if ts_m := RE_TIME.search(line):
                    log_time = ts_m.group(0)

                nitz_str = match.group(1).replace(" ", "") # 공백 제거하여 규격화

                # NITZ 문자열 분석 (예: 26/05/03,12:04:33-20,01)
                try:
                    parts = nitz_str.split('-') if '-' in nitz_str else nitz_str.split('+')
                    sign = '-' if '-' in nitz_str else '+'

                    tz_dst = parts[1].split(',')
                    tz_quarter = int(tz_dst[0])
                    dst_flag = tz_dst[1]

                    # 15분 단위 타임존을 시간으로 변환
                    tz_hours = (tz_quarter * 15) / 60.0
                    tz_desc = f"UTC{sign}{tz_hours:g}시간"

                    dst_desc = "적용(+1h)" if dst_flag != "00" else "미적용"

                except Exception:
                    tz_desc = "Unknown"
                    dst_desc = "Unknown"

                nitz_history.append({
                    "log_time": log_time,
                    "nitz_raw": nitz_str,
                    "timezone": tz_desc,
                    "dst_status": dst_desc
                })

        return nitz_history
