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
            # LLM에게 굳이 안 보여줘도 되는 무거운 원본이나 스택은 여기서 스킵!
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
        
        # 원본 로그가 있는 경우 메타데이터로 빼냄 (ChromaDB 제약상 문자열로 변환하여 저장)
        if "logs" in data_dict:
            metadata["raw_logs"] = json.dumps(data_dict["logs"], ensure_ascii=False)
        if "context_snapshot" in data_dict:
            metadata["raw_context"] = json.dumps(data_dict["context_snapshot"], ensure_ascii=False)
        if "context" in data_dict:
            metadata["raw_context"] = json.dumps(data_dict["context"], ensure_ascii=False)
        if "stack" in data_dict:
            metadata["raw_stack"] = json.dumps(data_dict["stack"], ensure_ascii=False)
        if "call_stack" in data_dict:
            metadata["raw_stack"] = json.dumps(data_dict["call_stack"], ensure_ascii=False)
            
        # 시간이나 슬롯 정보도 메타데이터에 넣어두면 나중에 DB에서 시간순 필터링할 때 유리합니다.
        if "start_time" in data_dict: metadata["time"] = data_dict["start_time"]
        elif "time" in data_dict: metadata["time"] = data_dict["time"]
        
        if "slot" in data_dict: metadata["slot"] = data_dict["slot"]
        elif "slotId" in data_dict: metadata["slot"] = data_dict["slotId"]

        return metadata

    def build_payload(self, output_file):
        if not os.path.exists(self.input_file):
            print(f"❌ 파일을 찾을 수 없습니다: {self.input_file}")
            return

        with open(self.input_file, 'r', encoding='utf-8') as f:
            report_data = json.load(f)

        rag_payload = []

        # 각 항목별로 Document와 Metadata를 분리하여 담기
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

        # 결과 저장
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(rag_payload, f, indent=4, ensure_ascii=False)

        print(f"✅ RAG 적재용 페이로드 생성 완료: {output_file}")
        print(f"   (총 {len(rag_payload)}개의 지식 조각이 준비되었습니다.)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build RAG Payload (Doc + Metadata) from JSON")
    parser.add_argument("input_json", help="원본 diag_report_all.json 파일 경로")
    parser.add_argument("output_json", help="출력될 rag_payload.json 파일 경로", default="rag_payload.json", nargs='?')
    
    args = parser.parse_args()
    RagPayloadBuilder(args.input_json).build_payload(args.output_json)
