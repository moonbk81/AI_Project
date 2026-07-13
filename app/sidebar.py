"""Sidebar rendering for the Streamlit web app."""

import os
import shutil
import time

import streamlit as st

from ui.plm_ui import render_plm_sidebar_stats

_INGESTED_FILES_CACHE_KEY = "ingested_files_cache"
_INGESTED_FILES_CACHE_DIRTY_KEY = "ingested_files_cache_dirty"


def _render_sidebar_style():
    st.markdown(
        """
        <style>
            [data-testid="stSidebar"] {
                color: var(--text-color);
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
                color: var(--text-color);
                background: var(--secondary-background-color);
                border-radius: 6px;
                padding: 0.12rem 0.38rem;
            }

            [data-testid="stSidebar"] hr {
                margin: 1rem 0;
                border-color: color-mix(in srgb, var(--text-color) 22%, transparent);
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
                background: #d94a4a;
                border-color: #d94a4a;
            }

            [data-testid="stSidebar"] [data-testid="stFileUploader"] {
                margin-top: 0.25rem;
            }

            [data-testid="stSidebar"] [data-testid="stFileUploader"] section {
                padding: 0.9rem;
                border-radius: 8px;
                border-color: color-mix(in srgb, var(--text-color) 22%, transparent);
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
                color: color-mix(in srgb, var(--text-color) 68%, transparent);
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

    # PLM에서 추출되어 분석 대기 중인 로그 파일
    pending_logs = st.session_state.get('plm_pending_logs', [])

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

    # Show PLM extracted logs waiting for analysis
    if pending_logs:
        names = ", ".join(log['filename'] for log in pending_logs)
        st.info(f"📥 PLM 추출 로그 {len(pending_logs)}개 분석 대기 중\n\n{names}")

    # 2. 버튼 클릭 즉시 상태를 '실행 중'으로 변경하는 콜백 함수
    def set_running():
        st.session_state.is_running = True

    # Auto-trigger analysis if:
    # 1. trigger_auto_analysis flag is set OR
    # 2. There are PLM extracted logs pending and is_running is False
    should_auto_trigger = (st.session_state.get('trigger_auto_analysis', False) or bool(pending_logs)) and not st.session_state.is_running

    # 3. 버튼에 disabled 속성과 on_click 콜백 적용
    if should_auto_trigger:
        # Auto-trigger: run analysis immediately
        st.session_state.trigger_auto_analysis = False
        st.session_state.is_running = True
        button_click = True
        st.info("PLM 추출 로그로 분석을 자동 시작합니다...")
    else:
        button_click = st.button("분석 및 DB 적재 시작", width="stretch", type="primary", on_click=set_running, disabled=st.session_state.is_running)

    if button_click or should_auto_trigger:
        try:
            # Combine uploaded files, PLM selected file, and PLM extracted logs
            files_to_analyze = list(uploaded_files) if uploaded_files else []

            from types import SimpleNamespace

            if plm_selected_file:
                # Create a file-like object from PLM selected file
                plm_file = SimpleNamespace()
                plm_file.name = plm_selected_file['filename']
                plm_file.getbuffer = lambda: plm_selected_file['content']
                files_to_analyze.append(plm_file)

            # Create file-like objects from PLM extracted logs
            for log in pending_logs:
                log_file = SimpleNamespace()
                log_file.name = log['filename']
                log_file.getbuffer = lambda content=log['content']: content
                files_to_analyze.append(log_file)

            if not files_to_analyze:
                st.error("파일을 하나 이상 업로드하거나 PLM에서 선택하십시오.")
            else:
                st.session_state.uploader_key += 1
                run_analysis_pipeline(files_to_analyze, False, "", "", engine)
                _invalidate_ingested_files_cache()
                # Clear PLM selected file and extracted logs after analysis
                st.session_state.plm_selected_from_zip = None
                st.session_state.plm_pending_logs = []

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
