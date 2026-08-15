import streamlit as st
import ui
from app.backend_client import get_result_json_with_optional_backend

def render_internet_tab():
    current_base = st.session_state.current_file.replace("_payload.json", "") if st.session_state.current_file else None
    data = get_result_json_with_optional_backend(current_base, "internet_stall", default={}) if current_base else {}
    ui.render_internet_stall_analyzer(current_base, data=data)
