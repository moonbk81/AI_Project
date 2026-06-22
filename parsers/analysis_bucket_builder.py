import re

class AnalysisBucketBuilder:
    BUCKET_NAMES = [
        'boot',
        'signal',
        'dns',
        'usage',
        'net_ts',
        'crash',
        'anr',
        'radio_power',
        'battery',
        'battery_thermal',
        'ntn',
        'datacall',
        'ims_sip',
        'sat_at',
        'internet_stall',
        'nitz',
        'native_crash',
        'binder',
        'binder_context',
        'rilj',
    ]

    CRASH_KEYWORDS = [
        "FATAL EXCEPTION", "Fatal signal", "AndroidRuntime", "am_crash",
        "force close", "Tombstone written to", "Build fingerprint:", "Abort message:",
        "Force CP CRASH", "Kernel panic", "Modem Not Responding", "CP MNR", "Watchdog",
    ]
    ANR_KEYWORDS = [
        "ANR", "am_anr", "Application Not Responding", "Input dispatching timed out",
        "Broadcast of Intent", "executing service", "traces.txt", "CPU usage from",
    ]
    RADIO_POWER_KEYWORDS = [
        "setRadioPower", "setRadioPowerForReason", "RADIO_POWER", "RIL_REQUEST_RADIO_POWER",
        "RADIO_OFF", "RADIO_ON", "airplane_mode_on", "AIRPLANE_MODE",
    ]
    BATTERY_KEYWORDS = [
        "Battery", "battery", "batterystats", "BatteryService", "HealthInfo",
        "level=", "plugged=", "temperature=", "voltage=",
    ]
    THERMAL_CONTEXT_KEYWORDS = [
        "ThermalEvent", "ThermalService", "ThermalManager", "thermalservice",
        "overheat", "throttling", "cooling device", "CoolingDevice",
    ]
    THERMAL_LINE_KEYWORDS = [
        "temperature mValue", "skin-therm", "battery-therm", "sec-battery",
        "WakeLock", "Wakelock", "wakelock held", "wake_lock",
    ]
    NTN_KEYWORDS = [
        "NTN", "ntn", "satellite", "Satellite", "NonTerrestrial", "non-terrestrial",
    ]
    DATACALL_KEYWORDS = [
        "DataCall", "data call", "SetupDataCall", "SETUP_DATA_CALL", "DEACTIVATE_DATA_CALL",
        "DcTracker", "DataNetwork", "TelephonyNetworkFactory", "ApnContext",
        "NO CARRIER", "authentication failed", "User authentication failed",
        "NOT_SPECIFIED", "E-PDN", "EPDN", "E_PDN",
    ]
    IMS_SIP_KEYWORDS = [
        "reSIProcate",  # reSIProcate 스택 로그 (SipReq/SipResp: CANCEL, ACK, PRACK, UPDATE 등 모두 포함)
        "SIP/2.0", "REGISTER sip:", "INVITE sip:", "BYE sip:", "CANCEL sip:",
        "P-CSCF", "ImsReasonInfo", "ImsPhoneConnection", "ImsCallSession",
        "createCallProfile", "onCallStarted", "onCallStartFailed",
    ]
    SAT_AT_KEYWORDS = [
        "AT+", "AT^", "AT$", "> AT", "< AT",
        "+CEREG", "+CREG", "+CGREG", "+COPS", "+CSQ",
    ]
    INTERNET_STALL_CONTEXT_KEYWORDS = [
        "Data Stall", "data stall", "validation failed",
        "PARTIAL_CONNECTIVITY", "NO_INTERNET", "EVENT_NETWORK_TESTED",
        "default network changed", "network lost",
    ]
    INTERNET_STALL_LINE_KEYWORDS = [
        "TcpSocketTracker", "PrivateDns", "NET_CAPABILITY_VALIDATED", "NetworkAgentInfo",
    ]
    NATIVE_CRASH_KEYWORDS = ["Fatal signal", "Abort mesage:", "Abort message:", "backtrace:"]

    # Binder UI 테이블에는 직접적인 Binder 문제 이벤트만 넣습니다.
    # 주변 문맥(ANR/Watchdog/CPU 등)은 별도 binder_context 버킷으로 분리하여
    # LLM RCA 보조 팩트에만 사용합니다. UI 테이블 폭증 방지가 목적입니다.
    BINDER_KEYWORDS = [
        "binder thread pool", "binder_sample", "Binder transaction to",
        "DeadObjectException", "FAILED_TRANSACTION", "binder transaction failed",
        "TransactionTooLargeException", "binder_alloc", "binder buffer",
        "am_kill", "am_wtf",
    ]
    BINDER_CONTEXT_KEYWORDS = [
        "ANR", "am_anr", "Application Not Responding", "Input dispatching timed out",
        "Watchdog", "system_server", "slow dispatch", "slow delivery",
        "lock contention", "monitor contention", "blocked on", "waiting to lock",
        "DeadObjectException", "FAILED_TRANSACTION", "RemoteException", "Service not responding",
        "CPU usage", "iowait", "lowmemorykiller", "lmkd", "memory pressure",
        "RILJ", "rild", "radio", "telephony", "IMS", "DataCall", "OOS",
    ]
    BINDER_CONTEXT_ANCHOR_KEYWORDS = [
        "binder thread pool", "binder_sample", "Binder transaction to",
        "DeadObjectException", "FAILED_TRANSACTION", "TransactionTooLargeException",
        "binder_alloc", "am_kill", "am_wtf",
    ]

    BASIC_USAGE_KEYWORDS = ["transports={0}", "metered=true", "DNS Requested", "pkg,", "st=", "App ID:", "Package:"]
    NET_TS_KEYWORDS = ["NetId", "DnsEvent", "TcpStats", "NetworkMonitor", "ConnectivityService"]
    RILJ_TAG_REGEX = re.compile(r'\b[VDIWEF](?:/|\s+)(?:RILJ|SEM_RILJ)\b', re.IGNORECASE)

    def __init__(self, add_context_window):
        self._add_context_window = add_context_window

    def build(self, lines):
        """
        parser마다 전체 dump를 반복 순회하지 않도록 1회 스캔으로 후보 라인 버킷을 만듭니다.
        call/oos parser는 상태 흐름 누락 위험이 있어 run_batch에서 계속 전체 lines를 사용합니다.
        """
        buckets = self._new_buckets()
        state = {
            'in_proxy_histogram': False,
        }

        for idx, line in enumerate(lines):
            self._collect_basic_buckets(buckets, line)
            self._collect_crash_anr_radio_buckets(buckets, lines, idx, line)
            self._collect_battery_thermal_buckets(buckets, lines, idx, line)
            self._collect_telephony_network_buckets(buckets, lines, idx, line)
            self._collect_native_crash_bucket(buckets, lines, idx, line)
            self._collect_binder_buckets(buckets, lines, idx, line, state)
            self._collect_rilj_bucket(buckets, line)

        return self._dedupe_and_print(buckets)

    def _new_buckets(self):
        return {name: [] for name in self.BUCKET_NAMES}

    def _collect_basic_buckets(self, buckets, line):
        if line.startswith("!@Boot"):
            buckets['boot'].append(line)

        if "EVENT_SIGNAL_LEVEL_INFO_CHANGED" in line or "NetworkSignalStrengthHandler" in line:
            buckets['signal'].append(line)

        if self._contains_any(line, self.BASIC_USAGE_KEYWORDS):
            buckets['usage'].append(line)

        if "DNS Requested" in line:
            buckets['dns'].append(line)

        if self._contains_any(line, self.NET_TS_KEYWORDS):
            buckets['net_ts'].append(line)

        if "nitz_status" in line:
            buckets['nitz'].append(line)

    def _collect_crash_anr_radio_buckets(self, buckets, lines, idx, line):
        if self._contains_any(line, self.CRASH_KEYWORDS):
            self._add_context_window(buckets, 'crash', lines, idx, window=80)

        if self._contains_any(line, self.ANR_KEYWORDS):
            self._add_context_window(buckets, 'anr', lines, idx, window=180)

        if self._contains_any(line, self.RADIO_POWER_KEYWORDS):
            self._add_context_window(buckets, 'radio_power', lines, idx, window=10)

    def _collect_battery_thermal_buckets(self, buckets, lines, idx, line):
        if self._contains_any(line, self.BATTERY_KEYWORDS):
            buckets['battery'].append(line)
            buckets['battery_thermal'].append(line)

        if self._contains_any(line, self.THERMAL_CONTEXT_KEYWORDS):
            self._add_context_window(buckets, 'battery_thermal', lines, idx, window=8)
        elif self._contains_any(line, self.THERMAL_LINE_KEYWORDS):
            buckets['battery_thermal'].append(line)

    def _collect_telephony_network_buckets(self, buckets, lines, idx, line):
        if self._contains_any(line, self.NTN_KEYWORDS):
            buckets['ntn'].append(line)

        if self._contains_any(line, self.DATACALL_KEYWORDS):
            # DataCall failure cause strings such as NO CARRIER / User authentication failed
            # can appear several lines away from SETUP_DATA_CALL, so keep a wider context.
            self._add_context_window(buckets, 'datacall', lines, idx, window=20)
            self._add_context_window(buckets, 'internet_stall', lines, idx, window=20)

        if self._contains_any(line, self.IMS_SIP_KEYWORDS):
            self._add_context_window(buckets, 'ims_sip', lines, idx, window=5)

        if self._contains_any(line, self.SAT_AT_KEYWORDS):
            buckets['sat_at'].append(line)

        if self._contains_any(line, self.INTERNET_STALL_CONTEXT_KEYWORDS):
            self._add_context_window(buckets, 'internet_stall', lines, idx, window=10)
        elif self._contains_any(line, self.INTERNET_STALL_LINE_KEYWORDS):
            buckets['internet_stall'].append(line)

    def _collect_native_crash_bucket(self, buckets, lines, idx, line):
        if self._contains_any(line, self.NATIVE_CRASH_KEYWORDS):
            self._add_context_window(buckets, 'native_crash', lines, idx, window=60)

    def _collect_binder_buckets(self, buckets, lines, idx, line, state):
        lower_line = line.lower()

        if "binderproxy descriptor histogram" in lower_line:
            state['in_proxy_histogram'] = True

        if state['in_proxy_histogram']:
            buckets['binder'].append(line)
            if (
                "critical dump took" in lower_line
                or "binderproxydumphelper" in lower_line
                or line.startswith("---------")
            ):
                state['in_proxy_histogram'] = False

        if self._contains_any(line, self.BINDER_KEYWORDS):
            buckets['binder'].append(line)

        # Binder context는 이벤트 주변의 제한된 문맥만 모읍니다.
        # context 라인을 binder_warnings에 넣으면 UI 상세 테이블이 수천 행으로 불어납니다.
        if self._contains_any(line, self.BINDER_CONTEXT_ANCHOR_KEYWORDS):
            self._collect_binder_context_window(buckets, lines, idx)
        elif self._contains_any_lower(line, self.BINDER_CONTEXT_KEYWORDS):
            # 앵커 없이 전역으로 잡히는 context는 과다 수집 방지를 위해 직접 append하지 않습니다.
            # ANR/Crash 전용 parser가 별도 bucket에서 처리합니다.
            pass

    def _collect_binder_context_window(self, buckets, lines, idx):
        start = max(0, idx - 20)
        end = min(len(lines), idx + 21)
        for ctx_line in lines[start:end]:
            if self._contains_any_lower(ctx_line, self.BINDER_CONTEXT_KEYWORDS):
                buckets['binder_context'].append(ctx_line)

    def _collect_rilj_bucket(self, buckets, line):
        if self.RILJ_TAG_REGEX.search(line):
            buckets['rilj'].append(line)

    def _dedupe_and_print(self, buckets):
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

    @staticmethod
    def _contains_any(line, keywords):
        return any(keyword in line for keyword in keywords)

    @staticmethod
    def _contains_any_lower(line, keywords):
        lower_line = line.lower()
        return any(keyword.lower() in lower_line for keyword in keywords)
