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
        ims_markers = [
            "[IPCT", "[IPCN", "ImsPhoneCallTracker", "SemImsPhoneConnection",
            "ImsPhoneConnection", "Connection: notifyDisconnect", "CallAnalytics:"
        ]
        return any(marker in line for marker in ims_markers)

    def detect_event(self, line: str) -> str | None:
        event_keywords = [
            'dial - initialCallNetworkType', 'createCallProfile', 'makeCall',
            'onIncomingCall', 'takeCall', 'accept', 'CallListener.onCallInitiating',
            'CallListener.onCallUpdated', 'reject', 'onCallTerminated',
            'onCallStartFailed', 'processCallStateChange state=DISCONNECTED',
            'notifyDisconnect', 'setTelecomCallIdToIms', 'close'
        ]
        for keyword in event_keywords:
            if keyword in line:
                return keyword
        return None

    def build_event_log(self, line: str) -> str:
        timestamp = self._extract_timestamp(line)
        payload_match = re.search(r'\s[VDIWEFS]\s+[^:]+:\s*(.*)$', line)
        payload = payload_match.group(1).strip() if payload_match else line.strip()
        return f"[{timestamp}] {payload}"

    def extract_direction(self, line: str) -> str | None:
        if "dial - initialCallNetworkType" in line or "makeCall" in line:
            return "MO"
        if "onIncomingCall" in line or "takeCall" in line or "incoming: true" in line:
            return "MT"
        return None

    def extract_obj_id(self, line: str, obj_re) -> str | None:
        ims_obj_match = re.search(r'\[ImsCall\s+objId:\s*(\d+)', line)
        if ims_obj_match:
            return ims_obj_match.group(1)

        # Connection objIds identify framework-side connection objects, not the
        # IMS call session key used by IPF callbacks.
        if "ImsPhoneConnection objId" in line or "telecomCallID:" in line:
            return None

        obj_match = obj_re.search(line)
        return obj_match.group(1) if obj_match else None

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

    def should_update_release_reason(self, existing_code: str, extracted_code: str) -> bool:
        if not extracted_code:
            return False
        is_release = "CODE_USER" in extracted_code or "USER_DECLINE" in extracted_code or "TERMINATED" in extracted_code
        return is_release and (not existing_code or is_release)

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

    def build_session(
        self, obj_id, events, tc_id, fail_reason, release_reason, direction,
        ims_bracket_re, ims_standard_re
    ):
        start_time = events[0].split("]")[0].replace("[", "") if events else "Unknown"
        end_time = events[-1].split("]")[0].replace("[", "") if events else "Unknown"
        is_user_reject = any("USER_DECLINE" in e for e in events)

        has_start_failed = any("onCallStartFailed" in e for e in events)
        has_user_release = any("CODE_USER" in e or "USER_DECLINE" in e for e in events)

        if has_start_failed and not has_user_release:
            status = "FAIL"
        elif has_user_release:
            status = "NORMAL_RELEASE"
        else:
            status = "SUCCESS"

        display_id = f"{tc_id} (objId:{obj_id})" if tc_id else f"objId:{obj_id}"
        final_reason = self.resolve_final_reason(events, status, fail_reason, ims_bracket_re, ims_standard_re)
        if status != "FAIL":
            release_reason = release_reason or final_reason
            final_reason = "0"
        else:
            release_reason = "0"

        return {
            "type": "PS(VoLTE)",
            "id": display_id,
            "direction": direction or "Unknown",
            "start_time": start_time,
            "end_time": end_time,
            "status": status,
            "is_user_reject": is_user_reject,
            "fail_reason": final_reason,
            "release_reason": release_reason or "0",
            "logs": events
        }

    def parse(self, lines):
        calls = defaultdict(list)
        obj_to_tc = {}
        obj_directions = defaultdict(str)
        pending_events = []
        pending_direction = None
        obj_fail_reasons = defaultdict(str)
        obj_release_reasons = defaultdict(str)

        obj_re = re.compile(r'objId:\s*(\d+)')
        tc_id_re = re.compile(r'(TC@[a-zA-Z0-9_]+)')
        ims_bracket_re = re.compile(r'ImsReasonInfo\s*::\s*\{\s*(\d+)\s*:\s*([A-Z_0-9]+)(?:,\s*(\d+)\s*,\s*([^,}]+))?', re.IGNORECASE)
        ims_standard_re = re.compile(r'ImsReasonInfo\s*(?:[:\s\(\=]+code\=)?[:\s\(\=]*(\d+)', re.IGNORECASE)

        for line in lines:
            if not self.is_ims_call_line(line):
                continue

            if not self.detect_event(line):
                continue

            event_str = self.build_event_log(line)
            obj_id = self.extract_obj_id(line, obj_re)
            extracted_code = self.extract_fail_reason(line, ims_bracket_re, ims_standard_re)
            direction = self.extract_direction(line)

            if not obj_id:
                pending_events.append(event_str)
                if direction:
                    pending_direction = direction
                continue

            if direction and not obj_directions[obj_id]:
                obj_directions[obj_id] = direction

            existing_code = obj_fail_reasons.get(obj_id, "")
            if self.should_update_fail_reason(existing_code, extracted_code):
                obj_fail_reasons[obj_id] = extracted_code
            if self.should_update_release_reason(obj_release_reasons.get(obj_id, ""), extracted_code):
                obj_release_reasons[obj_id] = extracted_code

            tc_match = tc_id_re.search(line)
            if tc_match:
                obj_to_tc[obj_id] = tc_match.group(1)

            if pending_events:
                for p_event in pending_events:
                    self.append_unique_event(calls[obj_id], p_event)
                pending_events = []
                if pending_direction and not obj_directions[obj_id]:
                    obj_directions[obj_id] = pending_direction
                pending_direction = None

            self.append_unique_event(calls[obj_id], event_str)

        multi_calls_list = []
        for obj_id, events in calls.items():
            multi_calls_list.append(
                self.build_session(
                    obj_id=obj_id,
                    events=events,
                    tc_id=obj_to_tc.get(obj_id),
                    fail_reason=obj_fail_reasons[obj_id],
                    release_reason=obj_release_reasons[obj_id],
                    direction=obj_directions[obj_id],
                    ims_bracket_re=ims_bracket_re,
                    ims_standard_re=ims_standard_re
                )
            )

        return multi_calls_list
