import re
import json
import os
from parsers.base import BaseParser

class SatAtProcessor(BaseParser):
    """AP(Framework) ↔ RIL ↔ CP(Modem) 3-Tier 위성 제어 풀스택 분석기"""

    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.parsed_data = {
            "call_flow": [],
            "registration_history": [],
            "metrics": {
                "arfcn": "Unknown",
                "last_rssi": "Unknown",
                "last_snr": "Unknown",
                "calls_total": 0,
                "sms_rx": 0,
                "sms_tx": 0,
                "current_reg_state": "Unknown"
            }
        }

        # 1. AT Command 추출 정규식
        self.re_at_cmd = re.compile(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?AtRawData\[(TX|RX)\].*?(?:Send|Received):\s*(.*)')
        # 2. Framework RILJ 추출 정규식 (예: [UNSL]< UNSOL_SAT_...)
        self.re_rilj = re.compile(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?SATELLITE_RILJ:\s*\[(.*?)\]([<>])\s*(.*)')

    def analyze(self, lines):
        flow_messages = []
        reg_history = []
        metrics = self.parsed_data["metrics"]

        # 🚨 [신규] KPI 매트릭스 확장 및 상태 추적 변수
        metrics.update({
            "calls_dropped_or_failed": 0,
            "sms_tx_attempt": 0,
            "sms_tx_success": 0,
            "sms_tx_fail": 0
        })
        last_tx_cmd = "" # 직전에 모뎀으로 보낸(TX) AT 커맨드 기억용

        # 🚨 노이즈 데이터 철벽 방어
        noise_prefixes = (
            'Rssi:', '+RSSI_', '+CESQ:', '+NETSTAT', '+NETFLAG',
            '^airInfo', '+CALLERROR:',
            'dl rrc', 'ul rrc', 'dl rb', 'ul rb', 'dl page', 'dl sec', 'ul sec', 'dl agch'
        )

        for line in lines:
            clean_line = self.clean_line(line)

            # ==========================================
            # A. Framework (SATELLITE_RILJ) 파싱
            # ==========================================
            rilj_match = self.re_rilj.search(clean_line)
            if rilj_match:
                time_str, req_id, dir_sym, content = rilj_match.groups()
                content = content.strip()

                if "UNSOL_SAT_SIGNAL_STRENGTH_CHANGED" in content:
                    m_sig = re.search(r'rssi:\s*(-?\d+)\s*snr:\s*(-?\d+)', content)
                    if m_sig:
                        metrics["last_rssi"] = m_sig.group(1)
                        metrics["last_snr"] = m_sig.group(2)
                    continue

                if "UNSOL_SAT_REGISTRATION_STATE_CHANGED" in content:
                    m_reg = re.search(r'regState=([A-Z_]+)', content)
                    m_arfcn = re.search(r'arfcn=(\d+)', content)
                    if m_reg:
                        state = m_reg.group(1)
                        metrics["current_reg_state"] = state
                        reg_history.append({"time": time_str, "status_str": state, "raw": content})
                    if m_arfcn: metrics["arfcn"] = m_arfcn.group(1)
                    continue

                # 💡 Call End Reason 기반 통화 실패/드랍 감지
                if "SAT_GET_CALL_END_REASON" in content and "causeCode" in content:
                    m_cause = re.search(r'causeCode:\s*(\d+)', content)
                    if m_cause and m_cause.group(1) != "16":
                        metrics["calls_dropped_or_failed"] += 1
                        flow_messages.append({
                            'time': time_str, 'src': 1, 'dst': 0,
                            'desc': f"❌ Call Drop/Fail (Cause: {m_cause.group(1)})",
                            'is_highlight': True, 'raw': content
                        })
                    continue # 상태 보고용이므로 아래 Flow 추가 로직은 스킵

                # 일반 RILJ Flow에 그릴 항목 설정 (0: AP, 1: RIL)
                is_highlight = "SAT_ANSWER" in content or "SAT_SET_POWER" in content or "CALL_STATE_CHANGED" in content

                if dir_sym == '>': # AP -> RIL
                    src, dst = 0, 1
                    desc = content.split(' ')[0]
                else:              # RIL -> AP
                    src, dst = 1, 0
                    desc = content.split(' ')[0]

                if "SAT_GET_CALL_STATE" in desc: desc = "GET_CALL_STATE"

                flow_messages.append({
                    'time': time_str, 'src': src, 'dst': dst,
                    'desc': desc, 'is_highlight': is_highlight, 'raw': content
                })
                continue

            # ==========================================
            # B. Modem (AT Command) 파싱
            # ==========================================
            at_match = self.re_at_cmd.search(clean_line)
            if at_match:
                time_str, direction, raw_cmd = at_match.groups()
                raw_cmd = raw_cmd.strip()

                # 노이즈 패턴과 일치하면 즉시 스킵
                if any(raw_cmd.startswith(prefix) or prefix in raw_cmd for prefix in noise_prefixes):
                    continue

                if raw_cmd.startswith("+BINFO:"):
                    m_arfcn = re.search(r'\+BINFO:(\d+)', raw_cmd)
                    if m_arfcn: metrics["arfcn"] = m_arfcn.group(1)

                if raw_cmd.startswith("+CREG:"): continue

                if raw_cmd.startswith("+CMT:"): metrics["sms_rx"] += 1
                elif raw_cmd.startswith("+CMGS:"): metrics["sms_tx"] += 1
                elif raw_cmd.startswith("RING") or raw_cmd.startswith("ATD"): metrics["calls_total"] += 1

                is_highlight = False
                desc = raw_cmd[:30] + ("..." if len(raw_cmd)>30 else "")

                # ⬆️ [TX 처리] RIL -> CP
                if direction == 'TX':
                    last_tx_cmd = raw_cmd # 직전 명령어 업데이트
                    src, dst = 1, 2

                    if "ATA" in raw_cmd or "AT+CFUN" in raw_cmd or "ATD" in raw_cmd or "ATH" in raw_cmd:
                        is_highlight = True
                    if raw_cmd.startswith("AT+CMGS"):
                        metrics["sms_tx_attempt"] += 1

                # ⬇️ [RX 처리] CP -> RIL
                else:
                    src, dst = 2, 1

                    if "RING" in raw_cmd or "^CEND" in raw_cmd or "+CEND" in raw_cmd:
                        is_highlight = True

                    # 동기식 커맨드 ERROR 감지
                    if raw_cmd == "ERROR" and last_tx_cmd:
                        if any(cmd in last_tx_cmd for cmd in ["ATA", "ATD", "ATH"]):
                            metrics["calls_dropped_or_failed"] += 1
                            desc = f"❌ Call CMD Fail ({last_tx_cmd.strip()})"
                            is_highlight = True
                        elif "CMGS" in last_tx_cmd:
                            metrics["sms_tx_fail"] += 1
                            desc = "❌ SMS 전송 실패 (AT+CMGS ERROR)"
                            is_highlight = True

                    # 비동기식 SMS 전송 결과 리포트 감지
                    elif raw_cmd.startswith("+SMS:"):
                        m_sms = re.search(r'\+SMS:\s*\d+,\s*(\d+)(?:,\s*(\d+))?', raw_cmd)
                        if m_sms:
                            state, err = m_sms.groups()
                            if state == "1":
                                metrics["sms_tx_success"] += 1
                                desc = "✅ SMS 전송 성공"
                                is_highlight = True
                            elif state == "0":
                                metrics["sms_tx_fail"] += 1
                                err_str = f" (Err: {err})" if err else ""
                                desc = f"❌ SMS 전송 실패{err_str}"
                                is_highlight = True

                flow_messages.append({
                    'time': time_str, 'src': src, 'dst': dst,
                    'desc': desc, 'is_highlight': is_highlight, 'raw': raw_cmd
                })

        self.parsed_data["call_flow"] = flow_messages
        self.parsed_data["registration_history"] = reg_history
        return self.parsed_data

    def save_ui_report(self, output_dir="./result", base_name=""):
        os.makedirs(output_dir, exist_ok=True)
        filename = f"{base_name}_sat_at.json" if base_name else "sat_at_parsed_logs.json"
        out_path = os.path.join(output_dir, filename)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(self.parsed_data, f, indent=4, ensure_ascii=False)

