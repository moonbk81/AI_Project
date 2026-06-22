import re

from core.constants import TEL_PATTERNS
from core.telephony_constants import CALL_FAIL_REASON_MAP

class CsCallStateMachine:
    """CS Call 로그를 통화 세션 상태로 누적/완료 처리한다."""

    def is_ps_call_payload(self, payload: str) -> bool:
        """IMS/PS(VoLTE) call 로그인지 판단한다."""
        return "[IPCT" in payload or "[IPCN" in payload

    def _extract_call_id(self, payload: str, tc_id_re):
        """payload에서 TC ID 또는 connection ID를 추출한다."""
        tc_match = tc_id_re.search(payload)
        if tc_match:
            return tc_match.group(1)

        id_m = TEL_PATTERNS['CONN_ID'].search(payload)
        if id_m:
            return f"conn_id:{id_m.group(1)}"

        return None

    def _create_new_call(self, ts, payload, active_list, slot_id, current_id):
        """CS 시작 이벤트를 신규 active call 세션으로 생성한다."""
        return {
            "type": "CS",
            "slot": slot_id,
            "start_time": ts,
            "end_time": None,
            "id": current_id if current_id else f"Unknown_{len(active_list)}_{ts[-6:].replace('.','')}",
            "status": "DIALING",
            "is_user_reject": False,
            "fail_reason": "0",
            "logs": [f"[{ts}] START: {payload}"]
        }

    def _find_target_call(self, current_id, active_list):
        """현재 payload가 어느 active call에 속하는지 찾는다."""
        if current_id:
            for call in active_list:
                if call["id"] == current_id:
                    return call

            for call in active_list:
                if call["id"].startswith("Unknown"):
                    call["id"] = current_id
                    return call

            return None

        if active_list:
            return active_list[-1]

        return None

    def _resolve_recent_completed_call(self, target_call, payload, completed_list):
        """종료 직후 들어온 LAST_CALL_FAIL_CAUSE가 어느 세션에 붙을지 보정한다."""
        if target_call:
            return target_call, False
        if "LAST_CALL_FAIL_CAUSE" in payload and completed_list:
            return completed_list[-1], True
        return None, False

    def _append_call_log(self, target_call, ts, payload, slot_id):
        """세션에 로그와 slot/status 기본 정보를 반영한다."""
        if target_call["slot"] == "Unknown" and slot_id != "Unknown":
            target_call["slot"] = slot_id

        target_call["logs"].append(f"[{ts}] {payload}")
        if ",ACTIVE," in payload:
            target_call["status"] = "SUCCESS"

    def _apply_last_call_fail_cause(self, target_call, payload):
        """LAST_CALL_FAIL_CAUSE payload에서 cause/vendor cause를 반영한다."""
        if "LAST_CALL_FAIL_CAUSE" not in payload or "causeCode" not in payload:
            return

        c_match = re.search(r'causeCode:\s*(\d+)', payload)
        v_match = re.search(r'vendorCause:\s*(\d+)', payload)
        if not c_match:
            return

        cause_str = f"callFailCause: {c_match.group(1)}"
        if v_match:
            cause_str += f", vendorCause: {v_match.group(1)}"
        target_call["fail_reason"] = cause_str
        target_call["status"] = "CALL DROP"

    def _apply_cs_reason(self, target_call, payload):
        """CS_REASON payload에서 성공/실패/취소 상태와 fail reason을 반영한다."""
        cs_m = TEL_PATTERNS['CS_REASON'].search(payload)
        if not cs_m:
            return

        reason_code = cs_m.group(1)
        is_drop = reason_code in ['34', '41', '42', '44', '49', '58', '65535']
        if not is_drop and target_call["status"] == "DIALING" and reason_code == "16":
            target_call["status"] = "CANCELED"
        else:
            target_call["status"] = "CALL DROP" if is_drop else "SUCCESS"

        target_call["fail_reason"] = CALL_FAIL_REASON_MAP.get(reason_code, f"Code:{reason_code}")

    def _is_end_event(self, payload: str) -> bool:
        """CS call 종료 이벤트 여부를 판단한다."""
        return bool(
            TEL_PATTERNS['END_EV'].search(payload)
            or re.search(r'\<\s*(?:GET_CURRENT_CALLS\s*)?\{\}', payload, re.I)
        )

    def _finalize_if_ended(self, target_call, ts, payload, completed_list, active_list):
        """종료 이벤트인 경우 active call을 완료 목록으로 이동한다."""
        if not self._is_end_event(payload):
            return

        target_call["end_time"] = ts
        if target_call["status"] == "DIALING":
            reason = target_call.get("fail_reason", "0")
            if any(code in reason for code in ["16(", "Normal"]):
                target_call["status"] = "CANCELED"
            elif reason != "0":
                target_call["status"] = "FAIL"
            else:
                target_call["status"] = "CALL DROP"

        completed_list.append(target_call)
        active_list.remove(target_call)

    def process(self, ts, payload, completed_list, active_list, slot_id, tc_id_re):
        """CS Call 상태 머신 처리."""
        # IMS/PS(VoLTE) 관련 로그는 CS 로직에서 처리하지 않는다.
        if self.is_ps_call_payload(payload):
            return

        current_id = self._extract_call_id(payload, tc_id_re)

        # 1. 새로운 CS Call 발생 시 active_list에 추가
        if TEL_PATTERNS['CS_START'].search(payload):
            active_list.append(
                self._create_new_call(ts, payload, active_list, slot_id, current_id)
            )
            return

        # 2. 이 로그가 어느 통화의 것인지(Target Call) 식별
        target_call = self._find_target_call(current_id, active_list)
        target_call, is_just_completed = self._resolve_recent_completed_call(
            target_call, payload, completed_list
        )

        if not target_call:
            return

        self._append_call_log(target_call, ts, payload, slot_id)
        self._apply_last_call_fail_cause(target_call, payload)

        # 이미 완료 처리된 콜에 로그만 덧붙인 경우, 아래의 종료 로직을 중복으로 타지 않고 반환
        if is_just_completed:
            return

        self._apply_cs_reason(target_call, payload)
        self._finalize_if_ended(target_call, ts, payload, completed_list, active_list)