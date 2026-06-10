"""Network-related RAG payload builders."""

from rag_builders.common import append_payload, append_callback_payload, source_file_name

def build_network_timeseries_payloads(report_data, build_markdown_doc, extract_metadata):
    rag_payload = []
    net_data = report_data.get("network_timeseries") or {}
    timeline = net_data.get("sorted_timeline", {})

    for ts, details in timeline.items():
        for stat in details.get("net_stats", []):
            stat_item = {
                "time": ts,
                "log_type": "Network_Timeline_Stat",
                "netId": stat.get("netId"),
                "transport": stat.get("transport"),
                "dns_avg": stat.get("dns_avg"),
                "dns_err_rate": stat.get("dns_err_rate"),
                "tcp_avg_loss": stat.get("tcp_avg_loss"),
            }
            doc = f"Network Stat at {ts}: netId={stat.get('netId')}, DNS Avg={stat.get('dns_avg')}ms"
            append_payload(rag_payload, doc, stat_item)

    for dns_issue in net_data.get("dns_issues", []):
        dns_issue["log_type"] = "Network_DNS_Issue"
        doc = (
            f"DNS Blocked Event: Package {dns_issue['package']} (UID: {dns_issue['uid']}) "
            f"was blocked. Effective Policy: {dns_issue.get('effective_policy', 'Unknown')}. "
            f"Time: {dns_issue['time']}"
        )
        append_payload(rag_payload, doc, dns_issue)

    if net_data.get("sorted_timeline"):
        summary = {"timeline_count": len(net_data["sorted_timeline"])}
        append_callback_payload(
            rag_payload,
            summary,
            "Network_Timeline_Summary",
            build_markdown_doc,
            extract_metadata,
        )

    return rag_payload

def build_data_usage_payloads(report_data, input_file):
    rag_payload = []
    source_file = source_file_name(input_file)

    for usage in report_data.get("data_usage_stats", []) or []:
        if usage.get("total_mb", 0) < 0.1:
            continue

        meta = {
            "source_file": source_file,
            "log_type": "Data_Usage",
            "time": usage.get("time", "시간 미상"),
            "app_name": usage.get("app_name", "Unknown"),
            "rat": usage.get("rat", "Unknown"),
            "total_mb": usage.get("total_mb", 0.0),
            "rx_mb": usage.get("rx_mb", 0.0),
            "tx_mb": usage.get("tx_mb", 0.0),
        }
        text_content = (
            f"[{meta['time']}] 데이터 사용량 기록: {meta['app_name']} 앱이 {meta['rat']} 망에서 "
            f"총 {meta['total_mb']} MB의 셀룰러 데이터를 사용했습니다. "
            f"(다운로드: {meta['rx_mb']} MB, 업로드: {meta['tx_mb']} MB)"
        )
        append_payload(rag_payload, text_content, meta)

    return rag_payload

def build_internet_stall_payloads(report_data, build_markdown_doc, extract_metadata):
    rag_payload = []
    stall_data = report_data.get("internet_stall", {}) or {}
    stall_windows = stall_data.get("stall_windows", []) or []

    top_windows = sorted(
        stall_windows,
        key=lambda w: w.get("severity_score", 0),
        reverse=True
    )[:5]

    for window in top_windows:
        append_callback_payload(rag_payload, window, "Internet_Stall_Analysis", build_markdown_doc, extract_metadata)

    return rag_payload

def build_network_payloads(report_data, input_file, build_markdown_doc, extract_metadata):
    rag_payload = []
    rag_payload.extend(build_network_timeseries_payloads(report_data, build_markdown_doc, extract_metadata))
    rag_payload.extend(build_data_usage_payloads(report_data, input_file))
    rag_payload.extend(build_internet_stall_payloads(report_data, build_markdown_doc, extract_metadata))
    return rag_payload
