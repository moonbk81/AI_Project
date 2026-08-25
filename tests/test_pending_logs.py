import pytest
import streamlit as st

from app.pending_logs import (
    SELECTED_FILE_KEY,
    SESSION_KEY,
    add_pending_log,
    clear_pending_logs,
    drop_pending_log,
    pending_logs,
)


@pytest.fixture(autouse=True)
def empty_queue():
    st.session_state[SESSION_KEY] = []
    st.session_state[SELECTED_FILE_KEY] = None
    yield
    st.session_state[SESSION_KEY] = []
    st.session_state[SELECTED_FILE_KEY] = None


def test_queue_keeps_the_order_files_arrived_in():
    add_pending_log("dumpstate.log", b"first")
    add_pending_log("dumpState_2.log", b"second")

    assert [log["filename"] for log in pending_logs()] == ["dumpstate.log", "dumpState_2.log"]
    assert pending_logs()[0]["content"] == b"first"


def test_a_queued_file_can_be_taken_back_out():
    for name in ("a.log", "b.log", "c.log"):
        add_pending_log(name, b"x")

    drop_pending_log(1)

    assert [log["filename"] for log in pending_logs()] == ["a.log", "c.log"]


def test_dropping_a_row_that_is_no_longer_there_is_harmless():
    add_pending_log("a.log", b"x")

    drop_pending_log(5)
    drop_pending_log(-1)

    assert len(pending_logs()) == 1


def test_clearing_also_forgets_the_file_picked_from_a_zip():
    add_pending_log("a.log", b"x")
    st.session_state[SELECTED_FILE_KEY] = {"filename": "picked.log", "content": b"y"}

    clear_pending_logs()

    assert pending_logs() == []
    assert st.session_state[SELECTED_FILE_KEY] is None


def test_queue_reads_as_empty_before_anything_was_added():
    del st.session_state[SESSION_KEY]

    assert pending_logs() == []
    # Reading must not create the key; the first add does that.
    add_pending_log("a.log", b"x")
    assert len(pending_logs()) == 1
