import re
import json
import os
from datetime import datetime
from parsers.base import BaseParser

class DataCallProcessor(BaseParser):
    """RIL SETUP_DATA_CALL Request/Response 매칭 및 데이터 스톨(Stall) 분석기"""

    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.parsed_data = []

    def analyze(self, lines):
        """run_parser()를 대체하는 단일 분석 인터페이스 (메모리 리스트 기반)"""
        pending_requests = {}      # SETUP_DATA_CALL Request 대기열
        pending_deactivates = {}   # DEACTIVATE_DATA_CALL Request 대기열
        active_sessions = {}       # 현재 연결이 유지 중인 세션 (cid 기준)
        self.parsed_data = []

        for line in lines:
            clean_line = self.clean_line(line)
            if not clean_line: continue

            # ==========================================
            # 1. SETUP_DATA_CALL (연결)
            # ==========================================
            req_match = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?\[(\d+)\]>\s*SETUP_DATA_CALL', clean_line)
            if req_match:
                time_str, token = req_match.groups()
                net_match = re.search(r'accessNetworkType=([^,]+)', clean_line)
                apn_match = re.search(r'mDnn=([^,}]+)', clean_line)
                proto_match = re.search(r',\s*(IPV4V6|IPV6|IP)\s*,', clean_line)

                pending_requests[token] = {
                    'req_time': time_str,
                    'network': net_match.group(1) if net_match else "UNKNOWN",
                    'apn': apn_match.group(1).strip() if apn_match else "UNKNOWN",
                    'protocol': proto_match.group(1) if proto_match else "UNKNOWN"
                }
                continue

            res_match = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?\[(\d+)\]<\s*SETUP_DATA_CALL DataCallResponse: \{ cause=([^ ]+).*?cid=([\d-]+)', clean_line)
            if res_match:
                res_time_str, token, cause, cid = res_match.groups()

                if token in pending_requests:
                    req = pending_requests.pop(token)
                    fmt = "%m-%d %H:%M:%S.%f"
                    try:
                        t_req = datetime.strptime(req['req_time'], fmt)
                        t_res = datetime.strptime(res_time_str, fmt)
                        latency_ms = int((t_res - t_req).total_seconds() * 1000)
                    except:
                        latency_ms = -1

                    status = "SUCCESS" if "NONE" in cause.upper() else "FAIL"

                    if status == "SUCCESS" and cid != "-1":
                        active_sessions[cid] = {
                            'apn': req['apn'],
                            'setup_res_time': res_time_str
                        }

                    self.parsed_data.append({
                        'event_type': 'DATA_SETUP',
                        'req_time': req['req_time'],
                        'res_time': res_time_str,
                        'token': token,
                        'cid': cid,
                        'apn': req['apn'],
                        'network': req['network'],
                        'protocol': req['protocol'],
                        'status': status,
                        'cause': cause,
                        'latency_ms': latency_ms
                    })
                continue

            # ==========================================
            # 2. DEACTIVATE_DATA_CALL (해제)
            # ==========================================
            deact_req = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?\[(\d+)\]>\s*DEACTIVATE_DATA_CALL\s*cid\s*=\s*(\d+)\s*reason\s*=\s*([^ ]+)', clean_line)
            if deact_req:
                time_str, token, cid, reason = deact_req.groups()
                pending_deactivates[token] = {
                    'req_time': time_str,
                    'cid': cid,
                    'reason': reason
                }
                continue

            deact_res = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?\[(\d+)\]<\s*DEACTIVATE_DATA_CALL', clean_line)
            if deact_res:
                res_time_str, token = deact_res.groups()

                if token in pending_deactivates:
                    deact = pending_deactivates.pop(token)
                    cid = deact['cid']
                    session_info = active_sessions.get(cid)
                    duration_sec = -1

                    if session_info:
                        fmt = "%m-%d %H:%M:%S.%f"
                        try:
                            t_start = datetime.strptime(session_info['setup_res_time'], fmt)
                            t_end = datetime.strptime(res_time_str, fmt)
                            duration_sec = round((t_end - t_start).total_seconds(), 2)
                        except:
                            pass
                        del active_sessions[cid]

                    self.parsed_data.append({
                        'event_type': 'DATA_DEACTIVATE',
                        'req_time': deact['req_time'],
                        'res_time': res_time_str,
                        'token': token,
                        'cid': cid,
                        'reason': deact['reason'],
                        'duration_sec': duration_sec,
                        'apn': session_info['apn'] if session_info else "UNKNOWN"
                    })
                continue

            # ==========================================
            # 3. UNSOL_DATA_CALL_LIST_CHANGED (모뎀 상태 통보)
            # ==========================================
            unsol_match = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?UNSOL_DATA_CALL_LIST_CHANGED\s+(.*)', clean_line)
            if unsol_match:
                time_str = unsol_match.group(1)
                unsol_payload = unsol_match.group(2)
                current_unsol_cids = set()

                call_blocks = re.split(r'SetupDataCallResult', unsol_payload)

                for block in call_blocks:
                    if not block.strip(): continue

                    cid_m = re.search(r'cid[:=]\s*(\d+)', block, re.I)
                    dnn_m = re.search(r'(?:dnn|apn)[:=]\s*["\']?([a-zA-Z0-9_\-]+)["\']?', block, re.I)
                    active_m = re.search(r'active[:=]\s*(\d+)', block, re.I)
                    type_m = re.search(r'type[:=]\s*([^,}\s]+)', block, re.I)
                    cause_m = re.search(r'cause[:=]\s*([^,}\s]+)', block, re.I)

                    if cid_m:
                        cid = cid_m.group(1)
                        current_unsol_cids.add(cid)

                        found_dnn = dnn_m.group(1).strip() if dnn_m else None
                        current_active = active_m.group(1) if active_m else "0"
                        current_protocol = type_m.group(1).strip() if type_m else "UNKNOWN"
                        current_cause = cause_m.group(1).strip() if cause_m else "UNKNOWN"

                        if cid not in active_sessions:
                            active_sessions[cid] = {
                                'apn': found_dnn if found_dnn else "UNKNOWN (Early-log)",
                                'protocol': current_protocol,
                                'active_state': 'UNKNOWN',
                                'setup_res_time': time_str
                            }
                        else:
                            if found_dnn and active_sessions[cid]['apn'] in ["UNKNOWN", "UNKNOWN (Early-log)"]:
                                active_sessions[cid]['apn'] = found_dnn
                            if current_protocol != "UNKNOWN":
                                active_sessions[cid]['protocol'] = current_protocol

                        sess = active_sessions[cid]
                        state_str = "ACTIVE" if current_active == "1" else "DORMANT" if current_active == "2" else f"STATE_{current_active}"
                        cause_str = f"{current_cause} (Active:{current_active})"

                        self.parsed_data.append({
                            'event_type': 'UNSOL_UPDATE',
                            'req_time': time_str,
                            'res_time': time_str,
                            'token': 'UNSL',
                            'cid': cid,
                            'apn': sess['apn'],
                            'protocol': sess.get('protocol', 'UNKNOWN'),
                            'network': sess.get('network', 'UNKNOWN'),
                            'status': state_str,
                            'cause': cause_str,
                            'latency_ms': 0
                        })
                        sess['active_state'] = current_active
                continue

            # ==========================================
            # 4. DATA STALL & RECOVERY (스톨 감지 및 복구 액션)
            # ==========================================
            stall_match = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?(onDataStallAlarm|DataStallRecovery|trigger data stall|Data stall detected)(.*)', clean_line, re.IGNORECASE)
            if stall_match:
                time_str, keyword, payload = stall_match.groups()

                # Recovery Action 레벨 추출 (예: action=1, step=2 등)
                action_m = re.search(r'(?:action|step|recoveryAction)\s*[=:]?\s*(\d+)', payload, re.IGNORECASE)
                action_level = action_m.group(1) if action_m else "DETECTED"

                # AOSP 표준 복구 시퀀스 매핑
                action_desc = "UNKNOWN"
                if action_level == "0": action_desc = "GET_DATA_CALL_LIST (상태 확인)"
                elif action_level == "1": action_desc = "CLEANUP (PDP 해제 및 재연결)"
                elif action_level == "2": action_desc = "REREGISTER (망 재등록)"
                elif action_level == "3": action_desc = "RADIO_RESTART (모뎀 리셋)"
                elif action_level == "4": action_desc = "MODEM_RESET (하드웨어 리셋)"
                elif action_level == "DETECTED": action_desc = "스톨(병목) 현상 감지됨"

                self.parsed_data.append({
                    'event_type': 'DATA_STALL_RECOVERY',
                    'req_time': time_str,
                    'res_time': time_str,
                    'token': 'STAL',
                    'cid': 'N/A',
                    'apn': 'N/A',
                    'protocol': 'N/A',
                    'network': 'N/A',
                    'status': f"ACTION_{action_level}",
                    'cause': action_desc,
                    'latency_ms': 0,
                    'raw_payload': payload.strip()
                })
                continue

        return self.parsed_data

    def save_ui_report(self, output_dir="./result", base_name=""):
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{base_name}_datacall.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(self.parsed_data if self.parsed_data else [], f, indent=4, ensure_ascii=False)