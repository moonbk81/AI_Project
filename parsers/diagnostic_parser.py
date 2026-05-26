import re
from datetime import datetime
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
        raw_signals = [] # 💡 라디오 로그(상세 신호)를 담아둘 새로운 바구니

        for line in lines:
            # 1️⃣ 세부 신호 정보(NetworkSignalStrengthHandler) 수집 -> raw_signals 바구니에 보관
            if "NetworkSignalStrengthHandler - SignalStrength:" in line:
                ts_m = re.search(r'\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}', line)
                if not ts_m: continue
                ts = ts_m.group(0)

                details = {}

                # LTE 파싱 (L)
                l_match = re.search(r'L:\(([^)]+)\)', line)
                if l_match:
                    vals = [v.strip() for v in l_match.group(1).split(',')]
                    if len(vals) >= 4:
                        rsrp = f"-{vals[1]} dBm" if vals[1] != "2147483647" else "Unknown"
                        rsrq = f"-{vals[2]} dB" if vals[2] != "2147483647" else "Unknown"
                        rssnr = f"{int(vals[3])/10.0} dB" if vals[3] != "2147483647" else "Unknown"
                        details['LTE'] = {"RSRP": rsrp, "RSRQ": rsrq, "SINR": rssnr, "raw": f"L:({l_match.group(1)})"}

                # NR 파싱 (N)
                n_match = re.search(r'N:\(([^)]+)\)', line)
                if n_match:
                    vals = [v.strip() for v in n_match.group(1).split(',')]
                    if len(vals) >= 3:
                        rsrp = f"-{vals[0]} dBm" if vals[0] != "2147483647" else "Unknown"
                        rsrq = f"-{vals[1]} dB" if vals[1] != "2147483647" else "Unknown"
                        sinr = f"{int(vals[2])/10.0} dB" if vals[2] != "2147483647" else "Unknown"
                        details['NR'] = {"RSRP": rsrp, "RSRQ": rsrq, "SINR": sinr, "raw": f"N:({n_match.group(1)})"}

                # WCDMA 파싱 (W)
                w_match = re.search(r'W:\(([^)]+)\)', line)
                if w_match:
                    vals = [v.strip() for v in w_match.group(1).split(',')]
                    if len(vals) >= 4:
                        rssi = vals[0] if vals[0] not in ["99", "255", "2147483647"] else "Unknown"
                        rscp = vals[2] if vals[2] not in ["99", "255", "2147483647"] else "Unknown"
                        ecno = vals[3] if vals[3] not in ["99", "255", "2147483647"] else "Unknown"
                        details['WCDMA'] = {"RSSI": rssi, "RSCP": f"-{rscp} dBm" if rscp != "Unknown" else "Unknown", "EcNo": ecno, "raw": f"W:({w_match.group(1)})"}

                # GSM 파싱 (G)
                g_match = re.search(r'G:\(([^)]+)\)', line)
                if g_match:
                    vals = [v.strip() for v in g_match.group(1).split(',')]
                    if len(vals) >= 2:
                        rssi = vals[0] if vals[0] not in ["99", "255", "2147483647"] else "Unknown"
                        details['GSM'] = {"RSSI": rssi, "raw": f"G:({g_match.group(1)})"}

                if details:
                    raw_signals.append({"time": ts, "details": details})

            # 2️⃣ 레벨 변경 이벤트 수집 -> history 바구니에 보관 (이 시점엔 details를 빈칸으로 둠)
            if "EVENT_SIGNAL_LEVEL_INFO_CHANGED" in line:
                m = DIAG_PATTERNS['SIGNAL_LEVEL'].search(line)
                if m:
                    time_str, slot, info = m.group(1), m.group(2), m.group(3).strip()

                    if "no level" in info.lower():
                        history.append({
                            "time": time_str, "slot": slot, "rat": "NO_SVC",
                            "level": 0, "raw_info": info, "details": {}
                        })
                    else:
                        for item in info.split():
                            if '=' in item:
                                k, v = item.split('=')
                                rat_name = k.replace('Level', '').upper()
                                history.append({
                                    "time": time_str, "slot": slot, "rat": rat_name,
                                    "level": self.safe_to_int(v), "raw_info": info, "details": {}
                                })

        # 3️⃣ [핵심] 파싱 종료 후 시간(Timestamp) 기반으로 결합하기 (Post-Processing)
        def time_to_sec(ts):
            try:
                time_part = ts.split(" ")[1] if " " in ts else ts
                h, m, s = time_part.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except: return 0

        # 라디오 신호 데이터를 시간 순으로 예쁘게 정렬
        raw_signals.sort(key=lambda x: time_to_sec(x["time"]))

        # 시스템 로그(history)의 시간에 맞춰 가장 가까운 라디오 신호 매핑
        for event in history:
            evt_sec = time_to_sec(event["time"])
            best_details = {}

            # 이벤트 발생 시점 기준 가장 가까운 과거(최대 5초 이내)의 상세 신호 찾기
            for raw in reversed(raw_signals):
                raw_sec = time_to_sec(raw["time"])
                diff = evt_sec - raw_sec

                if 0 <= diff <= 5.0:  # 5초 이내 발생한 최신 라디오 신호만 결합
                    best_details = raw["details"]
                    break
                elif diff < 0:
                    continue # 라디오 로그가 더 미래에 찍혔다면 스킵 (정렬되어 있으므로 계속 탐색)

            if best_details:
                event["details"] = best_details.copy()

        return history

class DataUsageParser(BaseParser):
    def analyze(self, lines, global_uid_map=None):
        # 🚨 [신규] 외부(Orchestrator)에서 만든 완벽한 매핑 테이블 가져오기
        if global_uid_map is None:
            global_uid_map = {}

        usage_by_key = {}
        # 전달받은 전역 맵을 베이스로 깔고 시작
        uid_map = global_uid_map.copy()
        current_app_id_in_log = None
        current_key = None

        for line in lines:
            line_stripped = self.clean_line(line)

            # 1. 기존 UID 수집 로직 (혹시 누락된 최신 앱이 있을까봐 보조용으로 유지)
            if "NetdEventListenerService" in line_stripped or "DNS Requested by" in line_stripped:
                m_pkg = re.search(r'DNS Requested by\s+\d+,\s*(\d+)\(([^)]+)\)', line_stripped)
                if m_pkg: uid_map[m_pkg.group(1)] = m_pkg.group(2)

            if m_app_id := re.search(r'App ID:\s*(\d+)', line_stripped):
                current_app_id_in_log = m_app_id.group(1)

            if (m_package := re.search(r'Package:\s*([a-zA-Z0-9_.]+)', line_stripped)) and current_app_id_in_log:
                uid_map[current_app_id_in_log] = m_package.group(1)
                current_app_id_in_log = None

            if line_stripped.startswith("pkg,"):
                m_pkg_csv = re.match(r'^pkg,([^,]+),(\d+)', line_stripped)
                if m_pkg_csv:
                    uid_map[m_pkg_csv.group(2)] = m_pkg_csv.group(1)

            # 2. Network Identity 블록 진입 시 UID와 RAT 임시 저장
            if "transports={0}" in line_stripped and "metered=true" in line_stripped:
                m_uid = re.search(r'uid=(-\d+|\d+)', line_stripped)
                m_rat = re.search(r'ratType=(-\d+|\d+)', line_stripped)
                if m_uid and m_rat:
                    uid_val, rat_val = m_uid.group(1), m_rat.group(1)
                    if uid_val == "-1": continue
                    current_key = (uid_val, RAT_TYPE_MAP.get(rat_val, f"RAT_{rat_val}"))
                continue

            # 3. 시간대별 데이터 쪼개기
            if current_key and line_stripped.startswith("st="):
                m_bytes = DIAG_PATTERNS['NETSTAT_BYTES'].search(line_stripped)
                m_st = re.search(r'st=(\d+)', line_stripped)

                if m_bytes and m_st:
                    st_timestamp = int(m_st.group(1))
                    if len(str(st_timestamp)) > 11:
                        st_timestamp /= 1000.0

                    bucket_time_str = datetime.fromtimestamp(st_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    uid_val, rat_val = current_key
                    full_key = (uid_val, rat_val, bucket_time_str)

                    if full_key not in usage_by_key:
                        usage_by_key[full_key] = {"rx_bytes": 0, "tx_bytes": 0}

                    usage_by_key[full_key]["rx_bytes"] += int(m_bytes.group(1))
                    usage_by_key[full_key]["tx_bytes"] += int(m_bytes.group(2))

        # 4. 결과 조립
        report_data = []
        for (uid, rat, bucket_time), data in usage_by_key.items():
            total_bytes = data["rx_bytes"] + data["tx_bytes"]
            if total_bytes > 0:
                # 🚨 [핵심] 이제 uid_map에는 [PACKAGE INFO]에서 가져온 완벽한 앱 이름이 들어있습니다.
                app_name = {"-5": "모바일 핫스팟 (Tethering)", "-4": "삭제된 앱 (Removed)", "1000": "Android System (OS)", "0": "OS Kernel (Root)"}.get(uid, uid_map.get(uid, f"App_UID_{uid}"))
                report_data.append({
                    "time": bucket_time,
                    "uid": uid,
                    "app_name": app_name,
                    "rat": rat,
                    "total_mb": round(total_bytes / (1024 * 1024), 2),
                    "rx_mb": round(data["rx_bytes"] / (1024 * 1024), 2),
                    "tx_mb": round(data["tx_bytes"] / (1024 * 1024), 2)
                })

        return sorted(report_data, key=lambda x: (x["time"], -x["total_mb"]))

class DnsParser(BaseParser):
    def analyze(self, lines, global_uid_map=None):
        if global_uid_map is None:
            global_uid_map = {}

        dns_events = []
        for line in lines:
            if "DNS Requested by" in line:
                m = DIAG_PATTERNS['DNS_FULL'].search(line)
                if m:
                    time_str, uid, orig_app_name, rest = m.group('time'), m.group('uid'), m.group('app_name'), m.group('rest')

                    # 🚨 [핵심 수정] global_uid_map에 완벽한 이름이 있으면 그걸 쓰고, 없으면 기존 로그에서 뽑은 이름 유지
                    app_name = global_uid_map.get(uid, orig_app_name)

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
                        "app_name": app_name, # <-- 이제 깔끔한 패키지명이 들어갑니다.
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

        # 1. Date 추출용: "Date: 2026-03-26 11:20:40"
        date_re = re.compile(r'Date:\s*([\d-]+\s[\d:]+)')

        # 2. NITZ 핵심 추출용: "NITZ: 26/03/26,10:00:14+04,00"
        # 그룹1(날짜/시간): 26/03/26,10:00:14
        # 그룹2(타임존): +04
        # 그룹3(DST): 00
        nitz_re = re.compile(r'NITZ:\s*(\d{2}/\d{2}/\d{2},\d{2}:\d{2}:\d{2})([-+]\d{1,3}),(\d{1,2})')

        for line in lines:
            date_m = date_re.search(line)
            nitz_m = nitz_re.search(line)

            if nitz_m:
                log_time = date_m.group(1) if date_m else "Unknown"

                nitz_time_str = nitz_m.group(1) # 예: 26/03/26,10:00:14
                tz_str = nitz_m.group(2)        # 예: +04
                dst_str = nitz_m.group(3)       # 예: 00

                try:
                    tz_val = int(tz_str)
                    # 3GPP 표준: 타임존은 15분(Quarter Hour) 단위입니다.
                    # (+04 * 15분) / 60 = +1.0 시간 (UTC+1)
                    tz_hours = (tz_val * 15) / 60.0

                    sign = "+" if tz_val >= 0 else ""
                    tz_desc = f"UTC{sign}{tz_hours:g}시간"
                    dst_desc = "적용(+1h)" if dst_str != "00" else "미적용"
                except Exception:
                    tz_desc = "Unknown"
                    dst_desc = "Unknown"

                nitz_history.append({
                    "log_time": log_time,
                    "nitz_raw": nitz_m.group(0), # "NITZ: 26/03/26,10:00:14+04,00"
                    "timezone": tz_desc,
                    "dst_status": dst_desc
                })

        return nitz_history

class BinderWarningParser(BaseParser):
    """Binder 관련 '이벤트'와 '보조 문맥'을 분리해서 분석합니다.

    - analyze(): UI 테이블/팩트로 노출할 실제 Binder 문제 이벤트만 반환합니다.
    - build_context_summary(): LLM RCA 보조용 요약만 반환합니다. UI 테이블 행으로 넣지 않습니다.
    """

    DIRECT_EVENT_TYPES = {
        "THREAD_EXHAUSTION",
        "TRANSACTION_DELAY",
        "BINDER_DELAY",
        "BINDER_TRANSACTION_FAILURE",
        "BINDER_BUFFER_ERROR",
        "REPEATED_BINDER_DELAY",
    }

    def _extract_time(self, line_str):
        ts_m = re.search(r'\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}', line_str)
        return ts_m.group(0) if ts_m else line_str[:18].strip()

    def _severity_label(self, duration_ms):
        if duration_ms >= 5000:
            return "치명적"
        if duration_ms >= 3000:
            return "높음"
        return "주의"

    def analyze(self, lines):
        warnings = []
        delay_count_by_target = {}
        last_delay_event_by_target = {}
        seen_raw = set()

        for line in lines:
            line_str = line.strip()
            if not line_str or line_str in seen_raw:
                continue
            seen_raw.add(line_str)
            lower = line_str.lower()
            event_time = self._extract_time(line_str)

            # 1. Thread Exhaustion / Starved: 강한 장애 신호
            if "binder thread pool" in lower and ("is full" in lower or "starved for" in lower):
                starved_match = re.search(r'starved for (\d+)\s*ms', line_str, re.IGNORECASE)
                if starved_match:
                    delay_ms = int(starved_match.group(1))
                    desc = (
                        f"Binder thread pool starvation 감지: IPC 처리 스레드가 부족하여 "
                        f"{delay_ms}ms(약 {round(delay_ms / 1000, 1)}초) 대기했습니다. "
                        "ANR/Watchdog/system_server 지연과 시간 상관관계 확인이 필요합니다."
                    )
                else:
                    desc = (
                        "Binder thread pool 포화 감지. IPC 처리 자원 부족을 의미하는 강한 이상 신호이며, "
                        "동시간대 ANR/Watchdog/느린 Binder transaction 여부를 함께 확인해야 합니다."
                    )

                warnings.append({
                    "time": event_time,
                    "type": "THREAD_EXHAUSTION",
                    "desc": desc,
                    "raw": line_str
                })
                continue

            # 2. Binder transaction delay: 원인 후보/증상으로 표현
            if "binder transaction to" in line_str and "took" in line_str:
                try:
                    target_part = line_str.split("Binder transaction to ", 1)[1]
                    target = target_part.split()[0]
                    took_part = line_str.split("took ", 1)[1]
                    duration_str = "".join(filter(str.isdigit, took_part.split("ms", 1)[0]))
                    duration_ms = int(duration_str)

                    if duration_ms > 1000:
                        level = self._severity_label(duration_ms)
                        desc = (
                            f"[{target}] 대상 Binder transaction이 {duration_ms}ms"
                            f"(약 {round(duration_ms / 1000, 1)}초) 지연되었습니다. 심각도: {level}. "
                            "단독으로 Root Cause를 확정하지 말고, ANR/Watchdog/thread starvation/대상 서비스 재시작 여부와 교차 확인해야 합니다."
                        )
                        event = {
                            "time": event_time,
                            "type": "TRANSACTION_DELAY",
                            "desc": desc,
                            "raw": line_str
                        }
                        warnings.append(event)
                        delay_count_by_target[target] = delay_count_by_target.get(target, 0) + 1
                        last_delay_event_by_target[target] = event
                except Exception:
                    warnings.append({
                        "time": event_time,
                        "type": "TRANSACTION_DELAY",
                        "desc": "Binder transaction 지연 로그가 감지되었으나 target/duration 상세 추출에 실패했습니다. 원문 확인이 필요합니다.",
                        "raw": line_str
                    })
                continue

            # 3. binder_sample: 느린 IPC 샘플링 지표
            if "binder_sample" in line_str:
                sample_pattern = re.compile(r'binder_sample.*?\[(.*?),\s*(\d+),\s*(\d+),\s*([^,\]]+)')
                m = sample_pattern.search(line_str)
                if m:
                    interface, code, duration_ms, pkg = m.group(1), m.group(2), int(m.group(3)), m.group(4)
                    if duration_ms > 1000:
                        level = self._severity_label(duration_ms)
                        warnings.append({
                            "time": event_time,
                            "type": "BINDER_DELAY",
                            "desc": (
                                f"[{pkg}] 패키지의 {interface} Binder call이 {duration_ms}ms 지연되었습니다. "
                                f"심각도: {level}. 반복 발생 또는 ANR 시점 인접 여부 확인이 필요합니다."
                            ),
                            "raw": line_str
                        })
                continue

            # 4. Binder transaction failure 계열: 단순 지연보다 장애성이 강함
            if any(k in lower for k in [
                "deadobjectexception", "failed_transaction", "binder transaction failed",
                "transaction failed", "remoteexception"
            ]):
                warnings.append({
                    "time": event_time,
                    "type": "BINDER_TRANSACTION_FAILURE",
                    "desc": "Binder transaction failure/RemoteException 계열 로그 감지. 대상 프로세스 종료, 서비스 재시작, IPC 실패 가능성이 있어 전후 Crash/ANR 로그 확인이 필요합니다.",
                    "raw": line_str
                })
                continue

            # 5. Binder buffer / allocation 계열
            if any(k in lower for k in [
                "transactiontoolargeexception", "binder_alloc", "binder buffer",
                "no space left", "buffer allocation", "parcel size"
            ]):
                warnings.append({
                    "time": event_time,
                    "type": "BINDER_BUFFER_ERROR",
                    "desc": "Binder buffer/parcel 크기 관련 오류 감지. 대용량 parcel, buffer 부족 또는 TransactionTooLargeException 가능성이 있습니다.",
                    "raw": line_str
                })
                continue

        # 동일 target의 반복 지연은 별도 요약 이벤트 1건만 추가합니다.
        for target, cnt in delay_count_by_target.items():
            if cnt >= 3:
                last = last_delay_event_by_target.get(target, {})
                warnings.append({
                    "time": last.get("time", ""),
                    "type": "REPEATED_BINDER_DELAY",
                    "desc": f"[{target}] 대상 Binder transaction 지연이 {cnt}회 반복되었습니다. 단발성 지연보다 서비스 병목 가능성이 높아 ANR/Watchdog 시점과 비교가 필요합니다.",
                    "raw": last.get("raw", "")
                })

        return warnings

    def build_context_summary(self, context_lines, max_examples=12):
        """Binder RCA 보조용 문맥 요약입니다. 반환값은 UI 테이블이 아닌 LLM/KPI 보조 팩트에만 사용합니다."""
        if not context_lines:
            return {}

        categories = {
            "anr_or_input_timeout": [" anr", "am_anr", "application not responding", "input dispatching timed out"],
            "watchdog_or_system_server": ["watchdog", "system_server", "slow dispatch", "slow delivery"],
            "lock_contention": ["lock contention", "monitor contention", "blocked on", "waiting to lock", "held by"],
            "service_or_ipc_failure": ["deadobjectexception", "failed_transaction", "remoteexception", "service not responding"],
            "resource_pressure": ["cpu usage", "iowait", "lowmemorykiller", "lmkd", "memory pressure", "kswapd"],
            "telephony_nearby": ["rilj", "rild", "radio", "telephony", "ims", "datacall", "oos"],
        }
        summary = {"total_context_lines": len(context_lines), "signals": {}, "examples": {}}

        for name, keywords in categories.items():
            matched = []
            for line in context_lines:
                lower = line.lower()
                if any(k in lower for k in keywords):
                    matched.append(line.strip())
            if matched:
                summary["signals"][name] = len(matched)
                summary["examples"][name] = matched[-max_examples:]

        if summary["signals"]:
            checklist = []
            if summary["signals"].get("anr_or_input_timeout"):
                checklist.append("ANR/Input timeout 시점과 Binder 지연 시점의 시간 상관관계 확인")
            if summary["signals"].get("watchdog_or_system_server"):
                checklist.append("system_server Watchdog/slow dispatch 동반 여부 확인")
            if summary["signals"].get("lock_contention"):
                checklist.append("Lock/monitor contention이 Binder 응답 지연의 선행 원인인지 확인")
            if summary["signals"].get("service_or_ipc_failure"):
                checklist.append("대상 서비스 사망/재시작/RemoteException 여부 확인")
            if summary["signals"].get("resource_pressure"):
                checklist.append("CPU/iowait/memory pressure로 인한 전역 지연 가능성 확인")
            if summary["signals"].get("telephony_nearby"):
                checklist.append("RILJ/Telephony/IMS/DataCall/OOS 이벤트와 장애 시점 비교")
            summary["checklist"] = checklist

        return summary if summary["signals"] else {}
