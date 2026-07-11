"""Sidebar rendering for the Streamlit web app."""

import os
import shutil
import time

import streamlit as st

from ui.plm_ui import render_plm_sidebar_stats
from ui.plm_auto_download import LogAnalysisPipeline

_INGESTED_FILES_CACHE_KEY = "ingested_files_cache"
_INGESTED_FILES_CACHE_DIRTY_KEY = "ingested_files_cache_dirty"


def _render_sidebar_style():
    st.markdown(
        """
        <style>
            [data-testid="stSidebar"] {
                color: #242733;
            }

            [data-testid="stSidebar"] section {
                padding-top: 1.1rem;
            }

            [data-testid="stSidebar"] h2,
            [data-testid="stSidebar"] h3 {
                font-size: 1.05rem;
                line-height: 1.3;
                font-weight: 700;
                letter-spacing: 0;
                margin: 0 0 0.65rem;
            }

            [data-testid="stSidebar"] p,
            [data-testid="stSidebar"] label,
            [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {
                font-size: 0.88rem;
                line-height: 1.45;
            }

            [data-testid="stSidebar"] code {
                font-size: 0.82rem;
                color: #256a3b;
                background: #f5f7f8;
                border-radius: 6px;
                padding: 0.12rem 0.38rem;
            }

            [data-testid="stSidebar"] hr {
                margin: 1rem 0;
                border-color: #d7dbe1;
            }

            [data-testid="stSidebar"] .stAlert {
                padding: 0.72rem 0.85rem;
                border-radius: 8px;
            }

            [data-testid="stSidebar"] .stAlert p {
                line-height: 1.45;
                margin-bottom: 0;
            }

            [data-testid="stSidebar"] .stButton > button {
                min-height: 2.35rem;
                border-radius: 8px;
                font-size: 0.9rem;
                font-weight: 600;
                padding: 0.38rem 0.75rem;
                white-space: nowrap;
            }

            [data-testid="stSidebar"] .stButton > button[kind="primary"] {
                background: #d63f3f;
                border-color: #d63f3f;
            }

            [data-testid="stSidebar"] [data-testid="stFileUploader"] {
                margin-top: 0.25rem;
            }

            [data-testid="stSidebar"] [data-testid="stFileUploader"] section {
                padding: 0.9rem;
                border-radius: 8px;
                border-color: #d7dbe1;
            }

            [data-testid="stSidebar"] [data-testid="stFileUploader"] section > div {
                display: flex;
                align-items: center;
                gap: 0.9rem;
            }

            [data-testid="stSidebar"] [data-testid="stFileUploader"] button {
                min-height: 2.2rem;
                border-radius: 8px;
                font-size: 0.88rem;
            }

            [data-testid="stSidebar"] [data-testid="stFileUploader"] small {
                font-size: 0.78rem;
                color: #777d89;
            }

            [data-testid="stSidebar"] .stSelectbox {
                margin-bottom: 0;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _invalidate_ingested_files_cache():
    st.session_state[_INGESTED_FILES_CACHE_DIRTY_KEY] = True


def _render_engine_status():
    st.subheader("분석 엔진")
    st.caption(f"Model: `{st.session_state.get('active_model', 'N/A')}`")


def _get_ingested_files(engine):
    if (
        _INGESTED_FILES_CACHE_KEY not in st.session_state
        or st.session_state.get(_INGESTED_FILES_CACHE_DIRTY_KEY, True)
    ):
        st.session_state[_INGESTED_FILES_CACHE_KEY] = engine.get_all_files()
        st.session_state[_INGESTED_FILES_CACHE_DIRTY_KEY] = False

    return st.session_state[_INGESTED_FILES_CACHE_KEY]


def _render_file_session_manager(engine, reset_analysis_context):
    st.divider()
    st.subheader("분석 세션 및 데이터베이스 관리")

    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("갱신", key="btn_refresh_ingested_files", help="적재 파일 목록을 다시 조회합니다."):
            _invalidate_ingested_files_cache()

    existing_files = _get_ingested_files(engine)
    if existing_files:
        default_idx = existing_files.index(st.session_state.current_file) + 1 if st.session_state.current_file in existing_files else 0
        with col1:
            selected_file = st.selectbox(
                "기존 적재 파일",
                options=["기존 적재파일 선택 안 함"] + existing_files,
                index=default_idx,
                label_visibility="collapsed",
            )
        if selected_file == "기존 적재파일 선택 안 함":
            selected_file = None

        if selected_file and st.session_state.current_file != selected_file:
            st.session_state.current_file = selected_file
            st.toast(f"분석 대상이 '{selected_file}'로 변경되었습니다.")
            st.session_state.messages = []
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
            st.session_state[_INGESTED_FILES_CACHE_KEY] = []
            st.session_state[_INGESTED_FILES_CACHE_DIRTY_KEY] = False
            reset_analysis_context()
            st.success("데이터베이스 및 물리적 파일이 초기화되었습니다.")
            time.sleep(1)
            st.rerun()

def _render_pipeline_controls(engine, run_analysis_pipeline):
    st.divider()
    st.subheader("자동 분석 파이프라인")

    # Check if auto-analysis should be triggered
    if st.session_state.get('trigger_auto_analysis', False):
        st.session_state.trigger_auto_analysis = False
        st.info("🚀 자동 분석 파이프라인 시작 중...")

    # Show analysis queue status
    queue_status = LogAnalysisPipeline.get_queue_status()
    total_in_queue = queue_status['total']

    if total_in_queue > 0:
        col1, col2 = st.columns([2, 1])
        with col1:
            st.info(f"📋 분석 큐: {total_in_queue}개 파일\n⏳ {queue_status['pending']} • 🔄 {queue_status['processing']} • ✅ {queue_status['completed']}")
        with col2:
            if st.button("🗑️ 큐 초기화", key="btn_clear_queue_sidebar"):
                LogAnalysisPipeline.clear_queue()
                st.rerun()

    # 1. 실행 상태를 관리할 세션 변수 초기화
    if "is_running" not in st.session_state:
        st.session_state.is_running = False

    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0

    uploaded_files = st.file_uploader(
        "원시 로그 파일 업로드 (다중 선택 가능)",
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.uploader_key}",
        label_visibility="collapsed",
    )

    # Check for PLM ZIP selected file
    plm_selected_file = st.session_state.get('plm_selected_from_zip')
    if plm_selected_file:
        st.success(f"✅ PLM 파일 준비됨: `{plm_selected_file['filename']}`")

    # Check for PLM analysis queue items
    pending_files = queue_status.get('pending', 0)
    if pending_files > 0:
        st.info(f"Analysis Queue: {pending_files} files pending\n\nManage in 'Analysis Queue' tab")

    # 2. 버튼 클릭 즉시 상태를 '실행 중'으로 변경하는 콜백 함수
    def set_running():
        st.session_state.is_running = True

    # Auto-trigger analysis if:
    # 1. trigger_auto_analysis flag is set OR
    # 2. Queue has pending files and is_running is False
    should_auto_trigger = (st.session_state.get('trigger_auto_analysis', False) or pending_files > 0) and not st.session_state.is_running

    # 3. 버튼에 disabled 속성과 on_click 콜백 적용
    if should_auto_trigger:
        # Auto-trigger: run analysis immediately
        st.session_state.trigger_auto_analysis = False
        st.session_state.is_running = True
        button_click = True
        st.info("Auto-starting analysis with queued files...")
    else:
        button_click = st.button("분석 및 DB 적재 시작", width="stretch", type="primary", on_click=set_running, disabled=st.session_state.is_running)

    if button_click or should_auto_trigger:
        try:
            # Combine uploaded files and PLM selected file
            files_to_analyze = list(uploaded_files) if uploaded_files else []

            if plm_selected_file:
                # Create a file-like object from PLM selected file
                import io
                from types import SimpleNamespace

                plm_file = SimpleNamespace()
                plm_file.name = plm_selected_file['filename']
                plm_file.getbuffer = lambda: plm_selected_file['content']
                files_to_analyze.append(plm_file)

            # If no uploaded files and no PLM files, pending queue items are valid input.
            # Pipeline will handle queue files automatically
            if not files_to_analyze and pending_files == 0:
                st.error("파일을 하나 이상 업로드하거나 PLM에서 선택하십시오.")
            else:
                st.session_state.uploader_key += 1
                run_analysis_pipeline(files_to_analyze, False, "", "", engine)
                _invalidate_ingested_files_cache()
                # Clear PLM selected file after analysis
                st.session_state.plm_selected_from_zip = None

        finally:
            # 4. 분석이 끝나거나 에러가 나더라도 무조건 상태를 해제하고 새로고침
            st.session_state.is_running = False
            st.rerun()

def _reset_analysis_context():
    st.session_state.messages = []
    st.session_state.last_ids = []
    st.session_state.last_metas = []

def render_sidebar(engine, run_analysis_pipeline):
    _render_sidebar_style()
    _render_engine_status()
    render_plm_sidebar_stats()
    _render_pipeline_controls(engine, run_analysis_pipeline)
    _render_file_session_manager(engine, _reset_analysis_context)
