from datetime import datetime, timedelta
import json
import os

from agent_toolkit.common import _load_json, _load_report_json
from agent_toolkit.correlation import _check_native_crash_correlation, _check_radio_power_correlation, _check_rf_correlation

def get_network_oos_analytics(base_name: str, result_dir: str = "./result") -> str:
    """망 이탈(OOS) 시점과 후보 원인(Root Cause Candidate)을 추출합니다."""
    report_data = _load_report_json(base_name, result_dir)
    oos_history = report_data.get("oos_events", [])

    oos_facts = []
    for oos in oos_history:
        radio_power_context = _check_radio_power_correlation(
            oos.get("time"),
            report_data,
            window_sec=15
        )

        native_crash_context = _check_native_crash_correlation(
            oos.get("time"),
            report_data,
            window_sec=10
        )

        inferred_reason = oos.get("root_cause_candidate")

        if native_crash_context.get("is_rild_crash_related"):
            inferred_reason = "RILD_CRASH_RESET"
        elif radio_power_context.get("is_user_radio_power_related"):
            inferred_reason = radio_power_context.get("classification")

        oos_facts.append({
            "time": oos.get("time"),
            "event": oos.get("event_type"),
            "slot": oos.get("slotId"),
            "reject_cause": oos.get("rej_cause"),
            "inferred_reason": inferred_reason,
            "original_oos_reason": oos.get("root_cause_candiate"),
            "radio_power_context": radio_power_context,
            "native_crash_context": native_crash_context
        })

    return json.dumps({
        "oos_count": len(oos_facts),
        "oos_events": oos_facts
    }, ensure_ascii=False)

def get_dns_latency_analytics(base_name: str, result_dir: str = "./result") -> str:
    """중증(Critical) DNS 지연 현상과 앱 차단 이력을 추출합니다."""
    report_data = _load_report_json(base_name, result_dir)
    net_ts = report_data.get("network_timeseries", {})
    dns_queries = report_data.get("dns_queries", []) or []

    timeline = net_ts.get("sorted_timeline", {})
    critical_latencies = []
    slow_dns_queries = []
    max_dns_latency_ms = 0
    max_dns_avg_ms = 0
    max_dns_max_ms = 0
    max_dns_delayed_cnt = 0
    max_dns_blocked_cnt = 0

    for ts, details in timeline.items():
        for stat in details.get("net_stats", []):
            dns_avg = stat.get("dns_avg", 0)
            max_dns_avg_ms = max(max_dns_avg_ms, dns_avg)
            max_dns_max_ms = max(max_dns_max_ms, stat.get("dns_max", 0) or 0)
            max_dns_delayed_cnt = max(max_dns_delayed_cnt, stat.get("dns_delayed_cnt", 0) or 0)
            max_dns_blocked_cnt = max(max_dns_blocked_cnt, stat.get("dns_blocked_cnt", 0) or 0)
            if isinstance(dns_avg, (int, float)) and dns_avg > 2000:
                critical_latencies.append({
                    "time": ts,
                    "netId": stat.get("netId"),
                    "dns_avg_ms": dns_avg,
                    "dns_max_ms": stat.get("dns_max"),
                    "dns_delayed_cnt": stat.get("dns_delayed_cnt"),
                    "dns_blocked_cnt": stat.get("dns_blocked_cnt"),
                    "dns_err_rate": stat.get("dns_err_rate")
                })

    for dns in dns_queries:
        if not isinstance(dns, dict):
            continue

        latency_ms = dns.get("latency_ms")
        if not isinstance(latency_ms, (int, float)):
            continue

        max_dns_latency_ms = max(max_dns_latency_ms, latency_ms)

        if latency_ms >= 1000:
            slow_dns_queries.append({
                "time": dns.get("time"),
                "net_id": dns.get("net_id"),
                "uid": dns.get("uid"),
                "app_name": dns.get("app_name"),
                "return_code": dns.get("return_code"),
                "latency_ms": latency_ms
            })

    slow_dns_queries = sorted(
        slow_dns_queries,
        key=lambda x: x.get("latency_ms", 0),
        reverse=True
    )[:20]

    return json.dumps({
        "dns_blocked_apps_count": len(net_ts.get("dns_issues", [])),
        "critical_dns_latency_spikes": critical_latencies,
        "dns_query_count": len(dns_queries),
        "max_dns_latency_ms": max_dns_latency_ms,
        "max_dns_avg_ms": max_dns_avg_ms,
        "max_dns_max_ms": max_dns_max_ms,
        "max_dns_delayed_cnt": max_dns_delayed_cnt,
        "max_dns_blocked_cnt": max_dns_blocked_cnt,
        "slow_dns_query_count": len(slow_dns_queries),
        "slow_dns_queries": slow_dns_queries
    }, ensure_ascii=False)

def get_data_stall_and_recovery_analytics(base_name: str, result_dir: str = "./result") -> str:
    """데이터 스톨(병목) 감지 및 프레임워크의 복구 동작(Recovery Action) 시퀀스를 분석합니다."""
    datacall_path = os.path.join(result_dir, f"{base_name}_datacall.json")
    if not os.path.exists(datacall_path):
        return json.dumps({"error": "Data call report not found. 데이터 스톨 분석 불가."})

    with open(datacall_path, 'r', encoding='utf-8') as f:
        dc_data = json.load(f)

    stall_events = [d for d in dc_data if d.get('event_type') == 'DATA_STALL_RECOVERY']

    if not stall_events:
        return json.dumps({
            "status": "CLEAN",
            "message": "해당 구간 내 데이터 스톨(병목) 및 복구 이력 없음 (정상)"
        }, ensure_ascii=False)

    report_data = _load_report_json(base_name, result_dir)

    analysis = []
    for event in stall_events:
        stall_time = event.get('req_time')
        rf_context = _check_rf_correlation(stall_time, report_data, window_sec=5)

        analysis.append({
            "time": stall_time,
            "action_status": event.get('status'),
            "action_description": event.get('cause'),
            "rf_correlation": rf_context,
            "raw_log": event.get('raw_payload')
        })

    return json.dumps({
        "total_stall_events": len(stall_events),
        "stall_and_recovery_facts": analysis
    }, ensure_ascii=False)

def get_datacall_setup_analytics(base_name: str, result_dir: str = "./result") -> str:
    """
    SetupDataCall / DataCall 설정 실패만 추출합니다.
    DNS/InternetStall/성공 DataCall을 제외하고, 명시적인 데이터 호 설정 실패 사유를 LLM에 제공합니다.
    """
    report_data = _load_report_json(base_name, result_dir)
    datacall_events = report_data.get("datacall_data", []) or []

    failure_events = []
    for event in datacall_events:
        if not isinstance(event, dict):
            continue

        event_type = str(event.get("event_type", "")).upper()
        status = str(event.get("status", "")).upper()
        cause = str(event.get("cause", ""))
        raw_context = str(event.get("raw_context") or event.get("raw_logs") or event.get("raw") or "")
        search_text = f"{event_type} {status} {cause} {raw_context}"

        is_setup_failure = (
            event_type == "DATA_SETUP_FAIL"
            or status == "FAIL"
            or "NOT_SPECIFIED" in search_text
            or "NO CARRIER" in search_text.upper()
            or "AUTHENTICATION FAILED" in search_text.upper()
            or "SETUP_DATA_CALL" in search_text.upper()
        )
        if not is_setup_failure:
            continue

        explicit_reasons = []
        if "NOT_SPECIFIED" in search_text:
            explicit_reasons.append("NOT_SPECIFIED")
        if "NO CARRIER" in search_text.upper():
            explicit_reasons.append("NO CARRIER")
        if "AUTHENTICATION FAILED" in search_text.upper():
            explicit_reasons.append("User authentication failed")
        explicit_reasons = list(dict.fromkeys(explicit_reasons))

        failure_events.append({
            "time": event.get("res_time") or event.get("req_time") or event.get("time"),
            "event_type": event.get("event_type"),
            "status": event.get("status"),
            "cause": event.get("cause"),
            "explicit_reasons": explicit_reasons,
            "apn": event.get("apn"),
            "network": event.get("network"),
            "protocol": event.get("protocol"),
            "cid": event.get("cid"),
            "latency_ms": event.get("latency_ms"),
            "raw_context": raw_context[:4000],
        })

    if not failure_events:
        return json.dumps({
            "status": "NO_DATA",
            "message": "SetupDataCall/DataCall 설정 실패 이벤트가 없습니다.",
            "total_datacall_events": len(datacall_events),
        }, ensure_ascii=False)

    return json.dumps({
        "status": "OK",
        "total_datacall_events": len(datacall_events),
        "setup_failure_count": len(failure_events),
        "setup_failures": failure_events,
        "analysis_rule": (
            "SetupDataCall 실패 원인 질문에서는 Network_DNS_Issue, Internet_Stall_Analysis, "
            "성공 DataCall_Event보다 이 setup_failures의 cause/explicit_reasons를 우선 근거로 사용해야 합니다."
        )
    }, ensure_ascii=False)

def get_internet_stall_analytics(base_name: str, result_dir: str = "./result") -> str:
    """
    인터넷 멈춤 전용 분석 결과를 LLM이 사용하기 좋은 JSON으로 요약합니다.
    """
    path = os.path.join(result_dir, f"{base_name}_internet_stall.json")
    data = _load_json(path, {})

    report_data = _load_report_json(base_name, result_dir)

    if not data:
        return json.dumps({
            "status": "NO_DATA",
            "message": "internet stall 분석 결과 파일이 없습니다.",
            "expected_file": path
        }, ensure_ascii=False)

    kpi = data.get("kpi", {}) or {}
    root_summary = data.get("root_cause_summary", {}) or {}
    windows = data.get("stall_windows", []) or []

    top_windows = sorted(
        windows,
        key=lambda w: w.get("severity_score", 0),
        reverse=True
    )[:5]

    window_facts = []
    for w in top_windows:
        center_time = w.get("center_time")
        radio_power_context = _check_radio_power_correlation(
            center_time, report_data, window_sec=30
        )
        related = w.get("related_events", []) or []
        layer_counts = w.get("layer_counts", {}) or {}

        window_facts.append({
            "center_time": center_time,
            "trigger": w.get("trigger"),
            "severity_score": w.get("severity_score"),
            "layer_counts": layer_counts,
            "root_cause_candidates": w.get("root_cause_candidates", []),
            "radio_power_context": radio_power_context,
            "user_action_hint": radio_power_context.get("is_user_radio_power_related", False),
            "dns_latency_events": [
                {
                    "time": e.get("time"),
                    "latency_ms": e.get("latency_ms"),
                    "reason": e.get("reason")
                }
                for e in related
                if e.get("layer") == "DNS" and e.get("latency_ms")
            ][:10],
            "key_related_events": [
                {
                    "time": e.get("time"),
                    "layer": e.get("layer"),
                    "event_type": e.get("event_type"),
                    "severity": e.get("severity"),
                    "reason": e.get("reason")
                }
                for e in related[:20]
            ]
        })

    radio_power_related_windows = [
        w for w in window_facts
        if w.get("user_action_hint")
    ]
    return json.dumps({
        "status": "OK",
        "kpi": {
            "stall_window_count": kpi.get("stall_window_count", 0),
            "high_risk_window_count": kpi.get("high_risk_window_count", 0),
            "primary_root_cause_candidate": kpi.get("primary_root_cause_candidate"),
            "dns_issue_count": kpi.get("dns_issue_count", 0),
            "validation_fail_count": kpi.get("validation_fail_count", 0),
            "data_stall_count": kpi.get("data_stall_count", 0),
            "data_call_fail_or_drop_count": kpi.get("data_call_fail_or_drop_count", 0),
            "tcp_tls_timeout_count": kpi.get("tcp_tls_timeout_count", 0),
            "rf_warning_count": kpi.get("rf_warning_count", 0),
            "power_idle_hint_count": kpi.get("power_idle_hint_count", 0)
        },
        "radio_power_interpretation": (
            "일부 Internet Stall 구간은 비행기 모드 또는 Radio Power OFF와 시간적으로 연관되어"
            "사용자 동작/Radio OFF 영향 가능성을 우선 검토해야 함"
            if radio_power_related_windows
            else "Internet Stall 구간 근처에서 명확한 Radio Power OFF/비행기 모드 흔적은 확인되지 않음"
        ),
        "root_cause_summary": root_summary,
        "highest_risk_windows": window_facts
    }, ensure_ascii=False)

def get_recent_data_usage_analytics(base_name: str, hours: int = 3, result_dir: str = "./result") -> str:
    """최근 N시간 동안의 앱별 데이터 사용량을 합산하여 상위 앱을 추출합니다."""
    report_data = _load_report_json(base_name, result_dir)
    usage_stats = report_data.get("data_usage_stats", [])

    if not usage_stats:
        return json.dumps({"error": "데이터 사용량 기록이 없습니다."}, ensure_ascii=False)

    parsed_entries = []

    for stat in usage_stats:
        time_str = stat.get("time")
        if not time_str:
            continue
        try:
            if len(time_str) > 15:
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            else:
                dt = datetime.strptime(time_str, "%m-%d %H:%M:%S")
            parsed_entries.append({"dt": dt, "stat": stat})
        except ValueError:
            continue

    if not parsed_entries:
        return json.dumps({"error": "시간을 파싱할 수 있는 데이터가 없습니다."}, ensure_ascii=False)

    latest_time = max(entry["dt"] for entry in parsed_entries)
    threshold_time = latest_time - timedelta(hours=hours)

    app_usage = {}
    for entry in parsed_entries:
        if entry["dt"] >= threshold_time:
            app_name = entry["stat"].get("app_name", "Unknown")
            try:
                mb = float(entry["stat"].get("total_mb", 0.0))
            except ValueError:
                mb = 0.0

            app_usage[app_name] = app_usage.get(app_name, 0.0) + mb

    sorted_usage = sorted(app_usage.items(), key=lambda x: x[1], reverse=True)

    usage_facts = []
    for app, mb in sorted_usage:
        if mb > 0.0:
            usage_facts.append({
                "app_name": app,
                "total_mb_used": round(mb, 2)
            })

    return json.dumps({
        "analysis_window_hours": hours,
        "latest_log_time": latest_time.strftime("%Y-%m-%d %H:%M:%S"),
        "recent_top_consuming_apps": usage_facts
    }, ensure_ascii=False)

def get_radio_power_analytics(base_name: str, result_dir: str = "./result") -> str:
    """라디오(모뎀) 전원 ON/OFF 제어 이력을 추출합니다."""
    report_data = _load_report_json(base_name, result_dir)
    power_events = report_data.get("radio_power", [])

    power_facts = []
    last_state = None
    for p in power_events:
        req = p.get("raw_request", "")
        if "RADIO_POWER" in req:
            state = "ON" if " 1" in req else "OFF"
            if state != last_state:
                power_facts.append({
                    "time": p.get("time"),
                    "power_state_request": state
                })
                last_state = state

    return json.dumps({"radio_power_transitions": power_facts}, ensure_ascii=False)

def get_internet_stall_kpi_for_integrated_report(base_name: str, result_dir: str = "./result") -> dict:
    """
    get_device_health_kpi()에 나중에 붙이기 쉬운 dict 형태 요약.
    기존 agent_tools.py를 당장 수정하지 않고도 종합 리포트 확장 후보로 사용 가능.
    """
    raw = json.loads(get_internet_stall_analytics(base_name, result_dir))
    if raw.get("status") != "OK":
        return {
            "status": "NO_DATA",
            "summary": raw.get("message", "인터넷 멈춤 분석 데이터 없음")
        }

    kpi = raw.get("kpi", {})
    return {
        "status": "OK",
        "summary": {
            "stall_windows": kpi.get("stall_window_count", 0),
            "high_risk_windows": kpi.get("high_risk_window_count", 0),
            "primary_root_cause_candidate": kpi.get("primary_root_cause_candidate"),
            "dns_issues": kpi.get("dns_issue_count", 0),
            "validation_failures": kpi.get("validation_fail_count", 0),
            "data_stalls": kpi.get("data_stall_count", 0),
            "data_call_fail_or_drops": kpi.get("data_call_fail_or_drop_count", 0),
            "rf_warnings": kpi.get("rf_warning_count", 0),
            "tcp_tls_timeouts": kpi.get("tcp_tls_timeout_count", 0),
            "power_idle_hints": kpi.get("power_idle_hint_count", 0)
        },
        "root_cause_summary": raw.get("root_cause_summary", {}),
        "highest_risk_windows": raw.get("highest_risk_windows", [])
    }
