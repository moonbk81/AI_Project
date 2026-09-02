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

from core.log_archive import (
    extract_logs_from_archive,
    is_archive_name,
    is_plain_log_name,
    join_volumes,
    list_archive_contents,
    volume_part,
)

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
    """Attachments worth opening: an archive that may hold a device log."""
    return [f for f in (files or []) if is_archive_name(f.get("title", ""))]


def _title(attachment: Optional[Dict[str, Any]]) -> str:
    return str((attachment or {}).get("title") or "")


def volume_sets(files: Optional[List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """분할 압축 묶음. 원본 이름 -> 번호순으로 세운 조각 첨부들.

    조각은 PLM 에 각각 별개 첨부로 올라오므로, 하나만 열어 보면 압축이 아니다.
    """
    grouped: Dict[str, List[tuple] ] = {}
    for attachment in files or []:
        part = volume_part(_title(attachment))
        if part:
            grouped.setdefault(part[0], []).append((part[1], attachment))
    return {
        base: [attachment for _, attachment in sorted(parts, key=lambda item: item[0])]
        for base, parts in grouped.items()
    }


def volume_parts_for(
    files: Optional[List[Dict[str, Any]]], attachment: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """이 첨부를 열려면 함께 내려받아야 하는 첨부들.

    분할 압축의 조각이면 같은 묶음 전부를 번호순으로, 아니면 그 첨부 하나만.
    사용자가 몇 번째 조각을 골랐든 묶음 전체로 되므로 고르는 쪽이 실수할 수 없다.
    """
    part = volume_part(_title(attachment))
    if not part:
        return [attachment]
    return volume_sets(files).get(part[0]) or [attachment]


def with_volume_siblings(
    listing: Optional[List[Dict[str, Any]]], picked: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """고른 첨부에 분할 압축의 나머지 조각을 채워 넣는다.

    사용자는 묶음의 첫 조각 하나만 고른다. 그 상태로 열려고 하면 조각 하나는
    압축이 아니어서 아무것도 못 꺼낸다. 목록 전체를 아는 여기서 채워 준다.
    """
    filled: List[Dict[str, Any]] = []
    seen = set()
    for attachment in picked or []:
        for part in volume_parts_for(listing, attachment):
            key = str(part.get("fileId")) or _title(part)
            if key in seen:
                continue
            seen.add(key)
            filled.append(part)
    return filled


def select_analyzable_attachments(files: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """분석할 수 있는 첨부: 압축과, 압축 없이 그대로 올라온 로그.

    로그가 늘 압축 안에 온다고 보고 압축만 골랐더니, dumpState 를 그대로 올린
    결함에서는 고를 것이 하나도 없어 분석을 시작할 수조차 없었다.

    분할 압축은 묶음 하나가 압축 하나다. 그래서 첫 조각만 남긴다 -- 조각마다
    한 줄씩 세우면 같은 압축을 조각 수만큼 여는 셈이 된다.
    """
    sets = volume_sets(files)
    firsts = {id(parts[0]) for parts in sets.values() if parts}
    picked = []
    for attachment in files or []:
        title = _title(attachment)
        if volume_part(title):
            if id(attachment) in firsts:
                picked.append(attachment)
            continue
        if is_archive_name(title) or is_plain_log_name(title):
            picked.append(attachment)
    return picked


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
    archives = select_analyzable_attachments(files)
    if not archives:
        yield LogExtractionEvent(NO_ARCHIVE_ATTACHMENTS)
        return

    total = len(archives)
    yield LogExtractionEvent(ARCHIVE_ATTACHMENTS_FOUND, total=total)

    for index, attachment in enumerate(archives, 1):
        title = str(attachment.get("title", "unknown"))
        try:
            parts = volume_parts_for(files, attachment)
            # 분할 압축은 조각을 다 받아 이어붙여야 압축이 된다. 이름은 번호를 뗀
            # 원본으로 적는다 -- 사용자가 아는 이름은 그쪽이다.
            if len(parts) > 1:
                base = volume_part(title)[0]
                chunks = []
                failure = None
                for part_index, part in enumerate(parts, 1):
                    ids = _attachment_ids(part)
                    if ids is None:
                        failure = f"{_title(part)} 의 PLM 정보가 비어 있습니다"
                        break
                    part_doc, part_file, part_title = ids
                    yield LogExtractionEvent(
                        DOWNLOADING,
                        title=f"{base} ({part_index}/{len(parts)})",
                        index=index,
                        total=total,
                    )
                    response = download(doc_id=part_doc, title=part_title, file_id=part_file) or {}
                    if not response.get("success"):
                        failure = response.get("message", "Unknown error")
                        break
                    if not response.get("data"):
                        failure = f"{part_title} 의 내용이 비어 있습니다"
                        break
                    chunks.append(response["data"])

                if failure:
                    logger.error("Failed to download a volume of %s: %s", base, failure)
                    yield LogExtractionEvent(DOWNLOAD_FAILED, title=base, error=failure)
                    continue

                title = base
                file_data = join_volumes(chunks)
            else:
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

            # 압축이 아니면 열어 볼 안쪽이 없다. 내려받은 것이 곧 그 로그다.
            if is_plain_log_name(title):
                logger.info("%s is a log file itself", title)
                yield LogExtractionEvent(LOGS_EXTRACTED, title=title, count=1)
                yield LogExtractionEvent(LOG_READY, title=title, filename=title, content=file_data)
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
