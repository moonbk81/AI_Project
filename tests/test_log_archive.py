import io
import zipfile

import pytest

from core.log_archive import (
    NESTED_ZIP_MAX_DEPTH,
    extract_file,
    extract_logs_from_zip,
    is_log_file,
    list_archive_contents,
    list_zip_contents,
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

    assert extract_logs_from_zip(attachment) == {"dumpState_1783577655961.log": b"inner"}


def test_same_name_in_two_archives_does_not_overwrite():
    inner = make_zip({"dumpstate.log": b"from inner"})
    attachment = make_zip({"dumpstate.log": b"from root", "second.zip": inner})

    logs = extract_logs_from_zip(attachment)

    assert logs["dumpstate.log"] == b"from root"
    assert logs["second.zip/dumpstate.log"] == b"from inner"


def test_nesting_stops_at_the_depth_limit():
    payload = make_zip({"dumpstate.log": b"deepest"})
    for level in range(NESTED_ZIP_MAX_DEPTH + 1):
        payload = make_zip({f"level{level}.zip": payload})

    assert extract_logs_from_zip(payload) == {}

    one_level_shallower = make_zip({"dumpstate.log": b"reachable"})
    for level in range(NESTED_ZIP_MAX_DEPTH):
        one_level_shallower = make_zip({f"level{level}.zip": one_level_shallower})
    assert extract_logs_from_zip(one_level_shallower) == {"dumpstate.log": b"reachable"}


def test_return_all_ignores_the_name_patterns():
    attachment = make_zip({"screenshot.png": b"img", "notes.txt": b"n"})

    assert extract_logs_from_zip(attachment) == {}
    assert set(extract_logs_from_zip(attachment, return_all=True)) == {"screenshot.png", "notes.txt"}


def test_listing_only_shows_root_level_files():
    attachment = make_zip({"dumpstate.log": b"root", "sub/other.log": b"nested"})

    assert list_zip_contents(attachment) == {"dumpstate.log": 4}


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

    assert extract_logs_from_zip(broken) == {}
    assert list_zip_contents(broken) == {}
    assert list_archive_contents(broken) == {}
    assert extract_file(broken, "dumpstate.log") is None
