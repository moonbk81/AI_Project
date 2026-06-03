

import json

from agent_toolkit.common import _load_report_json
from agent_toolkit.correlation import _check_rf_correlation


def get_cs_call_analytics(base_name: str, result_dir: str = "./result") -> str:
    """CS 통화의 릴리즈 코드를 파싱하고 장애 시 무선 환경(RF)을 교차 검증합니다."""
    report_data = _load_report_json(base_name, result_dir)
    sessions = report_data.get("call_sessions", [])

    cs_sessions = [s for s in sessions if s.get("type") == "CS"]

    if not cs_sessions:
        return json.dumps({"status": "NO_DATA", "message": "cs 통화 이력이 없습니다."}, ensure_ascii=False)

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

    sessions = report_data.get("call_sessions", [])
    ps_sessions = [s for s in sessions if "PS" in s.get("type", "")]

    if not ps_sessions:
        return json.dumps({"status": "NO_DATA", "message": "PS(VoLTE) 통화 이력이 없습니다."}, ensure_ascii=False)

    ps_analysis = []
    for s in ps_sessions:
        status = s.get("status", "")
        target_time = s.get("end_time") if "DROP" in status else s.get("start_time")
        # 에러(FAIL/DROP)일 경우에만 RF 교차 검증
        rf_context = _check_rf_correlation(target_time, report_data) if "FAIL" in status or "DROP" in status else []

        ps_analysis.append({
            "call_id": s.get("id", ""),
            "time": s.get("start_time"),
            "status": status,
            "fail_reason": s.get("fail_reason", ""),
            "lifecycle_events": s.get("logs", []),
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
