import re
from datetime import datetime
from parsers.base import BaseParser
from core.telephony_constants import RIL_ERR_MAP

class RiljParser(BaseParser):
    def analyze(self, lines):
        # 🚨 1. 정규식 튜닝: RilRequest 태그 추가 (NETWORK_ERR 등 Exception 로그를 잡기 위함)
        rilj_tag_regex = re.compile(r'\b[VDIWEF](?:/|\s+)(?:RILJ|SEM_RILJ|RilRequest)\b', re.IGNORECASE)
        req_pattern = re.compile(r'(?:RILJ|SEM_RILJ|RilRequest)\s*:\s*\[(\d+)\]>\s*([A-Z_0-9]+)(.*)', re.IGNORECASE)
        resp_pattern = re.compile(r'(?:RILJ|SEM_RILJ|RilRequest)\s*:\s*\[(\d+)\]<\s*([A-Z_0-9]+)\s*(error[:\s]+[A-Z_0-9_]+)?(.*)', re.IGNORECASE)
        unsol_pattern = re.compile(r'(?:RILJ|SEM_RILJ|RilRequest)\s*:\s*\[(?:UNSOL|UNSL)\][><]\s*([A-Z_0-9]+)(.*)', re.IGNORECASE)

        pending_requests = {}
        # 🚨 2. 리스트([]) 대신 딕셔너리({}) 사용: RilRequest의 상세 에러로 덮어쓰기 위함
        completed_requests = {}
        unsol_events = []

        current_year = datetime.now().year

        for line in lines:
            if not rilj_tag_regex.search(line):
                continue

            # "04-13 16:25:57.576" 18자리 타임스탬프 추출
            time_str = line[:18].strip()

            # [A] UNSOL (일방적 통보) 처리
            m_unsol = unsol_pattern.search(line)
            if m_unsol:
                unsol_events.append({
                    "time": time_str,
                    "command": m_unsol.group(1).strip(),
                    "details": m_unsol.group(2).strip()
                })
                continue

            # [B] REQUEST (AP -> CP) 처리
            m_req = req_pattern.search(line)
            if m_req:
                serial = m_req.group(1)
                command = m_req.group(2)
                if command in ["SETUP_DATA_CALL", "DEACTIVATE_DATA_CALL", "INITIAL_ATTACH"]:
                    continue
                pending_requests[serial] = {
                    "start_time": time_str,
                    "command": command,
                    "req_details": m_req.group(3).strip()
                }
                continue

            # [C] RESPONSE (CP -> AP) 처리 및 지연시간(Latency) 계산
            m_resp = resp_pattern.search(line)
            if m_resp:
                serial = m_resp.group(1)

                # pending에 있거나 이미 완료(completed)된 요청이어도 상세 에러 병합을 위해 가져옴
                req_data = None
                if serial in pending_requests:
                    req_data = pending_requests[serial]
                elif serial in completed_requests:
                    req_data = completed_requests[serial]

                if req_data:
                    end_time = time_str

                    # 레이턴시(ms) 계산
                    try:
                        t_start = datetime.strptime(f"{current_year}-{req_data['start_time']}", "%Y-%m-%d %H:%M:%S.%f")
                        t_end = datetime.strptime(f"{current_year}-{end_time}", "%Y-%m-%d %H:%M:%S.%f")
                        latency_ms = int((t_end - t_start).total_seconds() * 1000)
                    except:
                        latency_ms = req_data.get('latency_ms', 0)

                    # 파싱 준비
                    raw_error_field = m_resp.group(3)
                    resp_payload = m_resp.group(4) if m_resp.group(4) else ""

                    # 이미 저장된 상태가 있다면 계승
                    is_error = completed_requests.get(serial, {}).get('is_error', False)
                    error_msg = completed_requests.get(serial, {}).get('error_msg', "SUCCESS")

                    # 🚨 3. 성공 조건 확실한 방어막 (mErrorCode = 0)
                    if "mErrorCode = 0" in resp_payload or (raw_error_field and "error: 0" in raw_error_field.lower()):
                        is_error = False
                        error_msg = "SUCCESS"
                    else:
                        # (1) 명시적 에러 필드 처리 (예: error 49)
                        if raw_error_field:
                            clean_err = re.sub(r'error[:\s]+', '', raw_error_field, flags=re.IGNORECASE).strip()
                            if clean_err and clean_err.upper() not in ["0", "NONE"]:
                                is_error = True
                                # 기존 에러가 단순 숫자일 때만 덮어쓰기
                                if error_msg == "SUCCESS" or error_msg.isdigit():
                                    error_msg = RIL_ERR_MAP.get(clean_err, clean_err)

                        # 🚨 (2) RilRequest Exception 파싱 (가장 정확한 NETWORK_ERR 캡처)
                        if "Exception:" in resp_payload:
                            ex_match = re.search(r'Exception:\s*([A-Z_]+)', resp_payload)
                            if ex_match:
                                is_error = True
                                error_msg = ex_match.group(1) # NETWORK_ERR 할당

                        # 🚨 (3) [수정됨] mErrorCode 오판을 막기 위해 단어 단위(\b)로 error만 검색
                        elif not is_error and re.search(r'\berror\b', resp_payload, re.IGNORECASE):
                            extra_err_match = re.search(r'\berror[:\s]+([A-Z_0-9_]+)', resp_payload, re.IGNORECASE)
                            if extra_err_match and extra_err_match.group(1) != "0":
                                is_error = True
                                extracted = extra_err_match.group(1)
                                error_msg = RIL_ERR_MAP.get(extracted, extracted)

                    # 처리된 요청 갱신 (리스트 append 대신 딕셔너리 할당)
                    completed_requests[serial] = {
                        "start_time": req_data['start_time'],
                        "latency_ms": latency_ms,
                        "command": req_data['command'],
                        "is_error": is_error,
                        "error_msg": error_msg,
                        "req_details": req_data.get('req_details', ''),
                        "resp_details": resp_payload.strip()
                    }

                    # 타임아웃 미아 처리를 막기 위해 pending에서는 제거
                    if serial in pending_requests:
                        del pending_requests[serial]

        # [D] 영원히 응답받지 못한 모뎀 먹통(Timeout) 명령들 색출
        timeout_requests = []
        for serial, req in pending_requests.items():
            timeout_requests.append({
                "time": req['start_time'],
                "command": req['command'],
                "details": req['req_details']
            })

        return {
            # 🚨 4. 기존 반환 포맷 유지 (딕셔너리 값들을 리스트로 변환)
            "completed": list(completed_requests.values()),
            "unsol": unsol_events,
            "timeouts": timeout_requests
        }
