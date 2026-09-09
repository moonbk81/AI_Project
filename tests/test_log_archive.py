import io
import os
import zipfile

import pytest

from core.log_archive import (
    MAX_MAYBE_LOGS,
    NESTED_ARCHIVE_MAX_DEPTH,
    extract_file,
    extract_logs_from_archive,
    find_log_candidates,
    is_log_file,
    list_archive_contents,
    list_root_contents,
    read_by_route,
    join_volumes,
    volume_part,
)
from parsers.pcap_parser import is_pcap_name


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
        ("dumpstate-2026-08-25-12-05-44.txt", True),  # 날짜 꼬리표
        ("dumpstate_board.txt", True),
        ("bugreport-a56x-UP1A-2026-08-17-16-07.txt", True),
        ("dumpstateXYZ.txt", False),  # 구분자 없이 이어 붙은 이름은 다른 파일이다
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


# ------------------------------------------------------- picking logs by hand


def test_candidates_are_the_known_log_names_at_this_level():
    attachment = make_zip({
        "dumpstate.log": b"log",
        "screenshot.png": b"img",
        "notes.txt": b"n",
    })

    assert [(c.path, c.group) for c in find_log_candidates(attachment)] == [("dumpstate.log", "")]


def test_logs_in_ap_silentlog_carry_the_folder_as_their_group():
    attachment = make_zip({
        "dumpState_1783577655961.log": b"main",
        "ap_silentlog/SILENT_LOG_01.log": b"a",
        "ap_silentlog/SILENT_LOG_02.log": b"b",
        "ap_silentlog/thumb.png": b"img",
    })

    grouped = {c.path: c.group for c in find_log_candidates(attachment)}

    assert grouped["dumpState_1783577655961.log"] == ""
    assert grouped["ap_silentlog/SILENT_LOG_01.log"] == "ap_silentlog"
    assert grouped["ap_silentlog/SILENT_LOG_02.log"] == "ap_silentlog"
    assert "ap_silentlog/thumb.png" not in grouped  # 로그가 아닌 것은 묶음에서도 뺀다


def test_a_log_beside_its_own_archive_leaves_the_archive_shut():
    packed = make_zip({"dumpstate.log": b"inner copy"})
    attachment = make_zip({"dumpstate.log": b"plain file", "dumpstate.zip": packed})

    assert [c.path for c in find_log_candidates(attachment)] == ["dumpstate.log"]


def test_only_archives_named_like_a_log_are_opened():
    hinted = make_zip({"dumpstate.log": b"found"})
    other = make_zip({"dumpstate.log": b"never opened"})
    attachment = make_zip({"screenshots.zip": other, "bugreport_pack.zip": hinted})

    assert [c.path for c in find_log_candidates(attachment)] == ["bugreport_pack.zip/dumpstate.log"]


def test_unhinted_archives_are_opened_only_when_nothing_else_matched():
    attachment = make_zip({"attach01.zip": make_zip({"dumpstate.log": b"deep"})})

    assert [c.path for c in find_log_candidates(attachment)] == ["attach01.zip/dumpstate.log"]


def test_a_candidate_route_reads_back_just_that_file():
    inner = make_zip({"dumpstate.log": b"the log", "ap_silentlog/SILENT_LOG_01.log": b"silent"})
    attachment = make_zip({"bugreport.zip": inner})

    candidates = {c.path: c for c in find_log_candidates(attachment)}

    assert read_by_route(attachment, candidates["bugreport.zip/dumpstate.log"].route) == b"the log"
    silent = candidates["bugreport.zip/ap_silentlog/SILENT_LOG_01.log"]
    assert silent.group == "bugreport.zip/ap_silentlog"
    assert read_by_route(attachment, silent.route) == b"silent"


def test_scanning_stops_at_the_depth_limit():
    payload = make_zip({"dumpstate.log": b"deepest"})
    for level in range(NESTED_ARCHIVE_MAX_DEPTH + 1):
        payload = make_zip({f"log_level{level}.zip": payload})

    assert find_log_candidates(payload) == []


def test_empty_logs_are_not_offered_as_candidates():
    attachment = make_zip({
        "dumpstate.log": b"body",
        "ap_silentlog/logcat_kernel.txt": b"",       # 0 바이트
        "ap_silentlog/logcat_main.txt": b"content",
    })

    assert [c.path for c in find_log_candidates(attachment)] == [
        "dumpstate.log", "ap_silentlog/logcat_main.txt",
    ]


def test_a_bugreport_archive_is_opened_and_its_log_recognised():
    inner = make_zip({"bugreport-a56x-2026-08-17.txt": b"dumpstate body", "version.txt": b"1"})
    attachment = make_zip({"GalaxyDiagnostics_Bugreport.zip": inner, "readme.pdf": b"x"})

    found = find_log_candidates(attachment)

    assert [(c.path, c.kind) for c in found] == [
        ("GalaxyDiagnostics_Bugreport.zip/bugreport-a56x-2026-08-17.txt", "log"),
    ]


def test_files_that_only_look_like_logs_are_offered_when_nothing_is_recognised():
    inner = make_zip({"logcat_main.txt": b"a" * 50, "sec_log.log": b"b" * 10, "icon.png": b"x"})
    attachment = make_zip({"SystemLog.zip": inner})

    found = find_log_candidates(attachment)

    # 큰 것부터, 압축 안의 경로를 그대로 달고 나온다.
    assert [(c.path, c.kind) for c in found] == [
        ("SystemLog.zip/logcat_main.txt", "other"),
        ("SystemLog.zip/sec_log.log", "other"),
    ]


# ------------------------------------------------------------- 패킷 캡처


PCAP_BYTES = b"\xd4\xc3\xb2\xa1" + b"\x00" * 200


def test_a_capture_is_offered_beside_the_logs():
    attachment = make_zip({
        "dumpstate.log": b"body",
        "tcpdump_any_20260902084203.pcap": PCAP_BYTES,
    })

    found = find_log_candidates(attachment)

    # 아는 로그가 있는 첨부에서도 캡처가 보여야 한다. `maybe` 로 내려가면
    # 대부분의 첨부에서 화면에 아예 나오지 않는다.
    assert sorted((c.path, c.kind) for c in found) == [
        ("dumpstate.log", "log"),
        ("tcpdump_any_20260902084203.pcap", "capture"),
    ]


@pytest.mark.parametrize(
    "filename", ["tcpdump_any.pcap", "capture.PCAPNG", "old.cap", "tcpdump_any.pcap.gz"]
)
def test_every_capture_extension_tshark_reads_is_offered(filename):
    found = find_log_candidates(make_zip({filename: PCAP_BYTES}))

    assert [(c.path, c.kind) for c in found] == [(filename, "capture")]


def test_an_attachment_with_only_a_capture_is_not_empty_handed():
    # 캡처만 올라와도 파이프라인은 돈다(빈 로그를 만들어 준다). 고를 수 있어야
    # 거기까지 갈 수 있다.
    found = find_log_candidates(make_zip({"tcpdump_any.pcap": PCAP_BYTES, "icon.png": b"x"}))

    assert [(c.path, c.kind) for c in found] == [("tcpdump_any.pcap", "capture")]


def test_a_capture_inside_its_own_archive_is_still_found():
    inner = make_zip({"tcpdump_any_20260902084203.pcap": PCAP_BYTES})
    attachment = make_zip({"dumpstate.log": b"body", "tcpdump.zip": inner})

    found = find_log_candidates(attachment)

    # 아는 로그를 이미 찾았어도 이름에 힌트가 붙은 압축은 연다. tcpdump 는
    # 자기 이름의 압축으로 오는 일이 많다.
    assert sorted((c.path, c.kind) for c in found) == [
        ("dumpstate.log", "log"),
        ("tcpdump.zip/tcpdump_any_20260902084203.pcap", "capture"),
    ]


def test_a_capture_in_a_grouped_folder_stays_its_own_choice():
    attachment = make_zip({
        "ap_silentlog/SILENT_LOG_1.log": b"a",
        "ap_silentlog/tcpdump_any.pcap": PCAP_BYTES,
    })

    found = find_log_candidates(attachment)

    # 묶음은 "이 폴더의 로그를 통째로" 라는 뜻이다. 캡처는 골라서 넣는 것이라
    # 폴더에 섞지 않는다.
    assert sorted((c.path, c.group) for c in found) == [
        ("ap_silentlog/SILENT_LOG_1.log", "ap_silentlog"),
        ("ap_silentlog/tcpdump_any.pcap", ""),
    ]


def test_the_capture_it_offers_is_the_capture_the_pipeline_splits_out():
    """고른 캡처가 분석 쪽에서도 캡처로 인정돼야 한다.

    두 판정이 갈라지면 여기서 내준 캡처가 텍스트 로그 한가운데로 병합되고,
    뒤쪽 파서가 전부 헛돈다. 그 둘이 같은 함수를 쓰는지 여기서 지킨다.
    """
    inner = make_zip({"tcpdump_any.pcap": PCAP_BYTES})
    attachment = make_zip({"dumpstate.log": b"body", "tcpdump.zip": inner})

    found = find_log_candidates(attachment)
    capture = next(item for item in found if item.kind == "capture")
    log = next(item for item in found if item.kind == "log")

    assert is_pcap_name(os.path.basename(capture.route[-1]))
    assert not is_pcap_name(os.path.basename(log.route[-1]))
    # 꺼낸 바이트가 그대로여야 tshark 가 읽는다.
    assert read_by_route(attachment, capture.route) == PCAP_BYTES


def test_an_empty_capture_is_not_worth_choosing():
    found = find_log_candidates(make_zip({"dumpstate.log": b"body", "tcpdump_any.pcap": b""}))

    assert [c.path for c in found] == ["dumpstate.log"]


def test_a_known_log_wins_over_the_look_alikes():
    attachment = make_zip({"dumpstate.log": b"body", "logcat_main.txt": b"a" * 100})

    assert [c.path for c in find_log_candidates(attachment)] == ["dumpstate.log"]


def test_the_look_alike_list_is_capped():
    attachment = make_zip({f"part_{index:03d}.txt": b"x" * (index + 1) for index in range(MAX_MAYBE_LOGS + 10)})

    found = find_log_candidates(attachment)

    assert len(found) == MAX_MAYBE_LOGS
    assert found[0].path == f"part_{MAX_MAYBE_LOGS + 9:03d}.txt"  # 큰 것부터


# ------------------------------------------------------- 워치(웨어러블) 첨부

def make_watch_attachment(extra=None):
    """Galaxy Wearable 이 넣어 주는 워치 덤프의 실제 중첩 구조.

    첨부 → G_MANAGER/gear_dump.zip → bugreport-<모델>.zip → 워치 dumpstate
    """
    inner = make_zip({"dumpState_SM-R950_20260830.log": b"watch dumpstate body"})
    gear_dump = make_zip({"bugreport-SM-R950_20260830.zip": inner})
    return make_zip({"G_MANAGER/gear_dump.zip": gear_dump, **(extra or {})})


WATCH_PATH = (
    "G_MANAGER/gear_dump.zip/bugreport-SM-R950_20260830.zip/"
    "dumpState_SM-R950_20260830.log"
)


def test_a_watch_dump_is_found_three_archives_deep():
    assert [c.path for c in find_log_candidates(make_watch_attachment())] == [WATCH_PATH]


def test_a_watch_dump_is_still_offered_beside_a_phone_log():
    """폰 로그를 찾았다고 워치 압축까지 닫아 버리면 워치를 고를 방법이 없다."""
    attachment = make_watch_attachment({"dumpState_SM-S928N_20260830.log": b"phone body"})

    assert [c.path for c in find_log_candidates(attachment)] == [
        "dumpState_SM-S928N_20260830.log",
        WATCH_PATH,
    ]


def test_the_watch_dump_reads_back_through_its_route():
    attachment = make_watch_attachment({"dumpState_SM-S928N_20260830.log": b"phone body"})
    watch = next(c for c in find_log_candidates(attachment) if "R950" in c.path)

    assert read_by_route(attachment, watch.route) == b"watch dumpstate body"


def test_a_dumpstate_archive_beside_a_silentlog_folder_is_still_opened():
    """한 첨부에 서로 다른 로그가 나란히 들어온다.

    PLM 첨부는 MO/MT 폴더마다 log/ap_silentlog 와 <날짜>_dumpstate.zip 이 함께
    들어오는 형태가 있다. ap_silentlog 를 찾았다고 멈추면 정작 dumpstate 를
    고를 수 없다 -- 둘은 같은 로그의 재압축이 아니라 다른 로그다.
    """
    dump = make_zip({"dumpstate.txt": b"dumpstate body"})
    attachment = make_zip({
        "MO/log/ap_silentlog/SILENT_LOG_1.log": b"mo silent",
        "MO/2026-08-27-19-39-21_dumpstate.zip": dump,
        "MT/log/ap_silentlog/SILENT_LOG_1.log": b"mt silent",
        "MT/2026-08-27-19-39-21_dumpstate.zip": dump,
    })

    paths = [c.path for c in find_log_candidates(attachment)]

    assert "MO/2026-08-27-19-39-21_dumpstate.zip/dumpstate.txt" in paths
    assert "MT/2026-08-27-19-39-21_dumpstate.zip/dumpstate.txt" in paths
    # ap_silentlog 쪽도 그대로 남아 있어야 한다.
    assert "MO/log/ap_silentlog/SILENT_LOG_1.log" in paths


def test_that_dumpstate_reads_back_through_its_route():
    dump = make_zip({"dumpstate.txt": b"dumpstate body"})
    attachment = make_zip({
        "MO/log/ap_silentlog/SILENT_LOG_1.log": b"mo silent",
        "MO/2026-08-27-19-39-21_dumpstate.zip": dump,
    })

    route = ("MO/2026-08-27-19-39-21_dumpstate.zip", "dumpstate.txt")

    assert read_by_route(attachment, route) == b"dumpstate body"


def test_an_unhinted_archive_stays_shut_once_a_log_is_found():
    """이름에 로그 단서가 없는 압축까지 열면 첨부마다 값이 커진다."""
    attachment = make_zip({
        "dumpstate.log": b"plain file",
        "screenshots.zip": make_zip({"dumpstate.log": b"never opened"}),
    })

    assert [c.path for c in find_log_candidates(attachment)] == ["dumpstate.log"]


def test_a_repack_of_the_log_beside_it_is_still_left_shut():
    """워치 예외가 재압축 중복 방지까지 풀어 버리면 안 된다."""
    attachment = make_zip({
        "dumpstate.log": b"plain file",
        "dumpstate.zip": make_zip({"dumpstate.log": b"inner copy"}),
    })

    assert [c.path for c in find_log_candidates(attachment)] == ["dumpstate.log"]


# ---------------------------------------------------------------- 분할 압축

@pytest.mark.parametrize(
    "filename, expected",
    [
        ("log.7z.001", ("log.7z", 1)),
        ("log.7z.002", ("log.7z", 2)),
        ("log.zip.010", ("log.zip", 10)),
        ("LOG.7Z.001", ("LOG.7Z", 1)),
        # 번호를 뗀 이름이 압축이 아니면 조각이 아니다.
        ("report.2026.001", None),
        ("dumpState.log.001", None),
        ("log.7z", None),
        ("log.7z.abc", None),
        ("", None),
    ],
)
def test_volume_part_names(filename, expected):
    assert volume_part(filename) == expected


def test_volumes_joined_in_order_open_as_the_original_archive():
    """7-Zip 볼륨은 압축 스트림을 바이트로 자른 것이라 이어붙이면 원본이 된다.

    조각 하나만으로는 압축으로 읽히지 않는다 -- 그래서 첨부 하나씩 열어 보는
    경로에서는 분할 압축을 통째로 놓쳤다.
    """
    original = make_zip({"dumpState_0.log": b"09-01 07:51:48.943 D emergency\n"})
    cut = len(original) // 3 + 1
    parts = [original[:cut], original[cut:2 * cut], original[2 * cut:]]

    assert len(parts[0]) < len(original)
    assert extract_logs_from_archive(parts[0]) == {}

    joined = join_volumes(parts)
    assert joined == original
    assert list(extract_logs_from_archive(joined)) == ["dumpState_0.log"]
