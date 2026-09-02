"""긴급호(E911/112) 시도 파서.

일반 통화 파서는 긴급호를 그냥 MO 발신으로 본다. 그래서 실패해도 남는 것이
`IMS_CALL_START_FAILED` 한 줄인데, 긴급호는 실패했을 때 봐야 할 것이 다르다:

* 어느 도메인/RAT 으로 걸었는지 (`Emergency Search: Route to VoLTE`, `latestDomain=PS`)
* 그때 단말이 정상 서비스였는지 (`mIsEmergencyOnly=true`, `combinedRegState`)
* 모뎀의 긴급호 상태 기계가 어디까지 갔는지 (`EmergencyControl`, `E911 progress`)
* 긴급 PDN(APN `sos`) 이 올라왔는지 (`SETUP_DATA_CALL` 요청/응답)
* 도중에 모뎀이 죽지 않았는지 (`All Service is closed, Modem Reset!!`)

이 흐름을 "시도" 하나로 묶는다. IMS 로 걸어 보고 안 되면 CS 로 다시 거는 식의
재시도는 사용자에게 한 번의 긴급호이므로, 앞 이벤트에서 시간이 얼마 지나지
않았으면 같은 시도로 이어 붙인다.
"""

import re

from core.constants import RE_TIME

# 시도를 여는 줄. 셋 다 발신 직후 몇 ms 안에 붙어 나오므로 무엇이 먼저 걸려도 된다.
_ROUTE = re.compile(r'Emergency Search: Route to\s+(?P<route>[^.\s]+)')
_CALL_INFO = re.compile(r'setEmergencyCallInfo\s*\{\s*(?P<number>[^/}\s]+)(?:/(?P<category>\d+))?')
_CONTROL_DIALED = re.compile(r'EMERGENCY_CONTROL command:\s*DIALED')

# 슬롯은 태그 뒤의 `[0]` 이나 RILJ 의 `[PHONE0]` 으로 찍힌다.
_SLOT = re.compile(r':\s*\[(?P<slot>\d+)\]|\[PHONE(?P<phone>\d+)\]')
_RAT = re.compile(r'Emergency call rat:\s*(?P<rat>\S+)')
_DOMAIN = re.compile(r'latestDomain=(?P<domain>\w+)')
_ECC_CATEGORY = re.compile(r'eccCategory:\s*(?P<category>\d+)')
# IMS 통화 세션 키. 통화 이력의 `objId:...` 와 맞춰 같은 통화임을 잇는다.
_IMS_OBJ = re.compile(r'\[ImsCall objId:\s*(?P<obj>\d+)')
_CONTROL_STATE = re.compile(
    r'EmergencyControl - state:\s*(?P<state>[A-Z_]+\(\d+\)),\s*command:\s*(?P<command>[A-Z_]+)'
)
_E911_PROGRESS = re.compile(r'E911 progress:\s*(?P<progress>[A-Z_]+\(\d+\))')
_SERVICE_STATE = re.compile(r'combinedRegState=(?P<state>[A-Z_]+)')
_EMERGENCY_ONLY = re.compile(r'mIsEmergencyOnly=(?P<value>true|false)')
_VOLTE_911 = re.compile(r'VOLTE_911_CALL,\s*V:\s*(?P<value>\d+)')
_IMS_REASON = re.compile(r'ImsReasonInfo\s*::\s*\{\s*(?P<code>\d+)\s*:\s*(?P<name>[A-Z_0-9]+)')

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
# 붙었다고 볼 수 있는 표시. 여기까지 왔으면 실패로 적지 않는다.
_CONNECTED = ("state:ACTIVE", "onCallStarted", ",ACTIVE,")

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
        return bool(_ROUTE.search(line) or _CALL_INFO.search(line) or _CONTROL_DIALED.search(line))

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
            "ims_obj_id": "",
            "service_state": "Unknown",
            "emergency_only": "",
            "volte_911_config": "",
            "emergency_control": [],
            "e911_progress": [],
            "emergency_pdn": {},
            "modem_reset": False,
            "ims_fail_reason": "",
            "start_failed": False,
            "connected": False,
            "status": "UNKNOWN",
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
        if obj := _IMS_OBJ.search(line):
            attempt["ims_obj_id"] = obj.group("obj")
            touched = True
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
        if not attempt["volte_911_config"]:
            if config := _VOLTE_911.search(line):
                attempt["volte_911_config"] = config.group("value")
                touched = True
        if reason := _IMS_REASON.search(line):
            attempt["ims_fail_reason"] = f"{reason.group('code')}:{reason.group('name')}"
            touched = True
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
        reasons = []
        causes = []

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

        if reasons:
            attempt["status"] = "FAIL"
        elif attempt["connected"]:
            attempt["status"] = "SUCCESS"
        else:
            attempt["status"] = "UNKNOWN"

        attempt["fail_reason"] = ", ".join(reasons)
        attempt["root_cause_candidate"] = ", ".join(causes)
        attempt.pop("start_failed", None)
        attempt.pop("connected", None)
