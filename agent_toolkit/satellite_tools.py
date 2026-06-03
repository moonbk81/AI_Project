
import json
import os

def get_ntn_spacex_analytics(base_name: str, result_dir: str = "./result") -> str:
    """SpaceX(Direct-to-Cell) 위성 로밍 이력 및 신호 상태만 추출합니다. (Call 미지원)"""
    ntn_path = os.path.join(result_dir, f"{base_name}_ntn.json")
    if not os.path.exists(ntn_path):
        return json.dumps({"status": "NO_DATA", "message": "SpaceX/NTN 연결 이력이 없습니다."}, ensure_ascii=False)

    with open(ntn_path, 'r', encoding='utf-8') as f:
        ntn_data = json.load(f)

    policy_events = [d for d in ntn_data if d.get('log_type') == 'NTN_Policy']

    return json.dumps({
        "spacex_ntn_facts": {
            "ntn_policy_change_events_count": len(policy_events),
            "raw_policy_events": policy_events[:10]
        }
    }, ensure_ascii=False)


def get_tiantong_satellite_analytics(base_name: str, result_dir: str = "./result") -> str:
    """Tiantong 위성 모뎀 제어 이력(AT Command) 및 Call/SMS 상태를 추출합니다."""
    sat_at_path = os.path.join(result_dir, f"{base_name}_sat_at.json")
    if not os.path.exists(sat_at_path):
        return json.dumps({"status": "NO_DATA", "message": "Tiantong 위성(AT Command) 이력이 없습니다."}, ensure_ascii=False)

    with open(sat_at_path, 'r', encoding='utf-8') as f:
        sat_data = json.load(f)

    sat_metrics = sat_data.get("metrics", {})
    sat_flow = sat_data.get("call_flow", [])

    critical_errors = [
        f"[{msg['time']}] {msg['desc']} (Raw: {msg.get('raw', '')})"
        for msg in sat_flow if "❌" in msg.get('desc', '') or "ERROR" in msg.get('raw', '')
    ]

    return json.dumps({
        "tiantong_satellite_facts": {
            "arfcn": sat_metrics.get("arfcn", "Unknown"),
            "signal_rssi_snr": f"{sat_metrics.get('last_rssi')} / {sat_metrics.get('last_snr')}",
            "call_drops_and_fails": sat_metrics.get("calls_dropped_or_failed", 0),
            "sms_tx_fails": sat_metrics.get("sms_tx_fail", 0),
            "critical_errors_detected": critical_errors if critical_errors else "없음 (Call/SMS 정상 처리됨)"
        }
    }, ensure_ascii=False)

