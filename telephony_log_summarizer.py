import re
import os
import json
import argparse
from collections import deque
from datetime import datetime, timedelta
from telephony_constants import CALL_FAIL_REASON_MAP, RAT_TYPE_MAP, VENDER_FAIL_REASON_MAP

class TelephonyLogSummarizer:
    def __init__(self, file_path):
        self.file_path = file_path
        self.re_time = re.compile(r'\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}')
        self.re_tag = re.compile(r'[VDIWE]\s+([a-zA-Z0-9_\-]+)\s*(?=:)', re.I)

        # [Call/OOS Patterns]
        self.patterns = {
            'CS_START': re.compile(r'RILJ\s+:\s+\[\d+\]>\s(?:DIAL)|RILJ\s+:\s+\[UNSL\]<\sUNSOL_CALL_RING', re.I),
            'PS_START': re.compile(r'IPF.*>\s*(?:createCallProfile)|IPF.*onIncomingCall', re.I),
            'CONN_ID': re.compile(r'(?:ImsPhoneConnection|ImsPhoneCallTracker).*telecomCallID:\s*([^\s,]+)', re.I),
            'END_EV': re.compile(r'\[IPCN(\d*)\]>\s*close|\<\s*LAST_CALL_FAIL_CAUSE', re.I),
            'FAIL_EV': re.compile(r'(onCallStartFailed|onCallHoldFailed|onCallResumeFailed)', re.I),
            'REJECT_EV': re.compile(r'IPF.*>\s*reject\s*\{reason:\s*(\w+)', re.I),
            'SST_POLL': re.compile(r'Poll ServiceState done', re.I),
            'IMS_REASON': re.compile(r'ImsReasonInfo\s*::\s*\{(\d+)\s*:\s*(\w+)'),
            'CS_REASON': re.compile(r'LAST_CALL_FAIL_CAUSE.*?causeCode:\s*(\d+)\s+vendorCause:\s*(\d+)')
        }

        # [SST Fields]
        self.re_sst_fields = {
            'v_reg': re.compile(r'm?VoiceRegState\s*=\s*([^,\s]+)', re.I),
            'd_reg': re.compile(r'mDataRegState\s*=\s*([^,\s]+)', re.I),
            'rat': re.compile(r'm?RadioTechnology\s*=\s*([^,\s]+)', re.I),
            'op_long': re.compile(r'm?OperatorAlphaLong\s*=\s*([^,\s]+)', re.I),
            'op_short': re.compile(r'm?OperatorAlphaShort\s*=\s*([^,\s]+)', re.I),
            'is_emergency': re.compile(r'm?IsEmergencyOnly\s*=\s*([^,\s]+)', re.I),
            'rej_cause': re.compile(r'm?RejectCause\s*=\s*([^,\s]+)', re.I)
        }

        self.radio_power_error_keywords = [
            'GENERIC_FAILURE', 'RADIO_NOT_AVAILABLE',
            'REQUEST_NOT_SUPPORTED', 'INVALID_ARGUMENTS', 'INTERNAL_ERR',
            'MODEM_ERR', 'FAILURE', 'ERROR'
        ]

        self.re_radio_power_req = re.compile(
            r'(?P<timestamp>\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+'
            r'radio\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+'
            r'(?P<level>[VDIWEFS])\s+RILJ\s*:\s*'
            r'\[(?P<seq>\d+)\]\s*>\s*RADIO_POWER\s+'
            r'on\s*=\s*(?P<on>\w+)\s+'
            r'forEmergencyCall\s*=\s*(?P<for_emergency>\w+)\s+'
            r'preferredForEmergencyCall\s*=\s*(?P<preferred_emergency>\w+)\s+'
            r'\[(?P<phone>PHONE\d+)\]'
        )

        self.re_radio_power_resp = re.compile(
            r'(?P<timestamp>\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+'
            r'radio\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+'
            r'(?P<level>[VDIWEFS])\s+RILJ\s*:\s*'
            r'\[(?P<seq>\d+)\]\s*<\s*RADIO_POWER\s*'
            r'(?P<content>.*)'
        )
        # 🚨 [신규 추가] Boot Stat 파싱용 정규식 (!@Boot로 시작하고 뒤에 숫자 3개가 띄어쓰기로 있는 패턴)
        self.re_boot_event = re.compile(r'^((!@Boot:|!@Boot_SVC|!@Boot_DEBUG).*?)\s+(\d+)\s+(\d+)\s+(\d+)', re.I)
        # [신규 추가] 안테나 레벨 파싱용 정규식
        self.re_signal_level = re.compile(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?\[(\d+)\] EVENT_SIGNAL_LEVEL_INFO_CHANGED - SignalBarInfo\{\s*(.*?)\s*\}')

        # [신규 추가] Netstats (데이터 사용량) 파싱용 정규식
        # 셀룰러(transports={0})이고 과금(metered=true)되는 식별자에서 UID 추출
        self.re_netstat_ident = re.compile(r'ident=\[\{.*?metered=true.*?transports=\{0\}\}\].*?uid=(-\d+|\d+)')
        # 데이터 사용량 라인 (rb: Rx Bytes, tb: Tx Bytes) 추출
        self.re_netstat_bytes = re.compile(r'rb=(\d+)\s+rp=\d+\s+tb=(\d+)')

        self.valid_tags = {
            'RILD', 'RILD2', 'RILJ', 'IPF', 'IMS', 'VoLTE', 'SST', 'ServiceState',
            'SignalStrength', 'ServiceStateTracker', 'ImsPhoneCallTracker',
            'ImsPhoneConnection', 'SST-1', 'SST-0'
        }
        self.common_excludes = ['keep-alive', 'handlePollStateResultMessage', 'getCarrierNameDisplayBitmask']
        self.tag_specific_excludes = {
            'RILD': [
                    'SCREEN_STATE', 'GET_OPERATOR', 'WAKE_LOCK', 'BATTERY_LEVEL',
                    'nv::', 'ProcessIpcMessageReceived', 'GetIpcMessage',
                    'IsRegisteredNetworkType', 'ServiceMode', 'SvcMode', 'IpcProtocol GR',
                    'OEM', 'GetRxData','Signal', 'RSSI', 'Dbm', 'RefreshHandler', '[B]',
                    '3gRove', 'mWeak', 'lte_sig', 'IpcRx', 'DoRoute', 'Onprocessing', '-MGR', 'Request', 'serviceType',
                    'MakeData', '[*]', 'IpcModem', 'PsRegistration', 'Pdn', 'RRC_STATE',
                    'RegistrationState', 'UnsolRespFilter', 'Screen', 'hysteresis', 'CellInfo', 'earfcn', 'IsRegistered',
                    'Rsrp', 'BuildSolicited', 'PhysicalChannel', 'DataCall', 'Interface', 'SSAC', 'RrcState',
                    'ACTIVITY_INFO', 'BIG_DATA', 'IpcProtocol41', 'ProcessSingleIpcMessageReceived',
                    'NITZ', 'Location', 'PS_REGISTRATION', 'SetEmergencyState'],
            'RILJ': [
                'UNSOL_PHYSICAL_CHANNEL_CONFIG', 'SET_SIGNAL_STRENGTH_REPORTING_CRITERIA',
                'SET_UNSOLICITED_RESPONSE_FILTER', 'SEND_DEVICE_STATE', 'Sending ack',
                'GET_BARRING_INFO', 'UNSOL_RESPONSE_NETWORK_STATE_CHANGED', 'processResponse',
                'OPERATOR', 'QUERY_NETWORK_SELECTION_MODE', 'VOICE_REGISTRATION_STATE',
                'DATA_REGISTRATION_STATE'
            ]
        }
        self.ps_exclude_tags = {'RILD', 'RILD2', 'RILJ'}
        self.re_hex_data = re.compile(r'([0-9a-fA-F]{2}\s){3,}')
        self.network_exclude_tags = {'ImsPhoneConnection', 'ImsPhoneCallTracker'}

        self.re_pid_line = re.compile(r'----- pid (\d+) at ', re.I)
        self.re_cmd_phone = re.compile(r'Cmd line:\s+com\.android\.phone', re.I)
        self.re_thread_header = re.compile(r'^"(.*?)".*?(?:sysTid|tid)=(\d+)', re.I)
        self.re_lock_held = re.compile(r'waiting to lock <(.*?)>.*?held by thread (\d+)', re.I)
        self.section_anr_traces = re.compile(r'VM TRACES AT LAST ANR', re.I)
        self.re_lock_held_by = re.compile(r'waiting to lock <(.*?)>.*?held by thread (\d+)', re.I)
        self.re_outgoing = re.compile(r'outgoing transaction (\d+):(\d+) to (\d+):(\d+) code (\d+)', re.I)
        self.re_fatal_app = re.compile(r'FATAL EXCEPTION:\s+main', re.I)
        self.re_fatal_sys = re.compile(r'FATAL EXCEPTION IN PROCESS:\s+main', re.I)
        self.re_proc_phone = re.compile(r'Process:\s+com\.android\.phone', re.I)
        self.re_stack_line = re.compile(r'^\s*(at\s+|Caused\s+by:)', re.I)

    # =========================================================================
    # 🚀 [해시맵 인덱싱] 수십억 번의 연산을 O(1) 딕셔너리 검색으로 최적화
    # =========================================================================
    def _get_surrounding_context_logs(self, lines, target_time_str, window_seconds=3, max_lines=150):
        # 1. 파이프라인 가동 후 단 1번만 실행되는 '찾아보기(인덱스)' 생성 로직 (약 0.3초 소요)
        if not hasattr(self, '_time_index'):
            self._time_index = {}
            for line in lines:
                if len(line) > 15:
                    # 앞 14글자 추출 (예: "04-12 14:10:05")
                    t_str = line[:14]
                    # 무의미한 텍스트 제외하고 날짜 포맷 형태일 때만 사전에 등록
                    if t_str[2] == '-' and t_str[5] == ' ':
                        if t_str not in self._time_index:
                            self._time_index[t_str] = []
                        self._time_index[t_str].append(line.strip())

        # 2. 타겟 시간 변환
        if not target_time_str or target_time_str == "00-00 00:00:00.000":
            return []

        base_time_str = target_time_str.split('.')[0] if '.' in target_time_str else target_time_str
        current_year = datetime.now().year

        try:
            target_dt = datetime.strptime(f"{current_year}-{base_time_str}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return []

        cross_context_logs = []

        # 3. 전체 파일을 뒤지지 않고, 딕셔너리에서 필요한 시간의 로그만 '즉시' 꺼내옴! (0.0001초 컷)
        for offset in range(-window_seconds, window_seconds + 1):
            win_str = (target_dt + timedelta(seconds=offset)).strftime("%m-%d %H:%M:%S")
            if win_str in self._time_index:
                cross_context_logs.extend(self._time_index[win_str])

        # 최대 라인 수 제한
        if len(cross_context_logs) > max_lines:
            return cross_context_logs[-max_lines:]

        return cross_context_logs

    def _parse_sst(self, content, key):
        match = self.re_sst_fields[key].search(content)
        if match:
            val = match.group(1).strip().rstrip(')')
            if "(" in val and ")" not in val: val += ")"
            return val
        return "Unknown"

    def analyze_data_usage(self, lines):
        # 🚨 [수정됨] key를 (uid, rat) 튜플로 사용하여 통신망별로 분리해서 저장합니다.
        usage_by_key = {}
        current_key = None

        uid_map = {}
        re_dns_pkg = re.compile(r'DNS Requested by\s+\d+,\s*(\d+)\(([^)]+)\)')

        for line in lines:
            line_stripped = line.strip()

            # 1. 패키지명 <-> UID 매핑 정보 수집 (Netd 로그 활용)
            if "NetdEventListenerService" in line_stripped or "DNS Requested by" in line_stripped:
                m_pkg = re_dns_pkg.search(line_stripped)
                if m_pkg:
                    uid_map[m_pkg.group(1)] = m_pkg.group(2)

            # 2. 데이터 사용량 수집 (dumpsys netstats 구역)
            # 조건: 과금되는(metered=true) 셀룰러(transports={0}) 트래픽
            if "transports={0}" in line_stripped and "metered=true" in line_stripped:
                m_uid = re.search(r'uid=(-\d+|\d+)', line_stripped)
                m_rat = re.search(r'ratType=(-\d+|\d+)', line_stripped)

                if m_uid and m_rat:
                    uid_val = m_uid.group(1)
                    rat_val = m_rat.group(1)

                    # 🚨 안드로이드 상수를 친숙한 통신망 이름으로 변환
                    rat_name = RAT_TYPE_MAP.get(rat_val, f"RAT_{rat_val}")
                    current_key = (uid_val, rat_name)
                    if current_key not in usage_by_key:
                        usage_by_key[current_key] = {"rx_bytes": 0, "tx_bytes": 0}
                continue

            if current_key and line_stripped.startswith("st="):
                m_bytes = re.search(r'rb=(\d+)\s+rp=\d+\s+tb=(\d+)', line_stripped)
                if m_bytes:
                    usage_by_key[current_key]["rx_bytes"] += int(m_bytes.group(1))
                    usage_by_key[current_key]["tx_bytes"] += int(m_bytes.group(2))

        # 3. MB 단위 변환 및 리포트 생성
        report_data = []
        for (uid, rat), data in usage_by_key.items():
            total_bytes = data["rx_bytes"] + data["tx_bytes"]
            if total_bytes > 0:
                total_mb = round(total_bytes / (1024 * 1024), 2)

                if uid == "-5": app_name = "모바일 핫스팟 (Tethering)"
                elif uid == "-4": app_name = "삭제된 앱 (Removed)"
                elif uid == "1000": app_name = "Android System (OS)"
                elif uid == "0": app_name = "OS Kernel (Root)"
                elif uid in uid_map: app_name = uid_map[uid]
                else: app_name = f"App_UID_{uid}"

                report_data.append({
                    "uid": uid,
                    "app_name": app_name,
                    "rat": rat,                 # 🚨 통신망 정보 추가!
                    "total_mb": total_mb,
                    "rx_mb": round(data["rx_bytes"] / (1024 * 1024), 2),
                    "tx_mb": round(data["tx_bytes"] / (1024 * 1024), 2)
                })

        # 내림차순 정렬
        report_data.sort(key=lambda x: x["total_mb"], reverse=True)
        return report_data

    # 🚨 [신규 추가] Boot Stat 텍스트를 파싱하여 Dictionary List로 반환
    def analyze_boot_stat(self, lines):
        boot_events = []
        for line in lines:
            clean_line = line.strip()
            # 빠르게 필터링하기 위해 startswith 사용
            if clean_line.startswith("!@Boot"):
                match = self.re_boot_event.search(clean_line)
                if match:
                    boot_events.append({
                        "Event": match.group(1).strip(),
                        "Time_ms": int(match.group(3)),
                        "Ktime_ms": int(match.group(4)),
                        "Delta_ms": int(match.group(5))
                    })
        return boot_events

    def analyze_signal_level(self, lines):
        history = []
        for line in lines:
            if "EVENT_SIGNAL_LEVEL_INFO_CHANGED" in line:
                m = self.re_signal_level.search(line)
                if m:
                    time_str = m.group(1)
                    slot = m.group(2)
                    info = m.group(3).strip() # 예: "lteLevel=2 nrLevel=2"

                    # 🚨 "no level" 인 경우 RAT를 'NO_SVC'로 지정하여 0칸 처리
                    if "no level" in info.lower():
                        history.append({
                            "time": time_str,
                            "slot": slot,
                            "rat": "NO_SVC",
                            "level": 0,
                            "raw_info": info
                        })
                    else:
                        # 🚨 띄어쓰기로 쪼갠 뒤, 각각의 RAT(lte, nr, wcdma 등)별로 기록
                        for item in info.split():
                            if '=' in item:
                                k, v = item.split('=')
                                # 'lteLevel' -> 'LTE', 'nrLevel' -> 'NR' 로 깔끔하게 변환
                                rat_name = k.replace('Level', '').upper()
                                try:
                                    history.append({
                                        "time": time_str,
                                        "slot": slot,
                                        "rat": rat_name,
                                        "level": int(v),
                                        "raw_info": info
                                    })
                                except: pass
        return history

    def analyze_radio_power(self, lines):
        requests = {}
        responses = {}
        results = []

        for line in lines:
            req_match = self.re_radio_power_req.search(line)
            if req_match:
                seq = req_match.group('seq')
                requests[seq] = {
                    'timestamp': req_match.group('timestamp'),
                    'seq': seq,
                    'on': req_match.group('on').lower() == 'true',
                    'for_emergency': req_match.group('for_emergency').lower() == 'true',
                    'preferred_emergency': req_match.group('preferred_emergency').lower() == 'true',
                    'phone': req_match.group('phone'),
                    'raw_line': line.strip()
                }
                continue

            resp_match = self.re_radio_power_resp.search(line)
            if resp_match:
                seq = resp_match.group('seq')
                content = resp_match.group('content').strip()
                is_error = any(kw.upper() in content.upper() for kw in self.radio_power_error_keywords)
                phone_match = re.search(r'\[(PHONE\d+)\]', content)
                phone = phone_match.group(1) if phone_match else ''

                error_msg = ''
                if is_error:
                    for kw in self.radio_power_error_keywords:
                        if kw.upper() in content.upper():
                            error_msg = kw; break

                responses[seq] = {
                    'timestamp': resp_match.group('timestamp'),
                    'seq': seq,
                    'phone': phone,
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
                'phone': req['phone'],
                'on': req['on'],
                'for_emergency': req['for_emergency'],
                'preferred_emergency': req['preferred_emergency'],
                'success': success,
                'error_msg': resp['error_msg'] if resp else 'NO_RESPONSE',
                'request_raw': req['raw_line'],
                'response_raw': resp['raw_line'] if resp else None
            }

            # [기능 통합 1] Radio Power가 실패(에러)한 경우 동시간대 교차 로그 수집
            if not success:
                err_time = result['response_time'] or result['request_time']
                result['cross_context_logs'] = self._get_surrounding_context_logs(lines, err_time)

            results.append(result)

        return results

    def analyze_telephony(self, lines):
        all_sessions, oos_history = [], []
        current_session, last_v, last_d = None, None, None
        last_slot_states = {"0": {"v": None, "d": None}, "1": {"v": None, "d": None}}
        target_phone_id = None
        pre_context = deque(maxlen=50)
        in_radio = False

        for i, line in enumerate(lines):
            clean_line = line.strip()
            ts_m = self.re_time.search(clean_line)
            ts = ts_m.group(0) if ts_m else "00-00 00:00:00.000"

            if "logcat -b radio" in line: in_radio = True; continue
            if in_radio and "was the duration of 'RADIO LOG'" in line: in_radio = False; continue

            if in_radio:
                tag_m = self.re_tag.search(line)
                tag = tag_m.group(1).strip() if tag_m else None
                if not tag or tag not in self.valid_tags: continue

                # OOS 분석
                if self.patterns['SST_POLL'].search(clean_line) and "newSS={" in clean_line:
                    ss_data = clean_line.split("newSS={")[1].rsplit("}", 1)[0]
                    v_reg, d_reg = self._parse_sst(ss_data, 'v_reg'), self._parse_sst(ss_data, 'd_reg')

                    slot_id = "1" if ('RILD2' in tag or 'SST-1' in tag or 'PHONE1' in clean_line) else "0"
                    prev = last_slot_states[slot_id]
                    if (v_reg[0] != prev["v"] or d_reg[0] != prev["d"]):
                        recent_logs = [l for l in list(pre_context) if not (any(t in l for t in self.network_exclude_tags))]
                        context_summary = " ".join(recent_logs[-20:]).lower()

                        prev_in_service = (prev["v"] == "0" and prev["d"] == "0")
                        now_in_service = (v_reg[0] == "0" and d_reg[0] == "0")

                        if not prev_in_service and now_in_service: event_type = "OOS_RECOVER"
                        elif prev_in_service and not now_in_service: event_type = "OOS_ENTER"
                        else: event_type = "OOS_STATE_CHANGE"

                        reason = "Unknown"
                        rej = self._parse_sst(ss_data, 'rej_cause')
                        if rej != "0" and rej != "Unknown": reason = f"NW_REJECT_CAUSE_{rej}"
                        elif "rrc connection release" in context_summary: reason = "RRC_RELEASE_BY_NW"
                        elif "out_of_service" in context_summary or "no_service" in context_summary: reason = "SIGNAL_LOSS_OR_SHADOW_AREA"
                        if v_reg[0] == "0" or d_reg[0] =="0": reason = "None"

                        oos_event = {
                            "time": ts,
                            "slotId": slot_id,
                            "event_type": event_type,
                            "voice_reg": v_reg,
                            "data_reg": d_reg,
                            "rat": self._parse_sst(ss_data, 'rat'),
                            "root_cause_candidate": reason,
                            "operator": f"{self._parse_sst(ss_data, 'op_long')} ({self._parse_sst(ss_data, 'op_short')})",
                            "rej_cause": self._parse_sst(ss_data, 'rej_cause'),
                            "emergency": self._parse_sst(ss_data, 'is_emergency'),
                            "context_snapshot": recent_logs[-15:]
                        }

                        # [기능 통합 2] OOS 발생(단절 진입) 시 동시간대 AP/Kernel 로그 추가
                        if event_type == "OOS_ENTER":
                            oos_event["cross_context_logs"] = self._get_surrounding_context_logs(lines, ts)

                        oos_history.append(oos_event)
                        last_slot_states[slot_id] = {"v": v_reg[0], "d": d_reg[0]}

                # 세션 시작
                is_cs = self.patterns['CS_START'].search(clean_line)
                is_ps = self.patterns['PS_START'].search(clean_line)
                if is_cs or is_ps:
                    if current_session: all_sessions.append(current_session)
                    p_match = re.search(r'PHONE(\d)', clean_line, re.I)
                    target_phone_id = p_match.group(0).upper() if p_match else "PHONE0"
                    c_type = "CS" if is_cs else "PS(VoLTE)"
                    logs_to_add = [l for l in list(pre_context) if not (c_type == "PS(VoLTE)" and any(t in l for t in self.ps_exclude_tags))]
                    current_session = {
                        "type": c_type,
                        "slot": target_phone_id,
                        "start_time": ts,
                        "end_time": None,
                        "id": "PENDING",
                        "status": "Unknown",
                        "is_user_reject": False,
                        "fail_reason": "0",
                        "logs": logs_to_add + [f"==> [START_{target_phone_id}]: {clean_line}"]
                    }
                    continue

                if current_session:
                    is_low_level_in_ps = (current_session["type"] == "PS(VoLTE)" and tag in self.ps_exclude_tags)
                    if is_low_level_in_ps: continue
                    if current_session["slot"] == "PHONE0":
                        if tag in ['RILD2', 'SST-1'] or (tag == 'RILJ' and 'PHONE1' in clean_line): continue
                    if current_session["slot"] == "PHONE1":
                        if tag in ['RILD', 'SST-0'] or (tag == 'RILJ' and 'PHONE0' in clean_line): continue

                    if any(kw in clean_line for kw in self.common_excludes): continue
                    if self.re_hex_data.search(clean_line): continue
                    if any(kw.lower() in clean_line.lower() for kw in self.tag_specific_excludes["RILD"]):
                        if tag == 'RILD' or tag == 'RILD2': continue
                    if any(kw.lower() in clean_line.lower() for kw in self.tag_specific_excludes["RILJ"]):
                        if tag == 'RILJ': continue

                    current_session["logs"].append(clean_line)

                    if id_m := self.patterns['CONN_ID'].search(clean_line): current_session["id"] = id_m.group(1)
                    reject_m = self.patterns['REJECT_EV'].search(clean_line)
                    if reject_m:
                        current_session["status"], current_session["is_user_reject"] = f"{reject_m.group(1)}", True

                    ims_m = self.patterns['IMS_REASON'].search(clean_line)
                    if self.patterns['FAIL_EV'].search(clean_line):
                        if ims_m:
                            current_session["status"], current_session["fail_reason"] = "FAIL", f"{ims_m.group(1)}: {ims_m.group(2)}"

                    normal_clear = False
                    if ims_m: normal_clear = True if ims_m.group(1) in ["501", "510"] else False

                    if normal_clear:
                        current_session["status"], current_session["fail_reason"] = "SUCCESS", f"{ims_m.group(1)}: {ims_m.group(2)}"
                    elif ims_m and not normal_clear:
                        current_session["status"], current_session["fail_reason"] = "FAIL", f"{ims_m.group(1)}: {ims_m.group(2)}"

                    cs_m = self.patterns['CS_REASON'].search(clean_line)
                    cs_fail_cause = ['34', '41', '42', '44', '49', '58', '65535'] # Base on GsmCdmaCallTracker.java
                    if cs_m:
                        readerable_reason = CALL_FAIL_REASON_MAP.get(cs_m.group(1), f"미확인_에러_코드({cs_m.group(1)})")
                        readerable_vendor_cause = VENDER_FAIL_REASON_MAP.get(cs_m.group(2), f"미확인_Vendor_Cause_코드({cs_m.group(2)})")
                        current_session["status"] = "SUCCESS"
                        if cs_m.group(1) in cs_fail_cause: current_session["status"] = "CALL DROP"

                        current_session["fail_reason"] = f"{cs_m.group(1)}({readerable_reason}): {cs_m.group(2)}({readerable_vendor_cause})"

                    # 세션 종료 판정
                    if self.patterns['END_EV'].search(clean_line):
                        current_session["end_time"] = ts
                        current_session["logs"].append(f"==> [END_{target_phone_id}]: {clean_line}")

                        # [기능 통합 3] 콜 드랍이나 연결 실패 시 동시간대 교차 로그 수집
                        if current_session["status"] in ["FAIL", "CALL DROP"]:
                            current_session["cross_context_logs"] = self._get_surrounding_context_logs(lines, ts)

                        all_sessions.append(current_session)
                        current_session = None
                        target_phone_id = None

            pre_context.append(clean_line)
        return {"sessions": all_sessions, "network_history": oos_history}

    def analyze_anr(self, lines):
        # ... (기존과 동일하게 유지하되 줄임)
        all_threads, phone_pid, main_tid = {}, None, None
        in_anr, in_phone = False, False
        current_tid = None
        for i, line in enumerate(lines):
            clean_line = line.strip()
            if self.section_anr_traces.search(line): in_anr = True; continue
            if in_anr:
                pid_m = self.re_pid_line.search(line)
                if pid_m:
                    if any(self.re_cmd_phone.search(lines[i+k]) for k in range(1, 5) if i+k < len(lines)):
                        phone_pid, in_phone = pid_m.group(1), True
                    elif in_phone: in_phone = False
                if in_phone:
                    thread_m = self.re_thread_header.search(line.strip())
                    if thread_m:
                        current_tid = thread_m.group(2)
                        name = thread_m.group(1)
                        if "main" in name.lower(): main_tid = current_tid
                        all_threads[current_tid] = {"name": name, "stack": [], "is_main": "main" in name.lower()}
                        all_threads[current_tid]["stack"].append(clean_line)
                    elif current_tid and clean_line:
                        all_threads[current_tid]["stack"].append(clean_line)
        lock_info = None
        if main_tid and main_tid in all_threads:
            for s_line in all_threads[main_tid]["stack"]:
                if lock_m := self.re_lock_held_by.search(s_line):
                    lock_info = {"addr": lock_m.group(1), "owner_tid": lock_m.group(2)}
                    break

        matched_tx = []
        in_binder = False
        for line in lines:
            if "BINDER TRANSACTIONS" in line: in_binder = True; continue
            if in_binder and "BINDER" in line and ":" not in line and "TRANSACTIONS" not in line: in_binder = False
            if in_binder:
                out_m = self.re_outgoing.search(line)
                if out_m and out_m.group(1) == phone_pid and out_m.group(2) == main_tid:
                    matched_tx.append({"to_pid": out_m.group(3), "to_tid": out_m.group(4), "code": out_m.group(5)})

        main_stack = all_threads[main_tid]["stack"] if (main_tid and main_tid in all_threads) else []

        report = {
            "process_info": {"name": "com.android.phone", "pid": phone_pid},
            "main": {"tid": main_tid, "stack": main_stack},
            "analysis_summary": {
                "has_lock_contention": lock_info is not None,
                "has_active_binder": len(matched_tx) > 0
            },
            "lock_chain": {
                "waiting_thread": main_tid,
                "blocker_thread": lock_info["owner_tid"] if lock_info else None,
                "lock_address": lock_info["addr"] if lock_info else None,
                "blocker_stack": all_threads.get(lock_info["owner_tid"], {}).get("stack") if lock_info else None
            },
            "active_binder_transactions": matched_tx
        }
        return report

    def analyze_crash(self, lines):
        crashes, is_cap, step, tmp = [], False, 0, None
        pre_ctx = deque(maxlen=10)
        for line in lines:
            clean_line = line.replace('\r', '').replace('\n', '').strip()
            if not clean_line: continue
            ts_m = self.re_time.search(clean_line)
            ts = ts_m.group(0) if ts_m else "00-00 00:00:00.000"

            is_fatal_app = self.re_fatal_app.search(clean_line)
            is_fatal_sys = self.re_fatal_sys.search(clean_line)

            if is_fatal_app or is_fatal_sys:
                if is_cap and tmp:
                    # [기능 통합 4] 크래시는 언제나 치명적이므로, 무조건 주변 로그 추가
                    tmp["cross_context_logs"] = self._get_surrounding_context_logs(lines, tmp["time"])
                    crashes.append(tmp)

                is_cap, step, fatal_info_count = True, (1 if is_fatal_app else 2), 0
                tmp = {"time": ts, "trigger": clean_line, "process": ("system_server" if is_fatal_sys else "Unknown"), "exception_info": "", "call_stack": [], "context": list(pre_ctx)[-5:]}
                continue

            if is_cap:
                if step == 1:
                    if self.re_proc_phone.search(clean_line): tmp["process"] = "com.android.phone"; step = 2; continue
                    elif "Process:" in clean_line: is_cap = False
                elif step == 2:
                    if self.re_stack_line.search(clean_line) or clean_line.startswith("at "): tmp["call_stack"].append(clean_line)
                    else:
                        if len(tmp["call_stack"]) > 0:
                            tmp["cross_context_logs"] = self._get_surrounding_context_logs(lines, tmp["time"])
                            crashes.append(tmp); is_cap = False
                        elif fatal_info_count < 3: tmp["exception_info"] += clean_line + " "; fatal_info_count += 1
                        else:
                            tmp["cross_context_logs"] = self._get_surrounding_context_logs(lines, tmp["time"])
                            crashes.append(tmp); is_cap = False
            pre_ctx.append(line.strip())
        return crashes

    def analyze_battery(self, lines):
        """
        [전체 스캔 최적화판] 무한 트랩 방지 및 초고속 문자열 매칭 적용
        """
        battery_report = {
            "stats_period": "Unknown",
            "time_on_battery": "Unknown",
            "screen_off_time": "Unknown",
            "screen_on_battery_use": "Unknown",
            "signal_strength_distribution": {},
            "mobile_radio_active": "Unknown",
            "telephony_drain_evaluation": "Unknown"
        }

        has_data = False
        in_signal_levels = False
        signal_line_count = 0  # 🚨 [핵심 방어막] 무한 트랩 방지용 카운터

        re_stats = re.compile(r'Stats from\s+(.*?)\s+to\s+(.*)', re.I)
        re_pct = re.compile(r'\(([\d.]+)%\)')
        re_level = re.compile(r'^(none|poor|moderate|good|great)\s', re.I)

        for line in lines:
            clean_line = line.strip()
            if not clean_line:
                in_signal_levels = False
                continue

            # ==========================================
            # 1. 멀티라인 신호 세기 파싱 (안전장치 포함)
            # ==========================================
            if clean_line.startswith("Phone signal levels:") or clean_line.startswith("Phone signal strength:"):
                in_signal_levels = True
                signal_line_count = 0  # 카운터 초기화
                has_data = True
                continue

            if in_signal_levels:
                signal_line_count += 1
                # 🚨 신호 레벨은 길어봐야 5~6줄. 10줄이 넘어가면 엉뚱한 로그에 갇힌 것이므로 강제 탈출!
                if signal_line_count > 10 or ":" in clean_line:
                    in_signal_levels = False
                else:
                    level_match = re_level.match(clean_line)
                    if level_match:
                        level_name = level_match.group(1).lower()
                        pct_match = re_pct.search(clean_line)
                        if pct_match:
                            try:
                                battery_report["signal_strength_distribution"][level_name] = float(pct_match.group(1))
                            except ValueError:
                                pass
                # 신호 세기 파싱 중에는 밑에 있는 단일 라인 검사를 생략 (속도 향상)
                continue

            # ==========================================
            # 2. 단일 라인 데이터 파싱 (.lower() 제거 및 startswith 최적화)
            # ==========================================
            if clean_line.startswith("Time on battery:"):
                battery_report["time_on_battery"] = clean_line.split(":", 1)[1].strip()
                has_data = True
            elif clean_line.startswith("Time on battery screen off:"):
                battery_report["screen_off_time"] = clean_line.split(":", 1)[1].strip()
                has_data = True
            elif clean_line.startswith("Battery use(%) while screen on:"):
                battery_report["screen_on_battery_use"] = clean_line.split(":", 1)[1].strip()
                has_data = True
            elif clean_line.startswith("Mobile radio active:"):
                battery_report["mobile_radio_active"] = clean_line.split(":", 1)[1].strip()
                has_data = True
            # Stats from 파싱 (startswith로 1차 필터링 후 정규식)
            elif clean_line.startswith("Stats from ") and " to " in clean_line:
                m = re_stats.search(clean_line)
                if m:
                    battery_report["stats_period"] = f"{m.group(1).strip()} ~ {m.group(2).strip()}"
                    has_data = True

        # ==========================================
        # 3. 휴리스틱 평가
        # ==========================================
        poor_pct = battery_report["signal_strength_distribution"].get("poor", 0.0)
        none_pct = battery_report["signal_strength_distribution"].get("none", 0.0)
        total_bad_signal = poor_pct + none_pct

        if total_bad_signal > 30.0:
            battery_report["telephony_drain_evaluation"] = f"CRITICAL: 단말이 신호 없음(none)/미약(poor) 상태에 {total_bad_signal}% 동안 머물렀습니다. 모뎀의 잦은 망 탐색(Hunting)으로 인한 심각한 배터리 광탈이 의심됩니다."
        elif total_bad_signal > 15.0:
            battery_report["telephony_drain_evaluation"] = f"WARNING: 신호 미약 상태 비중이 {total_bad_signal}%로 다소 높습니다. 음영 지역 체류로 인한 모뎀 전력 소모가 큽니다."
        elif total_bad_signal > 0:
            battery_report["telephony_drain_evaluation"] = f"NORMAL: 신호 불량 비중이 {total_bad_signal}%로 양호한 수준입니다."
        else:
            if not battery_report["signal_strength_distribution"]:
                 battery_report["telephony_drain_evaluation"] = "신호 세기 분포 데이터가 덤프에 없습니다."

        if not has_data:
            return None

        return battery_report

    def run_batch(self, mode, output_path):
        """파일을 한 번만 읽어 분석 수행 후 JSON 저장 (초고속 병렬 파싱)"""
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines() # 슬라이싱 덕분에 메모리 부하 제로!

            result = {}
            if mode in ['all']: result['radio_power'] = self.analyze_radio_power(lines)
            if mode in ['call', 'all']: result['telephony'] = self.analyze_telephony(lines)
            if mode in ['anr', 'all']: result['anr_context'] = self.analyze_anr(lines)
            if mode in ['crash', 'all']: result['crash_context'] = self.analyze_crash(lines)
            if mode in ['all']:
                battery_res = self.analyze_battery(lines)
                if battery_res: result['battery_stats'] = battery_res

            # 🚨 [신규 추가] Boot Stat 파싱 실행 및 JSON 저장
            if mode in ['all']:
                boot_res = self.analyze_boot_stat(lines)
                if boot_res: result['boot_stats'] = boot_res

            # 🚨 [신규 추가] 안테나 레벨 파싱 실행
            if mode in ['all']:
                sig_res = self.analyze_signal_level(lines)
                if sig_res: result['signal_level_history'] = sig_res

            if mode in ['all']:
                net_usage = self.analyze_data_usage(lines)
                if net_usage: result['data_usage_stats'] = net_usage

            with open(output_path, "w", encoding="utf-8") as j:
                json.dump(result, j, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error in run_batch: {e}")
            return False

def main():
    parser = argparse.ArgumentParser(description="Telephony Batch Diagnostic Tool")
    parser.add_argument("input", help="분석 대상 파일 또는 폴더 경로")
    parser.add_argument("--mode", choices=['call', 'anr', 'crash', 'all'], default='all')
    args = parser.parse_args()

    input_dir = os.path.dirname(args.input)
    output_dir = os.path.join(input_dir, "result")
    os.makedirs(output_dir, exist_ok=True)

    targets = []
    if os.path.isdir(args.input):
        targets = [os.path.join(args.input, f) for f in os.listdir(args.input)
                   if os.path.isfile(os.path.join(args.input, f))]
    else:
        targets = [args.input]

    print(f"-- 분석 시작 (총 {len(targets)}개 파일)")
    print("-" * 50)

    for target in targets:
        filename = os.path.basename(target)
        report_name = f"{os.path.splitext(filename)[0]}_report.json"
        report_path = os.path.join(output_dir, report_name)

        print(f"-- 분석 중: {filename}", end="\r")

        suite = TelephonyLogSummarizer(target)
        success = suite.run_batch(args.mode, report_path)

        if success:
            print(f"-- 완료: {filename} -> {report_name}      ")
        else:
            print(f"-- 실패: {filename}                          ")

    print("-" * 50)
    print(f"-- 모든 작업이 완료되었습니다. 결과는 '{output_dir}/' 폴더에 있습니다.")

if __name__ == "__main__":
    main()
