import re
from collections import deque
from core.constants import RE_TIME, RE_TAG, TEL_PATTERNS, SST_FIELDS, VALID_TAGS, COMMON_EXCLUDES, TAG_SPECIFIC_EXCLUDES, PS_EXCLUDE_TAGS, RE_HEX_DATA, NETWORK_EXCLUDE_TAGS
from core.telephony_constants import CALL_FAIL_REASON_MAP, VENDER_FAIL_REASON_MAP
from parsers.base import BaseParser

class TelephonyParser(BaseParser):
    def __init__(self, context_getter):
        super().__init__(context_getter)
        self.last_slot_states = {"0": {"v": None, "d": None}, "1": {"v": None, "d": None}}
        self.pre_context = deque(maxlen=50)

    def _parse_sst_val(self, content, key):
        match = SST_FIELDS[key].search(content)
        if match:
            val = match.group(1).strip().rstrip(')')
            if "(" in val and ")" not in val: val += ")"
            return val
        return "Unknown"

    def analyze(self, lines):
        all_sessions, oos_history = [], []
        current_session = None
        target_phone_id = None
        in_radio = False

        for line in lines:
            clean_line = line.strip()
            ts_m = RE_TIME.search(clean_line)
            ts = ts_m.group(0) if ts_m else "00-00 00:00:00.000"

            if "logcat -b radio" in line: in_radio = True; continue
            if in_radio and "was the duration of 'RADIO LOG'" in line: in_radio = False; continue

            if in_radio:
                tag_m = RE_TAG.search(line)
                tag = tag_m.group(1).strip() if tag_m else None
                if not tag or tag not in VALID_TAGS: continue

                # 1. OOS 분석 로직
                if TEL_PATTERNS['SST_POLL'].search(clean_line) and "newSS={" in clean_line:
                    ss_data = clean_line.split("newSS={")[1].rsplit("}", 1)[0]
                    v_reg, d_reg = self._parse_sst_val(ss_data, 'v_reg'), self._parse_sst_val(ss_data, 'd_reg')

                    slot_id = "1" if ('RILD2' in tag or 'SST-1' in tag or 'PHONE1' in clean_line) else "0"
                    prev = self.last_slot_states[slot_id]
                    if (v_reg[0] != prev["v"] or d_reg[0] != prev["d"]):
                        recent_logs = [l for l in list(self.pre_context) if not (any(t in l for t in NETWORK_EXCLUDE_TAGS))]
                        context_summary = " ".join(recent_logs[-20:]).lower()

                        prev_in_service = (prev["v"] == "0" and prev["d"] == "0")
                        now_in_service = (v_reg[0] == "0" and d_reg[0] == "0")

                        if not prev_in_service and now_in_service: event_type = "OOS_RECOVER"
                        elif prev_in_service and not now_in_service: event_type = "OOS_ENTER"
                        else: event_type = "OOS_STATE_CHANGE"

                        reason = "Unknown"
                        rej = self._parse_sst_val(ss_data, 'rej_cause')
                        if rej != "0" and rej != "Unknown": reason = f"NW_REJECT_CAUSE_{rej}"
                        elif "rrc connection release" in context_summary: reason = "RRC_RELEASE_BY_NW"
                        elif "out_of_service" in context_summary or "no_service" in context_summary: reason = "SIGNAL_LOSS_OR_SHADOW_AREA"
                        if v_reg[0] == "0" or d_reg[0] =="0": reason = "None"

                        oos_event = {
                            "time": ts,
                            "slotId": slot_id,
                            "event_type": event_type,
                            "voice_reg": v_reg,
                            "data_reg": d_reg,
                            "rat": self._parse_sst_val(ss_data, 'rat'),
                            "root_cause_candidate": reason,
                            "operator": f"{self._parse_sst_val(ss_data, 'op_long')} ({self._parse_sst_val(ss_data, 'op_short')})",
                            "rej_cause": self._parse_sst_val(ss_data, 'rej_cause'),
                            "emergency": self._parse_sst_val(ss_data, 'is_emergency'),
                            "context_snapshot": recent_logs[-15:]
                        }

                        if event_type == "OOS_ENTER":
                            oos_event["cross_context_logs"] = self.get_context_fn(lines, ts)

                        oos_history.append(oos_event)
                        self.last_slot_states[slot_id] = {"v": v_reg[0], "d": d_reg[0]}

                # 2. Call 세션 시작 매칭
                is_cs = TEL_PATTERNS['CS_START'].search(clean_line)
                is_ps = TEL_PATTERNS['PS_START'].search(clean_line)
                if is_cs or is_ps:
                    if current_session: all_sessions.append(current_session)
                    p_match = re.search(r'PHONE(\d)', clean_line, re.I)
                    target_phone_id = p_match.group(0).upper() if p_match else "PHONE0"
                    c_type = "CS" if is_cs else "PS(VoLTE)"
                    logs_to_add = [l for l in list(self.pre_context) if not (c_type == "PS(VoLTE)" and any(t in l for t in PS_EXCLUDE_TAGS))]
                    current_session = {
                        "type": c_type, "slot": target_phone_id, "start_time": ts, "end_time": None,
                        "id": "PENDING", "status": "Unknown", "is_user_reject": False, "fail_reason": "0",
                        "logs": logs_to_add + [f"==> [START_{target_phone_id}]: {clean_line}"]
                    }
                    continue

                # 3. Call 세션 진행 중 추적
                if current_session:
                    is_low_level_in_ps = (current_session["type"] == "PS(VoLTE)" and tag in PS_EXCLUDE_TAGS)
                    if is_low_level_in_ps: continue
                    if current_session["slot"] == "PHONE0" and (tag in ['RILD2', 'SST-1'] or (tag == 'RILJ' and 'PHONE1' in clean_line)): continue
                    if current_session["slot"] == "PHONE1" and (tag in ['RILD', 'SST-0'] or (tag == 'RILJ' and 'PHONE0' in clean_line)): continue
                    if any(kw in clean_line for kw in COMMON_EXCLUDES): continue
                    if RE_HEX_DATA.search(clean_line): continue
                    if any(kw.lower() in clean_line.lower() for kw in TAG_SPECIFIC_EXCLUDES["RILD"]) and tag in ['RILD', 'RILD2']: continue
                    if any(kw.lower() in clean_line.lower() for kw in TAG_SPECIFIC_EXCLUDES["RILJ"]) and tag == 'RILJ': continue

                    current_session["logs"].append(clean_line)

                    if id_m := TEL_PATTERNS['CONN_ID'].search(clean_line): current_session["id"] = id_m.group(1)
                    if reject_m := TEL_PATTERNS['REJECT_EV'].search(clean_line):
                        current_session["status"], current_session["is_user_reject"] = f"{reject_m.group(1)}", True

                    ims_m = TEL_PATTERNS['IMS_REASON'].search(clean_line)
                    if TEL_PATTERNS['FAIL_EV'].search(clean_line) and ims_m:
                        current_session["status"], current_session["fail_reason"] = "FAIL", f"{ims_m.group(1)}: {ims_m.group(2)}"

                    if ims_m:
                        if ims_m.group(1) in ["501", "510"]:
                            current_session["status"], current_session["fail_reason"] = "SUCCESS", f"{ims_m.group(1)}: {ims_m.group(2)}"
                        else:
                            current_session["status"], current_session["fail_reason"] = "FAIL", f"{ims_m.group(1)}: {ims_m.group(2)}"

                    cs_m = TEL_PATTERNS['CS_REASON'].search(clean_line)
                    cs_fail_cause = ['34', '41', '42', '44', '49', '58', '65535']
                    if cs_m:
                        readerable_reason = CALL_FAIL_REASON_MAP.get(cs_m.group(1), f"미확인_코드({cs_m.group(1)})")
                        readerable_vendor_cause = VENDER_FAIL_REASON_MAP.get(cs_m.group(2), f"미확인_Vendor({cs_m.group(2)})")
                        current_session["status"] = "CALL DROP" if cs_m.group(1) in cs_fail_cause else "SUCCESS"
                        current_session["fail_reason"] = f"{cs_m.group(1)}({readerable_reason}): {cs_m.group(2)}({readerable_vendor_cause})"

                    # 세션 종료 판정
                    if TEL_PATTERNS['END_EV'].search(clean_line):
                        current_session["end_time"] = ts
                        current_session["logs"].append(f"==> [END_{target_phone_id}]: {clean_line}")

                        if current_session["status"] in ["FAIL", "CALL DROP"]:
                            current_session["cross_context_logs"] = self.get_context_fn(lines, ts)

                        all_sessions.append(current_session)
                        current_session = None
                        target_phone_id = None

            self.pre_context.append(clean_line)
        return {"sessions": all_sessions, "network_history": oos_history}