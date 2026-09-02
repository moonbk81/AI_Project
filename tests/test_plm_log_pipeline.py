import io
import zipfile

from plm import log_pipeline
from plm.log_pipeline import (
    extract_logs_from_attachments,
    select_analyzable_attachments,
    select_archive_attachments,
)


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

    assert [f["title"] for f in select_archive_attachments(files)] == ["logs.ZIP"]
    assert select_archive_attachments(None) == []


def test_defect_without_anything_analyzable_stops_immediately():
    events = list(extract_logs_from_attachments([{"title": "spec.pdf"}], _downloader({})))

    assert _kinds(events) == [log_pipeline.NO_ARCHIVE_ATTACHMENTS]


def test_a_log_uploaded_without_an_archive_is_analyzable():
    """로그가 늘 압축 안에 오지는 않는다. dumpState 를 그대로 올린 결함도 있다.

    압축만 고르던 동안에는 이런 결함에서 고를 것이 없어 분석을 시작조차 못 했다.
    """
    files = [
        _attachment("dumpState_1787.log"),
        _attachment("logs.zip", 2),
        _attachment("spec.pdf", 3),
    ]

    assert [f["title"] for f in select_analyzable_attachments(files)] == [
        "dumpState_1787.log", "logs.zip",
    ]
    assert select_analyzable_attachments(None) == []


def test_a_txt_that_is_not_a_known_log_name_is_left_alone():
    """첨부 목록에는 결함 설명서 같은 .txt 가 그냥 붙어 있다.

    압축 안에서는 아는 이름이 하나도 없을 때만 .txt 를 후보로 올리지만, 첨부
    목록에 그 조건 없이 넓히면 로그가 아닌 것에도 체크박스가 생긴다.
    """
    files = [_attachment("결함내용.txt"), _attachment("dumpstate.txt", 2)]

    # dumpstate 계열은 .txt 여도 LOG_PATTERNS 가 받는다.
    assert [f["title"] for f in select_analyzable_attachments(files)] == ["dumpstate.txt"]


def test_a_plain_log_attachment_is_used_as_it_is():
    """압축이 아니면 열어 볼 안쪽이 없다. 내려받은 것이 곧 그 로그다."""
    files = [_attachment("dumpState_1787.log")]
    download = _downloader({"dumpState_1787.log": {"success": True, "data": b"log body"}})

    events = list(extract_logs_from_attachments(files, download))

    assert _kinds(events) == [
        log_pipeline.ARCHIVE_ATTACHMENTS_FOUND,
        log_pipeline.DOWNLOADING,
        log_pipeline.LOGS_EXTRACTED,
        log_pipeline.LOG_READY,
    ]
    ready = events[-1]
    assert ready.filename == "dumpState_1787.log"
    assert ready.content == b"log body"


def test_extracted_logs_are_yielded_with_their_content():
    files = [_attachment("logs.zip")]
    download = _downloader({"logs.zip": {"success": True, "data": LOG_ZIP}})

    events = list(extract_logs_from_attachments(files, download))

    assert _kinds(events) == [
        log_pipeline.ARCHIVE_ATTACHMENTS_FOUND,
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


# ------------------------------------------------------- 분할 압축 (.7z.001 ...)

def _volume_attachments(base="log.7z", count=3):
    """조각은 PLM 에 각각 별개 첨부로 올라온다."""
    return [_attachment(f"{base}.{index:03d}", index) for index in range(1, count + 1)]


def _split(data, count):
    size = len(data) // count + 1
    return [data[start:start + size] for start in range(0, len(data), size)]


def test_volume_parts_are_grouped_by_the_name_without_the_number():
    files = [*_volume_attachments(), _attachment("shots.zip", 9)]

    sets = log_pipeline.volume_sets(files)

    assert list(sets) == ["log.7z"]
    assert [f["title"] for f in sets["log.7z"]] == ["log.7z.001", "log.7z.002", "log.7z.003"]


def test_a_volume_set_is_offered_as_one_archive_not_as_its_pieces():
    """조각마다 한 줄씩 세우면 같은 압축을 조각 수만큼 여는 셈이 된다."""
    files = [*_volume_attachments(), _attachment("shots.zip", 9), _attachment("dumpstate.log", 8)]

    assert [f["title"] for f in select_analyzable_attachments(files)] == [
        "log.7z.001",
        "shots.zip",
        "dumpstate.log",
    ]


def test_any_piece_of_a_set_resolves_to_the_whole_set_in_order():
    files = _volume_attachments()
    shuffled = [files[2], files[0], files[1]]

    for picked in shuffled:
        assert [f["title"] for f in log_pipeline.volume_parts_for(shuffled, picked)] == [
            "log.7z.001",
            "log.7z.002",
            "log.7z.003",
        ]


def test_a_lone_attachment_resolves_to_itself():
    single = _attachment("shots.zip")
    assert log_pipeline.volume_parts_for([single], single) == [single]


def test_a_split_archive_is_downloaded_whole_and_opened():
    parts = _split(LOG_ZIP, 3)
    files = _volume_attachments(base="log.zip", count=3)
    payloads = {
        f["title"]: {"success": True, "data": chunk} for f, chunk in zip(files, parts)
    }

    events = list(extract_logs_from_attachments(files, _downloader(payloads)))

    # 조각을 다 받아서 한 번 열었다.
    downloads = [e for e in events if e.kind == log_pipeline.DOWNLOADING]
    assert [e.title for e in downloads] == ["log.zip (1/3)", "log.zip (2/3)", "log.zip (3/3)"]
    ready = [e for e in events if e.kind == log_pipeline.LOG_READY]
    assert [(e.title, e.filename, e.content) for e in ready] == [
        ("log.zip", "dumpstate.log", b"log body"),
    ]


def test_a_missing_piece_fails_the_set_rather_than_opening_a_fragment():
    """조각 하나가 빠지면 이어붙인 것은 압축이 아니다. 그 사실을 그대로 말한다."""
    parts = _split(LOG_ZIP, 3)
    files = _volume_attachments(base="log.zip", count=3)
    payloads = {
        files[0]["title"]: {"success": True, "data": parts[0]},
        files[1]["title"]: {"success": False, "message": "권한 없음"},
        files[2]["title"]: {"success": True, "data": parts[2]},
    }

    events = list(extract_logs_from_attachments(files, _downloader(payloads)))

    failed = [e for e in events if e.kind == log_pipeline.DOWNLOAD_FAILED]
    assert [(e.title, e.error) for e in failed] == [("log.zip", "권한 없음")]
    assert log_pipeline.LOG_READY not in _kinds(events)
