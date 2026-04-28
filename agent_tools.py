import os
import json
import pandas as pd

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
        "9_ril_sip_correlation": []
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
        # total_mb 기준으로 내림차순 정렬 후 Top 3 추출
        sorted_usage = sorted(data_usage_stats, key=lambda x: x.get('total_mb', 0), reverse=True)[:3]
        kpi_report["1_data_usage_top3"] = [
            f"{u.get('app_name', 'Unknown')} ({u.get('rat', 'Unknown')}망 {u.get('total_mb', 0)}MB)"
            for u in sorted_usage
        ]

    # ==========================================
    # 2. 📞 통화 이력 및 상태
    # ==========================================
    telephony_data = report_data.get("telephony", {})
    sessions = telephony_data.get("sessions", [])
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
    # 3. 🌐 데이터 호(SETUP_DATA_CALL) 연결
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
    # 4. 🚨 망 이탈(OOS) 및 신호(Signal)
    # ==========================================
    oos_history = telephony_data.get("network_history", [])
    signal_history = report_data.get("signal_level_history", [])

    avg_signal = "N/A"
    if signal_history:
        levels = [float(s.get('level', 0)) for s in signal_history if 'level' in s]
        if levels:
            avg_signal = round(sum(levels) / len(levels), 1)

    kpi_report["4_oos_and_signal"] = {
        "oos_occurrence_count": len(oos_history),
        "average_signal_level": avg_signal
    }

    # ==========================================
    # 5. 🔋 발열(Thermal) 및 배터리 점유
    # ==========================================
    battery_stats = report_data.get("battery_stats", {})
    if isinstance(battery_stats, dict):
        thermal_stats = battery_stats.get("thermal_stats", [])
        wakelock_stats = battery_stats.get("wakelock_stats", [])

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

    if dns_issues:
        blocked_packages = list(set([d.get("package") for d in dns_issues if d.get("package")]))
        kpi_report["7_dns_network_issues"] = {
            "dns_issue_count": len(dns_issues),
            "blocked_packages": blocked_packages
        }
    else:
        kpi_report["7_dns_network_issues"] = "DNS 차단 이력 없음 (정상)"

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

            # Timestamp 파싱 (연도는 임의 처리, 시간차 비교용)
            c_time = pd.to_datetime(c_time_str, format='%m-%d %H:%M:%S.%f', errors='coerce')

            for sip in sip_errors:
                s_time_str = sip.get('time')
                s_time = pd.to_datetime(s_time_str, format='%m-%d %H:%M:%S.%f', errors='coerce')

                # 2.0초 이내 상관관계 판정
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

    return json.dumps(kpi_report, indent=4, ensure_ascii=False)