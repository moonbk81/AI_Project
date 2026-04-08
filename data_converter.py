import json
import os
import argparse

class RilDataConverter:
    def __init__(self, input_file):
        self.input_file = input_file

    def _convert_to_markdown(self, data_dict, log_type):
        """
        JSON 딕셔너리를 토큰 효율이 높은 형태의 텍스트로 변환합니다.
        """
        lines = [f"### [Type: {log_type}]"]
        
        for key, value in data_dict.items():
            # [토큰 절약 핵심] LLM에게 불필요한 원본 로그 원문이나 너무 긴 컨텍스트 배제
            if key in ["logs", "context_snapshot", "context"]:
                continue
                
            # ANR/Crash의 긴 콜스택은 핵심이 되는 상위 3줄만 남기고 생략
            if key in ["stack", "call_stack"] and isinstance(value, list):
                if len(value) > 3:
                    short_stack = value[:3]
                    lines.append(f"- {key}: {short_stack} ... (truncated)")
                else:
                    lines.append(f"- {key}: {value}")
                continue

            # 중첩된 딕셔너리 구조 평탄화 (Flattening)
            if isinstance(value, dict):
                for sub_k, sub_v in value.items():
                    lines.append(f"- {key}_{sub_k}: {sub_v}")
            else:
                lines.append(f"- {key}: {value}")
                
        return "\n".join(lines)

    def run_conversion(self, output_file):
        if not os.path.exists(self.input_file):
            print(f"❌ 파일을 찾을 수 없습니다: {self.input_file}")
            return

        with open(self.input_file, 'r', encoding='utf-8') as f:
            report_data = json.load(f)

        converted_docs = []

        # 1. Telephony (Call & OOS 처리)
        if "telephony" in report_data:
            for session in report_data["telephony"].get("sessions", []):
                converted_docs.append(self._convert_to_markdown(session, "Call_Session"))
            
            for oos in report_data["telephony"].get("network_history", []):
                converted_docs.append(self._convert_to_markdown(oos, "OOS_Event"))

        # 2. ANR 처리
        if "anr_context" in report_data:
            anr = report_data["anr_context"]
            # ANR은 단일 딕셔너리일 수도 있으므로 바로 변환
            converted_docs.append(self._convert_to_markdown(anr, "ANR_Context"))

        # 3. Crash 처리
        if "crash_context" in report_data:
            for crash in report_data["crash_context"]:
                converted_docs.append(self._convert_to_markdown(crash, "Crash_Event"))

        # 최종 결과를 하나의 텍스트 파일로 저장 (나중에 줄 단위나 블록 단위로 쪼개어 임베딩 가능)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("\n\n".join(converted_docs))

        print(f"✅ 변환 완료! 토큰 최적화된 파일이 저장되었습니다: {output_file}")

if __name__ == "__main__":
    # 터미널에서 실행할 때의 인자 처리
    parser = argparse.ArgumentParser(description="Convert Telephony JSON to LLM-friendly Markdown")
    parser.add_argument("input_json", help="분석이 완료된 원본 JSON 리포트 파일")
    parser.add_argument("output_txt", help="변환된 Markdown 텍스트를 저장할 파일 이름", default="optimized_for_rag.txt", nargs='?')
    
    args = parser.parse_args()
    
    converter = RilDataConverter(args.input_json)
    converter.run_conversion(args.output_txt)
