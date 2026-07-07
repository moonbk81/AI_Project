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

logger = logging.getLogger(__name__)


def _initialize_plm_session():
    """Initialize Streamlit session state for PLM"""
    if 'plm_integration' not in st.session_state:
        try:
            st.session_state.plm_integration = create_plm_integration()
            st.session_state.plm_available = True
            st.session_state.plm_cache = {}
            st.session_state.plm_search_results = None
            st.session_state.plm_search_division = None
            st.session_state.plm_analysis_results = None
            st.session_state.plm_selected_defect_code = None
            st.session_state.plm_selected_division = None
            st.session_state.plm_files_list = None
            st.session_state.plm_download_data = {}  # {file_id: (file_data, file_name)}
            st.session_state.plm_zip_file_data = None  # Binary data of ZIP file (for lazy extraction)
            st.session_state.plm_zip_file_list = {}  # {filename: file_size} - metadata only
            st.session_state.plm_selected_from_zip = None  # Selected file from ZIP
        except Exception as e:
            logger.error(f"Failed to initialize PLM: {e}")
            st.session_state.plm_available = False
            st.session_state.plm_integration = None


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
            _render_defects_table(st.session_state.plm_search_results)

            # Show details for cached results
            for i, defect in enumerate(st.session_state.plm_search_results):
                with st.expander(f"📋 Details: {defect.get('defectCode')}"):
                    try:
                        _render_defect_details(defect, st.session_state.plm_search_division)
                    except Exception as e:
                        logger.error(f"Error rendering cached defect details: {e}", exc_info=True)
                        st.error(f"Error displaying defect details: {str(e)}")
                        with st.expander("Debug Info"):
                            st.json({"defect_keys": list(defect.keys()), "error": str(e)})

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
                        _render_defects_table(defects)

                        # Show details for each defect
                        for i, defect in enumerate(defects):
                            with st.expander(f"📋 Details: {defect.get('defectCode')}"):
                                try:
                                    _render_defect_details(defect, division_code)
                                except Exception as e:
                                    logger.error(f"Error rendering defect details: {e}", exc_info=True)
                                    st.error(f"Error displaying defect details: {str(e)}")
                                    with st.expander("Debug Info"):
                                        st.json({"defect_keys": list(defect.keys()), "error": str(e)})
                    else:
                        st.info("No defects found")

                else:
                    st.error(f"Search failed: {response.get_error_message()}")

            except PLMAPIException as e:
                st.error(f"API Error: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                st.error(f"Error: {e}")


def _render_defects_table(defects: List[Dict[str, Any]]):
    """Render defects in a table"""
    df_data = []

    for defect in defects:
        # Safe handling of Title (truncate if too long)
        title = defect.get('plmTitle', '')
        if isinstance(title, str) and len(title) > 50:
            title = title[:50] + "..."

        # Safe handling of Created date (get first 10 chars)
        created = defect.get('createDate', '')
        if isinstance(created, str) and created:
            created = created[:10]

        df_data.append({
            'Code': defect.get('defectCode'),
            'Title': title,
            'Status': defect.get('plmStatus', 'N/A'),
            'Priority': defect.get('plmPriority', 'N/A'),
            'Owner': defect.get('mainOwnerName', 'N/A'),
            'Created': created
        })

    df = pd.DataFrame(df_data)
    st.dataframe(df, use_container_width=True)


def _render_defect_details(defect: Dict[str, Any], division_code: str):
    """Render detailed view of a defect"""
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

        # Button to send to Chat analysis
        col1, col2 = st.columns([3, 1])
        with col2:
            # Check if we just saved this problem to avoid duplicate processing
            current_defect_code = defect.get('defectCode')
            last_analyzed = st.session_state.get('plm_last_analyzed_code')

            is_already_sent = (
                st.session_state.get('plm_problem_query') and
                st.session_state.plm_problem_query.get('defect_code') == current_defect_code and
                not st.session_state.get('plm_problem_analyzed', True)  # Not yet analyzed
            )

            # Create safe button key
            defect_code_str = str(current_defect_code) if current_defect_code else "unknown"
            button_key = f"analyze_problem_{defect_code_str}"

            if st.button(
                "🚀 분석하기",
                key=button_key,
                help="Send this problem to Chat tab for analysis",
                disabled=is_already_sent
            ):
                # Store problem content in session for Chat tab
                st.session_state.plm_problem_query = {
                    'content': problem_content,
                    'defect_code': defect.get('defectCode'),
                    'defect_title': defect.get('plmTitle', 'Unknown'),
                    'timestamp': datetime.now().isoformat()
                }
                st.session_state.plm_problem_analyzed = False  # Reset analyzed flag
                st.session_state.plm_last_analyzed_code = current_defect_code
                st.session_state.navigate_to_chat = True  # Flag to navigate to chat tab
                st.success("✅ Problem content saved! Navigating to Log Analysis tab...")
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

            # Show download buttons for cached files
            if st.session_state.plm_download_data:
                st.divider()
                st.subheader("💾 Save Downloaded Files")
                st.info(
                    f"📥 **{len(st.session_state.plm_download_data)} file(s) ready**\n\n"
                    f"**How to download:**\n"
                    f"1. Click the 💾 button below\n"
                    f"2. Browser will automatically save to: **Downloads folder** (`~/Downloads` or `C:\\Users\\{{username}}\\Downloads`)\n"
                    f"3. Check your Downloads folder for the file"
                )

                for file_id, (file_content, file_name) in st.session_state.plm_download_data.items():
                    if file_content:
                        file_size_kb = len(file_content) / 1024
                        is_zip = file_name.lower().endswith('.zip')

                        col1, col2, col3 = st.columns([2, 1, 1])
                        with col1:
                            st.download_button(
                                label=f"💾 {file_name} ({file_size_kb:.1f} KB)",
                                data=file_content,
                                file_name=file_name,
                                key=f"save_{file_id}",
                                use_container_width=True
                            )
                        with col2:
                            st.caption(f"{len(file_content)} bytes")

                        # If ZIP file, add button to open and view contents
                        with col3:
                            if is_zip:
                                if st.button("📂 Open", key=f"open_zip_{file_id}", help="List ZIP contents (memory efficient)"):
                                    zip_file_list = _list_zip_contents(file_content)
                                    if zip_file_list:
                                        st.session_state.plm_zip_file_data = file_content  # Store ZIP binary
                                        st.session_state.plm_zip_file_list = zip_file_list  # Store metadata only
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


def render_plm_comment():
    """
    Render PLM comment management interface

    Allows adding, modifying, and deleting comments on defects
    """
    st.subheader("💬 Add Comment")

    # Use selected defect code from Search tab if available
    default_code = st.session_state.get('plm_selected_defect_code', '')
    default_division = st.session_state.get('plm_selected_division')

    if default_code:
        st.info(f"📌 Using Defect Code from Search: **{default_code}**")

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

    with st.form("add_comment"):
        col1, col2 = st.columns(2)

        with col1:
            system_code = st.text_input("System Code", value="AI_ANALYSIS", key="comment_system")
        with col2:
            create_user = st.text_input("Your Knox ID", key="comment_user")

        comment = st.text_area(
            "Comment",
            height=100,
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
                    else:
                        st.error(f"Failed: {response.get_error_message()}")

            except Exception as e:
                logger.error(f"Error: {e}")
                st.error(f"Error: {e}")


def render_plm_section():
    """
    Main PLM section renderer

    Renders tabs for different PLM operations
    """
    st.header("📋 PLM Defect Management")

    _initialize_plm_session()

    if not st.session_state.get('plm_available', False):
        st.warning("⚠️ PLM API is not configured. Check credentials and network.")
        return

    # Create tabs
    tab1, tab2, tab3, tab4 = st.tabs([
        "🔍 검색 및 파일",
        "📊 분석",
        "➕ 등록",
        "💬 댓글"
    ])

    with tab1:
        # Combined Search and Files tab
        col1, col2 = st.columns([1, 1])

        with col1:
            st.subheader("🔍 결함 검색")
            render_plm_search()

        with col2:
            st.subheader("📁 파일 관리")
            render_plm_files()

    with tab2:
        render_plm_analyze()

    with tab3:
        render_plm_register()

    with tab4:
        render_plm_comment()


def render_plm_sidebar_stats():
    """
    Render PLM status in sidebar

    Shows connection status and quick actions
    """
    _initialize_plm_session()

    if not st.session_state.get('plm_available', False):
        return

    with st.sidebar:
        st.markdown("---")
        st.subheader("📋 PLM Status")

        try:
            st.caption("✅ Connected to PLM")

            # Quick actions
            if st.button("🔄 Refresh Cache", key="btn_refresh_plm"):
                if 'plm_integration' in st.session_state:
                    st.session_state.plm_integration.clear_documents()
                    st.success("Cache cleared")

        except Exception as e:
            st.caption(f"❌ {str(e)[:30]}")


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
]
