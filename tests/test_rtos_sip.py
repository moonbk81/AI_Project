from parsers.rtos import parse_lines
from parsers.rtos.sip import parse_sip_messages

# callMO.txt 형식: 줄 안에 IMS logcat 헤더가 한 번 더 있다. 다른 task 줄이 사이에 낀다.
FULL_FORMAT = [
    "[31/12/99 02:39:02.182800] [346] [ap] 12-31 02:39:02.182+0000 185 346 [W][IMS-FW] [SIP]> INVITE sip:9560959279@ims.mnc011.mcc404.3gppnetwork.org;user=phone SIP/2.0",
    "[31/12/99 02:39:02.210400] [346] [ap] 12-31 02:39:02.210+0000 185 346 [W][IMS-FW] Call-ID: abc@host",
    "[31/12/99 02:39:02.212300] [346] [ap] 12-31 02:39:02.212+0000 185 346 [W][IMS-FW] CSeq: 1 INVITE",
    "[31/12/99 02:39:02.226000] [764] [ap] [SAP_LITE]|D|SAP| #isGMCon 0",
    "[31/12/99 02:39:02.232000] [346] [ap] 12-31 02:39:02.232+0000 185 346 [W][IMS-FW] P-Early-Media: supported",
    "[31/12/99 02:39:02.240200] [346] [ap] 12-31 02:39:02.240+0000 185 346 [W][IMS-FW] Content-Length: 44",
    "[31/12/99 02:39:02.241500] [346] [ap] 12-31 02:39:02.241+0000 185 346 [W][IMS-FW] ",
    "[31/12/99 02:39:02.243600] [346] [ap] 12-31 02:39:02.243+0000 185 346 [W][IMS-FW] v=0",
    "[31/12/99 02:39:02.257300] [346] [ap] 12-31 02:39:02.257+0000 185 346 [W][IMS-FW] a=rtpmap:116 AMR-WB/16000/1",
    "[31/12/99 02:39:02.264900] [346] [ap] 12-31 02:39:02.264+0000 185 346 [W][IMS-FW] a=rtpmap:118 AMR/8000/1",
    "[31/12/99 02:39:02.282200] [346] [ap] 12-31 02:39:02.282+0000 185 346 [W][IMS-FW]",
    "[31/12/99 02:39:02.290000] [346] [ap] 12-31 02:39:02.290+0000 185 346 [W][IMS-FW] EventMessage::~EventMessage(): obj refcnt=0",
    "[31/12/99 02:39:08.837300] [346] [ap] 12-31 02:39:08.837+0000 185 346 [W][IMS-FW] [SIP]< SIP/2.0 486 Busy Here",
    "[31/12/99 02:39:08.853000] [346] [ap] 12-31 02:39:08.853+0000 185 346 [W][IMS-FW] Call-ID: abc@host",
    "[31/12/99 02:39:08.855000] [346] [ap] 12-31 02:39:08.855+0000 185 346 [W][IMS-FW] CSeq: 1 INVITE",
    "[31/12/99 02:39:08.856000] [346] [ap] 12-31 02:39:08.856+0000 185 346 [W][IMS-FW] Reason: SIP;cause=486",
    "[31/12/99 02:39:08.871000] [346] [ap] 12-31 02:39:08.871+0000 185 346 [W][IMS-FW] Content-Length: 0",
    "[31/12/99 02:39:08.873000] [346] [ap] 12-31 02:39:08.873+0000 185 346 [W][IMS-FW] ",
    "[31/12/99 02:39:08.876000] [346] [ap] 12-31 02:39:08.876+0000 185 346 [D][IMS-FW] Registrant::Notify(): refcnt(1)",
]

# call_failed.txt 형식: logcat 헤더 없이 `[I][IMS-FW]: 본문`. 헤더가 공백으로 접힌다.
SHORT_FORMAT = [
    "[31/12/99 01:54:42.890200] [361] [ap] [I][IMS-FW]: [SIP]< SIP/2.0 401 Unauthorized",
    "[31/12/99 01:54:42.891000] [361] [ap] [I][IMS-FW]: Call-ID: VV_Rly@host",
    "[31/12/99 01:54:42.892000] [361] [ap] [I][IMS-FW]: CSeq: 1 REGISTER",
    '[31/12/99 01:54:42.893000] [361] [ap] [I][IMS-FW]: WWW-Authenticate: Digest realm="ims",',
    "[31/12/99 01:54:42.894000] [361] [ap] [I][IMS-FW]:    algorithm=AKAv1-MD5,",
    "[31/12/99 01:54:42.895000] [361] [ap] [I][IMS-FW]: Content-Length: 0",
    "[31/12/99 01:54:42.896000] [361] [ap] [I][IMS-FW]: ",
    "[31/12/99 01:54:42.897000] [361] [ap] [I][IMS-FW]",
    "[31/12/99 01:54:42.898000] [361] [ap] [W][IMS6.0]: KA-MGR: evt(94)",
]


def test_full_format_messages_are_grouped_across_interleaved_tasks():
    msgs = parse_sip_messages(parse_lines(FULL_FORMAT))

    assert [m["method_code"] for m in msgs] == ["INVITE", "486 Busy Here"]
    invite, busy = msgs
    assert invite["is_outgoing"] is True and invite["direction"] == "Tx ⬆️"
    assert invite["cseq"] == "1 INVITE" and invite["call_id"] == "abc@host"
    assert invite["codecs"] == ["AMR-WB/16000", "AMR/8000"]
    assert invite["key_headers"] == {"P-Early-Media": "supported"}
    assert invite["time"] == "12-31 02:39:02.182800"
    assert busy["is_outgoing"] is False and busy["is_error"] is True
    assert busy["key_headers"]["Reason"] == "SIP;cause=486"


def test_short_format_with_folded_header_and_auth_challenge():
    msgs = parse_sip_messages(parse_lines(SHORT_FORMAT))

    assert len(msgs) == 1
    challenge = msgs[0]
    assert challenge["method_code"] == "401 Unauthorized"
    assert challenge["is_error"] is False and challenge["is_auth_challenge"] is True
    assert challenge["cseq"] == "1 REGISTER"
    assert challenge["header_count"] == 4


def test_short_format_tags_are_not_mistaken_for_level_letters():
    rec = parse_lines(["[31/12/99 01:54:42.898000] [361] [ap] [W][IMS6.0]: KA-MGR: evt(94)"])[0]
    assert (rec.tag, rec.level) == ("IMS6.0", "W")
