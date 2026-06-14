import json
import os
from collections import Counter # 💡 요약 카운트를 위해 추가

import pandas as pd

from agent_toolkit.common import _ensure_dict

def get_device_health_kpi(base_name: str, result_dir: str = "./result") -> str:
    """
    단말의 9대 핵심 성능 지표(Health KPI)를 종합하여 반환합니다.
    LLM이 단말 상태를 진단할 때 팩트(Fact) 기반으로 요약할 수 있도록 돕는 도구입니다.
    """
    kpi_report = {
        "1_data_usage_top3": {},
        "2_call_history_status": {},
        "3_datacall_connection": {},
        "4_oos_and_signal": {},
        "5_battery_and_thermal": {},
        "6_ntn_satellite": {},
        "7_dns_network_issues": {},
        "8_ims_sip_transactions": {},
        "9_ril_sip_correlation": [],
        "10_system_crash_and_fatal_errors": {},
        "11_satellite_modem_status": {},
        "12_time_and_tniz_updates": []
    }

    # ==========================================
    # 0. 메인 통합 리포트 로드
    # ==========================================
    report_path = os.path.join(result_dir, f"{base_name}_report.json")
    if not os.path.exists(report_path):
        return json.dumps({"error": f"Report file not found for {base_name}"})

    with open(report_path, 'r', encoding='utf-8') as f:
        report_data = json.load(f)

    # ==========================================
    # 1. 📱 데이터 사용량 Top 3
    # ==========================================
    data_usage_stats = report_data.get("data_usage_stats", [])
    if data_usage_stats:
        sorted_usage = sorted(data_usage_stats, key=lambda x: x.get('total_mb', 0), reverse=True)[:3]
        kpi_report["1_data_usage_top3"] = [
            f"{u.get('app_name', 'Unknown')} ({u.get('rat', 'Unknown')}망 {u.get('total_mb', 0)}MB)"
            for u in sorted_usage
        ]

    # ==========================================
    # 2. 📞 통화 이력 및 상태
    # ==========================================
    sessions = report_data.get("call_sessions", [])
    call_drops = []

    if sessions:
        total_calls = len(sessions)
        for s in sessions:
            if isinstance(s.get('status'), str) and ('FAIL' in s['status'].upper() or 'DROP' in s['status'].upper()):
                call_drops.append({
                    "time": s.get("end_time") or s.get("start_time"),
                    "status": s.get("status"),
                    "fail_reason": s.get("fail_reason", "Unknown")
                })

        kpi_report["2_call_history_status"] = {
            "total_calls": total_calls,
            "dropped_calls_count": len(call_drops),
            "drop_details": call_drops if call_drops else "모든 통화 100% 정상 성공 (드랍 없음)"
        }

    # ==========================================
    # 3. 데이터 호(SETUP_DATA_CALL) 연결
    # ==========================================
    datacall_path = os.path.join(result_dir, f"{base_name}_datacall.json")
    if os.path.exists(datacall_path):
        with open(datacall_path, 'r', encoding='utf-8') as f:
            dc_data = json.load(f)
            setup_events = [d for d in dc_data if d.get('event_type') == 'DATA_SETUP']
            if setup_events:
                total_dc = len(setup_events)
                fail_dc = len([d for d in setup_events if d.get('status') != 'SUCCESS'])
                kpi_report["3_datacall_connection"] = {
                    "total_attempts": total_dc,
                    "failed_attempts": fail_dc,
                    "failure_rate_percent": round((fail_dc / total_dc) * 100, 1) if total_dc > 0 else 0
                }

    # ==========================================
    # 4. 망 이탈(OOS) 및 신호(Signal)
    # ==========================================
    oos_history = report_data.get("oos_events", [])
    signal_history = report_data.get("signal_level_history", [])

    avg_signal = "N/A"
    worst_rsrp = None
    worst_sinr = None

    if signal_history:
        levels = [float(s.get('level', 0)) for s in signal_history if 'level' in s]
        if levels:
            avg_signal = round(sum(levels) / len(levels), 1)

        # [LLM 팩트 주입] 전체 로그 내에서 최악의 신호 상태(RSRP/SINR)를 탐색합니다.
        for sig in signal_history:
            details = sig.get("details", {})
            for rat in ["LTE", "NR"]:
                if rat in details:
                    # RSRP 최저값 (절댓값이 클수록 안 좋으므로 min 사용)
                    rsrp_str = details[rat].get("RSRP", "Unknown")
                    if rsrp_str != "Unknown" and "dBm" in rsrp_str:
                        try:
                            val = int(rsrp_str.replace("dBm", "").strip())
                            if worst_rsrp is None or val < worst_rsrp:
                                worst_rsrp = val
                        except: pass

                    # SINR 최저값
                    sinr_str = details[rat].get("SINR", "Unknown")
                    if sinr_str != "Unknown" and "dB" in sinr_str:
                        try:
                            val = float(sinr_str.replace("dB", "").strip())
                            if worst_sinr is None or val < worst_sinr:
                                worst_sinr = val
                        except: pass

    kpi_report["4_oos_and_signal"] = {
        "oos_occurrence_count": len(oos_history),
        "average_signal_level": avg_signal,
        "worst_rsrp_detected": f"{worst_rsrp} dBm" if worst_rsrp is not None else "데이터 없음",
        "worst_sinr_detected": f"{worst_sinr} dB" if worst_sinr is not None else "데이터 없음"
    }

    # ==========================================
    # 5. 🔋 발열(Thermal) 및 배터리 점유
    # ==========================================
    battery_thermal_stats = _ensure_dict(report_data.get("battery_thermal_stats", {}))
    if isinstance(battery_thermal_stats, dict):
        thermal_stats = battery_thermal_stats.get("thermal_stats", [])
        wakelock_stats = battery_thermal_stats.get("wakelock_stats", [])

        max_temp = max([float(t.get("temperature", 0)) for t in thermal_stats]) if thermal_stats else "기록 없음"
        top_wl = sorted(wakelock_stats, key=lambda x: int(x.get("times", 0)), reverse=True)[0].get("app_name") if wakelock_stats else "없음"

        kpi_report["5_battery_and_thermal"] = {
            "max_temperature_celsius": max_temp,
            "top_wakelock_app": top_wl
        }

    # ==========================================
    # 6. 🛰️ 위성(NTN) 통신 전환 이력
    # ==========================================
    ntn_path = os.path.join(result_dir, f"{base_name}_ntn.json")
    if os.path.exists(ntn_path):
        with open(ntn_path, 'r', encoding='utf-8') as f:
            ntn_data = json.load(f)
            policy_events = [d for d in ntn_data if d.get('log_type') == 'NTN_Policy']
            kpi_report["6_ntn_satellite"] = {
                "ntn_policy_change_events": len(policy_events),
                "summary": f"위성 로밍 정책 변경 {len(policy_events)}건 확인됨" if policy_events else "위성 연결 이력 없음"
            }

    # ==========================================
    # 7. 🚫 DNS 및 네트워크 지연/차단
    # ==========================================
    net_ts = report_data.get("network_timeseries", {})
    dns_issues = net_ts.get("dns_issues", [])
    private_dns = net_ts.get("private_dns_status", {})
    dns_queries = report_data.get("dns_queries", [])

    high_latency_queries = [
        d for d in dns_queries
        if isinstance(d, dict)
        and isinstance(d.get("latency_ms"), (int, float))
        and d.get("latency_ms", 0) >= 1000
    ]

    max_dns_latency = max(
        [d.get("latency_ms", 0) for d in dns_queries if isinstance(d, dict)],
        default=0
    )

    slow_dns_apps = sorted(list(set([
        d.get("app_name")
        for d in high_latency_queries
        if d.get("app_name")
    ])))

    max_dns_avg_ms = 0
    max_dns_max_ms = 0
    max_dns_delayed_cnt = 0
    max_dns_blocked_cnt = 0
    critical_dns_timeline_spikes = []

    timeline = net_ts.get("sorted_timeline", {})
    for ts, details in timeline.items():
        for stat in details.get("net_stats", []):
            if not isinstance(stat, dict):
                continue

            dns_avg = stat.get("dns_avg", 0) or 0
            dns_max = stat.get("dns_max", 0) or 0
            dns_delayed_cnt = stat.get("dns_delayed_cnt", 0) or 0
            dns_blocked_cnt = stat.get("dns_blocked_cnt", 0) or 0

            max_dns_avg_ms = max(max_dns_avg_ms, dns_avg)
            max_dns_max_ms = max(max_dns_max_ms, dns_max)
            max_dns_delayed_cnt = max(max_dns_delayed_cnt, dns_delayed_cnt)
            max_dns_blocked_cnt = max(max_dns_blocked_cnt, dns_blocked_cnt)

            if isinstance(dns_avg, (int, float)) and dns_avg >= 1000:
                critical_dns_timeline_spikes.append({
                    "time": ts,
                    "netId": stat.get("netId"),
                    "transport": stat.get("transport"),
                    "dns_avg_ms": dns_avg,
                    "dns_max_ms": dns_max,
                    "dns_err_rate": stat.get("dns_err_rate"),
                    "dns_tot": stat.get("dns_tot"),
                    "dns_delayed_cnt": dns_delayed_cnt,
                    "dns_blocked_cnt": dns_blocked_cnt,
                })

    critical_dns_timeline_spikes = sorted(
        critical_dns_timeline_spikes,
        key=lambda x: x.get("dns_avg_ms", 0),
        reverse=True
    )[:10]

    blocked_packages = list(set([d.get("package") for d in dns_issues if d.get("package")])) if dns_issues else []

    dot_failures = []
    for net_id, info in private_dns.items():
        if info.get("fail_count", 0) > 0:
            dot_failures.append(f"NetId {net_id}: {info['mode']} 모드에서 DoT 연결 실패 {info['fail_count']}건 (실패 IP: {', '.join(info['failed_ips'])})")

    kpi_report["7_dns_network_issues"] = {
        "dns_issue_count": len(dns_issues),
        "blocked_packages": blocked_packages if blocked_packages else "없음",
        "private_dns_failures": dot_failures if dot_failures else "DoT 세션 모두 정상 (또는 OFF 상태)",
        "dns_query_count": len(dns_queries),
        "slow_dns_query_count": len(high_latency_queries),
        "max_dns_latency_ms": max_dns_latency,
        "slow_dns_apps": slow_dns_apps if slow_dns_apps else "없음",
        "max_dns_avg_ms": max_dns_avg_ms,
        "max_dns_max_ms": max_dns_max_ms,
        "max_dns_delayed_cnt": max_dns_delayed_cnt,
        "max_dns_blocked_cnt": max_dns_blocked_cnt,
        "critical_dns_timeline_spikes": critical_dns_timeline_spikes if critical_dns_timeline_spikes else "없음"
    }

    # ==========================================
    # 8. 💬 IMS SIP 트랜잭션 상태
    # ==========================================
    sip_errors = []
    sip_path = os.path.join(result_dir, f"{base_name}_ims_sip.json")
    if os.path.exists(sip_path):
        with open(sip_path, 'r', encoding='utf-8') as f:
            sip_data = json.load(f)
            if sip_data:
                sip_errors = [s for s in sip_data if s.get('is_error') == True]
                error_methods = list(set([s.get('method_code') for s in sip_errors]))
                kpi_report["8_ims_sip_transactions"] = {
                    "total_transactions": len(sip_data),
                    "error_count": len(sip_errors),
                    "detected_errors": error_methods if error_methods else "없음"
                }

    # ==========================================
    # 9. 🔗 [RIL-SIP 융합 진단] (상관관계)
    # ==========================================
    if call_drops and sip_errors:
        for call in call_drops:
            c_time_str = call.get('time')
            if not c_time_str: continue

            c_time = pd.to_datetime(c_time_str, format='%m-%d %H:%M:%S.%f', errors='coerce')

            for sip in sip_errors:
                s_time_str = sip.get('time')
                s_time = pd.to_datetime(s_time_str, format='%m-%d %H:%M:%S.%f', errors='coerce')

                if pd.notna(c_time) and pd.notna(s_time) and abs((c_time - s_time).total_seconds()) <= 2.0:
                    kpi_report["9_ril_sip_correlation"].append({
                        "message": "🔥 [핵심 상관관계 발견]: RIL 통화 드랍과 동시간대(±2초)에 SIP 에러 발생 확인",
                        "ril_drop_time": c_time_str,
                        "ril_reason": call.get("fail_reason"),
                        "sip_error_time": s_time_str,
                        "sip_error_method": sip.get("method_code")
                    })

    if not kpi_report["9_ril_sip_correlation"]:
        kpi_report["9_ril_sip_correlation"] = "RIL-SIP 간 직접적인 시간대 상관관계 특이사항 없음"

    # ==========================================
    # 10. 💥 시스템 크래시 및 Binder 경고 요약
    # ==========================================
    crash_data = report_data.get("crash_context", [])
    native_crash_data = report_data.get("native_crash_context", [])
    anr_data = report_data.get("anr_context", [])

    kpi_report["10_system_crash_and_fatal_errors"] = {}
    has_fatal_or_anr = False
    crash_summaries = []

    if crash_data:
        has_fatal_or_anr = True
        crash_summaries.extend([
            f"[{c.get('time', 'Unknown Time')}] Process: {c.get('process', 'Unknown')} | Type: {c.get('crash_type', 'FATAL')}"
            for c in crash_data
        ])

    if native_crash_data:
        has_fatal_or_anr = True
        crash_summaries.extend([
            f"[{n.get('time', 'Unknown Time')}] Process: {n.get('process', 'Unknown')} | Type: NATIVE_CRASH (Signal: {n.get('signal', 'Unknown')})"
            for n in native_crash_data
        ])

    if crash_summaries:
        kpi_report["10_system_crash_and_fatal_errors"]["total_crashes"] = len(crash_summaries)
        kpi_report["10_system_crash_and_fatal_errors"]["crash_summaries"] = crash_summaries

    if isinstance(anr_data, dict) and anr_data:
        anr_data = [anr_data]
    elif not isinstance(anr_data, list):
        anr_data = []

    if anr_data:
        has_fatal_or_anr = True
        anr_events = []
        for a in anr_data:
            if not isinstance(a, dict):
                continue

            process_info = a.get("process_info", {}) or {}
            analysis_summary = a.get("analysis_summary", {}) or {}
            anr_events.append({
                "time": a.get("time"),
                "process": a.get("process") or process_info.get("name", "Unknown"),
                "reason": a.get("reason", "Unknown ANR Reason"),
            })
        kpi_report["10_system_crash_and_fatal_errors"]["anr_events"] = anr_events

    # 💡 [핵심 수정] 무지성 배열 덤프 제거 및 타입별 개수 압축 (SYSTEM_WTF 등 방어)
    binder_warnings = report_data.get("binder_warnings", [])
    if binder_warnings:
        has_fatal_or_anr = True
        warning_types = Counter([b.get('type', 'UNKNOWN') for b in binder_warnings if isinstance(b, dict)])
        summary_strs = [f"{k}: {v}건" for k, v in warning_types.items()]

        kpi_report["10_system_crash_and_fatal_errors"]["binder_warnings_summary"] = (
            f"총 {len(binder_warnings)}건 감지 ({', '.join(summary_strs)})"
        )

    if not has_fatal_or_anr:
        kpi_report["10_system_crash_and_fatal_errors"] = "시스템 크래시/FATAL 에러 발생 이력 없음 (안정적)"

    # ==========================================
    # 11. 🛰️ 위성 모뎀(AT Command) 제어 및 에러 상태
    # ==========================================
    sat_at_path = os.path.join(result_dir, f"{base_name}_sat_at.json")
    if os.path.exists(sat_at_path):
        with open(sat_at_path, 'r', encoding='utf-8') as f:
            sat_data = json.load(f)
            sat_metrics = sat_data.get("metrics", {})
            sat_flow = sat_data.get("call_flow", [])
            reg_history = sat_data.get("registration_history", [])

            achieved_states = []
            for r in reg_history:
                state_str = r.get('status_str', 'Unknown')
                if not achieved_states or achieved_states[-1] != state_str:
                    achieved_states.append(state_str)

            power_off_detected = False
            for msg in sat_flow:
                raw_text = msg.get('raw', '')
                if 'SAT_SET_POWER, state: OFF' in raw_text or 'AT+CFUN=0' in raw_text or 'AT+CFUN=4' in raw_text:
                    power_off_detected = True
                    break

            critical_sat_errors = [
                f"[{msg['time']}] {msg['desc']} (Raw: {msg.get('raw', '')})"
                for msg in sat_flow if "❌" in msg.get('desc', '') or "ERROR" in msg.get('raw', '')
            ]

            kpi_report["11_satellite_modem_status"] = {
                "arfcn": sat_metrics.get("arfcn", "Unknown"),
                "registration_history_flow": " -> ".join(achieved_states) if achieved_states else "Unknown",
                "is_intentional_power_off": power_off_detected,
                "signal_rssi_snr": f"{sat_metrics.get('last_rssi')} / {sat_metrics.get('last_snr')}",
                "call_drops_and_fails": sat_metrics.get("calls_dropped_or_failed", 0),
                "sms_tx_fails": sat_metrics.get("sms_tx_fail", 0),
                "critical_errors_detected": critical_sat_errors if critical_sat_errors else "없음 (해당 기간 내 Call/SMS 정상 처리됨)"
            }

    # ==========================================
    # 12. 🕒 NITZ 시간 보정 로그 압축 (토큰 방어)
    # ==========================================
    nitz_data = report_data.get("nitz_history", [])
    if nitz_data:
        # 💡 [핵심 수정] 50줄짜리 배열 덤프를 처음/마지막만 남기고 중략 처리
        if len(nitz_data) > 3:
            kpi_report["12_time_and_tniz_updates"] = [
                nitz_data[0],
                f"... (중략: 총 {len(nitz_data)}건의 NITZ 보정 발생) ...",
                nitz_data[-1]
            ]
        else:
            kpi_report["12_time_and_tniz_updates"] = nitz_data

    return json.dumps(kpi_report, indent=4, ensure_ascii=False)