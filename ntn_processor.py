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
        if not os.path.exists(self.log_path):
            return []

        with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                # 1. 위성 PLMN 매칭 (NtnCapabilityResolver)
                match_ntn_plmn = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*NtnCapabilityResolver:\sRegistered to satellite PLMN\s(\d+)', line)
                if match_ntn_plmn:
                    self.parsed_data.append({
                        'time': match_ntn_plmn.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'PLMN_MATCH',
                        'ntn_plmn': match_ntn_plmn.group(2)
                    })
                    continue

                # 2. (신규) Radio Power 상태 추적 (모뎀 ON/OFF)
                # AOSP 표준인 setRadioPower 또는 RadioStateChanged 로그를 캐치합니다.
                match_radio_power = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*(?:setRadioPower.*?=?(true|false)|RadioStateChanged.*RADIO_(ON|OFF))', line, re.IGNORECASE)
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

                # 3. 데이터 서비스 정책 확인 (DataServicePolicy)
                match_ntn_policy = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*SatelliteController:\sgetSatelliteDataServicePolicyForPlmn:\sreturn data support mode.*:\s(\d+)', line)
                if match_ntn_policy:
                    mode_map = {'1': 'Restricted (SOS)', '2': 'Broadband (Starlink)', '0': 'None'}
                    self.parsed_data.append({
                        'time': match_ntn_policy.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'DATA_POLICY',
                        'data_policy': mode_map.get(match_ntn_policy.group(2), f"Mode {match_ntn_policy.group(2)}")
                    })
                    continue

                # 4. NTN Mode 상태 알림 (실제 상태 및 UI 아이콘 트리거)
                # 정규식 패턴 수정: updateLastNotifiedNtnModeAndNotify 정확히 매칭
                match_ntn_mode = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*updateLastNotifiedNtnModeAndNotify.*currNtnMode=(true|false)', line, re.IGNORECASE)
                if match_ntn_mode:
                    self.parsed_data.append({
                        'time': match_ntn_mode.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'NTN_MODE_NOTIFY',
                        'ntn_mode': 'ON' if match_ntn_mode.group(2).lower() == 'true' else 'OFF'
                    })
                    continue

                # 5. Hysteresis 구간 (물리적 단절이나 UI 위성 아이콘 유지 구간)
                match_hys_icon = re.search(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*isInSatelliteModeForCarrierRoaming.*?connected to satellite within hysteresis time', line, re.IGNORECASE)
                if match_hys_icon:
                    self.parsed_data.append({
                        'time': match_hys_icon.group(1),
                        'log_type': 'NTN_Policy',
                        'event_type': 'HYSTERESIS_ICON_ON',
                        'is_hysteresis': 'True'
                    })
                    continue

        return self.parsed_data

    def build_and_save_payloads(self, payload_dir="./payloads"):
        """파싱된 데이터를 RAG Vector DB가 읽을 수 있는 문서 구조로 변환하여 저장합니다."""
        if not self.parsed_data:
            return 0

        os.makedirs(payload_dir, exist_ok=True)

        for item in self.parsed_data:
            event = item.get('event_type', 'Unknown')
            time_str = item.get('time', 'N/A')

            # AI가 문맥을 정확히 이해하도록 자연어로 변환
            if event == 'PLMN_MATCH':
                text_content = f"[{time_str}] NTN Policy: NtnCapabilityResolver verified and registered to Satellite PLMN {item.get('ntn_plmn')}. The device recognizes this PLMN as a Non-Terrestrial Network (Starlink/T-Mobile roaming)."
            elif event == 'RADIO_POWER':
                text_content = f"[{time_str}] Modem State: Radio power was turned {item.get('power_state')}."
            elif event == 'NTN_MODE_NOTIFY':
                text_content = f"[{time_str}] NTN Mode: SatelliteController updated notified NTN mode. Current NTN Mode is {item.get('ntn_mode')}."
            elif event == 'HYSTERESIS_ICON_ON':
                text_content = f"[{time_str}] NTN UI State: Device is physically evaluating/handover, but is within hysteresis time. The Satellite UI Icon remains ON to prevent flickering."
            elif event == 'DATA_POLICY':
                text_content = f"[{time_str}] NTN Policy: SatelliteDataServicePolicy updated. Allowed data support mode is set to {item.get('data_policy')}."
            else:
                text_content = f"[{time_str}] NTN Policy Event: {event} occurred."

            # 메타데이터를 포함한 Payload 객체 조립
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
                    "raw_info": item.get('raw_info', '')
                }
            }
            self.payloads.append(payload)

        # JSON 파일로 저장 (기존 파이프라인에서 engine.ingest_folder가 읽어갈 수 있도록)
        out_filename = os.path.splitext(self.filename)[0] + "_ntn_payload.json"
        out_path = os.path.join(payload_dir, out_filename)

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(self.payloads, f, indent=4, ensure_ascii=False)

        return len(self.payloads)

