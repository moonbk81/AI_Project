"""Reading Android log files out of PLM attachment archives.

Attachments arrive as ZIP or 7z, often nested inside one another, so the
format is decided per archive from its magic bytes rather than its name.

Pure functions over archive bytes. Nothing here touches a web framework, the
filesystem or the network.
"""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Dict, Iterable, List, NamedTuple, Optional

import zipfile

logger = logging.getLogger(__name__)

# Attachment names that hold a device log. Matched case-insensitively against
# the base name, so a match inside a nested folder still counts.
LOG_PATTERNS = [
    r'^dumpstate\.log$',              # dumpstate.log
    r'^dumpstate\.txt$',              # dumpstate.txt
    r'^dumpState\.log$',              # dumpState.log
    r'^dumpState_\d+\.log$',          # dumpState_1783577655961.log (Unix timestamp only)
    r'^dumpState_[A-Z0-9]+_\d{10,}\.log$',  # dumpState_S911NKSS7EZCI_202607070957.log (device ID + timestamp)
    r'^act_dumpstate\.txt$',          # act_dumpstate.txt
]

# PLM 첨부는 압축 파일 안에 압축 파일이 다시 들어있는 경우가 많다(로그는 안쪽에 있음).
# 무한 재귀와 zip bomb 을 막기 위한 가드.
NESTED_ARCHIVE_MAX_DEPTH = 3
MAX_TOTAL_EXTRACT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

# 중첩 압축을 열지 말지 정하는 힌트. 안을 열기 전에 아는 단서는 이름뿐이라,
# 로그가 들어 있을 만한 이름만 골라 연다.
LOG_ARCHIVE_HINTS = ("dumpstate", "bugreport", "systemlog", "log")

# 안의 로그를 하나씩 고르는 것이 의미 없는 폴더. 통째로 한 항목으로 묶는다.
GROUPED_LOG_FOLDERS = ("ap_silentlog",)

# 묶음 폴더 안에서 로그로 볼 확장자. 이름 규칙(LOG_PATTERNS)과 달리
# SILENT_LOG_* 처럼 제각각이라 확장자로 본다.
GROUPED_LOG_SUFFIXES = (".log", ".txt")

ARCHIVE_SUFFIXES = (".zip", ".7z")

ZIP = "zip"
SEVEN_ZIP = "7z"

_MAGIC = {
    b"PK\x03\x04": ZIP,
    b"PK\x05\x06": ZIP,  # empty archive
    b"7z\xbc\xaf\x27\x1c": SEVEN_ZIP,
}


class Entry(NamedTuple):
    """One member of an archive, before its bytes are read."""

    name: str
    size: int
    is_dir: bool


def is_log_file(filename: str) -> bool:
    """True when the name looks like a device log this project can analyze."""
    return any(re.match(pattern, filename, re.IGNORECASE) for pattern in LOG_PATTERNS)


def is_archive_name(filename: str) -> bool:
    return str(filename).lower().endswith(ARCHIVE_SUFFIXES)


def archive_format(data: bytes) -> Optional[str]:
    """Which format the bytes are, or None when they are not an archive."""
    head = bytes(data or b"")[:8]
    for magic, name in _MAGIC.items():
        if head.startswith(magic):
            return name
    return None


# --------------------------------------------------------------- per format

def _zip_entries(data: bytes) -> List[Entry]:
    with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
        return [Entry(info.filename, info.file_size, info.is_dir()) for info in archive.infolist()]


def _zip_read(data: bytes, names: Iterable[str]) -> Dict[str, bytes]:
    wanted = list(names)
    if not wanted:
        return {}
    out = {}
    with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
        for name in wanted:
            try:
                out[name] = archive.read(name)
            except Exception as e:
                logger.error("Failed to read %s: %s", name, e)
    return out


def _sevenzip_entries(data: bytes) -> List[Entry]:
    import py7zr

    with py7zr.SevenZipFile(io.BytesIO(data), "r") as archive:
        return [
            Entry(info.filename, int(info.uncompressed or 0), bool(info.is_directory))
            for info in archive.list()
        ]


def _sevenzip_read(data: bytes, names: Iterable[str]) -> Dict[str, bytes]:
    import py7zr
    from py7zr.io import BytesIOFactory

    wanted = list(names)
    if not wanted:
        return {}

    # 7z is solid-compressed: pulling members one at a time re-reads the whole
    # archive, so everything wanted is taken in a single pass, into memory.
    factory = BytesIOFactory(limit=MAX_TOTAL_EXTRACT_BYTES)
    with py7zr.SevenZipFile(io.BytesIO(data), "r") as archive:
        archive.extract(targets=wanted, factory=factory)

    out = {}
    for name in wanted:
        try:
            out[name] = factory.get(name).read()
        except KeyError:
            logger.error("Not found in 7z archive: %s", name)
    return out


_READERS = {
    ZIP: (_zip_entries, _zip_read),
    SEVEN_ZIP: (_sevenzip_entries, _sevenzip_read),
}


def entries(data: bytes) -> List[Entry]:
    """Members of an archive, or an empty list when it cannot be read."""
    fmt = archive_format(data)
    if fmt is None:
        logger.error("Not a supported archive (expected zip or 7z)")
        return []
    try:
        return _READERS[fmt][0](data)
    except Exception as e:
        logger.error("Invalid %s data: %s", fmt, e)
        return []


def read_members(data: bytes, names: Iterable[str]) -> Dict[str, bytes]:
    fmt = archive_format(data)
    if fmt is None:
        return {}
    try:
        return _READERS[fmt][1](data, names)
    except Exception as e:
        logger.error("Failed to read from %s archive: %s", fmt, e)
        return {}


# ------------------------------------------------------------------ walking

def _collect_logs(
    data: bytes,
    extracted: Dict[str, bytes],
    state: Dict[str, int],
    return_all: bool,
    depth: int,
    origin: str,
) -> None:
    """Walk one archive level, recursing into nested archives.

    Mutates ``extracted``. Members are decided first and read afterwards, in
    the same order, because 7z is far cheaper to read in one pass.
    """
    where = origin or "<root>"
    planned = []  # (kind, member name, base name)
    projected = state["bytes"]

    for entry in entries(data):
        if entry.is_dir:
            continue

        base_filename = os.path.basename(entry.name)
        if not base_filename:
            continue

        # Nested archive: recurse instead of treating it as a payload.
        if is_archive_name(base_filename):
            if depth >= NESTED_ARCHIVE_MAX_DEPTH:
                logger.warning(
                    "Nested archive depth limit (%d) reached at %s%s; not descending further",
                    NESTED_ARCHIVE_MAX_DEPTH, origin, entry.name,
                )
                continue
            planned.append(("archive", entry.name, base_filename))
            continue

        if not (return_all or is_log_file(base_filename)):
            continue

        if projected + entry.size > MAX_TOTAL_EXTRACT_BYTES:
            logger.warning(
                "Extraction size cap (%d bytes) would be exceeded by %s%s; skipping",
                MAX_TOTAL_EXTRACT_BYTES, origin, entry.name,
            )
            continue

        projected += entry.size
        planned.append(("log", entry.name, base_filename))

    if not planned:
        if not entries(data):
            logger.error("Nothing readable in archive at %s (depth=%d)", where, depth)
        return

    members = read_members(data, [name for _, name, _ in planned])

    for kind, name, base_filename in planned:
        content = members.get(name)
        if content is None:
            logger.error("Failed to extract %s%s", origin, name)
            continue

        if kind == "archive":
            logger.info("Descending into nested archive %s%s", origin, name)
            _collect_logs(
                content, extracted, state, return_all,
                depth=depth + 1, origin=f"{origin}{base_filename}/",
            )
            continue

        # Same base name in two nested archives must not overwrite.
        key = base_filename
        if key in extracted:
            key = f"{origin}{base_filename}"

        extracted[key] = content
        state["bytes"] += len(content)


def extract_logs_from_archive(data: bytes, return_all: bool = False) -> Dict[str, bytes]:
    """Extract log files from an archive, descending into nested archives.

    PLM dumpstate attachments commonly wrap the log in an inner archive, so a
    non-recursive scan finds nothing and silently reports "no logs".

    Args:
        data: Binary data of a ZIP or 7z file
        return_all: If True, return every file; if False, only log files

    Returns:
        Dictionary {filename: file_content} for matching files. Names that
        collide across nested archives keep their inner path as a prefix.
    """
    extracted: Dict[str, bytes] = {}
    state = {"bytes": 0}
    _collect_logs(data, extracted, state, return_all, depth=0, origin="")

    if extracted:
        logger.info(
            "Extracted %d file(s) from archive (%.1f MB total)",
            len(extracted),
            state["bytes"] / 1024 / 1024,
        )
    return extracted


def extract_file(data: bytes, target_filename: str) -> Optional[bytes]:
    """Read one file out of an archive by name.

    Matches on the base name first, so a file the user picked from a listing is
    still found when it actually lives inside a folder.
    """
    names = [entry.name for entry in entries(data) if not entry.is_dir]

    match = next((name for name in names if os.path.basename(name) == target_filename), None)
    if match is None and target_filename in names:
        match = target_filename
    if match is None:
        return None

    return read_members(data, [match]).get(match)


def list_root_contents(data: bytes) -> Dict[str, int]:
    """Names and sizes of the archive's root-level files.

    Subdirectories are skipped: this feeds the "pick a file" list, and a nested
    path is not something the user can hand to the analyzer directly.
    """
    return {
        entry.name: entry.size
        for entry in entries(data)
        if not entry.is_dir and "/" not in entry.name
    }


def list_archive_contents(data: bytes, _depth: int = 0) -> Dict[str, int]:
    """Every file name inside an archive, descending into nested archives.

    Used for diagnostics when no log file matched: the interesting names are
    usually inside an inner archive, which `list_root_contents()` cannot see.

    Returns:
        {display_path: file_size_in_bytes}
    """
    if _depth > NESTED_ARCHIVE_MAX_DEPTH:
        return {}

    found: Dict[str, int] = {}
    members = [entry for entry in entries(data) if not entry.is_dir]
    nested = [entry.name for entry in members if is_archive_name(entry.name)]
    contents = read_members(data, nested) if nested else {}

    for entry in members:
        if entry.name in nested:
            inner = contents.get(entry.name)
            if inner is None:
                found[f"{entry.name} (읽기 실패)"] = entry.size
                continue
            for sub, size in list_archive_contents(inner, _depth + 1).items():
                found[f"{entry.name}/{sub}"] = size
        else:
            found[entry.name] = entry.size

    return found


# ------------------------------------------------------- picking logs by hand

def looks_like_log_archive(filename: str) -> bool:
    """중첩 압축을 열어 볼 만한 이름인지(대소문자 무시).

    안을 열기 전에는 알 수 없으니 어디까지나 어림이다. 여는 순서와 여부를
    정하는 데만 쓴다.
    """
    name = str(filename).lower()
    return any(hint in name for hint in LOG_ARCHIVE_HINTS)


def grouped_folder_of(member_name: str) -> str:
    """멤버가 묶음 폴더(ap_silentlog 등) 안에 있으면 그 폴더 경로.

    폴더가 없으면 빈 문자열. 경로 중간 어디에 있어도 잡는다.
    """
    parts = str(member_name).split("/")
    for index, part in enumerate(parts[:-1]):
        if part.lower() in GROUPED_LOG_FOLDERS:
            return "/".join(parts[: index + 1])
    return ""


class LogCandidate(NamedTuple):
    """고를 수 있는 로그 하나.

    ``route`` 는 바깥 압축에서 안으로 들어가는 멤버 이름들이고 마지막이 로그
    파일이다. 이것만 있으면 나중에 그 파일 하나만 다시 꺼낼 수 있다.
    """

    route: tuple
    size: int
    group: str = ""

    @property
    def path(self) -> str:
        """사람이 보는 경로이자 후보를 구분하는 id."""
        return "/".join(self.route)


def _is_grouped_log(filename: str) -> bool:
    return is_log_file(filename) or filename.lower().endswith(GROUPED_LOG_SUFFIXES)


def _candidates_here(data: bytes, route: tuple) -> List[LogCandidate]:
    """이 층에서 바로 고를 수 있는 로그들. 압축은 열지 않는다."""
    found = []
    for entry in entries(data):
        if entry.is_dir:
            continue
        base_filename = os.path.basename(entry.name)
        if not base_filename or is_archive_name(base_filename):
            continue

        folder = grouped_folder_of(entry.name)
        if folder:
            if not _is_grouped_log(base_filename):
                continue
        elif not is_log_file(base_filename):
            continue

        # 0 바이트 로그가 섞여 있는 첨부가 있다(ap_silentlog 안이 특히 그렇다).
        # 꺼내 봐야 분석할 내용이 없으니 고를 거리로도 내놓지 않는다.
        if entry.size <= 0:
            logger.info("Skipping empty log %s", entry.name)
            continue

        prefix = "/".join(route)
        found.append(LogCandidate(
            route=(*route, entry.name),
            size=entry.size,
            group=f"{prefix}/{folder}" if prefix and folder else folder,
        ))
    return found


def find_log_candidates(data: bytes, _depth: int = 0, _route: tuple = ()) -> List[LogCandidate]:
    """압축 안에서 고를 만한 로그들을 찾아 목록으로 돌려준다.

    본문은 읽지 않는다 — 목록(zip 은 중앙 디렉터리)만 보므로 첨부 하나를
    훑는 값이 거의 들지 않는다. 예외는 중첩 압축이고, 그것도 이름에 로그
    힌트가 있는 것만 연다.

    규칙:
      * 이 층에 로그가 있으면 옆의 압축은 열지 않는다. dumpstate.log 와 그것을
        다시 압축한 dumpstate.zip 이 함께 든 첨부가 흔하다.
      * ap_silentlog 같은 폴더 안의 로그는 ``group`` 이 채워져 나간다. 부르는
        쪽에서 한 묶음으로 고르게 하기 위한 것이다.
      * 힌트가 붙은 압축에서 아무것도 못 찾으면, 그때만 나머지 압축도 연다.
    """
    here = _candidates_here(data, _route)
    if here:
        return here

    if _depth >= NESTED_ARCHIVE_MAX_DEPTH:
        return []

    nested = [
        entry.name for entry in entries(data)
        if not entry.is_dir and is_archive_name(os.path.basename(entry.name))
    ]
    hinted = [name for name in nested if looks_like_log_archive(os.path.basename(name))]
    plain = [name for name in nested if name not in set(hinted)]

    # 힌트가 붙은 것부터, 그래도 없으면 나머지. 나머지까지 여는 경우는 어차피
    # 아무 로그도 못 찾은 첨부라 아끼고 말 것이 없다.
    for wave in (hinted, plain):
        found: List[LogCandidate] = []
        for name, content in _read_in_order(data, wave):
            if content is None:
                logger.error("Failed to open nested archive %s", name)
                continue
            logger.info("Scanning nested archive %s (depth=%d)", name, _depth + 1)
            found.extend(find_log_candidates(content, _depth + 1, (*_route, name)))
        if found:
            return found

    return []


def _read_in_order(data: bytes, names: List[str]):
    """멤버를 주어진 순서대로 (name, content) 로 내어준다.

    7z 은 solid 라 한 개만 꺼내도 전체를 다시 읽으므로 한 번에 다 읽고, zip 은
    필요한 것만 하나씩 꺼낸다.
    """
    if not names:
        return
    if archive_format(data) == SEVEN_ZIP:
        members = read_members(data, names)
        for name in names:
            yield name, members.get(name)
        return
    for name in names:
        yield name, read_members(data, [name]).get(name)


def read_member(data: bytes, name: str) -> bytes:
    """압축에서 멤버 하나를 읽는다. 못 읽으면 왜 못 읽었는지 담아 올린다.

    zipfile/py7zr 가 말하는 이유(지원하지 않는 압축 방식, 깨진 항목 등)를 그대로
    전한다. "꺼내지 못했습니다" 만으로는 다음에 무엇을 해야 할지 알 수 없다.
    """
    fmt = archive_format(data)
    if fmt is None:
        raise ValueError("zip 도 7z 도 아닙니다")

    try:
        if fmt == ZIP:
            with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
                return archive.read(name)

        got = _sevenzip_read(data, [name])
        if name not in got:
            raise KeyError(name)
        return got[name]
    except KeyError:
        # 목록에 적힌 이름이 그대로 통하지 않는 압축이 있다(경로 구분자나 이름
        # 인코딩 차이). 파일명으로 한 번 더 찾아 본다.
        fallback = extract_file(data, os.path.basename(name))
        if fallback is None:
            raise FileNotFoundError(f"압축 안에 {os.path.basename(name)} 이 없습니다") from None
        logger.warning("Read %s by base name; the listed path did not work", name)
        return fallback


def read_by_route(data: bytes, route: Iterable[str]) -> bytes:
    """``LogCandidate.route`` 를 따라 파일 하나만 꺼낸다.

    중간 단계는 중첩 압축이고 마지막이 로그다. 고르지 않은 파일은 풀지 않는다.
    한 단계라도 실패하면 그 이유를 담아 올린다 — 부르는 쪽이 그 파일만 건너뛰고
    나머지를 계속할 수 있게.
    """
    payload = data
    for name in route:
        payload = read_member(payload, name)
    return payload
