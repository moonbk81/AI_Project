import os
import json
import pandas as pd
from datetime import datetime, timedelta

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
        "10_system_crash_and_fatal_errors": {}
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
    private_dns = net_ts.get("private_dns_status", {})

    blocked_packages = list(set([d.get("package") for d in dns_issues if d.get("package")])) if dns_issues else []

    dot_failures = []
    for net_id, info in private_dns.items():
        if info.get("fail_count", 0) > 0:
            dot_failures.append(f"NetId {net_id}: {info['mode']} 모드에서 DoT 연결 실패 {info['fail_count']}건 (실패 IP: {', '.join(info['failed_ips'])})")

    kpi_report["7_dns_network_issues"] = {
        "dns_issue_count": len(dns_issues),
        "blocked_packages": blocked_packages if blocked_packages else "없음",
        "private_dns_failures": dot_failures if dot_failures else "DoT 세션 모두 정상 (또는 OFF 상태)"
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
    # 10. 💥 시스템 크래시 (FATAL EXCEPTION / ANR / Tombstone)
    # ==========================================
    crash_data = report_data.get("crash_context", [])
    if crash_data:
        kpi_report["10_system_crash_and_fatal_errors"] = {
            "total_crashes": len(crash_data),
            "crash_summaries": [
                f"[{c.get('timestamp', 'Unknown Time')}] Process: {c.get('process', 'Unknown')} | Type: {c.get('crash_type', 'FATAL')}"
                for c in crash_data
            ]
        }
    else:
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

    return json.dumps(kpi_report, indent=4, ensure_ascii=False)

def _load_report_json(base_name: str, result_dir: str = "./result") -> dict:
    """분석된 통합 리포트 파일을 안전하게 로드합니다."""
    report_path = os.path.join(result_dir, f"{base_name}_report.json")
    if not os.path.exists(report_path):
        return {}
    with open(report_path, 'r', encoding='utf-8') as f:
        return json.load(f)

# ==========================================
# 🛠️ [신규 추가] 메모리 기반 타임라인 교차 검증 헬퍼
# ==========================================
def _check_rf_correlation(target_time_str: str, report_data: dict, window_sec: int = 2) -> list:
    """에러 발생 시간 기준 ±window_sec 내의 망 이탈(OOS) 및 신호 급감(Level 0~1) 이력을 탐색합니다."""
    if not target_time_str:
        return []

    current_year = datetime.now().year
    try:
        clean_time = target_time_str[:14]
        target_dt = datetime.strptime(f"{current_year}-{clean_time}", "%Y-%m-%d %H:%M:%S")
    except:
        return ["시간 파싱 불가"]

    correlated = []

    # 1. OOS 타임라인 교차 검증
    oos_events = report_data.get("telephony", {}).get("network_history", [])
    for oos in oos_events:
        oos_time = str(oos.get("time", ""))[:14]
        if oos_time:
            try:
                oos_dt = datetime.strptime(f"{current_year}-{oos_time}", "%Y-%m-%d %H:%M:%S")
                diff = abs((oos_dt - target_dt).total_seconds())
                if diff <= window_sec:
                    v_reg = str(oos.get("voice_reg", ""))
                    d_reg = str(oos.get("data_reg", ""))
                    if "1" in v_reg or "1" in d_reg or "OUT_OF_SERVICE" in v_reg or "OUT_OF_SERVICE" in d_reg:
                        correlated.append(f"[OOS 동반] 망 이탈 발생 (시간차: {diff}초)")
            except: pass

    # 2. Signal 타임라인 교차 검증
    signal_events = report_data.get("signal_level_history", [])
    for sig in signal_events:
        sig_time = str(sig.get("time", ""))[:14]
        sig_level = str(sig.get("level", sig.get("max_level", "")))
        if sig_time and sig_level in ["0", "1"]:
            try:
                sig_dt = datetime.strptime(f"{current_year}-{sig_time}", "%Y-%m-%d %H:%M:%S")
                diff = abs((sig_dt - target_dt).total_seconds())
                if diff <= window_sec:
                    correlated.append(f"[약전계 진입] Level {sig_level} (시간차: {diff}초)")
            except: pass

    return correlated if correlated else ["명시적인 무선 환경(RF) 악화 동반 안됨"]

def get_cs_call_analytics(base_name: str, result_dir: str = "./result") -> str:
    """CS 통화의 릴리즈 코드를 파싱하고 장애 시 무선 환경(RF)을 교차 검증합니다."""
    report_data = _load_report_json(base_name, result_dir)
    sessions = report_data.get("telephony", {}).get("sessions", [])

    cs_sessions = [s for s in sessions if s.get("type") == "CS"]

    analysis = []
    for s in cs_sessions:
        reason_str = str(s.get("fail_reason", ""))
        reason_code = reason_str.split('(')[0].strip()
        is_normal = reason_code in ['16', '31']
        status = "NORMAL_RELEASE" if is_normal else "CALL_DROP"

        # 에러 시에만 2초 내의 RF(OOS/약전계) 환경 자동 조회
        target_time = s.get("end_time") or s.get("start_time")
        rf_context = _check_rf_correlation(target_time, report_data) if not is_normal else []

        analysis.append({
            "time": s.get("start_time"),
            "status": status,
            "raw_reason": reason_str,
            "slot": s.get("slot"),
            "rf_correlation": rf_context
        })

    return json.dumps({"cs_call_facts": analysis}, ensure_ascii=False)

def get_ps_ims_call_analytics(base_name: str, result_dir: str = "./result") -> str:
    """PS Call 세션과 SIP 에러를 통합 추출하고, 무선 환경(RF)과 교차 검증합니다."""
    report_data = _load_report_json(base_name, result_dir)

    sessions = report_data.get("telephony", {}).get("sessions", [])
    ps_sessions = [s for s in sessions if "PS" in s.get("type", "")]

    ps_analysis = []
    for s in ps_sessions:
        status = s.get("status", "")
        target_time = s.get("end_time") if "DROP" in status else s.get("start_time")
        # 에러(FAIL/DROP)일 경우에만 RF 교차 검증
        rf_context = _check_rf_correlation(target_time, report_data) if "FAIL" in status or "DROP" in status else []

        ps_analysis.append({
            "time": s.get("start_time"),
            "status": status,
            "fail_reason": s.get("fail_reason", ""),
            "rf_correlation": rf_context
        })

    sip_data = report_data.get("ims_sip_data", [])
    sip_errors = [m for m in sip_data if m.get("is_error")]

    sip_analysis = []
    for e in sip_errors[:5]:
        err_time = e.get("time")
        sip_analysis.append({
            "time": err_time,
            "method": e.get("method_code"),
            "rf_correlation": _check_rf_correlation(err_time, report_data) # SIP 에러 발생 시점 환경 체크
        })

    return json.dumps({
        "ril_ps_call_facts": ps_analysis,
        "sip_error_facts": sip_analysis
    }, ensure_ascii=False)

def get_network_oos_analytics(base_name: str, result_dir: str = "./result") -> str:
    """망 이탈(OOS) 시점과 후보 원인(Root Cause Candidate)을 추출합니다."""
    report_data = _load_report_json(base_name, result_dir)
    oos_history = report_data.get("telephony", {}).get("network_history", [])

    oos_facts = []
    for oos in oos_history:
        oos_facts.append({
            "time": oos.get("time"),
            "event": oos.get("event_type"),
            "slot": oos.get("slotId"),
            "reject_cause": oos.get("rej_cause"),
            "inferred_reason": oos.get("root_cause_candidate")
        })

    return json.dumps({
        "oos_count": len(oos_facts),
        "oos_events": oos_facts
    }, ensure_ascii=False)

def get_dns_latency_analytics(base_name: str, result_dir: str = "./result") -> str:
    """중증(Critical) DNS 지연 현상과 앱 차단 이력을 추출합니다."""
    report_data = _load_report_json(base_name, result_dir)
    net_ts = report_data.get("network_timeseries", {})

    timeline = net_ts.get("sorted_timeline", {})
    critical_latencies = []

    for ts, details in timeline.items():
        for stat in details.get("net_stats", []):
            dns_avg = stat.get("dns_avg", 0)
            if isinstance(dns_avg, (int, float)) and dns_avg > 2000:
                critical_latencies.append({
                    "time": ts,
                    "netId": stat.get("netId"),
                    "dns_avg_ms": dns_avg
                })

    return json.dumps({
        "dns_blocked_apps_count": len(net_ts.get("dns_issues", [])),
        "critical_dns_latency_spikes": critical_latencies
    }, ensure_ascii=False)

def get_battery_thermal_analytics(base_name: str, result_dir: str = "./result") -> str:
    """배터리 광탈 주범(Wakelock)과 기기 발열(Thermal) 최고 온도를 추출합니다."""
    report_data = _load_report_json(base_name, result_dir)
    battery_stats = report_data.get("battery_stats", {})

    if not isinstance(battery_stats, dict):
        return json.dumps({"battery_facts": "데이터 없음"}, ensure_ascii=False)

    thermal_stats = battery_stats.get("thermal_stats", [])
    wakelock_stats = battery_stats.get("wakelock_stats", [])

    max_temp = 0
    if thermal_stats:
        max_temp = max([float(t.get("temperature", 0)) for t in thermal_stats])

    top_wakelocks = []
    if wakelock_stats:
        sorted_wl = sorted(wakelock_stats, key=lambda x: int(x.get("times", 0)), reverse=True)[:3]
        top_wakelocks = [{"app": wl.get("app_name"), "times": wl.get("times")} for wl in sorted_wl]

    return json.dumps({
        "max_temperature_celsius": max_temp,
        "top_wakelocks": top_wakelocks
    }, ensure_ascii=False)

def get_crash_anr_analytics(base_name: str, result_dir: str = "./result") -> str:
    """시스템 크래시(FATAL) 및 응답없음(ANR) 발생 이력을 추출합니다."""
    report_data = _load_report_json(base_name, result_dir)
    crashes = report_data.get("crash_context", [])
    anr = report_data.get("anr_context", {})

    crash_facts = []
    for c in crashes:
        crash_facts.append({
            "time": c.get("timestamp"),
            "process": c.get("process"),
            "type": c.get("crash_type")
        })

    anr_facts = []
    if anr and isinstance(anr, dict) and anr.get("time"):
        anr_facts.append({
            "time": anr.get("time"),
            "process": anr.get("process", "Unknown"),
            "reason": anr.get("reason", "")
        })

    return json.dumps({
        "crash_count": len(crashes),
        "crash_history": crash_facts,
        "anr_count": 1 if anr_facts else 0,
        "anr_history": anr_facts
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