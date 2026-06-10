import json
import os
import argparse

from rag_builders.builder import build_all_payloads

class RagPayloadBuilder:
    def __init__(self, input_file):
        self.input_file = input_file

    def _build_markdown_doc(self, data_dict, log_type):
        """임베딩 및 LLM이 읽을 아주 가벼운 본문(Document) 생성"""
        lines = [f"### [Type: {log_type}]"]

        # 제외할 키워드 (전체 원문을 다 넣으면 임베딩 품질이 떨어지므로 일단 제외)
        exclude_keys = ["logs", "context_snapshot", "context", "stack", "call_stack", "raw_logs", "cross_context_logs"]

        for key, value in data_dict.items():
            if key in exclude_keys:
                continue
            if isinstance(value, dict):
                for sub_k, sub_v in value.items():
                    lines.append(f"- {key}_{sub_k}: {sub_v}")
            else:
                lines.append(f"- {key}: {value}")

        # 원문 로그가 임베딩 모델의 눈에 띄도록, 에러가 몰려있는 마지막 5줄만 강제로 본문에 삽입합니다.
        raw_snippets = []
        for exc_key in exclude_keys:
            if exc_key in data_dict and data_dict[exc_key]:
                val = data_dict[exc_key]
                if isinstance(val, list) and len(val) > 0:
                    # 긴 스택이나 로그에서 핵심 단서가 있는 뒷부분 추출 (최대 5줄)
                    snippet = "\n".join(str(x) for x in val[-5:])
                    raw_snippets.append(f"[{exc_key} Key Snippet]:\n{snippet}")

        if raw_snippets:
            lines.append("\n" + "\n".join(raw_snippets))

        return "\n".join(lines)

    def _extract_metadata(self, data_dict, log_type):
        """엔지니어가 확인할 정보와 차트용 수치를 메타데이터에 포함 (에러 원천 차단)"""
        base_name = os.path.basename(self.input_file).replace("_report.json", "")
        metadata = {"log_type": log_type}

        def add_safe_meta(key, val):
            if "stack" in key: return
            if val in [None, [], {}]: return
            if not isinstance(val, (str, int, float, bool)):
                metadata[key] = str(val)
            else:
                metadata[key] = val

        for k, v in data_dict.items():
            if k in ["logs", "context_snapshot", "context", "stack", "call_stack", "raw_logs", "cross_context_logs"]:
                continue
            if isinstance(v, dict):
                for sub_k, sub_v in v.items():
                    add_safe_meta(f"{k}_{sub_k}", sub_v)
            else:
                add_safe_meta(k, v)

        MAX_LINES = 300
        def get_safe_list(log_list):
            if not isinstance(log_list, list): return log_list
            if len(log_list) > MAX_LINES:
                return log_list[:150] + ["\n... [초대용량 로그 중략됨] ...\n"] + log_list[-150:]
            return log_list.copy()

        # 🚨 100% 안전한 키 접근 (.get 방식)
        if data_dict.get("logs"):
            metadata["raw_logs"] = json.dumps(get_safe_list(data_dict.get("logs")), ensure_ascii=False)
        if data_dict.get("context_snapshot"):
            metadata["raw_context"] = json.dumps(get_safe_list(data_dict.get("context_snapshot")), ensure_ascii=False)
        if data_dict.get("context"):
            metadata["raw_context"] = json.dumps(get_safe_list(data_dict.get("context")), ensure_ascii=False)

        if data_dict.get("request_time"): metadata["time"] = data_dict.get("request_time")
        elif data_dict.get("start_time"): metadata["time"] = data_dict.get("start_time")
        elif data_dict.get("time"): metadata["time"] = data_dict.get("time")
        elif data_dict.get("stats_period"): metadata["time"] = data_dict.get("stats_period")
        # 🚨 Boot_Stat의 Time_ms도 표준 time으로 인식하게 함
        elif data_dict.get("Time_ms") is not None: metadata["time"] = data_dict.get("Time_ms")


        if data_dict.get("slot"): metadata["slot"] = data_dict.get("slot")
        elif data_dict.get("slotId"): metadata["slot"] = data_dict.get("slotId")

        # 🚨 Signal_Level 전용 메타데이터 통과시키기
        if data_dict.get("rat") is not None:
            metadata["rat"] = data_dict.get("rat")
        if data_dict.get("level") is not None:
            metadata["level"] = data_dict.get("level")
        if data_dict.get("raw_info"):
            metadata["raw_info"] = data_dict.get("raw_info")

        if data_dict.get("top_method") is not None:
            metadata["top_method"] = data_dict.get("top_method")
        if data_dict.get("exception_info") is not None:
            metadata["exception_info"] = data_dict.get("exception_info")

        return metadata

    def _extract_global_metadata(self, report_data):
        """
        report_data에 포함된 단말기 전역 정보(Build Info)를 1회성 메타로 추출합니다.

        주의:
        - 이 값들은 모든 RAG 문서에 반복 주입하지 않습니다.
        - kernel/radio/model 같은 공통 정보가 retrieved_meta마다 반복되면
          정작 이벤트 판단 필드가 밀릴 수 있기 때문입니다.
        """
        if "build_info" not in report_data or not isinstance(report_data["build_info"], dict):
            return {}

        b_info = report_data["build_info"]
        return {
            "model_name": b_info.get("model_name", "Unknown"),
            "hardware": b_info.get("hardware", "Unknown"),
            "android_sdk": b_info.get("android_sdk", "Unknown"),
            "radio": b_info.get("radio", "Unknown"),
            "kernel": b_info.get("kernel", "Unknown"),
        }

    def _strip_repeated_global_metadata(self, rag_payload):
        """
        기존 payload/metadata 안에 반복 저장된 공통 단말 메타를 제거합니다.
        이벤트별 판단에 필요한 metadata만 남겨 retrieved_meta 노이즈를 줄입니다.
        """
        repeated_keys = {"model_name", "hardware", "android_sdk", "radio", "kernel"}

        for payload in rag_payload:
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                continue
            for key in repeated_keys:
                metadata.pop(key, None)

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

        rag_payload.extend(
            build_all_payloads(
                report_data,
                self.input_file,
                self._build_markdown_doc,
                self._extract_metadata,
            )
        )

        global_metadata = self._extract_global_metadata(report_data)
        self._strip_repeated_global_metadata(rag_payload)

        # 기존 리스트 기반 payload 구조를 유지하면 전역 메타를 "바깥에 1회" 둘 수 없으므로,
        # 출력 JSON을 wrapper 형태로 저장합니다.
        # downstream에서는 data.get("payloads", data) 형태로 읽으면 기존 리스트/신규 wrapper 모두 호환됩니다.
        output_data = {
            "global_metadata": global_metadata,
            "payloads": rag_payload,
        }

        base_dir = os.path.dirname(os.path.abspath(__file__))
        payload_dir = os.path.join(base_dir, "payloads")
        os.makedirs(payload_dir, exist_ok=True)
        final_output_path = os.path.join(payload_dir, os.path.basename(output_filename))

        with open(final_output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

