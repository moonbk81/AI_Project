import re
from collections import deque
from core.constants import RE_TIME, RE_TAG, TEL_PATTERNS, VALID_TAGS, COMMON_EXCLUDES, PS_EXCLUDE_TAGS, SST_FIELDS, NETWORK_EXCLUDE_TAGS
from core.telephony_constants import CALL_FAIL_REASON_MAP, VENDER_FAIL_REASON_MAP
from parsers.base import BaseParser

class TelephonyParser(BaseParser):
    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.pre_context = deque(maxlen=50)

    def analyze(self, lines):
        dump_sessions = []
        radio_sessions = []
        conn_histories = []

        current_dump = None
        current_radio = None

        in_call_log = False
        in_radio = False
        in_conn_history = False

        # 💡 Slot 추적용 변수 추가
        current_dump_slot = "Unknown"

        dump_time_re = re.compile(r'\d{4}-(\d{2}-\d{2})T(\d{2}:\d{2}:\d{2}\.\d{3})')
        radio_time_re = re.compile(r'(\d{2}-\d{2})\s(\d{2}:\d{2}:\d{2}\.\d{3})')
        tc_id_re = re.compile(r'(TC@[a-zA-Z0-9_]+)')
        # 💡 ImsReasonInfo 정규식 추가
        ims_reason_re = re.compile(r'ImsReasonInfo\s*::\s*\{(\d+)\s*:\s*([^,}]+)')

        def time_to_sec(t_str):
            try:
                if " " in t_str: t_str = t_str.split(" ")[1]
                p = t_str.split(".")[0].split(":")
                return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2])
            except: return 0

        def process_payload(ts, payload, session_list, current_call, slot_id="Unknown"):
            is_cs = TEL_PATTERNS['CS_START'].search(payload)
            is_ps = TEL_PATTERNS['PS_START'].search(payload)

            if is_cs or is_ps:
                if current_call: session_list.append(current_call)
                current_call = {
                    "type": "CS" if is_cs else "PS(VoLTE)", "slot": slot_id, "start_time": ts, "end_time": None,
                    "id": "Unknown", "status": "DIALING", "is_user_reject": False, "fail_reason": "0",
                    "logs": [f"[{ts}] START: {payload}"]
                }
                return current_call

            if current_call:
                # 중간에 Slot 정보가 업데이트되면 반영
                if current_call["slot"] == "Unknown" and slot_id != "Unknown":
                    current_call["slot"] = slot_id

                current_call["logs"].append(f"[{ts}] {payload}")

                if tc_match := tc_id_re.search(payload):
                    current_call["id"] = tc_match.group(1)
                elif "TC@" not in current_call["id"]:
                    if id_m := TEL_PATTERNS['CONN_ID'].search(payload):
                        current_call["id"] = id_m.group(1)

                if current_call["type"] == "CS" and ",ACTIVE," in payload:
                    current_call["status"] = "SUCCESS"
                elif current_call["type"] == "PS(VoLTE)" and any(kw in payload for kw in ["onCallStarted", "callSessionStarted"]):
                    current_call["status"] = "SUCCESS"

                # 💡 IMS 실패/종료 사유 (ImsReasonInfo) 파싱 및 기록
                ims_m = ims_reason_re.search(payload)
                explicit_fallback_kws = [
                    "CODE_LOCAL_CALL_CS_RETRY_REQUIRED",
                    "CODE_SIP_ALTERNATE_EMERGENCY_CALL"
                ]

                sip_fallback_re = re.compile(r'(SIP/2\.0\s+(380|381|408|488|503|504)|(380|381)\s+Alternative)', re.I)

                is_explicit_kw = any(kw in payload for kw in explicit_fallback_kws)
                sip_m = sip_fallback_re.search(payload)

                fallback_codes = ["380", "381", "408", "488", "503", "504"]
                is_fallback_trigger = (
                    is_explicit_kw or
                    bool(sip_m) or
                    (ims_m and ims_m.group(1) in fallback_codes)
                )

                if ims_m or is_fallback_trigger:
                    code = ims_m.group(1) if ims_m else ("380" if sip_m else "FallbackTrigger")
                    reason = ims_m.group(2).strip() if ims_m else "Alternative Service(Silent Redial)"

                    # 1. 일반적인 fail_reason 할당 (CS 리다이얼이 아니어도 모두 할당)
                    current_call["fail_reason"] = f"{code}: {reason}"
                    if code not in ["501", "510"] and any(kw in payload for kw in ["StartFailed", "reject", "FAIL"]):
                        if current_call["status"] == "DIALING":
                            current_call["status"] = "FAIL"

                    # 2. 380 등 Fallback 인 경우 즉시 분리 후 CS 세션 생성
                    if is_fallback_trigger:
                        current_call["status"] = "CS_FALLBACK"
                        current_call["end_time"] = ts
                        session_list.append(current_call)

                        current_call = {
                            "type": "CS", "slot": slot_id, "start_time": ts, "end_time": None,
                            "id": "SILENT_REDIAL", "status": "DIALING", "is_user_reject": False, "fail_reason": "0",
                            "logs": [f"[{ts}] CS_FALLBACK_START: {payload}"]
                        }
                        return current_call

                # 💡 CS Reason 파싱
                cs_m = TEL_PATTERNS['CS_REASON'].search(payload)
                if cs_m:
                    is_drop = cs_m.group(1) in ['34', '41', '42', '44', '49', '58', '65535']
                    if not is_drop and current_call["status"] == "DIALING" and cs_m.group(1) == "16":
                        current_call["status"] = "CANCELED"
                    else:
                        current_call["status"] = "CALL DROP" if is_drop else "SUCCESS"

                    reason_desc = CALL_FAIL_REASON_MAP.get(cs_m.group(1), f"Code:{cs_m.group(1)}")
                    current_call["fail_reason"] = reason_desc

                is_end = TEL_PATTERNS['END_EV'].search(payload)
                if is_end:
                    current_call["end_time"] = ts
                    if current_call["status"] == "DIALING":
                        reason = current_call.get("fail_reason")
                        if any(code in reason for code in ["510", "501", "16(", "Normal"]):
                            current_call["status"] = "CANCELED"
                        elif reason != "0" and "Fallback" not in reason:
                            current_call["status"] = "FAIL"
                        else:
                            current_call["status"] = "CALL DROP"
                    session_list.append(current_call)
                    current_call = None

            return current_call

        for line in lines:
            clean_line = line.strip()

            # 💡 TelephonyLogger[slot] 헤더에서 슬롯 ID 추적
            if "TelephonyLogger[" in clean_line:
                slot_m = re.search(r'TelephonyLogger\[(\d+)\]', clean_line)
                if slot_m:
                    current_dump_slot = slot_m.group(1)

            if clean_line.startswith("Connection History Log") or "Connection History Log:" in clean_line:
                in_conn_history = True
                in_call_log = False
                in_radio = False
                continue

            if in_conn_history:
                if clean_line.startswith("---------") or "DUMP OF" in clean_line or clean_line.startswith("Call Log"):
                    in_conn_history = False
                else:
                    tc_m = tc_id_re.search(clean_line)
                    if tc_m:
                        tc_id = tc_m.group(1)
                        time_m = re.findall(r'\((\d{2}:\d{2}:\d{2})\)', clean_line)
                        date_m = re.search(r'\((\d{2}-\d{2})\)', clean_line)
                        if time_m and date_m:
                            start_ts = f"{date_m.group(1)} {time_m[0]}.000"
                            conn_histories.append({
                                "tc_id": tc_id,
                                "start_sec": time_to_sec(start_ts),
                                "raw_log": clean_line
                            })
                    continue

            if clean_line.startswith("Call Log") or "DUMP OF SERVICE telecom" in clean_line:
                in_call_log = True; continue
            if in_call_log and (clean_line.startswith("---------") or ("DUMP OF" in clean_line and "telecom" not in clean_line)):
                in_call_log = False; continue

            if "logcat -b radio" in clean_line or "--------- beginning of radio" in clean_line:
                in_radio = True; continue
            if in_radio and any(kw in clean_line for kw in ["was the duration", "--------- beginning of main", "--------- beginning of system"]):
                in_radio = False; continue

            if in_call_log:
                m = dump_time_re.search(clean_line)
                if m:
                    ts = f"{m.group(1)} {m.group(2)}.000"
                    parts = clean_line.split(" - ", 1)
                    payload = parts[1] if len(parts) > 1 else clean_line
                    # 💡 current_dump_slot 전달
                    current_dump = process_payload(ts, payload, dump_sessions, current_dump, slot_id=current_dump_slot)

            elif in_radio:
                m = radio_time_re.search(clean_line)
                if m:
                    ts = f"{m.group(1)} {m.group(2)}"
                    parts = clean_line.split(":", 1)
                    payload = parts[1].strip() if len(parts) > 1 else clean_line
                    # 💡 라디오 로그의 PHONE0/1 에서 슬롯 추출 (없으면 Unknown)
                    radio_slot_m = re.search(r'PHONE(\d)', clean_line, re.I)
                    radio_slot = radio_slot_m.group(1) if radio_slot_m else "Unknown"
                    current_radio = process_payload(ts, payload, radio_sessions, current_radio, slot_id=radio_slot)

        if current_dump: dump_sessions.append(current_dump)
        if current_radio: radio_sessions.append(current_radio)

        merged_sessions = []
        if dump_sessions:
            for r_call in radio_sessions:
                r_sec = time_to_sec(r_call["start_time"])
                matched = False
                for d_call in dump_sessions:
                    d_sec = time_to_sec(d_call["start_time"])
                    if abs(r_sec - d_sec) <= 4:
                        matched = True
                        d_call["logs"].extend(r_call["logs"])
                        if d_call["fail_reason"] == "0" and r_call["fail_reason"] != "0":
                            d_call["fail_reason"] = r_call["fail_reason"]
                        if d_call["slot"] == "Unknown" and r_call["slot"] != "Unknown":
                            d_call["slot"] = r_call["slot"]
                        break
                if not matched:
                    dump_sessions.append(r_call)
            merged_sessions = dump_sessions
        else:
            merged_sessions = radio_sessions

        for session in merged_sessions:
            if "TC@" not in session["id"]:
                s_sec = time_to_sec(session["start_time"])
                for ch in conn_histories:
                    if abs(s_sec - ch["start_sec"]) <= 5:
                        session["id"] = ch["tc_id"]
                        session["logs"].append(f"==> [BOUND from Connection History]: {ch['raw_log']}")
                        break

        for session in merged_sessions:
            session["logs"] = sorted(list(set(session["logs"])))
        merged_sessions.sort(key=lambda x: x["start_time"])

        return merged_sessions

# ==========================================
# 2. Radio Log 전담 망 이탈(OOS) 파서
# ==========================================
class OosParser(BaseParser):
    def __init__(self, context_getter=None):
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
        oos_history = []
        in_radio = False

        for line in lines:
            clean_line = line.strip()
            ts_m = RE_TIME.search(clean_line)
            ts = ts_m.group(0) if ts_m else "00-00 00:00:00.000"

            if "logcat -b radio" in line or "--------- beginning of radio" in line:
                in_radio = True
                continue
            if in_radio and any(kw in line for kw in ["was the duration of 'RADIO LOG'", "--------- beginning of main", "--------- beginning of system"]):
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
                        recent_logs = [l for l in list(self.pre_context) if not any(t in l for t in NETWORK_EXCLUDE_TAGS)]
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
                            "time": ts, "slotId": slot_id, "event_type": event_type,
                            "voice_reg": v_reg, "data_reg": d_reg, "rat": self._parse_sst_val(ss_data, 'rat'),
                            "root_cause_candidate": reason,
                            "operator": f"{self._parse_sst_val(ss_data, 'op_long')} ({self._parse_sst_val(ss_data, 'op_short')})",
                            "rej_cause": self._parse_sst_val(ss_data, 'rej_cause'),
                            "emergency": self._parse_sst_val(ss_data, 'is_emergency'),
                            "context_snapshot": recent_logs[-15:]
                        }

                        if event_type == "OOS_ENTER" and self.get_context_fn:
                            oos_event["cross_context_logs"] = self.get_context_fn(lines, ts)

                        oos_history.append(oos_event)
                        self.last_slot_states[slot_id] = {"v": v_reg[0], "d": d_reg[0]}

            self.pre_context.append(clean_line)

        return oos_history

