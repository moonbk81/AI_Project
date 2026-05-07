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
        call_log_dumps = []
        current_session = None
        dump_current_session = None
        target_phone_id = None
        in_radio = False
        in_call_log = False

        import re
        dump_time_re = re.compile(r'\d{4}-(\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})')

        for line in lines:
            clean_line = line.strip()
            ts_m = RE_TIME.search(clean_line)
            ts = ts_m.group(0) if ts_m else "00-00 00:00:00.000"

            # ==========================================
            # 1. Framework Dump (Call Log) 심층 상태 추적 파싱
            # ==========================================
            if clean_line.startswith("Call Log") or "DUMP OF SERVICE telecom" in clean_line:
                in_call_log = True
                continue

            if in_call_log:
                if clean_line.startswith("---------") or ("DUMP OF" in clean_line and "telecom" not in clean_line):
                    in_call_log = False
                    if dump_current_session:
                        call_log_dumps.append(dump_current_session)
                        dump_current_session = None
                    continue

                dt_match = dump_time_re.search(clean_line)
                if not dt_match:
                    continue

                date_str, time_str = dt_match.group(1), dt_match.group(2)
                d_ts = f"{date_str} {time_str}.000"

                fallback_triggers = [
                    "CODE_LOCAL_CALL_CS_RETRY_REQUIRED",
                    "CODE_SIP_ALTERNATE_EMERGENCY_CALL",
                    "CODE_LOCAL_NETWORK_NO_SERVICE",
                    "380, ALTERNATIVE", "380, UNKNOWN", "381, ALTERNATIVE",
                    "408, REQUEST TIMEOUT", "488, NOT ACCEPTABLE",
                    "503, SERVICE UNAVAILABLE", "504, SERVER TIMEOUT"
                ]

                # (1) 380 에러 발생 -> 기존 IMS 끊고 CS 리다이얼 생성
                if any(kw in clean_line.upper() for kw in fallback_triggers):
                    if dump_current_session:
                        dump_current_session["end_time"] = d_ts
                        dump_current_session["status"] = "FAIL (CS_FALLBACK)"
                        dump_current_session["fail_reason"] = "Fallback Triggered (Alternative/Timeout/Error)"
                        dump_current_session["logs"].append(f"[{d_ts}] IMS Call Failed -> Triggering CS Redial")
                        call_log_dumps.append(dump_current_session)

                    # 새로운 CS 리다이얼 세션 오픈
                    dump_current_session = {
                        "type": "CS",
                        "slot": "Unknown",
                        "start_time": d_ts,
                        "end_time": None,
                        "id": "RESTORED_CS",
                        "status": "DIALING",
                        "is_user_reject": False,
                        "fail_reason": "0",
                        "logs": [f"[{d_ts}] CS Silent Redial Started", clean_line]
                    }

                # (2) 통화 연결 (ACTIVE 상태 감지)
                elif dump_current_session and dump_current_session["type"] == "CS" and ",ACTIVE," in clean_line:
                    dump_current_session["status"] = "SUCCESS"
                    dump_current_session["call_state"] = "ACTIVE"
                    dump_current_session["logs"].append(f"[{d_ts}] CS Call Active: {clean_line}")

                # 💡 (3) [핵심 수정] 통화 종료 판단 로직 (CS와 PS 엄격 분리 및 방어)
                elif dump_current_session and (
                    (dump_current_session["type"] == "CS" and any(kw in clean_line for kw in ["> HANGUP", "< GET_CURRENT_CALLS {}"])) or
                    (dump_current_session["type"] != "CS" and any(kw in clean_line for kw in ["> terminate", "> close", "> HANGUP", "< GET_CURRENT_CALLS {}"]) and "redialToCs" not in clean_line)
                ):
                    dump_current_session["end_time"] = d_ts
                    if dump_current_session["type"] == "CS":
                        if dump_current_session["status"] == "DIALING":
                            dump_current_session["status"] = "CALL DROP"
                    else:
                        if dump_current_session["status"] == "IMS_INITIATED":
                            dump_current_session["status"] = "ENDED"
                    dump_current_session["logs"].append(f"[{d_ts}] Call Ended: {clean_line}")
                    call_log_dumps.append(dump_current_session)
                    dump_current_session = None

                # (4) 정확한 통화 시작 감지
                elif not dump_current_session and any(kw in clean_line for kw in ["> makeCall", "> DIAL", "> EMERGENCY_DIAL"]):
                    call_type = "PS(VoLTE)" if "makeCall" in clean_line else "CS"
                    dump_current_session = {
                        "type": call_type,
                        "slot": "Unknown",
                        "start_time": d_ts,
                        "end_time": None,
                        "id": "RESTORED_" + call_type[:2],
                        "status": "IMS_INITIATED" if call_type == "PS(VoLTE)" else "DIALING",
                        "is_user_reject": False,
                        "fail_reason": "0",
                        "logs": [f"[{d_ts}] Call Initiated", clean_line]
                    }

                elif dump_current_session and dump_current_session["type"] == "PS(VoLTE)" and any(
                    kw in clean_line for kw in [
                        "onCallStarted",
                        "callSessionStarted"
                    ]
                ):
                    dump_current_session["status"] = "SUCCESS"
                    dump_current_session["logs"].append(f"[{d_ts}] IMS Call Connected: {clean_line}")

                continue

            # ==========================================
            # 2. Radio Log 구간 (기존 로직 유지)
            # ==========================================
            if "logcat -b radio" in line or "--------- beginning of radio" in line:
                in_radio = True
                continue
            if in_radio and any(kw in line for kw in [
                "was the duration of 'RADIO LOG'",
                "--------- beginning of main",
                "--------- beginning of system"
            ]):
                in_radio = False
                continue

            if in_radio:
                tag_m = RE_TAG.search(line)
                tag = tag_m.group(1).strip() if tag_m else None
                if not tag or tag not in VALID_TAGS: continue

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

                    fallback_sip_codes = ["380", "381", "408", "488", "503", "504"]
                    is_fallback_text = ("INVITE" in clean_line and any(err in clean_line for err in ["380 ", "381 ", "Alternative", "Unknown Status"]))

                    if ims_m or is_fallback_text:
                        code = ims_m.group(1) if ims_m else "380/381"
                        reason = ims_m.group(2) if ims_m else "Alternative Service (Silent Redial)"

                        if code in ["501", "510"]:
                            current_session["status"], current_session["fail_reason"] = "SUCCESS", f"{code}: {reason}"
                        elif code in fallback_sip_codes or is_fallback_text:
                            current_session["status"] = "CS_FALLBACK"
                            current_session["fail_reason"] = f"{code}: {reason}"
                            current_session["end_time"] = ts
                            all_sessions.append(current_session)

                            current_session = {
                                "type": "CS",
                                "slot": current_session.get("slot", "PHONE0"),
                                "start_time": ts,
                                "end_time": None,
                                "id": "SILENT_REDIAL",
                                "status": "Unknown",
                                "is_user_reject": False,
                                "fail_reason": "0",
                                "logs": [f"==> [CS_FALLBACK_START]: Silent Redial Triggered by {code}", clean_line]
                            }
                            continue
                        else:
                            current_session["status"], current_session["fail_reason"] = "FAIL", f"{code}: {reason}"

                    cs_m = TEL_PATTERNS['CS_REASON'].search(clean_line)
                    cs_fail_cause = ['34', '41', '42', '44', '49', '58', '65535']
                    if cs_m:
                        readerable_reason = CALL_FAIL_REASON_MAP.get(cs_m.group(1), f"미확인_코드({cs_m.group(1)})")
                        readerable_vendor_cause = VENDER_FAIL_REASON_MAP.get(cs_m.group(2), f"미확인_Vendor({cs_m.group(2)})")
                        current_session["status"] = "CALL DROP" if cs_m.group(1) in cs_fail_cause else "SUCCESS"
                        current_session["fail_reason"] = f"{cs_m.group(1)}({readerable_reason}): {cs_m.group(2)}({readerable_vendor_cause})"

                    if TEL_PATTERNS['END_EV'].search(clean_line):
                        current_session["end_time"] = ts
                        current_session["logs"].append(f"==> [END_{target_phone_id}]: {clean_line}")

                        if current_session["status"] in ["FAIL", "CALL DROP"]:
                            current_session["cross_context_logs"] = self.get_context_fn(lines, ts)

                        all_sessions.append(current_session)
                        current_session = None
                        target_phone_id = None

            self.pre_context.append(clean_line)

        # ==========================================
        # 3. 루프 종료 후 남은 세션 수거 및 스마트 병합 (초 단위)
        # ==========================================
        if current_session:
            all_sessions.append(current_session)
        if dump_current_session:
            call_log_dumps.append(dump_current_session)

        def time_to_sec(t_str):
            try:
                parts = t_str.split(" ")[1].split(".")[0].split(":")
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except:
                return 0

        for dump_call in call_log_dumps:
            d_sec = time_to_sec(dump_call["start_time"])
            matched_radio_session = None

            for r_call in all_sessions:
                if "RESTORED" not in r_call.get("id", ""):
                    r_sec = time_to_sec(r_call["start_time"])
                    if abs(d_sec - r_sec) <= 3:
                        matched_radio_session = r_call
                        break

            if matched_radio_session:
                if matched_radio_session["status"] in ["Unknown", "PENDING"] or matched_radio_session["end_time"] is None:
                    matched_radio_session["status"] = dump_call["status"]
                    matched_radio_session["end_time"] = dump_call["end_time"]
                    if dump_call["fail_reason"] != "0":
                        matched_radio_session["fail_reason"] = dump_call["fail_reason"]
                    matched_radio_session["logs"].extend(dump_call["logs"])
            else:
                all_sessions.append(dump_call)

        return {"sessions": all_sessions, "network_history": oos_history}

