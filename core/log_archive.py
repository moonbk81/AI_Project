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
