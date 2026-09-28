from parsers.rtos import RtosCallFlowParser, is_rtos_log, parse_line, parse_lines

# callMO.txt 에서 발췌 (다이얼 관련 줄 + 종료)
MO_LINES = [
    "[31/12/99 02:38:57.935900] [174] [ap] [TAPI_FWK] Dialing: 9560959279 (hide=0)",
    "[31/12/99 02:38:57.943500] [174] [ap] [TAPI_FWK] Dial request sent successfully",
    "[31/12/99 02:38:57.944500] [174] [ap] [SAMSUNG_CALL] DIAL: tapi_fwk_dial result=0",
    '    "ecio"[31/12/99 02:38:58.221500] [152] [ap] [0,0136]> RIL_REQUEST_DIAL (***,0,0,0)',
    "[31/12/99 02:38:58.234500] [151] [ap] [RIL_CPP] dispatchDial",
    "[31/12/99 02:38:58.235400] [151] [ap] [RIL_CPP] [0136]> DIAL (num=9560959279,clir=0)",
    "[31/12/99 02:38:58.441000] [151] [ap] [RIL-IMSCALL] call + sid=1120794317 idx=5 mo DIALING",
    "[31/12/99 02:38:58.442000] [151] [ap] [RIL_CPP] RequestComplete DIAL",
    "[31/12/99 02:38:58.451400] [152] [ap] [0,0136]< RIL_REQUEST_DIAL",
    "[31/12/99 02:38:58.457400] [152] [ap] [0,0137]> RIL_REQUEST_GET_CURRENT_CALLS",
    "[31/12/99 02:38:58.462100] [151] [ap] [RIL_CPP] [0137]> GET_CURRENT_CALLS ",
    "[31/12/99 02:38:58.463900] [151] [ap] [RIL-IMSCALL] GET_CURRENT_CALLS -> 1 call(s)",
    "[31/12/99 02:38:58.465800] [151] [ap] [RIL_CPP] {[id=5,DIALING,toa=129,norm,mo,als=0,voc,noevp,9560959279,cli=0,name='',2}",
    "[31/12/99 02:38:58.485100] [152] [ap] [0,0137]< RIL_REQUEST_GET_CURRENT_CALLS",
    "[31/12/99 02:39:02.182800] [346] [ap] 12-31 02:39:02.182+0000 185 346 [W][IMS-FW] [SIP]> INVITE sip:9560959279;phone-context=ims.mnc011.mcc404.3gppnetwork.org@ims.mnc011.mcc404.3gppnetwork.org;user=phone SIP/2.0",
    "[31/12/99 02:39:02.210400] [346] [ap] 12-31 02:39:02.210+0000 185 346 [W][IMS-FW] Call-ID: _AmzwYN2my1Rhtvf-SJcTg..@2402:8100:6af2:ac53::37:3101:b901",
    "[31/12/99 02:39:02.212300] [346] [ap] 12-31 02:39:02.212+0000 185 346 [W][IMS-FW] CSeq: 1 INVITE",
    "[31/12/99 02:39:02.240200] [346] [ap] 12-31 02:39:02.240+0000 185 346 [W][IMS-FW] Content-Length: 0",
    "[31/12/99 02:39:02.241500] [346] [ap] 12-31 02:39:02.241+0000 185 346 [W][IMS-FW] ",
    "[31/12/99 02:39:08.837300] [346] [ap] 12-31 02:39:08.837+0000 185 346 [W][IMS-FW] [SIP]< SIP/2.0 180 Ringing",
    "[31/12/99 02:39:08.853000] [346] [ap] 12-31 02:39:08.853+0000 185 346 [W][IMS-FW] Call-ID: _AmzwYN2my1Rhtvf-SJcTg..@2402:8100:6af2:ac53::37:3101:b901",
    "[31/12/99 02:39:08.855000] [346] [ap] 12-31 02:39:08.855+0000 185 346 [W][IMS-FW] CSeq: 1 INVITE",
    "[31/12/99 02:39:08.871000] [346] [ap] 12-31 02:39:08.871+0000 185 346 [W][IMS-FW] Content-Length: 0",
    "[31/12/99 02:39:08.873000] [346] [ap] 12-31 02:39:08.873+0000 185 346 [W][IMS-FW] ",
    "[31/12/99 02:39:09.253700] [151] [ap] [RIL-IMSCALL] call ~ sid=1120794317 idx=5 DIALING -> ALERTING",
    "[31/12/99 02:39:35.238100] [152] [ap] [0,0164]> RIL_REQUEST_GET_CURRENT_CALLS",
    "[31/12/99 02:39:35.243200] [151] [ap] [RIL_CPP] [0164]> GET_CURRENT_CALLS ",
    "[31/12/99 02:39:35.244900] [151] [ap] [RIL-IMSCALL] GET_CURRENT_CALLS -> 0 call(s)",
    "[31/12/99 02:39:35.245800] [151] [ap] [RIL_CPP] RequestComplete GET_CURRENT_CALLS",
    "[31/12/99 02:39:35.250400] [152] [ap] [0,0164]< RIL_REQUEST_GET_CURRENT_CALLS",
    "[31/12/99 02:39:35.251500] [152] [ap] [0,0165]> RIL_REQUEST_LAST_CALL_FAIL_CAUSE",
    "[31/12/99 02:39:35.256600] [151] [ap] [RIL_CPP] [0165]> LAST_CALL_FAIL_CAUSE ",
    "[31/12/99 02:39:35.258700] [151] [ap] [RIL_CPP] RequestComplete LAST_CALL_FAIL_CAUSE",
    "[31/12/99 02:39:35.265600] [152] [ap] [0,0165]< RIL_REQUEST_LAST_CALL_FAIL_CAUSE {16}",
]

# callMT.txt 에서 발췌 — TAPI open 문제로 첫 INCOMING 뒤 UI 까지 가지 못한 사례
MT_LINES = [
    "[31/12/99 02:06:23.668300] [346] [ap] 12-31 02:06:23.668+0000 185 346 [W][IMS-FW] [SIP]< INVITE sip:+917290070351@[2402:8100:6af2:ac53::37:3101:b901]:6100 SIP/2.0",
    "[31/12/99 02:06:23.670000] [346] [ap] 12-31 02:06:23.670+0000 185 346 [W][IMS-FW] Call-ID: LU-1789968831275873-50013530@ims333-013.dl.ims.sbc.nokia.com",
    "[31/12/99 02:06:23.671000] [346] [ap] 12-31 02:06:23.671+0000 185 346 [W][IMS-FW] CSeq: 1 INVITE",
    "[31/12/99 02:06:23.672000] [346] [ap] 12-31 02:06:23.672+0000 185 346 [W][IMS-FW] Content-Length: 0",
    "[31/12/99 02:06:23.673000] [346] [ap] 12-31 02:06:23.673+0000 185 346 [W][IMS-FW] ",
    "[31/12/99 02:06:25.388300] [346] [ap] 12-31 02:06:25.387+0000 185 346 [I][IMS6.0] CallSession::NotifyIncomingCall(): :(7)",
    "[31/12/99 02:06:25.471500] [346] [ap] 12-31 02:06:25.471+0000 185 346 [W][IMS-FW] [TID:UNSL]< NOTIFY_INCOMING_CALL(10005)",
    "[31/12/99 02:06:26.371500] [245] [ap] [RIL-IMSBRIDGE] [rx] <- noti:CallIncomingInd sid=1593715712",
    "[31/12/99 02:06:26.376000] [151] [ap] [RIL-IMSBRIDGE] [loop] <- noti:CallIncomingInd tok=0 sid=1593715712",
    "[31/12/99 02:06:26.377300] [151] [ap] [RIL-IMSCALL] call + sid=1593715712 idx=1 mt INCOMING",
    "[31/12/99 02:06:26.385400] [152] [ap] [0,0093]> RIL_REQUEST_GET_CURRENT_CALLS",
    "[31/12/99 02:06:26.396500] [151] [ap] [RIL_CPP] [0093]> GET_CURRENT_CALLS ",
    "[31/12/99 02:06:26.398300] [151] [ap] [RIL-IMSCALL] GET_CURRENT_CALLS -> 1 call(s)",
    "[31/12/99 02:06:26.400300] [151] [ap] [RIL_CPP] {[id=1,INCOMING,toa=145,norm,mt,als=0,voc,noevp,+918317007463,cli=0,name='',2}",
    "[31/12/99 02:06:26.404700] [152] [ap] [0,0093]< RIL_REQUEST_GET_CURRENT_CALLS",
]


def test_line_parses_task_core_and_tags():
    rec = parse_line("[31/12/99 02:38:58.235400] [151] [ap] [RIL_CPP] [0136]> DIAL (num=1,clir=0)", 7)
    assert (rec.line_no, rec.time, rec.task, rec.core, rec.tag) == (7, "02:38:58.235400", 151, "ap", "RIL_CPP")

    nested = parse_line(next(l for l in MT_LINES if "[IMS6.0]" in l))
    assert (nested.tag, nested.level) == ("IMS6.0", "I")

    word = parse_line("[31/12/99 01:33:16.943000] [173] [ap] ATD: ListenFD()")
    assert word.tag == "ATD"

    ofono = parse_line(MO_LINES[3])
    assert ofono.tag is None and ofono.msg.startswith("[0,0136]>")


def test_lines_are_sorted_by_time_and_keep_original_line_numbers():
    records = parse_lines([
        "[31/12/99 01:33:16.952400] [324] [ap] [NxpHal] write successful status = 0x0",
        "",
        "[31/12/99 01:33:16.937800] [253] [ap] org.ofono.Error.Failed: Operation failed",
        '\t"jver":\t"1.0",',
    ])
    assert [r.line_no for r in records] == [3, 1]
    assert records[0].extra == ['\t"jver":\t"1.0",']


def test_day_rollover_keeps_order():
    records = parse_lines([
        "[01/01/00 00:00:00.100000] [1] [ap] after",
        "[31/12/99 23:59:59.900000] [1] [ap] before",
    ])
    assert [r.msg for r in records] == ["before", "after"]


def test_is_rtos_log_tolerates_lastword_header():
    header = ["=" * 39, "CHIP=best1700", "KERNEL=NUTTX", "=" * 39]
    assert is_rtos_log(header + MO_LINES)
    assert not is_rtos_log(["08-25 10:00:00.000  1000  1000 D RILJ: [0001]> DIAL"])


def test_mo_call_reaches_alerting_and_ends_with_fail_cause():
    result = RtosCallFlowParser().analyze(MO_LINES)

    assert result["kpi"]["call_count"] == 1
    call = result["calls"][0]
    assert call["direction"] == "MO"
    assert [s["state"] for s in call["states"]] == ["DIALING", "ALERTING"]
    assert call["end_time"] == "02:39:35.244900"
    assert call["fail_cause"] == "16"
    assert call["fail_reason"].startswith("NORMAL_CLEARING")
    assert call["broken_at"] is None
    assert call["status"] == "연결 전 종료"
    assert call["checkpoints"]["ofono_dial"]["time"] == "02:38:58.221500"
    assert call["checkpoints"]["rild_dial"]["time"] == "02:38:58.235400"
    assert call["checkpoints"]["sip_invite"]["time"] == "02:39:02.182800"
    assert [m["method_code"] for m in call["sip_messages"]] == ["INVITE", "180 Ringing"]

    dial = next(r for r in call["ril_requests"] if r["name"] == "DIAL")
    assert (dial["token"], dial["latency_ms"]) == ("0136", 229.9)
    assert result["kpi"]["unanswered_request_count"] == 0


def test_mt_call_stuck_before_ui_is_reported_at_ui_stage():
    result = RtosCallFlowParser().analyze(MT_LINES)

    call = result["calls"][0]
    assert call["direction"] == "MT"
    assert call["sid"] == "1593715712"
    assert call["status"] == "연결 전 로그 끝남"
    assert call["broken_at"]["stage"] == "ui_incoming"
    reached = [c["stage"] for c in call["chain"] if c["reached"]]
    assert reached == ["sip_invite", "ims_incoming", "bridge", "ril_call", "ofono_clcc"]
    assert call["sip_call_id"].startswith("LU-1789968831275873")


def test_active_state_from_call_list_marks_connected():
    lines = MO_LINES[:15] + [
        "[31/12/99 02:39:12.000000] [151] [ap] [RIL_CPP] {[id=5,ACTIVE,toa=129,norm,mo,als=0,voc,noevp,9560959279,cli=0,name='',2}",
    ] + MO_LINES[15:]
    call = RtosCallFlowParser().analyze(lines)["calls"][0]
    assert call["connected"] is True
    assert call["status"] == "통화 후 종료"


def test_request_without_response_is_reported():
    result = RtosCallFlowParser().analyze(MT_LINES + [
        "[31/12/99 02:06:30.000000] [152] [ap] [0,0094]> RIL_REQUEST_ANSWER",
    ])
    assert result["kpi"]["unanswered_request_count"] == 1
    assert result["unanswered_requests"][0]["name"] == "ANSWER"


def test_mo_without_invite_breaks_at_ims_stage():
    no_sip = [l for l in MO_LINES if "[346]" not in l]
    call = RtosCallFlowParser().analyze(no_sip[:14])["calls"][0]
    assert call["broken_at"]["stage"] == "sip_invite"
