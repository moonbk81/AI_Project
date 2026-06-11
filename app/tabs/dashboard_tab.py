"""Dashboard tab renderer."""

import json
import os

import pandas as pd
import plotly.express as px
import streamlit as st

import ui
from agent_tools import get_device_health_kpi
from app.helpers import get_collection_metadatas_batched

def render_dashboard_tab(engine):
    st.header("로그 통계 대시보드")
    st.markdown("적재된 로그 데이터의 통계와 기존 분석 사례를 확인합니다.")

    try:
        all_data = get_collection_metadatas_batched(engine.collection, batch_size=500)
    except Exception as e:
        st.error(f"Vector DB metadata 조회 중 오류가 발생했습니다: {e}")
        all_data = {"metadatas": [], "ids": []}

    if not all_data or not all_data.get("metadatas") or len(all_data["metadatas"]) == 0:
        st.info("데이터베이스가 비어 있습니다. 로그 파일을 업로드해 주십시오.")
        return

    meta_list = [m for m in all_data["metadatas"] if m is not None]
    if not meta_list:
        st.info("데이터베이스가 비어 있습니다.")
        return

    df_all = pd.DataFrame(meta_list)

    st.divider()
    view_mode = st.radio("조회 범위", ["현재 세션", "전체 이력"], horizontal=True)

    if view_mode == "현재 세션" and st.session_state.current_file:
        df = df_all[df_all['source_file'] == st.session_state.current_file]
        st.info(f"현재 파일: `{st.session_state.current_file}`")
    else:
        df = df_all
        st.info(f"전체 이력 기준 조회 (총 {df_all['source_file'].nunique()}개 세션)")

    col1, col2, col3 = st.columns(3)
    col1.metric("적재 문서 수", f"{len(df)} 건")
    col2.metric("분석 완료 로그 수", f"{df['source_file'].nunique()} 개" if 'source_file' in df.columns else "0 개")
    col3.metric("등록 사례 수", f"{df['known_solution'].notna().sum()} 건" if 'known_solution' in df.columns else "0 건")
    st.divider()

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("로그 유형별 분포")
        if 'log_type' in df.columns:
            fig1 = px.pie(df, names='log_type', hole=0.4)
            st.plotly_chart(fig1, width="stretch")
        else:
            st.info("Log Type 데이터가 존재하지 않습니다.")

    with c2:
        st.subheader("파일별 로그 비중")
        if 'source_file' in df.columns:
            file_counts = df['source_file'].value_counts().reset_index()
            file_counts.columns = ['source_file', 'count']
            fig2 = px.bar(file_counts, x='count', y='source_file', orientation='h')
            st.plotly_chart(fig2, width="stretch")
        else:
            st.info("파일 이름 데이터가 존재하지 않습니다.")

    if view_mode != "현재 세션":
        return

    _render_current_session_dashboard(engine, df)

def _render_current_session_dashboard(engine, df):
    _render_kpi_summary(df)
    _render_knowledge_base_table(df)
    _render_integrated_timeline()
    _render_package_deep_dive(df)
    _render_detail_sections(df)
    _render_ai_integrated_report(engine, df)

def _render_kpi_summary(df):
    du_df = df[df['log_type'] == 'Data_Usage'].copy()
    if not du_df.empty:
        du_df['total_mb'] = pd.to_numeric(du_df['total_mb'], errors='coerce')
        top_1 = du_df.sort_values(by='total_mb', ascending=False).iloc[0]
        top_app_name, top_app_mb = top_1.get('app_name', 'Unknown'), f"{top_1['total_mb']:,.2f}"
    else:
        top_app_name, top_app_mb = "N/A", "0"

    call_df = df[df['log_type'] == 'Call_Session'].copy()
    if not call_df.empty:
        total_calls = len(call_df)
        drop_count = len(call_df[call_df['status'].str.contains('FAIL|DROP', na=False, case=False)]) if 'status' in call_df.columns else 0
        success_rate = round(((total_calls - drop_count) / total_calls) * 100, 1) if total_calls > 0 else 100
    else:
        success_rate, drop_count = 100, 0

    oos_df = df[df['log_type'] == 'OOS_Event'].copy()
    if not oos_df.empty:
        is_v_oos = oos_df['voice_reg'].astype(str).str.contains('OUT_OF_SERVICE|OOS', na=False, case=False) if 'voice_reg' in oos_df.columns else False
        is_d_oos = oos_df['data_reg'].astype(str).str.contains('OUT_OF_SERVICE|OOS', na=False, case=False) if 'data_reg' in oos_df.columns else False
        oos_count = len(oos_df[is_v_oos | is_d_oos])
    else:
        oos_count = 0

    sig_df = df[df['log_type'] == 'Signal_Level'].copy()
    avg_signal = sig_df['level'].mean() if not sig_df.empty else 0

    st.subheader("단말 상태 요약")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("데이터 사용 상위 앱", f"{top_app_name}", f"{top_app_mb} MB")
    col2.metric("평균 신호 수신 강도", f"Level {avg_signal:.1f}")
    col3.metric("Call 성공률", f"{success_rate}%", delta=f"{drop_count} 건 실패", delta_color="inverse" if drop_count > 0 else "normal")
    col4.metric("OOS 발생 횟수", f"{oos_count} 회", delta="망 이탈 감지" if oos_count > 0 else "안정", delta_color="inverse" if oos_count > 0 else "normal")

def _render_knowledge_base_table(df):
    st.divider()
    st.subheader("분석 사례")
    if 'known_solution' not in df.columns:
        st.info("솔루션 데이터 필드가 존재하지 않습니다.")
        return

    solution_df = df.dropna(subset=['known_solution'])[['source_file', 'log_type', 'known_solution']]
    if not solution_df.empty:
        st.dataframe(solution_df, width="stretch")
    else:
        st.info("등록된 지식 데이터가 없습니다.")

def _render_integrated_timeline():
    st.divider()
    if not st.session_state.current_file:
        st.info("분석 대상을 선택해 주십시오.")
        return

    current_base_name = st.session_state.current_file.replace("_payload.json", "")
    target_report_path = os.path.join("./result", f"{current_base_name}_report.json")
    if not os.path.exists(target_report_path):
        st.info("통합 타임라인 생성을 위한 JSON 데이터가 부족합니다.")
        return

    try:
        with open(target_report_path, 'r', encoding='utf-8') as _f:
            loaded_report_data = json.load(_f)
        ui.render_integrated_rf_call_timeline(loaded_report_data)
    except Exception as e:
        st.error(f"차트 렌더링 오류 발생: {e}")

def _render_package_deep_dive(df):
    st.divider()
    st.subheader("패키지별 상세")
    data_df = df[df['log_type'] == 'Data_Usage'].copy()
    if data_df.empty:
        st.info("Netstats 로그를 찾을 수 없습니다.")
        return

    app_list = data_df['app_name'].dropna().unique().tolist()
    if not app_list:
        st.info("데이터 사용량 기록이 존재하지 않습니다.")
        return

    top_app = data_df.groupby('app_name')['total_mb'].sum().idxmax()
    default_idx = app_list.index(top_app) if top_app in app_list else 0
    selected_app = st.selectbox("패키지 선택", app_list, index=default_idx)
    target_app_df = data_df[data_df['app_name'] == selected_app]
    rat_summary = target_app_df.groupby('rat')['total_mb'].sum().reset_index()
    rat_summary['total_mb'] = rat_summary['total_mb'].apply(lambda x: f"{x:,.2f} MB")

    c1, c2 = st.columns([1, 2.5])
    with c1:
        st.markdown(f"**[{selected_app}] RAT 요약**")
        st.dataframe(rat_summary, hide_index=True, width="stretch")
    with c2:
        st.markdown("**상세 트래픽 로그**")
        display_cols = ['time', 'rat', 'total_mb', 'rx_bytes', 'tx_bytes']
        actual_cols = [c for c in display_cols if c in target_app_df.columns]
        st.dataframe(target_app_df[actual_cols], hide_index=True, width="stretch")

def _render_detail_sections(df):
    current_base = st.session_state.current_file.replace("_payload.json", "") if st.session_state.current_file else ""

    st.divider()
    ui.render_rilj_transactions(current_base)
    st.divider()
    ui.render_battery_thermal_chart(df)
    st.divider()
    ui.render_network_timeseries_and_dns(df)
    st.divider()
    ui.render_dns_analysis_chart(df)
    st.divider()
    ui.render_call_history_summary(df)
    st.divider()
    ui.render_signal_level_timeline(df)
    st.divider()
    ui.render_service_state_timeline(df)
    st.divider()
    ui.render_data_usage_profiling(df)
    st.divider()
    ui.render_data_usage_timeline(df)
    st.divider()
    ui.render_ims_sip_flow(current_base)
    st.divider()

    current_dc_data = []
    if current_base:
        dc_json_path = f"./result/{current_base}_datacall.json"
        if os.path.exists(dc_json_path):
            with open(dc_json_path, 'r', encoding='utf-8') as f:
                current_dc_data = json.load(f)
    ui.render_data_call_analyzer(current_dc_data)

def _render_ai_integrated_report(engine, df):
    st.subheader("종합 진단 리포트")
    if not st.button("현재 세션 리포트 생성", width="stretch"):
        return

    with st.spinner("관련 이벤트와 지표를 정리하는 중입니다..."):
        actual_file_name = df['source_file'].iloc[0] if not df.empty and 'source_file' in df.columns else "Unknown"
        current_base = st.session_state.current_file.replace("_payload.json", "")
        health_kpi_json = get_device_health_kpi(current_base)
        combined_query = f"""
        [입력 데이터]
        {health_kpi_json}

        [지시사항]
        제공된 데이터와 검색된 로그를 기반으로 단말 상태를 진단하여 다음 항목만 작성하십시오.

        1. 핵심 원인 (Root Cause):
           - '9_ril_sip_correlation' 항목에 문제가 확인되면 이를 최상단에 가장 먼저 명시하십시오.
        2. 주요 이상 징후 요약:
           - 입력 데이터 내 부문별 에러나 특이사항을 사실대로 요약하십시오.

        * 규칙: 데이터에 없는 수치나 원인을 임의로 추측하거나 지어내지 마십시오.
        """

        raw_result = engine.ask(combined_query, current_file=actual_file_name)

        if isinstance(raw_result, (tuple, list)):
            report_answer = raw_result[0]
            report_thinking = raw_result[3] if len(raw_result) > 3 else ""
        else:
            report_answer = raw_result
            report_thinking = ""

        st.success("리포트 생성이 완료되었습니다.")

        if report_thinking:
            with st.expander("처리 과정", expanded=False):
                st.markdown(f"```text\n{report_thinking}\n```")

        _render_copyable_report(report_answer)

        all_db_data = get_collection_metadatas_batched(
            engine.collection,
            batch_size=500,
            where={"source_file": actual_file_name}
        )
        if all_db_data and all_db_data.get('ids'):
            st.session_state.last_ids = all_db_data['ids']
            st.session_state.last_metas = all_db_data['metadatas']
            st.toast("리포트 결과가 임시 저장되었습니다.")

def _render_copyable_report(report_answer):
    safe_report = report_answer.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$").replace("\n", "\\n")
    st.markdown(f"""
<div style="position: relative; background-color: #f8f9fa; padding: 25px; border-radius: 4px; border-left: 4px solid #0056b3; margin-bottom: 20px;">
<button onclick="copyReport()" style="position: absolute; top: 10px; right: 10px; padding: 6px 12px; background-color: #0056b3; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: 500;">복사</button>
<div id="full-report-text" style="white-space: pre-wrap; font-size: 14px; color: #212529; line-height: 1.6;">

{report_answer}

</div>
</div>
<script>
function copyReport() {{
    const reportText = `{safe_report}`;
    navigator.clipboard.writeText(reportText.replace(/\\n/g, '\n')).then(() => {{
        alert('리포트 내용을 복사했습니다.');
    }}).catch(err => {{
        console.error('Copy failed:', err);
    }});
}}
</script>
""", unsafe_allow_html=True)
