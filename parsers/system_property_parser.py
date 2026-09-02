import re
from parsers.base import BaseParser

_SECTION_START = "------ SYSTEM PROPERTIES"

# dumpstate 는 명령을 동시에 돌리기 때문에, 다른 섹션의 "------ ..." 줄이
# 프로퍼티 본문 한가운데로 섞여 들어온다. 실제로 아래처럼 나온다:
#
#   ------ SYSTEM PROPERTIES (getprop) ------
#   ------ 0.019s was the duration of 'mount debugfs' ------   <- 남의 줄
#   ------ chmod debugfs (...) ------                          <- 남의 줄
#   [aaudio.hw_burst_min_usec]: [2000]                         <- 여기서부터 본문
#   ...
#   ------ 0.014s was the duration of 'chmod debugfs' ------   <- 본문 한가운데
#   ...
#   ------ 0.050s was the duration of 'SYSTEM PROPERTIES' ------  <- 진짜 끝
#
# 그래서 아무 "------" 줄에서나 끊으면 첫 줄에서 바로 빠져나와 아무것도 못 읽는다.
# 믿을 수 있는 경계는 이 섹션 자신의 종료 표시뿐이다.
_SECTION_END = re.compile(r"duration of 'SYSTEM PROPERTIES'")

# getprop 출력 한 줄: [key]: [value]
_PROPERTY = re.compile(r'\[(.*?)\]:\s*\[(.*?)\]')

# 설정값은 getprop 이 아니라 settings 덤프에, 다른 모양으로 찍힌다. 현재 값과
# 변경 이력이 함께 나온다:
#
#   _id:3267 name:mobile_data pkg:com.android.phone value:0 default:0 ...
#     History (mobile_data)
#       time:01-02 12:26:18.872 mode:insert oldValue:null newValue:1 package:android
#       time:08-23 10:01:51.098 mode:update oldValue:1 newValue:0 package:com.android.phone
#
# 이름은 정확히 끊어서 본다. `mobile_data_question` 같은 이웃이 같은 모양으로
# 찍히므로 앞부분만 맞춰 보면 엉뚱한 설정을 읽는다. 그리고 한 줄에 `value` 와
# `default` 가 같이 있어 -- 서로 다를 수 있다 -- 지금 값은 `value` 쪽이다.
_SETTING_ROW = re.compile(r'\bname:(?P<name>\S+)\b.*?\bvalue:(?P<value>\S*)')

# 이력 줄. 마지막 것이 지금 값을 만든 변경이다. time 은 공백을 품는다.
_HISTORY_HEADER = re.compile(r'\bHistory\s*\((?P<name>[^)]+)\)')
_HISTORY_ROW = re.compile(
    r'\btime:(?P<time>\S+\s+\S+)\s+mode:(?P<mode>\S+)'
    r'.*?\bnewValue:(?P<new>\S*)\s+package:(?P<package>\S*)'
)

# 담을 설정. settings 덤프는 수천 줄이라 관심 있는 것만 고른다.
WANTED_SETTINGS = ("mobile_data",)

# 슬롯에 꽂힌 것이 eSIM 인지 물리 SIM 인지는 getprop 에 없다. 가입 정보 덤프의
# `isEmbedded` 가 유일한 근거다:
#
#   Active subscriptions:
#     [SubscriptionInfoInternal: id=2 ... simSlotIndex=0 ... isEmbedded=0 ...]
#     [SubscriptionInfoInternal: id=3 ... simSlotIndex=1 ... isEmbedded=1 ...]
#
# 지금 꽂혀 있는 것만 봐야 하므로 "Active subscriptions" 아래 줄만 읽는다. 뒤에
# 이어지는 전체 목록에는 뽑아 버린 지난 SIM 도 같은 모양으로 들어 있어, 그것까지
# 읽으면 한 슬롯이 두 번 덮인다.
_ACTIVE_SUBSCRIPTIONS = "Active subscriptions:"
_SUB_SLOT = re.compile(r'\bsimSlotIndex=(?P<slot>-?\d+)')
# `isRemovableEmbedded` 는 앞 글자가 붙어 있어 여기 걸리지 않는다.
_SUB_EMBEDDED = re.compile(r'\bisEmbedded=(?P<embedded>\w+)')
# 0/1 을 그대로 두면 뜻이 없어 부르는 이름으로 바꾼다. 안드로이드 버전에 따라
# true/false 로 찍히기도 한다.
_SIM_TYPES = {"1": "eSIM", "true": "eSIM", "0": "pSIM", "false": "pSIM"}


class SystemPropertyParser(BaseParser):
    def __init__(self, context_getter=None):
        super().__init__(context_getter)
        self.properties = {}
        # 수집할 타겟 접두사 정의
        self.target_prefixes = ("ril.", "persist.radio.", "gsm.")

    def analyze(self, lines):
        is_prop_section = False  # 프로퍼티 구간 진입 여부를 알리는 플래그
        history_for = None       # 지금 어느 설정의 변경 이력을 읽고 있는지
        in_active_subs = False   # 지금 활성 가입 정보 목록을 읽고 있는지

        for line in lines:
            clean_line = line.strip()

            # 활성 가입 정보. 프로퍼티·설정과는 또 다른 구간이라 따로 흐른다.
            if _ACTIVE_SUBSCRIPTIONS in clean_line:
                in_active_subs = True
                continue
            if in_active_subs:
                if "SubscriptionInfo" not in clean_line:
                    in_active_subs = False  # 목록이 끝났다
                else:
                    self._read_subscription(clean_line)
                    continue

            # 설정 덤프는 프로퍼티 구간과 별개로 흐른다. dumpstate 는 섹션을
            # 뒤섞어 내보내므로 둘을 한 플래그로 묶지 않는다. 설정 줄과 getprop
            # 줄은 모양이 겹치지 않아 서로를 삼키지 않는다.
            header = _HISTORY_HEADER.search(clean_line)
            if header:
                history_for = header.group("name").strip()
                continue

            if history_for:
                change = _HISTORY_ROW.search(clean_line)
                if change:
                    if history_for in WANTED_SETTINGS:
                        # 이력은 시간순이라 덮어쓰며 흐르면 마지막 변경이 남는다.
                        # 그게 지금 값을 만든 변경이다.
                        self.properties[f"{history_for}_changed_at"] = change.group("time").strip()
                        self.properties[f"{history_for}_changed_by"] = change.group("package").strip()
                    continue
                history_for = None  # 이력 줄이 끊겼다

            setting = _SETTING_ROW.search(clean_line)
            if setting and setting.group("name") in WANTED_SETTINGS:
                self.properties[setting.group("name")] = setting.group("value").strip()

            # 1. 구간 밖에서는 시작 헤더만 찾는다.
            if not is_prop_section:
                if _SECTION_START in clean_line:
                    is_prop_section = True
                continue

            # 2. 이 섹션 자신의 종료 표시에서만 빠져나온다.
            #    (여러 로그를 합친 파일이면 뒤에 또 나올 수 있어 break 하지 않는다)
            if _SECTION_END.search(clean_line):
                is_prop_section = False
                continue

            # 3. 구간 내부에서 [key]: [value] 추출
            match = _PROPERTY.search(clean_line)
            if match:
                key = match.group(1).strip()
                val = match.group(2).strip()

                # 원하는 통신 관련 프로퍼티만 쏙쏙 필터링
                if key.startswith(self.target_prefixes):
                    self.properties[key] = val

        return self.properties

    def _read_subscription(self, line):
        """활성 가입 정보 한 줄에서 슬롯별 SIM 종류를 담는다.

        슬롯을 모르면(빼 둔 가입 정보는 `simSlotIndex=-1`) 어디에 적을지 알 수
        없으니 버린다.
        """
        slot = _SUB_SLOT.search(line)
        embedded = _SUB_EMBEDDED.search(line)
        if not slot or not embedded:
            return
        index = int(slot.group("slot"))
        if index < 0:
            return
        sim_type = _SIM_TYPES.get(embedded.group("embedded").strip().lower())
        if sim_type:
            self.properties[f"sim_slot{index}_type"] = sim_type
