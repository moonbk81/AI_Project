import re
from collections import deque
from core.constants import RE_TIME, RE_TAG, TEL_PATTERNS, VALID_TAGS, COMMON_EXCLUDES, PS_EXCLUDE_TAGS, SST_FIELDS, NETWORK_EXCLUDE_TAGS

from parsers.base import BaseParser
from parsers.call import ImsCallParser, CsCallStateMachine

class TelephonyParser(BaseParser):

    def _normalize_payload_from_dump_line(self, clean_line: str) -> str:
        """Telephony dump 라인에서 실제 payload만 분리한다."""
        parts = clean_line.split(" - ", 1)
        return parts[1] if len(parts) > 1 else clean_line

    def _normalize_payload_from_radio_line(self, clean_line: str) -> str:
        """Radio log 라인에서 실제 payload만 분리한다."""
        parts = clean_line.split(":", 1)
        return parts[1].strip() if len(parts) > 1 else clean_line

    def _time_to_sec(self, t_str: str) -> int:
        """MM-DD HH:MM:SS.mmm 형태의 timestamp를 초 단위로 변환한다."""
        try:
            if " " in t_str:
                t_str = t_str.split(" ")[1]
            p = t_str.split(".")[0].split(":")
            return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2])
        except Exception:
            return 0


    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.pre_context = deque(maxlen=50)
        # TelephonyParser는 전체 흐름만 조율하고,
        # 실제 CS/IMS Call 상태 해석은 전담 parser에 위임한다.
        self.ims_call_parser = ImsCallParser(self._extract_timestamp)
        self.cs_call_parser = CsCallStateMachine()

    def _extract_timestamp(self, line: str) -> str:
        # 1) 2025-05-03T07:04:35.388 / 2025-05-03 07:04:35.388
        m = re.search(
            r'\d{4}-(\d{2}-\d{2})[T\s](\d{2}:\d{2}:\d{2})(?:\.(\d{3}))?',
            line
        )
        if m:
            ms = m.group(3) or "000"
            return f"{m.group(1)} {m.group(2)}.{ms}"

        # 2) 05-03 07:04:35.388 / 05-03 07:04:35
        m = re.search(
            r'(\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})(?:\.(\d{3}))?',
            line
        )
        if m:
            ms = m.group(3) or "000"
            return f"{m.group(1)} {m.group(2)}.{ms}"

        # 3) Connection History 스타일: (05-03) ... (07:04:35)
        date_m = re.search(r'\((\d{2}-\d{2})\)', line)
        time_m = re.search(r'\((\d{2}:\d{2}:\d{2})\)', line)
        if date_m and time_m:
            return f"{date_m.group(1)} {time_m.group(1)}.000"

        return "UnknownTime"


    def _extract_connection_history_entry(self, clean_line: str, tc_id_re):
        """Connection History 라인에서 TC ID와 시작 시간을 추출한다."""
        tc_m = tc_id_re.search(clean_line)
        if not tc_m:
            return None

        time_m = re.findall(r'\((\d{2}:\d{2}:\d{2})\)', clean_line)
        date_m = re.search(r'\((\d{2}-\d{2})\)', clean_line)
        if not time_m or not date_m:
            return None

        start_ts = f"{date_m.group(1)} {time_m[0]}.000"
        return {
            "tc_id": tc_m.group(1),
            "start_sec": self._time_to_sec(start_ts),
            "raw_log": clean_line
        }

    def _finalize_active_sessions(self, dump_sessions, radio_sessions, active_dump_calls, active_radio_calls):
        """종료 이벤트를 만나지 못한 active call을 최종 세션 목록에 합친다."""
        dump_sessions.extend(active_dump_calls)
        radio_sessions.extend(active_radio_calls)

    def _merge_cs_sessions(self, dump_sessions, radio_sessions):
        """dump 기반 CS 세션을 우선 사용하고 없을 때 radio 기반 세션을 사용한다."""
        return dump_sessions if dump_sessions else radio_sessions

    def _bind_connection_history(self, sessions, conn_histories):
        """TC ID가 없는 세션을 Connection History 기준으로 보강한다."""
        for session in sessions:
            if "TC@" in session["id"]:
                continue

            s_sec = self._time_to_sec(session["start_time"])
            for ch in conn_histories:
                if abs(s_sec - ch["start_sec"]) > 5:
                    continue

                # PS 콜은 기존 objId를 남겨두고 앞에 TC@를 붙여 가독성을 높인다.
                if "objId:" in session["id"]:
                    session["id"] = f"{ch['tc_id']} ({session['id']})"
                else:
                    session["id"] = ch["tc_id"]

                session["logs"].append(f"==> [BOUND from Connection History]: {ch['raw_log']}")
                break

    def _dedupe_and_sort_sessions(self, sessions):
        """세션 로그 중복 제거 후 시작 시간 기준으로 정렬한다."""
        for session in sessions:
            session["logs"] = sorted(list(set(session["logs"])))
        sessions.sort(key=lambda x: x["start_time"])
        return sessions

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


        # --- 로그 라인 순회 ---
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
                    conn_entry = self._extract_connection_history_entry(clean_line, tc_id_re)
                    if conn_entry:
                        conn_histories.append(conn_entry)
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
                    time_part = m.group(2)
                    if '.' not in time_part:
                        time_part += ".000"
                    ts = f"{m.group(1)} {time_part}"
                    payload = self._normalize_payload_from_dump_line(clean_line)
                    self.cs_call_parser.process(
                        ts, payload, dump_sessions, active_dump_calls,
                        slot_id=current_dump_slot, tc_id_re=tc_id_re
                    )

            elif in_radio:
                m = radio_time_re.search(clean_line)
                if m:
                    ts = f"{m.group(1)} {m.group(2)}"
                    payload = self._normalize_payload_from_radio_line(clean_line)
                    radio_slot_m = re.search(r'PHONE(\d)', clean_line, re.I)
                    radio_slot = radio_slot_m.group(1) if radio_slot_m else "Unknown"
                    self.cs_call_parser.process(
                        ts, payload, radio_sessions, active_radio_calls,
                        slot_id=radio_slot, tc_id_re=tc_id_re
                    )

        self._finalize_active_sessions(
            dump_sessions, radio_sessions,
            active_dump_calls, active_radio_calls
        )

        merged_sessions = self._merge_cs_sessions(dump_sessions, radio_sessions)
        merged_sessions.extend(self.ims_call_parser.parse(lines))

        self._bind_connection_history(merged_sessions, conn_histories)
        return self._dedupe_and_sort_sessions(merged_sessions)

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