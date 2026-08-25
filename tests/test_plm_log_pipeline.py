import io
import zipfile

from plm import log_pipeline
from plm.log_pipeline import extract_logs_from_attachments, select_zip_attachments


def make_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


LOG_ZIP = make_zip({"dumpstate.log": b"log body"})
NO_LOG_ZIP = make_zip({"screenshot.png": b"img"})


def _attachment(title, index=1):
    return {"title": title, "docId": f"D{index}", "fileId": f"F{index}"}


def _downloader(payloads):
    """Answer with the bytes registered per title."""

    def download(doc_id, title, file_id):
        response = payloads[title]
        if isinstance(response, Exception):
            raise response
        return response

    return download


def _kinds(events):
    return [event.kind for event in events]


def test_only_zip_attachments_are_opened():
    files = [{"title": "notes.txt"}, {"title": "logs.ZIP"}, {"title": None}]

    assert [f["title"] for f in select_zip_attachments(files)] == ["logs.ZIP"]
    assert select_zip_attachments(None) == []


def test_defect_without_zip_attachments_stops_immediately():
    events = list(extract_logs_from_attachments([{"title": "notes.txt"}], _downloader({})))

    assert _kinds(events) == [log_pipeline.NO_ZIP_ATTACHMENTS]


def test_extracted_logs_are_yielded_with_their_content():
    files = [_attachment("logs.zip")]
    download = _downloader({"logs.zip": {"success": True, "data": LOG_ZIP}})

    events = list(extract_logs_from_attachments(files, download))

    assert _kinds(events) == [
        log_pipeline.ZIP_ATTACHMENTS_FOUND,
        log_pipeline.DOWNLOADING,
        log_pipeline.EXTRACTING,
        log_pipeline.LOGS_EXTRACTED,
        log_pipeline.LOG_READY,
    ]
    ready = events[-1]
    assert (ready.filename, ready.content) == ("dumpstate.log", b"log body")
    assert events[1].index == 1 and events[1].total == 1


def test_archive_without_a_log_reports_what_it_did_contain():
    files = [_attachment("logs.zip")]
    download = _downloader({"logs.zip": {"success": True, "data": NO_LOG_ZIP}})

    event = list(extract_logs_from_attachments(files, download))[-1]

    assert event.kind == log_pipeline.NO_LOGS_MATCHED
    assert "screenshot.png" in event.contents


def test_a_failing_attachment_does_not_stop_the_others():
    files = [
        _attachment("broken.zip", 1),
        _attachment("refused.zip", 2),
        _attachment("empty.zip", 3),
        _attachment("good.zip", 4),
    ]
    download = _downloader(
        {
            "broken.zip": RuntimeError("connection reset"),
            "refused.zip": {"success": False, "message": "no permission"},
            "empty.zip": {"success": True, "data": None},
            "good.zip": {"success": True, "data": LOG_ZIP},
        }
    )

    events = list(extract_logs_from_attachments(files, download))
    by_kind = {event.kind: event for event in events}

    assert by_kind[log_pipeline.ATTACHMENT_FAILED].error == "connection reset"
    assert by_kind[log_pipeline.DOWNLOAD_FAILED].error == "no permission"
    assert by_kind[log_pipeline.DOWNLOAD_EMPTY].title == "empty.zip"
    assert by_kind[log_pipeline.LOG_READY].filename == "dumpstate.log"


def test_attachments_missing_plm_ids_are_skipped_silently():
    files = [{"title": "logs.zip"}, _attachment("good.zip", 2)]
    download = _downloader({"good.zip": {"success": True, "data": LOG_ZIP}})

    events = list(extract_logs_from_attachments(files, download))

    # Both count towards the progress total, but only the usable one is fetched.
    assert events[0].total == 2
    assert [event.title for event in events if event.kind == log_pipeline.DOWNLOADING] == ["good.zip"]


def test_downloader_returning_nothing_is_treated_as_a_failure():
    files = [_attachment("logs.zip")]

    events = list(extract_logs_from_attachments(files, lambda **kwargs: None))

    assert _kinds(events)[-1] == log_pipeline.DOWNLOAD_FAILED


# ------------------------------------------------- one downloaded attachment


def test_a_zip_holding_logs_is_worth_analyzing():
    outcome = log_pipeline.inspect_attachment("attachment.zip", LOG_ZIP)

    assert outcome.kind == log_pipeline.LOGS_FOUND
    assert outcome.logs == {"dumpstate.log": b"log body"}


def test_a_zip_without_logs_has_nothing_to_analyze():
    outcome = log_pipeline.inspect_attachment("attachment.ZIP", NO_LOG_ZIP)

    assert outcome.kind == log_pipeline.NO_LOGS_IN_ARCHIVE
    assert outcome.logs == {}


def test_a_plain_file_is_not_opened_at_all():
    assert log_pipeline.inspect_attachment("report.txt", b"anything").kind == log_pipeline.NOT_AN_ARCHIVE


def test_a_damaged_archive_reports_no_logs_rather_than_raising():
    assert log_pipeline.inspect_attachment("broken.zip", b"not a zip").kind == log_pipeline.NO_LOGS_IN_ARCHIVE
