"""Satellite (NTN) charts.

Rendering only: the series come from `core.charts.satellite`.
"""

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.charts import SAT_FLOW_ACTORS, build_ntn_overview, build_sat_at_overview

# Framework hops are purple, modem-side hops blue; a highlighted step of either
# gets the stronger shade, and anything that failed goes red.
FLOW_COLORS = {
    (True, True): "#9c27b0",
    (True, False): "#ba68c8",
    (False, True): "#d32f2f",
    (False, False): "#1f77b4",
}
FLOW_ERROR_COLOR = "red"

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

def render_sat_at_analyzer(current_base=None, data=None):
    st.subheader("위성 모뎀 제어 상태")

    if not current_base: return

    overview = build_sat_at_overview(data)
    kpi = overview.kpi

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("위성 ARFCN", kpi.arfcn)
    c2.metric("등록 상태", kpi.reg_state)
    c3.metric("음성 통화(전체/실패)", f"{kpi.calls_total} / {kpi.calls_failed}",
              delta=f"{kpi.calls_failed}건 실패" if kpi.calls_failed > 0 else "정상", delta_color="inverse")
    c4.metric("SMS(Rx/Tx 성공/Tx 실패)", f"{kpi.sms_rx} / {kpi.sms_tx_success} / {kpi.sms_tx_fail}",
              delta=f"{kpi.sms_tx_fail}건 실패" if kpi.sms_tx_fail > 0 else "정상", delta_color="inverse")
    st.divider()

    if not overview.registration.empty:
        st.write("#### 위성망 등록 이력")

        fig_reg = px.line(
            overview.registration, x="time", y="status_str", markers=True,
            hover_data=["raw"],
            labels={"time": "시간", "status_str": "상태"}
        )
        fig_reg.update_traces(line_shape='hv', line_color='#E64A19', marker=dict(size=8))
        fig_reg.update_yaxes(categoryorder='array', categoryarray=overview.reg_state_order)
        fig_reg.update_layout(height=250, margin=dict(t=20, b=20))
        st.plotly_chart(fig_reg, width="stretch")
        st.divider()

    if overview.call_flow:
        st.write("#### 통화 제어 시퀀스(AP ↔ RIL ↔ Modem)")
        _render_sat_call_flow(overview.call_flow)

def _render_sat_call_flow(call_flow):
    fig = go.Figure()
    step_count = len(call_flow)

    for idx, step in enumerate(call_flow):
        offset = 0.05
        x0 = step.src + offset if step.src < step.dst else step.src - offset
        x1 = step.dst - offset if step.src < step.dst else step.dst + offset
        y = step_count - idx

        color = FLOW_ERROR_COLOR if step.is_error else FLOW_COLORS[(step.involves_framework, step.is_highlight)]

        fig.add_annotation(
            x=x1, y=y, ax=x0, ay=y, xref="x", yref="y", axref="x", ayref="y",
            text=f"<b>{step.desc}</b>", showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=1.5, arrowcolor=color,
            font=dict(color=color, size=11), align="center", yshift=8
        )
        fig.add_annotation(
            x=-0.2, y=y, xref="x", yref="y", text=step.time, showarrow=False,
            font=dict(size=10, color="gray"), xanchor="right"
        )

    fig.update_layout(
        xaxis=dict(
            tickmode='array', tickvals=list(range(len(SAT_FLOW_ACTORS))),
            ticktext=SAT_FLOW_ACTORS,
            tickfont=dict(size=14, weight='bold'),
            range=[-0.5, len(SAT_FLOW_ACTORS) - 0.5], side="top", showgrid=False, zeroline=False
        ),
        yaxis=dict(showticklabels=False, range=[0, step_count+1], showgrid=False, zeroline=False),
        height=max(400, step_count * 35), margin=dict(l=150, r=50, t=60, b=20), plot_bgcolor="white"
    )
    st.plotly_chart(fig, width="stretch")
