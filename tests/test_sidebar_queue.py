"""The analysis queue must be emptyable without running an analysis."""

from streamlit.testing.v1 import AppTest

from app.pending_logs import SESSION_KEY

# Renders just the queue widget, so the test does not boot the whole app.
PROBE = """
import streamlit as st
from app.pending_logs import SESSION_KEY
from app.sidebar import _render_analysis_queue

_render_analysis_queue(st.session_state.get(SESSION_KEY) or [], locked=st.session_state.get("locked", False))
"""


def _queued_app(locked=False):
    app = AppTest.from_string(PROBE, default_timeout=30)
    app.session_state[SESSION_KEY] = [
        {"filename": "dumpstate.log", "content": b"x" * 2048},
        {"filename": "dumpState_2.log", "content": b"y" * 1024},
    ]
    app.session_state["locked"] = locked
    return app.run()


def test_queued_files_are_listed_with_their_size():
    app = _queued_app()

    assert not app.exception
    assert [c.value for c in app.caption] == ["dumpstate.log (2.0 KB)", "dumpState_2.log (1.0 KB)"]
    assert app.info[0].value == "PLM 추출 로그 2개 분석 대기 중"


def test_dropping_one_file_leaves_the_rest_queued():
    app = _queued_app()

    app.button[0].click().run()

    assert [log["filename"] for log in app.session_state[SESSION_KEY]] == ["dumpState_2.log"]


def test_clearing_empties_the_whole_queue():
    app = _queued_app()

    app.button(key="clear_pending_logs").click().run()

    assert app.session_state[SESSION_KEY] == []


def test_a_running_analysis_locks_the_queue():
    """Removing a file mid-run would change what the pipeline is working on."""
    app = _queued_app(locked=True)

    assert all(button.disabled for button in app.button)


def test_an_empty_queue_shows_nothing_at_all():
    app = AppTest.from_string(PROBE, default_timeout=30)
    app.session_state[SESSION_KEY] = []
    app.run()

    assert len(app.button) == 0
    assert len(app.info) == 0
