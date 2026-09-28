"""RTOS(NuttX) 텍스트 로그 한 줄을 구조화한다.

형식: `[DD/MM/YY HH:MM:SS.ffffff] [task] [core] message`
- 태스크별 버퍼링 때문에 파일 순서와 시간 순서가 다르다 → 정렬하되 원래 줄 번호를 보관한다
- RTC 미설정이면 날짜가 31/12/99 이다 → 벽시계가 아니라 부팅 후 경과 시간으로 본다
- 타임스탬프가 줄 맨 앞이 아닐 수 있다 (`    "ecio"[31/12/99 ...` 처럼 앞 JSON 조각에 붙는다)
- 접두어 없는 줄(여러 줄 JSON, D-Bus 경고)은 바로 앞 줄의 extra 로 붙인다
- IMS 스택은 message 안에 logcat 형식을 한 번 더 싣는다:
  `12-31 02:06:25.562+0000 185 357 [I][IMS6.0] ...`
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from core.text_encoding import detect_text_encoding

RTOS_LINE_RE = re.compile(
    r'\[(\d{2})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2}\.\d+)\] \[(\d+)\] \[([a-z0-9]+)\] ?(.*)'
)
NESTED_LOGCAT_RE = re.compile(
    r'^\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+[+-]\d{4} +\d+ +\d+ +\[([VDIWEF])\]\[([^\]]+)\]\s*(.*)'
)
# 빌드에 따라 IMS 줄이 logcat 헤더 없이 `[I][IMS-FW]: 본문` 으로만 찍힌다 (콜론이 없기도 하다).
SHORT_LEVEL_TAG_RE = re.compile(r'^\[([VDIWEF])\]\[([^\]]+)\]:? ?(.*)$')
BRACKET_TAG_RE = re.compile(r'^\[([A-Za-z][\w\-. ]*?)\]\s*(.*)')
WORD_TAG_RE = re.compile(r'^([A-Za-z_][\w]*):\s+(.*)')
BUILD_KV_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$')


@dataclass
class RtosLine:
    line_no: int
    time: str
    sec: float
    task: int
    core: str
    tag: Optional[str]
    level: Optional[str]
    msg: str
    extra: List[str] = field(default_factory=list)
    date: str = ""  # MM-DD. Android 쪽 차트가 쓰는 "MM-DD HH:MM:SS.f" 시각을 만들 때 쓴다


def _to_sec(dd, mm, yy, h, m, s) -> float:
    year = 2000 + int(yy) if int(yy) < 70 else 1900 + int(yy)
    try:
        day = date(year, int(mm), int(dd)).toordinal()
    except ValueError:
        day = 0
    return day * 86400 + int(h) * 3600 + int(m) * 60 + float(s)


def split_level_tag(msg: str):
    """IMS 계열 줄이면 (level, tag, 본문), 아니면 None. 두 가지 찍는 형식을 모두 받는다."""
    nested = NESTED_LOGCAT_RE.match(msg) or SHORT_LEVEL_TAG_RE.match(msg)
    if nested:
        return nested.group(1), nested.group(2), nested.group(3)
    return None


def _split_tag(msg: str):
    """message 앞부분에서 (tag, level, 본문)을 뽑는다. 못 뽑으면 tag=None."""
    leveled = split_level_tag(msg)
    if leveled:
        return leveled[1], leveled[0], leveled[2]
    bracket = BRACKET_TAG_RE.match(msg)
    if bracket:
        return bracket.group(1), None, bracket.group(2)
    word = WORD_TAG_RE.match(msg)
    if word:
        return word.group(1), None, word.group(2)
    return None, None, msg


def parse_line(raw: str, line_no: int = 0) -> Optional[RtosLine]:
    m = RTOS_LINE_RE.search(raw.rstrip('\r\n'))
    if not m:
        return None
    dd, mm, yy, h, mi, s, task, core, msg = m.groups()
    tag, level, _ = _split_tag(msg)
    return RtosLine(
        line_no=line_no,
        time=f"{h}:{mi}:{s}",
        sec=_to_sec(dd, mm, yy, h, mi, s),
        task=int(task),
        core=core,
        tag=tag,
        level=level,
        msg=msg.rstrip(),
        date=f"{mm}-{dd}",
    )


def parse_lines(lines, sort: bool = True) -> List[RtosLine]:
    records: List[RtosLine] = []
    for line_no, raw in enumerate(lines, start=1):
        rec = parse_line(raw, line_no)
        if rec:
            records.append(rec)
        elif raw.strip() and records:
            records[-1].extra.append(raw.rstrip('\r\n'))
    if sort:
        records.sort(key=lambda r: (r.sec, r.line_no))
    return records


def read_log_lines(path) -> List[str]:
    """BOM 을 보고 인코딩을 고른다. Windows 터미널로 저장한 로그는 UTF-16LE 로 온다."""
    # splitlines() 는 \x1c, \u2028 같은 문자에서도 줄을 나눠 편집기/orchestrator 와 줄 번호가 어긋난다.
    with open(path, 'r', encoding=detect_text_encoding(path), errors='replace') as f:
        return f.read().split('\n')


def parse_build_header(lines, max_lines: int = 60) -> dict:
    """lastword 앞의 `KEY=VALUE` 빌드 정보 블록을 읽는다. 첫 RTOS 줄에서 멈춘다."""
    info = {}
    for raw in lines[:max_lines]:
        if RTOS_LINE_RE.search(raw):
            break
        m = BUILD_KV_RE.match(raw.strip())
        if m:
            info[m.group(1)] = m.group(2).strip()
    return info


def is_rtos_log(lines, sample: int = 2000, min_hits: int = 20) -> bool:
    """앞부분 샘플에 RTOS 줄이 충분히 있는지로 판별한다.

    lastword 는 빌드 정보 헤더가, call_failed 는 앞 200줄 대부분이 다른 형식이다.
    이 줄 형식은 Android logcat 과 겹치지 않으니 비율 대신 개수로 본다.
    """
    head = [l for l in lines[:sample] if l.strip()]
    if not head:
        return False
    hits = sum(1 for l in head if RTOS_LINE_RE.search(l))
    return hits >= min(min_hits, max(1, len(head) // 2))
