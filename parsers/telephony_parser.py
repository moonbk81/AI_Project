import re
from collections import deque, defaultdict
from core.constants import RE_TIME, RE_TAG, TEL_PATTERNS, VALID_TAGS, COMMON_EXCLUDES, PS_EXCLUDE_TAGS, SST_FIELDS, NETWORK_EXCLUDE_TAGS
from core.telephony_constants import CALL_FAIL_REASON_MAP, VENDER_FAIL_REASON_MAP
from parsers.base import BaseParser

class TelephonyParser(BaseParser):
    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.pre_context = deque(maxlen=50)

    # 💡 [신규 추가] IMS/PS Call 전담 파서 (objId 기반 멀티콜 지원)
    def _parse_ims_multi_calls(self, lines):
        calls = defaultdict(list)
        obj_to_tc = {}  # 💡 [추가] objId와 TC@ 매핑을 저장할 딕셔너리

        time_re = re.compile(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3})')
        obj_re = re.compile(r'objId:(\d+)')
        tc_id_re = re.compile(r'(TC@[a-zA-Z0-9_]+)') # 💡 [추가] TC 정규식

        event_keywords = [
            'onIncomingCall', 'takeCall', 'accept', 'reject',
            'onCallTerminated', 'onCallStartFailed'
        ]

        for line in lines:
            if "[IPCT]" not in line and "[IPCN]" not in line:
                continue

            obj_match = obj_re.search(line)
            if not obj_match:
                continue

            obj_id = obj_match.group(1)

            # 💡 [핵심] 같은 라인에 TC@가 찍혀있다면 objId의 짝꿍으로 저장해둠
            tc_match = tc_id_re.search(line)
            if tc_match:
                obj_to_tc[obj_id] = tc_match.group(1)

            detected_event = None
            for keyword in event_keywords:
                if keyword in line:
                    detected_event = keyword
                    break

            if not detected_event:
                continue

            time_match = time_re.search(line)
            timestamp = time_match.group(1) if time_match else "UnknownTime"

            reason = ""
            if "Terminated" in detected_event or "reject" in detected_event or "Failed" in detected_event:
                reason_match = re.search(r'(CODE_[A-Z_]+|USER_DECLINE|\d{3}\s:\s[^,]+)', line)
                if reason_match:
                    reason = f" [{reason_match.group(1).strip()}]"

            event_str = f"{timestamp} : {detected_event}{reason}"

            if not calls[obj_id] or not calls[obj_id][-1].endswith(f"{detected_event}{reason}"):
                calls[obj_id].append(event_str)

        multi_calls_list = []
        for obj_id, events in calls.items():
            start_time = events[0].split(" : ")[0] if events else "Unknown"
            end_time = events[-1].split(" : ")[0] if events else "Unknown"

            status = "SUCCESS"
            if "Terminated" in events[-1] or "reject" in events[-1] or "Failed" in events[-1]:
                 status = "FAIL" if "CODE_USER" not in events[-1] and "USER_DECLINE" not in events[-1] else "NORMAL_RELEASE"

            # 💡 [추가] 찾은 TC@ 아이디가 있으면 objId와 함께 예쁘게 표시
            tc_id = obj_to_tc.get(obj_id)
            display_id = f"{tc_id} (objId:{obj_id})" if tc_id else f"objId:{obj_id}"

            multi_calls_list.append({
                "type": "PS(VoLTE)",
                "id": display_id,  # 👈 여기에 반영됩니다.
                "start_time": start_time,
                "end_time": end_time,
                "status": status,
                "fail_reason": events[-1] if status == "FAIL" else "0",
                "logs": events
            })

        return multi_calls_list

    def analyze(self, lines):
        dump_sessions = []
        radio_sessions = []
        conn_histories = []

        active_dump_calls = []
        active_radio_calls = []

        in_call_log = False
        in_radio = False
        in_conn_history = False
        current_dump_slot = "Unknown"

        dump_time_re = re.compile(r'\d{4}-(\d{2}-\d{2})T(\d{2}:\d{2}:\d{2}\.\d{3})')
        radio_time_re = re.compile(r'(\d{2}-\d{2})\s(\d{2}:\d{2}:\d{2}\.\d{3})')
        tc_id_re = re.compile(r'(TC@[a-zA-Z0-9_]+)')

        def time_to_sec(t_str):
            try:
                if " " in t_str: t_str = t_str.split(" ")[1]
                p = t_str.split(".")[0].split(":")
                return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2])
            except: return 0

        # 💡 [변경점] process_payload는 이제 순수하게 CS Call(또는 예외적인 Call)만 처리합니다.
        def process_cs_multi_payload(ts, payload, completed_list, active_list, slot_id="Unknown"):
            is_cs = TEL_PATTERNS['CS_START'].search(payload)
            tc_match = tc_id_re.search(payload)
            id_m = TEL_PATTERNS['CONN_ID'].search(payload)

            current_id = None
            if tc_match:
                current_id = tc_match.group(1)
            elif id_m:
                current_id = f"conn_id:{id_m.group(1)}"

            # 1. 새로운 CS Call 발생 시 active_list에 추가
            if is_cs:
                new_call = {
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
                active_list.append(new_call)
                return

            # 2. 이 로그가 어느 통화의 것인지(Target Call) 식별
            target_call = None
            if current_id:
                for call in active_list:
                    if call["id"] == current_id:
                        target_call = call
                        break

                if not target_call:
                    for call in active_list:
                        if call["id"].startswith("Unknown"):
                            call["id"] = current_id
                            target_call = call
                            break
            else:
                if active_list:
                    target_call = active_list[-1]

            if not target_call:
                return

            if target_call["slot"] == "Unknown" and slot_id != "Unknown":
                target_call["slot"] = slot_id

            target_call["logs"].append(f"[{ts}] {payload}")
            if ",ACTIVE," in payload:
                target_call["status"] = "SUCCESS"

            cs_m = TEL_PATTERNS['CS_REASON'].search(payload)
            if cs_m:
                is_drop = cs_m.group(1) in ['34', '41', '42', '44', '49', '58', '65535']
                if not is_drop and target_call["status"] == "DIALING" and cs_m.group(1) == "16":
                    target_call["status"] = "CANCELED"
                else:
                    target_call["status"] = "CALL DROP" if is_drop else "SUCCESS"

                reason_desc = CALL_FAIL_REASON_MAP.get(cs_m.group(1), f"Code:{cs_m.group(1)}")
                target_call["fail_reason"] = reason_desc

            is_end = TEL_PATTERNS['END_EV'].search(payload)
            if is_end:
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

        # --- 로그 라인 순회 (기존과 동일하지만 함수 호출부 변경) ---
        for line in lines:
            clean_line = line.strip()

            if "TelephonyLogger[" in clean_line:
                slot_m = re.search(r'TelephonyLogger\[(\d+)\]', clean_line)
                if slot_m: current_dump_slot = slot_m.group(1)

            if clean_line.startswith("Connection History Log") or "Connection History Log:" in clean_line:
                in_conn_history = True; in_call_log = False; in_radio = False
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
                                "tc_id": tc_id, "start_sec": time_to_sec(start_ts), "raw_log": clean_line
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

            # 💡 [변경점] 새로운 process_cs_multi_payload 함수 호출
            if in_call_log:
                m = dump_time_re.search(clean_line)
                if m:
                    ts = f"{m.group(1)} {m.group(2)}.000"
                    parts = clean_line.split(" - ", 1)
                    payload = parts[1] if len(parts) > 1 else clean_line
                    process_cs_multi_payload(ts, payload, dump_sessions, active_dump_calls, slot_id=current_dump_slot)

            elif in_radio:
                m = radio_time_re.search(clean_line)
                if m:
                    ts = f"{m.group(1)} {m.group(2)}"
                    parts = clean_line.split(":", 1)
                    payload = parts[1].strip() if len(parts) > 1 else clean_line
                    radio_slot_m = re.search(r'PHONE(\d)', clean_line, re.I)
                    radio_slot = radio_slot_m.group(1) if radio_slot_m else "Unknown"
                    process_cs_multi_payload(ts, payload, radio_sessions, active_radio_calls, slot_id=radio_slot)

        # 4. 루프 종료 후, 아직 END_EV를 못 만나서 끝나지 않은(Active) 통화들을 완료 목록에 합쳐줌
        dump_sessions.extend(active_dump_calls)
        radio_sessions.extend(active_radio_calls)

        merged_sessions = dump_sessions if dump_sessions else radio_sessions

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

        # 💡 [핵심] 독립적으로 파싱한 IMS/PS Call 정보들을 가져와서 CS 리스트와 병합
        ps_sessions = self._parse_ims_multi_calls(lines)
        merged_sessions.extend(ps_sessions)

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

