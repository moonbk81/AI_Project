"""The queue of log files waiting to be analyzed.

PLM attachment extraction puts files in here; the sidebar pipeline takes them
out when the user starts an analysis. Both sides go through this module so the
session key lives in one place, and so a queued file can be dropped again
without running an analysis just to get rid of it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import streamlit as st

logger = logging.getLogger(__name__)

SESSION_KEY = "plm_pending_logs"
# A single file the user picked out of an opened ZIP, kept separately because
# it never went through extraction.
SELECTED_FILE_KEY = "plm_selected_from_zip"


def pending_logs() -> List[Dict[str, Any]]:
    """Files queued for the next analysis run, in the order they arrived."""
    return st.session_state.get(SESSION_KEY) or []


def add_pending_log(filename: str, content: bytes) -> bool:
    """Register an extracted log file for the analysis pipeline."""
    try:
        if SESSION_KEY not in st.session_state:
            st.session_state[SESSION_KEY] = []

        st.session_state[SESSION_KEY].append({"filename": filename, "content": content})
        logger.info(f"Registered {filename} for analysis (size: {len(content)} bytes)")
        return True
    except Exception as e:
        logger.error(f"Failed to register pending log: {e}")
        return False


def drop_pending_log(index: int) -> None:
    """Take one file back out of the queue."""
    logs = st.session_state.get(SESSION_KEY) or []
    if 0 <= index < len(logs):
        dropped = logs.pop(index)
        logger.info("Dropped %s from the analysis queue", dropped.get("filename"))


def clear_pending_logs() -> None:
    """Empty the queue, including the file picked from an opened ZIP."""
    st.session_state[SESSION_KEY] = []
    st.session_state[SELECTED_FILE_KEY] = None
