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
        # if "cross_context_logs" in data_dict and data_dict["cross_context_logs"]:
        #     lines.append("\n[동시간대 타 버퍼(Main/System) 교차 로그]")
        #     # 토큰 절약을 위해 교차 로그 중 최대 50줄만 LLM에게 제공 (핵심 에러 파악용)
        #     cross_summary = data_dict["cross_context_logs"][:50]
        #     lines.extend(cross_summary)

        return "\n".join(lines)

    def _extract_metadata(self, data_dict, log_type):
        """엔지니어가 확인할 정보와 차트용 수치를 메타데이터에 포함 (에러 원천 차단)"""
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

        if data_dict.get("slot"): metadata["slot"] = data_dict.get("slot")
        elif data_dict.get("slotId"): metadata["slot"] = data_dict.get("slotId")

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

        if "network_timeseries" in report_data:
            net_data = report_data["network_timeseries"]
            timeline = net_data.get("sorted_timeline", {})

            # 1. 시계열 통계 데이터 평탄화 (그래프용)
            for ts, details in timeline.items():
                for stat in details.get("net_stats", []):
                    # 이 구조가 web_app.py의 px.line이 읽는 데이터 구조가 됩니다.
                    stat_item = {
                        "time": ts,
                        "log_type": "Network_Timeline_Stat", # 중요: log_type 명시
                        "netId": stat.get("netId"),
                        "transport": stat.get("transport"),
                        "dns_avg": stat.get("dns_avg"),
                        "dns_err_rate": stat.get("dns_err_rate"),
                        "tcp_avg_loss": stat.get("tcp_avg_loss")
                    }
                    # 별도의 document 텍스트 생성
                    doc = f"Network Stat at {ts}: netId={stat.get('netId')}, DNS Avg={stat.get('dns_avg')}ms"
                    rag_payload.append({"document": doc, "metadata": stat_item})
                # DNS 이슈들을 개별 지식 조각으로 추가
                for dns_issue in net_data.get("dns_issues", []):
                    dns_issue["log_type"] = "Network_DNS_Issue"
                    # LLM에게 전달될 문장(Document) 강화
                    doc = (
                        f"DNS Blocked Event: Package {dns_issue['package']} (UID: {dns_issue['uid']}) "
                        f"was blocked. Effective Policy: {dns_issue.get('effective_policy', 'Unknown')}. "
                        f"Time: {dns_issue['time']}"
                    )
                    rag_payload.append({"document": doc, "metadata": dns_issue})


                # 시계열 통계 요약본 추가
                if net_data.get("sorted_timeline"):
                    summary = {"timeline_count": len(net_data["sorted_timeline"]), "device_config": net_data.get("device_config")}
                    add_to_payload(summary, "Network_Timeline_Summary")

        base_dir = os.path.dirname(os.path.abspath(__file__))
        payload_dir = os.path.join(base_dir, "payloads")
        os.makedirs(payload_dir, exist_ok=True)
        final_output_path = os.path.join(payload_dir, os.path.basename(output_filename))

        with open(final_output_path, 'w', encoding='utf-8') as f:
            json.dump(rag_payload, f, indent=4, ensure_ascii=False)
