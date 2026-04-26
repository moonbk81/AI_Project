import re
import json
import os
from parsers.base import BaseParser

class ImsSipProcessor(BaseParser):
    """reSIProcate 기반 VoLTE/IMS SIP 메시지 파서 및 Call Flow 추출기"""

    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.parsed_data = []

        # 💡 [신규 추가] 주요 VoLTE / IMS SIP 응답 코드 사전
        self.sip_status_map = {
            100: "Trying", 180: "Ringing", 183: "Session Progress",
            200: "OK", 202: "Accepted",
            400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
            404: "Not Found", 408: "Request Timeout", 480: "Temporarily Unavailable",
            486: "Busy Here", 487: "Request Terminated", 488: "Not Acceptable Here",
            500: "Server Internal Error", 503: "Service Unavailable", 504: "Server Time-out",
            603: "Decline"
        }

    def get_status_desc(self, code):
        return self.sip_status_map.get(code, "Unknown Status")

    def analyze(self, lines):
        self.parsed_data = []
        sip_pattern = re.compile(
            r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?reSIProcate:.*?Sip(Req|Resp):\s*([a-zA-Z0-9]+).*?tid=([a-zA-Z0-9]+)\s+cseq=(\d+)\s+([a-zA-Z]+).*?from\((wire|tu)\)'
        )

        for line in lines:
            clean_line = self.clean_line(line)

            match = sip_pattern.search(clean_line)
            if match:
                time_str = match.group(1)
                msg_type = match.group(2)          # Req or Resp
                method_or_code = match.group(3)    # 183, 200, INVITE, BYE, 403 등 모두 캡처
                tid = match.group(4)
                cseq_num = match.group(5)
                cseq_method = match.group(6)
                direction = match.group(7)

                dir_str = "Rx ⬇️" if direction == "wire" else "Tx ⬆️"

                is_error = False
                display_method = method_or_code

                # 💡 [개선] 숫자로 된 Status Code일 경우, 사전을 참조하여 "403 Forbidden" 형태로 강화
                if msg_type == "Resp" and method_or_code.isdigit():
                    code = int(method_or_code)
                    display_method = f"{code} {self.get_status_desc(code)}"
                    if code >= 400: # 4xx, 5xx, 6xx 계열은 모두 에러 처리
                        is_error = True

                self.parsed_data.append({
                    'time': time_str,
                    'log_type': 'IMS_SIP_Message',
                    'direction': dir_str,
                    'msg_type': msg_type,
                    'method_code': display_method, # 텍스트가 보강된 결과 저장
                    'tid': tid,
                    'cseq': f"{cseq_num} {cseq_method}",
                    'is_error': is_error,
                    'raw_log': line
                })

        return self.parsed_data

    def save_ui_report(self, output_dir="./result", base_name=""):
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{base_name}_ims_sip.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(self.parsed_data if self.parsed_data else [], f, indent=4, ensure_ascii=False)