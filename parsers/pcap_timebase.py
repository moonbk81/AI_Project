"""pcap 의 시각을 로그의 시계로 옮기는 일.

pcap 의 프레임 시각은 UTC epoch 이고, Android 로그의 시각은 연도가 없는 단말
현지시각(``MM-DD HH:MM:SS.mmm``)이다. 둘을 같은 축에 올려놓지 못하면 패킷과
로그를 나란히 볼 수 없다 -- 이 모듈이 그 축을 맞춘다.

기준이 되는 UTC 오프셋은 다음 순서로 정한다:

1. 명시적으로 넘겨받은 값 (사용자가 아는 경우)
2. 로그 안의 NITZ. 망이 알려 준 시간대라 그 단말에 대해서는 가장 믿을 만하다.
3. 이 서버의 시간대. 마지막 수단이고, 로그를 받은 곳과 찍은 곳이 다르면 틀린다.

어느 것을 썼는지 결과에 함께 실어 보낸다. 시각이 어긋났을 때 "왜 어긋났나" 를
되짚을 수 있는 유일한 단서이기 때문이다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Tuple

# 로그 타임스탬프 형식. 리포트 전체가 이 형식이라 pcap 도 여기에 맞춘다.
LOG_TIME_FORMAT = "%m-%d %H:%M:%S"

# NitzParser 가 만들어 두는 문자열: "UTC+9시간", "UTC-3시간"
_NITZ_OFFSET = re.compile(r"UTC\s*([+-]?\d+(?:\.\d+)?)")


def _offset_from_nitz(report_data: dict) -> Optional[float]:
    """리포트의 NITZ 이력에서 UTC 오프셋(시간)을 꺼낸다.

    가장 마지막 항목부터 본다. 로밍 중이면 시간대가 바뀌므로 캡처 시점에 가까운
    쪽이 맞을 확률이 높다.
    """
    history = (report_data or {}).get("nitz_history") or []
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        match = _NITZ_OFFSET.search(str(entry.get("timezone") or ""))
        if not match:
            continue
        try:
            return float(match.group(1))
        except ValueError:
            continue
    return None


def _host_offset_hours() -> float:
    offset = datetime.now().astimezone().utcoffset()
    return (offset.total_seconds() / 3600.0) if offset else 0.0


def resolve_timebase(report_data: dict = None, override_hours: Optional[float] = None) -> dict:
    """pcap epoch 을 로그 시각으로 옮길 때 쓸 오프셋과 그 출처."""
    if override_hours is not None:
        return {
            "tz_offset_hours": float(override_hours),
            "source": "override",
            "confidence": "high",
            "note": "호출자가 지정한 UTC 오프셋입니다.",
        }

    nitz_offset = _offset_from_nitz(report_data or {})
    if nitz_offset is not None:
        return {
            "tz_offset_hours": nitz_offset,
            "source": "nitz",
            "confidence": "high",
            "note": "로그의 NITZ(망이 알려 준 시간대)에서 가져왔습니다.",
        }

    host = _host_offset_hours()
    return {
        "tz_offset_hours": host,
        "source": "host",
        "confidence": "low",
        "note": (
            "로그에 NITZ 가 없어 분석 서버의 시간대를 썼습니다. 로그를 찍은 단말과 "
            "시간대가 다르면 pcap 시각이 통째로 밀립니다."
        ),
    }


def to_log_time(epoch_seconds: float, tz_offset_hours: float) -> str:
    """UTC epoch -> 로그와 같은 ``MM-DD HH:MM:SS.mmm``."""
    moment = datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc) + timedelta(
        hours=float(tz_offset_hours)
    )
    return f"{moment.strftime(LOG_TIME_FORMAT)}.{moment.microsecond // 1000:03d}"


def log_time_window(time_keys: Iterable[str]) -> Optional[Tuple[str, str]]:
    """로그가 덮는 구간. ``LogOrchestrator`` 의 시간 인덱스 키를 그대로 받는다.

    키는 ``MM-DD HH:MM:SS`` 라 문자열 정렬이 곧 시간순이다 -- 연말을 넘기지 않는
    한. 넘기는 로그는 1월이 앞으로 와서 구간이 실제보다 넓게 잡히는데, 이 값은
    pcap 이 로그와 겹치는지 보는 데만 쓰므로 넓게 잡혀도 놓치는 쪽으로는 틀리지
    않는다.
    """
    keys = [key for key in (time_keys or []) if key]
    if not keys:
        return None
    return min(keys), max(keys)


def check_overlap(capture_start: str, capture_end: str, log_window: Optional[Tuple[str, str]]) -> dict:
    """pcap 구간과 로그 구간이 실제로 겹치는지.

    겹치지 않으면 시간대를 잘못 잡았거나 애초에 다른 세션의 pcap 이다. 어느
    쪽이든 상관 분석 결과가 전부 빈손으로 나오는데, 이유를 말해 주지 않으면
    "패킷상 이상 없음" 으로 잘못 읽힌다. 그래서 조용히 넘기지 않는다.

    시각을 임의로 밀어서 맞추지는 않는다. 맞아 보이게 만든 시각은 틀렸을 때
    알아챌 방법이 없다. 대신 몇 시간이 어긋나 보이는지만 계산해 붙인다.
    """
    if not log_window or not capture_start or not capture_end:
        return {"checked": False, "reason": "로그 구간을 알 수 없어 확인하지 않았습니다."}

    log_start, log_end = log_window
    overlaps = capture_start <= log_end and log_start <= capture_end
    result = {
        "checked": True,
        "overlaps": overlaps,
        "log_window": [log_start, log_end],
        "capture_window": [capture_start, capture_end],
    }
    if overlaps:
        return result

    # 몇 시간 밀렸는지 어림한다. 연도가 없으니 같은 해로 놓고 뺀다.
    suggestion = _suggest_shift_hours(capture_start, log_start)
    result["warning"] = (
        "pcap 캡처 구간이 로그 구간과 겹치지 않습니다. 시간대(UTC 오프셋)를 잘못 "
        "잡았거나, 이 로그와 다른 시점의 pcap 일 수 있습니다. 상관 분석 결과는 "
        "'이상 없음'이 아니라 '비교 불가'로 읽어야 합니다."
    )
    if suggestion is not None:
        result["suggested_extra_offset_hours"] = suggestion
    return result


def _suggest_shift_hours(capture_start: str, log_start: str) -> Optional[float]:
    """캡처 시작을 로그 시작에 맞추려면 몇 시간을 더해야 하는지(가장 가까운 정시)."""
    reference_year = datetime.now().year
    try:
        captured = datetime.strptime(f"{reference_year}-{capture_start[:14]}", "%Y-%m-%d %H:%M:%S")
        logged = datetime.strptime(f"{reference_year}-{log_start[:14]}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return round((logged - captured).total_seconds() / 3600.0)
