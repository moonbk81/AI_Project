import re

from parsers.call.ims_call_parser import ImsCallParser


def _extract_timestamp(line: str) -> str:
    match = re.search(r"(\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2}\.\d{3})", line)
    return f"{match.group(1)} {match.group(2)}" if match else "UnknownTime"


def test_mo_start_failed_keeps_pre_objid_start_events():
    lines = [
        "05-23 15:38:55.971 radio  2818  2818 D ImsPhoneCallTracker: [0] dial - initialCallNetworkType: 13",
        "05-23 15:38:55.975 radio  2818  2818 D IPF     : [IPCT]> makeCall {<MASKED>}",
        "05-23 15:38:55.983 radio  2818  2818 D IPF     : [IPCT]< makeCall {[ImsCall objId:7705404 session:[ImsCallSession objId:123 callId:[UNINITIALIZED]]]}",
        "05-23 15:38:56.456 radio  2818  2818 D IPF     : [IPCT]< CallListener.onCallStartFailed {ImsReasonInfo :: {369 : CODE_SIP_REQUEST_URI_TOO_LARGE, 414, REQUEST URI TOO LARGE}, [ImsCall objId:7705404]}",
        "05-23 15:38:56.456 radio  2818  2818 D ImsPhoneCallTracker: [0] onCallStartFailed - Use dialing connection. [telecomCallID: TC@42_1 objId: 235677200 incoming: false state: DIALING]",
        "05-23 15:38:56.487 radio  2818  2818 D ImsPhoneCallTracker: [0] processCallStateChange [ImsCall objId:7705404] state=DISCONNECTED cause=36",
        "05-23 15:38:56.528 radio  2818  2818 D IPF     : [IPCN]> close {[ImsCall objId:7705404]}",
    ]

    sessions = ImsCallParser(_extract_timestamp).parse(lines)

    assert len(sessions) == 1
    session = sessions[0]
    assert session["direction"] == "MO"
    assert session["start_time"] == "05-23 15:38:55.971"
    assert session["end_time"] == "05-23 15:38:56.528"
    assert session["status"] == "FAIL"
    assert session["fail_reason"] == "369_CODE_SIP_REQUEST_URI_TOO_LARGE (SIP_414_REQUEST URI TOO LARGE)"
    assert session["release_reason"] == "0"
    assert all("objId:235677200" not in item for item in session["logs"])


def test_user_release_is_not_reported_as_fail_reason():
    lines = [
        "03-13 11:27:43.018 radio  1793  2632 D IPF     : [IPCT2]< onIncomingCall {callId: null}",
        "03-13 11:27:43.023 radio  1793  1793 D IPF     : [IPCT2]< takeCall {[ImsCall objId:56711474]}",
        "03-13 11:27:44.271 radio  1793  1793 D IPF     : [IPCT2]> reject {reason: USER_DECLINE, [ImsCall objId:56711474]}",
        "03-13 11:27:44.362 radio  1793  1793 D IPF     : [IPCT2]< CallListener.onCallTerminated {ImsReasonInfo :: {501 : CODE_USER_TERMINATED, 200, null}, [ImsCall objId:56711474]}",
        "03-13 11:27:44.421 radio  1793  1793 D IPF     : [IPCN2]> close {[ImsCall objId:56711474]}",
    ]

    session = ImsCallParser(_extract_timestamp).parse(lines)[0]

    assert session["direction"] == "MT"
    assert session["status"] == "NORMAL_RELEASE"
    assert session["is_user_reject"] is True
    assert session["fail_reason"] == "0"
    assert session["release_reason"] == "501_CODE_USER_TERMINATED (SIP_200_null)"


def test_sip_480_user_decline_is_reported_as_failed_call():
    lines = [
        "04-09 12:06:54.097 radio  4210  4210 D ImsPhoneCallTracker: [0] dial - initialCallNetworkType: 13",
        "04-09 12:06:54.099 radio  4210  4210 D IPF     : [IPCT]> makeCall {642609099}",
        "04-09 12:06:54.101 radio  4210  4210 D IPF     : [IPCT]< makeCall {[ImsCall objId:87635467 session:[ImsCallSession objId:144249306 callId:[UNINITIALIZED]]]}",
        "04-09 12:07:00.876 radio  4210  4210 D IPF     : [IPCT]< CallListener.onCallTerminated {ImsReasonInfo :: {504 : CODE_USER_DECLINE, 480, Temporarily Unavailable}, [ImsCall objId:87635467]}",
        "04-09 12:07:00.878 radio  4210  4210 D ImsPhoneCallTracker: [0] processCallStateChange [ImsCall objId:87635467] state=DISCONNECTED cause=36",
        "04-09 12:07:00.881 radio  4210  4210 D IPF     : [IPCN]> close {[ImsCall objId:87635467]}",
    ]

    session = ImsCallParser(_extract_timestamp).parse(lines)[0]

    assert session["status"] == "FAIL"
    assert session["is_user_reject"] is False
    assert session["fail_reason"] == "504_CODE_USER_DECLINE (SIP_480_Temporarily Unavailable)"
    assert session["release_reason"] == "0"


def test_sip_480_in_release_reason_is_still_reported_as_failed_call():
    parser = ImsCallParser(_extract_timestamp)

    session = parser.build_session(
        obj_id="87635467",
        events=[
            "[04-09 12:06:54.097] dial - initialCallNetworkType: 13",
            "[04-09 12:07:00.876] CallListener.onCallTerminated {ImsReasonInfo :: {504 : CODE_USER_DECLINE, 480, Temporarily Unavailable}, [ImsCall objId:87635467]}",
        ],
        tc_id="TC@4_1",
        fail_reason="",
        release_reason="504_CODE_USER_DECLINE (SIP_480_Temporarily Unavailable)",
        direction="MO",
        ims_bracket_re=re.compile(r"ImsReasonInfo\s*::\s*\{\s*(\d+)\s*:\s*([A-Z_0-9]+)"),
        ims_standard_re=re.compile(r"ImsReasonInfo\s*(?:[:\s\(\=]+code\=)?[:\s\(\=]*(\d+)", re.IGNORECASE),
    )

    assert session["status"] == "FAIL"
    assert session["is_user_reject"] is False
    assert session["fail_reason"] == "504_CODE_USER_DECLINE (SIP_480_Temporarily Unavailable)"
    assert session["release_reason"] == "0"
