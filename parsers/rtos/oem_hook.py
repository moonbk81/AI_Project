"""RIL_REQUEST_OEM_HOOK_RAW 한 건이 cpif 를 거쳐 모뎀까지 갔다가 응답이 돌아왔는지 따라간다.

요청 한 건이 지나가는 계층과 로그 (secril 소스 기준):
  ofono   `[0,0096]> RIL_REQUEST_OEM_HOOK_RAW`                    ← token
  rild    `[RIL_CPP] [0096]> OEM_HOOK_RAW (raw_size=21)`          ← 같은 token
  secril  `[RILD] SecRilProxy::OnRequest(59) RIL_REQUEST_OEM_HOOK_RAW`   ← 여기부터 token 없음
          `[RILD] GetMessage : oem func id = 0x0F`                ← payload 첫 바이트
  raw IPC `[RILD] DoOemRawIpc: Calling SendRawIpc funcId=0x0F len=9`
          `[RILD] [B] TX: (M)IMS_CMD (S)IMS_ENGINE (T)SET l:9 m:46 a:00 [ 01 00 ]`   ← cpif 쓰기
          `[RILD] DoOemRawIpc: SendRawIpc success (ret=0)`
  완료    `[0,0096]< RIL_REQUEST_OEM_HOOK_RAW 1`  (raw IPC 는 쓰기 성공 즉시 완료된다)
  모뎀    `[RILD] [B] RX: (M)GEN_CMD (S)GEN_PHONE_RES (T)RESP l:c m:43 a:46 [ 16 09 03 00 80 ]`
  전달    `[0,UNSOL]< UNSOL_OEM_HOOK_RAW 12` + `oem_hook_raw_ind: len=12 data=.. [0C 00 43 46 80 ...]`

이어 붙이는 열쇠:
- ofono ↔ rild 는 token, rild → secril → funcId 는 순서(FIFO). secril 은 요청을 한 줄로 처리한다.
- TX ↔ RX 는 모뎀이 TX 의 msg_seq(m:) 를 RX 의 ack_seq(a:) 로 돌려주는 것으로 잇는다.
  secril 은 raw IPC 응답을 seq 로 확인하지 않으니 이 매칭은 우리가 하는 것이다.
- General Response payload 는 `recv_main recv_sub recv_type err_lo err_hi`, 0x8000 이 성공이다.
- ofono 의 oem_hook_raw_ind hex 는 RX 프레임 그대로라 바이트 2,3 이 RX 의 m:, a: 다.
UICC(0x15)·FACTORY(0x12)·MISC(0x11) 같은 funcId 는 secril 매니저가 안에서 처리하고 cpif 로 가지 않는다.
"""

import json
import os
import re

from parsers.base import BaseParser
from parsers.rtos.line import parse_lines

OFONO_REQ_RE = re.compile(r'^\[(\d+),(\d+)\]> RIL_REQUEST_OEM_HOOK_RAW\b')
OFONO_RESP_RE = re.compile(r'^\[(\d+),(\d+)\]< RIL_REQUEST_OEM_HOOK_RAW(?: (failed) (\w+)| (\d+))?')
OFONO_IND_RE = re.compile(r'oem_hook_raw_ind: len=(\d+) data=\S+ \[([0-9A-Fa-f ]*)\]')
RILD_REQ_RE = re.compile(r'^\[RIL_CPP\] \[(\d+)\]> OEM_HOOK_RAW(?: \(raw_size=(\d+)\))?')
RILD_DONE_RE = re.compile(r'^\[RIL_CPP\] RequestComplete OEM_HOOK_RAW\b')
# rild 는 응답을 보냈는데 ofono 가 parcel 을 못 읽으면 `<` 줄 없이 이것만 남는다
OFONO_PARCEL_ERR_RE = re.compile(r'ril_oem_request_raw_cb: malformed parcel')
SECRIL_REQ_RE = re.compile(r'SecRilProxy::OnRequest\(59\)')
FUNC_ID_RE = re.compile(r'GetMessage : oem func id = 0x([0-9A-Fa-f]+)')
SEND_RAW_RE = re.compile(r'Calling SendRawIpc funcId=0x([0-9A-Fa-f]+) len=(\d+)')
SEND_OK_RE = re.compile(r'SendRawIpc success')
SEND_FAIL_RE = re.compile(r'SendRawIpc failed \(ret=(-?\d+)\)|cmd not permitted as raw ipc|can\'t send IPC to non-IPC modem')
IPC_DUMP_RE = re.compile(
    r'\[(\w)\] (TX|RX): \(M\)(\S+) \(S\)(\S+) \(T\)(\S+) l:([0-9a-fA-F]+) m:([0-9a-fA-F]{2}) a:([0-9a-fA-F]{2}) \[ ?(.*)'
)
HEX_BYTE_RE = re.compile(r'\b[0-9A-Fa-f]{2}\b')

# secril/include/oemfunctions.h OemHookRawFuncID
OEM_FUNC_NAMES = {
    0x01: "SVC_MODE", 0x02: "NETWORK", 0x03: "SS", 0x04: "PERSONALIZATION", 0x05: "POWER",
    0x06: "IMEI", 0x07: "SYSDUMP", 0x08: "SOUND", 0x09: "GPRS", 0x0A: "OMADM", 0x0B: "CALL",
    0x0C: "CONFIGURATION", 0x0D: "DATA", 0x0E: "GPS", 0x0F: "CP_IMS", 0x10: "PHONE", 0x11: "MISC",
    0x12: "FACTORY", 0x13: "RFS", 0x14: "SAP", 0x15: "UICC", 0x16: "DOMESTIC", 0x18: "DISP",
    0x20: "SMARTAS", 0x22: "IMS", 0x23: "JPN", 0x24: "MMS", 0x27: "TAS", 0x55: "MCPTT",
}
# OemHookManager::GetMessage 가 EVENT_OEM_RAW_IPC 로 보내는 funcId — 이것만 cpif 로 IPC 를 쓴다
RAW_IPC_FUNCS = {0x0E, 0x0F, 0x20, 0x27, 0x55}

# TX 타입별로 모뎀이 돌려주는 응답 (2026-09-29 사용자 확인):
#   SET·EXEC → GEN_CMD/GEN_PHONE_RES(General Response), GET → 같은 MAIN/SUB 의 RESP,
#   EVENT(PDA 상태 통지)·CFRM(INDI 에 대한 확인) → 응답 없음.
# GET 을 모뎀이 거절할 때는 RESP 대신 에러 GR 이 온다 (ENGINE_CAPABILITY GET → GR 0x8001).
NO_RESPONSE_TX_TYPES = {"EVENT", "CFRM"}
GR_ONLY_TX_TYPES = {"SET", "EXEC"}
RESP_TX_TYPES = {"GET"}

# secril/modem/ipc/ipc41/ipcv41.h ipc_main_cmd_type
IPC_MAIN_NAMES = {
    0x01: "PWR_CMD", 0x02: "CALL_CMD", 0x03: "CDMA_DATA_CMD", 0x04: "SMS_CMD", 0x05: "SEC_CMD",
    0x06: "PB_CMD", 0x07: "DISP_CMD", 0x08: "NET_CMD", 0x09: "SND_CMD", 0x0A: "MISC_CMD",
    0x0B: "SVC_CMD", 0x0C: "SS_CMD", 0x0D: "GPRS_CMD", 0x0E: "SAT_CMD", 0x0F: "CFG_CMD",
    0x10: "IMEI_CMD", 0x11: "GPS_CMD", 0x12: "SAP_CMD", 0x13: "FACTORY_CMD", 0x14: "OMADM_CMD",
    0x15: "RFS_CMD", 0x16: "IMS_CMD", 0x17: "EMBMS_CMD", 0x18: "MMS_PROVISION_CMD",
    0x19: "RFS_EXT_CMD", 0x1F: "MODEMTEST_CMD", 0x20: "DOMESTIC_CMD", 0x21: "PCSC_CMD",
    0x22: "QMI_HIDDENMENU_CMD", 0x23: "SMARTAS_CMD", 0x24: "PROSE_CMD", 0x25: "MCPTT_CMD",
    0x26: "OES_CMD", 0x27: "SYSTEM_CMD", 0x28: "CPAI_CMD", 0x29: "NTN_CMD", 0x30: "JPN_CMD",
    0x40: "QMIIMS_CMD", 0x80: "GEN_CMD",
}
IPC_TX_TYPES = {0x01: "EXEC", 0x02: "GET", 0x03: "SET", 0x04: "CFRM", 0x05: "EVENT"}
IPC_RX_TYPES = {0x01: "INDI", 0x02: "RESP", 0x03: "NOTI"}
GEN_ERR_NONE = 0x8000
GEN_ERR_NAMES = {
    0x8000: "NONE", 0x8001: "INVALID_IPC", 0x8002: "PHONE_OFFLINE", 0x8003: "CMD_NOT_ALLOWED",
    0x8004: "PHONE_IS_INUSE", 0x8005: "INVALID_STATE", 0x8006: "NO_BUFFER", 0x8007: "OPER_REJ",
    0x8008: "INSUFFICIENT_RESOURCE", 0x8009: "NET_NOT_RESPOND", 0x800A: "SIM_PIN_ENABLE_REQ",
    0x800B: "SIM_PERM_BLOCKED", 0x800C: "SIM_PHONEBOOK_RESTRICTED", 0x800D: "FIXED_DIALING_NUMBER_ONLY",
    0x800E: "SIM_PIN2_PERM_BLOCKED", 0x800F: "NO_SUBSCRIPTION",
}

# 모뎀 응답은 보통 수백 ms 안에 온다. 이보다 늦으면 다른 요청의 응답과 섞일 수 있다
MODEM_RESPONSE_WINDOW_SEC = 10.0
TX_AFTER_SEND_SEC = 1.0
MAX_UNMATCHED_IND = 50

RAW_CHAIN = [
    ("ofono_req", "ofono 요청"),
    ("rild", "rild 수신"),
    ("secril", "secril 수신"),
    ("func", "funcId 분기"),
    ("cpif_tx", "cpif 송신(TX)"),
    ("rild_done", "rild 응답 송신"),
    ("complete", "ofono 요청 완료"),
    ("modem_rx", "모뎀 응답(RX)"),
    ("unsol", "ofono 응답 전달(UNSOL)"),
]
LOCAL_CHAIN = [
    ("ofono_req", "ofono 요청"),
    ("rild", "rild 수신"),
    ("secril", "secril 수신"),
    ("func", "funcId 분기"),
    ("rild_done", "rild 응답 송신"),
    ("complete", "ofono 요청 완료"),
]
MODEM_STAGES = {"modem_rx", "unsol"}


def _evidence(rec):
    return {"time": rec.time, "line_no": rec.line_no, "text": rec.msg}


def _hex_bytes(text):
    return [int(b, 16) for b in HEX_BYTE_RE.findall(text or "")]


def gen_error_label(code):
    if code is None:
        return None
    return f"0x{code:04X} {GEN_ERR_NAMES.get(code, 'UNKNOWN')}"


def _parse_dump(rec):
    m = IPC_DUMP_RE.search(rec.msg)
    if not m:
        return None
    modem, direction, main, sub, cmd_type, length, seq, ack, rest = m.groups()
    payload = rest.split("]")[0]
    dump = {
        "modem": modem, "direction": direction, "main": main, "sub": sub, "type": cmd_type,
        "len": int(length, 16), "seq": int(seq, 16), "ack": int(ack, 16),
        "time": rec.time, "sec": rec.sec, "line_no": rec.line_no, "text": rec.msg,
        "gen": None,
    }
    if direction == "RX" and main == "GEN_CMD":
        data = _hex_bytes(payload)
        if len(data) >= 5:
            dump["gen"] = {
                "main": IPC_MAIN_NAMES.get(data[0], f"0x{data[0]:02X}"),
                "sub": f"0x{data[1]:02X}",
                "type": IPC_TX_TYPES.get(data[2], f"0x{data[2]:02X}"),
                "error": data[3] | (data[4] << 8),
            }
    return dump


def _expected_response(tx):
    if not tx:
        return None
    if tx["type"] in NO_RESPONSE_TX_TYPES:
        return "응답 없음"
    if tx["type"] in GR_ONLY_TX_TYPES:
        return "GEN_CMD / GEN_PHONE_RES"
    if tx["type"] in RESP_TX_TYPES:
        return f"{tx['main']} / {tx['sub']} / RESP"
    return None


def _decode_ind(rec, m):
    """oem_hook_raw_ind 의 hex 는 IPC 프레임이다: len(2) seq ack main sub type ..."""
    data = _hex_bytes(m.group(2))
    ind = {"time": rec.time, "sec": rec.sec, "line_no": rec.line_no, "text": rec.msg,
           "len": int(m.group(1)), "seq": None, "ack": None, "main": None, "sub": None, "type": None,
           "gen_error": None, "matched": False}
    if len(data) >= 7:
        ind.update(seq=data[2], ack=data[3],
                   main=IPC_MAIN_NAMES.get(data[4], f"0x{data[4]:02X}"),
                   sub=f"0x{data[5]:02X}", type=IPC_RX_TYPES.get(data[6], f"0x{data[6]:02X}"))
        if data[4] == 0x80 and len(data) >= 12:
            ind["gen_error"] = data[10] | (data[11] << 8)
    return ind


class RtosOemHookParser(BaseParser):
    def analyze(self, lines):
        records = parse_lines(lines)
        by_token = {}
        requests = []
        rx_dumps = []
        inds = []
        last_tx_task = None
        last_sec = records[-1].sec if records else 0.0

        for rec in records:
            msg = rec.msg
            m = OFONO_REQ_RE.match(msg)
            if m:
                req = self._new_request(rec, m)
                by_token[(m.group(1), m.group(2))] = req
                requests.append(req)
                continue
            m = OFONO_RESP_RE.match(msg)
            if m:
                req = by_token.pop((m.group(1), m.group(2)), None)
                if req:
                    req["checkpoints"]["complete"] = _evidence(rec)
                    req["complete_sec"] = rec.sec
                    req["ril_error"] = m.group(4) if m.group(3) else None
                    req["resp_len"] = int(m.group(5)) if m.group(5) else None
                continue
            m = RILD_REQ_RE.match(msg)
            if m:
                req = next((r for r in requests if r["token"] == m.group(1) and "rild" not in r["checkpoints"]), None)
                if req:
                    req["checkpoints"]["rild"] = _evidence(rec)
                    req["raw_size"] = int(m.group(2)) if m.group(2) else None
                continue
            if RILD_DONE_RE.match(msg):
                req = self._oldest(requests, rec, has="secril", missing="rild_done")
                if req:
                    req["checkpoints"]["rild_done"] = _evidence(rec)
                continue
            if OFONO_PARCEL_ERR_RE.search(msg):
                req = self._oldest(requests, rec, has="rild_done", missing="complete")
                if req:
                    by_token.pop((str(req["slot"]), req["token"]), None)
                    req["complete_sec"] = rec.sec
                    req["ril_error"] = "MALFORMED_PARCEL"
                    req["ofono_error_evidence"] = _evidence(rec)
                continue
            if SECRIL_REQ_RE.search(msg):
                req = self._oldest(requests, rec, has="rild", missing="secril")
                if req:
                    req["checkpoints"]["secril"] = _evidence(rec)
                continue
            m = FUNC_ID_RE.search(msg)
            if m:
                req = self._oldest(requests, rec, has="secril", missing="func")
                if req:
                    func = int(m.group(1), 16)
                    req["checkpoints"]["func"] = _evidence(rec)
                    req["func_id"] = f"0x{func:02X}"
                    req["func_name"] = OEM_FUNC_NAMES.get(func, "UNKNOWN")
                    req["path"] = "raw_ipc" if func in RAW_IPC_FUNCS else "local"
                continue
            m = SEND_RAW_RE.search(msg)
            if m:
                func = f"0x{int(m.group(1), 16):02X}"
                req = next((r for r in requests if r.get("func_id") == func and r["send"] is None), None)
                if req:
                    req["send"] = {"sec": rec.sec, "len": int(m.group(2)), "result": None, "task": rec.task}
                continue
            if SEND_OK_RE.search(msg) or SEND_FAIL_RE.search(msg):
                req = next((r for r in requests if r["send"] and r["send"]["result"] is None), None)
                if req:
                    ok = bool(SEND_OK_RE.search(msg))
                    req["send"]["result"] = "success" if ok else "failed"
                    if not ok:
                        req["send_error"] = msg
                continue
            dump = _parse_dump(rec) if "X: (M)" in msg else None
            if dump:
                if dump["direction"] == "TX":
                    self._attach_tx(requests, dump, rec)
                else:
                    rx_dumps.append(dump)
                continue
            m = OFONO_IND_RE.search(msg)
            if m:
                inds.append(_decode_ind(rec, m))

        for req in requests:
            if req["tx"]:
                self._attach_rx(req, rx_dumps)
                self._attach_ind(req, inds)
            self._finalize(req, last_sec)

        unmatched = [i for i in inds if not i["matched"]]
        public = [self._public(r) for r in requests]
        return {
            "kpi": {
                "request_count": len(public),
                "raw_ipc_count": sum(1 for r in public if r["path"] == "raw_ipc"),
                "local_count": sum(1 for r in public if r["path"] == "local"),
                "ok_count": sum(1 for r in public if r["verdict"] == "ok"),
                "problem_count": sum(1 for r in public if r["verdict"] == "problem"),
                "pending_count": sum(1 for r in public if r["verdict"] == "pending"),
                "modem_answered_count": sum(1 for r in public if r["modem_rx"]),
                "modem_error_count": sum(1 for r in public if r["modem_error"] not in (None, GEN_ERR_NONE)),
                "unsol_count": len(inds),
                "unmatched_unsol_count": len(unmatched),
            },
            "requests": public,
            "unmatched_unsol": [{k: v for k, v in i.items() if k not in ("sec", "matched")}
                                for i in unmatched[:MAX_UNMATCHED_IND]],
        }

    def save_ui_report(self, output_dir="./result", base_name="", analysis=None):
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{base_name}_rtos_oem_hook.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(analysis or {}, f, indent=4, ensure_ascii=False)
        return out_path

    # ---- 수집 ----

    @staticmethod
    def _new_request(rec, m):
        return {
            "slot": int(m.group(1)), "token": m.group(2),
            "req_time": rec.time, "req_sec": rec.sec, "req_line": rec.line_no,
            "checkpoints": {"ofono_req": _evidence(rec)},
            "raw_size": None, "func_id": None, "func_name": None, "path": None,
            "send": None, "send_error": None, "tx": None, "rx": None, "ind": None,
            "complete_sec": None, "ril_error": None, "resp_len": None, "ofono_error_evidence": None,
        }

    @staticmethod
    def _oldest(requests, rec, has, missing):
        """secril 은 요청을 순서대로 처리한다. 앞 단계는 지났고 이 단계는 아직인 가장 오래된 요청.

        ofono 가 이미 실패 응답을 받은 요청(secril 이 앞에서 거절한 것)은 건너뛴다.
        """
        for req in requests:
            cps = req["checkpoints"]
            if has in cps and missing not in cps:
                if req["complete_sec"] is not None and req["complete_sec"] <= rec.sec:
                    continue
                return req
        return None

    @staticmethod
    def _attach_tx(requests, dump, rec):
        """`Calling SendRawIpc` 바로 뒤 같은 태스크의 TX 가 그 요청의 IPC 다."""
        for req in requests:
            send = req["send"]
            if send and req["tx"] is None and send["task"] == rec.task \
                    and 0 <= rec.sec - send["sec"] <= TX_AFTER_SEND_SEC:
                req["tx"] = dump
                req["checkpoints"]["cpif_tx"] = _evidence(rec)
                return

    @staticmethod
    def _attach_rx(req, rx_dumps):
        tx = req["tx"]
        for rx in rx_dumps:
            if rx.get("claimed") or not (0 <= rx["sec"] - tx["sec"] <= MODEM_RESPONSE_WINDOW_SEC):
                continue
            if rx["ack"] != tx["seq"]:
                continue
            gen = rx["gen"]
            if gen:
                if gen["main"] != tx["main"]:
                    continue
            elif tx["type"] in GR_ONLY_TX_TYPES or rx["main"] != tx["main"] or rx["sub"] != tx["sub"] \
                    or rx["type"] != "RESP":
                continue
            rx["claimed"] = True
            req["rx"] = rx
            req["checkpoints"]["modem_rx"] = {"time": rx["time"], "line_no": rx["line_no"], "text": rx["text"]}
            return

    @staticmethod
    def _attach_ind(req, inds):
        rx = req["rx"]
        if not rx:
            return
        for ind in inds:
            if not ind["matched"] and ind["seq"] == rx["seq"] and ind["ack"] == rx["ack"] \
                    and 0 <= ind["sec"] - rx["sec"] <= MODEM_RESPONSE_WINDOW_SEC:
                ind["matched"] = True
                req["ind"] = ind
                req["checkpoints"]["unsol"] = {"time": ind["time"], "line_no": ind["line_no"], "text": ind["text"]}
                return

    # ---- 판정 ----

    def _finalize(self, req, last_sec):
        chain = RAW_CHAIN if req["path"] == "raw_ipc" else LOCAL_CHAIN
        tx_type = (req["tx"] or {}).get("type")
        no_response_ipc = tx_type in NO_RESPONSE_TX_TYPES
        if no_response_ipc:
            chain = [c for c in chain if c[0] not in MODEM_STAGES]
        req["expects_modem_response"] = req["path"] == "raw_ipc" and not no_response_ipc
        req["expected_response"] = _expected_response(req["tx"])
        cps = req["checkpoints"]
        missing = next(((s, label) for s, label in chain if s not in cps), None)
        req["broken_at"] = {"stage": missing[0], "label": missing[1]} if missing else None
        rx = req["rx"]
        req["modem_error"] = rx["gen"]["error"] if rx and rx["gen"] else None
        req["chain"] = [
            {"stage": s, "label": label, "reached": s in cps, "time": cps.get(s, {}).get("time")}
            for s, label in chain
        ]
        # 로그가 이 시점 뒤로 조금밖에 없으면 응답이 없는 게 아니라 로그가 먼저 끝난 것이다
        tail_short = last_sec - req["req_sec"] < MODEM_RESPONSE_WINDOW_SEC
        verdict, status = "ok", "정상"
        if req["ril_error"] == "MALFORMED_PARCEL":
            verdict, status = "problem", "ofono 가 rild 응답을 못 읽음 (malformed parcel)"
        elif req["ril_error"]:
            verdict, status = "problem", f"요청 실패 {req['ril_error']}"
        elif req["send"] and req["send"]["result"] == "failed":
            verdict, status = "problem", "cpif 송신 실패"
        elif missing:
            if tail_short:
                verdict, status = "pending", f"{missing[1]} 전에 로그 끝남"
            elif missing[0] == "unsol":
                verdict, status = "problem", "모뎀 응답이 ofono 로 전달 안 됨"
            elif missing[0] == "modem_rx":
                verdict, status = "problem", "모뎀 응답 없음"
            else:
                verdict, status = "problem", f"{missing[1]} 로그 없음"
        elif rx and rx["gen"] and tx_type in RESP_TX_TYPES and req["modem_error"] == GEN_ERR_NONE:
            verdict, status = "problem", "GET 에 RESP 대신 성공 General Response 가 옴"
        elif req["modem_error"] not in (None, GEN_ERR_NONE):
            verdict, status = "problem", f"모뎀 오류 {gen_error_label(req['modem_error'])}"
        elif req["path"] == "local":
            status = "정상 (secril 내부 처리)"
        elif no_response_ipc:
            status = f"정상 ({tx_type}: 모뎀 응답이 없는 IPC)"
        req["verdict"], req["status"] = verdict, status
        req["tx_rx_ms"] = round((rx["sec"] - req["tx"]["sec"]) * 1000, 1) if rx else None

    @staticmethod
    def _public(req):
        def ipc(d):
            if not d:
                return None
            out = {k: d[k] for k in ("modem", "main", "sub", "type", "len", "seq", "ack", "time", "line_no")}
            if d.get("gen"):
                out["gen"] = dict(d["gen"], error_label=gen_error_label(d["gen"]["error"]))
            return out
        out = {k: v for k, v in req.items()
               if k not in ("req_sec", "complete_sec", "send", "tx", "rx", "ind")}
        out["send_result"] = req["send"]["result"] if req["send"] else None
        out["tx"] = ipc(req["tx"])
        out["modem_rx"] = ipc(req["rx"])
        out["modem_error_label"] = gen_error_label(req["modem_error"])
        out["unsol_time"] = req["ind"]["time"] if req["ind"] else None
        return out
