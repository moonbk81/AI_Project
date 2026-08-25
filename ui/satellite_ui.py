"""Satellite (NTN) charts.

Rendering only: the series come from `core.charts.satellite`.
"""

import plotly.express as px
import streamlit as st

from core.charts import build_ntn_overview

def render_ntn_advanced_fw_analyzer(current_base, data=None):
    st.subheader("NTN 로밍 정책 및 UI 상태")

    if not current_base:
        st.info("분석 대상 파일을 선택해 주세요.")
        return

    overview = build_ntn_overview(data)
    if overview.status == "no_data":
        st.error("로그에서 추출된 NTN 데이터가 없습니다.")
        return
    if overview.status == "no_ntn_events":
        st.info("NTN 관련 이벤트가 없습니다.")
        return

    ntn_status = overview.ntn_status
    col1, col2, col3 = st.columns(3)
    col1.metric("대상 위성 PLMN", ntn_status.plmn)
    col2.metric("적용 데이터 정책", ntn_status.data_policy)
    col3.metric("상태바 아이콘", ntn_status.icon_status)

    st.divider()

    st.markdown("**NTN 진입 및 상태 전이 타임라인**")

    if not overview.transitions.empty:
        fig = px.scatter(
            overview.transitions, x='time_dt', y='event_type', color='event_type',
            hover_data=['ntn_plmn', 'last_ntn_mode', 'ntn_mode', 'is_hysteresis', 'power_state'],
            title="NTN 상태 전이 이벤트",
            labels={'time_dt': '이벤트 시간', 'event_type': '이벤트 유형'}
        )
        fig.update_traces(marker=dict(size=14, symbol='diamond', line=dict(width=2, color='DarkSlateGrey')))
        fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")
        fig.update_layout(yaxis={'categoryorder': 'array', 'categoryarray': overview.event_order})
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("표시할 타임라인 이벤트가 없습니다.")

    st.markdown("**NTN 상태 전이 상세**")
    st.dataframe(overview.table, width="stretch")
