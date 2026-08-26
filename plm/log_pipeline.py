"""Turning a defect's attachments into analyzable log files.

The walk itself lives here so it can be tested without a browser: the caller
supplies a `download` callable and consumes the events this yields, deciding
how (or whether) to show progress.

Called from the FastAPI routes; nothing here imports a web framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Callable, Dict, Iterator, List, Optional

from core.log_archive import extract_logs_from_archive, is_archive_name, list_archive_contents

logger = logging.getLogger(__name__)


# Event kinds yielded while walking the attachments.
NO_ARCHIVE_ATTACHMENTS = "no_archive_attachments"
ARCHIVE_ATTACHMENTS_FOUND = "archive_attachments_found"
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


# What a single downloaded attachment turned out to be.
NOT_AN_ARCHIVE = "not_an_archive"
LOGS_FOUND = "logs_found"
NO_LOGS_IN_ARCHIVE = "no_logs_in_archive"


@dataclass(frozen=True)
class AttachmentOutcome:
    """Result of looking inside one downloaded attachment.

    `kind` is `"logs_found"` (the caller queues `logs` for analysis),
    `"no_logs_in_archive"` or `"not_an_archive"` — for the latter two there is
    nothing this project can analyze, so the file is only worth handing back to
    the user.
    """

    kind: str
    logs: Dict[str, bytes] = field(default_factory=dict)


def inspect_attachment(filename: str, content: bytes) -> AttachmentOutcome:
    """Decide what an attachment is worth doing with."""
    if not is_archive_name(filename):
        return AttachmentOutcome(NOT_AN_ARCHIVE)

    logs = extract_logs_from_archive(content)
    if not logs:
        return AttachmentOutcome(NO_LOGS_IN_ARCHIVE)
    return AttachmentOutcome(LOGS_FOUND, logs=logs)


def select_archive_attachments(files: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Attachments worth opening: a device log arrives inside an archive."""
    return [f for f in (files or []) if is_archive_name(f.get("title", ""))]


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
    """Download every archive attachment and yield the log files found inside.

    The caller registers each `LOG_READY` event's file; this function keeps no
    state of its own, so a failure on one attachment never stops the rest.
    """
    archives = select_archive_attachments(files)
    if not archives:
        yield LogExtractionEvent(NO_ARCHIVE_ATTACHMENTS)
        return

    total = len(archives)
    yield LogExtractionEvent(ARCHIVE_ATTACHMENTS_FOUND, total=total)

    for index, attachment in enumerate(archives, 1):
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
            logs = extract_logs_from_archive(file_data)

            if logs:
                logger.info("Found %d LOG file(s) in %s", len(logs), title)
                yield LogExtractionEvent(LOGS_EXTRACTED, title=title, count=len(logs))
                for filename, content in logs.items():
                    yield LogExtractionEvent(LOG_READY, title=title, filename=filename, content=content)
                continue

            # 중첩 압축은 추출기가 재귀로 들어가므로, 여기까지 왔다면 이름이
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
