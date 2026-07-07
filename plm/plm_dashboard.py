"""
PLM Defect Dashboard - Streamlit Integration

Displays PLM defect information alongside AI analysis results
in the Streamlit dashboard.
"""

import streamlit as st
from typing import Optional, List, Dict, Any
import pandas as pd
from datetime import datetime
import logging

from plm_rag_integration import (
    create_plm_integration,
    PLMDefectContextBuilder,
    PLMConfigManager
)
from plm_api_client import DivisionCode, PLMAPIException

logger = logging.getLogger(__name__)


def initialize_session_state():
    """Initialize Streamlit session state for PLM"""
    if 'plm_integration' not in st.session_state:
        try:
            st.session_state.plm_integration = create_plm_integration()
            st.session_state.plm_available = True
        except Exception as e:
            logger.error(f"Failed to initialize PLM: {e}")
            st.session_state.plm_available = False

    if 'plm_cache' not in st.session_state:
        st.session_state.plm_cache = {}


def render_plm_section():
    """Render PLM defect section in dashboard"""
    st.header("📋 PLM Defect Management")

    if not st.session_state.get('plm_available', False):
        st.warning("⚠️ PLM API is not configured. Check credentials and network.")
        return

    # Create tabs for different PLM operations
    tab1, tab2, tab3, tab4 = st.tabs([
        "🔍 Search Defects",
        "📊 Defect Analysis",
        "➕ Register Defect",
        "📝 Add Comment"
    ])

    with tab1:
        render_search_defects_tab()

    with tab2:
        render_defect_analysis_tab()

    with tab3:
        render_register_defect_tab()

    with tab4:
        render_add_comment_tab()


def render_search_defects_tab():
    """Render search defects tab"""
    st.subheader("Search Defects")

    col1, col2 = st.columns(2)

    with col1:
        division = st.selectbox(
            "Division",
            options=["Mobile", "Network"],
            format_func=lambda x: f"{x} ({'25' if x == 'Mobile' else '26'})"
        )

    division_code = "25" if division == "Mobile" else "26"

    with col2:
        search_type = st.radio(
            "Search by",
            options=["Defect Code", "Defect ID"],
            horizontal=True
        )

    # Search input
    if search_type == "Defect Code":
        search_input = st.text_input(
            "Defect Code",
            placeholder="e.g., P190404-00007",
            help="Enter one or more defect codes separated by commas"
        )
        search_values = [code.strip() for code in search_input.split(",") if code.strip()]
        is_code_search = True
    else:
        search_input = st.text_input(
            "Defect ID",
            placeholder="e.g., 00EIYX38PtPMWL1000",
            help="Enter one or more defect IDs separated by commas"
        )
        search_values = [id.strip() for id in search_input.split(",") if id.strip()]
        is_code_search = False

    if st.button("🔍 Search", key="search_defects"):
        if not search_values:
            st.error("Please enter at least one defect code or ID")
            return

        with st.spinner("Searching defects..."):
            try:
                integration = st.session_state.plm_integration
                response = integration.client.get_defect_info(
                    division_code=division_code,
                    defect_codes=search_values if is_code_search else None,
                    defect_ids=search_values if not is_code_search else None
                )

                if response.is_success():
                    defects = response.result.get('defectList', [])

                    if defects:
                        st.success(f"Found {len(defects)} defect(s)")
                        render_defects_table(defects)

                        # Show details for first defect
                        if defects:
                            st.subheader("Detailed View")
                            render_defect_details(defects[0], division_code)
                    else:
                        st.info("No defects found matching the criteria")

                else:
                    st.error(f"Search failed: {response.get_error_message()}")

            except PLMAPIException as e:
                st.error(f"API Error: {e}")
            except Exception as e:
                st.error(f"Error: {e}")


def render_defects_table(defects: List[Dict[str, Any]]):
    """Render defects in a table"""
    df_data = []

    for defect in defects:
        df_data.append({
            'Code': defect.get('defectCode'),
            'Title': defect.get('plmTitle')[:50] + "..." if len(defect.get('plmTitle', '')) > 50 else defect.get('plmTitle'),
            'Status': defect.get('plmStatus'),
            'Priority': defect.get('plmPriority'),
            'Owner': defect.get('mainOwnerName'),
            'Created': defect.get('createDate')[:10] if defect.get('createDate') else ''
        })

    df = pd.DataFrame(df_data)
    st.dataframe(df, use_container_width=True)


def render_defect_details(defect: Dict[str, Any], division_code: str):
    """Render detailed view of a defect"""
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Status", defect.get('plmStatus', 'N/A'))
    with col2:
        st.metric("Priority", defect.get('plmPriority', 'N/A'))
    with col3:
        st.metric("Main Owner", defect.get('mainOwnerName', 'N/A')[:20])
    with col4:
        st.metric("Created", defect.get('createDate', 'N/A')[:10])

    # Problem description
    with st.expander("📌 Problem Description"):
        st.write(defect.get('content', 'N/A'))

    # Root cause
    with st.expander("🔍 Root Cause"):
        st.write(defect.get('reason', 'N/A'))

    # Solution
    with st.expander("✅ Solution/Countermeasure"):
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


def render_defect_analysis_tab():
    """Render defect analysis tab"""
    st.subheader("Defect Analysis")

    col1, col2 = st.columns(2)

    with col1:
        defect_code = st.text_input(
            "Defect Code for Analysis",
            placeholder="P190404-00007"
        )

    with col2:
        division = st.selectbox(
            "Division (Analysis)",
            options=["Mobile", "Network"],
            key="analysis_division"
        )

    division_code = "25" if division == "Mobile" else "26"

    if st.button("📊 Analyze", key="analyze_defect"):
        if not defect_code:
            st.error("Please enter a defect code")
            return

        with st.spinner("Analyzing defect..."):
            try:
                integration = st.session_state.plm_integration
                builder = PLMDefectContextBuilder(integration)

                context = builder.build_defect_context(defect_code, division_code)

                if context:
                    # Display analysis
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
                        st.write("**Last Updated:**", context.get('updated_date', 'N/A'))

                else:
                    st.error("Defect not found")

            except PLMAPIException as e:
                st.error(f"API Error: {e}")
            except Exception as e:
                st.error(f"Error: {e}")


def render_register_defect_tab():
    """Render register defect tab"""
    st.subheader("Register New Defect")

    # Form for defect registration
    with st.form("defect_registration"):
        col1, col2 = st.columns(2)

        with col1:
            division = st.selectbox(
                "Division",
                options=["Mobile", "Network"],
                key="reg_division"
            )
            system_code = st.text_input("System Code", value="AI_ANALYSIS")

        with col2:
            change_type = st.selectbox("Type", options=["DRAFT", "OPEN"])
            create_user = st.text_input("Creator Knox ID", value="your_knox_id")

        # Main details
        title = st.text_input("Title", placeholder="Brief description of the issue")
        content = st.text_area("Problem Description", height=100)

        col1, col2 = st.columns(2)
        with col1:
            importance = st.selectbox("Priority", options=["A", "B", "C"])
            occur_rate = st.selectbox(
                "Occurrence Rate",
                options=["Always", "Sometimes", "Once"]
            )

        with col2:
            project_name = st.text_input("Project/Model Name", value="Galaxy S24")
            external_id = st.text_input("External Defect ID", value="EXT_001")

        col1, col2 = st.columns(2)
        with col1:
            test_unit = st.text_input("Test Unit", value="S/W Engineering")
            function_block = st.text_input("Function Block", value="General")

        with col2:
            test_item = st.text_input("Test Item", value="Functional Test")
            detail_function = st.text_input("Feature", value="General Feature")

        # Optional fields
        with st.expander("Advanced Options"):
            reappearance = st.text_area("Steps to Reproduce", height=60)
            forecast = st.text_area("Expected Result", height=60)
            sw_version = st.text_input("S/W Version")

        submit = st.form_submit_button("📤 Register Defect")

        if submit:
            if not all([title, content, create_user]):
                st.error("Title, content, and creator are required")
                return

            try:
                from plm_api_client import DefectRegistrationRequest, ChangeType

                division_code = "25" if division == "Mobile" else "26"

                request = DefectRegistrationRequest(
                    divisionCode=division_code,
                    systemCode=system_code,
                    changeType=change_type,
                    refObjectName=project_name,
                    refObjectType="MFG",
                    externalDefectId=external_id,
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
                        st.success(f"✅ Defect registered successfully!\n\n**Code:** {defect_code}\n**ID:** {defect_id}")
                    else:
                        st.error(f"Registration failed: {response.get_error_message()}")

            except Exception as e:
                st.error(f"Error: {e}")


def render_add_comment_tab():
    """Render add comment tab"""
    st.subheader("Add Comment to Defect")

    with st.form("add_comment"):
        col1, col2 = st.columns(2)

        with col1:
            division = st.selectbox(
                "Division",
                options=["Mobile", "Network"],
                key="comment_division"
            )
            defect_code = st.text_input(
                "Defect Code",
                placeholder="P190404-00007"
            )

        with col2:
            system_code = st.text_input("System Code", value="AI_ANALYSIS")
            create_user = st.text_input(
                "Your Knox ID",
                value="your_knox_id",
                key="comment_user"
            )

        comment = st.text_area(
            "Comment",
            height=100,
            placeholder="Add your comment here..."
        )

        col1, col2 = st.columns(2)
        with col1:
            change_type = st.radio("Action", options=["Save", "Modify", "Delete"], horizontal=True)
        with col2:
            if change_type == "Modify" or change_type == "Delete":
                comment_id = st.text_input(
                    "Comment ID (for Modify/Delete)",
                    placeholder="01YJK98RTtPMWL1000"
                )

        submit = st.form_submit_button("💬 Submit Comment")

        if submit:
            if not all([defect_code, create_user, comment]):
                st.error("Defect Code, Knox ID, and Comment are required")
                return

            try:
                from plm_api_client import CommentRegistrationRequest, ChangeType

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
                        st.success(f"✅ Comment submitted successfully!")
                    else:
                        st.error(f"Failed: {response.get_error_message()}")

            except Exception as e:
                st.error(f"Error: {e}")


def render_plm_stats_sidebar():
    """Render PLM stats in sidebar"""
    if not st.session_state.get('plm_available', False):
        return

    with st.sidebar:
        st.markdown("---")
        st.subheader("📋 PLM Status")

        try:
            # Check connection
            st.caption("✅ Connected to PLM")

            # Quick actions
            if st.button("🔄 Refresh PLM Cache"):
                if 'plm_integration' in st.session_state:
                    st.session_state.plm_integration.clear_documents()
                    st.success("Cache cleared")

        except Exception as e:
            st.caption(f"❌ {str(e)[:30]}")


if __name__ == "__main__":
    # For standalone testing
    st.set_page_config(layout="wide", page_title="PLM Dashboard")
    initialize_session_state()
    render_plm_section()
