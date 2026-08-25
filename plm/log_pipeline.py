"""Turning a defect's attachments into analyzable log files.

The walk itself lives here so it can be tested without a browser: the caller
supplies a `download` callable and consumes the events this yields, deciding
how (or whether) to show progress.

Shared by Streamlit and FastAPI; nothing here imports either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Callable, Dict, Iterator, List, Optional

from core.log_archive import extract_logs_from_zip, list_archive_contents

logger = logging.getLogger(__name__)

ZIP_SUFFIX = ".zip"

# Event kinds yielded while walking the attachments.
NO_ZIP_ATTACHMENTS = "no_zip_attachments"
ZIP_ATTACHMENTS_FOUND = "zip_attachments_found"
DOWNLOADING = "downloading"
DOWNLOAD_FAILED = "download_failed"
DOWNLOAD_EMPTY = "download_empty"
EXTRACTING = "extracting"
LOGS_EXTRACTED = "logs_extracted"
LOG_READY = "log_ready"
NO_LOGS_MATCHED = "no_logs_matched"
ATTACHMENT_FAILED = "attachment_failed"

# A downloader takes the three ids PLM needs and returns the client's
# {"success": bool, "data": bytes, "message": str} envelope.
Downloader = Callable[..., Dict[str, Any]]


@dataclass(frozen=True)
class LogExtractionEvent:
    """One step of the walk. `kind` decides which fields carry meaning."""

    kind: str
    title: str = ""
    index: int = 0
    total: int = 0
    count: int = 0
    filename: str = ""
    content: bytes = b""
    error: str = ""
    # Names found inside an archive that yielded no log, for diagnostics.
    contents: Dict[str, int] = field(default_factory=dict)


def select_zip_attachments(files: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Attachments worth opening: only ZIPs can hold a device log."""
    return [f for f in (files or []) if str(f.get("title", "")).lower().endswith(ZIP_SUFFIX)]


def _attachment_ids(attachment: Dict[str, Any]):
    doc_id = attachment.get("docId")
    file_id = attachment.get("fileId")
    title = attachment.get("title")
    if not doc_id or not file_id or not title:
        return None
    return doc_id, file_id, title


def extract_logs_from_attachments(
    files: Optional[List[Dict[str, Any]]],
    download: Downloader,
) -> Iterator[LogExtractionEvent]:
    """Download every ZIP attachment and yield the log files found inside.

    The caller registers each `LOG_READY` event's file; this function keeps no
    state of its own, so a failure on one attachment never stops the rest.
    """
    zip_files = select_zip_attachments(files)
    if not zip_files:
        yield LogExtractionEvent(NO_ZIP_ATTACHMENTS)
        return

    total = len(zip_files)
    yield LogExtractionEvent(ZIP_ATTACHMENTS_FOUND, total=total)

    for index, attachment in enumerate(zip_files, 1):
        title = str(attachment.get("title", "unknown"))
        try:
            ids = _attachment_ids(attachment)
            if ids is None:
                logger.warning("Skipping file (missing docId/fileId/title): %s", title)
                continue
            doc_id, file_id, title = ids

            yield LogExtractionEvent(DOWNLOADING, title=title, index=index, total=total)
            logger.info("Auto-downloading %s", title)

            response = download(doc_id=doc_id, title=title, file_id=file_id) or {}
            if not response.get("success"):
                error = response.get("message", "Unknown error")
                logger.error("Failed to download %s: %s", title, error)
                yield LogExtractionEvent(DOWNLOAD_FAILED, title=title, error=error)
                continue

            file_data = response.get("data")
            if not file_data:
                logger.error("No data returned for %s", title)
                yield LogExtractionEvent(DOWNLOAD_EMPTY, title=title)
                continue

            yield LogExtractionEvent(EXTRACTING, title=title)
            logger.info("Extracting LOG files from %s", title)
            logs = extract_logs_from_zip(file_data)

            if logs:
                logger.info("Found %d LOG file(s) in %s", len(logs), title)
                yield LogExtractionEvent(LOGS_EXTRACTED, title=title, count=len(logs))
                for filename, content in logs.items():
                    yield LogExtractionEvent(LOG_READY, title=title, filename=filename, content=content)
                continue

            # 중첩 ZIP 은 추출기가 재귀로 들어가므로, 여기까지 왔다면 이름이
            # LOG_PATTERNS(dumpstate 계열)와 맞지 않는 경우다. 어떤 파일이
            # 있었는지 보여줘야 왜 못 잡았는지 알 수 있다.
            contents = list_archive_contents(file_data)
            logger.info(
                "No LOG files matched in %s; archive root contains: %s",
                title,
                list(contents.keys()) or "(unreadable or empty)",
            )
            yield LogExtractionEvent(NO_LOGS_MATCHED, title=title, contents=contents)

        except Exception as e:
            logger.error("Error processing %s: %s", title, e, exc_info=True)
            yield LogExtractionEvent(ATTACHMENT_FAILED, title=title, error=str(e))
