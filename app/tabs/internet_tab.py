import streamlit as st
import ui

def render_internet_tab():
    current_base = st.session_state.current_file.replace("_payload.json", "") if st.session_state.current_file else None
    ui.render_internet_stall_analyzer(current_base)
