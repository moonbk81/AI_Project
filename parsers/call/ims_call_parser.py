import re
from collections import defaultdict

# ==========================================
# IMS/PS(VoLTE) Call Parser (objId-based session parsing)
# ==========================================
class ImsCallParser:
    """IMS/PS(VoLTE) Call 로그를 objId 기준 세션으로 파싱한다."""

    def __init__(self, timestamp_extractor):
        self._extract_timestamp = timestamp_extractor

    def is_ims_call_line(self, line: str) -> bool:
        return "[IPCT" in line or "[IPCN" in line

    def detect_event(self, line: str) -> str | None:
        event_keywords = [
            'onIncomingCall', 'takeCall', 'accept', 'reject',
            'onCallTerminated', 'onCallStartFailed'
        ]
        for keyword in event_keywords:
            if keyword in line:
                return keyword
        return None

    def build_event_log(self, line: str) -> str:
        timestamp = self._extract_timestamp(line)
        payload = line.split(' - ', 1)[-1].strip()
        return f"[{timestamp}] {payload}"

    def extract_fail_reason(self, line: str, ims_bracket_re, ims_standard_re) -> str:
        bracket_match = ims_bracket_re.search(line)
        if bracket_match:
            extracted_code = f"{bracket_match.group(1)}_{bracket_match.group(2)}"
            sip_code = bracket_match.group(3)
            sip_desc = bracket_match.group(4)
            if sip_code and sip_code != "0":
                extracted_code += f" (SIP_{sip_code}_{sip_desc.strip()})"
            return extracted_code

        standard_match = ims_standard_re.search(line)
        if standard_match:
            return f"IMS_REASON_{standard_match.group(1)}"

        return ""

    def should_update_fail_reason(self, existing_code: str, extracted_code: str) -> bool:
        if not extracted_code:
            return False
        if not existing_code:
            return True
        return "510" not in extracted_code and "TERMINATED" not in extracted_code

    def append_unique_event(self, events: list, event: str) -> None:
        if not events or events[-1] != event:
            events.append(event)

    def resolve_final_reason(self, events, status, fail_reason, ims_bracket_re, ims_standard_re):
        if fail_reason:
            return fail_reason
        if status != "FAIL":
            return "0"

        last_line = events[-1]
        reason_match = re.search(r'(CODE_[A-Z_]+|USER_DECLINE|\d{3}\s:\s[^,]+)', last_line)
        if reason_match:
            return reason_match.group(1).strip()

        fallback_match = ims_bracket_re.search(last_line)
        if fallback_match:
            return f"{fallback_match.group(1)}_{fallback_match.group(2)}"

        fallback_std = ims_standard_re.search(last_line)
        return f"IMS_FAIL_{fallback_std.group(1)}" if fallback_std else "IMS_CALL_START_FAILED"

    def build_session(self, obj_id, events, tc_id, fail_reason, ims_bracket_re, ims_standard_re):
        start_time = events[0].split("]")[0].replace("[", "") if events else "Unknown"
        end_time = events[-1].split("]")[0].replace("[", "") if events else "Unknown"
        is_user_reject = any("USER_DECLINE" in e for e in events)

        status = "SUCCESS"
        has_failed_event = any(any(k in e for k in ['Terminated', 'reject', 'Failed']) for e in events)
        if has_failed_event:
            status = "FAIL" if "CODE_USER" not in events[-1] and "USER_DECLINE" not in events[-1] else "NORMAL_RELEASE"

        display_id = f"{tc_id} (objId:{obj_id})" if tc_id else f"objId:{obj_id}"
        final_reason = self.resolve_final_reason(events, status, fail_reason, ims_bracket_re, ims_standard_re)

        return {
            "type": "PS(VoLTE)",
            "id": display_id,
            "start_time": start_time,
            "end_time": end_time,
            "status": status,
            "is_user_reject": is_user_reject,
            "fail_reason": final_reason,
            "logs": events
        }

    def parse(self, lines):
        calls = defaultdict(list)
        obj_to_tc = {}
        pending_events = []
        obj_fail_reasons = defaultdict(str)

        obj_re = re.compile(r'objId:(\d+)')
        tc_id_re = re.compile(r'(TC@[a-zA-Z0-9_]+)')
        ims_bracket_re = re.compile(r'ImsReasonInfo\s*::\s*\{\s*(\d+)\s*:\s*([A-Z_0-9]+)(?:,\s*(\d+)\s*,\s*([^,}]+))?', re.IGNORECASE)
        ims_standard_re = re.compile(r'ImsReasonInfo\s*(?:[:\s\(\=]+code\=)?[:\s\(\=]*(\d+)', re.IGNORECASE)

        for line in lines:
            if not self.is_ims_call_line(line):
                continue

            if not self.detect_event(line):
                continue

            event_str = self.build_event_log(line)
            obj_match = obj_re.search(line)
            extracted_code = self.extract_fail_reason(line, ims_bracket_re, ims_standard_re)

            if not obj_match:
                pending_events.append(event_str)
                continue

            obj_id = obj_match.group(1)

            existing_code = obj_fail_reasons.get(obj_id, "")
            if self.should_update_fail_reason(existing_code, extracted_code):
                obj_fail_reasons[obj_id] = extracted_code

            tc_match = tc_id_re.search(line)
            if tc_match:
                obj_to_tc[obj_id] = tc_match.group(1)

            if pending_events:
                for p_event in pending_events:
                    self.append_unique_event(calls[obj_id], p_event)
                pending_events = []

            self.append_unique_event(calls[obj_id], event_str)

        multi_calls_list = []
        for obj_id, events in calls.items():
            multi_calls_list.append(
                self.build_session(
                    obj_id=obj_id,
                    events=events,
                    tc_id=obj_to_tc.get(obj_id),
                    fail_reason=obj_fail_reasons[obj_id],
                    ims_bracket_re=ims_bracket_re,
                    ims_standard_re=ims_standard_re
                )
            )

        return multi_calls_list