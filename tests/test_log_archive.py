import io
import zipfile

import pytest

from core.log_archive import (
    NESTED_ARCHIVE_MAX_DEPTH,
    extract_file,
    extract_logs_from_archive,
    is_log_file,
    list_archive_contents,
    list_root_contents,
)


def make_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("dumpstate.log", True),
        ("DUMPSTATE.LOG", True),  # matched case-insensitively
        ("dumpstate.txt", True),
        ("dumpState_1783577655961.log", True),
        ("dumpState_S911NKSS7EZCI_202607070957.log", True),
        ("act_dumpstate.txt", True),
        ("dumpstate.log.gz", False),  # still packed
        ("logcat.txt", False),
        ("", False),
    ],
)
def test_log_file_patterns(filename, expected):
    assert is_log_file(filename) is expected


def test_logs_are_found_inside_nested_archives():
    inner = make_zip({"dumpState_1783577655961.log": b"inner", "junk.bin": b"x"})
    attachment = make_zip({"payload.zip": inner, "manifest.json": b"{}"})

    assert extract_logs_from_archive(attachment) == {"dumpState_1783577655961.log": b"inner"}


def test_same_name_in_two_archives_does_not_overwrite():
    inner = make_zip({"dumpstate.log": b"from inner"})
    attachment = make_zip({"dumpstate.log": b"from root", "second.zip": inner})

    logs = extract_logs_from_archive(attachment)

    assert logs["dumpstate.log"] == b"from root"
    assert logs["second.zip/dumpstate.log"] == b"from inner"


def test_nesting_stops_at_the_depth_limit():
    payload = make_zip({"dumpstate.log": b"deepest"})
    for level in range(NESTED_ARCHIVE_MAX_DEPTH + 1):
        payload = make_zip({f"level{level}.zip": payload})

    assert extract_logs_from_archive(payload) == {}

    one_level_shallower = make_zip({"dumpstate.log": b"reachable"})
    for level in range(NESTED_ARCHIVE_MAX_DEPTH):
        one_level_shallower = make_zip({f"level{level}.zip": one_level_shallower})
    assert extract_logs_from_archive(one_level_shallower) == {"dumpstate.log": b"reachable"}


def test_return_all_ignores_the_name_patterns():
    attachment = make_zip({"screenshot.png": b"img", "notes.txt": b"n"})

    assert extract_logs_from_archive(attachment) == {}
    assert set(extract_logs_from_archive(attachment, return_all=True)) == {"screenshot.png", "notes.txt"}


def test_listing_only_shows_root_level_files():
    attachment = make_zip({"dumpstate.log": b"root", "sub/other.log": b"nested"})

    assert list_root_contents(attachment) == {"dumpstate.log": 4}


def test_recursive_listing_reaches_inside_nested_archives():
    inner = make_zip({"deep.txt": b"12345"})
    attachment = make_zip({"payload.zip": inner, "top.txt": b"1"})

    contents = list_archive_contents(attachment)

    assert contents["payload.zip/deep.txt"] == 5
    assert contents["top.txt"] == 1


def test_extract_file_finds_a_name_that_lives_in_a_folder():
    attachment = make_zip({"logs/dumpstate.log": b"content"})

    assert extract_file(attachment, "dumpstate.log") == b"content"
    assert extract_file(attachment, "logs/dumpstate.log") == b"content"
    assert extract_file(attachment, "missing.log") is None


def test_unreadable_archives_are_reported_as_empty():
    broken = b"this is not a zip"

    assert extract_logs_from_archive(broken) == {}
    assert list_root_contents(broken) == {}
    assert list_archive_contents(broken) == {}
    assert extract_file(broken, "dumpstate.log") is None


# ----------------------------------------------------------------- 7z

def make_7z(entries):
    import py7zr

    buffer = io.BytesIO()
    with py7zr.SevenZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writef(io.BytesIO(data), name)
    return buffer.getvalue()


def test_a_7z_attachment_is_read_like_any_other():
    attachment = make_7z({"dumpstate.log": b"log body", "screenshot.png": b"img"})

    assert extract_logs_from_archive(attachment) == {"dumpstate.log": b"log body"}
    assert list_root_contents(attachment) == {"dumpstate.log": 8, "screenshot.png": 3}


def test_the_format_is_decided_by_the_bytes_not_the_name():
    """A .zip that is really a 7z (or the other way round) still opens."""
    from core.log_archive import archive_format

    assert archive_format(make_7z({"a.txt": b"x"})) == "7z"
    assert archive_format(make_zip({"a.txt": b"x"})) == "zip"
    assert archive_format(b"neither") is None


def test_archives_nest_across_formats():
    inner_7z = make_7z({"dumpstate.log": b"from 7z"})
    attachment = make_zip({"payload.7z": inner_7z, "notes.txt": b"n"})

    assert extract_logs_from_archive(attachment) == {"dumpstate.log": b"from 7z"}

    inner_zip = make_zip({"dumpState_1783577655961.log": b"from zip"})
    other = make_7z({"payload.zip": inner_zip})

    assert extract_logs_from_archive(other) == {"dumpState_1783577655961.log": b"from zip"}


def test_a_single_file_can_be_pulled_out_of_a_7z():
    attachment = make_7z({"logs/dumpstate.log": b"content"})

    assert extract_file(attachment, "dumpstate.log") == b"content"
    assert extract_file(attachment, "missing.log") is None


def test_a_damaged_7z_reads_as_empty():
    broken = b"7z\xbc\xaf\x27\x1c" + b"garbage" * 10

    assert extract_logs_from_archive(broken) == {}
    assert list_root_contents(broken) == {}
