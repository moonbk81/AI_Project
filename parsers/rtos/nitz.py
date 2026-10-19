"""NITZ(망이 내려 준 시각) 줄을 읽는다. 실시간 모니터가 마지막 값을 들고 있다가 화면에 보여준다.

두 군데에서 찍힌다 (CL 10297121 — ts_core.c on_unsol 이 1008 을 MSG_NITZ_STATE_CHANGE_IND 로 넘긴다):
- telephonytool (tools/telephony_tool.c network_signal_change) — tapi 클라이언트까지 왔다는 뜻이다.
  `sec = 9 min = 25 hour = 3 \\` 다음 줄이 문자열 안에서 이어져서 hour 와 mday 사이에 공백이 여러 칸 들어간다.
  `sec = 9 min = 25 hour = 3                 mday = 19 mon = 10 year = 2026 dst = 0 utcoff = 32400`
  year 는 네 자리, mon 은 1부터, utcoff 는 초다 (SDK parse_nitz).
- RILJ 트레이스 `[RILJ] [UNSL]< NITZ_TIME_RECEIVED {26/10/19,03:25:09+36,0}` — 1008 원문
  `yy/mm/dd,hh:mm:ss(+|-)tz,dt`, tz 는 15분 단위. 표에 이름이 없으면 `UNSOL_1008` 로 찍힌다.
  이것만 있고 telephonytool 줄이 없으면 SDK 가 클라이언트에 안 넘긴 것이다.
NITZ 시각은 UTC 이고 utcoff 를 더하면 현지 시각이다.
"""

import re
from datetime import datetime, timedelta
from typing import Dict, Optional

TOOL_NITZ_RE = re.compile(
    r'sec = (\d+) min = (\d+) hour = (\d+)\s+mday = (\d+) mon = (\d+) year = (\d+) '
    r'dst = (-?\d+) utcoff = (-?\d+)')
RILJ_NITZ_RE = re.compile(
    r'\[UNSL\]< (?:UNSOL_)?(?:NITZ_TIME_RECEIVED|1008)\b.*?\{"?(\d+)/(\d+)/(\d+),(\d+):(\d+):(\d+)([+-])(\d+)(?:,(\d+))?')


def _result(source, year, mon, mday, hour, minute, sec, utcoff_sec, dst) -> Optional[Dict]:
    try:
        utc = datetime(year, mon, mday, hour, minute, sec)
    except ValueError:
        return None
    local = utc + timedelta(seconds=utcoff_sec)
    return {
        "source": source,
        "utc": utc.strftime("%Y-%m-%d %H:%M:%S"),
        "local": local.strftime("%Y-%m-%d %H:%M:%S"),
        "utcoff_min": utcoff_sec // 60,
        "dst": dst,
    }


def parse_nitz(msg: str) -> Optional[Dict]:
    """NITZ 한 줄 → {source: telephonytool|rilj, utc, local, utcoff_min, dst}, 아니면 None."""
    m = TOOL_NITZ_RE.search(msg)
    if m:
        sec, minute, hour, mday, mon, year, dst, utcoff = (int(v) for v in m.groups())
        return _result("telephonytool", year, mon, mday, hour, minute, sec, utcoff, dst)
    m = RILJ_NITZ_RE.search(msg)
    if m:
        yy, mon, mday, hour, minute, sec = (int(v) for v in m.groups()[:6])
        tz = int(m.group(8)) * (-1 if m.group(7) == "-" else 1)
        return _result("rilj", 2000 + yy, mon, mday, hour, minute, sec, tz * 15 * 60, int(m.group(9) or 0))
    return None

