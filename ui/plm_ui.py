"""
PLM Defect Management UI - Streamlit Components

Provides Streamlit UI components for PLM defect management integration.
Follows the same pattern as other UI modules (crash_ui, network_ui, etc).
"""

import streamlit as st
from typing import Optional, List, Dict, Any
import pandas as pd
from datetime import datetime
import logging
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.pending_logs import (
    SESSION_KEY as PENDING_LOGS_KEY,
    add_pending_log,
    clear_pending_logs,
    pending_logs,
)
from app.backend_client import (
    is_backend_api_enabled,
    plm_analyze_with_optional_backend,
    plm_download_file_with_optional_backend,
    plm_get_defect_details_with_optional_backend,
    plm_get_human_comments_with_optional_backend,
    plm_list_files_with_optional_backend,
    plm_quick_search_with_optional_backend,
    plm_refine_description_with_optional_backend,
    plm_register_defect_with_optional_backend,
    plm_submit_comment_with_optional_backend,
)
from plm.plm_rag_integration import (
    create_plm_integration,
    PLMConfigManager
)
from core.log_archive import extract_file, list_root_contents
from plm import log_pipeline
from plm import local_test as plm_local_test
from plm.comments import build_comment_payload, format_analysis_as_comment
from plm.registration import build_defect_payload
from plm.tables import build_archive_rows, build_attachment_rows, build_defect_rows
from plm.plm_api_client import PLMAPIException
logger = logging.getLogger(__name__)


def _queue_attachment_logs(filename: str, content: bytes) -> None:
    """Queue the logs of one downloaded attachment and report what happened."""
    outcome = log_pipeline.inspect_attachment(filename, content)

    if outcome.kind == log_pipeline.NOT_AN_ARCHIVE:
        st.info(f"{filename}은 ZIP 이 아니라 추출할 LOG 파일이 없습니다. 다운로드 버튼으로 내려받으세요.")
        return

    if outcome.kind == log_pipeline.NO_LOGS_IN_ARCHIVE:
        st.warning(f"{filename} 안에서 인식 가능한 LOG 파일을 찾지 못했습니다.")
        return

    queued = _queue_extracted_logs(outcome)
    st.success(f"{queued}개 LOG 파일을 분석 대기열에 추가했습니다 - Sidebar 에서 분석을 시작하세요")
    for log_name in outcome.logs:
        st.caption(f"  • {log_name}")
    st.rerun()


def _queue_extracted_logs(outcome) -> int:
    """Put every log of a `LOGS_FOUND` outcome into the analysis queue."""
    queued = 0
    for filename, content in outcome.logs.items():
        if add_pending_log(filename, content):
            queued += 1
    return queued


def _is_plm_local_test_mode() -> bool:
    return bool(st.session_state.get('plm_local_test_mode', False))


def _get_plm_local_test_defects() -> List[Dict[str, Any]]:
    """Sample defects for offline testing, shared with the API layer."""
    return list(plm_local_test.SAMPLE_DEFECTS)


def _apply_plm_local_test_data(force: bool = False):
    """Seed sample PLM state so offline UI flows can be tested."""
    if not _is_plm_local_test_mode():
        return

    if force or not st.session_state.get('plm_quick_search_results'):
        sample_defects = _get_plm_local_test_defects()
        st.session_state.plm_quick_search_results = sample_defects
        st.session_state.plm_quick_search_division = "25"
        st.session_state.plm_quick_search_label = "Local Test"
        st.session_state.plm_quick_search_status = "Open"
        st.session_state.plm_quick_search_selected_index = 0

        first_defect = sample_defects[0]
        st.session_state.plm_active_defect_code = first_defect.get('defectCode')
        st.session_state.plm_active_division = "25"


def _render_plm_local_test_controls():
    """Render global PLM local test controls."""
    # The switch itself lives in plm/local_test.py so the browser UI and the
    # API see the same mode; PLM_LOCAL_TEST seeds it.
    if 'plm_local_test_mode' not in st.session_state:
        st.session_state.plm_local_test_mode = plm_local_test.is_enabled()

    local_test = st.checkbox(
        "PLM 로컬 테스트 모드",
        key="plm_local_test_mode",
        help="사내 PLM 연결 없이 샘플 defect와 comment 등록 UI를 테스트합니다.",
    )
    plm_local_test.set_enabled(local_test)

    if local_test:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption("샘플 defect list와 active defect를 사용합니다. 실제 PLM API 호출은 comment 로컬 테스트에서 수행하지 않습니다.")
        with col2:
            if st.button("샘플 재생성", key="btn_seed_plm_local_test"):
                _apply_plm_local_test_data(force=True)
                st.rerun()

        _apply_plm_local_test_data()
    elif st.session_state.get('plm_quick_search_label') == "Local Test":
        st.session_state.plm_quick_search_results = None
        st.session_state.plm_quick_search_division = None
        st.session_state.plm_quick_search_label = None
        st.session_state.plm_quick_search_status = None
        st.session_state.plm_quick_search_selected_index = 0
        st.session_state.plm_active_defect_code = None
        st.session_state.plm_active_division = None


def _refine_problem_description(problem_content: str) -> str:
    """Condense a PLM problem description before sending it to the Chat tab.

    The LLM call lives in the backend (POST /plm/refine-description); this used to
    hit the gateway straight from the UI process, bypassing the backend entirely.
    """
    return plm_refine_description_with_optional_backend(
        problem_content,
        model=st.session_state.get('active_model', ''),
    )


def _initialize_plm_session():
    """Initialize Streamlit session state for PLM"""
    import time
    start_time = time.time()

    defaults = {
        'plm_local_test_mode': False,
        'plm_cache': {},
        'plm_search_results': None,
        'plm_search_division': None,
        'plm_quick_search_results': None,
        'plm_quick_search_division': None,
        'plm_quick_search_label': None,
        'plm_quick_search_status': None,
        'plm_analysis_results': None,
        'plm_selected_defect_code': None,
        'plm_selected_division': None,
        'plm_files_list': None,
        'plm_download_data': {},
        'plm_zip_file_data': None,
        'plm_zip_file_list': {},
        'plm_selected_from_zip': None,
        PENDING_LOGS_KEY: [],
        'plm_active_defect_code': None,
        'plm_active_division': None,
        'plm_current_analysis_result': None,
        'plm_groups_cache': {},
        'plm_groups_loading': False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    if 'plm_integration' not in st.session_state:
        try:
            plm_start = time.time()
            st.session_state.plm_integration = create_plm_integration()
            st.session_state.plm_available = True
            logger.info(f"PLM integration initialized in {time.time() - plm_start:.2f}s")
            # Groups will be lazy-loaded when actually needed (not on init)
        except Exception as e:
            logger.error(f"Failed to initialize PLM: {e}")
            st.session_state.plm_available = False
            st.session_state.plm_integration = None

    logger.info(f"_initialize_plm_session completed in {time.time() - start_time:.2f}s")


def _lazy_load_groups():
    """Lazy-load groups in background when needed"""
    import time
    if st.session_state.get('plm_groups_loading', False) or st.session_state.get('plm_groups_cache'):
        return  # Already loading or loaded

    st.session_state.plm_groups_loading = True
    try:
        start_time = time.time()
        config_manager = PLMConfigManager()
        st.session_state.plm_groups_cache = config_manager.get_groups_by_division("25")
        elapsed = time.time() - start_time
        logger.info(f"Groups pre-loaded: {len(st.session_state.plm_groups_cache)} groups in {elapsed:.2f}s")
    except Exception as e:
        logger.warning(f"Failed to pre-load groups: {e}")
        st.session_state.plm_groups_cache = {}
    finally:
        st.session_state.plm_groups_loading = False


def _get_plm_client():
    """Get PLM API client from session state"""
    if not st.session_state.get('plm_available', False):
        return None
    return st.session_state.plm_integration.client


def _auto_load_and_process_defect_files(defect: Dict[str, Any], division_code: str, selected_index: int = 0):
    """
    Automatically load files and start background processing when defect is selected

    This function:
    1. Auto-loads attached files from PLM
    2. Auto-downloads ZIP files
    3. Extracts LOG files and adds them to analysis queue

    Args:
        defect: Selected defect dictionary
        division_code: PLM division code
        selected_index: Current row index in the table
    """
    import time
    auto_start = time.time()
    defect_code = defect.get('defectCode')
    if not defect_code:
        return

    # Skip if files already loaded for this defect
    if defect_code in st.session_state.get('plm_quick_search_files', {}):
        logger.info(f"Files already loaded for {defect_code}, skipping auto-load")
        return

    # Skip if auto-processing is already in progress
    if st.session_state.get(f'plm_auto_processing_{defect_code}'):
        logger.info(f"Auto-processing already in progress for {defect_code}")
        return

    # Mark as in progress & preserve selection state before rerun
    st.session_state[f'plm_auto_processing_{defect_code}'] = True
    st.session_state.plm_quick_search_selected_index = selected_index
    logger.info(f"Starting auto-load for {defect_code} (preserving selection index {selected_index})")

    # Initialize session state if needed
    if 'plm_quick_search_files' not in st.session_state:
        st.session_state.plm_quick_search_files = {}
    if 'plm_quick_search_downloads' not in st.session_state:
        st.session_state.plm_quick_search_downloads = {}

    # Show progress container
    progress_container = st.container()

    try:
        with progress_container.status("📥 PLM 첨부 파일 자동 처리 중...", expanded=True) as status:
            client = _get_plm_client()
            logger.info(f"PLM client check: client={client is not None}, local_test={_is_plm_local_test_mode()}, backend_enabled={is_backend_api_enabled()}")

            if not client and not _is_plm_local_test_mode() and not is_backend_api_enabled():
                status.update(label="❌ PLM 클라이언트 연결 실패", state="error")
                st.session_state[f'plm_auto_processing_{defect_code}'] = False
                logger.error(f"Auto-load cancelled: no PLM client available for {defect_code}")
                return

            # Get file list
            if _is_plm_local_test_mode():
                st.write("🧪 로컬 테스트 모드: 파일 목록이 비어있습니다")
                st.session_state.plm_quick_search_files[defect_code] = {
                    'files': [],
                    'division_code': division_code,
                    'defect_code': defect_code,
                }
                status.update(label="✅ 파일 로드 완료 (로컬 테스트 모드)", state="complete", expanded=False)
                st.session_state[f'plm_auto_processing_{defect_code}'] = False
                return

            st.write("📋 파일 목록 조회 중...")
            result = plm_list_files_with_optional_backend(
                client,
                division_code=division_code,
                defect_code=defect_code
            )

            if not result.get("success"):
                status.update(label="❌ 파일 목록 조회 실패", state="error")
                st.session_state[f'plm_auto_processing_{defect_code}'] = False
                return

            files = result.get("files", [])

            if files:
                st.write(f"✅ {len(files)}개 파일 발견")
            else:
                st.write("ℹ️ 첨부 파일이 없습니다")

            # Store files in session state
            st.session_state.plm_quick_search_files[defect_code] = {
                'files': files,
                'division_code': division_code,
                'defect_code': defect_code
            }

            # Auto-download and process ZIP files in background
            total_logs = 0
            if files:
                total_logs = _auto_download_and_extract_logs(defect_code, division_code, files, status)
                if total_logs > 0:
                    status.update(label=f"✅ {total_logs}개 LOG 파일 로드 완료 - Sidebar에서 분석 시작", state="complete", expanded=False)
                else:
                    status.update(label="✅ 자동 처리 완료 (LOG 파일 없음)", state="complete", expanded=False)
            else:
                status.update(label="✅ 파일 로드 완료", state="complete", expanded=False)

            logger.info(f"Auto-load for {defect_code} completed in {time.time() - auto_start:.2f}s")

    except Exception as e:
        logger.error(f"Error auto-loading files for {defect_code}: {e}", exc_info=True)
        progress_container.error(f"❌ 오류 발생: {e}")
    finally:
        st.session_state[f'plm_auto_processing_{defect_code}'] = False
        logger.info(f"Auto-load cleanup for {defect_code}, total time: {time.time() - auto_start:.2f}s")


# How many names of a non-matching archive are worth listing before it turns
# into a wall of text.
_ARCHIVE_PREVIEW_LIMIT = 15


def _write_extraction_event(event) -> None:
    """Render one pipeline event as a progress line."""
    if event.kind == log_pipeline.NO_ARCHIVE_ATTACHMENTS:
        st.write("ℹ️ 압축 파일(ZIP/7z)이 없습니다")
    elif event.kind == log_pipeline.ARCHIVE_ATTACHMENTS_FOUND:
        st.write(f"📦 {event.total}개 압축 파일 발견")
    elif event.kind == log_pipeline.DOWNLOADING:
        st.write(f"⬇️ [{event.index}/{event.total}] {event.title} 다운로드 중...")
    elif event.kind == log_pipeline.DOWNLOAD_FAILED:
        st.write(f"❌ {event.title} 다운로드 실패: {event.error}")
    elif event.kind == log_pipeline.DOWNLOAD_EMPTY:
        st.write(f"❌ {event.title} 데이터 없음")
    elif event.kind == log_pipeline.EXTRACTING:
        st.write(f"📂 {event.title}에서 LOG 파일 추출 중...")
    elif event.kind == log_pipeline.LOGS_EXTRACTED:
        st.write(f"✅ {event.count}개 LOG 파일 추출됨")
    elif event.kind == log_pipeline.LOG_READY:
        st.write(f"  ➕ {event.filename} → 분석 큐에 추가됨")
    elif event.kind == log_pipeline.NO_LOGS_MATCHED:
        _write_no_logs_matched(event)
    elif event.kind == log_pipeline.ATTACHMENT_FAILED:
        st.write(f"❌ {event.title} 처리 중 오류: {event.error}")


def _write_no_logs_matched(event) -> None:
    st.write(f"ℹ️ {event.title}에서 인식 가능한 LOG 파일을 찾지 못했습니다")
    if not event.contents:
        st.write("  압축 파일에서 읽을 수 있는 파일이 없습니다")
        return

    st.write(f"  압축 파일 안의 파일 {len(event.contents)}개:")
    for name, size in list(event.contents.items())[:_ARCHIVE_PREVIEW_LIMIT]:
        st.write(f"  · {name} ({size / 1024:.1f} KB)")
    if len(event.contents) > _ARCHIVE_PREVIEW_LIMIT:
        st.write(f"  · ... 외 {len(event.contents) - _ARCHIVE_PREVIEW_LIMIT}개")
    st.caption(
        "위 이름이 dumpstate 계열이 아니면 자동 인식 대상이 아닙니다. "
        "'검색 및 파일' 탭의 ZIP 열기로 직접 선택할 수 있습니다."
    )


def _auto_download_and_extract_logs(defect_code: str, division_code: str, files: List[Dict[str, Any]], status=None) -> int:
    """Download the defect's ZIP attachments and queue the logs found inside.

    Args:
        defect_code: Defect code
        division_code: PLM division code
        files: List of attached files
        status: Streamlit status object; progress lines are written when set

    Returns:
        Total number of LOG files extracted and added to queue
    """
    if not files:
        return 0

    if not log_pipeline.select_archive_attachments(files):
        if status:
            st.write("ℹ️ 압축 파일(ZIP/7z)이 없습니다")
        return 0

    client = _get_plm_client()
    if not client and not is_backend_api_enabled():
        logger.warning("PLM client unavailable while auto-downloading ZIP files for %s", defect_code)
        if status:
            st.write("❌ PLM 클라이언트 연결 실패")
        return 0

    def download(doc_id, title, file_id):
        return plm_download_file_with_optional_backend(
            client,
            division_code=division_code,
            doc_id=doc_id,
            title=title,
            file_id=file_id,
        )

    total_logs_found = 0
    for event in log_pipeline.extract_logs_from_attachments(files, download):
        if event.kind == log_pipeline.LOG_READY:
            logger.info(f"Adding {event.filename} to analysis queue")
            add_pending_log(event.filename, event.content)
            total_logs_found += 1
        if status:
            _write_extraction_event(event)

    if total_logs_found > 0 and status:
        st.write(f"\n🎯 총 {total_logs_found}개 LOG 파일이 분석 큐에 추가되었습니다")

    return total_logs_found


def render_plm_search():
    """
    Render PLM defect search interface

    Allows users to search defects by code or ID and view details
    """
    st.subheader("🔍 Search Defects")

    # Display cached results if available
    if st.session_state.get('plm_search_results'):
        with st.container():
            col1, col2 = st.columns([3, 1])
            with col1:
                st.info(f"📌 Cached results: {len(st.session_state.plm_search_results)} defect(s)")
            with col2:
                if st.button("Clear Cache", key="btn_clear_search"):
                    st.session_state.plm_search_results = None
                    st.session_state.plm_search_division = None
                    st.session_state.plm_search_selected_index = 0
                    st.rerun()

            st.divider()
            st.write("**Select a defect to view details:**")
            selected_index = _render_selectable_defects_table_for_search(st.session_state.plm_search_results)

            # Show details for selected defect
            if selected_index is not None and 0 <= selected_index < len(st.session_state.plm_search_results):
                selected_defect = st.session_state.plm_search_results[selected_index]
                st.session_state.plm_selected_defect_code = selected_defect.get('defectCode')
                st.session_state.plm_selected_division = st.session_state.plm_search_division
                st.divider()
                st.subheader(f"📋 Details: {selected_defect.get('defectCode')}")
                _render_defect_details(selected_defect, st.session_state.plm_search_division)

            st.divider()
            st.markdown("**New Search**")

    col1, col2 = st.columns(2)

    with col1:
        division = st.selectbox(
            "Division",
            options=["Mobile", "Network"],
            format_func=lambda x: f"{x} ({'25' if x == 'Mobile' else '26'})",
            key="search_division"
        )

    division_code = "25" if division == "Mobile" else "26"

    with col2:
        search_type = st.radio(
            "Search by",
            options=["Code", "ID"],
            horizontal=True,
            key="search_type"
        )

    # Search input
    if search_type == "Code":
        search_input = st.text_input(
            "Defect Code",
            placeholder="e.g., P190404-00007",
            help="Enter codes separated by commas (max 99)",
            key="search_code"
        )
        search_values = [code.strip() for code in search_input.split(",") if code.strip()]
        is_code_search = True
    else:
        search_input = st.text_input(
            "Defect ID",
            placeholder="e.g., 00EIYX38PtPMWL1000",
            help="Enter IDs separated by commas",
            key="search_id"
        )
        search_values = [id.strip() for id in search_input.split(",") if id.strip()]
        is_code_search = False

    if st.button("🔍 Search", key="btn_search_defects"):
        if not search_values:
            st.error("Please enter at least one defect code or ID")
            return

        with st.spinner("Searching defects..."):
            try:
                client = _get_plm_client()
                if not client and not is_backend_api_enabled():
                    st.error("PLM API not configured")
                    return

                result = plm_get_defect_details_with_optional_backend(
                    client,
                    division_code=division_code,
                    defect_codes=search_values if is_code_search else None,
                    defect_ids=search_values if not is_code_search else None
                )

                if result.get("success"):
                    defects = result.get('defects', [])

                    if defects:
                        st.session_state.plm_search_results = defects
                        st.session_state.plm_search_division = division_code
                        # Store first defect code for use in other tabs
                        if len(search_values) == 1:
                            st.session_state.plm_selected_defect_code = search_values[0]
                            st.session_state.plm_selected_division = division_code
                        st.success(f"Found {len(defects)} defect(s)")

                        # Use selectable table for search results
                        st.divider()
                        st.write("**Select a defect to view details:**")
                        selected_index = _render_selectable_defects_table_for_search(defects)

                        # Show details for selected defect
                        if selected_index is not None and 0 <= selected_index < len(defects):
                            selected_defect = defects[selected_index]
                            st.session_state.plm_selected_defect_code = selected_defect.get('defectCode')
                            st.session_state.plm_selected_division = division_code
                            st.divider()
                            st.subheader(f"📋 Details: {selected_defect.get('defectCode')}")
                            _render_defect_details(selected_defect, division_code)
                    else:
                        st.info("No defects found")

                else:
                    st.error(f"Search failed: {result.get('message', 'Unknown error')}")

            except PLMAPIException as e:
                st.error(f"API Error: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                st.error(f"Error: {e}")


def _render_defects_table(defects: List[Dict[str, Any]], key: str):
    """Draw the selectable defect table and hand back Streamlit's selection state.

    The two search tabs show the same table but react to a click differently,
    so only the widget itself is shared.
    """
    return st.dataframe(
        pd.DataFrame(build_defect_rows(defects)),
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
        column_config={
            "Code": st.column_config.LinkColumn(
                "Code",
                display_text=r"#([^#]+)$",
                help="Open this defect in PLM",
                width="medium",
            ),
            "Title": st.column_config.TextColumn("Title", width="large"),
            "Status": st.column_config.TextColumn("Status", width="small"),
            "Priority": st.column_config.TextColumn("Priority", width="small"),
            "Owner": st.column_config.TextColumn("Owner", width="medium"),
            "Created": st.column_config.TextColumn("Created", width="small"),
        },
    )


def _selected_rows(table_state) -> List[int]:
    return list(table_state.selection.rows) if table_state and table_state.selection else []


def _clamped_index(index: int, defects: List[Dict[str, Any]]) -> int:
    """A stored row index goes stale as soon as a new search returns fewer rows."""
    return index if 0 <= index < len(defects) else 0


def _render_selectable_defects_table_for_search(defects: List[Dict[str, Any]]) -> Optional[int]:
    """Render search results as a selectable table and return selected row index."""
    table_state = _render_defects_table(defects, key="search_results_table")

    selected_rows = _selected_rows(table_state)
    if selected_rows:
        selected_index = _clamped_index(selected_rows[0], defects)
        st.session_state.plm_search_selected_index = selected_index
        return selected_index

    selected_index = _clamped_index(st.session_state.get('plm_search_selected_index', 0), defects)
    st.session_state.plm_search_selected_index = selected_index
    return selected_index


def _render_selectable_defects_table(defects: List[Dict[str, Any]]) -> int:
    """Render Quick Search results as a selectable table and return selected row index."""
    table_state = _render_defects_table(defects, key="quick_search_results_table")

    if not defects:
        return 0

    # single-row mode: Streamlit itself clears the previous row when a new one is
    # picked, so `rows` holds at most one index.
    selected_rows = _selected_rows(table_state)
    prev_selected_rows = st.session_state.get('plm_quick_search_prev_selected_rows', [])
    division_code = st.session_state.get('plm_quick_search_division')

    if selected_rows:
        selected_index = selected_rows[0]
        selection_source = "user_selected"
    else:
        # Nothing checked (first render, or the user cleared the row): show the
        # previously viewed defect, defaulting to the first one.
        selected_index = st.session_state.get('plm_quick_search_selected_index', 0)
        selection_source = "default"

    selected_index = _clamped_index(selected_index, defects)

    selected_defect = defects[selected_index]
    defect_code = selected_defect.get('defectCode')

    # Only kick off work when the checked row actually changed, not on every rerun.
    if selected_rows and selected_rows != prev_selected_rows:
        logger.info(f"Row selected: {defect_code} (index {selected_index})")
        if division_code:
            # Logs from the previously selected defect must not leak into this one.
            clear_pending_logs()
            # Queue the download/extract so it runs after the details have rendered.
            st.session_state.plm_pending_auto_load = {
                'defect': selected_defect,
                'division_code': division_code,
                'selected_index': selected_index,
            }
        else:
            logger.warning(f"Row {selected_index} selected but division_code not set")

    st.session_state.plm_quick_search_prev_selected_rows = selected_rows
    st.session_state.plm_quick_search_selected_index = selected_index

    # Active defect always mirrors the row whose details are shown below.
    st.session_state.plm_active_defect_code = defect_code
    st.session_state.plm_active_division = division_code
    logger.info(f"Active defect set: {defect_code} (index {selected_index}, source: {selection_source})")

    # Paint the sidebar slot right now. The end-of-script refresh in web_app.py
    # would otherwise only land after the (blocking) attachment auto-download
    # below, which is why the sidebar used to lag a whole selection behind.
    _write_plm_active_defect_slot()

    return selected_index


def _fetch_human_comments(defect_code: str, division_code: str) -> List[Dict[str, Any]]:
    """
    Fetch developer-written comments for a defect via get_defect_history.

    The history API does not expose a per-comment systemCode, so "human" comments
    are identified as historyType == 'C' entries with non-empty text that are not
    this tool's own AI-generated comments.
    """
    if _is_plm_local_test_mode():
        return [
            {
                'comment': '[Network팀] 이관합니다. 5G 안테나가 풀인데도 throughput이 안 나옵니다. NSA/SA 전환 구간이 의심됩니다.',
                'historyDate': '2026-07-13 10:32:11',
                'historyUser': 'Jinsu Park/Network Group',
                'commentId': 'LOCAL_C0001',
            },
            {
                'comment': '특정 gNB에서만 PDCP 재전송이 급증하는 로그를 확인했습니다. 첨부 로그 7시 11분대 참고 부탁드립니다.',
                'historyDate': '2026-07-13 11:05:44',
                'historyUser': 'Hana Kim/Modem Group',
                'commentId': 'LOCAL_C0002',
            },
        ]

    # Cache per defect so checkbox toggles (which rerun the script) don't re-hit the API.
    cache = st.session_state.setdefault('plm_defect_comments_cache', {})
    if defect_code in cache:
        return cache[defect_code]

    try:
        client = _get_plm_client()
        if not client and not is_backend_api_enabled():
            return []

        result = plm_get_human_comments_with_optional_backend(
            client,
            division_code=division_code,
            defect_code=defect_code,
        )
        if not result.get("success"):
            logger.warning(f"get_human_comments failed: {result.get('message', 'Unknown error')}")
            return []
        comments = result.get("comments", [])
    except Exception as e:
        logger.error(f"Error fetching defect comments: {e}", exc_info=True)
        return []

    cache[defect_code] = comments
    return comments


def _render_defect_details(defect: Dict[str, Any], division_code: str):
    """Render detailed view of a defect"""
    # Validate input is a dictionary
    if not isinstance(defect, dict):
        st.error(f"Invalid defect data: expected dict, got {type(defect)}")
        return

    # Key metrics
    status = defect.get('plmStatus', 'N/A')
    priority = defect.get('plmPriority', 'N/A')
    owner = defect.get('mainOwnerName', 'N/A')
    created = defect.get('createDate', 'N/A')

    # Format owner (truncate if too long)
    if isinstance(owner, str) and owner != 'N/A' and len(owner) > 20:
        owner = owner[:20]

    # Format created date (get first 10 chars)
    if isinstance(created, str) and created != 'N/A' and len(created) > 10:
        created = created[:10]

    # Display in 2x2 grid
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Status", str(status) if status is not None else 'N/A')
    with col2:
        st.metric("Priority", str(priority) if priority is not None else 'N/A')

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Owner", str(owner) if owner is not None else 'N/A')
    with col2:
        st.metric("Created", str(created) if created is not None else 'N/A')

    # Problem description
    problem_content = defect.get('content', 'N/A')
    with st.expander("📌 Problem"):
        if problem_content and problem_content != 'N/A':
            st.write(problem_content)
        else:
            st.info("No problem content available")

        current_defect_code = defect.get('defectCode')
        defect_code_str = str(current_defect_code) if current_defect_code else "unknown"

        # Developer comments carried over from other teams.
        comments = _fetch_human_comments(current_defect_code, division_code) if current_defect_code else []

        # Check if we already sent this problem, to avoid duplicate processing.
        plm_query = st.session_state.get('plm_problem_query')
        is_already_sent = bool(
            plm_query and isinstance(plm_query, dict)
            and plm_query.get('defect_code') == current_defect_code
            and not st.session_state.get('plm_problem_analyzed', False)
        )

        # Comment checkboxes + the analyze button live inside a form so that toggling
        # a checkbox does NOT trigger a full-app rerun (which dims the whole screen).
        # Streamlit only reruns on form submit ("분석하기").
        with st.form(f"analyze_form_{defect_code_str}"):
            if comments:
                st.markdown(f"**💬 등록된 개발자 코멘트 ({len(comments)}건)**")
                st.caption("분석에 함께 반영할 코멘트를 선택하세요. (AI 자동 분석 코멘트는 제외됨)")
                for idx, cmt in enumerate(comments):
                    meta = " · ".join(
                        x for x in [cmt.get('historyUser', ''), cmt.get('historyDate', '')] if x
                    )
                    st.checkbox(meta or f"Comment {idx + 1}", key=f"cmt_sel_{defect_code_str}_{idx}")
                    st.caption(cmt.get('comment', ''))
                st.divider()

            col1, col2 = st.columns([3, 1])
            with col2:
                submitted = st.form_submit_button(
                    "🚀 분석하기",
                    help="Send this problem to Chat tab for analysis",
                    disabled=is_already_sent,
                )

        if is_already_sent:
            st.caption("⏳ Pending analysis")

        if submitted:
            # Read checkbox selections from session state (set inside the form).
            selected_comments = [
                cmt for idx, cmt in enumerate(comments)
                if st.session_state.get(f"cmt_sel_{defect_code_str}_{idx}")
            ]

            # Refine problem description before sending to Chat
            with st.spinner("💡 Refining problem description..."):
                refined_content = _refine_problem_description(problem_content)

            # Store refined problem content in session for Chat tab
            st.session_state.plm_problem_query = {
                'content': refined_content,
                'original_content': problem_content,
                'defect_code': defect.get('defectCode'),
                'defect_title': defect.get('plmTitle', 'Unknown'),
                'reason': defect.get('reason', ''),
                'countermeasure': defect.get('countermeasure', ''),
                'status': defect.get('plmStatus', ''),
                'priority': defect.get('plmPriority', ''),
                'owner': defect.get('mainOwnerName', ''),
                'created_date': defect.get('createDate', ''),
                'comments': [
                    {
                        'user': c.get('historyUser', ''),
                        'date': c.get('historyDate', ''),
                        'text': c.get('comment', ''),
                    }
                    for c in selected_comments
                ],
                'timestamp': datetime.now().isoformat()
            }
            st.session_state.plm_problem_analyzed = False  # Reset analyzed flag
            st.session_state.plm_last_analyzed_code = current_defect_code
            st.session_state.navigate_to_chat = True  # Flag to navigate to chat tab
            st.success("✅ Problem refined! Navigating to Log Analysis tab...")
            st.rerun()  # Rerun to apply navigation

            # Show status if already sent
            if is_already_sent:
                st.caption("⏳ Pending analysis")

    # Root cause
    with st.expander("🔍 Root Cause"):
        st.write(defect.get('reason', 'N/A'))

    # Solution
    with st.expander("✅ Solution"):
        st.write(defect.get('countermeasure', 'N/A'))

    # Steps to reproduce
    with st.expander("📋 Steps to Reproduce"):
        st.write(defect.get('reappearancePath', 'N/A'))

    # Additional details
    with st.expander("⚙️ Technical Details"):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.write("**Detected in Version:**")
            st.code(defect.get('swRegVersion', 'N/A'))
        with col2:
            st.write("**Resolved in Version:**")
            st.code(defect.get('swResolveVersion', 'N/A'))
        with col3:
            st.write("**Test Unit:**")
            st.write(defect.get('testUnit', 'N/A'))


def render_plm_analyze():
    """
    Render PLM defect analysis interface

    Shows problem-solution mapping and detailed analysis
    """
    st.subheader("📊 Defect Analysis")

    # Use selected defect code from Search tab if available
    default_code = st.session_state.get('plm_selected_defect_code', '')
    default_division = st.session_state.get('plm_selected_division')

    if default_code:
        st.info(f"📌 Using Defect Code from Search: **{default_code}**")

    col1, col2 = st.columns(2)

    with col1:
        defect_code = st.text_input(
            "Defect Code (or enter new one)",
            placeholder="P190404-00007",
            key="analyze_code"
        )
        # Use default if no input
        if not defect_code and default_code:
            defect_code = default_code

    with col2:
        division_options = ["Mobile", "Network"]
        default_index = 0
        if default_division == "26":
            default_index = 1

        division = st.selectbox(
            "Division",
            options=division_options,
            index=default_index,
            key="analyze_division"
        )

    division_code = "25" if division == "Mobile" else "26"

    if st.button("📊 Analyze", key="btn_analyze"):
        if not defect_code:
            st.error("Please enter a defect code")
            return

        with st.spinner("Analyzing defect..."):
            try:
                if _is_plm_local_test_mode():
                    defect = next(
                        (
                            item for item in _get_plm_local_test_defects()
                            if item.get("defectCode") == defect_code
                        ),
                        _get_plm_local_test_defects()[0],
                    )
                    result = {
                        "success": True,
                        "message": "",
                        "context": {
                            "defect_code": defect.get("defectCode"),
                            "title": defect.get("plmTitle"),
                            "status": defect.get("plmStatus"),
                            "priority": defect.get("plmPriority"),
                            "problem": defect.get("content"),
                            "root_cause": defect.get("reason"),
                            "solution": defect.get("countermeasure"),
                            "main_owner": defect.get("mainOwnerName"),
                            "created_date": defect.get("createDate"),
                            "updated_date": defect.get("updateDate", ""),
                            "version_detected": defect.get("swRegVersion"),
                            "version_resolved": defect.get("swResolveVersion"),
                        },
                    }
                else:
                    result = plm_analyze_with_optional_backend(
                        st.session_state.plm_integration,
                        division_code,
                        defect_code,
                    )
                context = result.get("context", {}) if result.get("success") else None

                if context:
                    st.success("Analysis Complete")

                    # Save analysis result to session state for comment posting
                    st.session_state.plm_current_analysis_result = context
                    st.session_state.plm_active_defect_code = defect_code
                    st.session_state.plm_active_division = division_code

                    # Key metrics
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Status", context.get('status', 'N/A'))
                    with col2:
                        st.metric("Priority", context.get('priority', 'N/A'))
                    with col3:
                        owner = context.get('main_owner', 'N/A')
                        st.metric("Owner", owner[:20] if owner else 'N/A')

                    # Problem-Solution flow
                    st.subheader("Problem → Solution Flow")

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.info("**Problem**\n" + (context.get('problem', 'N/A')[:200]))
                    with col2:
                        st.warning("**Root Cause**\n" + (context.get('root_cause', 'N/A')[:200]))
                    with col3:
                        st.success("**Solution**\n" + (context.get('solution', 'N/A')[:200]))

                    # Version tracking
                    st.subheader("Version Tracking")
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write("**Detected In:**")
                        st.code(context.get('version_detected', 'N/A'))
                    with col2:
                        st.write("**Resolved In:**")
                        st.code(context.get('version_resolved', 'N/A'))

                    # Timeline
                    st.subheader("Timeline")
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write("**Created:**", context.get('created_date', 'N/A'))
                    with col2:
                        st.write("**Updated:**", context.get('updated_date', 'N/A'))

                    # Button to post analysis result as PLM comment
                    st.divider()
                    st.subheader("📤 Post to PLM")
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.caption("분석 결과를 PLM comment로 등록합니다")
                    with col2:
                        if st.button("📝 Comment 등록", key="btn_post_analysis_comment"):
                            st.session_state.navigate_to_comment_tab = True
                            st.success("💬 댓글 탭으로 이동합니다")
                            st.rerun()

                else:
                    st.error(result.get("message") or "Defect not found")

            except PLMAPIException as e:
                st.error(f"API Error: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                st.error(f"Error: {e}")


def render_plm_register():
    """
    Render PLM defect registration interface

    Allows users to register new defects via dashboard
    """
    st.subheader("➕ Register New Defect")

    with st.form("defect_registration"):
        col1, col2 = st.columns(2)

        with col1:
            division = st.selectbox(
                "Division",
                options=["Mobile", "Network"],
                key="reg_division"
            )
            system_code = st.text_input("System Code", value="AI_ANALYSIS", key="reg_system")

        with col2:
            change_type = st.selectbox("Type", options=["DRAFT", "OPEN"], key="reg_type")
            create_user = st.text_input("Creator Knox ID", key="reg_user")

        # Main details
        title = st.text_input("Title", placeholder="Brief description", key="reg_title")
        content = st.text_area("Problem Description", height=100, key="reg_content")

        col1, col2 = st.columns(2)
        with col1:
            importance = st.selectbox("Priority", options=["A", "B", "C"], key="reg_priority")
            occur_rate = st.selectbox(
                "Occurrence Rate",
                options=["Always", "Sometimes", "Once"],
                key="reg_occur"
            )

        with col2:
            project_name = st.text_input("Project/Model Name", value="Galaxy S24", key="reg_project")
            external_id = st.text_input("External ID", value="", key="reg_ext_id")

        col1, col2 = st.columns(2)
        with col1:
            test_unit = st.text_input("Test Unit", value="S/W Engineering", key="reg_test_unit")
            function_block = st.text_input("Function Block", value="General", key="reg_func")

        with col2:
            test_item = st.text_input("Test Item", value="Functional Test", key="reg_test_item")
            detail_function = st.text_input("Feature", value="General Feature", key="reg_feature")

        # Optional fields
        with st.expander("Advanced Options"):
            reappearance = st.text_area("Steps to Reproduce", height=60, key="reg_reappear")
            forecast = st.text_area("Expected Result", height=60, key="reg_forecast")
            sw_version = st.text_input("S/W Version", key="reg_sw_ver")

        submit = st.form_submit_button("📤 Register Defect")

        if submit:
            if not all([title, content, create_user]):
                st.error("Title, content, and creator are required")
                return

            try:
                division_code = "25" if division == "Mobile" else "26"

                payload = build_defect_payload(
                    division_code=division_code,
                    system_code=system_code,
                    change_type=change_type,
                    project_name=project_name,
                    external_id=external_id,
                    create_user=create_user,
                    title=title,
                    content=content,
                    importance=importance,
                    occur_rate=occur_rate,
                    test_unit=test_unit,
                    test_item=test_item,
                    function_block=function_block,
                    detail_function=detail_function,
                    reappearance=reappearance,
                    forecast=forecast,
                    sw_version=sw_version,
                )

                with st.spinner("Registering defect..."):
                    client = _get_plm_client()
                    response = plm_register_defect_with_optional_backend(client, payload)

                    if response.get("success"):
                        result = response.get("result") or {}
                        defect_code = result.get('defectCode')
                        defect_id = result.get('defectId')
                        st.success(
                            f"✅ Defect registered successfully!\n\n"
                            f"**Code:** {defect_code}\n"
                            f"**ID:** {defect_id}"
                        )
                    else:
                        st.error(f"Registration failed: {response.get('message', 'Unknown error')}")

            except Exception as e:
                logger.error(f"Error: {e}")
                st.error(f"Error: {e}")


def render_plm_files():
    """
    Render PLM file management interface

    Allows listing and downloading files attached to defects
    """
    st.subheader("📁 File Management")

    # Use selected defect code from Search tab if available
    default_code = st.session_state.get('plm_selected_defect_code', '')
    default_division = st.session_state.get('plm_selected_division')

    if default_code:
        st.info(f"📌 Using Defect Code from Search: **{default_code}**")

    col1, col2 = st.columns(2)

    with col1:
        division_options = ["Mobile", "Network"]
        default_index = 0
        if default_division == "26":
            default_index = 1

        division = st.selectbox(
            "Division",
            options=division_options,
            index=default_index,
            format_func=lambda x: f"{x} ({'25' if x == 'Mobile' else '26'})",
            key="file_division"
        )

    with col2:
        defect_code_input = st.text_input(
            "Defect Code",
            placeholder="e.g., P190404-00007",
            key="file_code"
        )
        defect_code = defect_code_input if defect_code_input else default_code

    # Display cached files if available
    if st.session_state.get('plm_files_list'):
        cached_files = st.session_state.plm_files_list
        cached_division = st.session_state.get('plm_files_division')

        with st.container():
            col1, col2 = st.columns([3, 1])
            with col1:
                st.info(f"📌 Cached files: {len(cached_files)} file(s)")
            with col2:
                if st.button("Clear Cache", key="btn_clear_files"):
                    st.session_state.plm_files_list = None
                    st.session_state.plm_files_division = None
                    st.session_state.plm_download_data = {}
                    st.rerun()

            st.divider()

            # Show cached file list
            st.dataframe(
                pd.DataFrame(build_attachment_rows(cached_files, name_column='File', include_id=True)),
                use_container_width=True,
                hide_index=True,
            )

            # Download section
            st.subheader("Download Files")

            for file in cached_files:
                doc_id = file.get('docId')
                file_id = file.get('fileId')
                title = file.get('title', f'file_{file_id}')

                col1, col2 = st.columns([3, 1])
                with col1:
                    st.text(f"📄 {title}")
                with col2:
                    if st.button(
                        "⬇️ Download",
                        key=f"btn_download_{file_id}"
                    ):
                        try:
                            client = _get_plm_client()
                            division_code = cached_division or "25"

                            download_result = plm_download_file_with_optional_backend(
                                client,
                                division_code=division_code,
                                doc_id=doc_id,
                                title=title,
                                file_id=file_id,
                            )

                            if download_result.get('success'):
                                file_content = download_result.get('data')
                                file_size = download_result.get('size', 0)

                                if file_content and file_size > 0:
                                    st.session_state.plm_download_data[file_id] = (file_content, title)
                                    logger.info(f"File downloaded: {title} ({file_size} bytes)")
                                    st.success(f"✅ Downloaded {file_size:,} bytes - Scroll down to save file")
                                else:
                                    st.warning(f"File content not available (size: {file_size} bytes)")
                            else:
                                error_msg = download_result.get('message', 'Unknown error')
                                st.error(f"Download failed: {error_msg}")
                                if "권한" in error_msg or "권" in error_msg:
                                    st.info("💡 권한 문제: 파일에 접근할 권한이 없습니다. 관리자에게 문의하세요.")

                        except Exception as e:
                            st.error(f"Error: {e}")

            # Auto-process downloaded files
            if st.session_state.plm_download_data:
                st.divider()
                st.subheader("💾 Downloaded Files - Auto Processing")
                st.info(
                    f"📥 **{len(st.session_state.plm_download_data)} file(s) ready**\n\n"
                    f"• ZIP 파일 → LOG 파일을 뽑아 분석 대기열에 추가\n"
                    f"• 그 외 파일 → 다운로드 버튼으로 내려받기"
                )

                for file_id, (file_content, file_name) in st.session_state.plm_download_data.items():
                    if file_content:
                        file_size_kb = len(file_content) / 1024
                        is_zip = file_name.lower().endswith('.zip')

                        col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
                        with col1:
                            st.text(f"📄 {file_name} ({file_size_kb:.1f} KB)")

                        with col2:
                            if st.button(
                                "➕ 분석 대기열",
                                key=f"auto_download_{file_id}",
                                help="ZIP 안의 LOG 파일을 분석 대기열에 추가합니다"
                            ):
                                with st.spinner(f"Processing {file_name}..."):
                                    _queue_attachment_logs(file_name, file_content)

                        with col3:
                            st.download_button(
                                "⬇️ 다운로드",
                                data=file_content,
                                file_name=file_name,
                                key=f"save_{file_id}",
                                help="파일을 브라우저로 내려받습니다",
                            )

                        # If ZIP file, add button to open and view contents
                        with col4:
                            if is_zip:
                                if st.button("📂 Open", key=f"open_zip_{file_id}", help="List ZIP contents"):
                                    zip_file_list = list_root_contents(file_content)
                                    if zip_file_list:
                                        st.session_state.plm_zip_file_data = file_content
                                        st.session_state.plm_zip_file_list = zip_file_list
                                        st.success(f"✅ Listed {len(zip_file_list)} file(s)")
                                    else:
                                        st.error("Failed to list ZIP or ZIP is empty")
                    else:
                        st.warning(f"⚠️ {file_name} - Invalid data")

                # Display ZIP contents (metadata only, no extraction)
                if st.session_state.plm_zip_file_list:
                    st.divider()
                    st.subheader("📂 ZIP Contents (미압축 상태)")
                    st.info(
                        f"📋 {len(st.session_state.plm_zip_file_list)} file(s) in archive  \n"
                        f"💾 Files are loaded on-demand when selected (memory efficient)"
                    )

                    # Create table of files
                    st.dataframe(
                        pd.DataFrame(build_archive_rows(st.session_state.plm_zip_file_list)),
                        use_container_width=True,
                        hide_index=True,
                    )

                    # File selection for analysis
                    st.subheader("🔍 Select File for Analysis")
                    selected_file = st.selectbox(
                        "Choose a file to analyze",
                        options=list(st.session_state.plm_zip_file_list.keys()),
                        key="select_zip_file"
                    )

                    if selected_file:
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            file_size_kb = st.session_state.plm_zip_file_list[selected_file] / 1024
                            st.text(f"Selected: **{selected_file}** ({file_size_kb:.1f} KB)")
                        with col2:
                            if st.button("➕ Add to Analysis", key=f"add_to_analysis_{selected_file}"):
                                with st.spinner(f"Extracting {selected_file}..."):
                                    # Extract only the selected file (lazy extraction)
                                    file_content = extract_file(
                                        st.session_state.plm_zip_file_data,
                                        selected_file
                                    )

                                    if file_content:
                                        st.session_state.plm_selected_from_zip = {
                                            'filename': selected_file,
                                            'content': file_content,
                                            'size': len(file_content),
                                            'type': selected_file.split('.')[-1].lower()
                                        }
                                        st.success(f"✅ Extracted and added {selected_file} to analysis pipeline")
                                        st.info(f"🔍 Go to sidebar to start analysis")
                                    else:
                                        st.error(f"Failed to extract {selected_file}")

            st.divider()
            st.markdown("**New Search**")

    if st.button("📂 List Files", key="btn_list_files"):
        if not defect_code:
            st.error("Please enter a defect code")
            return

        with st.spinner("Loading files..."):
            try:
                if _is_plm_local_test_mode():
                    st.session_state.plm_files_list = []
                    st.session_state.plm_files_division = "25" if division == "Mobile" else "26"
                    st.info("No files attached to this defect in local test mode")
                    return

                client = _get_plm_client()
                if not client and not _is_plm_local_test_mode() and not is_backend_api_enabled():
                    st.error("PLM API not configured")
                    return

                division_code = "25" if division == "Mobile" else "26"

                result = plm_list_files_with_optional_backend(
                    client,
                    division_code=division_code,
                    defect_code=defect_code
                )

                if result.get("success"):
                    files = result.get("files", [])

                    if files:
                        # Cache files and division
                        st.session_state.plm_files_list = files
                        st.session_state.plm_files_division = division_code
                        st.success(f"Found {len(files)} file(s)")
                        st.rerun()  # Rerun to display cached files

                    else:
                        st.info("No files attached to this defect")

                else:
                    st.error(f"Failed to list files: {result.get('message', 'Unknown error')}")

            except PLMAPIException as e:
                st.error(f"API Error: {e}")
                with st.expander("📋 Debug Info"):
                    st.code(str(e), language="text")
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                st.error(f"Error: {e}")
                with st.expander("📋 Debug Info"):
                    st.code(str(e), language="text")


def render_plm_comment():
    """
    Render PLM comment management interface

    Allows adding, modifying, and deleting comments on defects
    """
    st.subheader("💬 Add Comment")

    # Check if navigating from analysis tab
    analysis_result = st.session_state.get('plm_current_analysis_result')

    # Use active defect from analysis or search tab
    default_code = st.session_state.get('plm_active_defect_code') or st.session_state.get('plm_selected_defect_code', '')
    default_division = st.session_state.get('plm_active_division') or st.session_state.get('plm_selected_division')

    if default_code:
        st.info(f"📌 현재 결함: **{default_code}**")
        if analysis_result:
            st.success("✅ 분석 결과가 준비되어 있습니다")
        else:
            st.caption("분석 결과 없음 (직접 입력해주세요)")

    # Setup form with pre-filled values before form creation
    col1, col2 = st.columns(2)

    with col1:
        division_options = ["Mobile", "Network"]
        default_index = 0
        if default_division == "26":
            default_index = 1

        division = st.selectbox(
            "Division",
            options=division_options,
            index=default_index,
            key="comment_division"
        )

    with col2:
        defect_code_input = st.text_input(
            "Defect Code",
            placeholder="P190404-00007",
            key="comment_code"
        )
        defect_code = defect_code_input if defect_code_input else default_code

    # Pre-fill comment if analysis result is available (outside form)
    default_comment = ""
    if analysis_result:
        default_comment = format_analysis_as_comment(analysis_result)
        st.info(f"✅ Chat 분석 결과가 로드되었습니다")

    with st.form("add_comment"):
        col1, col2 = st.columns(2)

        with col1:
            system_code = st.text_input("System Code", value="AI_ANALYSIS", key="comment_system")
        with col2:
            create_user = st.text_input("Your Knox ID", key="comment_user")

        comment = st.text_area(
            "Comment",
            value=default_comment,
            height=150,
            placeholder="Add your comment here...",
            key="comment_text"
        )

        col1, col2 = st.columns(2)
        with col1:
            change_type = st.radio("Action", options=["Save", "Modify", "Delete"], horizontal=True, key="comment_action")
        with col2:
            if change_type in ["Modify", "Delete"]:
                comment_id = st.text_input(
                    "Comment ID",
                    placeholder="01YJK98RTtPMWL1000",
                    key="comment_id"
                )
            else:
                comment_id = None

        submit = st.form_submit_button("💬 Submit")

        if submit:
            if not all([defect_code, create_user, comment]):
                st.error("Defect Code, Knox ID, and Comment are required")
                return

            try:
                division_code = "25" if division == "Mobile" else "26"
                change_map = {"Save": "S", "Modify": "M", "Delete": "D"}

                request_payload = build_comment_payload(
                    division_code=division_code,
                    defect_code=defect_code,
                    comment=comment,
                    create_user=create_user,
                    system_code=system_code,
                    change_type=change_map[change_type],
                    comment_id=comment_id if change_type in ["Modify", "Delete"] else "",
                )

                if _is_plm_local_test_mode():
                    st.success("✅ Local test completed. Comment was not submitted to PLM.")
                    with st.expander("Local test payload", expanded=False):
                        st.json(request_payload)
                    return

                with st.spinner("Submitting comment..."):
                    result = plm_submit_comment_with_optional_backend(
                        _get_plm_client(),
                        request_payload,
                    )

                    if result.get("success"):
                        st.success("✅ Comment submitted successfully!")
                        # Clear analysis result after successful submission
                        st.session_state.plm_current_analysis_result = None
                        st.session_state.navigate_to_comment_tab = False
                    else:
                        st.error(f"Failed: {result.get('message', 'Unknown error')}")

            except Exception as e:
                logger.error(f"Error: {e}")
                st.error(f"Error: {e}")




def _research_with_same_conditions():
    """Re-search using the same conditions as the previous search"""
    if _is_plm_local_test_mode():
        _apply_plm_local_test_data(force=True)
        st.success("Local test samples refreshed")
        st.rerun()
        return

    search_label = st.session_state.get('plm_quick_search_label', '')
    status = st.session_state.get('plm_quick_search_status', '')
    division_code = st.session_state.get('plm_quick_search_division', '25')

    search_id = None

    if search_label and "Group" in search_label:
        # Extract from "Group (15 users)" format
        import re
        match = re.search(r'Group \((\d+) users\)', search_label)
        if match:
            # Re-fetch users for the previously selected group
            try:
                config_manager = PLMConfigManager()
                # We need to find which group was selected - for now, get users dynamically
                groups = config_manager.get_groups_by_division("25")
                if groups:
                    # Use the first group as fallback, ideally we'd store the group key
                    first_group_key = list(groups.keys())[0] if groups else None
                    if first_group_key:
                        users = config_manager.get_users_for_search(first_group_key)
                        search_id = ",".join(users)
            except Exception as e:
                logger.error(f"Failed to get users for re-search: {e}")
                st.error("Failed to re-search: Could not fetch group users")
                return
    elif search_label and "User:" in search_label:
        # Extract from "User: bongki.moon" format
        search_id = search_label.replace("User: ", "").strip()

    if not search_id or not status:
        st.error("Cannot re-search: Missing search conditions")
        return

    with st.spinner(f"Searching {status} defects with same conditions..."):
        try:
            client = _get_plm_client()
            if not client and not _is_plm_local_test_mode() and not is_backend_api_enabled():
                st.error("PLM API not configured")
                return

            result = plm_quick_search_with_optional_backend(
                client,
                division_code=division_code,
                main_owner_id=search_id,
                status=status,
                search_type="main",
            )

            if not result.get("success"):
                st.error(f"Search failed: {result.get('message', 'Unknown error')}")
                return

            defects = result.get("defects", [])
            if not defects:
                st.info(f"No {status} defects found")
                return

            if result.get("truncated"):
                st.warning(f"Showing first {len(defects)} out of {result.get('total_codes', len(defects))} defects")

            st.session_state.plm_quick_search_results = defects
            st.session_state.plm_quick_search_selected_index = 0
            st.session_state.navigate_to_chat = False
            st.success(f"Refreshed: {len(defects)} {status} defect(s)")
            st.rerun()

        except PLMAPIException as e:
            st.error(f"API Error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            st.error(f"Error: {e}")


def _show_cached_results_in_fragment():
    """Show cached Quick Search results with row selection."""
    # Safety check
    if not st.session_state.get('plm_quick_search_results'):
        st.info("No cached results")
        return

    st.subheader("Quick Search Results")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        search_label = st.session_state.get('plm_quick_search_label', 'Unknown')
        status_cached = st.session_state.get('plm_quick_search_status', 'Unknown')
        results_count = len(st.session_state.plm_quick_search_results) if st.session_state.plm_quick_search_results else 0
        st.caption(f"Cached results · {status_cached} · {search_label} · {results_count} defect(s)")
    with col2:
        if st.button("Search Again", key="btn_search_again"):
            _research_with_same_conditions()
    with col3:
        if st.button("Clear Results", key="btn_clear_quick_search"):
            st.session_state.plm_quick_search_results = None
            st.session_state.plm_quick_search_division = None
            st.session_state.plm_quick_search_label = None
            st.session_state.plm_quick_search_status = None
            st.session_state.plm_quick_search_downloads = {}
            st.rerun()
            return

    st.divider()

    results = st.session_state.plm_quick_search_results
    division_code = st.session_state.plm_quick_search_division

    # Check if this is the first render (no selection made yet)
    is_first_render = (
        not st.session_state.get('plm_quick_search_prev_selected_rows') and
        st.session_state.get('plm_quick_search_selected_index', 0) == 0
    )

    if is_first_render:
        st.info("💡 첫 번째 결함이 자동 선택되었습니다. 다른 결함을 선택하려면 테이블에서 행을 클릭하세요.")

    st.caption("Select a row to view details. Click the defect code to open PLM.")
    selected_index = _render_selectable_defects_table(results)
    selected_defect = results[selected_index]
    defect_code = selected_defect.get('defectCode')

    st.session_state.plm_active_defect_code = defect_code
    st.session_state.plm_active_division = division_code

    # Clear downloads from previous defect when selecting a new one
    current_active = st.session_state.get('plm_quick_search_current_defect_code')
    if current_active and current_active != defect_code:
        st.session_state.plm_quick_search_downloads = {}
        st.session_state.plm_quick_search_files = {}
    st.session_state.plm_quick_search_current_defect_code = defect_code

    # Note: Auto-loading disabled for performance. Files are loaded on-demand when user clicks "Load Attached Files"
    # This prevents 30+ second delays when selecting defects

    st.divider()
    st.subheader("Defect Details")
    st.caption(defect_code)
    _render_defect_details(selected_defect, division_code)

    # Show files section
    st.divider()
    st.subheader("Attached Files")

    defect_code = selected_defect.get('defectCode')

    # Initialize file storage in session state
    if 'plm_quick_search_files' not in st.session_state:
        st.session_state.plm_quick_search_files = {}
    if 'plm_quick_search_downloads' not in st.session_state:
        st.session_state.plm_quick_search_downloads = {}

    if defect_code:
        # Check if we need to load files for this defect
        should_load = defect_code not in st.session_state.plm_quick_search_files

        if should_load:
            st.caption("Load the current defect's attachments from PLM.")
            if st.button("Load Attached Files", key=f"load_files_{defect_code}"):
                if _is_plm_local_test_mode():
                    st.session_state.plm_quick_search_files[defect_code] = {
                        'files': [],
                        'division_code': division_code,
                        'defect_code': defect_code,
                    }
                    st.rerun()
                    return

                try:
                    client = _get_plm_client()
                    if not client and not _is_plm_local_test_mode() and not is_backend_api_enabled():
                        st.error("PLM API not configured")
                    else:
                        with st.spinner(f"Loading attached files for {defect_code}..."):
                            result = plm_list_files_with_optional_backend(
                                client,
                                division_code=division_code,
                                defect_code=defect_code
                            )

                            if result.get("success"):
                                files = result.get("files", [])
                                # Store files in session state
                                st.session_state.plm_quick_search_files[defect_code] = {
                                    'files': files,
                                    'division_code': division_code,
                                    'defect_code': defect_code
                                }
                                st.rerun()
                            else:
                                st.error(f"Failed to list files: {result.get('message', 'Unknown error')}")

                except Exception as e:
                    logger.error(f"Error loading files: {e}", exc_info=True)
                    st.error(f"Error: {e}")

        # Display files if loaded
        if defect_code in st.session_state.plm_quick_search_files:
            file_data = st.session_state.plm_quick_search_files[defect_code]
            files = file_data.get('files', [])

            if files:
                st.caption(f"{len(files)} attached file(s)")

                st.dataframe(
                    pd.DataFrame(build_attachment_rows(files)),
                    use_container_width=True,
                    hide_index=True,
                )

                st.markdown("**Download Files**")
                for file in files:
                    doc_id = file.get('docId')
                    file_id = file.get('fileId')
                    title = file.get('title', f'file_{file_id}')
                    file_size = file.get('fileSize', 0)
                    created = file.get('createDate', '')[:10] if file.get('createDate') else ''

                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.write(title)
                        details = []
                        if file_size:
                            details.append(f"{file_size / 1024:.1f} KB")
                        if created:
                            details.append(created)
                        if details:
                            st.caption(" · ".join(details))
                    with col2:
                        # Check if already downloaded
                        is_downloaded = file_id in st.session_state.plm_quick_search_downloads

                        if st.button("Download", key=f"download_{file_id}", disabled=is_downloaded):
                            # Download and store in session state
                            client = _get_plm_client()
                            download_result = plm_download_file_with_optional_backend(
                                client,
                                division_code=division_code,
                                doc_id=doc_id,
                                title=title,
                                file_id=file_id,
                            )

                            if download_result.get('success'):
                                file_content = download_result.get('data')
                                file_size = download_result.get('size', 0)

                                if file_content and file_size > 0:
                                    # Store in session state for display
                                    st.session_state.plm_quick_search_downloads[file_id] = {
                                        'content': file_content,
                                        'filename': title,
                                        'size': file_size
                                    }
                                    st.rerun()
                                else:
                                    st.warning(f"File content not available")
                            else:
                                error_msg = download_result.get('message', 'Unknown error')
                                st.error(f"Download failed: {error_msg}")

                        # Show download status
                        if is_downloaded:
                            st.caption("Downloaded")

                # Auto-process downloaded files
                if st.session_state.plm_quick_search_downloads:
                    st.divider()
                    st.subheader("Downloaded Files")

                    # Show auto-save status and analysis queue info
                    st.caption(
                        "'분석 대기열'은 ZIP 안의 LOG 파일을 뽑아 분석 파이프라인으로 보냅니다. "
                        "원본 파일이 필요하면 '다운로드'로 내려받으세요."
                    )

                    for file_id, file_info in st.session_state.plm_quick_search_downloads.items():
                        filename = file_info['filename']
                        content = file_info['content']
                        file_size_kb = len(content) / 1024

                        col1, col2, col3 = st.columns([2, 1, 1])
                        with col1:
                            st.write(filename)
                            st.caption(f"{file_size_kb:.1f} KB")

                        with col2:
                            if st.button(
                                "분석 대기열",
                                key=f"auto_download_{file_id}",
                                help="ZIP 안의 LOG 파일을 분석 대기열에 추가합니다"
                            ):
                                with st.spinner(f"Processing {filename}..."):
                                    _queue_attachment_logs(filename, content)

                        with col3:
                            st.download_button(
                                "다운로드",
                                data=content,
                                file_name=filename,
                                key=f"manual_save_{file_id}",
                                help="파일을 브라우저로 내려받습니다",
                            )

                # Clear button
                if st.button("Refresh File List", key=f"reload_files_{defect_code}"):
                    st.session_state.plm_quick_search_files.pop(defect_code, None)
                    st.rerun()
            else:
                st.info("No attached files for this defect.")

                if st.button("Refresh File List", key=f"reload_files_{defect_code}"):
                    st.session_state.plm_quick_search_files.pop(defect_code, None)
                    st.rerun()
    else:
        st.info("Select a defect to view files")

    # Process pending auto-load AFTER defect details are rendered
    if st.session_state.get('plm_pending_auto_load'):
        pending = st.session_state.pop('plm_pending_auto_load')
        _auto_load_and_process_defect_files(
            pending['defect'],
            pending['division_code'],
            selected_index=pending['selected_index']
        )
        # The attachment list above and the sidebar's pending-log count were both
        # drawn before this download finished, so they still show the pre-download
        # state. Refresh once. On the next run the selection is unchanged, so no
        # new auto-load is queued and this cannot loop.
        st.rerun()

    st.divider()
    st.subheader("New Search")

    if st.button("Start New Search", key="btn_new_search"):
        st.session_state.plm_quick_search_results = None
        st.session_state.plm_quick_search_division = None
        st.session_state.plm_quick_search_label = None
        st.session_state.plm_quick_search_status = None
        st.session_state.plm_quick_search_downloads = {}
        st.session_state.plm_quick_search_selected_index = 0
        st.session_state.plm_quick_search_files = {}
        st.session_state.show_new_search_form = True

    if st.session_state.get('show_new_search_form', False):
        st.session_state.show_new_search_form = False
        st.success("Search cleared. Start a new search below.")
        st.divider()
        _show_search_input_form_fragment()


def _show_search_input_form_fragment():
    """Display search input form using radio buttons"""
    st.subheader("Quick Search")

    # Division fixed to Mobile
    division = "Mobile"
    division_code = "25"

    # Status first (no dependencies, faster)
    col1, col2 = st.columns(2)
    with col1:
        status = st.radio(
            "Status",
            options=["Open", "Resolve", "Close"],
            horizontal=True,
            key="quick_search_status_radio"
        )

    # Search method (with dependency: Group needs API cache)
    # Initialize session state if not present
    if 'quick_search_method' not in st.session_state:
        st.session_state.quick_search_method = "Group"

    search_method = st.radio(
        "Search By",
        options=["Group", "User ID"],
        horizontal=True,
        key="quick_search_method_select",
        index=0 if st.session_state.quick_search_method == "Group" else 1,
        on_change=lambda: st.session_state.update({'quick_search_method': st.session_state.quick_search_method_select})
    )

    # Update session state with current selection
    st.session_state.quick_search_method = search_method

    with st.container():
        if search_method == "Group":
            # Lazy-load groups if not already loaded
            groups = st.session_state.get('plm_groups_cache', {})

            if not groups and not st.session_state.get('plm_groups_loading', False):
                with st.spinner("Loading groups..."):
                    _lazy_load_groups()
                groups = st.session_state.get('plm_groups_cache', {})

            if not groups:
                if st.session_state.get('plm_groups_loading', False):
                    st.info("Loading groups from PLM...")
                else:
                    st.warning(f"No groups are defined for {division}")
                return

            selected_group_key = st.radio(
                "Select Group",
                options=list(groups.keys()),
                format_func=lambda k: groups[k],
                key="quick_search_group_radio"
            )
            owner_id = None
            group_key = selected_group_key
        else:
            owner_id = st.text_input(
                "User ID (Knox ID)",
                placeholder="e.g., bongki.moon",
                help="Enter your Knox ID to search your defects",
                key="quick_search_user_id"
            )
            group_key = None

    if st.button("Search", key="btn_quick_search"):
        if _is_plm_local_test_mode():
            _apply_plm_local_test_data(force=True)
            st.success("Local test samples loaded")
            _show_cached_results_in_fragment()
            return

        if search_method == "Group":
            if not group_key:
                st.error("Please select a group")
                return
            config_manager = PLMConfigManager()
            users = config_manager.get_users_for_search(group_key)
            if not users:
                st.error(f"No users found in selected group")
                return
            logger.info(f"Group search - group_key: {group_key}, users: {users}")
            search_id = ",".join(users)
            search_label = f"Group ({len(users)} users)"
            logger.info(f"search_id: {search_id}, search_label: {search_label}")
        else:
            if not owner_id or not owner_id.strip():
                st.error("Please enter a user ID")
                return
            search_id = owner_id.strip()
            search_label = f"User: {owner_id.strip()}"

        with st.spinner(f"Searching {status} defects for {search_label}..."):
            try:
                client = _get_plm_client()
                if not client and not _is_plm_local_test_mode() and not is_backend_api_enabled():
                    st.error("PLM API not configured")
                    return

                result = plm_quick_search_with_optional_backend(
                    client,
                    division_code=division_code,
                    main_owner_id=search_id,
                    status=status,
                    search_type="main",
                )

                if not result.get("success"):
                    st.error(f"Search failed: {result.get('message', 'Unknown error')}")
                    return

                defects = result.get("defects", [])
                if not defects:
                    st.info(f"No {status} defects found")
                    return

                logger.info(
                    "Quick search loaded %s defect detail rows from %s code(s)",
                    len(defects),
                    result.get("total_codes", len(defects)),
                )
                if result.get("truncated"):
                    st.warning(f"Showing first {len(defects)} out of {result.get('total_codes', len(defects))} defects")

                st.session_state.plm_quick_search_results = defects
                st.session_state.plm_quick_search_division = division_code
                st.session_state.plm_quick_search_label = search_label
                st.session_state.plm_quick_search_status = status
                st.session_state.plm_quick_search_selected_index = 0
                st.session_state.plm_quick_search_prev_selected_rows = []
                st.success(f"Loaded {len(defects)} {status} defect(s)")
                # Show results immediately; the sidebar's active-defect slot is
                # refreshed at the end of the script run (see web_app.py).
                _show_cached_results_in_fragment()
                return

            except PLMAPIException as e:
                st.error(f"API Error: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                st.error(f"Error: {e}")


def render_plm_section():
    """
    Main PLM section renderer

    Renders tabs for different PLM operations
    """
    import time
    section_start = time.time()
    st.header("📋 PLM Defect Management")

    _initialize_plm_session()
    logger.info(f"After initialize_plm_session: {time.time() - section_start:.2f}s")

    _render_plm_local_test_controls()
    logger.info(f"After render_local_test_controls: {time.time() - section_start:.2f}s")

    if (
        not st.session_state.get('plm_available', False)
        and not _is_plm_local_test_mode()
        and not is_backend_api_enabled()
    ):
        st.warning("⚠️ PLM API is not configured. Check credentials and network.")
        return

    if _is_plm_local_test_mode():
        st.info("PLM 로컬 테스트 모드가 활성화되어 있습니다. 샘플 defect로 UI를 검증합니다.")

    # 추출된 로그가 대기 중이면 알려주되, 시작은 사용자가 sidebar 버튼으로 한다.
    queued_logs = pending_logs()
    if queued_logs:
        st.info(
            f"📥 LOG 파일 {len(queued_logs)}개가 분석 대기 중입니다. "
            "Sidebar의 **분석 및 DB 적재 시작** 을 누르면 시작합니다."
        )

    # Create tabs
    tab0, tab1, tab2, tab3 = st.tabs([
        "🔍 Quick Search",
        "🔍 검색 및 파일",
        "📊 분석",
        "💬 댓글"
    ])

    with tab0:
        try:
            tab0_start = time.time()
            # Check for cached results directly
            if st.session_state.get('plm_quick_search_results'):
                _show_cached_results_in_fragment()
            else:
                _show_search_input_form_fragment()
            logger.info(f"Tab 0 (Quick Search) rendered in {time.time() - tab0_start:.2f}s")
        except Exception as e:
            logger.error(f"Error in Quick Search: {e}", exc_info=True)
            st.error(f"Error: {e}")

    with tab1:
        try:
            tab1_start = time.time()
            # Lazy-load groups only when tab1 is actually viewed
            if (not st.session_state.get('plm_groups_cache') and
                not st.session_state.get('plm_groups_loading') and
                not _is_plm_local_test_mode() and
                not is_backend_api_enabled()):
                _lazy_load_groups()
            logger.info(f"After lazy_load_groups in Tab1: {time.time() - tab1_start:.2f}s")

            col1, col2 = st.columns([1, 1])
            with col1:
                st.subheader("🔍 결함 검색")
                render_plm_search()
            with col2:
                st.subheader("📁 파일 관리")
                render_plm_files()
            logger.info(f"Tab 1 (Search & Files) rendered in {time.time() - tab1_start:.2f}s")
        except Exception as e:
            logger.error(f"Error in Search & Files: {e}", exc_info=True)
            st.error(f"Error: {e}")

    with tab2:
        try:
            tab2_start = time.time()
            render_plm_analyze()
            logger.info(f"Tab 2 (Analysis) rendered in {time.time() - tab2_start:.2f}s")
        except Exception as e:
            logger.error(f"Error in Analysis: {e}", exc_info=True)
            st.error(f"Error: {e}")

    with tab3:
        try:
            tab3_start = time.time()
            render_plm_comment()
            logger.info(f"Tab 3 (Comment) rendered in {time.time() - tab3_start:.2f}s")
        except Exception as e:
            logger.error(f"Error in Comments: {e}", exc_info=True)
            st.error(f"Error: {e}")


def render_plm_sidebar_stats():
    """
    Render PLM status in sidebar

    Shows connection status, active defect, and quick actions
    """
    # Only initialize if not already done
    if 'plm_integration' not in st.session_state:
        _initialize_plm_session()

    if (
        not st.session_state.get('plm_available', False)
        and not _is_plm_local_test_mode()
        and not is_backend_api_enabled()
    ):
        return

    with st.sidebar:
        st.subheader("PLM 상태")

        try:
            if _is_plm_local_test_mode():
                st.caption("로컬 테스트 모드")

            # The sidebar renders before the PLM tab (see web_app.py), so the
            # selection the user just made is not known yet. Reserve a slot here
            # and let refresh_plm_sidebar_active_defect() fill it again once the
            # tab has resolved the selection — no extra rerun needed.
            st.session_state['_plm_active_defect_slot'] = st.empty()
            _write_plm_active_defect_slot()

        except Exception as e:
            st.caption(str(e)[:30])


def _write_plm_active_defect_slot():
    """Write the current active defect into the reserved sidebar slot."""
    slot = st.session_state.get('_plm_active_defect_slot')
    if slot is None:
        return

    active_defect = st.session_state.get('plm_active_defect_code')
    if active_defect:
        slot.info(f"**활성 결함:**\n`{active_defect}`")
    else:
        slot.caption("활성 결함: 없음")


def refresh_plm_sidebar_active_defect():
    """
    Re-render the sidebar's active defect after the tabs have rendered.

    Streamlit runs the script top to bottom and the sidebar is drawn before the
    PLM tab, so a selection made in the tab would otherwise only show up on the
    *next* rerun (one frame behind). Calling this at the end of the script run
    updates the reserved slot in place.
    """
    try:
        _write_plm_active_defect_slot()
    except Exception as e:  # never break the app over a sidebar refresh
        logger.warning(f"Failed to refresh PLM sidebar active defect: {e}")


# Export functions for use in other modules
__all__ = [
    'render_plm_section',
    'render_plm_search',
    'render_plm_analyze',
    'render_plm_register',
    'render_plm_comment',
    'render_plm_files',
    'render_plm_sidebar_stats',
    'refresh_plm_sidebar_active_defect',
    '_initialize_plm_session',
    '_get_plm_client',
]
