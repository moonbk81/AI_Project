import re
import json
import os

class NtnProcessor:
    """Starlink / T-Mobile Direct-to-Cell 환경 전용 로그 파서 및 RAG 페이로드 빌더"""

    def __init__(self, log_path):
        self.log_path = log_path
        self.filename = os.path.basename(log_path)
        self.parsed_data = []
        self.payloads = []

    def run_parser(self):
        """정규식으로 NTN(위성) 관련 프레임워크 정책/상태 로그를 추출합니다."""
        self.parsed_data = [] # 초기화 안전장치

        if not os.path.exists(self.log_path):
            return []

        with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip() # 개행 문자 및 공백 제거

                # 1. 위성 PLMN 매칭
                match_ntn_plmn = re.search(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?NtnCapabilityResolver:\s*Registered to satellite PLMN\s*(\d+)', line, re.IGNORECASE)
                if match_ntn_plmn:
                    self.parsed_data.append({
                        'time': match_ntn_plmn.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'PLMN_MATCH',
                        'ntn_plmn': match_ntn_plmn.group(2)
                    })
                    continue

                # 2. Radio Power 상태 (모뎀 ON/OFF)
                match_radio_power = re.search(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?(?:setRadioPower.*?=?(true|false)|RadioStateChanged.*RADIO_(ON|OFF))', line, re.IGNORECASE)
                if match_radio_power:
                    val = match_radio_power.group(2) or match_radio_power.group(3)
                    power_state = 'ON' if val.upper() in ['TRUE', 'ON'] else 'OFF'
                    self.parsed_data.append({
                        'time': match_radio_power.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'RADIO_POWER',
                        'power_state': power_state
                    })
                    continue

                # 3. 데이터 서비스 정책 (DataPolicy)
                match_ntn_policy = re.search(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?SatelliteController:\s*getSatelliteDataServicePolicyForPlmn:\s*return data support mode.*?:\s*(\d+)', line, re.IGNORECASE)
                if match_ntn_policy:
                    mode_map = {'1': 'Restricted (SOS)', '2': 'Broadband (Starlink)', '0': 'None'}
                    self.parsed_data.append({
                        'time': match_ntn_policy.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'DATA_POLICY',
                        'data_policy': mode_map.get(match_ntn_policy.group(2), f"Mode {match_ntn_policy.group(2)}")
                    })
                    continue

                # 4. NTN Mode 상태 알림 (상태 전이 추적)
                match_ntn_mode = re.search(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?updateLastNotifiedNtnModeAndNotify.*?lastNotifiedNtnMode=(true|false).*?lastNotifiedNtnModePhone=(true|false).*?currNtnMode=(true|false)', line, re.IGNORECASE)
                if match_ntn_mode:
                    self.parsed_data.append({
                        'time': match_ntn_mode.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'NTN_MODE_NOTIFY',
                        'last_ntn_mode': 'ON' if match_ntn_mode.group(2).lower() == 'true' else 'OFF',
                        'last_phone_mode': 'ON' if match_ntn_mode.group(3).lower() == 'true' else 'OFF',
                        'ntn_mode': 'ON' if match_ntn_mode.group(4).lower() == 'true' else 'OFF'
                    })
                    continue

                # 5. Hysteresis 구간 아이콘 유지
                match_hys_icon = re.search(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?isInSatelliteModeForCarrierRoaming.*?connected to satellite within hysteresis time', line, re.IGNORECASE)
                if match_hys_icon:
                    self.parsed_data.append({
                        'time': match_hys_icon.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'HYSTERESIS_ICON_ON',
                        'is_hysteresis': 'True'
                    })
                    continue

        return self.parsed_data

    def save_ui_report(self, output_dir="./result"):
        """UI 대시보드가 직접 읽을 수 있도록 위성 전용 JSON 파일을 분리 저장합니다."""
        if not self.parsed_data:
            return
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, "ntn_parsed_logs.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(self.parsed_data, f, indent=4, ensure_ascii=False)

    def build_and_save_payloads(self, payload_dir="./payloads"):
        """RAG Vector DB용 지식 페이로드 생성"""
        if not self.parsed_data:
            return 0

        os.makedirs(payload_dir, exist_ok=True)

        for item in self.parsed_data:
            event = item.get('event_type', 'Unknown')
            time_str = item.get('time', 'N/A')

            if event == 'PLMN_MATCH':
                text_content = f"[{time_str}] NTN Policy: NtnCapabilityResolver verified and registered to Satellite PLMN {item.get('ntn_plmn')}."
            elif event == 'RADIO_POWER':
                text_content = f"[{time_str}] Modem State: Radio power was turned {item.get('power_state')}."
            elif event == 'DATA_POLICY':
                text_content = f"[{time_str}] NTN Policy: SatelliteDataServicePolicy updated. Allowed data support mode is set to {item.get('data_policy')}."
            elif event == 'NTN_MODE_NOTIFY':
                text_content = f"[{time_str}] NTN Mode Transition: SatelliteController updated NTN mode. Previous notified mode was {item.get('last_ntn_mode')}, and CURRENT mode is now {item.get('ntn_mode')}."
            elif event == 'HYSTERESIS_ICON_ON':
                text_content = f"[{time_str}] NTN UI State: Device is physically evaluating/handover, but is within hysteresis time. The Satellite UI Icon remains ON to prevent flickering."
            else:
                text_content = f"[{time_str}] NTN Policy Event: {event} occurred."

            payload = {
                "document": text_content,
                "metadata": {
                    "source_file": self.filename,
                    "log_type": item['log_type'],
                    "time": time_str,
                    "event_type": event,
                    "ntn_plmn": item.get('ntn_plmn', ''),
                    "power_state": item.get('power_state', ''),
                    "data_policy": item.get('data_policy', ''),
                    "ntn_mode": item.get('ntn_mode', ''),
                    "last_ntn_mode": item.get('last_ntn_mode', ''),
                    "is_hysteresis": item.get('is_hysteresis', '')
                }
            }
            self.payloads.append(payload)

        out_filename = os.path.splitext(self.filename)[0] + "_ntn_payload.json"
        out_path = os.path.join(payload_dir, out_filename)

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(self.payloads, f, indent=4, ensure_ascii=False)

        return len(self.payloads)
