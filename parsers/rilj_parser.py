import re
from datetime import datetime
from parsers.base import BaseParser

class RiljParser(BaseParser):
    def analyze(self, lines):
        rilj_tag_regex = re.compile(r'\b[VDIWEF](?:/|\s+)RILJ\b', re.IGNORECASE)
        # 1. 정규식 튜닝: 대소문자 무시(re.IGNORECASE) 및 실제 포맷 완벽 대응
        req_pattern = re.compile(r'(?:RILJ|SEM_RILJ)\s*:\s*\[(\d+)\]>\s*([A-Z_0-9]+)(.*)', re.IGNORECASE)
        resp_pattern = re.compile(r'(?:RILJ|SEM_RILJ)\s*:\s*\[(\d+)\]<\s*([A-Z_0-9]+)\s*(error:\s*[A-Z_0-9_]+)?(.*)', re.IGNORECASE)
        unsol_pattern = re.compile(r'(?:RILJ|SEM_RILJ)\s*:\s*\[(?:UNSOL|UNSL)\][><]\s*([A-Z_0-9]+)(.*)', re.IGNORECASE)

        pending_requests = {}
        completed_requests = []
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
                if serial in pending_requests:
                    req_data = pending_requests.pop(serial)
                    end_time = time_str

                    # 레이턴시(ms) 계산
                    try:
                        t_start = datetime.strptime(f"{current_year}-{req_data['start_time']}", "%Y-%m-%d %H:%M:%S.%f")
                        t_end = datetime.strptime(f"{current_year}-{end_time}", "%Y-%m-%d %H:%M:%S.%f")
                        latency_ms = int((t_end - t_start).total_seconds() * 1000)
                    except:
                        latency_ms = 0

                    # 에러 여부 판독 (에러 문자열이 없거나 error: NONE이면 SUCCESS)
                    error_str = m_resp.group(3)
                    is_error = False
                    error_msg = "SUCCESS"
                    if error_str and "error: NONE" not in error_str.upper():
                        is_error = True
                        error_msg = error_str.replace("error:", "").strip()

                    completed_requests.append({
                        "start_time": req_data['start_time'],
                        "latency_ms": latency_ms,
                        "command": req_data['command'],
                        "is_error": is_error,
                        "error_msg": error_msg,
                        "req_details": req_data['req_details'],
                        "resp_details": m_resp.group(4).strip()
                    })

        # [D] 영원히 응답받지 못한 모뎀 먹통(Timeout) 명령들 색출
        timeout_requests = []
        for serial, req in pending_requests.items():
            timeout_requests.append({
                "time": req['start_time'],
                "command": req['command'],
                "details": req['req_details']
            })

        return {
            "completed": completed_requests,
            "unsol": unsol_events,
            "timeouts": timeout_requests
        }
