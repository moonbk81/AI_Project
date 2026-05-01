import re
import json
import os
from parsers.base import BaseParser

class NtnProcessor(BaseParser):
    """Starlink / T-Mobile Direct-to-Cell 환경 전용 로그 파서 및 RAG 페이로드 빌더"""

    def __init__(self, filename="unknown.log", context_getter=None):
        super().__init__(context_getter)
        self.filename = filename
        self.parsed_data = []
        self.payloads = []

    def analyze(self, lines):
        """정규식으로 NTN(위성) 관련 프레임워크 정책/상태 로그를 추출합니다."""
        self.parsed_data = [] # 초기화 안전장치

        # 🚨 [상태 추적 변수] 중복 로그(Spam) 방지를 위해 이전 상태를 기억
        last_plmn = None
        last_radio_power = None
        last_data_policy = None
        last_ntn_mode = None
        last_hysteresis_time = None # Hysteresis 도배 방지용

        for line in lines:
            clean_line = self.clean_line(line)

            # 1. 위성 PLMN 매칭
            match_ntn_plmn = re.search(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?NtnCapabilityResolver:\s*Registered to satellite PLMN\s*(\d+)', line, re.IGNORECASE)
            if match_ntn_plmn:
                current_plmn = match_ntn_plmn.group(2)
                if current_plmn != last_plmn: # 상태가 변했을 때만 추가
                    self.parsed_data.append({
                        'time': match_ntn_plmn.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'PLMN_MATCH',
                        'ntn_plmn': current_plmn
                    })
                    last_plmn = current_plmn
                continue

            # 2. Radio Power 상태 (모뎀 ON/OFF)
            match_radio_power = re.search(r'^((?:\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3})|(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)).*?RADIO_POWER\s+on\s*=\s*(true|false)', line, re.IGNORECASE)
            if match_radio_power:
                raw_time = match_radio_power.group(1)

                if 'T' in raw_time:
                    time_str = raw_time[5:10] + ' ' + raw_time[11:23]
                else:
                    time_str = raw_time

                power_state = 'ON' if match_radio_power.group(2).lower() == 'true' else 'OFF'

                if power_state != last_radio_power:
                    self.parsed_data.append({
                        'time': time_str,
                        'log_type': 'NTN_Policy',
                        'event_type': 'RADIO_POWER',
                        'power_state': power_state
                    })
                    last_radio_power = power_state
                continue

            # 3. 데이터 서비스 정책 (DataPolicy)
            match_ntn_policy = re.search(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?SatelliteController:\s*getSatelliteDataServicePolicyForPlmn:\s*return data support mode.*?:\s*(\d+)', line, re.IGNORECASE)
            if match_ntn_policy:
                mode_map = {'1': 'Restricted (SOS)', '2': 'Broadband (Starlink)', '0': 'None'}
                current_policy = mode_map.get(match_ntn_policy.group(2), f"Mode {match_ntn_policy.group(2)}")

                if current_policy != last_data_policy:
                    self.parsed_data.append({
                        'time': match_ntn_policy.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'DATA_POLICY',
                        'data_policy': current_policy
                    })
                    last_data_policy = current_policy
                continue

            # 4. NTN Mode 상태 알림 (상태 전이 추적)
            match_ntn_mode = re.search(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?updateLastNotifiedNtnModeAndNotify.*?lastNotifiedNtnMode=(true|false).*?lastNotifiedNtnModePhone=(true|false).*?currNtnMode=(true|false)', line, re.IGNORECASE)
            if match_ntn_mode:
                current_ntn_mode = 'ON' if match_ntn_mode.group(4).lower() == 'true' else 'OFF'

                if current_ntn_mode != last_ntn_mode:
                    self.parsed_data.append({
                        'time': match_ntn_mode.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'NTN_MODE_NOTIFY',
                        'last_ntn_mode': 'ON' if match_ntn_mode.group(2).lower() == 'true' else 'OFF',
                        'last_phone_mode': 'ON' if match_ntn_mode.group(3).lower() == 'true' else 'OFF',
                        'ntn_mode': current_ntn_mode
                    })
                    last_ntn_mode = current_ntn_mode
                continue

            # 5. Hysteresis 구간 아이콘 유지 (1초 단위 내 중복 방지)
            match_hys_icon = re.search(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}).*?isInSatelliteModeForCarrierRoaming.*?connected to satellite within hysteresis time', line, re.IGNORECASE)
            if match_hys_icon:
                current_time_sec = match_hys_icon.group(1) # 밀리초를 제외한 초 단위까지만 절삭

                # 동일한 초(sec) 내에 수십 개씩 찍히는 것을 1개로 압축
                if current_time_sec != last_hysteresis_time:
                    self.parsed_data.append({
                        'time': match_hys_icon.group(0)[:18], # 원본 로그의 타임스탬프 일부 사용
                        'log_type': 'NTN_Policy',
                        'event_type': 'HYSTERESIS_ICON_ON',
                        'is_hysteresis': 'True'
                    })
                    last_hysteresis_time = current_time_sec
                continue

        return self.parsed_data

    def save_ui_report(self, output_dir="./result", base_name=""):
        if not self.parsed_data: return
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{base_name}_ntn.json")
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
