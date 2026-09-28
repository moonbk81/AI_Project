"""RTOS 콜(MO/MT)을 계층별 체크포인트로 따라가서 어디서 끊겼는지 찾는다.

콜 한 통은 여러 계층을 지나고 계층마다 로그를 남긴다.
  MO: UI/TAPI → ofono(RIL_REQUEST_DIAL) → rild(RIL_CPP DIAL) → RIL-IMSCALL 호 생성 → ALERTING → ACTIVE
  MT: IMS 착신 → RIL-IMSBRIDGE → RIL-IMSCALL 호 생성 → ofono 호 목록 → TAPI/UI 착신 화면 → ACTIVE
세션의 뼈대는 RIL-IMSCALL 의 `call + / call ~` 줄이다. 상태 전이가 가장 깔끔하게 찍힌다.
종료는 `GET_CURRENT_CALLS -> 0 call(s)`, 원인은 뒤따르는 `LAST_CALL_FAIL_CAUSE {16}` 이다.
"""

import json
import os
import re

from core.telephony_constants import CALL_FAIL_REASON_MAP
from parsers.base import BaseParser
from parsers.rtos.line import parse_lines

IMSCALL_NEW_RE = re.compile(r'call \+ sid=(\d+) idx=(\d+) (mo|mt) (\w+)')
IMSCALL_TRANS_RE = re.compile(r'call ~ sid=(\d+) idx=(\d+) (\w+) -> (\w+)')
IMSCALL_END_RE = re.compile(r'call - sid=(\d+)')
CLCC_COUNT_RE = re.compile(r'GET_CURRENT_CALLS -> (\d+) call\(s\)')
CALL_LIST_RE = re.compile(r'\[id=(\d+),([A-Z_]+),toa=\d+,\w+,(mo|mt)\b')
OFONO_RE = re.compile(r'^\[(\d+),(\d+)\]([<>]) RIL_((?:REQUEST|UNSOL)_\w+)\s*(.*)$')
RILD_REQ_RE = re.compile(r'^\[RIL_CPP\] \[(\d+)\]> (\w+)')
BRIDGE_INCOMING_RE = re.compile(r'noti:CallIncomingInd .*?sid=(\d+)')
FAIL_CAUSE_RE = re.compile(r'\{\s*(\d+)')

CALL_REQUEST_RE = re.compile(r'CALL|DIAL|HANGUP|ANSWER|UDUB|DTMF|CONFERENCE|SWITCH')

# 세션 시작 전에 찍히는 단계는 시작 시각에서 이만큼 앞까지, 시작 후 단계는 이만큼 뒤까지 본다
PRE_WINDOW_SEC = 10.0
POST_WINDOW_SEC = 10.0
FAIL_CAUSE_WINDOW_SEC = 3.0
MAX_UNANSWERED = 50

MO_CHAIN = [
    ("ui_dial", "UI/TAPI 발신 요청"),
    ("ofono_dial", "ofono → RIL DIAL"),
    ("rild_dial", "rild DIAL 수신"),
    ("ril_call", "RIL 호 생성"),
    ("alerting", "상대방 호출 중(ALERTING)"),
]
MT_CHAIN = [
    ("ims_incoming", "IMS 착신 수신"),
    ("bridge", "IMS → RIL 전달"),
    ("ril_call", "RIL 호 생성(INCOMING)"),
    ("ofono_clcc", "ofono 호 목록 수신"),
    ("ui_incoming", "TAPI/UI 착신 화면"),
]
PRE_STAGES = {"ui_dial", "ofono_dial", "rild_dial", "ims_incoming", "bridge"}


def _stage_of(rec):
    """세션과 무관하게 줄 하나가 어느 체크포인트인지 판단한다."""
    tag, msg = rec.tag, rec.msg
    if tag == "TAPI_FWK" and "Dialing:" in msg:
        return "ui_dial"
    if tag == "SAMSUNG_CALL" and "DIAL:" in msg:
        return "ui_dial"
    ofono = OFONO_RE.match(msg)
    if ofono:
        direction, name = ofono.group(3), ofono.group(4)
        if direction == ">" and name == "REQUEST_DIAL":
            return "ofono_dial"
        if direction == "<" and name == "REQUEST_GET_CURRENT_CALLS":
            return "ofono_clcc"
        return None
    rild = RILD_REQ_RE.match(msg)
    if rild and rild.group(2) == "DIAL":
        return "rild_dial"
    if tag == "IMS6.0" and "NotifyIncomingCall" in msg:
        return "ims_incoming"
    if tag == "IMS-FW" and "NOTIFY_INCOMING_CALL" in msg:
        return "ims_incoming"
    if tag == "RIL-IMSBRIDGE" and "noti:CallIncomingInd" in msg:
        return "bridge"
    if tag == "page_stack" and re.search(r'id=PAGE_SAMSUNG_CALL_(?:INCOMING|MT)', msg):
        return "ui_incoming"
    if tag == "TAPI_FWK" and re.search(r'incoming|ring', msg, re.I):
        return "ui_incoming"
    return None


def _evidence(rec):
    return {"time": rec.time, "line_no": rec.line_no, "text": rec.msg}


class RtosCallFlowParser(BaseParser):
    def analyze(self, lines):
        records = parse_lines(lines)
        markers = {}
        requests = {}
        request_order = []
        fail_causes = []
        calls = []
        active = []

        for rec in records:
            stage = _stage_of(rec)
            if stage:
                markers.setdefault(stage, []).append(rec)

            ofono = OFONO_RE.match(rec.msg)
            if ofono:
                self._track_request(rec, ofono, requests, request_order, fail_causes)

            if rec.tag == "RIL-IMSCALL":
                self._track_imscall(rec, calls, active)
            elif rec.tag == "RIL_CPP":
                self._track_call_list(rec, active)

        for call in calls:
            self._attach_checkpoints(call, markers)
            self._attach_requests(call, request_order)
        self._attach_fail_causes(calls, fail_causes)
        for call in calls:
            self._finalize(call)

        unanswered = [r for r in request_order if r["resp_time"] is None]
        return {
            "kpi": {
                "call_count": len(calls),
                "mo_count": sum(1 for c in calls if c["direction"] == "MO"),
                "mt_count": sum(1 for c in calls if c["direction"] == "MT"),
                "connected_count": sum(1 for c in calls if c["connected"]),
                "broken_count": sum(1 for c in calls if c["broken_at"]),
                "unanswered_request_count": len(unanswered),
            },
            "calls": [self._public(c) for c in calls],
            "unanswered_requests": unanswered[:MAX_UNANSWERED],
        }

    def save_ui_report(self, output_dir="./result", base_name="", analysis=None):
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{base_name}_rtos_call_flow.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(analysis or {}, f, indent=4, ensure_ascii=False)
        return out_path

    # ---- 수집 ----

    def _track_request(self, rec, m, requests, request_order, fail_causes):
        slot, token, direction, name, payload = m.groups()
        name = name.replace("REQUEST_", "", 1)
        key = (slot, token)
        if direction == ">":
            req = {
                "slot": int(slot), "token": token, "name": name,
                "req_time": rec.time, "req_sec": rec.sec, "req_line": rec.line_no,
                "resp_time": None, "latency_ms": None, "response": None,
            }
            requests[key] = req
            request_order.append(req)
            return
        req = requests.pop(key, None)
        if req:
            req["resp_time"] = rec.time
            req["latency_ms"] = round((rec.sec - req["req_sec"]) * 1000, 1)
            req["response"] = payload.strip() or None
        if name == "LAST_CALL_FAIL_CAUSE":
            cause = FAIL_CAUSE_RE.search(payload)
            if cause:
                fail_causes.append((rec, cause.group(1)))

    def _track_imscall(self, rec, calls, active):
        new = IMSCALL_NEW_RE.search(rec.msg)
        if new:
            sid, idx, direction, state = new.groups()
            call = {
                "sid": sid, "idx": int(idx), "direction": direction.upper(),
                "start_time": rec.time, "start_sec": rec.sec,
                "end_time": None, "end_sec": None,
                "states": [{"time": rec.time, "state": state, "line_no": rec.line_no}],
                "checkpoints": {"ril_call": _evidence(rec)},
                "fail_cause": None, "fail_reason": None, "ril_requests": [],
            }
            calls.append(call)
            active.append(call)
            return
        trans = IMSCALL_TRANS_RE.search(rec.msg)
        if trans:
            sid, _, _, new_state = trans.groups()
            call = next((c for c in active if c["sid"] == sid), None)
            if call:
                self._set_state(call, rec, new_state)
            return
        end = IMSCALL_END_RE.search(rec.msg)
        if end:
            call = next((c for c in active if c["sid"] == end.group(1)), None)
            if call:
                self._end(call, rec, active)
            return
        count = CLCC_COUNT_RE.search(rec.msg)
        if count and int(count.group(1)) == 0:
            for call in list(active):
                self._end(call, rec, active)

    def _track_call_list(self, rec, active):
        """rild 호 목록 `{[id=5,ACTIVE,...]}` 으로 IMSCALL 전이를 놓친 경우를 메운다."""
        for m in CALL_LIST_RE.finditer(rec.msg):
            idx, state = int(m.group(1)), m.group(2)
            call = next((c for c in active if c["idx"] == idx), None)
            if call:
                self._set_state(call, rec, state)

    def _set_state(self, call, rec, state):
        if call["states"][-1]["state"] != state:
            call["states"].append({"time": rec.time, "state": state, "line_no": rec.line_no})
        if state in ("ALERTING", "ACTIVE"):
            call["checkpoints"].setdefault(state.lower(), _evidence(rec))

    def _end(self, call, rec, active):
        call["end_time"], call["end_sec"] = rec.time, rec.sec
        active.remove(call)

    # ---- 세션에 붙이기 ----

    def _attach_checkpoints(self, call, markers):
        chain = MO_CHAIN if call["direction"] == "MO" else MT_CHAIN
        start = call["start_sec"]
        post_end = call["end_sec"] if call["end_sec"] is not None else start + POST_WINDOW_SEC
        for stage, _ in chain:
            if stage in call["checkpoints"]:
                continue
            candidates = markers.get(stage, [])
            if stage in PRE_STAGES:
                # 시작 직전 것 중 가장 가까운 것
                picked = [r for r in candidates if start - PRE_WINDOW_SEC <= r.sec <= start]
                if stage == "bridge":
                    picked = [r for r in picked if f"sid={call['sid']}" in r.msg] or picked
                rec = picked[-1] if picked else None
            else:
                rec = next((r for r in candidates if start < r.sec <= post_end), None)
            if rec:
                call["checkpoints"][stage] = _evidence(rec)

    def _attach_requests(self, call, request_order):
        lo = call["start_sec"] - PRE_WINDOW_SEC
        hi = call["end_sec"] + FAIL_CAUSE_WINDOW_SEC if call["end_sec"] is not None else float("inf")
        call["ril_requests"] = [
            {k: v for k, v in r.items() if k != "req_sec"}
            for r in request_order
            if lo <= r["req_sec"] <= hi and CALL_REQUEST_RE.search(r["name"])
        ]

    def _attach_fail_causes(self, calls, fail_causes):
        for rec, cause in fail_causes:
            ended = [c for c in calls if c["end_sec"] is not None
                     and 0 <= rec.sec - c["end_sec"] <= FAIL_CAUSE_WINDOW_SEC
                     and c["fail_cause"] is None]
            if ended:
                ended[-1]["fail_cause"] = cause
                ended[-1]["fail_reason"] = CALL_FAIL_REASON_MAP.get(cause, f"UNKNOWN ({cause})")

    def _finalize(self, call):
        chain = MO_CHAIN if call["direction"] == "MO" else MT_CHAIN
        call["connected"] = "active" in call["checkpoints"]
        call["broken_at"] = None
        if not call["connected"]:
            missing = next(((s, label) for s, label in chain if s not in call["checkpoints"]), None)
            if missing:
                call["broken_at"] = {"stage": missing[0], "label": missing[1]}
        ended = call["end_time"] is not None
        if call["connected"]:
            call["status"] = "통화 후 종료" if ended else "통화 중 로그 끝남"
        else:
            call["status"] = "연결 전 종료" if ended else "연결 전 로그 끝남"
        call["chain"] = [
            {"stage": s, "label": label, "reached": s in call["checkpoints"],
             "time": call["checkpoints"].get(s, {}).get("time")}
            for s, label in chain + [("active", "통화 연결(ACTIVE)")]
        ]

    def _public(self, call):
        return {k: v for k, v in call.items() if k not in ("start_sec", "end_sec")}
