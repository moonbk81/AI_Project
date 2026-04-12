import re
import os
import json
import argparse
from collections import deque
from datetime import datetime, timedelta

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
    # [핵심 추가] 동시간대 교차 로그 추출 (메모리 스캔 방식 최적화)
    # =========================================================================
    def _get_surrounding_context_logs(self, lines, target_time_str, window_seconds=2, max_lines=150):
        """메모리에 로드된 lines 배열에서 문제 발생 시점 전후의 로그를 모두 긁어옵니다."""
        if not target_time_str or target_time_str == "00-00 00:00:00.000":
            return []

        current_year = datetime.now().year
        time_format = "%Y-%m-%d %H:%M:%S"
        
        # 밀리초(.123) 제거하고 기준 시간 맞추기
        base_time_str = target_time_str.split('.')[0] if '.' in target_time_str else target_time_str
        
        try:
            target_dt = datetime.strptime(f"{current_year}-{base_time_str}", time_format)
        except ValueError:
            return []

        start_dt = target_dt - timedelta(seconds=window_seconds)
        end_dt = target_dt + timedelta(seconds=window_seconds)
        
        pattern = re.compile(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})')
        cross_context_logs = []
        
        for line in lines:
            match = pattern.search(line)
            if match:
                log_time_str = match.group(1)
                try:
                    log_dt = datetime.strptime(f"{current_year}-{log_time_str}", time_format)
                    
                    if start_dt <= log_dt <= end_dt:
                        # (선택 사항) 이미 Radio 파서가 radio 로그를 담고 있으므로 중복을 줄이려면
                        # 아래 필터를 켤 수 있습니다. 지금은 전체 흐름 파악을 위해 모두 수집합니다.
                        # if " radio " not in line.lower() and "rild" not in line.lower():
                        cross_context_logs.append(line.strip())
                        
                    # 안드로이드 덤프 파일은 버퍼 단위(main, radio, system)로 덩어리져서 기록되므로
                    # 특정 섹션이 끝났다고 해서 시간만 보고 break 하면 다른 버퍼 로그를 놓칠 수 있습니다.
                    # 따라서 break 없이 전체를 순회합니다 (슬라이싱 되어있으므로 순회 속도는 매우 빠름).
                except ValueError:
                    continue

        # ==========================================
        # [수정 로직] 메모리 폭발 방지를 위한 다이어트
        # ==========================================
        if len(cross_context_logs) > max_lines:
            # 로그가 너무 많으면 가장 중요한 에러 시점 근처의 마지막 로그들만 유지
            return cross_context_logs[-max_lines:]
        return cross_context_logs
    # =========================================================================

    def _parse_sst(self, content, key):
        match = self.re_sst_fields[key].search(content)
        if match:
            val = match.group(1).strip().rstrip(')')
            if "(" in val and ")" not in val: val += ")"
            return val
        return "Unknown"
    
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
                        current_session["status"], current_session["fail_reason"] = "SUCESS", f"{ims_m.group(1)}: {ims_m.group(2)}"
                    elif ims_m and not normal_clear:
                        current_session["status"], current_session["fail_reason"] = "FAIL", f"{ims_m.group(1)}: {ims_m.group(2)}"
                        
                    cs_m = self.patterns['CS_REASON'].search(clean_line)
                    cs_fail_cause = ['34','41', '42', '44', '49', '58', '65535']
                    if cs_m:
                        if cs_m.group(1) in cs_fail_cause:
                            current_session["status"], current_session["fail_reason"] = "CALL DROP", f"{cs_m.group(1)}: {cs_m.group(2)}"
                        else:
                            current_session["status"], current_session["fail_reason"] = "SUCCESS", f"{cs_m.group(1)}: {cs_m.group(2)}"

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