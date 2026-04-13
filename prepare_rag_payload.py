import json
import os
import argparse

class RagPayloadBuilder:
    def __init__(self, input_file):
        self.input_file = input_file

    def _build_markdown_doc(self, data_dict, log_type):
        """임베딩 및 LLM이 읽을 아주 가벼운 본문(Document) 생성"""
        lines = [f"### [Type: {log_type}]"]

        # 제외할 키워드에 cross_context_logs 추가 (수동으로 예쁘게 붙이기 위함)
        exclude_keys = ["logs", "context_snapshot", "context", "stack", "call_stack", "raw_logs", "cross_context_logs"]

        for key, value in data_dict.items():
            if key in exclude_keys:
                continue
            if isinstance(value, dict):
                for sub_k, sub_v in value.items():
                    lines.append(f"- {key}_{sub_k}: {sub_v}")
            else:
                lines.append(f"- {key}: {value}")

        # ==========================================
        # [핵심 추가 1] LLM이 읽을 본문에 교차 로그 삽입
        # ==========================================
        if "cross_context_logs" in data_dict and data_dict["cross_context_logs"]:
            lines.append("\n[동시간대 타 버퍼(Main/System) 교차 로그]")
            # 토큰 절약을 위해 교차 로그 중 최대 50줄만 LLM에게 제공 (핵심 에러 파악용)
            cross_summary = data_dict["cross_context_logs"][:50]
            lines.extend(cross_summary)

        return "\n".join(lines)

    def _extract_metadata(self, data_dict, log_type):
        """엔지니어가 눈으로 확인할 원본 로그(Metadata) 추출 (json.dumps 메모리 폭발 방지)"""
        metadata = {"log_type": log_type}
        
        cross_logs = []
        if "cross_context_logs" in data_dict and data_dict["cross_context_logs"]:
            cross_logs.append("\n" + "="*40)
            cross_logs.append("🚨 [동시간대 교차 로그 (Main/System/Crash)] 🚨")
            cross_logs.append("="*40)
            # 교차 로그도 최대 150줄만 유지하여 렌더링 및 변환 부하 방지
            cross_logs.extend(data_dict["cross_context_logs"][:150])

        # ==========================================
        # 🚨 [핵심 방어막] 수십만 줄의 배열을 안전하게 자르는 함수
        # ==========================================
        MAX_LINES = 300 # 메타데이터 배열 하나당 허용할 최대 라인 수

        def get_safe_list(log_list):
            if not isinstance(log_list, list): return log_list
            if len(log_list) > MAX_LINES:
                # 에러 파악에 필수적인 앞부분 150줄과 뒷부분 150줄만 보존하고 중간은 생략
                return log_list[:150] + ["\n... [초대용량 로그 중략됨] ...\n"] + log_list[-150:]
            return log_list.copy()

        # 원본 로그 배열에 다이어트 함수 적용 후 교차 로그 결합
        if "logs" in data_dict: 
            combined = get_safe_list(data_dict["logs"]) + cross_logs
            metadata["raw_logs"] = json.dumps(combined, ensure_ascii=False)
            
        if "context_snapshot" in data_dict: 
            combined = get_safe_list(data_dict["context_snapshot"]) + cross_logs
            metadata["raw_context"] = json.dumps(combined, ensure_ascii=False)
            
        if "context" in data_dict: 
            combined = get_safe_list(data_dict["context"]) + cross_logs
            metadata["raw_context"] = json.dumps(combined, ensure_ascii=False)
            
        if "stack" in data_dict: 
            metadata["raw_stack"] = json.dumps(get_safe_list(data_dict["stack"]), ensure_ascii=False)
            
        if "call_stack" in data_dict: 
            combined = get_safe_list(data_dict["call_stack"]) + cross_logs
            metadata["raw_stack"] = json.dumps(combined, ensure_ascii=False)

        # 기존 RADIO_POWER 등 파싱 데이터 유지
        if "request_raw" in data_dict: metadata["raw_request"] = data_dict["request_raw"]
        if "response_raw" in data_dict: metadata["raw_response"] = data_dict["response_raw"]
        
        # 시간 정보 매핑 (배터리 통계 포함)
        if "request_time" in data_dict: metadata["time"] = data_dict["request_time"]
        if "start_time" in data_dict: metadata["time"] = data_dict["start_time"]
        elif "time" in data_dict: metadata["time"] = data_dict["time"]
        elif "stats_period" in data_dict: metadata["time"] = data_dict["stats_period"]
        
        if "slot" in data_dict: metadata["slot"] = data_dict["slot"]
        elif "slotId" in data_dict: metadata["slot"] = data_dict["slotId"]

        return metadata

    def build_payload(self, output_filename=None):
        if not os.path.exists(self.input_file):
            print(f"❌ 파일을 찾을 수 없습니다: {self.input_file}")
            return

        if output_filename is None:
            input_basename = os.path.basename(self.input_file)
            name_without_ext = os.path.splitext(input_basename)[0]
            output_filename = f"{name_without_ext}_rag_payload.json"

        with open(self.input_file, 'r', encoding='utf-8') as f:
            report_data = json.load(f)

        rag_payload = []

        def add_to_payload(item, type_name):
            doc = self._build_markdown_doc(item, type_name)
            meta = self._extract_metadata(item, type_name)
            rag_payload.append({"document": doc, "metadata": meta})

        if "radio_power" in report_data:
            for rp in report_data["radio_power"]:
                add_to_payload(rp, "Radio_Power_Event")

        if "telephony" in report_data:
            for session in report_data["telephony"].get("sessions", []):
                add_to_payload(session, "Call_Session")
            for oos in report_data["telephony"].get("network_history", []):
                add_to_payload(oos, "OOS_Event")

        if "anr_context" in report_data:
            add_to_payload(report_data["anr_context"], "ANR_Context")

        if "crash_context" in report_data:
            for crash in report_data["crash_context"]:
                add_to_payload(crash, "Crash_Event")

        if "battery_stats" in report_data:
            add_to_payload(report_data["battery_stats"], "Battery_Drain_Report")

        base_dir = os.path.dirname(os.path.abspath(__file__))
        payload_dir = os.path.join(base_dir, "payloads")
        os.makedirs(payload_dir, exist_ok=True)
        final_output_path = os.path.join(payload_dir, os.path.basename(output_filename))

        with open(final_output_path, 'w', encoding='utf-8') as f:
            json.dump(rag_payload, f, indent=4, ensure_ascii=False)
