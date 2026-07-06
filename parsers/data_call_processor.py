import re
import json
import os
from datetime import datetime
from parsers.base import BaseParser
from core.telephony_constants import RIL_DATA_FAIL_CAUSE_MAP

class DataCallProcessor(BaseParser):
    """RIL SETUP_DATA_CALL Request/Response 매칭 및 데이터 스톨(Stall) 분석기"""

    IRRELEVANT_LOG_KEYWORDS = [
        "LocationAccessPolicy", "VolteServiceModule", "SatelliteController",
        "checkLocationPermission", "onServiceStateChanged", "getSatellitePerPlmnConfiguration"
    ]

    SERVICE_STATE_CONTEXT_KEYWORDS = [
        "Poll ServiceState done", "ServiceState", "mDataRegState", "mVoiceRegState",
        "emergencyEnabled", "availableServices", "cellIdentity", "voiceSpecificInfo",
        "dataSpecificInfo", "nrState", "rRplmn", "isUsingCarrierAggregation",
        "isNonTerrestrialNetwork"
    ]

    DATA_CALL_CONTEXT_KEYWORDS = [
        "setupdatacall", "setup data call", "data call", "datacall",
        "deactivate", "pdp", "epdn", "apn", "dnn", "e-pdn",
        "data setup", "data connection", "데이터 연결", "데이터콜", "pdp 활성화"
    ]

    FAILURE_CONTEXT_KEYWORDS = [
        "fail", "failed", "failure", "error", "reject", "rejected", "disconnected",
        "disallowed", "unsupported", "invalid", "forbidden", "denied", "cannot",
        "unable", "not allowed", "not found", "no carrier", "authentication failed"
    ]

    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.parsed_data = []

    def _is_success_cause(self, cause: str) -> bool:
        cause_upper = (cause or "").strip().upper()
        return (
            cause_upper.startswith("NONE")
            or "(0X0)" in cause_upper
            or cause_upper == "0"
            or bool(re.match(r'^0(?:\s|$)', cause_upper))
        )

    def _extract_framework_fail_cause(self, clean_line: str):
        next_field = (
            r'(?=\s+(?:APN Setting|mDnn|dnn|apn|emergencyEnabled|availableServices|'
            r'cellIdentity|voiceSpecificInfo|dataSpecificInfo|nrState|rRplmn|'
            r'isUsingCarrierAggregation|isNonTerrestrialNetwork)\b|[,}]|$)'
        )
        match = re.search(
            r'(?:fail[_\s-]*cause|(?<![A-Za-z])cause)\s*[:=]\s*(.+?)' + next_field,
            clean_line,
            re.I,
        )
        return match.group(1).strip() if match else None

    def analyze(self, lines):
        """run_parser()를 대체하는 단일 분석 인터페이스 (메모리 리스트 기반)"""
        pending_requests = {}      # SETUP_DATA_CALL Request 대기열
        pending_deactivates = {}   # DEACTIVATE_DATA_CALL Request 대기열
        active_sessions = {}       # 현재 연결이 유지 중인 세션 (cid 기준)
        self.parsed_data = []

        last_rild_fail_cause = None

        for idx, line in enumerate(lines):
            clean_line = self.clean_line(line)
            if not clean_line: continue

            if "fail cause" in clean_line.lower() and ("RILD" in clean_line or "RILD2" in clean_line):
                rild_cause_match = re.search(r'fail cause\s*\((\d+)\) is permanent fail', clean_line, re.IGNORECASE)
                if rild_cause_match:
                    last_rild_fail_cause = rild_cause_match.group(1)
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

            # 🚨 [수정됨] 정규식을 유연하게 열어서 SETUP_DATA_CALL 응답 뒤의 모든 페이로드를 가져옵니다.
            res_match = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?\[(\d+)\]<\s*SETUP_DATA_CALL(.*)', clean_line)
            if res_match:
                res_time_str, token, payload = res_match.groups()

                req = pending_requests.pop(token, None)
                if req is None:
                    # Bucket/window 기반 입력에서는 SETUP_DATA_CALL 응답 라인만 들어오고
                    # 같은 token의 요청 라인이 빠질 수 있다. 실패 응답은 이 경우에도 버리지 않고 이벤트화한다.
                    net_match = re.search(r'accessNetworkType=([^,}\s]+)', payload, re.I)
                    apn_match = re.search(r'(?:mDnn|dnn|apn)[:=]\s*([^,}\s]+)', payload, re.I)
                    proto_match = re.search(r'(?:protocolType|type)[:=]\s*([^,}\s]+)', payload, re.I)
                    req = {
                        'req_time': res_time_str,
                        'network': net_match.group(1).strip() if net_match else "UNKNOWN",
                        'apn': apn_match.group(1).strip() if apn_match else "UNKNOWN",
                        'protocol': proto_match.group(1).strip() if proto_match else "UNKNOWN",
                    }
                    latency_ms = -1
                else:
                    fmt = "%m-%d %H:%M:%S.%f"
                    try:
                        t_req = datetime.strptime(req['req_time'], fmt)
                        t_res = datetime.strptime(res_time_str, fmt)
                        latency_ms = int((t_res - t_req).total_seconds() * 1000)
                    except:
                        latency_ms = -1

                # 🚨 [수정됨] Payload 내부를 샅샅이 뒤져서 진짜 상태값들을 추출합니다.
                cause_m = re.search(r'cause[:=]\s*([^,}\s]+)', payload, re.I)
                # 'link status'를 잡든 'status'를 잡든 안전하게 처리하도록 추출
                status_m = re.search(r'status[:=]\s*([^,}\s]+)', payload, re.I)
                cid_m = re.search(r'cid[:=]\s*([\d-]+)', payload, re.I)

                cause = cause_m.group(1) if cause_m else "NONE"
                d_status = status_m.group(1) if status_m else "UNKNOWN"
                cid = cid_m.group(1) if cid_m else "-1"

                # 💡 [핵심 수정] 가짜 SUCCESS / 가짜 FAIL 판별 로직 고도화
                is_success = True

                # [조건 1] cause가 에러 코드를 가리키면 무조건 실패
                if not self._is_success_cause(cause):
                    is_success = False

                # [조건 2] status 문자열이 명시적 실패(NOT_SPECIFIED, FAIL, ERROR)인 경우 실패
                if d_status.upper() in ["NOT_SPECIFIED", "FAIL", "ERROR"]:
                    is_success = False

                # [조건 3] status가 숫자일 경우, 0(성공), 1(Active), 2(Dormant)는 정상 연결로 취급. 그 외 숫자는 에러.
                elif d_status.isdigit() and d_status not in ["0", "1", "2"]:
                    is_success = False

                final_status = "SUCCESS" if is_success else "FAIL"
                detailed_cause = f"status={d_status}, cause={cause}"

                # 벤더 로그나 추가 설명에 NO CARRIER, Auth failed 등이 섞여있는지 확인 (TC-008 정답지 대응)
                # SETUP_DATA_CALL 응답 한 줄에 원인이 없고, 직전/직후 RILD 로그에만 남는 경우가 있어 주변 문맥까지 확인한다.
                vendor_err = []
                context_start = max(0, idx - 20)
                context_end = min(len(lines), idx + 21)
                nearby_context = "\n".join(self.clean_line(ctx_line) for ctx_line in lines[context_start:context_end])
                vendor_search_text = f"{payload}\n{nearby_context}"

                if re.search(r'NO\s+CARRIER', vendor_search_text, re.I):
                    vendor_err.append("NO CARRIER")
                if re.search(r'(?:User\s+)?authentication\s+failed', vendor_search_text, re.I):
                    vendor_err.append("User authentication failed")

                if last_rild_fail_cause:
                    rild_fail_cause_str = RIL_DATA_FAIL_CAUSE_MAP.get(last_rild_fail_cause)
                    if rild_fail_cause_str:
                        vendor_err.append(rild_fail_cause_str)
                    else:
                        vendor_err.append(f"RILD fail cause {last_rild_fail_cause}")
                    last_rild_fail_cause = None

                # 중복 원인 문자열 제거. 순서는 보존한다.
                vendor_err = list(dict.fromkeys(vendor_err))

                if vendor_err:
                    final_status = "FAIL" # 벤더 에러가 보이면 무조건 실패 처리
                    detailed_cause += f" ({' / '.join(vendor_err)})"

                if final_status == "SUCCESS" and cid != "-1":
                    active_sessions[cid] = {
                        'apn': req['apn'],
                        'setup_res_time': res_time_str
                    }

                self.parsed_data.append({
                    # 실패한 호 연결은 DATA_SETUP_FAIL로 명확히 이벤트 타입을 분리
                    'event_type': 'DATA_SETUP_FAIL' if final_status == "FAIL" else 'DATA_SETUP',
                    'req_time': req['req_time'],
                    'res_time': res_time_str,
                    'token': token,
                    'cid': cid,
                    'apn': req['apn'],
                    'network': req['network'],
                    'protocol': req['protocol'],
                    'status': final_status,
                    'cause': detailed_cause,
                    'latency_ms': latency_ms
                })
                continue

            # SETUP_DATA_CALL 응답이 아닌 framework 상태 로그에도 실패 원인이 남는 경우 보강 처리
            # 단, 데이터 콜 설정과 무관한 로그는 제외
            clean_line_lower = clean_line.lower()
            if any(kw.lower() in clean_line_lower for kw in self.IRRELEVANT_LOG_KEYWORDS):
                continue

            if any(kw.lower() in clean_line_lower for kw in self.SERVICE_STATE_CONTEXT_KEYWORDS):
                continue

            is_data_call_context = any(k in clean_line_lower for k in self.DATA_CALL_CONTEXT_KEYWORDS)
            if not is_data_call_context:
                continue

            has_failure_context = any(k in clean_line_lower for k in self.FAILURE_CONTEXT_KEYWORDS)
            if not has_failure_context:
                continue

            # 먼저 fail cause를 추출
            fail_cause = self._extract_framework_fail_cause(clean_line)

            # APN Setting을 추출
            apn_match = re.search(
                r'(?:APN Setting|mDnn|dnn|apn)[:=]?\s*([^,}\s()]+)',
                clean_line,
                re.I,
            )

            # 타임스탐프 추출
            time_match = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3})', clean_line)

            if time_match and fail_cause:
                time_str = time_match.group(1)
                apn = apn_match.group(1).strip() if apn_match else "UNKNOWN"
                # NONE, NONE(0x0), 0 등은 성공을 의미하므로 DATA_SETUP_FAIL로 처리하지 않음
                if not self._is_success_cause(fail_cause):
                    # 실패 관련 키워드를 포함한 로그만 필터링하여 raw_context에 포함
                    # (정상 상태 알림 등 무관한 로그 제외)
                    failure_keywords = [
                        "fail", "error", "disconnected", "disallowed", "reject",
                        "unsupported", "invalid", "forbidden", "denied", "failed",
                        "cannot", "unable", "not allowed", "not found"
                    ]
                    context_start = max(0, idx - 20)
                    context_end = min(len(lines), idx + 21)
                    relevant_lines = []
                    for ctx_line in lines[context_start:context_end]:
                        clean = self.clean_line(ctx_line)
                        # 실패 관련 키워드가 있거나, 현재 라인(idx)인 경우만 포함
                        if any(kw.lower() in clean.lower() for kw in failure_keywords) or ctx_line == lines[idx]:
                            relevant_lines.append(clean)
                    nearby_context = "\n".join(relevant_lines) if relevant_lines else "\n".join(self.clean_line(ctx_line) for ctx_line in lines[context_start:context_end])
                    vendor_err = []
                    if re.search(r'NO\s+CARRIER', nearby_context, re.I):
                        vendor_err.append("NO CARRIER")
                    if re.search(r'(?:User\s+)?authentication\s+failed', nearby_context, re.I):
                        vendor_err.append("User authentication failed")
                    detailed_cause = f"status=UNKNOWN, cause={fail_cause}"
                    if vendor_err:
                        detailed_cause += f" ({' / '.join(dict.fromkeys(vendor_err))})"
                    self.parsed_data.append({
                        'event_type': 'DATA_SETUP_FAIL',
                        'req_time': time_str,
                        'res_time': time_str,
                        'token': 'FW',
                        'cid': '-1',
                        'apn': apn,
                        'network': 'UNKNOWN',
                        'protocol': 'UNKNOWN',
                        'status': 'FAIL',
                        'cause': detailed_cause,
                        'latency_ms': 0,
                        'raw_context': nearby_context,
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
            # 🚨 타임스탬프 포맷(MM-DD HH... 또는 YYYY-MM-DDTHH...)과 벤더 특화 스톨 키워드 모두 호환되도록 확장
            stall_match = re.search(r'([\d-]{5,19}[T\s]\d{2}:\d{2}:\d{2}[.,]\d+).*?(data stall: start|data stall: end|onDataStallAlarm|DataStallRecovery|trigger data stall|Data stall detected)(.*)', clean_line, re.IGNORECASE)

            if stall_match:
                time_str, keyword, payload = stall_match.groups()
                keyword_lower = keyword.lower()

                action_desc = "스톨(병목) 현상 감지됨"
                action_level = "DETECTED"

                # 🚨 새로 발견된 로그 포맷 처리 (start / end)
                if "data stall: start" in keyword_lower:
                    action_level = "START"
                    action_desc = "Data Stall 감지되어 Recovery 로직 진입"
                elif "data stall: end" in keyword_lower:
                    action_level = "END"
                    action_desc = "Recovery 동작 종료"

                    # 성공 여부 판별
                    if "isRecovered=true" in payload:
                        action_desc += " (정상적으로 망 복구 완료됨)"
                    else:
                        action_desc += " (복구 실패 또는 진행 중)"

                    # 소요 시간 파싱 (밀리초 -> 초 변환)
                    dur_m = re.search(r'TimeDuration=(\d+)', payload)
                    if dur_m:
                        duration_sec = int(dur_m.group(1)) / 1000.0
                        action_desc += f" [복구 소요시간: {duration_sec}초]"

                else:
                    # 기존 AOSP 표준 복구 시퀀스 매핑
                    action_m = re.search(r'(?:action|step|recoveryAction)\s*[=:]?\s*(\d+)', payload, re.IGNORECASE)
                    action_level = action_m.group(1) if action_m else "DETECTED"

                    if action_level == "0": action_desc = "GET_DATA_CALL_LIST (상태 확인)"
                    elif action_level == "1": action_desc = "CLEANUP (PDP 해제 및 재연결)"
                    elif action_level == "2": action_desc = "REREGISTER (망 재등록)"
                    elif action_level == "3": action_desc = "RADIO_RESTART (모뎀 리셋)"
                    elif action_level == "4": action_desc = "MODEM_RESET (하드웨어 리셋)"

                # lastaction, isRecovered, reason, TimeDuration 필드 추출
                last_action_m = re.search(r'lastaction=([A-Za-z0-9_]+)', payload)
                is_recovered_m = re.search(r'isRecovered=(\w+)', payload)
                reason_m = re.search(r'reason=([A-Za-z0-9_]+)', payload)
                time_duration_m = re.search(r'TimeDuration=(\d+)', payload)

                last_action = last_action_m.group(1) if last_action_m else None
                is_recovered = is_recovered_m.group(1) if is_recovered_m else None
                recovered_reason = reason_m.group(1) if reason_m else None
                time_duration = int(time_duration_m.group(1)) if time_duration_m else None

                event_dict = {
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
                }

                # 추가 필드들 (있으면 포함)
                if last_action:
                    event_dict['last_action'] = last_action
                if is_recovered:
                    event_dict['is_recovered'] = is_recovered
                if recovered_reason:
                    event_dict['recovered_reason'] = recovered_reason
                if time_duration:
                    event_dict['duration_ms'] = time_duration

                self.parsed_data.append(event_dict)
                continue

        return self.parsed_data

    def save_ui_report(self, output_dir="./result", base_name=""):
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{base_name}_datacall.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(self.parsed_data if self.parsed_data else [], f, indent=4, ensure_ascii=False)
