"""Reading Android log files out of PLM attachment archives.

Pure functions over ZIP bytes, shared by the Streamlit UI and the backend.
Nothing here touches Streamlit, the filesystem or the network.
"""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Dict, Optional

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

# PLM 첨부는 ZIP 안에 ZIP 이 다시 들어있는 경우가 많다(로그는 안쪽에 있음).
# 무한 재귀와 zip bomb 을 막기 위한 가드.
NESTED_ZIP_MAX_DEPTH = 3
MAX_TOTAL_EXTRACT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


def is_log_file(filename: str) -> bool:
    """True when the name looks like a device log this project can analyze."""
    return any(re.match(pattern, filename, re.IGNORECASE) for pattern in LOG_PATTERNS)


def _collect_logs(
    zip_data: bytes,
    extracted: Dict[str, bytes],
    state: Dict[str, int],
    return_all: bool,
    depth: int,
    origin: str,
) -> None:
    """Walk one archive level, recursing into nested ZIPs. Mutates ``extracted``."""
    where = origin or "<root>"
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.is_dir():
                    continue

                filename = file_info.filename
                base_filename = os.path.basename(filename)
                if not base_filename:
                    continue

                # Nested archive: recurse instead of treating it as a payload.
                if base_filename.lower().endswith('.zip'):
                    if depth >= NESTED_ZIP_MAX_DEPTH:
                        logger.warning(
                            "Nested ZIP depth limit (%d) reached at %s%s; not descending further",
                            NESTED_ZIP_MAX_DEPTH, origin, filename,
                        )
                        continue
                    try:
                        inner = zip_ref.read(filename)
                    except Exception as e:
                        logger.error(f"Failed to read nested ZIP {origin}{filename}: {e}")
                        continue
                    logger.info("Descending into nested ZIP %s%s", origin, filename)
                    _collect_logs(
                        inner, extracted, state, return_all,
                        depth=depth + 1, origin=f"{origin}{base_filename}/",
                    )
                    continue

                if not (return_all or is_log_file(base_filename)):
                    continue

                if state["bytes"] + file_info.file_size > MAX_TOTAL_EXTRACT_BYTES:
                    logger.warning(
                        "Extraction size cap (%d bytes) would be exceeded by %s%s; skipping",
                        MAX_TOTAL_EXTRACT_BYTES, origin, filename,
                    )
                    continue

                # Same base name in two nested archives must not overwrite.
                key = base_filename
                if key in extracted:
                    key = f"{origin}{base_filename}"
                try:
                    content = zip_ref.read(filename)
                except Exception as e:
                    logger.error(f"Failed to extract {origin}{filename}: {e}")
                    continue

                extracted[key] = content
                state["bytes"] += len(content)

    except zipfile.BadZipFile:
        logger.error("Invalid ZIP data at %s (depth=%d)", where, depth)
    except Exception as e:
        logger.error("Error extracting from ZIP at %s: %s", where, e)


def extract_logs_from_zip(zip_data: bytes, return_all: bool = False) -> Dict[str, bytes]:
    """Extract log files from a ZIP archive, descending into nested ZIPs.

    PLM dumpstate attachments commonly wrap the log in an inner ZIP, so a
    non-recursive scan finds nothing and silently reports "no logs".

    Args:
        zip_data: Binary data of ZIP file
        return_all: If True, return every file; if False, only log files

    Returns:
        Dictionary {filename: file_content} for matching files. Names that
        collide across nested archives keep their inner path as a prefix.
    """
    extracted: Dict[str, bytes] = {}
    state = {"bytes": 0}
    _collect_logs(zip_data, extracted, state, return_all, depth=0, origin="")

    if extracted:
        logger.info(
            "Extracted %d file(s) from archive (%.1f MB total)",
            len(extracted),
            state["bytes"] / 1024 / 1024,
        )
    return extracted


def extract_file(zip_data: bytes, target_filename: str) -> Optional[bytes]:
    """Read one file out of a ZIP by name.

    Matches on the base name first, so a file the user picked from a listing is
    still found when it actually lives inside a folder.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                if os.path.basename(file_info.filename) == target_filename:
                    return zip_ref.read(file_info.filename)

            # Direct match as fallback
            if target_filename in zip_ref.namelist():
                return zip_ref.read(target_filename)

            return None

    except Exception as e:
        logger.error(f"Error extracting single file: {e}")
        return None


def list_zip_contents(zip_data: bytes) -> Dict[str, int]:
    """Names and sizes of the archive's root-level files.

    Subdirectories are skipped: this feeds the "pick a file" list, and a nested
    path is not something the user can hand to the analyzer directly.

    Returns:
        Dictionary with {filename: file_size_in_bytes}
    """
    try:
        files_dict = {}
        with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.is_dir():
                    continue

                filename = file_info.filename
                if '/' not in filename:
                    files_dict[filename] = file_info.file_size

        return files_dict

    except zipfile.BadZipFile:
        return {}
    except Exception as e:
        logger.error(f"Error listing ZIP: {e}")
        return {}


def list_archive_contents(zip_data: bytes, _depth: int = 0) -> Dict[str, int]:
    """Every file name inside a ZIP, descending into nested ZIPs.

    Used for diagnostics when no log file matched: the interesting names are
    usually inside an inner archive, which `list_zip_contents()` cannot see.

    Returns:
        {display_path: file_size_in_bytes}
    """
    if _depth > NESTED_ZIP_MAX_DEPTH:
        return {}

    found: Dict[str, int] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as zip_ref:
            for info in zip_ref.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if name.lower().endswith('.zip'):
                    try:
                        inner = zip_ref.read(name)
                    except Exception:
                        found[f"{name} (읽기 실패)"] = info.file_size
                        continue
                    for sub, size in list_archive_contents(inner, _depth + 1).items():
                        found[f"{name}/{sub}"] = size
                else:
                    found[name] = info.file_size
    except zipfile.BadZipFile:
        return {}
    except Exception as e:
        logger.error(f"Error listing archive: {e}")
        return {}

    return found
