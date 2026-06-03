
from agent_toolkit.common import _parse_android_time

def _check_rf_correlation(target_time_str: str, report_data: dict, window_sec: int = 2) -> list:
    """에러 발생 시간 기준 ±window_sec 내의 망 이탈(OOS) 및 신호 급감(Level 0~1) 이력을 탐색합니다."""
    target_dt = _parse_android_time(target_time_str)
    if target_dt is None:
        return ["시간 파싱 불가"]

    correlated = []

    # 1. OOS 타임라인 교차 검증
    oos_events = report_data.get("oos_events", [])
    for oos in oos_events:
        v_reg = str(oos.get("voice_reg", ""))
        d_reg = str(oos.get("data_reg", ""))

        is_oos = (
            v_reg.strip() == "1"
            or d_reg.strip() == "1"
            or "OUT_OF_SERVICE" in v_reg
            or "OUT_OF_SERVICE" in d_reg
        )
        if not is_oos:
            continue

        oos_time_str = oos.get("time", "")
        if oos_time_str:
            oos_dt = _parse_android_time(oos_time_str)
            if oos_dt is None:
                continue

            diff = abs((oos_dt - target_dt).total_seconds())
            if diff <= window_sec:
                correlated.append(f"[OOS 동반] 망 이탈 발생 (시간차: {diff}초)")

    # 2. Signal 타임라인 교차 검증 (다이어트: 레벨 0, 1 먼저 필터링)
    signal_events = report_data.get("signal_level_history", [])
    for sig in signal_events:
        sig_level = str(sig.get("level", sig.get("max_level", ""))).strip()

        # 레벨 0, 1이 아니면 시간 파싱 자체를 스킵
        if sig_level not in ["0", "1"]:
            continue

        sig_dt = _parse_android_time(sig.get("time", ""))
        if sig_dt is None:
            continue

        diff = abs((sig_dt - target_dt).total_seconds())
        if diff <= window_sec:
            details = sig.get("details", {})
            extra_info = ""
            for rat in ["LTE", "NR"]:
                if rat in details and details[rat].get("RSRP") != "Unknown":
                    rsrp = details[rat].get("RSRP", "Unknown")
                    sinr = details[rat].get("SINR", "Unknown")
                    extra_info = f" (상세 측정값 -> RSRP: {rsrp}, SINR: {sinr})"
                    break

            correlated.append(f"[약전계 진입] 안테나 Level {sig_level}{extra_info} (시간차: {diff}초)")

    return correlated if correlated else ["명시적인 무선 환경(RF) 악화 동반 안됨"]

def _check_radio_power_correlation(target_time_str: str, report_data: dict, window_sec: int = 10) -> dict:
    target_dt = _parse_android_time(target_time_str)
    if target_dt is None:
        return {
            "is_user_radio_power_related": False,
            "reason": "시간 파싱 불가",
            "nearby_radio_power_events": []
        }

    radio_events = report_data.get("radio_power", [])
    nearby = []

    for ev in radio_events:
        ev_dt = _parse_android_time(ev.get("time", ""))
        if ev_dt is None:
            continue

        diff = (ev_dt - target_dt).total_seconds()
        if abs(diff) <= window_sec:
            raw = str(ev.get("raw_request", ""))
            reason = str(ev.get("reason", ev.get("power_reason", "")))
            state = str(ev.get("state", ev.get("power_state", "")))

            if not state:
                if "RADIO_POWER" in raw:
                    state = "ON" if " 1" in raw else "OFF"

            user_trigger_keywords = [
                "airplane",
                "AIRPLANE_MODE",
                "airplane_mode_on",
                "USER",
                "setRadioPowerForReason"
            ]

            is_user_trigger = any(k in raw for k in user_trigger_keywords) or any(k in reason for k in user_trigger_keywords)

            nearby.append({
                "time": ev.get("time"),
                "time_diff_sec": diff,
                "state": state,
                "reason": reason,
                "raw_request": raw,
                "is_user_trigger_candidate": is_user_trigger
            })

    off_events = [
        e for e in nearby
        if e.get("state") == "OFF"
        or "OFF" in str(e.get("raw_request", ""))
        or " 0" in str(e.get("raw_request", ""))
    ]

    airplane_events = [
        e for e in nearby
        if "airplane" in str(e.get("raw_request", "")).lower()
        or "airplane" in str(e.get("reason", "")).lower()
        or "AIRPLANE_MODE" in str(e.get("raw_request", ""))
    ]

    if airplane_events:
        classification = "USER_AIRPLANE_MODE_RELATED"
        message = "OOS 시점 근처에 비행기 모드/사용자 Radio OFF 흔적이 있어, 망 품질 문제보다 사용자 동작 가능성이 높음"
    elif off_events:
        classification = "RADIO_POWER_OFF_RELATED"
        message = "OOS 시점 근처에 Radio Power OFF가 있어, 망 이탈 원인 판단 시 Radio OFF를 우선 고려해야 함"
    else:
        classification = "NO_RADIO_POWER_CORRELATION"
        message = "OOS 시점 근처에 Radio Power OFF/비행기 모드 흔적 없음"

    return {
        "is_user_radio_power_related": classification in [
            "USER_AIRPLANE_MODE_RELATED",
            "RADIO_POWER_OFF_RELATED"
        ],
        "classification": classification,
        "reason": message,
        "nearby_radio_power_events": nearby
    }

def _check_native_crash_correlation(target_time_str: str, report_data: dict, window_sec: int = 10) -> dict:
    """OOS 발생 시점 기준 직전 window_sec 내에 rild 데몬의 크래시가 있었는지 검증합니다."""
    target_dt = _parse_android_time(target_time_str)
    if target_dt is None:
        return {"is_rild_crash_related": False, "message": "시간 파싱 불가"}

    native_crashes = report_data.get("native_crash_context", [])

    for crash in native_crashes:
        crash_time_str = crash.get("time", "")
        if not crash_time_str:
            continue

        crash_dt = _parse_android_time(crash_time_str)
        if crash_dt is None:
            continue

        diff = (target_dt - crash_dt).total_seconds()

        if 0 <= diff <= window_sec and crash.get("process") == "rild":
            return {
                "is_rild_crash_related": True,
                "crash_time": crash_time_str,
                "process": crash.get("process"),
                "signal": crash.get("signal"),
                "abort_message": crash.get("abort_message"),
                "message": f"💥 [RILD 크래시 연계] OOS 발생 {diff}초 전 rild 데몬 Native Crash 감지 (데몬 리셋으로 인한 OOS)"
            }

    return {"is_rild_crash_related": False, "message": "주변 시간대 rild Native Crash 흔적 없음"}
