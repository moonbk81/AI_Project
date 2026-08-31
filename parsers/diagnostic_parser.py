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
    _INVALID_SIGNAL_VALUES = {"99", "255", "2147483647", "-2147483648"}

    def _field(self, text, name):
        match = re.search(rf'\b{name}\s*=\s*(-?\d+)', text, re.I)
        return match.group(1) if match else None

    def _dbm(self, value):
        if value is None or value in self._INVALID_SIGNAL_VALUES:
            return "Unknown"
        return f"{value} dBm" if value.startswith("-") else f"-{value} dBm"

    def _db(self, value):
        if value is None or value in self._INVALID_SIGNAL_VALUES:
            return "Unknown"
        return f"{value} dB" if value.startswith("-") else f"-{value} dB"

    def _tenths_db(self, value):
        if value is None or value in self._INVALID_SIGNAL_VALUES:
            return "Unknown"
        try:
            return f"{int(value) / 10.0} dB"
        except ValueError:
            return "Unknown"

    def _signed_db(self, value):
        if value is None or value in self._INVALID_SIGNAL_VALUES:
            return "Unknown"
        return f"{value} dB"

    def _slot_from_line(self, line):
        phone_match = re.search(r'\[PHONE(\d+)\]', line)
        if phone_match:
            return phone_match.group(1)

        tag_match = re.search(r'\b(?:SST|SSCtr|RILD2?|RILJ)-(\d+)\b', line)
        return tag_match.group(1) if tag_match else "0"

    def _raw_cell_signal(self, cell, name):
        match = re.search(rf'{name}:\s*\{{?\s*([^}}]+)', cell)
        return match.group(1).strip() if match else name

    def _parse_cell_signal_strength_samples(self, line):
        ts_m = re.search(r'\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}', line)
        if not ts_m:
            return []

        time_str = ts_m.group(0)
        slot = self._slot_from_line(line)
        samples = []

        lte_cells = re.findall(r'(CellInfoLte:\{.*?CellSignalStrengthLte:\s*\{?\s*.*?)(?=, CellInfo|] \[PHONE|\Z)', line)
        if not lte_cells and "CellSignalStrengthLte:" in line:
            lte_cells = re.findall(r'(CellSignalStrengthLte:\s*\{?\s*.*?)(?=, CellInfo|] \[PHONE|\Z)', line)
        if lte_cells:
            lte = next((cell for cell in lte_cells if "mRegistered=YES" in cell), lte_cells[0])
            details = {
                "RSRP": self._dbm(self._field(lte, "rsrp")),
                "RSRQ": self._db(self._field(lte, "rsrq")),
                "SINR": self._tenths_db(self._field(lte, "rssnr")),
                "RSSI": self._dbm(self._field(lte, "rssi")),
                "raw": self._raw_cell_signal(lte, "CellSignalStrengthLte"),
            }
            samples.append({
                "time": time_str, "slot": slot, "rat": "LTE",
                "level": self.safe_to_int(self._field(lte, "level")),
                "raw_info": details["raw"], "details": {"LTE": details},
            })

        nr_cells = re.findall(r'(CellInfoNr:\{.*?CellSignalStrengthNr:\s*\{?\s*.*?)(?=, CellInfo|] \[PHONE|\Z)', line)
        if not nr_cells and "CellSignalStrengthNr:" in line:
            nr_cells = re.findall(r'(CellSignalStrengthNr:\s*\{?\s*.*?)(?=, CellInfo|] \[PHONE|\Z)', line)
        if nr_cells:
            nr = next((cell for cell in nr_cells if "mRegistered=YES" in cell), nr_cells[0])
            details = {
                "RSRP": self._dbm(self._field(nr, "ssRsrp")),
                "RSRQ": self._db(self._field(nr, "ssRsrq")),
                "SINR": self._signed_db(self._field(nr, "ssSinr")),
                "raw": self._raw_cell_signal(nr, "CellSignalStrengthNr"),
            }
            samples.append({
                "time": time_str, "slot": slot, "rat": "NR",
                "level": self.safe_to_int(self._field(nr, "level")),
                "raw_info": details["raw"], "details": {"NR": details},
            })

        wcdma_cells = re.findall(r'(CellInfoWcdma:\{.*?CellSignalStrengthWcdma:\s*\{?\s*.*?)(?=, CellInfo|] \[PHONE|\Z)', line)
        if not wcdma_cells and "CellSignalStrengthWcdma:" in line:
            wcdma_cells = re.findall(r'(CellSignalStrengthWcdma:\s*\{?\s*.*?)(?=, CellInfo|] \[PHONE|\Z)', line)
        if wcdma_cells:
            wcdma = next((cell for cell in wcdma_cells if "mRegistered=YES" in cell), wcdma_cells[0])
            details = {
                "RSSI": self._dbm(self._field(wcdma, "rssi")),
                "RSCP": self._dbm(self._field(wcdma, "rscp")),
                "EcNo": self._db(self._field(wcdma, "ecno")),
                "raw": self._raw_cell_signal(wcdma, "CellSignalStrengthWcdma"),
            }
            samples.append({
                "time": time_str, "slot": slot, "rat": "WCDMA",
                "level": self.safe_to_int(self._field(wcdma, "level")),
                "raw_info": details["raw"], "details": {"WCDMA": details},
            })

        gsm_cells = re.findall(r'(CellInfoGsm:\{.*?CellSignalStrengthGsm:\s*\{?\s*.*?)(?=, CellInfo|] \[PHONE|\Z)', line)
        if not gsm_cells and "CellSignalStrengthGsm:" in line:
            gsm_cells = re.findall(r'(CellSignalStrengthGsm:\s*\{?\s*.*?)(?=, CellInfo|] \[PHONE|\Z)', line)
        if gsm_cells:
            gsm = next((cell for cell in gsm_cells if "mRegistered=YES" in cell), gsm_cells[0])
            details = {
                "RSSI": self._dbm(self._field(gsm, "rssi")),
                "BER": self._field(gsm, "ber") or "Unknown",
                "raw": self._raw_cell_signal(gsm, "CellSignalStrengthGsm"),
            }
            samples.append({
                "time": time_str, "slot": slot, "rat": "GSM",
                "level": self.safe_to_int(self._field(gsm, "level")),
                "raw_info": details["raw"], "details": {"GSM": details},
            })

        return samples

    def analyze(self, lines):
        history = []

        for line in lines:
            if (
                "CellSignalStrengthLte" in line
                or "CellSignalStrengthNr" in line
                or "CellSignalStrengthWcdma" in line
                or "CellSignalStrengthGsm" in line
            ):
                history.extend(self._parse_cell_signal_strength_samples(line))

        return history

class DataUsageParser(BaseParser):
    def analyze(self, lines, global_uid_map=None):
        # 🚨 [신규] 외부(Orchestrator)에서 만든 완벽한 매핑 테이블 가져오기
        if global_uid_map is None:
            global_uid_map = {}

        usage_by_key = {}
        uid_map = global_uid_map.copy()
        current_app_id_in_log = None
        current_key = None

        FAST_KEYWORDS = ["st=", "NetdEventListener", "DNS Requested", "App ID:", "Package:", "pkg,", "transports={0}"]

        for line in lines:
            if not any(k in line for k in FAST_KEYWORDS):
                continue
            if "rb=0" in line and "tb=0" in line:
                continue

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
                total_mb = round(total_bytes / (1024 * 1024), 2)
                rx_mb = round(data["rx_bytes"] / (1024 * 1024), 2)
                tx_mb = round(data["tx_bytes"] / (1024 * 1024), 2)

                if total_mb < 5.0:
                    continue
                # 🚨 [핵심] 이제 uid_map에는 [PACKAGE INFO]에서 가져온 완벽한 앱 이름이 들어있습니다.
                app_name = {"-5": "모바일 핫스팟 (Tethering)", "-4": "삭제된 앱 (Removed)", "1000": "Android System (OS)", "0": "OS Kernel (Root)"}.get(uid, uid_map.get(uid, f"App_UID_{uid}"))
                report_data.append({
                    "time": bucket_time,
                    "uid": uid,
                    "app_name": app_name,
                    "rat": rat,
                    "total_mb": total_mb,
                    "rx_mb": rx_mb,
                    "tx_mb": tx_mb
                })

        return sorted(report_data, key=lambda x: (x["time"], -x["total_mb"]))

class DnsParser(BaseParser):
    def analyze(self, lines, global_uid_map=None):
        if global_uid_map is None:
            global_uid_map = {}

        result = {
            "queries": [],
            "health_warnings": []
        }

        current_net_id = "Unknown"

        for line in lines:
            # NetId 추적 (어떤 네트워크에서 발생한 불량인지 알기 위함)
            if line.startswith("NetId:"):
                m_netid = re.search(r'NetId:\s*(\d+)', line)
                if m_netid:
                    current_net_id = m_netid.group(1)
                continue

            # 1. 일반 DNS 쿼리 수집 (기존 로직)
            if "DNS Requested by" in line:
                m = DIAG_PATTERNS['DNS_FULL'].search(line)
                if m:
                    time_str, net_id, uid, orig_app_name, rest = m.group('time'), m.group('net_id'), m.group('uid'), m.group('app_name') or '', m.group('rest')

                    app_name = global_uid_map.get(uid, orig_app_name) or f"UID_{uid}"
                    latency_ms = None
                    latency_match = re.search(r',\s*(\d+)\s*ms\b', rest, re.IGNORECASE)
                    if latency_match:
                        latency_ms = self.safe_to_int(latency_match.group(1))

                    if "SUCCESS" in rest.upper(): return_code = "SUCCESS"
                    else:
                        rc_match = re.search(r'(?:^|,\s*)(\d+)\(([^)]+)\)', rest)
                        if rc_match:
                            raw_code, status_text = rc_match.group(1), rc_match.group(2)
                            return_code = "SUCCESS" if raw_code == "0" else f"{status_text} (Code:{raw_code})".strip()
                            if re.search(r'isBlocked\s*=\s*true', rest, re.I):
                                return_code = f"BLOCKED (Code:{raw_code})"
                        else: return_code = "UNKNOWN"

                    result["queries"].append({
                        "time": time_str,
                        "net_id": net_id,
                        "uid": uid,
                        "app_name": app_name,
                        "return_code": return_code,
                        "latency_ms": latency_ms,
                        "raw_info": rest.strip()
                    })

            # 2. DNS 서버 건강 상태(Health) 분석
            elif "score{" in line or "score {" in line:
                m_ip = re.search(r'^\s*([\[\]a-fA-F0-9\.:]+):\d+', line)
                m_score = re.search(r'score\s*\{\s*([0-9.]+)\s*\}', line)
                m_timeout = re.search(r'TIMEOUT:(\d+)', line)
                m_latency = re.search(r'(\d+)ms', line)

                if m_ip and m_score:
                    ip = m_ip.group(1)
                    score = float(m_score.group(1))
                    timeout_cnt = int(m_timeout.group(1)) if m_timeout else 0
                    latency = self.safe_to_int(m_latency.group(1)) if m_latency else 0

                    # 임계치: 점수가 10점 이하이거나, 타임아웃이 10회 이상 발생한 경우
                    if score <= 10.0 or timeout_cnt >= 10:
                        desc = (
                            f"NetId {current_net_id}에 할당된 DNS 서버({ip})의 응답 불량이 감지되었습니다. "
                            f"(상태 점수: {score}점, 타임아웃: {timeout_cnt}회, 평균지연: {latency}ms). "
                            f"해당 서버로의 DNS 라우팅 실패가 초기 앱 인터넷 접속 지연(Internet Stall)의 근본 원인일 확률이 매우 높습니다."
                        )

                        result["health_warnings"].append({
                            "net_id": current_net_id,
                            "server_ip": ip,
                            "score": score,
                            "timeout_count": timeout_cnt,
                            "avg_latency_ms": latency,
                            "description": desc,
                            "raw_log": line.strip()
                        })

        return result

class CrashParser(BaseParser):
    def analyze(self, lines):
        crashes, is_cap, step, tmp = [], False, 0, None
        # 💡 커널 패닉 직전의 MNR 등 풍부한 단서를 잡기 위해 maxlen을 20으로 증가
        pre_ctx = deque(maxlen=20)

        for line in lines:
            clean_line = self.clean_line(line)
            if not clean_line: continue

            # 1. 타임스탬프 추출 (Logcat 시간 vs 커널 KTime)
            ts_m = RE_TIME.search(clean_line)
            ktime_m = re.search(r'\[\s*(\d+\.\d+)\s*\]', clean_line)

            if ts_m:
                ts = ts_m.group(0)
            elif ktime_m:
                ts = f"KTime: {ktime_m.group(1)}"
            else:
                ts = "00-00 00:00:00.000"

            is_fatal_app = DIAG_PATTERNS['FATAL_APP'].search(clean_line) if 'FATAL_APP' in DIAG_PATTERNS else False
            is_fatal_sys = DIAG_PATTERNS['FATAL_SYS'].search(clean_line) if 'FATAL_SYS' in DIAG_PATTERNS else False

            # 💡 커널 패닉 감지 정규식
            is_kernel_panic = re.search(r'Kernel panic(?: - not syncing:)?\s*(.*)', clean_line)

            if is_fatal_app or is_fatal_sys or is_kernel_panic:
                if is_cap and tmp:
                    # 동일 시간대 연속 크래시 방어
                    if tmp["time"][:14] == ts[:14] or (ts.startswith("KTime:") and tmp["time"] == ts):
                        tmp["exception_info"] += f"\n[Chain Crash] {clean_line} "
                        step = 1
                        fatal_info_count = 0
                        continue
                    else:
                        if self.get_context_fn: tmp["cross_context_logs"] = self.get_context_fn(lines, tmp["time"])
                        crashes.append(tmp)

                is_cap = True
                step = 1
                fatal_info_count = 0

                if is_kernel_panic:
                    panic_reason = is_kernel_panic.group(1).strip()

                    # 💡 [핵심 최적화] 패닉 직전 문맥(pre_ctx)에서 MNR 단서가 있으면 사유로 즉시 끌어올림!
                    mnr_hints = [l for l in pre_ctx if "Modem Not Responding" in l or "Force CP CRASH" in l]
                    if mnr_hints:
                        panic_reason = "[CP MNR 감지] " + panic_reason

                    tmp = {
                        "time": ts,
                        "trigger": clean_line,
                        "process": "Kernel / Modem",
                        "exception_info": f"Kernel Panic: {panic_reason}",
                        "top_method": panic_reason if panic_reason else "Kernel Panic",
                        "call_stack": [],
                        "context": list(pre_ctx),
                        "is_kernel": True # 💡 명시적 플래그 (이름이 바뀌어도 로직 안 풀림)
                    }
                    step = 2
                else:
                    tmp = {
                        "time": ts,
                        "trigger": clean_line,
                        "process": "system_server" if is_fatal_sys else "Unknown",
                        "exception_info": "",
                        "top_method": "Unknown",
                        "call_stack": [],
                        "context": list(pre_ctx)[-5:],
                        "is_kernel": False
                    }
                continue

            if is_cap and tmp:
                # 💡 플래그를 통해 커널 로그 수집을 끝까지 유지
                if tmp.get("is_kernel"):
                    # 커널 패닉 프로세스 수집 (Comm: ESAR 등)
                    if "Comm:" in clean_line and "Kernel" in tmp["process"]:
                        comm_match = re.search(r'Comm:\s*([^\s]+)', clean_line)
                        if comm_match:
                            tmp["process"] = f"Kernel ({comm_match.group(1)})"

                    tmp["call_stack"].append(clean_line)
                    fatal_info_count += 1

                    # 커널 로그는 end trace 가 있거나 30줄 정도 모으면 자름
                    if fatal_info_count > 30 or "---[ end trace" in clean_line or "Rebooting in" in clean_line:
                        if self.get_context_fn: tmp["cross_context_logs"] = self.get_context_fn(lines, tmp["time"])
                        crashes.append(tmp)
                        is_cap = False
                        tmp = None

                else:
                    # 기존 자바(앱/시스템) 크래시 스택 수집
                    if step == 1:
                        if "Process:" in clean_line:
                            proc_match = re.search(r"Process:\s*([^\s,]+)", clean_line)
                            if proc_match:
                                new_proc = proc_match.group(1).strip()
                                if new_proc != "zygote" and tmp["process"] == "Unknown":
                                    tmp["process"] = new_proc
                                elif new_proc != "zygote" and new_proc not in tmp["process"]:
                                    tmp["process"] += f", {new_proc}"
                            step = 2
                            continue
                        elif DIAG_PATTERNS['STACK_LINE'].search(clean_line) or clean_line.startswith("at ") or "Exception" in clean_line:
                            step = 2

                    if step == 2:
                        if DIAG_PATTERNS['STACK_LINE'].search(clean_line) or "at " in clean_line:
                            if tmp["top_method"] == "Unknown":
                                method_match = re.search(r'at\s+([^\(]+)', clean_line)
                                if method_match:
                                    tmp["top_method"] = method_match.group(1).strip()

                            tmp["call_stack"].append(clean_line)
                            fatal_info_count = 0
                        else:
                            if len(tmp["call_stack"]) > 0:
                                fatal_info_count += 1
                                if fatal_info_count > 3:
                                    if self.get_context_fn: tmp["cross_context_logs"] = self.get_context_fn(lines, tmp["time"])
                                    crashes.append(tmp)
                                    is_cap = False
                                    tmp = None
                            else:
                                tmp["exception_info"] += clean_line + " "
                                fatal_info_count += 1
                                if fatal_info_count > 15:
                                    if self.get_context_fn: tmp["cross_context_logs"] = self.get_context_fn(lines, tmp["time"])
                                    crashes.append(tmp)
                                    is_cap = False
                                    tmp = None

            pre_ctx.append(line.strip())

        # 루프 종료 후 남은 크래시 담기
        if is_cap and tmp and (len(tmp["call_stack"]) > 0 or len(tmp["exception_info"]) > 0):
            if self.get_context_fn: tmp["cross_context_logs"] = self.get_context_fn(lines, tmp["time"])
            crashes.append(tmp)

        return crashes

class AnrParser(BaseParser):
    def analyze(self, lines, target_package=None):
        anr_list = []
        current_anr = None

        activity_anr_re = re.compile(r'ActivityManager:\s+ANR in\s+(\S+)(?:\s+\(.*?PID\s+(\d+)\))?', re.I)
        app_not_responding_re = re.compile(r'Application is not responding:\s+(.+)', re.I)
        am_anr_re = re.compile(r'am_anr\s*:\s*\[\s*\d+\s*,\s*(\d+)\s*,\s*([^,\]]+)\s*,\s*[^,\]]+\s*,\s*(.+)\]$', re.I)
        completed_anr_re = re.compile(r'ActivityManager:\s+Completed ANR of\s+(\S+)', re.I)
        anr_pid_re = re.compile(r'ActivityManager:\s+PID:\s+(\d+)')
        anr_reason_re = re.compile(r'ActivityManager:\s+Reason:\s+(.+)')
        cmd_line_re = re.compile(r'Cmd line:\s+(.+)')
        package_re = re.compile(r'\b(?:[a-zA-Z_]\w*\.)+[a-zA-Z_]\w*(?::[a-zA-Z_]\w*)?\b')

        cpu_re = re.compile(r'CPU usage from|CPU usage since|Load:')
        system_server_re = re.compile(r'system_server|Watchdog|ActivityManager|InputDispatcher|WindowManager')
        io_re = re.compile(r'\biowait\b|\bblocked\b|slow operation|StrictMode|fsync|disk|I/O|io ', re.I)
        max_context_seconds = 30.0

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
        current_anr_start_sec = None

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

        def time_to_sec(clean_line):
            ts_m = RE_TIME.search(clean_line)
            if not ts_m:
                return None
            try:
                _, time_part = ts_m.group(0).split(" ")
                hour, minute, second = time_part.split(":")
                return int(hour) * 3600 + int(minute) * 60 + float(second)
            except (ValueError, TypeError):
                return None

        def is_in_context_window(clean_line):
            if current_anr_start_sec is None:
                return True

            line_sec = time_to_sec(clean_line)
            if line_sec is None:
                return True

            delta = line_sec - current_anr_start_sec
            if delta < 0:
                delta += 24 * 3600
            return delta <= max_context_seconds

        def extract_application_process(detail):
            first_token = detail.split()[0].strip(",;") if detail.split() else ""
            if package_re.fullmatch(first_token):
                return first_token

            pkg_m = package_re.search(detail)
            if pkg_m:
                return pkg_m.group(0)
            return None

        def extract_intent_action(reason):
            intent_m = re.search(r'act=([^\s\}]+)', reason or "")
            return intent_m.group(1) if intent_m else "Unknown"

        def event_time_to_sec(event):
            time_str = event.get("time")
            if not time_str or time_str == "Unknown":
                return None
            return time_to_sec(time_str)

        def time_delta_seconds(left, right):
            if left is None or right is None:
                return None
            delta = abs(left - right)
            return min(delta, (24 * 3600) - delta)

        def is_same_anr(existing, incoming):
            if existing.get("process") != incoming.get("process"):
                return False

            existing_pid = (existing.get("process_info", {}) or {}).get("pid") or existing.get("logcat_pid")
            incoming_pid = (incoming.get("process_info", {}) or {}).get("pid") or incoming.get("logcat_pid")
            if existing_pid not in (None, "Unknown") and incoming_pid not in (None, "Unknown") and existing_pid != incoming_pid:
                return False

            existing_action = existing.get("intent_action")
            incoming_action = incoming.get("intent_action")
            existing_reason = existing.get("reason")
            incoming_reason = incoming.get("reason")
            same_reason = (
                existing_reason == incoming_reason
                or (
                    existing_action not in (None, "Unknown")
                    and incoming_action not in (None, "Unknown")
                    and existing_action == incoming_action
                )
            )
            if not same_reason:
                return False

            delta = time_delta_seconds(event_time_to_sec(existing), event_time_to_sec(incoming))
            return delta is None or delta <= 5.0

        def merge_unique_logs(left, right, limit=80):
            merged = []
            seen = set()
            for item in (left or []) + (right or []):
                if item in seen:
                    continue
                seen.add(item)
                merged.append(item)
            return merged[-limit:]

        def merge_transactions(left, right, limit=20):
            merged = []
            seen = set()
            for item in (left or []) + (right or []):
                if not isinstance(item, dict):
                    continue
                key = item.get("raw") or tuple(sorted(item.items()))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
            return merged[-limit:]

        def prefer_known(current, candidate):
            return candidate if current in (None, "Unknown", "", []) and candidate not in (None, "Unknown", "", []) else current

        def merge_anr(existing, incoming):
            existing["time"] = min(
                [t for t in [existing.get("time"), incoming.get("time")] if t and t != "Unknown"],
                default=existing.get("time", "Unknown")
            )
            existing["reason"] = prefer_known(existing.get("reason"), incoming.get("reason"))
            existing["intent_action"] = prefer_known(existing.get("intent_action"), incoming.get("intent_action"))

            existing_pid = (existing.get("process_info", {}) or {}).get("pid")
            incoming_pid = (incoming.get("process_info", {}) or {}).get("pid")
            if existing_pid in (None, "Unknown") and incoming_pid not in (None, "Unknown"):
                existing.setdefault("process_info", {})["pid"] = incoming_pid

            if incoming.get("main", {}).get("stack") and not existing.get("main", {}).get("stack"):
                existing["main"] = incoming["main"]
            if incoming.get("lock_chain", {}).get("blocker_thread") and not existing.get("lock_chain", {}).get("blocker_thread"):
                existing["lock_chain"] = incoming["lock_chain"]

            existing_summary = existing.setdefault("analysis_summary", {})
            for key, value in (incoming.get("analysis_summary", {}) or {}).items():
                if isinstance(value, bool):
                    existing_summary[key] = existing_summary.get(key, False) or value
                elif key == "evidence_level" and value == "TRACE_INCLUDED":
                    existing_summary[key] = value
                else:
                    existing_summary[key] = prefer_known(existing_summary.get(key), value)

            existing["active_binder_transactions"] = merge_transactions(
                existing.get("active_binder_transactions"),
                incoming.get("active_binder_transactions"),
            )

            existing_context = existing.setdefault("context_analysis", {})
            incoming_context = incoming.get("context_analysis", {}) or {}
            for key in ("cpu_logs", "system_server_logs", "io_logs"):
                existing_context[key] = merge_unique_logs(existing_context.get(key), incoming_context.get(key))
            existing["raw_context_analysis"] = existing_context
            existing["raw_log"] = merge_unique_logs(
                existing.get("raw_log", "").splitlines(),
                incoming.get("raw_log", "").splitlines(),
                limit=40
            )
            existing["raw_log"] = "\n".join(existing["raw_log"]) + ("\n" if existing["raw_log"] else "")
            existing["pre_anr_logcat"] = existing.get("pre_anr_logcat") or incoming.get("pre_anr_logcat", [])

        def append_or_merge_anr(incoming):
            for existing in anr_list:
                if is_same_anr(existing, incoming):
                    merge_anr(existing, incoming)
                    return
            anr_list.append(incoming)

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
            if current_anr and not is_in_context_window(clean_line):
                return

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

            # 🚨 수정: 최상위 payload에 프로세스, PID, Intent Action을 확정적으로 노출
            final_pid = target_pid or current_anr.get("logcat_pid", "Unknown")

            context_analysis = {
                "cpu_logs": cpu_logs[-80:],
                "system_server_logs": system_server_logs[-80:],
                "io_logs": io_logs[-80:]
            }

            current_anr.update({
                "process_info": {
                    "name": current_anr.get("process"),
                    "pid": final_pid
                },
                "intent_action": current_anr.get("intent_action", "Unknown"), # LLM이 바로 볼 수 있게 승격!
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
                "context_analysis": context_analysis,
                "raw_context_analysis": context_analysis
            })

            # LLM 인지 과부하 방지를 위해 임시 필드 삭제
            current_anr.pop("logcat_pid", None)

            append_or_merge_anr(current_anr)

        reset_trace_state()

        for i, line in enumerate(lines):
            clean_line = self.clean_line(line)
            collect_context_hint(clean_line)

            # 1. ANR 시작 감지
            process_name = None
            logcat_pid = "Unknown"
            reason = "Unknown"

            if anr_m := activity_anr_re.search(clean_line):
                process_name = anr_m.group(1)
                logcat_pid = anr_m.group(2) or "Unknown"
            elif anr_m := am_anr_re.search(clean_line):
                logcat_pid = anr_m.group(1)
                process_name = anr_m.group(2).strip()
                reason = anr_m.group(3).strip()
            elif anr_m := app_not_responding_re.search(clean_line):
                process_name = extract_application_process(anr_m.group(1))

            if process_name:
                if current_anr:
                    finalize_current_anr()

                reset_trace_state()
                current_anr_start_sec = time_to_sec(clean_line)

                if target_package and process_name != target_package:
                    current_anr = None
                    current_anr_start_sec = None
                    pre_context.append(clean_line)
                    continue

                current_anr = {
                    "time": "Unknown",
                    "process": process_name,
                    "logcat_pid": logcat_pid,
                    "reason": reason,
                    "intent_action": extract_intent_action(reason),
                    "raw_log": clean_line + "\n",
                    "pre_anr_logcat": list(pre_context)
                }
                if logcat_pid != "Unknown":
                    target_pid = logcat_pid

                if ts_m := RE_TIME.search(clean_line):
                    current_anr["time"] = ts_m.group(0)

                pre_context.append(clean_line)
                continue

            if not current_anr:
                pre_context.append(clean_line)
                continue

            if completed_m := completed_anr_re.search(clean_line):
                if completed_m.group(1) == current_anr.get("process"):
                    current_anr["raw_log"] += clean_line + "\n"
                    finalize_current_anr()
                    current_anr = None
                    current_anr_start_sec = None
                    reset_trace_state()
                    pre_context.append(clean_line)
                    continue

            # 🚨 추가: traces.txt 없이 Logcat만 있을 때를 대비한 PID 확보
            if pid_m := anr_pid_re.search(clean_line):
                if current_anr["logcat_pid"] == "Unknown":
                    current_anr["logcat_pid"] = pid_m.group(1)
                    if not target_pid:
                        target_pid = pid_m.group(1)
                current_anr["raw_log"] += clean_line + "\n"

            # 2. Reason 및 Intent Action 수집
            if reason_m := anr_reason_re.search(clean_line):
                reason_str = reason_m.group(1)
                current_anr["reason"] = reason_str

                # 🚨 핵심: act= 인텐트 액션명을 명시적으로 파싱하여 최상위 키에 할당
                current_anr["intent_action"] = extract_intent_action(reason_str)

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
        "SYSTEM_KILL",
        "SYSTEM_WTF",
        "BINDER_ONEWAY_SPAM", # 💡 신규 이벤트 타입 추가
    }
    STARVATION_RCA_THRESHOLD_MS = 1000
    REPEATED_DELAY_WINDOW_SECONDS = 60
    PROXY_LEAK_THRESHOLD = 1000
    BUFFER_CORROBORATION_WINDOW_SECONDS = 30
    # 커널이 버퍼를 못 잡아준 신호. 호출 하나가 아니라 대상 프로세스 전체가 영향을 받는다.
    BUFFER_EXHAUSTION_MARKERS = (
        "no space left",
        "buffer allocation failed",
        "failed to allocate",
    )

    # am_kill 사유는 대부분 AMS 가 쓸모없어진 프로세스를 회수하며 남기는 정상 기록이다.
    # 장애로 읽어야 하는 사유만 따로 세워두고, 나머지는 판단을 보류한다.
    BENIGN_KILL_REASONS = (
        "isolated not needed",
        "remove task",
        "stop ",
        "force stop",
        "force-stop",
        "user stopped",
        "swap low and too many cached",
        "empty for",
        "empty #",
        "cached #",
        "background",
        "finishing",
    )
    RCA_KILL_REASONS = (
        "too many binders sent to system",
        "crash",
        "anr",
        "excessive cpu",
        "watchdog",
        "depends on",
    )

    def _extract_time(self, line_str):
        ts_m = re.search(r'\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}', line_str)
        return ts_m.group(0) if ts_m else line_str[:18].strip()

    def _classify_kill_reason(self, reason):
        """am_kill 사유를 양성 회수 / RCA 근거 / 판단 보류로 나눈다."""
        text = (reason or "").strip().lower()
        if any(k in text for k in self.BENIGN_KILL_REASONS):
            return "benign"
        if any(k in text for k in self.RCA_KILL_REASONS):
            return "rca_candidate"
        return "event"

    def _severity_label(self, duration_ms):
        if duration_ms >= 5000:
            return "치명적"
        if duration_ms >= 3000:
            return "높음"
        return "주의"

    def _time_to_sec(self, time_str):
        ts_m = re.search(r'\d{2}-\d{2}\s(\d{2}):(\d{2}):(\d{2}\.\d{3})', str(time_str))
        if not ts_m:
            return None
        hour, minute, second = ts_m.groups()
        return int(hour) * 3600 + int(minute) * 60 + float(second)

    def _is_repeated_within_window(self, events):
        if len(events) < 3:
            return False

        seconds = [self._time_to_sec(event.get("time")) for event in events]
        for idx in range(0, len(seconds) - 2):
            start = seconds[idx]
            end = seconds[idx + 2]
            if start is None or end is None:
                continue
            delta = end - start
            if delta < 0:
                delta += 24 * 3600
            if delta <= self.REPEATED_DELAY_WINDOW_SECONDS:
                return True
        return False

    def analyze(self, lines):
        warnings = []
        delay_events_by_target = {}
        last_delay_event_by_target = {}
        anr_seconds = []
        seen_raw = set()
        in_proxy_histogram = False
        current_histogram = []
        histogram_time = ""
        histogram_line_count = 0

        for line in lines:
            line_str = line.strip()
            if not line_str or line_str in seen_raw:
                continue
            seen_raw.add(line_str)
            lower = line_str.lower()
            event_time = self._extract_time(line_str)

            # 버퍼 에러를 뒷받침할 근거로만 쓴다. ANR 자체는 여기서 이벤트로 만들지 않는다.
            if "am_anr" in lower or "application not responding" in lower:
                anr_second = self._time_to_sec(event_time)
                if anr_second is not None:
                    anr_seconds.append(anr_second)

            if "am_kill" in line_str:
                match = re.search(r'am_kill\s*:\s*\[\d+,\d+,([^,]+),-?\d+,\s*([^\]]+)\]', line_str)
                if match:
                    process = match.group(1)
                    reason = match.group(2)
                    kill_role = self._classify_kill_reason(reason)
                    if kill_role == "benign":
                        desc = (
                            f"ActivityManager 프로세스 회수 이벤트. 대상 프로세스: {process}, 사유: {reason}. "
                            "바인딩이 끊긴 isolated 프로세스나 종료된 task 를 정리하는 정상 동작이므로, "
                            "장애/강제 종료 근거나 Root Cause 로 인용하지 않습니다."
                        )
                    elif kill_role == "rca_candidate":
                        desc = (
                            f"시스템(ActivityManager) 강제 종료 이벤트 감지. 대상 프로세스: {process}, 사유: {reason}. "
                            "사유 자체가 장애를 가리키므로 동시간대 Crash/ANR/Binder/메모리 상태와 함께 원인으로 검토합니다."
                        )
                    else:
                        desc = (
                            f"시스템(ActivityManager) 강제 종료 이벤트 감지. 대상 프로세스: {process}, 사유: {reason}. "
                            "Crash/ANR로 단정하지 말고 동시간대 Binder/메모리/프로세스 상태와 교차 확인해야 합니다."
                        )
                    warnings.append({
                        "time": event_time,
                        "type": "SYSTEM_KILL",
                        "process": process,
                        "kill_reason": reason,
                        "desc": desc,
                        "raw": line_str,
                        "evidence_role": "benign_event" if kill_role == "benign" else kill_role,
                        "rca_candidate": kill_role == "rca_candidate",
                        "cross_context_logs": self.get_context_fn(lines, event_time) if self.get_context_fn else []
                    })
                continue

            if "am_wtf" in line_str:
                match = re.search(r'am_wtf\s*:\s*\[\d+,\d+,([^,]+),', line_str)
                process = match.group(1) if match else "Unknown"
                from_match = re.search(r'from\s+(?:\d+:)?([a-zA-Z0-9\._]+)', line_str)
                if from_match:
                    process = from_match.group(1)

                warnings.append({
                    "time": event_time,
                    "type": "SYSTEM_WTF",
                    "process": process,
                    "desc": f"시스템 WTF(What a Terrible Failure) 이벤트 감지. 대상 프로세스: {process}. 심각한 시스템 상태 이상 신호이지만, Native Crash/ANR로 단정하지 말고 전후 로그와 교차 확인해야 합니다.",
                    "raw": line_str,
                    "cross_context_logs": self.get_context_fn(lines, event_time) if self.get_context_fn else []
                })
                continue

            if "binderproxy descriptor histogram" in lower:
                in_proxy_histogram = True
                current_histogram = []
                histogram_time = event_time
                histogram_line_count = 0
                continue

            if in_proxy_histogram:
                histogram_line_count += 1
                if "critical dump took" in lower or histogram_line_count > 100:
                    in_proxy_histogram = False
                    if current_histogram:
                        current_histogram.sort(key=lambda x: x[1], reverse=True)
                        max_count = current_histogram[0][1]
                        details = ", ".join([f"{name} ({cnt}개)" for name, cnt in current_histogram])
                        raw_logs = "\n".join([f"  {name} x{cnt}" for name, cnt in current_histogram])

                        warnings.append({
                            "time": histogram_time,
                            "type": "BINDER_PROXY_HISTOGRAM",
                            "max_count": max_count,
                            "evidence_role": "rca_candidate" if max_count > self.PROXY_LEAK_THRESHOLD else "state_dump",
                            "rca_candidate": max_count > self.PROXY_LEAK_THRESHOLD,
                            "desc": f"Binder Proxy 객체 상태 덤프: {details}",
                            "raw": raw_logs
                        })
                    continue

                match = re.search(r'(?:#\d+:\s*)?([a-zA-Z_][a-zA-Z0-9\.\$]+)\s*x\s*(\d+)', line_str)
                if match:
                    descriptor = match.group(1).strip()
                    count = int(match.group(2))
                    current_histogram.append((descriptor, count))
                continue

            # 1. Thread Exhaustion / Starved
            if "binder thread pool" in lower and ("is full" in lower or "starved for" in lower):
                starved_match = re.search(r'starved for (\d+)\s*ms', line_str, re.IGNORECASE)
                is_full = "is full" in lower
                if starved_match:
                    delay_ms = int(starved_match.group(1))
                    is_rca_candidate = delay_ms >= self.STARVATION_RCA_THRESHOLD_MS
                    if is_rca_candidate:
                        desc = f"Binder thread pool starvation 감지: IPC 처리 스레드가 부족하여 {delay_ms}ms(약 {round(delay_ms / 1000, 1)}초) 대기했습니다. ANR/Watchdog/system_server 지연과 시간 상관관계 확인이 필요합니다."
                    else:
                        desc = f"짧은 Binder thread pool starvation 감지: {delay_ms}ms 대기했습니다. 단독 Root Cause로 보지 말고 주변 지연/반복 여부 확인용 보조 신호로만 사용합니다."
                else:
                    is_rca_candidate = is_full
                    desc = "Binder thread pool 포화 감지. IPC 처리 자원 부족을 의미하는 강한 이상 신호이며, 동시간대 ANR/Watchdog/느린 Binder transaction 여부를 함께 확인해야 합니다."

                warnings.append({
                    "time": event_time,
                    "type": "THREAD_EXHAUSTION",
                    "desc": desc,
                    "raw": line_str,
                    "evidence_role": "rca_candidate" if is_rca_candidate or is_full else "secondary_signal",
                    "rca_candidate": is_rca_candidate or is_full
                })
                continue

            # 2. Binder transaction delay
            if "binder transaction to" in lower and "took" in lower:
                try:
                    tx_m = re.search(
                        r'binder transaction to\s+(?P<target>.*?)(?:,\s*function:\s*(?P<function>.*?))?,\s*code:\s*(?P<code>\d+),\s*took\s*(?P<duration>\d+)\s*ms',
                        line_str,
                        re.IGNORECASE
                    )
                    if not tx_m:
                        tx_m = re.search(
                            r'binder transaction to\s+(?P<target>\S+).*?took\s*(?P<duration>\d+)\s*ms',
                            line_str,
                            re.IGNORECASE
                        )
                    if not tx_m:
                        raise ValueError("unrecognized binder transaction delay format")

                    target = (tx_m.group("target") or "Unknown").strip().strip(",") or "Unknown"
                    duration_ms = int(tx_m.group("duration"))

                    if duration_ms > 1000:
                        level = self._severity_label(duration_ms)
                        desc = f"[{target}] 대상 Binder transaction이 {duration_ms}ms(약 {round(duration_ms / 1000, 1)}초) 지연되었습니다. 심각도: {level}. 단독으로 Root Cause를 확정하지 말고, ANR/Watchdog/thread starvation/대상 서비스 재시작 여부와 교차 확인해야 합니다."
                        event = {
                            "time": event_time,
                            "type": "TRANSACTION_DELAY",
                            "desc": desc,
                            "raw": line_str,
                            "evidence_role": "secondary_signal",
                            "rca_candidate": False
                        }
                        warnings.append(event)
                        delay_events_by_target.setdefault(target, []).append(event)
                        last_delay_event_by_target[target] = event
                except Exception:
                    warnings.append({
                        "time": event_time,
                        "type": "TRANSACTION_DELAY",
                        "desc": "Binder transaction 지연 로그가 감지되었으나 target/duration 상세 추출에 실패했습니다. 원문 확인이 필요합니다.",
                        "raw": line_str,
                        "evidence_role": "secondary_signal",
                        "rca_candidate": False
                    })
                continue

            # 3. binder_sample
            if "binder_sample" in line_str:
                sample_pattern = re.compile(r'binder_sample.*?\[(.*?),\s*(\d+),\s*(\d+),\s*([^,\]]+)')
                m = sample_pattern.search(line_str)
                if m:
                    interface, duration_ms, pkg = m.group(1), int(m.group(3)), m.group(4)
                    if duration_ms > 1000:
                        level = self._severity_label(duration_ms)
                        warnings.append({
                            "time": event_time,
                            "type": "BINDER_DELAY",
                            "desc": f"[{pkg}] 패키지의 {interface} Binder call이 {duration_ms}ms 지연되었습니다. 심각도: {level}. 반복 발생 또는 ANR 시점 인접 여부 확인이 필요합니다.",
                            "raw": line_str,
                            "evidence_role": "secondary_signal",
                            "rca_candidate": False
                        })
                continue

            # 4. Binder transaction failure 계열 (버퍼 고갈 라인은 5번에서 다룬다)
            if any(k in lower for k in [
                "deadobjectexception", "failed_transaction", "binder transaction failed",
                "binder transaction failure", "transaction failed", "transaction failure",
                "remoteexception"
            ]) and not any(k in lower for k in self.BUFFER_EXHAUSTION_MARKERS):
                warnings.append({
                    "time": event_time,
                    "type": "BINDER_TRANSACTION_FAILURE",
                    "desc": "Binder transaction failure/RemoteException 계열 로그 감지. 이는 상대 프로세스 종료, 서비스 재시작, endpoint 소멸 이후 나타나는 보조 증상일 수 있으므로 단독으로 Binder 병목/리소스 고갈/Root Cause로 판단하지 않습니다. 전후 Crash/ANR/am_kill/thread starvation 근거가 있을 때만 연관성을 검토합니다.",
                    "raw": line_str,
                    "evidence_role": "secondary_symptom",
                    "rca_candidate": False
                })
                continue

            # 5. Binder buffer / allocation 계열 (No space left on device 포함)
            if any(k in lower for k in ["transactiontoolargeexception", "binder_alloc", "binder buffer", "no space left", "buffer allocation", "parcel size"]):
                # 💡 6. 신규 추가: 커널 레벨의 Binder Oneway Spamming 감지
                if "spamming" in lower and "way" in lower:
                    # 커널 타임스탬프 (KTime) 추출 시도
                    ktime_match = re.search(r'\[\s*(\d+\.\d+)\s*\]', line_str)
                    spam_time = f"KTime: {ktime_match.group(1)}" if ktime_match else event_time

                    spam_match = re.search(r'binder_alloc:\s*(\d+):\s*pid\s*(\d+)\s*spamming\s+one\s*way\?', line_str)
                    if spam_match:
                        target_pid = spam_match.group(1)
                        sender_pid = spam_match.group(2)

                        size_match = re.search(r'total size of (\d+)', line_str)
                        total_size = size_match.group(1) if size_match else "Unknown"

                        warnings.append({
                            "time": spam_time,
                            "type": "BINDER_ONEWAY_SPAM",
                            "desc": f"[Caller PID: {sender_pid}] 프로세스가 [Callee PID: {target_pid}] 방향으로 비동기(Oneway) 트랜잭션을 과도하게 전송하고 있습니다(Spamming 감지). (버퍼 점유: {total_size} bytes). 이로 인해 대상 프로세스에 No space left on device (-28) 에러가 유발됩니다.",
                            "raw": line_str,
                            "evidence_role": "rca_candidate",
                            "rca_candidate": True
                        })
                    continue

	                # 일반 버퍼 에러 처리 (Spamming이 아닌 경우)
                is_secondary_no_vma = "binder_alloc_buf" in lower and "no vma" in lower
                is_buffer_exhaustion = any(k in lower for k in self.BUFFER_EXHAUSTION_MARKERS)
                # TransactionTooLargeException 은 그 호출 하나가 실패하고 끝나는 국소 증상이다.
                # system_server 상대이거나 동시간대 킬/ANR 이 있을 때만(아래 후처리) 원인 후보로 올린다.
                is_too_large = "transactiontoolargeexception" in lower
                hits_system_server = any(k in lower for k in ["system_server", "systemserver", "activitymanager"])
                # binder 가 null 이라 원격에 일부러 예외를 던지는 가드 로그. parcel 크기와 무관하다.
                is_null_binder = "nullbinder" in lower

                if is_null_binder:
                    desc = (
                        "NullBinder 가드 로그. 대상 binder 가 null 이라 호출부에 TransactionTooLargeException 을 "
                        "던진 것으로, parcel 크기나 buffer 고갈과 무관합니다. Root Cause 근거로 쓰지 않습니다."
                    )
                    evidence_role, rca_candidate = "secondary_symptom", False
                elif is_buffer_exhaustion or (is_too_large and hits_system_server):
                    desc = "Binder buffer 고갈/대용량 parcel 오류 감지. 대상 프로세스의 IPC 전반이 막힐 수 있어 원인 후보로 봅니다."
                    evidence_role, rca_candidate = "rca_candidate", True
                elif is_too_large:
                    desc = (
                        "TransactionTooLargeException 감지. 해당 IPC 호출 하나가 parcel 크기 한계로 실패한 국소 증상이라, "
                        "동시간대 강제 종료/ANR 이나 buffer 고갈 근거 없이는 단독 Root Cause 로 단정하지 않습니다."
                    )
                    evidence_role, rca_candidate = "secondary_symptom", False
                elif is_secondary_no_vma:
                    desc = "binder_alloc_buf no vma 감지. 대상 프로세스 종료나 mmap 소멸 이후 나타나는 후속 증상일 수 있으므로 단독 Binder buffer Root Cause로 단정하지 않습니다."
                    evidence_role, rca_candidate = "secondary_symptom", False
                else:
                    desc = "Binder buffer/parcel 크기 관련 로그 감지. 전후 Crash/ANR/thread starvation 근거와 함께 볼 보조 신호입니다."
                    evidence_role, rca_candidate = "secondary_signal", False

                warnings.append({
                    "time": event_time,
                    "type": "BINDER_BUFFER_ERROR",
                    "desc": desc,
                    "raw": line_str,
                    "evidence_role": evidence_role,
                    "rca_candidate": rca_candidate
                })
                continue

        # 동일 target의 반복 지연 처리
        for target, events in delay_events_by_target.items():
            if self._is_repeated_within_window(events):
                last = last_delay_event_by_target.get(target, {})
                warnings.append({
                    "time": last.get("time", ""),
                    "type": "REPEATED_BINDER_DELAY",
                    "desc": f"[{target}] 대상 Binder transaction 지연이 {len(events)}회 반복되었습니다({self.REPEATED_DELAY_WINDOW_SECONDS}초 이내 3회 이상). 단발성 지연보다 서비스 병목 가능성이 높아 ANR/Watchdog 시점과 비교가 필요합니다.",
                    "raw": last.get("raw", ""),
                    "evidence_role": "secondary_signal",
                    "rca_candidate": False
                })

        self._promote_corroborated_buffer_errors(warnings, anr_seconds)
        return warnings

    def _promote_corroborated_buffer_errors(self, warnings, anr_seconds):
        """TransactionTooLarge 는 같은 구간에 실제 킬/ANR/starvation 이 있을 때만 원인 후보다."""
        pending = [
            w for w in warnings
            if w.get("type") == "BINDER_BUFFER_ERROR"
            and not w.get("rca_candidate")
            and "transactiontoolargeexception" in str(w.get("raw", "")).lower()
            and "nullbinder" not in str(w.get("raw", "")).lower()
        ]
        if not pending:
            return

        corroborations = [
            self._time_to_sec(w.get("time"))
            for w in warnings
            if w.get("rca_candidate") and w.get("type") in ("SYSTEM_KILL", "THREAD_EXHAUSTION")
        ]
        corroborations = [t for t in corroborations if t is not None] + list(anr_seconds)
        if not corroborations:
            return

        for event in pending:
            when = self._time_to_sec(event.get("time"))
            if when is None:
                continue
            if any(abs(when - t) <= self.BUFFER_CORROBORATION_WINDOW_SECONDS for t in corroborations):
                event["evidence_role"] = "rca_candidate"
                event["rca_candidate"] = True
                event["desc"] += (
                    f" 다만 전후 {self.BUFFER_CORROBORATION_WINDOW_SECONDS}초 안에 강제 종료/ANR/thread starvation 이 "
                    "함께 확인되어 원인 후보로 올립니다."
                )

    def build_context_summary(self, context_lines, max_examples=12):
        # ... (기존 코드와 동일하게 유지)
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
            if summary["signals"].get("anr_or_input_timeout"): checklist.append("ANR/Input timeout 시점과 Binder 지연 시점의 시간 상관관계 확인")
            if summary["signals"].get("watchdog_or_system_server"): checklist.append("system_server Watchdog/slow dispatch 동반 여부 확인")
            if summary["signals"].get("lock_contention"): checklist.append("Lock/monitor contention이 Binder 응답 지연의 선행 원인인지 확인")
            if summary["signals"].get("service_or_ipc_failure"): checklist.append("대상 서비스 사망/재시작/RemoteException 여부 확인")
            if summary["signals"].get("resource_pressure"): checklist.append("CPU/iowait/memory pressure로 인한 전역 지연 가능성 확인")
            if summary["signals"].get("telephony_nearby"): checklist.append("RILJ/Telephony/IMS/DataCall/OOS 이벤트와 장애 시점 비교")
            summary["checklist"] = checklist

        return summary if summary["signals"] else {}

class BuildInfoParser(BaseParser):
    def analyze(self, lines):
        build_info = {
            "model_name": "Unknown",
            "build_fingerprint": "Unknown",
            "bootloader": "Unknown",
            "radio": "Unknown",
            "network": "Unknown",
            "android_sdk": "Unknown",
            "hardware": "Unknown",
            "kernel": "Unknown"
        }

        for line in lines[:50]:
            clean_line = line.strip()

            if clean_line.startswith("Build fingerprint:"):
                val = clean_line.split(":", 1)[1].strip().strip("‘’'\"")
                build_info["build_fingerprint"] = val

                # 핑거프린트에서 모델/프로젝트 코드명 추출 (예: samsung/h8qksx/...)
                parts = val.split('/')
                if len(parts) > 1:
                    build_info["model_name"] = parts[1]

            elif clean_line.startswith("Build:"):
                build_info["build_id"] = clean_line.split(":", 1)[1].strip()

            elif clean_line.startswith("Bootloader:"):
                build_info["bootloader"] = clean_line.split(":", 1)[1].strip()

            elif clean_line.startswith("Radio:"):
                build_info["radio"] = clean_line.split(":", 1)[1].strip()

            elif clean_line.startswith("Network:"):
                build_info["network"] = clean_line.split(":", 1)[1].strip().strip(",")

            elif clean_line.startswith("Android SDK version:"):
                build_info["android_sdk"] = clean_line.split(":", 1)[1].strip()

            elif clean_line.startswith("Kernel:"):
                build_info["kernel"] = clean_line.split(":", 1)[1].strip()

            elif "boot.hardware" in clean_line:
                m = re.search(r'boot\.hardware\s*=\s*[“"”]?([a-zA-Z0-9_]+)', clean_line)
                if m:
                    build_info["hardware"] = m.group(1) # 결과: qcom

        return build_info
