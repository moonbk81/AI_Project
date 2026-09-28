"""RTOS IMS-FW 가 찍는 SIP 메시지를 한 통씩 묶는다.

IMS 스택(`sec/apps/ims/.../stackif.cpp`)은 메시지 첫 줄에만 방향을 붙이고
나머지 헤더와 SDP 는 한 줄씩 따로 찍는다:
  [W][IMS-FW] [SIP]> INVITE sip:... SIP/2.0
  [W][IMS-FW] Via: ...
  ...
  [W][IMS-FW] Content-Length: 690
  [W][IMS-FW]                      <- 헤더 끝
  [W][IMS-FW] v=0 ...               <- Content-Length 만큼 SDP
`>` 는 BaseManager::LoggingSipMsgToNW(단말 → 망), `<` 는
TransactionUserHandler::LoggingSipMsgFromNW(망 → 단말)다. enum 이름이
SIP_REQUEST/SIP_RESPONSE 라 요청/응답처럼 보이지만 실제로는 송수신 방향이다.

사이사이 다른 task 줄이 끼어들므로 같은 task 의 [W][IMS-FW] 줄만 이어 붙인다.
출력은 Android ImsSipProcessor 와 같은 모양이라 기존 SIP 흐름 차트를 그대로 쓴다.
"""

import re

from parsers.rtos.line import split_level_tag

SIP_START_RE = re.compile(r'^\[SIP\]([<>]) (.*)$')
REQUEST_LINE_RE = re.compile(r'^([A-Z]+) (\S+) SIP/2\.0')
STATUS_LINE_RE = re.compile(r'^SIP/2\.0 (\d{3}) ?(.*)$')
HEADER_RE = re.compile(r'^([A-Za-z][\w\-.]*):\s*(.*)$')
NESTED_TIME_RE = re.compile(r'^\d{2}-\d{2} (\d{2}:\d{2}:\d{2}\.\d+)')
RTPMAP_RE = re.compile(r'^a=rtpmap:\d+ ([^/\s]+/\d+)')

# 한 메시지의 줄은 수 ms 간격으로 찍힌다. 이보다 벌어지면 끝난 것으로 본다.
MAX_LINE_GAP_SEC = 2.0
# 원인 분석에 쓰는 헤더만 남긴다. Via/Route 같은 경로 헤더까지 넣으면 문서가 헤더 덤프가 된다.
KEY_HEADERS = (
    "Reason", "Warning", "Retry-After", "P-Early-Media", "Require",
    "Session-Expires", "User-Agent", "Server", "P-Access-Network-Info",
)
AUTH_CHALLENGE_CODES = (401, 407)
SIP_STATUS_TEXT = {
    100: "Trying", 180: "Ringing", 181: "Call Is Being Forwarded", 183: "Session Progress",
    200: "OK", 202: "Accepted", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
    404: "Not Found", 408: "Request Timeout", 480: "Temporarily Unavailable",
    486: "Busy Here", 487: "Request Terminated", 488: "Not Acceptable Here",
    500: "Server Internal Error", 503: "Service Unavailable", 504: "Server Time-out",
    603: "Decline",
}


def _nested(rec):
    """IMS-FW 줄이면 (본문, IMS 쪽 시각, 레벨), 아니면 None."""
    if rec.tag != "IMS-FW":
        return None
    leveled = split_level_tag(rec.msg)
    if not leveled:
        return None
    t = NESTED_TIME_RE.match(rec.msg)
    return leveled[2], (t.group(1) if t else None), leveled[0]


def _open(rec, direction, first_line, ims_time, level):
    return {
        "rec": rec, "direction": direction, "first_line": first_line.strip(), "level": level,
        "ims_time": ims_time, "headers": [], "body": [], "in_body": False,
        "content_length": None, "consumed": 0, "last_sec": rec.sec,
    }


def _finish(state):
    rec = state["rec"]
    first = state["first_line"]
    headers = {}
    for name, value in state["headers"]:
        headers.setdefault(name.lower(), value)

    outgoing = state["direction"] == ">"
    req = REQUEST_LINE_RE.match(first)
    status = STATUS_LINE_RE.match(first)
    code = int(status.group(1)) if status else None
    if req:
        method_code, msg_type = req.group(1), "Req"
    elif status:
        reason = status.group(2).strip() or SIP_STATUS_TEXT.get(code, "")
        method_code, msg_type = f"{code} {reason}".strip(), "Resp"
    else:
        method_code, msg_type = first.split(" ")[0], "Unknown"

    codecs = []
    for line in state["body"]:
        m = RTPMAP_RE.match(line.strip())
        if m and m.group(1) not in codecs:
            codecs.append(m.group(1))

    time_text = f"{rec.date} {rec.time}"
    call_id = headers.get("call-id", "Unknown")
    cseq = headers.get("cseq", "")
    direction = "Tx ⬆️" if outgoing else "Rx ⬇️"
    return {
        "time": time_text,
        "time_sec": rec.sec,
        "ims_time": state["ims_time"],
        "line_no": rec.line_no,
        "log_type": "IMS_SIP_Message",
        "direction": direction,
        "is_outgoing": outgoing,
        "msg_type": msg_type,
        "method_code": method_code,
        "status_code": code,
        "request_uri": req.group(2) if req else None,
        "call_id": call_id,
        "cseq": cseq,
        "from": headers.get("from"),
        "to": headers.get("to"),
        # 401/407 은 인증 요구(AKA 챌린지)다. 곧바로 인증을 실어 다시 보내는 정상 절차라 오류가 아니다.
        "is_error": bool(code and code >= 400 and code not in AUTH_CHALLENGE_CODES),
        "is_auth_challenge": code in AUTH_CHALLENGE_CODES,
        "codecs": codecs,
        "key_headers": {
            name: headers[name.lower()] for name in KEY_HEADERS if name.lower() in headers
        },
        "header_count": len(state["headers"]),
        "document": f"[{time_text}] {direction} {method_code} (CSeq: {cseq}, Call-ID: {call_id})",
        "raw_log": first[:300],
    }


def parse_sip_messages(records):
    """정렬된 RtosLine 목록에서 SIP 메시지를 시간순으로 뽑는다."""
    open_by_task = {}
    messages = []

    def close(task):
        state = open_by_task.pop(task, None)
        if state:
            messages.append(_finish(state))

    for rec in records:
        state = open_by_task.get(rec.task)
        if state and rec.sec - state["last_sec"] > MAX_LINE_GAP_SEC:
            close(rec.task)
            state = None

        nested = _nested(rec)
        if nested is None:
            if state:
                close(rec.task)
            continue
        body, ims_time, level = nested

        start = SIP_START_RE.match(body)
        if start:
            close(rec.task)
            open_by_task[rec.task] = _open(rec, start.group(1), start.group(2), ims_time, level)
            continue
        if not state:
            continue
        if level != state["level"]:
            # 메시지 줄은 첫 줄과 같은 레벨로 찍힌다. 같은 task 의
            # [D][IMS-FW] Registrant::Notify 같은 줄이 오면 메시지는 끝났다.
            close(rec.task)
            continue

        state["last_sec"] = rec.sec
        if not state["in_body"]:
            if not body.strip():
                state["in_body"] = True
                if not state["content_length"]:
                    close(rec.task)
                continue
            if body[:1] in (" ", "\t") and state["headers"]:
                # 접힌 헤더 (WWW-Authenticate 의 nonce=, algorithm= 줄)
                name, value = state["headers"][-1]
                state["headers"][-1] = (name, f"{value} {body.strip()}")
                continue
            header = HEADER_RE.match(body.strip())
            if header:
                name, value = header.group(1), header.group(2).strip()
                state["headers"].append((name, value))
                if name.lower() == "content-length" and value.isdigit():
                    state["content_length"] = int(value)
            continue

        state["body"].append(body)
        state["consumed"] += len(body.strip()) + 2  # 줄마다 CRLF
        if state["consumed"] >= state["content_length"]:
            close(rec.task)

    for task in list(open_by_task):
        close(task)
    messages.sort(key=lambda m: (m["time_sec"], m["line_no"]))
    return messages
