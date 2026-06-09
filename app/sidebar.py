
"""Sidebar rendering for the Streamlit web app."""

import os
import shutil
import time
from datetime import datetime

import streamlit as st

from benchmark_ui import get_installed_ollama_models

def _render_sidebar_style():
    st.markdown(
        """
        <style>
            [data-testid="stSidebar"] .stSelectbox, [data-testid="stSidebar"] .stRadio { margin-bottom: -15px; }
            [data-testid="stSidebar"] hr { margin-top: 10px; margin-bottom: 10px; }
            [data-testid="stSidebar"] .stAlert p { line-height: 1.4; margin-bottom: 0px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_engine_settings():
    st.title("System Configuration")
    st.header("분석 엔진 설정")
    st.divider()

    available_models = get_installed_ollama_models()
    if available_models is None:
        available_models = ["gemma4:e4b", "gemma3:12b", "qwen2.5-coder:7b"]

    try:
        current_model_idx = available_models.index(st.session_state['active_model'])
    except ValueError:
        current_model_idx = 0

    ui_model = st.selectbox("AI 모델 선택", options=available_models, index=current_model_idx)

    routing_options = ["semantic", "llm", "hybrid"]
    try:
        current_mode_idx = routing_options.index(st.session_state['active_routing_mode'])
    except ValueError:
        current_mode_idx = 0

    ui_mode = st.radio("라우팅 모드", options=routing_options, index=current_mode_idx)

    if st.button("설정 적용 및 엔진 로드", width="stretch"):
        if (st.session_state['active_model'] != ui_model) or (st.session_state['active_routing_mode'] != ui_mode):
            st.session_state['active_model'] = ui_model
            st.session_state['active_routing_mode'] = ui_mode
            st.session_state['last_loaded_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.toast(f"엔진 설정이 업데이트 되었습니다. ({ui_model} / {ui_mode})")
            st.rerun()
        else:
            st.info("변경 사항이 없습니다.")

    st.divider()
    st.markdown("### 현재 활성 상태")
    st.info(
        f"**Model:** `{st.session_state['active_model']}`  \n"
        f"**Mode:** `{st.session_state['active_routing_mode']}`  \n"
        f"**Loaded:** `{st.session_state['last_loaded_at']}`"
    )

def _render_file_session_manager(engine, reset_analysis_context):
    st.divider()
    st.subheader("분석 세션 및 데이터베이스 관리")

    existing_files = engine.get_all_files()
    if existing_files:
        default_idx = existing_files.index(st.session_state.current_file) + 1 if st.session_state.current_file in existing_files else 0
        selected_file = st.selectbox("기존 적재 파일 선택", options=["선택 안 함"] + existing_files, index=default_idx)
        if selected_file != "선택 안 함" and st.session_state.current_file != selected_file:
            st.session_state.current_file = selected_file
            st.toast(f"분석 대상이 '{selected_file}'로 변경되었습니다.")
            st.session_state.messages = []
            st.rerun()
    else:
        st.info("데이터베이스가 비어 있습니다. 로그 파일을 업로드하십시오.")
        st.session_state.current_file = None

    if st.session_state.current_file:
        st.success(f"활성 파일: `{st.session_state.current_file}`")

    if st.button("전체 DB 초기화", width="stretch", help="Vector DB의 모든 지식을 삭제합니다."):
        if engine.reset_db():
            for folder in ["./payloads", "./result", "./temp_logs"]:
                if os.path.exists(folder):
                    shutil.rmtree(folder)
                os.makedirs(folder, exist_ok=True)
            st.session_state.current_file = None
            reset_analysis_context()
            st.success("데이터베이스 및 물리적 파일이 초기화되었습니다.")
            time.sleep(1)
            st.rerun()

def _render_pipeline_controls(engine, run_analysis_pipeline):
    st.divider()
    st.header("자동 분석 파이프라인")

    uploaded_files = st.file_uploader(
        "원시 로그 파일 업로드 (다중 선택 가능)",
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.uploader_key}",
    )

    use_slicing = st.checkbox("타임라인 슬라이싱 활성화 (대용량 로그 권장)")
    start_time, end_time = "", ""
    if use_slicing:
        st.info("이슈 발생 시점 기준 전후 5~10분으로 범위를 제한하면 분석 효율이 향상됩니다.")
        col1, col2 = st.columns(2)
        with col1:
            start_time = st.text_input("Start (ex: 04-12 14:00:00)")
        with col2:
            end_time = st.text_input("End (ex: 04-12 14:15:00)")

    if st.button("분석 및 DB 적재 시작", width="stretch", type="primary"):
        if not uploaded_files:
            st.error("파일을 하나 이상 업로드하십시오.")
        elif use_slicing and (not start_time or not end_time):
            st.error("슬라이싱 범위를 명확히 입력하십시오.")
        else:
            run_analysis_pipeline(uploaded_files, use_slicing, start_time, end_time, engine)

def _reset_analysis_context():
    st.session_state.messages = []
    st.session_state.last_ids = []
    st.session_state.last_metas = []

def render_sidebar(engine, run_analysis_pipeline):
    _render_sidebar_style()
    _render_engine_settings()
    _render_pipeline_controls(engine, run_analysis_pipeline)
    _render_file_session_manager(engine, _reset_analysis_context)
