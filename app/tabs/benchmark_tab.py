import streamlit as st
from benchmark_ui import render_benchmark_dashboard

def render_benchmark_tab():
    render_benchmark_dashboard()
    st.divider()
