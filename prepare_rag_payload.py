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
        """엔지니어가 눈으로 확인할 원본 로그(Metadata) 추출"""
        metadata = {"log_type": log_type}
        
        # ==========================================
        # [핵심 추가 2] 웹 UI 화면 출력을 위해 메인 로그와 교차 로그를 하나로 병합
        # (웹 앱 코드를 수정하지 않아도 자동으로 화면에 뜨게 만드는 트릭!)
        # ==========================================
        cross_logs = []
        if "cross_context_logs" in data_dict and data_dict["cross_context_logs"]:
            cross_logs.append("\n" + "="*40)
            cross_logs.append("🚨 [동시간대 교차 로그 (Main/System/Crash)] 🚨")
            cross_logs.append("="*40)
            cross_logs.extend(data_dict["cross_context_logs"])

        # 원본 로그 (Radio 등) 합치기
        if "logs" in data_dict: 
            combined = data_dict["logs"].copy() + cross_logs
            metadata["raw_logs"] = json.dumps(combined, ensure_ascii=False)
            
        if "context_snapshot" in data_dict: 
            combined = data_dict["context_snapshot"].copy() + cross_logs
            metadata["raw_context"] = json.dumps(combined, ensure_ascii=False)
            
        if "context" in data_dict: 
            combined = data_dict["context"].copy() + cross_logs
            metadata["raw_context"] = json.dumps(combined, ensure_ascii=False)
            
        if "stack" in data_dict: 
            metadata["raw_stack"] = json.dumps(data_dict["stack"], ensure_ascii=False)
            
        if "call_stack" in data_dict: 
            combined = data_dict["call_stack"].copy() + cross_logs
            metadata["raw_stack"] = json.dumps(combined, ensure_ascii=False)

        # 기존 RADIO_POWER 파싱 데이터 등
        if "request_raw" in data_dict: metadata["raw_request"] = data_dict["request_raw"]
        if "response_raw" in data_dict: metadata["raw_response"] = data_dict["response_raw"]
        
        if "request_time" in data_dict: metadata["time"] = data_dict["request_time"]
        if "start_time" in data_dict: metadata["time"] = data_dict["start_time"]
        elif "time" in data_dict: metadata["time"] = data_dict["time"]
        
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

        base_dir = os.path.dirname(os.path.abspath(__file__))
        payload_dir = os.path.join(base_dir, "payloads")
        os.makedirs(payload_dir, exist_ok=True)
        final_output_path = os.path.join(payload_dir, os.path.basename(output_filename))

        with open(final_output_path, 'w', encoding='utf-8') as f:
            json.dump(rag_payload, f, indent=4, ensure_ascii=False)