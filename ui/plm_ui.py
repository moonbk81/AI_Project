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
import zipfile
import io

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from plm.plm_rag_integration import (
    create_plm_integration,
    PLMDefectContextBuilder,
    PLMConfigManager
)
from plm.plm_api_client import DivisionCode, PLMAPIException
from ui.plm_auto_download import (
    LogFileExtractor,
    AutoDownloadManager,
    PLMAutoDownloadFlow
)

logger = logging.getLogger(__name__)


def _is_plm_local_test_mode() -> bool:
    return bool(st.session_state.get('plm_local_test_mode', False))


def _get_plm_local_test_defects() -> List[Dict[str, Any]]:
    """Return deterministic sample defects for offline PLM UI testing."""
    return [
        {
            'defectCode': 'P260711-LOCAL01',
            'defectId': 'LOCAL_DEFECT_001',
            'plmTitle': 'IMS registration retry failure after network handover',
            'plmStatus': 'Open',
            'plmPriority': 'A',
            'mainOwnerName': 'local.tester',
            'createDate': '2026-07-11T09:15:00',
            'content': 'After LTE to NR handover, IMS registration retries repeatedly and voice service is delayed.',
            'reason': 'Local test root cause: retry timer and registration state are not synchronized after handover.',
            'countermeasure': 'Local test solution: reset IMS registration state when handover completion is received.',
        },
        {
            'defectCode': 'P260711-LOCAL02',
            'defectId': 'LOCAL_DEFECT_002',
            'plmTitle': 'Data stall observed after airplane mode toggle',
            'plmStatus': 'Resolve',
            'plmPriority': 'B',
            'mainOwnerName': 'local.owner',
            'createDate': '2026-07-10T16:42:00',
            'content': 'Packet data appears connected, but DNS and TCP connection attempts time out after airplane mode toggle.',
            'reason': 'Local test root cause: stale network capabilities remain cached after radio reset.',
            'countermeasure': 'Local test solution: invalidate network capabilities and trigger reconnect.',
        },
        {
            'defectCode': 'P260711-LOCAL03',
            'defectId': 'LOCAL_DEFECT_003',
            'plmTitle': 'Battery drain during repeated modem recovery',
            'plmStatus': 'Close',
            'plmPriority': 'C',
            'mainOwnerName': 'local.review',
            'createDate': '2026-07-09T11:05:00',
            'content': 'Repeated modem recovery events keep radio components active and increase standby battery drain.',
            'reason': 'Local test root cause: recovery retry interval is too short under persistent radio errors.',
            'countermeasure': 'Local test solution: apply exponential backoff and stop retry after threshold.',
        },
    ]


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
    local_test = st.checkbox(
        "PLM 로컬 테스트 모드",
        key="plm_local_test_mode",
        help="사내 PLM 연결 없이 샘플 defect와 comment 등록 UI를 테스트합니다.",
    )

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


def _refine_problem_description(problem_content: str, use_llm: bool = True) -> str:
    """
    Refine problem description by extracting key points

    Args:
        problem_content: Original problem description
        use_llm: Whether to use LLM for refinement (True) or simple extraction (False)

    Returns:
        Refined, concise problem description
    """
    if not problem_content or len(problem_content.strip()) == 0:
        return problem_content

    # If content is already short, return as is
    if len(problem_content) < 200:
        return problem_content

    if use_llm:
        try:
            import ollama

            # Get the active model from session state
            model_name = st.session_state.get('active_model', 'gemma4:12b')

            system_prompt = """You are an expert at refining technical problem descriptions for intent recognition.
Your task is to extract and refine the essential information while preserving critical intent signals.

Rules:
1. Preserve the specific symptom/behavior (e.g., "intermittent data drops", "call fails", "battery drain")
2. Preserve affected component/app/feature names (these are intent signals)
3. Preserve specific conditions when they occur (e.g., "during handover", "when using app X")
4. Remove redundant details and unnecessary explanations
5. Extract and include key technical details (error codes, version info, network info if present)
6. Make it concise but complete (aim for 2-3 sentences max)
7. Use bullet points only for multiple distinct issues
8. Return ONLY the refined description, no additional text or explanation"""

            response = ollama.chat(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Please refine this problem description:\n\n{problem_content}"}
                ],
                stream=False
            )

            refined = response['message']['content'].strip()
            return refined if refined else problem_content

        except Exception as e:
            logger.warning(f"Failed to refine with LLM: {e}. Using fallback method.")
            return _refine_problem_description(problem_content, use_llm=False)
    else:
        # Fallback: simple extraction method
        lines = problem_content.split('\n')
        # Filter empty lines and very short lines
        meaningful_lines = [line.strip() for line in lines if len(line.strip()) > 10]
        # Take first 3 meaningful lines
        return '\n'.join(meaningful_lines[:3]) if meaningful_lines else problem_content


def _initialize_plm_session():
    """Initialize Streamlit session state for PLM"""
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
        'plm_pending_logs': [],
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
            st.session_state.plm_integration = create_plm_integration()
            st.session_state.plm_available = True

            # Lazy-load groups for Quick Search only when needed (not on init)
            if 'plm_groups_cache' not in st.session_state or not st.session_state.plm_groups_cache:
                _lazy_load_groups()
        except Exception as e:
            logger.error(f"Failed to initialize PLM: {e}")
            st.session_state.plm_available = False
            st.session_state.plm_integration = None


def _lazy_load_groups():
    """Lazy-load groups in background when needed"""
    if st.session_state.get('plm_groups_loading', False) or st.session_state.get('plm_groups_cache'):
        return  # Already loading or loaded

    st.session_state.plm_groups_loading = True
    try:
        config_manager = PLMConfigManager()
        st.session_state.plm_groups_cache = config_manager.get_groups_by_division("25")
        logger.info(f"Groups pre-loaded: {len(st.session_state.plm_groups_cache)} groups")
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


def _list_zip_contents(file_data: bytes) -> Dict[str, int]:
    """
    List ZIP file contents without extracting (memory efficient)
    Only includes files in root directory (ignores subdirectories)

    Args:
        file_data: Binary data of ZIP file

    Returns:
        Dictionary with {filename: file_size_in_bytes}
    """
    try:
        files_dict = {}
        zip_buffer = io.BytesIO(file_data)

        with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                # Skip directories and files in subdirectories
                if file_info.is_dir():
                    continue

                filename = file_info.filename
                # Only include root-level files (no "/" in name)
                if '/' not in filename:
                    files_dict[filename] = file_info.file_size

        return files_dict

    except zipfile.BadZipFile:
        return {}
    except Exception as e:
        logger.error(f"Error listing ZIP: {e}")
        return {}


def _extract_file_from_zip(file_data: bytes, target_filename: str) -> Optional[bytes]:
    """
    Extract a single file from ZIP (called only when user selects a file)

    Args:
        file_data: Binary data of ZIP file
        target_filename: Name of file to extract

    Returns:
        File content as bytes, or None if failed
    """
    try:
        zip_buffer = io.BytesIO(file_data)

        with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
            if target_filename in zip_ref.namelist():
                return zip_ref.read(target_filename)
        return None

    except Exception as e:
        logger.error(f"Error extracting file from ZIP: {e}")
        return None


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
                    st.rerun()

            st.divider()
            _render_defects_table(st.session_state.plm_search_results, st.session_state.plm_search_division)

            # Show details for cached results
            for i, defect in enumerate(st.session_state.plm_search_results):
                try:
                    defect_code = defect.get('defectCode') if isinstance(defect, dict) else 'Unknown'
                    with st.expander(f"📋 Details: {defect_code}"):
                        _render_defect_details(defect, st.session_state.plm_search_division)
                except Exception as e:
                    logger.error(f"Error rendering cached defect details: {e}", exc_info=True)
                    st.error(f"Error displaying defect details: {str(e)}")

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
                if not client:
                    st.error("PLM API not configured")
                    return

                response = client.get_defect_info(
                    division_code=division_code,
                    defect_codes=search_values if is_code_search else None,
                    defect_ids=search_values if not is_code_search else None
                )

                if response.is_success():
                    defects = response.result.get('defectList', [])

                    if defects:
                        st.session_state.plm_search_results = defects
                        st.session_state.plm_search_division = division_code
                        # Store first defect code for use in other tabs
                        if len(search_values) == 1:
                            st.session_state.plm_selected_defect_code = search_values[0]
                            st.session_state.plm_selected_division = division_code
                        st.success(f"Found {len(defects)} defect(s)")
                        _render_defects_table(defects, division_code)

                        # Show details for each defect
                        for i, defect in enumerate(defects):
                            try:
                                defect_code = defect.get('defectCode') if isinstance(defect, dict) else 'Unknown'
                                with st.expander(f"📋 Details: {defect_code}"):
                                    _render_defect_details(defect, division_code)
                            except Exception as e:
                                logger.error(f"Error rendering defect details: {e}", exc_info=True)
                                st.error(f"Error displaying defect details: {str(e)}")
                    else:
                        st.info("No defects found")

                else:
                    st.error(f"Search failed: {response.get_error_message()}")

            except PLMAPIException as e:
                st.error(f"API Error: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                st.error(f"Error: {e}")


def _get_plm_site_url(defect_id: str) -> str:
    """
    Generate PLM site URL for a defect

    Args:
        defect_id: Defect ID (e.g., 02FBN2PGBtPMWL1000)

    Returns:
        PLM site URL
    """
    # PLM site URL pattern
    base_url = "http://splm.sec.samsung.net/wl/tqm/defect/defectreg/goDefectDetail.do"
    # Construct URL with defectId
    return f"{base_url}?isPopUp=Y&menuGubun=&defectId={defect_id}"


def _render_defects_table(defects: List[Dict[str, Any]], division_code: str = "25"):
    """Render defects in a table with clickable code links"""
    cell_style = "border:1px solid var(--app-border); padding:8px; text-align:left;"
    html = (
        '<table style="width:100%; border-collapse:collapse;">'
        '<thead><tr style="background-color:var(--app-soft-bg);">'
        f'<th style="{cell_style}">Code</th>'
        f'<th style="{cell_style}">Title</th>'
        f'<th style="{cell_style}">Status</th>'
        f'<th style="{cell_style}">Priority</th>'
        f'<th style="{cell_style}">Owner</th>'
        f'<th style="{cell_style}">Created</th>'
        '</tr></thead><tbody>'
    )

    for defect in defects:
        defect_code = defect.get('defectCode', '')
        defect_id = defect.get('defectId', '')
        title = defect.get('plmTitle', '')
        if isinstance(title, str) and len(title) > 50:
            title = title[:50] + "..."

        created = defect.get('createDate', '')
        if isinstance(created, str) and created:
            created = created[:10]

        plm_url = _get_plm_site_url(defect_id) if defect_id else "#"

        html += (
            "<tr>"
            f'<td style="{cell_style}"><a href="{plm_url}" target="_blank" style="color:var(--app-primary); font-weight:700; text-decoration:none;">{defect_code}</a></td>'
            f'<td style="{cell_style}">{title}</td>'
            f'<td style="{cell_style}">{defect.get("plmStatus", "N/A")}</td>'
            f'<td style="{cell_style}">{defect.get("plmPriority", "N/A")}</td>'
            f'<td style="{cell_style}">{defect.get("mainOwnerName", "N/A")}</td>'
            f'<td style="{cell_style}">{created}</td>'
            "</tr>"
        )

    html += '</tbody></table>'
    st.markdown(html, unsafe_allow_html=True)


def _render_selectable_defects_table(defects: List[Dict[str, Any]]) -> int:
    """Render Quick Search results as a selectable table and return selected row index."""
    table_data = []

    for defect in defects:
        defect_code = defect.get('defectCode', '')
        defect_id = defect.get('defectId', '')
        title = defect.get('plmTitle', '')
        created = defect.get('createDate', '')

        if isinstance(title, str) and len(title) > 80:
            title = title[:80] + "..."
        if isinstance(created, str) and created:
            created = created[:10]

        plm_url = _get_plm_site_url(defect_id) if defect_id else ""
        if plm_url and defect_code:
            plm_url = f"{plm_url}#{defect_code}"

        table_data.append({
            "Code": plm_url,
            "Title": title,
            "Status": defect.get("plmStatus", "N/A"),
            "Priority": defect.get("plmPriority", "N/A"),
            "Owner": defect.get("mainOwnerName", "N/A"),
            "Created": created,
        })

    table_state = st.dataframe(
        pd.DataFrame(table_data),
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="quick_search_results_table",
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

    selected_rows = table_state.selection.rows if table_state and table_state.selection else []
    if selected_rows:
        selected_index = selected_rows[0]
        if selected_index >= len(defects):
            selected_index = 0
        st.session_state.plm_quick_search_selected_index = selected_index
        return selected_index

    selected_index = st.session_state.get('plm_quick_search_selected_index', 0)
    if selected_index >= len(defects):
        selected_index = 0
        st.session_state.plm_quick_search_selected_index = selected_index
    return selected_index


# Prefixes of comments auto-registered by this tool (excluded from human comments).
# Kept in sync with _format_analysis_as_comment().
_AI_COMMENT_SIGNATURES = ("💬 **AI Chat 분석 결과", "🤖 AI 분석 결과")

# System/automated registrants whose comments are not developer input (excluded).
_EXCLUDED_COMMENT_USERS = ("utopia", "mx ax development")


def _is_ai_generated_comment(text: str) -> bool:
    """True if the comment was auto-registered by this tool (AI analysis)."""
    stripped = (text or "").lstrip()
    return any(stripped.startswith(sig) for sig in _AI_COMMENT_SIGNATURES)


def _is_excluded_comment_user(history_user: str) -> bool:
    """True for system/automated registrants that should not surface as comments."""
    name = (history_user or "").lower()
    return any(excluded in name for excluded in _EXCLUDED_COMMENT_USERS)


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

    comments = []
    try:
        client = _get_plm_client()
        if not client:
            return []

        response = client.get_defect_history(
            division_code=division_code,
            defect_codes=[defect_code],
        )
        if not response.is_success():
            logger.warning(f"get_defect_history failed: {response.get_error_message()}")
            return []

        result = response.result or {}
        for arr in result.get('defectHistoryListArr', []) or []:
            for entry in arr.get('defectHistoryList', []) or []:
                if entry.get('historyType') != 'C':
                    continue
                if _is_excluded_comment_user(entry.get('historyUser', '')):
                    continue
                text = (entry.get('comment') or '').strip()
                if not text or _is_ai_generated_comment(text):
                    continue
                comments.append({
                    'comment': text,
                    'historyDate': entry.get('historyDate', ''),
                    'historyUser': entry.get('historyUser', ''),
                    'commentId': entry.get('commentId', ''),
                })
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
                integration = st.session_state.plm_integration
                builder = PLMDefectContextBuilder(integration)

                context = builder.build_defect_context(defect_code, division_code)

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
                    st.error("Defect not found")

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
                from plm.plm_api_client import DefectRegistrationRequest

                division_code = "25" if division == "Mobile" else "26"

                request = DefectRegistrationRequest(
                    divisionCode=division_code,
                    systemCode=system_code,
                    changeType=change_type,
                    refObjectName=project_name,
                    refObjectType="MFG",
                    externalDefectId=external_id or f"AI_{datetime.now().timestamp()}",
                    defectCategory="SW",
                    createUser=create_user,
                    title=title,
                    inChargeUser=create_user,
                    Content=content,
                    importance=importance,
                    occurRateType=occur_rate,
                    occurPhase="DV",
                    testUnit=test_unit,
                    testItem=test_item,
                    functionBlock=function_block,
                    detailFunctionclass=detail_function,
                    reappearancePath=reappearance if reappearance else None,
                    forecastResult=forecast if forecast else None,
                    swVersion=sw_version if sw_version else None
                )

                with st.spinner("Registering defect..."):
                    response = st.session_state.plm_integration.client.register_defect(request)

                    if response.is_success():
                        defect_code = response.result.get('defectCode')
                        defect_id = response.result.get('defectId')
                        st.success(
                            f"✅ Defect registered successfully!\n\n"
                            f"**Code:** {defect_code}\n"
                            f"**ID:** {defect_id}"
                        )
                    else:
                        st.error(f"Registration failed: {response.get_error_message()}")

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
            table_data = []
            for file in cached_files:
                table_data.append({
                    'File': file.get('title', 'N/A'),
                    'Size': f"{file.get('fileSize', 0) / 1024:.2f} KB" if file.get('fileSize') else 'N/A',
                    'Created': file.get('createDate', '')[:10] if file.get('createDate') else '',
                    'ID': file.get('fileId')
                })

            df = pd.DataFrame(table_data)
            st.dataframe(df, use_container_width=True, hide_index=True)

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
                        with st.spinner(f"Downloading {title}..."):
                            try:
                                client = _get_plm_client()
                                division_code = cached_division or "25"

                                download_result = client.download_file(
                                    division_code=division_code,
                                    doc_id=doc_id,
                                    title=title,
                                    file_id=file_id
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
                    f"**Auto-processing enabled:**\n"
                    f"• Non-ZIP files → Auto-saved to Downloads folder\n"
                    f"• ZIP files → Auto-extract log files\n"
                    f"• Log files → Auto-added to analysis pipeline"
                )

                for file_id, (file_content, file_name) in st.session_state.plm_download_data.items():
                    if file_content:
                        file_size_kb = len(file_content) / 1024
                        is_zip = file_name.lower().endswith('.zip')

                        col1, col2, col3 = st.columns([2, 1, 1])
                        with col1:
                            st.text(f"📄 {file_name} ({file_size_kb:.1f} KB)")

                        # Auto-download button
                        with col2:
                            if st.button(
                                "⬇️ Auto-Download",
                                key=f"auto_download_{file_id}",
                                help="Auto-save to Downloads folder and process"
                            ):
                                with st.spinner(f"Processing {file_name}..."):
                                    result = PLMAutoDownloadFlow.process_downloaded_file(
                                        filename=file_name,
                                        file_content=file_content,
                                        source_defect=st.session_state.get('plm_selected_defect_code'),
                                        auto_save=True,
                                        auto_extract_logs=True
                                    )

                                    # Show processing results
                                    if result['success']:
                                        st.success(f"✅ Processing completed")
                                        for msg in result['messages']:
                                            st.info(msg)

                                        # Show extracted logs if any
                                        if result['extracted_logs']:
                                            st.success(f"📋 Extracted {len(result['extracted_logs'])} log file(s)")
                                            for log_name in result['extracted_logs']:
                                                st.caption(f"  • {log_name}")

                                            # Trigger auto-analysis if logs were extracted
                                            st.rerun()
                                    else:
                                        st.warning(f"⚠️ Processing had issues")
                                        for msg in result['messages']:
                                            st.warning(msg)

                        # If ZIP file, add button to open and view contents
                        with col3:
                            if is_zip:
                                if st.button("📂 Open", key=f"open_zip_{file_id}", help="List ZIP contents"):
                                    zip_file_list = _list_zip_contents(file_content)
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
                    zip_files = []
                    for fname, fsize in st.session_state.plm_zip_file_list.items():
                        zip_files.append({
                            'File': fname,
                            'Size': f"{fsize / 1024:.1f} KB"
                        })

                    df_zip = pd.DataFrame(zip_files)
                    st.dataframe(df_zip, use_container_width=True, hide_index=True)

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
                                    file_content = _extract_file_from_zip(
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
                client = _get_plm_client()
                if not client:
                    st.error("PLM API not configured")
                    return

                division_code = "25" if division == "Mobile" else "26"

                # Note: The get_file_list API requires specific parameters that may differ
                # from the defect code. The API expects moduleCode and code parameters.
                response = client.get_file_list(
                    division_code=division_code,
                    defect_code=defect_code
                )

                if response.is_success():
                    # Response format: result is a list with objects containing 'data' array
                    result = response.result if response.result else []
                    files = []

                    # Extract files from the response structure
                    if isinstance(result, list) and len(result) > 0:
                        data = result[0].get('data', []) if isinstance(result[0], dict) else []
                        # Filter out non-file entries (messages)
                        files = [f for f in data if f.get('title') and f.get('fileId')]
                    elif isinstance(result, dict):
                        data = result.get('data', [])
                        files = [f for f in data if f.get('title') and f.get('fileId')]

                    if files:
                        # Cache files and division
                        st.session_state.plm_files_list = files
                        st.session_state.plm_files_division = division_code
                        st.success(f"Found {len(files)} file(s)")
                        st.rerun()  # Rerun to display cached files

                    else:
                        st.info("No files attached to this defect")

                else:
                    st.error(f"Failed to list files: {response.get_error_message()}")

            except PLMAPIException as e:
                st.error(f"API Error: {e}")
                with st.expander("📋 Debug Info"):
                    st.code(str(e), language="text")
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                st.error(f"Error: {e}")
                with st.expander("📋 Debug Info"):
                    st.code(str(e), language="text")


def _format_analysis_as_comment(context: Dict[str, Any]) -> str:
    """
    Format analysis context as a PLM comment

    Args:
        context: Analysis context from PLMDefectContextBuilder or Chat answer

    Returns:
        Formatted comment text
    """
    # Check if it's from Chat (has 'answer' and 'from_chat' flag)
    if context.get('from_chat'):
        return f"💬 **AI Chat 분석 결과**\n\n{context.get('answer', 'N/A')}"

    # Otherwise it's from PLM analysis tab
    comment_lines = [
        "🤖 AI 분석 결과",
        "",
        f"**문제점:**",
        context.get('problem', 'N/A'),
        "",
        f"**근본 원인:**",
        context.get('root_cause', 'N/A'),
        "",
        f"**해결 방안:**",
        context.get('solution', 'N/A'),
    ]

    return "\n".join(comment_lines)


def render_plm_comment():
    """
    Render PLM comment management interface

    Allows adding, modifying, and deleting comments on defects
    """
    st.subheader("💬 Add Comment")

    # Check if navigating from analysis tab
    navigate_to_comment = st.session_state.get('navigate_to_comment_tab', False)
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
        default_comment = _format_analysis_as_comment(analysis_result)
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
                from plm.plm_api_client import CommentRegistrationRequest

                division_code = "25" if division == "Mobile" else "26"
                change_map = {"Save": "S", "Modify": "M", "Delete": "D"}

                request = CommentRegistrationRequest(
                    divisionCode=division_code,
                    systemCode=system_code,
                    defectCode=defect_code,
                    defectComment=comment,
                    createUser=create_user,
                    changeType=change_map[change_type]
                )

                if change_type in ["Modify", "Delete"] and comment_id:
                    request.defectCommentId = comment_id

                with st.spinner("Submitting comment..."):
                    response = st.session_state.plm_integration.client.register_comment(request)

                    if response.is_success():
                        st.success("✅ Comment submitted successfully!")
                        # Clear analysis result after successful submission
                        st.session_state.plm_current_analysis_result = None
                        st.session_state.navigate_to_comment_tab = False
                    else:
                        st.error(f"Failed: {response.get_error_message()}")

            except Exception as e:
                logger.error(f"Error: {e}")
                st.error(f"Error: {e}")




def _research_with_same_conditions():
    """Re-search using the same conditions as the previous search"""
    search_label = st.session_state.get('plm_quick_search_label', '')
    status = st.session_state.get('plm_quick_search_status', '')
    division_code = st.session_state.get('plm_quick_search_division', '25')

    # Extract search_id and search_method from label
    search_id = None
    search_method = "Group"

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
        search_method = "User ID"

    if not search_id or not status:
        st.error("Cannot re-search: Missing search conditions")
        return

    with st.spinner(f"Searching {status} defects with same conditions..."):
        try:
            client = _get_plm_client()
            if not client:
                st.error("PLM API not configured")
                return

            response = client.get_defect_list(
                division_code=division_code,
                main_owner_id=search_id,
                status=status.lower(),
                search_type="main"
            )

            if not response.is_success():
                error_msg = response.get_error_message()
                st.error(f"Search failed: {error_msg}")
                return

            result_data = response.result.get('resultData', [])

            if not result_data or not isinstance(result_data, list) or len(result_data) == 0:
                st.info(f"No defects found")
                return

            defect_codes = []
            for result in result_data:
                if isinstance(result, dict) and 'defectCode' in result:
                    codes = result['defectCode']
                    if isinstance(codes, list):
                        defect_codes.extend(codes)
                    elif isinstance(codes, str):
                        defect_codes.extend([code.strip() for code in codes.split(',') if code.strip()])

            if not defect_codes:
                st.info(f"No {status} defects found")
                return

            codes_to_fetch = defect_codes[:50]
            st.info(f"Found {len(defect_codes)} {status} defect code(s). Loading details...")
            if len(defect_codes) > 50:
                st.warning(f"Showing first 50 out of {len(defect_codes)} defects")

            response_details = client.get_defect_info(
                division_code=division_code,
                defect_codes=codes_to_fetch
            )

            if response_details.is_success():
                defects = response_details.result.get('defectList', [])

                if defects:
                    st.session_state.plm_quick_search_results = defects
                    st.session_state.plm_quick_search_selected_index = 0
                    st.session_state.navigate_to_chat = False
                    st.success(f"Refreshed: {len(defects)} {status} defect(s)")
                    st.rerun()
                else:
                    st.info(f"No defect details available")
            else:
                error_msg = response_details.get_error_message()
                st.error(f"Failed to load details: {error_msg}")

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
                    if not client:
                        st.error("PLM API not configured")
                    else:
                        with st.spinner(f"Loading attached files for {defect_code}..."):
                            response = client.get_file_list(
                                division_code=division_code,
                                defect_code=defect_code
                            )

                            if response.is_success():
                                result = response.result if response.result else []
                                files = []

                                if isinstance(result, list) and len(result) > 0:
                                    data = result[0].get('data', []) if isinstance(result[0], dict) else []
                                    files = [f for f in data if f.get('title') and f.get('fileId')]
                                elif isinstance(result, dict):
                                    data = result.get('data', [])
                                    files = [f for f in data if f.get('title') and f.get('fileId')]

                                # Store files in session state
                                st.session_state.plm_quick_search_files[defect_code] = {
                                    'files': files,
                                    'division_code': division_code,
                                    'defect_code': defect_code
                                }
                                st.rerun()
                            else:
                                st.error(f"Failed to list files: {response.get_error_message()}")

                except Exception as e:
                    logger.error(f"Error loading files: {e}", exc_info=True)
                    st.error(f"Error: {e}")

        # Display files if loaded
        if defect_code in st.session_state.plm_quick_search_files:
            file_data = st.session_state.plm_quick_search_files[defect_code]
            files = file_data.get('files', [])

            if files:
                st.caption(f"{len(files)} attached file(s)")

                table_data = []
                for file in files:
                    table_data.append({
                        'Filename': file.get('title', 'N/A'),
                        'Size': f"{file.get('fileSize', 0) / 1024:.1f} KB" if file.get('fileSize') else 'N/A',
                        'Created': file.get('createDate', '')[:10] if file.get('createDate') else '',
                    })

                df = pd.DataFrame(table_data)
                st.dataframe(df, use_container_width=True, hide_index=True)

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
                            download_result = client.download_file(
                                division_code=division_code,
                                doc_id=doc_id,
                                title=title,
                                file_id=file_id
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
                        "Process saves non-ZIP files to Downloads, extracts ZIP files, and sends log files straight to the analysis pipeline."
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
                            # Auto-download button
                            if st.button(
                                "Process",
                                key=f"auto_download_{file_id}",
                                help="Auto-save to Downloads folder and process"
                            ):
                                with st.spinner(f"Processing {filename}..."):
                                    result = PLMAutoDownloadFlow.process_downloaded_file(
                                        filename=filename,
                                        file_content=content,
                                        source_defect=defect_code,
                                        auto_save=True,
                                        auto_extract_logs=True
                                    )

                                    # Show processing results
                                    if result['success']:
                                        st.success("Processing completed successfully")
                                        for msg in result['messages']:
                                            st.write(msg)

                                        # Show extracted logs if any
                                        if result['extracted_logs']:
                                            with st.expander(f"Extracted {len(result['extracted_logs'])} log file(s)", expanded=True):
                                                for log_name in result['extracted_logs']:
                                                    st.write(log_name)

                                        # Trigger auto-analysis if logs were extracted
                                        if result['extracted_logs']:
                                            st.rerun()
                                    else:
                                        st.error("Processing encountered issues")
                                        for msg in result['messages']:
                                            st.write(msg)

                        with col3:
                            # Direct save button (for users who prefer manual control)
                            if st.button(
                                "Save",
                                key=f"manual_save_{file_id}",
                                help="Manually save to Downloads"
                            ):
                                success, path_or_error = AutoDownloadManager.save_to_downloads(
                                    filename, content
                                )
                                if success:
                                    st.success(f"Saved to: {path_or_error}")
                                else:
                                    st.error(f"Failed: {path_or_error}")

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
                if not client:
                    st.error("PLM API not configured")
                    return

                response = client.get_defect_list(
                    division_code=division_code,
                    main_owner_id=search_id,
                    status=status.lower(),
                    search_type="main"
                )

                if not response.is_success():
                    error_msg = response.get_error_message()
                    st.error(f"Search failed: {error_msg}")
                    return

                result_data = response.result.get('resultData', [])

                if not result_data or not isinstance(result_data, list) or len(result_data) == 0:
                    st.info(f"No defects found")
                    return

                # Extract defect codes from ALL items in resultData
                # API returns an array where each item contains defectCode(s) for one owner
                defect_codes = []
                for result in result_data:
                    if isinstance(result, dict) and 'defectCode' in result:
                        codes = result['defectCode']
                        if isinstance(codes, list):
                            # defectCode is already a list
                            defect_codes.extend(codes)
                        elif isinstance(codes, str):
                            # defectCode is a comma-separated string
                            defect_codes.extend([code.strip() for code in codes.split(',') if code.strip()])

                if not defect_codes:
                    st.info(f"No {status} defects found")
                    return

                codes_to_fetch = defect_codes[:50]
                st.info(f"Found {len(defect_codes)} {status} defect code(s). Loading details...")
                if len(defect_codes) > 50:
                    st.warning(f"Showing first 50 out of {len(defect_codes)} defects")

                response_details = client.get_defect_info(
                    division_code=division_code,
                    defect_codes=codes_to_fetch
                )

                logger.info(f"API call: codes_to_fetch={len(codes_to_fetch)}, response codes={len(response_details.result.get('defectList', []))}")

                if response_details.is_success():
                    defects = response_details.result.get('defectList', [])
                    logger.info(f"Loaded {len(defects)} defect details")

                    if defects:
                        st.session_state.plm_quick_search_results = defects
                        st.session_state.plm_quick_search_division = division_code
                        st.session_state.plm_quick_search_label = search_label
                        st.session_state.plm_quick_search_status = status
                        st.session_state.plm_quick_search_selected_index = 0
                        st.success(f"Loaded {len(defects)} {status} defect(s)")
                        # Show results immediately after loading
                        _show_cached_results_in_fragment()
                        return
                    else:
                        st.info(f"No defect details available")
                else:
                    error_msg = response_details.get_error_message()
                    st.error(f"Failed to load details: {error_msg}")

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
    st.header("📋 PLM Defect Management")

    _initialize_plm_session()
    _render_plm_local_test_controls()

    if not st.session_state.get('plm_available', False) and not _is_plm_local_test_mode():
        st.warning("⚠️ PLM API is not configured. Check credentials and network.")
        return

    if _is_plm_local_test_mode():
        st.info("PLM 로컬 테스트 모드가 활성화되어 있습니다. 샘플 defect로 UI를 검증합니다.")

    # Check if auto-analysis should be triggered
    if st.session_state.get('trigger_auto_analysis', False):
        st.info("🚀 자동 분석 파이프라인이 시작되었습니다.")

    # Determine which tab to show based on navigation flag
    default_tab_index = 0
    if st.session_state.get('navigate_to_comment_tab', False):
        default_tab_index = 3

    # Lazy-load groups in background (non-blocking)
    if (not st.session_state.get('plm_groups_cache') and
        not st.session_state.get('plm_groups_loading') and
        not _is_plm_local_test_mode()):
        _lazy_load_groups()

    # Create tabs
    tab0, tab1, tab2, tab3 = st.tabs([
        "🔍 Quick Search",
        "🔍 검색 및 파일",
        "📊 분석",
        "💬 댓글"
    ])

    with tab0:
        try:
            # Check for cached results directly
            if st.session_state.get('plm_quick_search_results'):
                _show_cached_results_in_fragment()
            else:
                _show_search_input_form_fragment()
        except Exception as e:
            logger.error(f"Error in Quick Search: {e}", exc_info=True)
            st.error(f"Error: {e}")

    with tab1:
        try:
            col1, col2 = st.columns([1, 1])
            with col1:
                st.subheader("🔍 결함 검색")
                render_plm_search()
            with col2:
                st.subheader("📁 파일 관리")
                render_plm_files()
        except Exception as e:
            logger.error(f"Error in Search & Files: {e}", exc_info=True)
            st.error(f"Error: {e}")

    with tab2:
        try:
            render_plm_analyze()
        except Exception as e:
            logger.error(f"Error in Analysis: {e}", exc_info=True)
            st.error(f"Error: {e}")

    with tab3:
        try:
            render_plm_comment()
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

    if not st.session_state.get('plm_available', False) and not _is_plm_local_test_mode():
        return

    with st.sidebar:
        st.subheader("PLM 상태")

        try:
            if _is_plm_local_test_mode():
                st.caption("로컬 테스트 모드")

            # Show active defect if selected
            active_defect = st.session_state.get('plm_active_defect_code')
            if active_defect:
                st.info(f"**활성 결함:**\n`{active_defect}`")
            else:
                st.caption("활성 결함: 없음")

        except Exception as e:
            st.caption(str(e)[:30])


# Export functions for use in other modules
__all__ = [
    'render_plm_section',
    'render_plm_search',
    'render_plm_analyze',
    'render_plm_register',
    'render_plm_comment',
    'render_plm_files',
    'render_plm_sidebar_stats',
    '_initialize_plm_session',
    '_get_plm_client',
    '_format_analysis_as_comment',
]
