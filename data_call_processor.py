import re
import json
import os
from datetime import datetime

class DataCallProcessor:
    """RIL SETUP_DATA_CALL Request/Response 매칭 및 분석기"""

    def __init__(self, log_path):
        self.log_path = log_path
        self.parsed_data = []

    def run_parser(self):
        pending_requests = {}      # SETUP_DATA_CALL Request 대기열
        pending_deactivates = {}   # DEACTIVATE_DATA_CALL Request 대기열
        active_sessions = {}       # 현재 연결이 유지 중인 세션 (cid 기준)
        self.parsed_data = []

        if not os.path.exists(self.log_path):
            return []

        with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()

                # ==========================================
                # 1. SETUP_DATA_CALL (연결)
                # ==========================================
                # Request 매칭 [6270]>
                req_match = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?\[(\d+)\]>\s*SETUP_DATA_CALL', line)
                if req_match:
                    time_str, token = req_match.groups()
                    net_match = re.search(r'accessNetworkType=([^,]+)', line)
                    apn_match = re.search(r'mDnn=([^,}]+)', line)
                    proto_match = re.search(r',\s*(IPV4V6|IPV6|IP)\s*,', line)

                    pending_requests[token] = {
                        'req_time': time_str,
                        'network': net_match.group(1) if net_match else "UNKNOWN",
                        'apn': apn_match.group(1).strip() if apn_match else "UNKNOWN",
                        'protocol': proto_match.group(1) if proto_match else "UNKNOWN"
                    }
                    continue

                # Response 매칭 [6270]<
                res_match = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?\[(\d+)\]<\s*SETUP_DATA_CALL DataCallResponse: \{ cause=([^ ]+).*?cid=([\d-]+)', line)
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

                        # 성공했다면 활성 세션(active_sessions)에 등록! (나중에 해제될 때 시간 계산을 위해)
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
                # Request 매칭 [6394]>
                deact_req = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?\[(\d+)\]>\s*DEACTIVATE_DATA_CALL\s*cid\s*=\s*(\d+)\s*reason\s*=\s*([^ ]+)', line)
                if deact_req:
                    time_str, token, cid, reason = deact_req.groups()
                    # 대기열에 임시 저장 (Response가 올 때까지 대기)
                    pending_deactivates[token] = {
                        'req_time': time_str,
                        'cid': cid,
                        'reason': reason
                    }
                    continue

                # Response 매칭 [6394]<
                deact_res = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?\[(\d+)\]<\s*DEACTIVATE_DATA_CALL', line)
                if deact_res:
                    res_time_str, token = deact_res.groups()

                    if token in pending_deactivates:
                        deact = pending_deactivates.pop(token)
                        cid = deact['cid']

                        # 이전에 연결 성공했던 세션 정보 가져오기
                        session_info = active_sessions.get(cid)
                        duration_sec = -1

                        if session_info:
                            fmt = "%m-%d %H:%M:%S.%f"
                            try:
                                # 연결 성공 시점부터 해제 응답을 받은 시점까지의 총 세션 유지 시간 계산
                                t_start = datetime.strptime(session_info['setup_res_time'], fmt)
                                t_end = datetime.strptime(res_time_str, fmt)
                                duration_sec = round((t_end - t_start).total_seconds(), 2)
                            except:
                                pass
                            del active_sessions[cid] # 세션 종료 처리

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

                # ==========================================
                # 3. UNSOL_DATA_CALL_LIST_CHANGED (모뎀 상태 통보)
                # ==========================================
                unsol_match = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?UNSOL_DATA_CALL_LIST_CHANGED\s+(.*)', line)
                if unsol_match:
                    time_str = unsol_match.group(1)
                    unsol_payload = unsol_match.group(2)
                    current_unsol_cids = set()

                    # 1. SetupDataCallResult 단위로 분할
                    call_blocks = re.split(r'SetupDataCallResult', unsol_payload)

                    for block in call_blocks:
                        if not block.strip(): continue

                        # 블록 내에서 정보 추출
                        cid_m = re.search(r'cid[:=]\s*(\d+)', block, re.I)
                        dnn_m = re.search(r'(?:dnn|apn)[:=]\s*["\']?([a-zA-Z0-9_\-]+)["\']?', block, re.I)
                        active_m = re.search(r'active[:=]\s*(\d+)', block, re.I)

                        if cid_m:
                            cid = cid_m.group(1)
                            current_unsol_cids.add(cid)

                            found_dnn = dnn_m.group(1).strip() if dnn_m else None
                            current_active = active_m.group(1) if active_m else "0"

                            # 2. 세션 관리 및 APN 복구
                            if cid not in active_sessions:
                                active_sessions[cid] = {
                                    'apn': found_dnn if found_dnn else "UNKNOWN (Early-log)",
                                    'active_state': 'UNKNOWN',
                                    'setup_res_time': time_str
                                }
                            else:
                                # UNKNOWN 상태인 세션을 실제 DNN 값으로 업데이트
                                if found_dnn and active_sessions[cid]['apn'] in ["UNKNOWN", "UNKNOWN (Early-log)"]:
                                    active_sessions[cid]['apn'] = found_dnn

                            sess = active_sessions[cid]
                            state_str = "ACTIVE" if current_active == "1" else "DORMANT" if current_active == "2" else f"STATE_{current_active}"

                            self.parsed_data.append({
                                'event_type': 'UNSOL_UPDATE',
                                'req_time': time_str,
                                'res_time': time_str,
                                'cid': cid,
                                'apn': sess['apn'],
                                'status': state_str
                                # (나머지 필요한 필드들 추가)
                            })
                            sess['active_state'] = current_active

        return self.parsed_data

    def save_ui_report(self, output_dir="./result"):
        if not self.parsed_data:
            return
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, "datacall_parsed_logs.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(self.parsed_data, f, indent=4, ensure_ascii=False)

