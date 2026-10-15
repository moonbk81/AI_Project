"""RTOS 콜(MO/MT)을 계층별 체크포인트로 따라가서 어디서 끊겼는지 찾는다.

콜 한 통은 여러 계층을 지나고 계층마다 로그를 남긴다. 텔레포니 스택이 두 세대라 둘 다 읽는다.

telephonyd (2026-10 ~): ofono / D-Bus / rild 를 지우고 Samsung TAPI SDK → telephonyd(libtelephony) → secril 로 바뀌었다.
  MO: UI/TAPI(`[TAPI_CLIENT] x: > CALL_DIAL serial=n`) → telephonyd(`[TAPI_SERVER] > CALL_DIAL x#n`)
      → imsservice(`[IPCT] [0014]> MakeCall domain.call#3`, `[0014]< MakeCall domain.call#3 {sid=N} 12ms`)
      → call domain 호 생성(`[TCORE_CALL] call + ims bid=N idx=1 mo DIALING`) → INVITE → ALERTING → ACTIVE
  MT: INVITE → IMS 착신 → `[IPCT] [UNSL]< CallIncomingInd {sid=N ...}` → `call + ims bid=N idx=1 mt INCOMING`
      → SDK 호 목록(`[TAPI_CLIENT] x: < GET_CURRENT_CALLS ...`) → TAPI/UI 착신 화면 → ACTIVE
  call domain 은 MakeCall 응답(sid)을 받은 뒤에 호를 만든다. 그 전에 실패한 발신은 `call +` 가 없으니
  telephonyd 가 받은 CALL_DIAL 중 호가 안 생긴 것을 발신 실패로 따로 만든다.
  종료 원인은 imsservice 통지(CallEnded/CallBusy/CallError)로 본다. LAST_CALL_FAIL_CAUSE 는 call domain 이
  직접 답해서 [RILJ] 에 안 찍힌다.
  IMS 등록은 imsservice 통지 `[IPCT] [UNSL]< RegistrationStatus {registered=1 features=0x1 net=1 err=0}` 로 본다
  (telephonytool 의 tapi_ims_is_registered 는 SDK 스텁이다). 부팅 직후 한두 번 찍히고 바뀔 때만 다시 찍힌다.

ofono (~ 2026-09):
  MO: UI/TAPI → ofono(RIL_REQUEST_DIAL) → rild(RIL_CPP DIAL) → RIL-IMSCALL 호 생성 → ALERTING → ACTIVE
  MT: IMS 착신 → RIL-IMSBRIDGE → RIL-IMSCALL 호 생성 → ofono 호 목록 → TAPI/UI 착신 화면 → ACTIVE
  종료는 `GET_CURRENT_CALLS -> 0 call(s)`, 원인은 뒤따르는 `LAST_CALL_FAIL_CAUSE {16}` 이다.

두 세대 모두 세션의 뼈대는 `call + / call ~ / call -` 줄이다. 상태 전이가 가장 깔끔하게 찍힌다.
"""

import json
import os
import re

from core.telephony_constants import CALL_FAIL_REASON_MAP
from parsers.base import BaseParser
from parsers.rtos.line import parse_lines
from parsers.rtos.sip import parse_sip_messages

# ofono 세대는 `call + sid=N`, telephonyd 세대는 `call + ims bid=N` (bid 는 IMS 세션 id)
IMSCALL_NEW_RE = re.compile(r'call \+ (?:\w+ bid|sid)=(-?\d+) idx=(\d+) (mo|mt) (\w+)')
IMSCALL_TRANS_RE = re.compile(r'call ~ (?:\w+ bid|sid)=(-?\d+) idx=(\d+) (\w+) -> (\w+)')
IMSCALL_END_RE = re.compile(r'call - (?:\w+ bid|sid)=(-?\d+)')
CALL_TAGS = {"RIL-IMSCALL", "TCORE_CALL"}

# ---- ofono 세대 ----
CLCC_COUNT_RE = re.compile(r'GET_CURRENT_CALLS -> (\d+) call\(s\)')
CALL_LIST_RE = re.compile(r'\[id=(\d+),([A-Z_]+),toa=\d+,\w+,(mo|mt)\b')
OFONO_RE = re.compile(r'^\[(\d+),(\d+)\]([<>]) RIL_((?:REQUEST|UNSOL)_\w+)\s*(.*)$')
RILD_REQ_RE = re.compile(r'^\[RIL_CPP\] \[(\d+)\]> (\w+)')
BRIDGE_INCOMING_RE = re.compile(r'noti:CallIncomingInd .*?sid=(\d+)')
FAIL_CAUSE_RE = re.compile(r'\{\s*(\d+)')

# ---- telephonyd 세대 (telephony/trace/rilj.cpp, telephony_service/sdk/ts_core.c) ----
# [NNNN] 은 서버 전체에서 하나씩 늘어나는 trace 번호다. who#serial 은 요청한 쪽과 그쪽의 serial.
RILJ_RE = re.compile(r'^\[RILJ(?:-SEM)?\] \[(\d+)\]([<>!]) (\w+) (\S+)#(\d+)\s*(.*)$')
TAPI_SERVER_RE = re.compile(r'^\[TAPI_SERVER\] ([<>!]) (\w+) (\S+)#(\d+)\s*(.*)$')
TAPI_CLIENT_RE = re.compile(r'^\[TAPI_CLIENT\] (\S+): ([<>]) (\w+) serial=(\d+)\s*(.*)$')
# trace 가 0 이면 [----] 로 찍힌다 (짝 없는 요청/응답)
IPCT_RE = re.compile(r'^\[IPCT\] \[(\d+|-+)\]([<>]) (\w+) domain\.call(?:#(\d+))?\s*(.*)$')
IPCT_NOTI_RE = re.compile(r'^\[IPCT\] \[UNSL\]< (\w+)(?: \{(.*)\})?')
ERROR_RE = re.compile(r'\berror: (\w+\((-?\d+)\)|-?\d+)')
ELAPSED_RE = re.compile(r'\s(\d+)ms\b')
STUCK_RE = re.compile(r'STUCK (\d+)ms')
SID_RE = re.compile(r'\bsid=(-?\d+)')
REASON_RE = re.compile(r'\breason=(-?\d+/-?\d+)')
CODE_RE = re.compile(r'\bcode=(-?\d+)')
CLIENT_STATUS_RE = re.compile(r'\bstatus=(-?\d+)')
# call domain 이 호를 만들기 전에 발신을 접는 줄 (domains/call/call_tracker.cpp)
# (10 = RIL_REQUEST_DIAL, 205 = EMERGENCY_DIAL. CALL_DIAL op 도 이 둘로 바뀌어 들어간다)
DIAL_FAIL_RE = re.compile(r'^\[TCORE_CALL\] (?:dial[: ]|request (?:10|205) |entry_new:)')
SEMTEL_TAGS = {"RILJ", "RILJ-SEM", "TAPI_SERVER", "TAPI_CLIENT", "IPCT"}

CALL_REQUEST_RE = re.compile(r'CALL|DIAL|HANGUP|ANSWER|UDUB|DTMF|CONFERENCE|SWITCH|REJECT|HOLD|RESUME', re.I)

# 세션 시작 전에 찍히는 단계는 시작 시각에서 이만큼 앞까지, 시작 후 단계는 이만큼 뒤까지 본다
PRE_WINDOW_SEC = 10.0
POST_WINDOW_SEC = 10.0
FAIL_CAUSE_WINDOW_SEC = 3.0
# call domain 의 요청 타임아웃 (CALL_REQUEST_TIMEOUT_SEC 30) 뒤 실패 줄까지 본다
DIAL_SETUP_WINDOW_SEC = 35.0
MAX_UNANSWERED = 50

OFONO_MO_CHAIN = [
    ("ui_dial", "UI/TAPI 발신 요청"),
    ("ofono_dial", "ofono → RIL DIAL"),
    ("rild_dial", "rild DIAL 수신"),
    ("ril_call", "RIL 호 생성"),
    ("sip_invite", "IMS INVITE 송신"),
    ("alerting", "상대방 호출 중(ALERTING)"),
]
OFONO_MT_CHAIN = [
    ("sip_invite", "망 INVITE 수신"),
    ("ims_incoming", "IMS 착신 수신"),
    ("bridge", "IMS → RIL 전달"),
    ("ril_call", "RIL 호 생성(INCOMING)"),
    ("ofono_clcc", "ofono 호 목록 수신"),
    ("ui_incoming", "TAPI/UI 착신 화면"),
]
SEMTEL_MO_CHAIN = [
    ("ui_dial", "UI/TAPI 발신 요청"),
    ("server_dial", "telephonyd CALL_DIAL 수신"),
    ("ims_dial", "imsservice MakeCall 요청"),
    ("ims_dial_resp", "MakeCall 응답(sid)"),
    ("ril_call", "call domain 호 생성"),
    ("sip_invite", "IMS INVITE 송신"),
    ("alerting", "상대방 호출 중(ALERTING)"),
]
SEMTEL_MT_CHAIN = [
    ("sip_invite", "망 INVITE 수신"),
    ("ims_incoming", "IMS 착신 수신"),
    ("bridge", "imsservice → telephonyd 착신 전달"),
    ("ril_call", "call domain 호 생성(INCOMING)"),
    ("client_clcc", "TAPI 호 목록 수신"),
    ("ui_incoming", "TAPI/UI 착신 화면"),
]
CHAINS = {
    "ofono": {"MO": OFONO_MO_CHAIN, "MT": OFONO_MT_CHAIN},
    "telephonyd": {"MO": SEMTEL_MO_CHAIN, "MT": SEMTEL_MT_CHAIN},
}
PRE_STAGES = {"ui_dial", "ofono_dial", "rild_dial", "server_dial", "ims_dial", "ims_dial_resp",
              "ims_incoming", "bridge"}
# 이 단계 줄에는 sid 가 있어서 같은 콜의 것을 고를 수 있다
SID_STAGES = {"bridge", "ims_dial_resp"}

# imsservice 통지 → telephonyd 가 LAST_CALL_FAIL_CAUSE 로 돌려주는 값 (domains/call/adaptor/ims_adaptor.cpp).
# CallEnded 는 SIP 사유와 상관없이 NORMAL 로 보고한다 (TODO 상태) — 실제 사유는 ims_end 에 남긴다.
IMS_END_CAUSES = {"CallEnded": "16", "CallBusy": "17", "CallError": "65535"}
REG_FIELD_RE = re.compile(r'(\w+)=(\S+)')
MAX_REGISTRATIONS = 50


def parse_ims_registration(msg):
    """`[IPCT] [UNSL]< RegistrationStatus {registered=1 features=0x1 net=1 err=0}` → dict, 아니면 None."""
    noti = IPCT_NOTI_RE.match(msg)
    if not noti or noti.group(1) != "RegistrationStatus":
        return None
    fields = dict(REG_FIELD_RE.findall(noti.group(2) or ""))
    if "registered" not in fields:
        return None
    return {
        "registered": fields["registered"] == "1",
        "features": fields.get("features"),
        "net": fields.get("net"),
        "err": fields.get("err"),
    }


def ended_by(sip_dialog):
    """콜을 먼저 끝낸 쪽. BYE/CANCEL 을 보낸 쪽, 또는 INVITE 에 3xx~6xx 로 답한 쪽이다.

    단말이 보냈으면 local(이 단말), 망에서 왔으면 remote(상대방이나 망). 끝내는 메시지가 없으면 None.
    """
    for m in sip_dialog:
        hangup = m["msg_type"] == "Req" and m["method_code"] in ("BYE", "CANCEL")
        rejected = (m["msg_type"] == "Resp" and (m.get("cseq") or "").endswith("INVITE")
                    and (m.get("status_code") or 0) >= 300)
        if not (hangup or rejected):
            continue
        return {"side": "local" if m["is_outgoing"] else "remote",
                "message": f"{'↑' if m['is_outgoing'] else '↓'} {m['method_code']}",
                "time": m["time"].split(" ")[-1], "line_no": m["line_no"]}
    return None


def _stage_of(rec):
    """세션과 무관하게 줄 하나가 어느 체크포인트인지 판단한다."""
    tag, msg = rec.tag, rec.msg
    if tag == "TAPI_FWK" and "Dialing:" in msg:
        return "ui_dial"
    if tag == "SAMSUNG_CALL" and "DIAL:" in msg:
        return "ui_dial"
    if tag == "TAPI_CLIENT":
        client = TAPI_CLIENT_RE.match(msg)
        if client:
            direction, name, rest = client.group(2), client.group(3), client.group(5)
            if direction == ">" and name == "CALL_DIAL":
                return "ui_dial"
            if direction == "<" and name == "GET_CURRENT_CALLS" and _client_ok(rest):
                return "client_clcc"
        return None
    if tag == "TAPI_SERVER":
        server = TAPI_SERVER_RE.match(msg)
        if server and server.group(1) == ">" and server.group(2) == "CALL_DIAL":
            return "server_dial"
        return None
    if tag == "IPCT":
        ipct = IPCT_RE.match(msg)
        if ipct and ipct.group(3) == "MakeCall":
            if ipct.group(2) == ">":
                return "ims_dial"
            if not ERROR_RE.search(ipct.group(5)):
                return "ims_dial_resp"
            return None
        noti = IPCT_NOTI_RE.match(msg)
        if noti and noti.group(1) == "CallIncomingInd":
            return "bridge"
        return None
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


def _client_ok(rest):
    status = CLIENT_STATUS_RE.search(rest)
    return status is None or status.group(1) == "0"


def detect_stack(records):
    """telephonyd 세대 줄이 하나라도 있으면 telephonyd, 아니면 ofono."""
    for rec in records:
        tag = rec.tag or ""
        if tag in SEMTEL_TAGS or tag.startswith("TCORE_"):
            return "telephonyd"
    return "ofono"


def _semtel_request(msg):
    """telephonyd 세대 요청/응답 줄이면 (key, layer, 방향, 이름, token, client, 나머지)."""
    m = RILJ_RE.match(msg)
    if m:
        trace, direction, name, who, serial, rest = m.groups()
        return ("RILJ", trace), "telephonyd → secril", direction, name, trace, f"{who}#{serial}", rest
    m = TAPI_SERVER_RE.match(msg)
    if m:
        direction, name, who, serial, rest = m.groups()
        token = f"{who}#{serial}"
        return ("TAPI", who, serial), "TAPI → telephonyd", direction, name, token, token, rest
    m = IPCT_RE.match(msg)
    if m and m.group(1).isdigit():
        trace, direction, method, tok, rest = m.groups()
        return ("IPCT", trace), "telephonyd → imsservice", direction, method, trace, f"domain.call#{tok}", rest
    return None


def _strip_tail(rest):
    """응답 줄의 `error: X(n)` 과 끝의 `NNNms` 를 떼고 남은 요약."""
    rest = ERROR_RE.sub("", rest)
    rest = re.sub(r'\s*\d+ms\s*$', "", rest)
    return rest.strip() or None


def _public_sip(message):
    return {k: v for k, v in message.items() if k != "time_sec"}


def _evidence(rec):
    return {"time": rec.time, "line_no": rec.line_no, "text": rec.msg}


class RtosCallFlowParser(BaseParser):
    def analyze(self, lines):
        records = parse_lines(lines)
        stack = detect_stack(records)
        markers = {}
        requests = {}
        request_order = []
        fail_causes = []
        ims_ends = []
        dial_failures = []
        registrations = []
        calls = []
        active = []

        for rec in records:
            stage = _stage_of(rec)
            if stage:
                markers.setdefault(stage, []).append(rec)

            ofono = OFONO_RE.match(rec.msg)
            if ofono:
                self._track_request(rec, ofono, requests, request_order, fail_causes)
            semtel = _semtel_request(rec.msg) if rec.tag in SEMTEL_TAGS else None
            if semtel:
                self._track_semtel_request(rec, semtel, requests, request_order)
                if semtel[3] == "CALL_DIAL" and semtel[2] == "<" and ERROR_RE.search(semtel[6]):
                    dial_failures.append(rec)
            if rec.tag == "IPCT":
                noti = IPCT_NOTI_RE.match(rec.msg)
                if noti and noti.group(1) in IMS_END_CAUSES:
                    ims_ends.append((rec, noti.group(1), noti.group(2) or ""))
                reg = parse_ims_registration(rec.msg)
                if reg:
                    registrations.append((rec, reg))
            if DIAL_FAIL_RE.match(rec.msg):
                dial_failures.append(rec)

            if rec.tag in CALL_TAGS:
                self._track_imscall(rec, calls, active)
            elif rec.tag == "RIL_CPP":
                self._track_call_list(rec, active)

        sip_messages = parse_sip_messages(records)
        chains = CHAINS[stack]
        for call in calls:
            self._attach_checkpoints(call, markers, chains[call["direction"]], PRE_STAGES)
        if stack == "telephonyd":
            calls = self._add_failed_dials(calls, markers, dial_failures)
        for call in calls:
            self._attach_sip(call, sip_messages, stack)
            self._attach_requests(call, request_order)
        self._attach_fail_causes(calls, fail_causes)
        self._attach_ims_ends(calls, ims_ends)
        for call in calls:
            self._attach_registration(call, registrations)
            self._finalize(call, chains[call["direction"]])

        unanswered = [r for r in request_order if r["resp_time"] is None]
        return {
            "stack": stack,
            "kpi": {
                "call_count": len(calls),
                "mo_count": sum(1 for c in calls if c["direction"] == "MO"),
                "mt_count": sum(1 for c in calls if c["direction"] == "MT"),
                "connected_count": sum(1 for c in calls if c["connected"]),
                "broken_count": sum(1 for c in calls if c["broken_at"]),
                "dial_failed_count": sum(1 for c in calls if c.get("dial_failed")),
                "unanswered_request_count": len(unanswered),
            },
            "calls": [self._public(c) for c in calls],
            "unanswered_requests": [self._public_request(r) for r in unanswered[:MAX_UNANSWERED]],
            "ims_registrations": [{**reg, **_evidence(rec)} for rec, reg in registrations[-MAX_REGISTRATIONS:]],
            # 콜에 묶이지 않은 것(REGISTER, SUBSCRIBE)까지 전부. 기존 SIP 흐름 차트가 이걸 그린다.
            "sip_messages": [_public_sip(m) for m in sip_messages],
        }

    def save_ui_report(self, output_dir="./result", base_name="", analysis=None):
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{base_name}_rtos_call_flow.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(analysis or {}, f, indent=4, ensure_ascii=False)
        return out_path

    # ---- 수집 ----

    @staticmethod
    def _new_request(rec, layer, name, token, slot=None, client=None):
        return {
            "layer": layer, "slot": slot, "token": token, "client": client, "name": name,
            "req_time": rec.time, "req_sec": rec.sec, "req_line": rec.line_no,
            "resp_time": None, "latency_ms": None, "response": None, "error": None, "stuck_ms": None,
        }

    def _track_request(self, rec, m, requests, request_order, fail_causes):
        slot, token, direction, name, payload = m.groups()
        name = name.replace("REQUEST_", "", 1)
        key = (slot, token)
        if direction == ">":
            req = self._new_request(rec, "ofono → rild", name, token, slot=int(slot))
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

    def _track_semtel_request(self, rec, parsed, requests, request_order):
        key, layer, direction, name, token, client, rest = parsed
        if direction == ">":
            req = self._new_request(rec, layer, name, token, client=client)
            requests[key] = req
            request_order.append(req)
            return
        req = requests.get(key)
        if not req:
            return
        if direction == "!":
            stuck = STUCK_RE.search(rest)
            if stuck:
                req["stuck_ms"] = int(stuck.group(1))
            elif "unsupported" in rest:
                req["error"] = "REQUEST_NOT_SUPPORTED"  # 응답 줄이 뒤따른다
            elif "refused" in rest:
                # marshal/router 가 domain 에 넘기기 전에 거절했다. 이 요청의 응답 줄은 따로 없다
                requests.pop(key, None)
                req["resp_time"] = rec.time
                req["latency_ms"] = round((rec.sec - req["req_sec"]) * 1000, 1)
                req["error"] = rest.split("refused:", 1)[-1].strip() or "refused"
            return
        requests.pop(key, None)
        req["resp_time"] = rec.time
        elapsed = ELAPSED_RE.findall(rest)
        req["latency_ms"] = float(elapsed[-1]) if elapsed else round((rec.sec - req["req_sec"]) * 1000, 1)
        error = ERROR_RE.search(rest)
        if error:
            req["error"] = error.group(1)
        req["response"] = _strip_tail(rest)

    def _track_imscall(self, rec, calls, active):
        new = IMSCALL_NEW_RE.search(rec.msg)
        if new:
            sid, idx, direction, state = new.groups()
            call = {
                "sid": sid, "idx": int(idx), "direction": direction.upper(),
                "start_time": rec.time, "start_sec": rec.sec,
                "end_time": None, "end_sec": None,
                "states": [{"time": rec.time, "state": state, "line_no": rec.line_no}],
                "checkpoints": {"ril_call": _evidence(rec)}, "_cp_sec": {"ril_call": rec.sec},
                "fail_cause": None, "fail_reason": None, "ims_end": None, "ril_requests": [],
                "dial_failed": False, "dial_error": None,
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

    def _attach_checkpoints(self, call, markers, chain, pre_stages):
        start = call["start_sec"]
        post_end = call["end_sec"] if call["end_sec"] is not None else start + POST_WINDOW_SEC
        for stage, _ in chain:
            if stage in call["checkpoints"]:
                continue
            candidates = markers.get(stage, [])
            if stage in pre_stages:
                # 시작 직전 것 중 가장 가까운 것
                picked = [r for r in candidates if start - PRE_WINDOW_SEC <= r.sec <= start]
                if stage in SID_STAGES and call["sid"] is not None:
                    picked = [r for r in picked if f"sid={call['sid']}" in r.msg] or picked
                rec = picked[-1] if picked else None
            else:
                rec = next((r for r in candidates if start < r.sec <= post_end), None)
            if rec:
                call["checkpoints"][stage] = _evidence(rec)
                call["_cp_sec"][stage] = rec.sec

    def _add_failed_dials(self, calls, markers, dial_failures):
        """telephonyd 가 CALL_DIAL 을 받았는데 call domain 이 호를 만들지 못한 발신을 콜로 만든다."""
        claimed = {c["checkpoints"].get("server_dial", {}).get("line_no") for c in calls} | \
                  {c["checkpoints"].get("ui_dial", {}).get("line_no") for c in calls}
        dials = markers.get("server_dial") or [r for r in markers.get("ui_dial", []) if r.tag == "TAPI_CLIENT"]
        anchor = "server_dial" if markers.get("server_dial") else "ui_dial"
        failed = []
        for dial in dials:
            if dial.line_no in claimed:
                continue
            nxt = next((d.sec for d in dials if d.sec > dial.sec), float("inf"))
            hi = min(dial.sec + DIAL_SETUP_WINDOW_SEC, nxt)
            error = next((r for r in dial_failures if dial.sec <= r.sec <= hi), None)
            call = {
                "sid": None, "idx": None, "direction": "MO",
                "start_time": dial.time, "start_sec": dial.sec,
                "end_time": error.time if error else None, "end_sec": error.sec if error else None,
                "states": [],
                "checkpoints": {anchor: _evidence(dial)}, "_cp_sec": {anchor: dial.sec},
                "fail_cause": None, "fail_reason": None, "ims_end": None, "ril_requests": [],
                "dial_failed": True, "dial_error": _evidence(error) if error else None,
            }
            # 실패한 발신은 MakeCall 같은 단계가 시작 뒤에 온다. UI 줄만 시작 앞에서 찾는다
            self._attach_checkpoints(call, markers, SEMTEL_MO_CHAIN, {"ui_dial"} - {anchor})
            failed.append(call)
        return sorted(calls + failed, key=lambda c: c["start_sec"])

    def _attach_sip(self, call, sip_messages, stack):
        """콜의 INVITE 를 찾아 그 Call-ID 의 메시지를 모두 붙인다.

        MO 는 발신 요청 뒤에 INVITE 를 보내고, MT 는 INVITE 를 받은 뒤에 호가 생긴다.
        telephonyd 세대는 MakeCall 응답(sid)이 와야 호가 생겨서 INVITE 가 `call +` 보다 앞설 수 있다
        → MakeCall 요청 시각부터 본다.
        """
        start = call["start_sec"]
        outgoing = call["direction"] == "MO"
        if outgoing:
            lo = call["_cp_sec"].get("ims_dial", start) if stack == "telephonyd" else start
            hi = start + POST_WINDOW_SEC
        else:
            lo, hi = start - PRE_WINDOW_SEC, start
        invite = next((
            m for m in sip_messages
            if m["method_code"] == "INVITE" and m["is_outgoing"] == outgoing and lo <= m["time_sec"] <= hi
        ), None)
        call["sip_call_id"] = None
        call["sip_messages"] = []
        call["sip_final_response"] = None
        call["sip_error"] = None
        call["ended_by"] = None
        if not invite:
            return
        call["sip_call_id"] = invite["call_id"]
        call["checkpoints"]["sip_invite"] = {
            "time": invite["time"].split(" ")[-1], "line_no": invite["line_no"], "text": invite["raw_log"],
        }
        dialog = [m for m in sip_messages if m["call_id"] == invite["call_id"]]
        call["sip_messages"] = [_public_sip(m) for m in dialog]
        answers = [m for m in dialog if m["msg_type"] == "Resp" and m["cseq"].endswith("INVITE")
                   and (m["status_code"] or 0) >= 200]
        if answers:
            call["sip_final_response"] = answers[-1]["method_code"]
        errors = [m for m in dialog if m["is_error"]]
        if errors:
            call["sip_error"] = errors[0]["method_code"]
        call["ended_by"] = ended_by(dialog)

    def _attach_requests(self, call, request_order):
        lo = call["start_sec"] - PRE_WINDOW_SEC
        hi = call["end_sec"] + FAIL_CAUSE_WINDOW_SEC if call["end_sec"] is not None else float("inf")
        call["ril_requests"] = [
            self._public_request(r)
            for r in request_order
            if lo <= r["req_sec"] <= hi and CALL_REQUEST_RE.search(r["name"])
        ]

    def _attach_fail_causes(self, calls, fail_causes):
        for rec, cause in fail_causes:
            ended = [c for c in calls if c["end_sec"] is not None
                     and 0 <= rec.sec - c["end_sec"] <= FAIL_CAUSE_WINDOW_SEC
                     and c["fail_cause"] is None]
            if ended:
                self._set_fail_cause(ended[-1], cause)

    def _attach_ims_ends(self, calls, ims_ends):
        for rec, kind, fields in ims_ends:
            sid = SID_RE.search(fields)
            call = next((c for c in calls if sid and c["sid"] == sid.group(1) and c["ims_end"] is None), None)
            if not call:
                continue
            reason, code = REASON_RE.search(fields), CODE_RE.search(fields)
            call["ims_end"] = {
                "kind": kind, "reason": reason.group(1) if reason else None,
                "error_code": code.group(1) if code else None, **_evidence(rec),
            }
            if call["fail_cause"] is None:
                self._set_fail_cause(call, IMS_END_CAUSES[kind])

    @staticmethod
    def _attach_registration(call, registrations):
        """발신/착신이 시작되기 전 마지막 IMS 등록 상태. 콜 앞에 통지가 없으면 None 이다."""
        begin = min([call["start_sec"], *call["_cp_sec"].values()])
        before = [(rec, reg) for rec, reg in registrations if rec.sec <= begin]
        call["ims_registration"] = {**before[-1][1], **_evidence(before[-1][0])} if before else None

    @staticmethod
    def _set_fail_cause(call, cause):
        call["fail_cause"] = cause
        call["fail_reason"] = CALL_FAIL_REASON_MAP.get(cause, f"UNKNOWN ({cause})")

    def _finalize(self, call, chain):
        call["connected"] = "active" in call["checkpoints"]
        call["broken_at"] = None
        if not call["connected"]:
            missing = next(((s, label) for s, label in chain if s not in call["checkpoints"]), None)
            if missing:
                call["broken_at"] = {"stage": missing[0], "label": missing[1]}
        ended = call["end_time"] is not None
        if call["dial_failed"]:
            call["status"] = "발신 실패 (호 생성 전)" if call["dial_error"] else "호 생성 전 로그 끝남"
        elif call["connected"]:
            call["status"] = "통화 후 종료" if ended else "통화 중 로그 끝남"
        else:
            call["status"] = "연결 전 종료" if ended else "연결 전 로그 끝남"
        call["chain"] = [
            {"stage": s, "label": label, "reached": s in call["checkpoints"],
             "time": call["checkpoints"].get(s, {}).get("time")}
            for s, label in chain + [("active", "통화 연결(ACTIVE)")]
        ]

    @staticmethod
    def _public_request(req):
        return {k: v for k, v in req.items() if k != "req_sec"}

    def _public(self, call):
        return {k: v for k, v in call.items() if k not in ("start_sec", "end_sec", "_cp_sec")}
