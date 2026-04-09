import json
import os
import argparse

class RagPayloadBuilder:
    def __init__(self, input_file):
        self.input_file = input_file

    def _build_markdown_doc(self, data_dict, log_type):
        """임베딩 및 LLM이 읽을 아주 가벼운 본문(Document) 생성"""
        lines = [f"### [Type: {log_type}]"]
        for key, value in data_dict.items():
            if key in ["logs", "context_snapshot", "context", "stack", "call_stack", "raw_logs"]:
                continue
            if isinstance(value, dict):
                for sub_k, sub_v in value.items():
                    lines.append(f"- {key}_{sub_k}: {sub_v}")
            else:
                lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def _extract_metadata(self, data_dict, log_type):
        """엔지니어가 눈으로 확인할 원본 로그(Metadata) 추출"""
        metadata = {"log_type": log_type}
        
        if "logs" in data_dict: metadata["raw_logs"] = json.dumps(data_dict["logs"], ensure_ascii=False)
        if "context_snapshot" in data_dict: metadata["raw_context"] = json.dumps(data_dict["context_snapshot"], ensure_ascii=False)
        if "context" in data_dict: metadata["raw_context"] = json.dumps(data_dict["context"], ensure_ascii=False)
        if "stack" in data_dict: metadata["raw_stack"] = json.dumps(data_dict["stack"], ensure_ascii=False)
        if "call_stack" in data_dict: metadata["raw_stack"] = json.dumps(data_dict["call_stack"], ensure_ascii=False)
            
        if "start_time" in data_dict: metadata["time"] = data_dict["start_time"]
        elif "time" in data_dict: metadata["time"] = data_dict["time"]
        
        if "slot" in data_dict: metadata["slot"] = data_dict["slot"]
        elif "slotId" in data_dict: metadata["slot"] = data_dict["slotId"]

        return metadata

    def build_payload(self, output_filename):
        if not os.path.exists(self.input_file):
            print(f"❌ 파일을 찾을 수 없습니다: {self.input_file}")
            return

        with open(self.input_file, 'r', encoding='utf-8') as f:
            report_data = json.load(f)

        rag_payload = []

        def add_to_payload(item, type_name):
            doc = self._build_markdown_doc(item, type_name)
            meta = self._extract_metadata(item, type_name)
            rag_payload.append({"document": doc, "metadata": meta})

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

        # ---------------------------------------------------------
        # [수정된 핵심 로직] payloads 폴더 자동 생성 및 경로 지정
        # ---------------------------------------------------------
        # 1. 현재 파이썬 스크립트(.py)가 있는 절대 경로를 찾음
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # 2. 그 아래에 'payloads' 폴더 경로 구성
        payload_dir = os.path.join(base_dir, "payloads")
        
        # 3. 폴더가 없으면 새로 생성 (있으면 무시)
        os.makedirs(payload_dir, exist_ok=True)

        # 4. 사용자가 입력한 파일명(예: rag_payload.json)만 추출해서 payloads 폴더 안에 결합
        final_output_path = os.path.join(payload_dir, os.path.basename(output_filename))

        # 결과 저장
        with open(final_output_path, 'w', encoding='utf-8') as f:
            json.dump(rag_payload, f, indent=4, ensure_ascii=False)

        print(f"✅ RAG 적재용 페이로드 생성 완료!")
        print(f"📂 저장 위치: {final_output_path}")
        print(f"   (총 {len(rag_payload)}개의 지식 조각이 준비되었습니다.)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build RAG Payload (Doc + Metadata) from JSON")
    parser.add_argument("input_json", help="원본 diag_report_all.json 파일 경로")
    # 도움말에도 payloads 폴더에 저장된다고 명시해줍니다.
    parser.add_argument("output_json", help="저장될 파일 이름 (자동으로 payloads/ 폴더에 저장됨)", default="rag_payload.json", nargs='?')
    
    args = parser.parse_args()
    RagPayloadBuilder(args.input_json).build_payload(args.output_json)