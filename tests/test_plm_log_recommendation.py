from pathlib import Path

from plm.log_recommendation import recommend_candidates, record_selection


def candidates():
    return [
        {"file_id": "F1", "route": ["dumpstate.log"], "path": "dumpstate.log",
         "size": 100, "group": "", "kind": "log"},
        {"file_id": "F1", "route": ["trace.pcap"], "path": "trace.pcap",
         "size": 50, "group": "", "kind": "capture"},
    ]


def test_human_choices_change_recommendations(tmp_path: Path):
    db = tmp_path / "choices.sqlite3"
    pool = candidates()
    for _ in range(8):
        record_selection(pool, [pool[1]], division_code="25", defect_code="P1",
                         user_id="human", source="manual", path=db)

    ranked = recommend_candidates(pool, "25", path=db)
    by_path = {item["path"]: item for item in ranked}
    assert by_path["trace.pcap"]["recommended"] is True
    assert by_path["trace.pcap"]["recommendation_score"] > by_path["dumpstate.log"]["recommendation_score"]


def test_agent_choices_do_not_train_the_recommender(tmp_path: Path):
    db = tmp_path / "choices.sqlite3"
    pool = candidates()
    before = recommend_candidates(pool, "25", path=db)
    for _ in range(20):
        record_selection(pool, [pool[1]], division_code="25", defect_code="P1",
                         user_id="agent", source="agent", path=db)
    after = recommend_candidates(pool, "25", path=db)
    assert [item["recommendation_score"] for item in after] == [
        item["recommendation_score"] for item in before
    ]


def test_recommender_always_keeps_a_fallback(tmp_path: Path):
    db = tmp_path / "choices.sqlite3"
    pool = [{"file_id": "F", "route": ["notes.txt"], "path": "notes.txt",
             "kind": "other", "size": 1, "group": ""}]
    result = recommend_candidates(pool, "25", path=db)
    assert result[0]["recommended"] is True


def test_only_the_full_dump_outranks_its_siblings(tmp_path: Path):
    pool = [
        {"file_id": "F1", "route": ["dumpstate.txt"], "path": "d/dumpstate.txt",
         "size": 300, "group": "", "kind": "log"},
        {"file_id": "F1", "route": ["dumpstate_board.txt"], "path": "d/dumpstate_board.txt",
         "size": 200, "group": "", "kind": "log"},
        {"file_id": "F1", "route": ["dumpstate_log.txt"], "path": "d/dumpstate_log.txt",
         "size": 100, "group": "", "kind": "log"},
        {"file_id": "F1", "route": ["dumpState_FOTA_PROVIDER_fit3plugin0.log"],
         "path": "d/dumpState_FOTA_PROVIDER_fit3plugin0.log",
         "size": 50, "group": "", "kind": "log"},
    ]
    by_path = {item["path"]: item for item in recommend_candidates(pool, "25", path=tmp_path / "c.sqlite3")}
    full = by_path["d/dumpstate.txt"]["recommendation_score"]
    assert by_path["d/dumpstate.txt"]["recommended"] is True
    for other in ("d/dumpstate_board.txt", "d/dumpstate_log.txt",
                  "d/dumpState_FOTA_PROVIDER_fit3plugin0.log"):
        assert by_path[other]["recommendation_score"] < full
        assert by_path[other]["recommended"] is False
