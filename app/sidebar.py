"""Sidebar rendering for the Streamlit web app."""

import os
import shutil
import time

import streamlit as st

from app.backend_client import (
    create_analyze_job_via_backend,
    get_backend_api_url,
    get_backend_health,
    get_files_with_optional_backend,
    get_job_status_via_backend,
    is_backend_api_enabled,
    reset_db_with_optional_backend,
)
from app.pending_logs import clear_pending_logs, drop_pending_log, pending_logs
from rag.llm_provider import get_llm_provider
from ui.plm_ui import render_plm_sidebar_stats

_INGESTED_FILES_CACHE_KEY = "ingested_files_cache"
_INGESTED_FILES_CACHE_DIRTY_KEY = "ingested_files_cache_dirty"
_INGESTED_FILES_DEFERRED_KEY = "ingested_files_deferred"
_INGESTED_FILES_FORCE_LOAD_KEY = "ingested_files_force_load"
_BACKEND_ANALYSIS_JOB_ID_KEY = "backend_analysis_job_id"
_BACKEND_HEALTH_CACHE_KEY = "backend_health_cache"


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


def _invalidate_ingested_files_cache(force_load: bool = False):
    st.session_state[_INGESTED_FILES_CACHE_DIRTY_KEY] = True
    st.session_state[_INGESTED_FILES_DEFERRED_KEY] = False
    st.session_state[_INGESTED_FILES_FORCE_LOAD_KEY] = force_load


def _get_cached_backend_health():
    try:
        health = get_backend_health()
        st.session_state[_BACKEND_HEALTH_CACHE_KEY] = health
        return health
    except Exception:
        return st.session_state.get(_BACKEND_HEALTH_CACHE_KEY, {})


def _clear_backend_analysis_job():
    st.session_state[_BACKEND_ANALYSIS_JOB_ID_KEY] = None
    st.session_state.is_running = False


def _has_backend_analysis_job() -> bool:
    return bool(is_backend_api_enabled() and st.session_state.get(_BACKEND_ANALYSIS_JOB_ID_KEY))


@st.fragment(run_every="1s")
def _render_backend_analysis_status() -> bool:
    if not is_backend_api_enabled():
        return False

    job_id = st.session_state.get(_BACKEND_ANALYSIS_JOB_ID_KEY)
    if not job_id:
        return False

    try:
        job = get_job_status_via_backend(job_id)
    except Exception as e:
        st.error(f"Backend 분석 작업 상태 조회 실패: {e}")
        _clear_backend_analysis_job()
        return False

    status = job.get("status", "unknown")
    progress = int(job.get("progress") or 0)
    message = job.get("message") or status

    if status == "done":
        st.session_state.current_file = job.get("current_file")
        # Don't clear messages - preserve chat history in the Log Analysis tab
        # Messages should only be cleared when switching files (laline 273)
        clear_pending_logs()
        _invalidate_ingested_files_cache()
        _clear_backend_analysis_job()
        st.toast("Backend 분석 완료")
        st.rerun(scope="app")
        return False

    if status == "error":
        _clear_backend_analysis_job()
        st.error(job.get("error") or "Backend 분석 작업 실패")
        st.rerun(scope="app")
        return False

    st.info(f"Backend 분석 작업 실행 중... `{job_id[:8]}`")
    st.progress(progress)
    st.caption(message)
    return True


def _render_engine_status():
    st.subheader("분석 엔진")
    if is_backend_api_enabled():
        st.caption(f"Backend: `{get_backend_api_url()}`")
        try:
            health = _get_cached_backend_health()
            st.caption(f"Runtime: `{health.get('runtime', 'N/A')}`")
            st.caption(f"Engine: `{health.get('engine_status', 'unknown')}`")
            if health.get("provider"):
                st.caption(f"Provider: `{health.get('provider')}`")
        except Exception as e:
            st.caption(f"Backend 연결 실패: `{e}`")
    else:
        st.caption(f"Provider: `{get_llm_provider()}`")
        st.caption(f"Model: `{st.session_state.get('active_model', 'N/A')}`")


def _get_ingested_files(engine):
    if (
        is_backend_api_enabled()
        and st.session_state.get(_INGESTED_FILES_CACHE_KEY) is None
        and st.session_state.get(_INGESTED_FILES_CACHE_DIRTY_KEY, True)
        and not st.session_state.get(_INGESTED_FILES_FORCE_LOAD_KEY, False)
    ):
        health = _get_cached_backend_health()
        if not health.get("engine_loaded"):
            st.session_state[_INGESTED_FILES_DEFERRED_KEY] = True
            return []

    if (
        _INGESTED_FILES_CACHE_KEY not in st.session_state
        or st.session_state.get(_INGESTED_FILES_CACHE_DIRTY_KEY, True)
    ):
        st.session_state[_INGESTED_FILES_CACHE_KEY] = get_files_with_optional_backend(engine)
        st.session_state[_INGESTED_FILES_CACHE_DIRTY_KEY] = False
        st.session_state[_INGESTED_FILES_DEFERRED_KEY] = False
        st.session_state[_INGESTED_FILES_FORCE_LOAD_KEY] = False

    return st.session_state[_INGESTED_FILES_CACHE_KEY]


def _render_file_session_manager(engine, reset_analysis_context):
    st.divider()
    st.subheader("분석 세션 및 데이터베이스 관리")

    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("갱신", key="btn_refresh_ingested_files", help="적재 파일 목록을 다시 조회합니다."):
            _invalidate_ingested_files_cache(force_load=True)

    existing_files = _get_ingested_files(engine)
    if st.session_state.get(_INGESTED_FILES_DEFERRED_KEY):
        st.caption("파일 목록은 backend 엔진 초기화 후 조회됩니다.")
        with col1:
            st.selectbox(
                "기존 적재 파일",
                options=["기존 적재파일 선택 안 함"],
                index=0,
                label_visibility="collapsed",
                disabled=True,
            )
    elif existing_files:
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
        with col1:
            st.selectbox(
                "기존 적재 파일",
                options=["기존 적재파일 선택 안 함"],
                index=0,
                label_visibility="collapsed",
                disabled=True,
            )
        st.info("데이터베이스가 비어 있습니다. 로그 파일을 업로드하십시오.")
        st.session_state.current_file = None

    if st.session_state.current_file:
        st.success(f"활성 파일: `{st.session_state.current_file}`")

    if st.button("전체 DB 초기화", width="stretch", help="Vector DB의 모든 지식을 삭제합니다."):
        if reset_db_with_optional_backend(engine):
            if not is_backend_api_enabled():
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

def _render_analysis_queue(queued_logs, locked: bool) -> None:
    """List the queued log files, each with a way to drop it again.

    Queuing a defect's attachments is one click, so the queue fills up with
    files the user never meant to analyze. Without a remove button the only way
    out is to run the analysis anyway.
    """
    if not queued_logs:
        return

    st.info(f"📥 PLM 추출 로그 {len(queued_logs)}개 분석 대기 중")

    for index, log in enumerate(queued_logs):
        name_col, drop_col = st.columns([5, 1])
        name_col.caption(f"{log['filename']} ({len(log['content']) / 1024:.1f} KB)")
        if drop_col.button(
            "✕",
            key=f"drop_pending_log_{index}_{log['filename']}",
            help="대기열에서 제거",
            disabled=locked,
        ):
            drop_pending_log(index)
            st.rerun()

    if st.button("대기열 비우기", key="clear_pending_logs", width="stretch", disabled=locked):
        clear_pending_logs()
        st.rerun()


def _render_pipeline_controls(engine, run_analysis_pipeline):
    st.divider()
    st.subheader("자동 분석 파이프라인")

    # PLM에서 추출되어 분석 대기 중인 로그 파일
    queued_logs = pending_logs()

    # 1. 실행 상태를 관리할 세션 변수 초기화
    if "is_running" not in st.session_state:
        st.session_state.is_running = False

    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0

    if _INGESTED_FILES_CACHE_KEY not in st.session_state:
        st.session_state[_INGESTED_FILES_CACHE_KEY] = None

    backend_job_running = _has_backend_analysis_job()
    _render_backend_analysis_status()

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

    _render_analysis_queue(queued_logs, locked=st.session_state.is_running or backend_job_running)

    # 2. 버튼 클릭 즉시 상태를 '실행 중'으로 변경하는 콜백 함수
    def set_running():
        st.session_state.is_running = True

    # 분석/DB 적재는 사용자가 이 버튼을 눌러야만 시작한다.
    # PLM 첨부 로그가 대기 중이라는 이유로 자동 시작하면, 이슈 하나를 고르는 순간
    # 분석이 걸려버려 이전 작업이 끝날 때까지 다른 이슈를 볼 수 없다.
    st.session_state.trigger_auto_analysis = False

    # 3. 버튼에 disabled 속성과 on_click 콜백 적용
    button_click = st.button(
        "분석 및 DB 적재 시작",
        width="stretch",
        type="primary",
        on_click=set_running,
        disabled=st.session_state.is_running or backend_job_running,
    )

    if button_click:
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
        for log in queued_logs:
            log_file = SimpleNamespace()
            log_file.name = log['filename']
            log_file.getbuffer = lambda content=log['content']: content
            files_to_analyze.append(log_file)

        if not files_to_analyze:
            st.error("파일을 하나 이상 업로드하거나 PLM에서 선택하십시오.")
            st.session_state.is_running = False
            return

        st.session_state.uploader_key += 1
        if is_backend_api_enabled():
            try:
                job_id = create_analyze_job_via_backend(files_to_analyze, False, "", "")
            except Exception as e:
                st.session_state.is_running = False
                st.error(f"Backend 분석 작업 생성 실패: {e}")
                return

            st.session_state[_BACKEND_ANALYSIS_JOB_ID_KEY] = job_id
            st.rerun()
            return

        try:
            run_analysis_pipeline(files_to_analyze, False, "", "", engine)
            _invalidate_ingested_files_cache()
            # Clear PLM selected file and extracted logs after analysis
            clear_pending_logs()
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
