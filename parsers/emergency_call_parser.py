"""긴급호(E911/112) 시도 파서.

일반 통화 파서는 긴급호를 그냥 MO 발신으로 본다. 그래서 실패해도 남는 것이
`IMS_CALL_START_FAILED` 한 줄인데, 긴급호는 실패했을 때 봐야 할 것이 다르다:

흐름은 이렇다: Dial 에서 발신 -> 긴급번호 리스트에 있는지 확인 -> 긴급호로
`EMERGENCY_SEARCH` 를 내림 -> 그 응답을 모뎀이 주기도 하고 RIL 이 특정 케이스는
바로 CS 로 올리기도 함 -> 내려온 값에 따라 CS 긴급호나 PS(IMS) 긴급호로 진행.
그래서 봐야 하는 것은:

* 번호가 긴급번호로 인식됐는지 (`Found in mEmergencyNumberList`, `ecclistFromDatabase`)
* 어느 도메인으로 걸라고 했는지, 누가 그렇게 정했는지
  (`EMERGENCY_SEARCH {CS(1)}` / `{VoLTE(0)}`, RatDeterminer)
* 실제로 어느 쪽으로 진행됐는지 (IMS `makeCall` / CS `> EMERGENCY_DIAL`), 도중에
  도메인을 옮겨 다시 걸었는지 (`redialToCs`, `redialToIms`)
* 그때 단말이 정상 서비스였는지 (`mIsEmergencyOnly`, `combinedRegState`)
* 모뎀의 긴급호 상태 기계가 어디까지 갔는지 (`EmergencyControl`, `E911 progress`)
* 긴급 PDN(APN `sos`) 이 올라왔는지 (`SETUP_DATA_CALL` 요청/응답)
* 붙은 뒤 어떻게 끊겼는지 (`LAST_CALL_FAIL_CAUSE`)
* 도중에 모뎀이 죽지 않았는지 (`All Service is closed, Modem Reset!!`)

이 흐름을 "시도" 하나로 묶는다. IMS 로 걸어 보고 안 되면 CS 로 다시 거는 것은
사용자에게 한 번의 긴급호이므로, 앞 이벤트에서 시간이 얼마 지나지 않았으면 같은
시도로 이어 붙인다.
"""

import re

from core.constants import RE_TIME
from core.telephony_constants import CALL_FAIL_REASON_MAP, VENDER_FAIL_REASON_MAP

# 시도를 여는 줄. 모두 발신 직후 몇 ms 안에 붙어 나오므로 무엇이 먼저 걸려도 된다.
# `EMERGENCY_SEARCH` 요청이 보통 제일 앞이다 -- 모뎀에 "어느 도메인으로 걸까"를
# 묻는 줄이라, 이것을 놓치면 시도 시작 시각이 라우팅 결과 시각으로 밀린다.
_SEARCH_REQUEST = re.compile(r'>\s*EMERGENCY_SEARCH')
_ROUTE = re.compile(r'Emergency Search: Route to\s+(?P<route>[^.\s]+)')
_CALL_INFO = re.compile(r'setEmergencyCallInfo\s*\{\s*(?P<number>[^/}\s]+)(?:/(?P<category>\d+))?')
_CONTROL_DIALED = re.compile(r'EMERGENCY_CONTROL command:\s*DIALED')

# 슬롯은 RILJ 의 `[PHONE0]` 이나 태그 뒤의 `[0]` 으로 찍힌다. 뒤쪽 형태는 RILJ
# 일련번호(`[1277]> EMERGENCY_SEARCH`)와 모양이 겹치므로, 한 자리 수에 화살표가
# 따라오지 않는 것만 슬롯으로 본다.
_SLOT = re.compile(r'\[PHONE(?P<phone>\d+)\]|:\s*\[(?P<slot>\d)\](?![><])')
_RAT = re.compile(r'Emergency call rat:\s*(?P<rat>\S+)')
_DOMAIN = re.compile(r'latestDomain=(?P<domain>\w+)')
_ECC_CATEGORY = re.compile(r'eccCategory:\s*(?P<category>\d+)')
# IMS 통화 세션 키와 CS 쪽 키. 통화 이력의 세션과 이어 붙이는 데 쓴다.
_IMS_OBJ = re.compile(r'\[ImsCall objId:\s*(?P<obj>\d+)')
_CS_OBJ = re.compile(r'New Connection \(DialString\):.*?objId:\s*(?P<obj>\d+)')
_TELECOM_ID = re.compile(r'telecomCallID:\s*(?P<telecom>TC@[\w]+)')

# 도메인 선택. 모뎀이 고른 결과(`{CS(1)}`)와 무엇을 보고 골랐는지(RatDeterminer).
_SEARCH_RESULT = re.compile(r'EMERGENCY_SEARCH\s*\{(?P<result>[^}]+)\}')
_SEARCH_EVENT = re.compile(r'EMERGENCY_SEARCH_RESULT - search result:\s*(?P<result>\S+)')
# 결과 자체가 괄호를 품는다(`CS(1)`). `[^)]+` 로 끊으면 `CS(1` 이 된다.
_SEARCH_COMPLETE = re.compile(r'Complete search request \(result:\s*(?P<result>\w+(?:\(\w*\))?)')
_RAT_DETERMINER = re.compile(
    r'SelectE911RatDeterminer - Type:\s*(?P<type>\w+)(?:\s*\(Reason:\s*(?P<reason>[^)]+)\))?'
)
# 도메인을 옮겨 다시 거는 단계. Search 결과는 CS 말고 VoLTE·VoWiFi 도 오므로
# 대상을 함수 이름에서 그대로 읽는다(`redialToCs`, `redialToIms`, ...). 붙어 있는
# ImsReasonInfo 가 그 이유다.
_REDIAL = re.compile(r'redialTo(?P<target>[A-Za-z]+)\b')
_CS_DIAL = re.compile(r'>\s*EMERGENCY_DIAL')
# IMS(PS) 쪽으로 걸린 표식. `makeCall` 까지 갔거나, 적어도 IMS 통화 목록에 긴급호로
# 올라간 것. CS 로 다시 걸린 로그도 여기까지는 지나가므로 폴백을 읽는 근거가 된다.
_IMS_DIAL = re.compile(r'\[IPCT\]>\s*makeCall|setImsCallList.*_emergency')
# 발신 번호가 긴급번호로 인식됐는지. 이 확인이 없으면 긴급호로 걸리지 않는다.
_ECC_MATCHED = "Found in mEmergencyNumberList"
_ECC_LIST = re.compile(r'ecclistFromDatabase:\s*(?P<numbers>[\d,]+)')

_CONTROL_STATE = re.compile(
    r'EmergencyControl - state:\s*(?P<state>[A-Z_]+\(\d+\)),\s*command:\s*(?P<command>[A-Z_]+)'
)
_E911_PROGRESS = re.compile(r'E911 progress:\s*(?P<progress>[A-Z_]+\(\d+\))')
_SERVICE_STATE = re.compile(r'combinedRegState=(?P<state>[A-Z_]+)')
_EMERGENCY_ONLY = re.compile(r'mIsEmergencyOnly=(?P<value>true|false)')
_IMS_BARRING = re.compile(r'imsEmergencyCallBarring:\s*(?P<value>\d+)')
_VOLTE_911 = re.compile(r'VOLTE_911_CALL,\s*V:\s*(?P<value>\d+)')
_IMS_REASON = re.compile(r'ImsReasonInfo\s*::\s*\{\s*(?P<code>\d+)\s*:\s*(?P<name>[A-Z_0-9]+)')

# 붙은 통화가 어떻게 끝났는지. CS 는 여기에만 원인이 남는다.
_DISCONNECT = re.compile(r'onDisconnect: cause=(?P<cause>\d+)')
_LAST_FAIL = re.compile(
    r'LAST_CALL_FAIL_CAUSE.*?causeCode:\s*(?P<cause>\d+)(?:\s+vendorCause:\s*(?P<vendor>\d+))?'
)
# 정상 종료로 보는 종료 원인. 나머지는 붙었다가 끊긴 것으로 본다.
_NORMAL_END_CAUSES = {"16"}

# 긴급 PDN. 요청과 응답은 RILJ 일련번호로 짝지어야 한다 -- 응답 줄에는 APN 이
# 없고, 이 덤프에서는 응답이 8초 뒤 수백 줄 아래에 온다.
_PDN_REQUEST = re.compile(r'\[(?P<seq>\d+)\]>\s*SETUP_DATA_CALL')
_PDN_RESPONSE = re.compile(
    r'\[(?P<seq>\d+)\]<\s*SETUP_DATA_CALL\s+DataCallResponse:\s*\{\s*cause=(?P<cause>\S+)'
)
_EMERGENCY_APN = ("dnn=sos", "mDnn=sos", "apn:sos", "EIMS", "lte_emergency")
# 실패로 볼 수 없는 응답. cause 는 성공일 때 NONE(0x0) 으로 찍힌다.
_PDN_OK = ("NONE", "0x0")

_MODEM_RESET = "All Service is closed, Modem Reset!!"
_START_FAILED = "onCallStartFailed"
# 붙었다고 볼 수 있는 표시. CS 는 GET_CURRENT_CALLS 목록에 ACTIVE 로 나온다.
_CONNECTED = ("state:ACTIVE", ",ACTIVE,", "onCallStarted")

# 볼 가치가 있는 줄을 값싸게 고르는 표식. 위 정규식들이 찾는 줄을 모두 덮어야
# 한다. `mergency`/`MERGENCY` 두 벌인 이유는 로그가 두 표기를 다 쓰기 때문이다
# (`setEmergencyCallInfo` 와 `EMERGENCY_CONTROL`).
_INTERESTING = (
    "mergency",
    "MERGENCY",
    "E911",
    "SETUP_DATA_CALL",
    _MODEM_RESET,
    "combinedRegState",
    "ImsReasonInfo",
    _START_FAILED,
    "ImsCall objId",
    "VOLTE_911_CALL",
    "New Connection",
    # IMS 발신 줄. 긴급호면 `emergencyServiceCategories` 같은 항목이 같이 찍혀
    # 위의 표식으로도 걸리지만, 그 항목이 없는 형태도 있어 따로 둔다.
    "makeCall",
    "onDisconnect: cause=",
    "LAST_CALL_FAIL_CAUSE",
) + _CONNECTED

# 시도 사이를 가르는 간격. 이보다 짧게 이어지는 재발신은 같은 긴급호로 본다.
_ATTEMPT_GAP_SEC = 60
# 한 시도에 담을 로그 줄 수. 긴급호 구간은 RILD raw dump 로 수천 줄이 된다.
_MAX_LOGS = 40


def _to_seconds(timestamp: str) -> int:
    """`MM-DD HH:MM:SS.mmm` 를 비교용 초로. 날짜가 바뀌어도 간격 판단에는 충분하다."""
    try:
        date_part, clock = timestamp.split(" ")
        month, day = (int(part) for part in date_part.split("-"))
        hour, minute, second = (int(part) for part in clock.split(".")[0].split(":"))
        return ((month * 31 + day) * 24 + hour) * 3600 + minute * 60 + second
    except (ValueError, AttributeError):
        return 0


def _end_cause_text(cause: str, vendor: str) -> str:
    """종료 원인을 부르는 이름과 함께. 숫자만 적어 두면 읽는 사람이 또 찾아야 한다."""
    if not cause:
        return ""
    text = f"causeCode {cause}"
    if named := CALL_FAIL_REASON_MAP.get(cause):
        text += f"({named})"
    if vendor:
        text += f", vendorCause {vendor}"
        if named_vendor := VENDER_FAIL_REASON_MAP.get(vendor):
            text += f"({named_vendor})"
    return text


class EmergencyCallParser:
    """긴급호 시도를 하나씩 묶어 낸다."""

    def __init__(self, context_getter=None):
        self.get_context_fn = context_getter

    def analyze(self, lines):
        attempts = []
        current = None
        # 응답이 시도가 닫힌 뒤에 와도 제 시도에 붙도록 일련번호로 들고 있는다.
        pdn_requests = {}

        for line in lines:
            # 이 파서는 dumpState 전체(수억 줄)를 받는다. 정규식을 모든 줄에 대고
            # 돌리면 그것만으로 분석 시간이 늘어나므로, 먼저 값싼 문자열 검사로
            # 볼 가치가 있는 줄만 고른다.
            if not self._is_interesting(line):
                continue

            clean = line.strip()
            timestamp = self._timestamp(clean)

            if self._is_dial(clean):
                if current is None or self._gap(current, timestamp) > _ATTEMPT_GAP_SEC:
                    current = self._new_attempt(timestamp, clean)
                    attempts.append(current)
            elif current is not None and self._gap(current, timestamp) > _ATTEMPT_GAP_SEC:
                # 한참 뒤에 나온 줄은 지난 시도의 것이 아니다. 그대로 이어 붙이면
                # 몇 분 뒤의 모뎀 리셋이 끝난 긴급호의 실패 사유가 된다.
                current = None

            self._read_pdn(clean, timestamp, current, pdn_requests)

            if current is None:
                continue

            self._read_attempt_fields(clean, timestamp, current)

        for attempt in attempts:
            self._finalize(attempt)
        return attempts

    @staticmethod
    def _is_interesting(line):
        return any(marker in line for marker in _INTERESTING)

    # ------------------------------------------------------------------ 시도 열기

    @staticmethod
    def _is_dial(line):
        return bool(
            _SEARCH_REQUEST.search(line)
            or _ROUTE.search(line)
            or _CALL_INFO.search(line)
            or _CONTROL_DIALED.search(line)
        )

    @staticmethod
    def _new_attempt(timestamp, line):
        slot_match = _SLOT.search(line)
        slot = "Unknown"
        if slot_match:
            slot = slot_match.group("slot") or slot_match.group("phone") or "Unknown"
        return {
            "time": timestamp,
            "end_time": timestamp,
            "slot": slot,
            "number": "Unknown",
            "ecc_category": "",
            "route": "Unknown",
            "rat": "Unknown",
            "domain": "Unknown",
            "search_result": "",
            "rat_determiner": "",
            # IMS 에서 CS 로 다시 거는 단계들. 한 시도 안에서 순서대로 쌓인다.
            "redials": [],
            "cs_dialed_at": "",
            "ims_dialed_at": "",
            "dialed_domain": "",
            "ecc_list_matched": False,
            "ecc_list": "",
            "ims_obj_id": "",
            "cs_obj_id": "",
            "telecom_call_id": "",
            "service_state": "Unknown",
            "emergency_only": "",
            "ims_emergency_barring": "",
            "volte_911_config": "",
            "emergency_control": [],
            "e911_progress": [],
            "emergency_pdn": {},
            "modem_reset": False,
            "ims_fail_reason": "",
            "disconnect_cause": "",
            "end_cause": "",
            "end_vendor_cause": "",
            "end_cause_text": "",
            "start_failed": False,
            "connected": False,
            "status": "UNKNOWN",
            "fallback": "",
            "fail_reason": "",
            "root_cause_candidate": "",
            "logs": [],
        }

    def _timestamp(self, line):
        match = RE_TIME.search(line)
        return match.group(0) if match else ""

    @staticmethod
    def _gap(attempt, timestamp):
        if not timestamp or not attempt.get("end_time"):
            return 0
        return abs(_to_seconds(timestamp) - _to_seconds(attempt["end_time"]))

    # ------------------------------------------------------------- 시도 채우기

    def _read_attempt_fields(self, line, timestamp, attempt):
        touched = False

        if route := _ROUTE.search(line):
            attempt["route"] = route.group("route")
            touched = True
        if info := _CALL_INFO.search(line):
            attempt["number"] = info.group("number")
            if info.group("category") is not None:
                attempt["ecc_category"] = info.group("category")
            touched = True
        if rat := _RAT.search(line):
            attempt["rat"] = rat.group("rat")
            touched = True
        if domain := _DOMAIN.search(line):
            attempt["domain"] = domain.group("domain")
            touched = True
        if category := _ECC_CATEGORY.search(line):
            attempt["ecc_category"] = category.group("category")
            touched = True
        touched = self._read_domain_selection(line, attempt) or touched
        touched = self._read_keys(line, attempt) or touched
        if control := _CONTROL_STATE.search(line):
            self._append_unique(
                attempt["emergency_control"],
                f"{control.group('state')}/{control.group('command')}",
            )
            touched = True
        if progress := _E911_PROGRESS.search(line):
            self._append_unique(attempt["e911_progress"], progress.group("progress"))
            touched = True
        # 서비스 상태는 발신 시점의 것이 필요하다. 뒤로 갈수록 상태가 바뀌므로
        # 시도 안에서 처음 본 값을 남긴다.
        if attempt["service_state"] == "Unknown":
            if state := _SERVICE_STATE.search(line):
                attempt["service_state"] = state.group("state")
                touched = True
        if not attempt["emergency_only"]:
            if only := _EMERGENCY_ONLY.search(line):
                attempt["emergency_only"] = only.group("value")
                touched = True
        if not attempt["ims_emergency_barring"]:
            if barring := _IMS_BARRING.search(line):
                attempt["ims_emergency_barring"] = barring.group("value")
                touched = True
        if not attempt["volte_911_config"]:
            if config := _VOLTE_911.search(line):
                attempt["volte_911_config"] = config.group("value")
                touched = True
        touched = self._read_end(line, attempt) or touched
        if _START_FAILED in line:
            attempt["start_failed"] = True
            touched = True
        if _MODEM_RESET in line:
            attempt["modem_reset"] = True
            touched = True
        if any(marker in line for marker in _CONNECTED):
            attempt["connected"] = True
            touched = True

        if touched:
            self._record(attempt, timestamp, line)

    def _read_domain_selection(self, line, attempt):
        """모뎀이 고른 도메인과 CS 재발신 단계."""
        touched = False

        result = _SEARCH_RESULT.search(line) or _SEARCH_EVENT.search(line) or _SEARCH_COMPLETE.search(line)
        if result and not attempt["search_result"]:
            attempt["search_result"] = result.group("result").strip()
            touched = True
        if determiner := _RAT_DETERMINER.search(line):
            reason = determiner.group("reason")
            attempt["rat_determiner"] = (
                f"{determiner.group('type')} ({reason.strip()})" if reason else determiner.group("type")
            )
            touched = True
        if redial := _REDIAL.search(line):
            # 재발신 사유는 같은 줄의 ImsReasonInfo 다. 코드 0(CODE_UNSPECIFIED)은
            # 실패가 아니라 "사유 없음" 이므로 붙이지 않는다.
            reason = _IMS_REASON.search(line)
            step = redial.group("target").upper()
            if reason and reason.group("code") != "0":
                step += f" (ImsReasonInfo {reason.group('code')}:{reason.group('name')})"
            self._append_unique(attempt["redials"], step)
            touched = True
        elif reason := _IMS_REASON.search(line):
            # 코드 0 은 사유가 없다는 뜻이라 실패로 적으면 안 된다.
            if reason.group("code") != "0":
                attempt["ims_fail_reason"] = f"{reason.group('code')}:{reason.group('name')}"
                touched = True
        if _CS_DIAL.search(line) and not attempt["cs_dialed_at"]:
            attempt["cs_dialed_at"] = self._timestamp(line)
            touched = True
        if _IMS_DIAL.search(line) and not attempt["ims_dialed_at"]:
            attempt["ims_dialed_at"] = self._timestamp(line)
            touched = True
        # 긴급번호 확인은 통화 내내 다시 찍힌다. 처음 본 것만 남겨야 시도의 끝
        # 시각이 그 반복에 끌려 늘어나지 않는다.
        if not attempt["ecc_list_matched"] and _ECC_MATCHED in line:
            attempt["ecc_list_matched"] = True
            touched = True
        if not attempt["ecc_list"]:
            if ecc := _ECC_LIST.search(line):
                attempt["ecc_list"] = ecc.group("numbers")
                touched = True

        return touched

    @staticmethod
    def _read_keys(line, attempt):
        """통화 이력과 이어 붙일 키들(IMS objId, CS objId, telecom call id)."""
        touched = False
        if obj := _IMS_OBJ.search(line):
            attempt["ims_obj_id"] = obj.group("obj")
            touched = True
        if cs_obj := _CS_OBJ.search(line):
            attempt["cs_obj_id"] = cs_obj.group("obj")
            touched = True
        if not attempt["telecom_call_id"]:
            if telecom := _TELECOM_ID.search(line):
                attempt["telecom_call_id"] = telecom.group("telecom")
                touched = True
        return touched

    @staticmethod
    def _read_end(line, attempt):
        """붙은 통화의 종료 원인. 먼저 본 것만 남긴다 -- 뒤의 다른 통화 것을
        덮어쓰면 끊긴 이유가 바뀐다.
        """
        touched = False
        if not attempt["disconnect_cause"]:
            if disconnect := _DISCONNECT.search(line):
                attempt["disconnect_cause"] = disconnect.group("cause")
                touched = True
        if not attempt["end_cause"]:
            if last_fail := _LAST_FAIL.search(line):
                attempt["end_cause"] = last_fail.group("cause")
                attempt["end_vendor_cause"] = last_fail.group("vendor") or ""
                attempt["end_cause_text"] = _end_cause_text(
                    attempt["end_cause"], attempt["end_vendor_cause"]
                )
                touched = True
        return touched

    def _read_pdn(self, line, timestamp, current, pdn_requests):
        """긴급 PDN 요청/응답. 요청은 지금 열린 시도에, 응답은 그 요청의 시도에."""
        if request := _PDN_REQUEST.search(line):
            if current is not None and any(marker in line for marker in _EMERGENCY_APN):
                current["emergency_pdn"] = {
                    "apn": "sos",
                    "requested_at": timestamp,
                    "status": "PENDING",
                }
                pdn_requests[request.group("seq")] = current
                self._record(current, timestamp, line)
            return

        if response := _PDN_RESPONSE.search(line):
            attempt = pdn_requests.pop(response.group("seq"), None)
            if attempt is None:
                return
            cause = response.group("cause")
            ok = any(marker in cause.upper() for marker in _PDN_OK)
            attempt["emergency_pdn"].update({
                "answered_at": timestamp,
                "cause": cause,
                "status": "OK" if ok else "FAIL",
            })
            self._record(attempt, timestamp, line)

    @staticmethod
    def _append_unique(values, value):
        """상태 기계 로그는 같은 값을 계속 다시 찍는다. 바뀔 때만 남긴다."""
        if not values or values[-1] != value:
            values.append(value)

    @staticmethod
    def _record(attempt, timestamp, line):
        if timestamp:
            attempt["end_time"] = timestamp
        if len(attempt["logs"]) < _MAX_LOGS:
            attempt["logs"].append(f"[{timestamp}] {line}" if timestamp else line)

    # ---------------------------------------------------------------- 판정

    def _finalize(self, attempt):
        pdn = attempt.get("emergency_pdn") or {}
        connected = attempt["connected"]
        abnormal_end = bool(attempt["end_cause"]) and attempt["end_cause"] not in _NORMAL_END_CAUSES
        reasons = []
        causes = []

        # 붙지도 못한 경우에만 IMS 실패가 이 긴급호의 실패 사유다. CS 로 다시 걸어
        # 붙었으면 그것은 폴백 과정이지 결과가 아니다.
        if not connected:
            if attempt["ims_fail_reason"]:
                reasons.append(f"IMS 실패({attempt['ims_fail_reason']})")
                causes.append(f"IMS_{attempt['ims_fail_reason'].split(':')[0]}")
            elif attempt["start_failed"]:
                reasons.append("IMS 통화 시작 실패(onCallStartFailed)")
                causes.append("IMS_CALL_START_FAILED")

            if pdn.get("status") == "FAIL":
                reasons.append(f"긴급 PDN(sos) 설정 실패(cause={pdn.get('cause')})")
                causes.append("EMERGENCY_PDN_SETUP_FAILED")
            elif pdn.get("status") == "PENDING":
                reasons.append("긴급 PDN(sos) 응답 없음")
                causes.append("EMERGENCY_PDN_NO_RESPONSE")

        if abnormal_end:
            reasons.append(f"통화 종료({attempt['end_cause_text']})")
            causes.append(f"CALL_END_{attempt['end_cause']}")
            if vendor := attempt["end_vendor_cause"]:
                causes.append(f"VENDOR_{vendor}")

        if attempt["modem_reset"]:
            reasons.append("통화 중 모뎀 리셋")
            causes.append("MODEM_RESET")

        # 정상 서비스가 아니었다는 것 자체로 실패는 아니지만(긴급호는 그 상태에서
        # 걸도록 되어 있다), 왜 실패했는지의 앞자리에 놓을 사실이다.
        if attempt["emergency_only"] == "true" or attempt["service_state"] in {
            "OUT_OF_SERVICE",
            "EMERGENCY_ONLY",
        }:
            causes.append("NO_NORMAL_SERVICE_EMERGENCY_ONLY")

        if connected:
            # 붙은 뒤 비정상 종료거나 모뎀이 죽었으면 드롭이다.
            attempt["status"] = "CALL DROP" if reasons else "SUCCESS"
        elif reasons:
            attempt["status"] = "FAIL"
        else:
            attempt["status"] = "UNKNOWN"

        attempt["dialed_domain"] = self._dialed_domain(attempt)
        attempt["fallback"] = self._fallback_text(attempt)
        attempt["fail_reason"] = ", ".join(reasons)
        attempt["root_cause_candidate"] = ", ".join(causes)
        attempt.pop("start_failed", None)
        attempt.pop("connected", None)

    @staticmethod
    def _dialed_domain(attempt):
        """실제로 어느 쪽으로 진행됐는지. Search 가 내려 준 값과 다를 수 있다."""
        ims = bool(attempt["ims_dialed_at"])
        cs = bool(attempt["cs_dialed_at"])
        if ims and cs:
            return "PS → CS"
        if cs:
            return "CS"
        if ims:
            return "PS"
        return ""

    @staticmethod
    def _fallback_text(attempt):
        """도메인이 어떻게 옮겨 갔는지 한 줄로. 실패든 성공이든 알아야 하는 흐름이다.

        출발 도메인은 `latestDomain` 이 있으면 그것을 쓴다. 없을 때 CS 재발신만
        "IMS" 로 적는데, `redialToCs` 는 IMS 쪽에서 부르는 함수라서 그렇다. 다른
        대상은 어디서 옮겨 왔는지 로그로 확인된 바가 없어 짐작하지 않는다.
        """
        steps = attempt["redials"]
        if not steps:
            return ""
        start = attempt["domain"] if attempt["domain"] != "Unknown" else ""
        if not start and steps[0].startswith("CS"):
            start = "IMS"
        return " → ".join(([start] if start else []) + steps)
