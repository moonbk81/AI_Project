import json

from agent_toolkit import get_rtos_oem_hook_analytics
from core.charts import build_rtos_oem_hook
from parsers.rtos import RtosOemHookParser

# "device crashed after placing 2nd call.txt" 에서 발췌. 태스크 165=ofono, 164=rild, 346/345=secril, 373=cpif 리더


def _line(time, task, msg):
    return f"[31/12/99 {time}] [{task}] [ap] {msg}"


def _raw_request(token, t0, raw_size, tx):
    """ofono 요청부터 cpif TX 와 ofono 완료까지. t0 은 초 단위 문자열 앞부분 ('07:49:27')."""
    return [
        _line(f"{t0}.100000", 165, f"[0,{token}]> RIL_REQUEST_OEM_HOOK_RAW"),
        _line(f"{t0}.110000", 164, f"[RIL_CPP] [{token}]> OEM_HOOK_RAW (raw_size={raw_size})"),
        _line(f"{t0}.111000", 164, "[RILD] SecRilProxy::OnRequest(59) RIL_REQUEST_OEM_HOOK_RAW"),
        _line(f"{t0}.120000", 346, "[RILD] GetMessage : oem func id = 0x0F"),
        _line(f"{t0}.121000", 345, "[RILD] OEMHOOK-MGR: Receive event : 100"),
        _line(f"{t0}.122000", 345, f"[RILD] DoOemRawIpc: Calling SendRawIpc funcId=0x0F len={raw_size - 4}"),
        _line(f"{t0}.123000", 345, f"[RILD] [B] TX: {tx}"),
        _line(f"{t0}.124000", 345, "[RILD] DoOemRawIpc: SendRawIpc success (ret=0)"),
        _line(f"{t0}.125000", 345, "[RILD] SecRil::RequestComplete"),
        _line(f"{t0}.126000", 345, "[RIL_CPP] RequestComplete OEM_HOOK_RAW"),
        _line(f"{t0}.130000", 165, f"[0,{token}]< RIL_REQUEST_OEM_HOOK_RAW 1"),
        _line(f"{t0}.131000", 165, "oem_request_raw_reply: len=1 data=0x19caddd0 [FF ]"),
    ]


RAW_OK = _raw_request("0096", "07:49:27", 13, "(M)IMS_CMD (S)IMS_ENGINE (T)SET l:9 m:46 a:00 [ 01 00 ]") + [
    _line("07:49:28.603500", 373, "[RILD] [B] RX: (M)GEN_CMD (S)GEN_PHONE_RES (T)RESP l:c m:43 a:46 [ 16 09 03 00 80 ]"),
    _line("07:49:28.612000", 373, "[RILD] ProcessSingleIpcMessageReceived: forwarding to Oem."),
    _line("07:49:28.909400", 345, "[RILD] OnUnsolicitedResponse: Convert [61024] to UNSOL_OEM_HOOK_RAW"),
    _line("07:49:28.927600", 165, "[0,UNSOL]< UNSOL_OEM_HOOK_RAW 12"),
    _line("07:49:28.928400", 165, "oem_hook_raw_ind: len=12 data=0x19cbcd60 [0C 00 43 46 80 01 02 16 09 03 00 80 ]"),
]

RAW_GR_ERROR = _raw_request(
    "0099", "07:49:28", 12, "(M)IMS_CMD (S)IPC_IMS_GET_ENGINE_CAPABILITY (T)GET l:8 m:49 a:00 [ 00 ]"
) + [
    _line("07:49:28.738500", 373, "[RILD] [B] RX: (M)GEN_CMD (S)GEN_PHONE_RES (T)RESP l:c m:45 a:49 [ 16 26 02 01 80 ]"),
    _line("07:49:29.544700", 165, "oem_hook_raw_ind: len=12 data=0x19c25450 [0C 00 45 49 80 01 02 16 26 02 01 80 ]"),
]

RAW_EVENT = _raw_request("0100", "07:49:30", 112, "(M)IMS_CMD (S)IMS_INFORMATION (T)EVENT l:6c m:4a a:00 [ 7B ]")
RAW_GET_NO_RESPONSE = _raw_request("0098", "07:49:31", 13, "(M)IMS_CMD (S)IMS_FRAME_TIME (T)GET l:9 m:48 a:00 [ 00 00 ]")

UICC_MALFORMED = [
    _line("07:49:17.723400", 165, "[0,0083]> RIL_REQUEST_OEM_HOOK_RAW"),
    _line("07:49:18.989200", 164, "[RIL_CPP] [0083]> OEM_HOOK_RAW (raw_size=6)"),
    _line("07:49:18.990900", 164, "[RILD] SecRilProxy::OnRequest(59) RIL_REQUEST_OEM_HOOK_RAW"),
    _line("07:49:19.077500", 346, "[RILD] GetMessage : oem func id = 0x15"),
    # SIM 매니저가 안에서 처리한다. 이 TX 는 다른 요청의 것이라 붙으면 안 된다
    _line("07:49:19.116400", 345, "[RILD] [B] TX: (M)NET_CMD (S)NET_SERVING_NETWORK (T)GET l:7 m:3e a:00 [ ]"),
    _line("07:49:19.138100", 345, "[RILD] SIM-MGR: HandleEvent 130"),
    _line("07:49:19.155900", 345, "[RIL_CPP] RequestComplete OEM_HOOK_RAW"),
    _line("07:49:19.168300", 165, "ril_oem_request_raw_cb: malformed parcel"),
]

RADIO_OFF = [
    _line("07:48:29.600200", 165, "[0,0015]> RIL_REQUEST_OEM_HOOK_RAW"),
    _line("07:48:29.639400", 164, "[RIL_CPP] [0015]> OEM_HOOK_RAW (raw_size=4)"),
    _line("07:48:29.641200", 164, "[RILD] SecRilProxy::OnRequest(59) RIL_REQUEST_OEM_HOOK_RAW"),
    _line("07:48:29.682800", 164, "[RIL_CPP] RequestComplete OEM_HOOK_RAW"),
    _line("07:48:29.741100", 165, "[0,0015]< RIL_REQUEST_OEM_HOOK_RAW failed RADIO_NOT_AVAILABLE"),
]

# 로그 끝을 요청들 뒤로 충분히 밀어 둔다 (끝 근처의 요청은 '로그 끝남'으로 따로 본다)
TAIL = [_line("07:50:59.109800", 151, "[RIL_CPP] tail")]


def _by_token(lines):
    result = RtosOemHookParser().analyze(lines)
    return result, {r["token"]: r for r in result["requests"]}


def test_raw_ipc_goes_to_modem_and_back():
    result, reqs = _by_token(RAW_OK + TAIL)
    req = reqs["0096"]
    assert req["path"] == "raw_ipc"
    assert req["func_name"] == "CP_IMS"
    assert req["raw_size"] == 13
    assert req["tx"]["main"] == "IMS_CMD" and req["tx"]["seq"] == 0x46
    assert req["modem_rx"]["ack"] == 0x46
    assert req["modem_rx"]["gen"]["main"] == "IMS_CMD"
    assert req["modem_error_label"] == "0x8000 NONE"
    assert req["unsol_time"] == "07:49:28.928400"
    assert req["verdict"] == "ok" and req["broken_at"] is None
    assert all(stage["reached"] for stage in req["chain"])
    assert result["kpi"]["unmatched_unsol_count"] == 0


def test_general_response_error_is_a_problem():
    _, reqs = _by_token(RAW_GR_ERROR + TAIL)
    req = reqs["0099"]
    assert req["modem_error"] == 0x8001
    assert req["verdict"] == "problem"
    assert "INVALID_IPC" in req["status"]


def test_event_ipc_needs_no_modem_response():
    _, reqs = _by_token(RAW_EVENT + TAIL)
    req = reqs["0100"]
    assert req["expects_modem_response"] is False
    assert req["verdict"] == "ok"
    assert "EVENT" in req["status"]
    assert "모뎀 응답(RX)" not in [s["label"] for s in req["chain"]]


def test_get_without_modem_response_breaks_at_rx():
    _, reqs = _by_token(RAW_GET_NO_RESPONSE + TAIL)
    req = reqs["0098"]
    assert req["verdict"] == "problem"
    assert req["broken_at"]["stage"] == "modem_rx"
    assert req["status"] == "모뎀 응답 없음"


def test_request_near_log_end_is_pending_not_failed():
    _, reqs = _by_token(RAW_GET_NO_RESPONSE)
    assert reqs["0098"]["verdict"] == "pending"


def test_local_func_with_malformed_parcel_in_ofono():
    _, reqs = _by_token(UICC_MALFORMED + TAIL)
    req = reqs["0083"]
    assert req["path"] == "local"
    assert req["tx"] is None, "SIM 매니저가 처리하는 요청에 다른 IPC 를 붙이면 안 된다"
    assert req["ril_error"] == "MALFORMED_PARCEL"
    assert req["broken_at"]["stage"] == "complete"
    assert req["ofono_error_evidence"]["line_no"] == 8


def test_secril_rejected_request_does_not_steal_next_func_id():
    _, reqs = _by_token(RADIO_OFF + UICC_MALFORMED + TAIL)
    assert reqs["0015"]["ril_error"] == "RADIO_NOT_AVAILABLE"
    assert reqs["0015"]["func_id"] is None
    assert reqs["0083"]["func_id"] == "0x15"


def test_interleaved_requests_match_by_seq():
    lines = RAW_OK + RAW_GR_ERROR + RAW_EVENT + RAW_GET_NO_RESPONSE + UICC_MALFORMED + RADIO_OFF + TAIL
    result, reqs = _by_token(lines)
    assert result["kpi"]["request_count"] == 6
    assert result["kpi"]["raw_ipc_count"] == 4
    assert result["kpi"]["modem_answered_count"] == 2
    assert reqs["0096"]["modem_error"] == 0x8000
    assert reqs["0099"]["modem_error"] == 0x8001


def test_chart_and_tool_read_the_artifact(tmp_path):
    parser = RtosOemHookParser()
    data = parser.analyze(RAW_OK + UICC_MALFORMED + TAIL)
    parser.save_ui_report(str(tmp_path), "crash", data)

    chart = build_rtos_oem_hook(json.loads((tmp_path / "crash_rtos_oem_hook.json").read_text(encoding="utf-8")))
    assert chart.status == "ok"
    rows = {r["token"]: r for r in chart.requests}
    assert rows["0096"]["ipc"] == "IMS_CMD / IMS_ENGINE / SET"
    assert rows["0096"]["tx_seq"] == "46"
    assert rows["0083"]["error_evidence"]["text"].startswith("ril_oem_request_raw_cb")
    assert build_rtos_oem_hook({}).status == "no_data"
    assert build_rtos_oem_hook({"kpi": {}, "requests": []}).status == "no_oem_hook"

    fact = json.loads(get_rtos_oem_hook_analytics("crash", str(tmp_path)))
    assert fact["status"] == "OK"
    assert fact["requests"][0]["func"] == "0x15 UICC"
    assert json.loads(get_rtos_oem_hook_analytics("none", str(tmp_path)))["status"] == "NO_DATA"


def test_pipeline_writes_artifact_and_rag_document(tmp_path, monkeypatch):
    from log_orchestrator import LogOrchestrator
    from rag_builders.builder import build_all_payloads

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "crash.txt"
    log.write_text("\r\n".join(RAW_OK + RAW_GET_NO_RESPONSE + UICC_MALFORMED + TAIL * 30) + "\r\n", encoding="utf-8")
    report_path = tmp_path / "crash_report.json"
    assert LogOrchestrator(str(log)).run_batch(str(report_path)) is True

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["rtos_oem_hook"]["kpi"]["request_count"] == 3
    assert (tmp_path / "result" / "crash_rtos_oem_hook.json").exists()

    payloads = build_all_payloads(report, "crash_report.json", None, None)
    doc = next(p for p in payloads if p["metadata"]["log_type"] == "RTOS_OemHook_Flow")
    assert "token 0098" in doc["document"] and "모뎀 응답 없음" in doc["document"]
    assert "malformed parcel" in doc["document"]
    assert "token 0096" not in doc["document"], "정상 요청은 요약 숫자로만 남긴다"
    for meta in (p["metadata"] for p in payloads):
        assert all(isinstance(v, (str, int, float, bool)) for v in meta.values()), meta


GET_RESP = _raw_request("0120", "07:50:10", 13, "(M)IMS_CMD (S)IMS_FRAME_TIME (T)GET l:9 m:60 a:00 [ 00 00 ]") + [
    _line("07:50:10.400000", 373, "[RILD] [B] RX: (M)IMS_CMD (S)IMS_FRAME_TIME (T)RESP l:b m:61 a:60 [ 01 02 03 04 ]"),
    _line("07:50:10.500000", 165, "oem_hook_raw_ind: len=11 data=0x19cbcd60 [0B 00 61 60 16 0A 02 01 02 03 04 ]"),
]
# SET 에는 General Response 만 온다. 같은 MAIN/SUB 의 RESP 는 SET 의 응답이 아니다
SET_WITH_RESP_ONLY = _raw_request("0121", "07:50:20", 13, "(M)IMS_CMD (S)IMS_ENGINE (T)SET l:9 m:62 a:00 [ 01 00 ]") + [
    _line("07:50:20.400000", 373, "[RILD] [B] RX: (M)IMS_CMD (S)IMS_ENGINE (T)RESP l:9 m:63 a:62 [ 01 00 ]"),
]
GET_WITH_OK_GR = _raw_request("0122", "07:50:30", 13, "(M)IMS_CMD (S)IMS_FRAME_TIME (T)GET l:9 m:64 a:00 [ 00 00 ]") + [
    _line("07:50:30.400000", 373, "[RILD] [B] RX: (M)GEN_CMD (S)GEN_PHONE_RES (T)RESP l:c m:65 a:64 [ 16 0A 02 00 80 ]"),
    _line("07:50:30.500000", 165, "oem_hook_raw_ind: len=12 data=0x19cbcd60 [0C 00 65 64 80 01 02 16 0A 02 00 80 ]"),
]


def test_get_is_answered_by_its_own_resp():
    _, reqs = _by_token(GET_RESP + TAIL)
    req = reqs["0120"]
    assert req["verdict"] == "ok"
    assert req["modem_rx"]["main"] == "IMS_CMD" and req["modem_rx"]["type"] == "RESP"
    assert req["modem_error"] is None
    assert req["expected_response"] == "IMS_CMD / IMS_FRAME_TIME / RESP"


def test_set_is_not_answered_by_resp():
    _, reqs = _by_token(SET_WITH_RESP_ONLY + TAIL)
    req = reqs["0121"]
    assert req["modem_rx"] is None
    assert req["status"] == "모뎀 응답 없음"
    assert req["expected_response"] == "GEN_CMD / GEN_PHONE_RES"


def test_get_answered_by_success_gr_is_flagged():
    _, reqs = _by_token(GET_WITH_OK_GR + TAIL)
    assert reqs["0122"]["verdict"] == "problem"
    assert "RESP 대신" in reqs["0122"]["status"]


def test_no_response_types():
    cfrm = _raw_request("0123", "07:50:40", 13, "(M)IMS_CMD (S)IMS_INFORMATION (T)CFRM l:9 m:66 a:00 [ 00 ]")
    _, reqs = _by_token(cfrm + TAIL)
    assert reqs["0123"]["verdict"] == "ok"
    assert reqs["0123"]["expected_response"] == "응답 없음"


def test_exec_is_answered_by_general_response():
    exec_ok = _raw_request("0124", "07:50:45", 9, "(M)PWR_CMD (S)PWR_PHONE_STATE (T)EXEC l:9 m:70 a:00 [ 02 02 ]") + [
        _line("07:50:45.400000", 373, "[RILD] [B] RX: (M)PWR_CMD (S)PWR_PHONE_STATE (T)RESP l:9 m:71 a:70 [ 02 02 ]"),
        _line("07:50:45.450000", 373, "[RILD] [B] RX: (M)GEN_CMD (S)GEN_PHONE_RES (T)RESP l:c m:72 a:70 [ 01 03 01 00 80 ]"),
        _line("07:50:45.500000", 165, "oem_hook_raw_ind: len=12 data=0x19cbcd60 [0C 00 72 70 80 01 02 01 03 01 00 80 ]"),
    ]
    _, reqs = _by_token(exec_ok + TAIL)
    req = reqs["0124"]
    assert req["expected_response"] == "GEN_CMD / GEN_PHONE_RES"
    assert req["modem_rx"]["main"] == "GEN_CMD", "EXEC 에 RESP 를 붙이면 안 된다"
    assert req["verdict"] == "ok"
