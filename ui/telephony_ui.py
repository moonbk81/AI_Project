"""Telephony charts.

Rendering only: every series, KPI and table on this page is shaped by
`core.charts.telephony`, so the code here is limited to plotly styling and the
Streamlit layout.
"""

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.charts import (
    build_call_history_summary,
    build_data_call_summary,
    build_nitz_timeline,
    build_rf_call_timeline,
    build_rilj_overview,
    build_service_state_series,
    build_signal_level_series,
    build_sip_flow,
)

SIP_KIND_COLORS = {"error": "#e74c3c", "success": "#2ecc71", "normal": "#3498db"}

DATA_CALL_STATUS_COLORS = {
    "SUCCESS": "#2ecc71",
    "FAIL": "#e74c3c",
    "DORMANT": "#f1c40f",
    "ACTIVE": "#3498db",
    "DROP": "#8e44ad",
}

NITZ_STABILITY_LABELS = {
    "unstable": "불안정 (핑퐁)",
    "long_stay": "장기 체류",
    "stable": "안정",
}

def render_call_history_summary(df):
    """전체 통화 세션 (Call History) 차트 및 표 렌더링"""
    st.subheader("통화 세션 현황")

    summary = build_call_history_summary(df)
    if summary.status == "unavailable":
        return
    if summary.status == "no_calls":
        st.info("현재 분석 세션에 Call_Session 로그가 존재하지 않습니다.")
        return

    col_chart, col_table = st.columns([1, 2])
    with col_chart:
        st.markdown("**통화 상태 분포**")
        if summary.statuses is None:
            st.info("Status 데이터 필드가 누락되었습니다.")
        else:
            fig_call = px.pie(
                {"status": summary.statuses},  # px counts the occurrences itself
                names='status', hole=0.4, title="통화 성공/실패 비율"
            )
            st.plotly_chart(fig_call, width="stretch")
    with col_table:
        st.markdown(f"**통화 이력 상세(총 {summary.call_count}건)**")
        st.dataframe(summary.table, width="stretch", height=400)

def render_signal_level_timeline(df):
    st.subheader("RAT별 신호 세기 추이")

    series = build_signal_level_series(df)
    if series.status == "unavailable":
        return
    if series.status == "no_data":
        st.info("Signal Level 데이터가 없습니다.")
        return

    sig_df = series.to_frame()

    fig = px.line(
        sig_df, x='time', y='Level', color='rat', facet_row='slot',
        line_shape='hv', markers=True,
        title="RAT별 Signal Level 변화",
        hover_data={'hover_detail': True, 'raw_info': True}
    )

    fig.update_traces(
        hovertemplate="<b>%{customdata[0]}</b><br>Level: %{y}<br>Details:<br>%{customdata[1]}<extra></extra>",
        customdata=sig_df[['rat', 'hover_detail']].values
    )

    st.plotly_chart(fig, width="stretch")

def render_service_state_timeline(df):
    st.subheader("망 등록 상태 추이")

    series = build_service_state_series(df)

    if series.status == "unavailable":
        return
    if series.status == "no_events":
        st.success("IN_SERVICE 상태가 유지되었으며, OOS 또는 등록 상태 전이가 감지되지 않았습니다.")
        return
    if series.status == "no_changes":
        st.info("표시할 주요 상태 변화가 없습니다.")
        return

    clean_df = series.to_frame()
    category_order = series.state_order

    fig = px.line(
        clean_df, x='time_dt', y='State', color='Type', facet_row='Slot',
        line_shape='hv', markers=True,
        text='Label',
        title="Voice/Data 등록 상태 전이",
        labels={'time_dt': '이벤트 시간', 'State': '상태', 'Type': '연결 유형'},
        hover_data=['Event', 'Cause', 'Raw_Reg', 'Operator', 'Radio_Tech'],
        category_orders={"State": category_order}
    )

    fig.update_traces(
        marker=dict(size=8, line=dict(width=1, color='DarkSlateGrey')),
        textposition="top right",
        textfont=dict(size=11)
    )
    fig.update_yaxes(categoryorder='array', categoryarray=category_order)
    fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")

    chart_height = max(300, 200 * series.slot_count)
    fig.update_layout(height=chart_height, hovermode="x unified", margin=dict(t=50, b=20))

    st.plotly_chart(fig, width="stretch")

def render_data_call_analyzer(data):
    st.subheader("Data Call 설정 현황")

    summary = build_data_call_summary(data)
    if summary.status == "no_data":
        st.info("SETUP_DATA_CALL 이력이 없습니다.")
        return

    kpi = summary.kpi
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("연결 시도", f"{kpi.attempt_count}")
    col2.metric("성공률", f"{kpi.success_rate:.1f} %")
    col3.metric("실패 건수", f"{kpi.fail_count}")
    col4.metric("평균 설정 지연", f"{kpi.avg_setup_latency_ms:.0f} ms")

    st.divider()

    st.markdown("**Data Call 상태 전이**")

    chart_df = summary.to_frame()
    if not chart_df.empty:
        fig = px.scatter(
            chart_df, x='req_time_dt', y='apn', color='status',
            color_discrete_map=DATA_CALL_STATUS_COLORS,
            symbol='event_type',
            size=[15]*len(chart_df),
            hover_data=['event_type', 'network', 'protocol', 'cause', 'latency_ms', 'cid'],
            title="APN별 Data Call 상태 전이",
            labels={'req_time_dt': '시간', 'apn': 'APN'}
        )
        fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")
        st.plotly_chart(fig, width="stretch", key="datacall_scatter_chart")
    else:
        st.info("표시할 이벤트가 없습니다.")

    st.markdown("**Data Call 상세 이력**")
    st.dataframe(summary.table, width="stretch")

def render_ims_sip_flow(current_base=None, data=None):
    st.subheader("VoLTE / IMS SIP 흐름")

    if not current_base: return

    flow = build_sip_flow(data)
    if flow.status == "no_data":
        st.info("기록된 SIP transaction이 없습니다.")
        return

    kpi = flow.kpi
    col1, col2, col3 = st.columns(3)
    col1.metric("SIP transaction", f"{kpi.transaction_count}")
    col2.metric("SIP 오류 응답(4xx~6xx)", f"{kpi.error_count}", delta="이상" if kpi.error_count > 0 else "정상", delta_color="inverse" if kpi.error_count > 0 else "normal")
    col3.metric("통화 설정 지연(Max)", f"{kpi.setup_latency_ms} ms" if kpi.setup_latency_ms is not None else "N/A")

    st.divider()

    message_count = len(flow.messages)

    fig = go.Figure()
    fig.add_shape(type="line", x0=0, y0=0, x1=0, y1=message_count+1, line=dict(color="lightgray", width=2, dash="dash"))
    fig.add_shape(type="line", x0=1, y0=0, x1=1, y1=message_count+1, line=dict(color="lightgray", width=2, dash="dash"))

    for index, message in enumerate(flow.messages):
        y = message_count - index
        color = SIP_KIND_COLORS[message.kind]
        x0, x1 = (0.05, 0.95) if message.is_outgoing else (0.95, 0.05)

        fig.add_annotation(
            x=x1, y=y, ax=x0, ay=y,
            xref="x", yref="y", axref="x", ayref="y",
            text=f"<b>{message.method_code}</b><br><span style='font-size:10px'>{message.cseq}</span>",
            showarrow=True, arrowhead=2, arrowsize=1.5, arrowwidth=2, arrowcolor=color,
            font=dict(color=color, size=13), align="center", yshift=8
        )

        fig.add_annotation(
            x=-0.05, y=y, xref="x", yref="y",
            text=message.time_label, showarrow=False,
            font=dict(size=11, color="gray"), xanchor="right"
        )

    fig.update_layout(
        xaxis=dict(
            tickmode='array', tickvals=[0, 1],
            ticktext=['UE', 'IMS 망'],
            tickfont=dict(size=15, weight='bold'),
            range=[-0.2, 1.2], side="top", showgrid=False, zeroline=False
        ),
        yaxis=dict(showticklabels=False, range=[0, message_count+1], showgrid=False, zeroline=False),
        height=max(400, message_count * 45),
        margin=dict(l=120, r=50, t=80, b=20),
        plot_bgcolor='white', hovermode=False
    )

    st.plotly_chart(fig, width="stretch")

    st.markdown("**SIP 메시지 상세**")
    st.dataframe(flow.table, width="stretch")

def render_rilj_transactions(current_base=None, report_data=None):
    st.subheader("RILJ transaction 현황")

    if not current_base:
        return

    overview = build_rilj_overview(report_data)
    if overview.status == "no_data":
        st.info("RILJ transaction 데이터가 없습니다.")
        return

    kpi = overview.kpi
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("RIL 요청", f"{kpi.request_count}")
    c2.metric("Timeout", f"{kpi.timeout_count}", delta="주의" if kpi.timeout_count else "정상", delta_color="inverse")
    c3.metric("오류 응답", f"{kpi.error_count}", delta="오류" if kpi.error_count else "정상", delta_color="inverse")
    c4.metric("UNSL 이벤트", f"{kpi.unsol_count}", delta="Modem 이벤트" if kpi.unsol_count else "정상")

    st.divider()

    tab_anomaly, tab_unsol = st.tabs(["이상 transaction", "UNSL 이벤트"])

    with tab_anomaly:
        if not overview.abnormal.empty:
            st.dataframe(overview.abnormal, width="stretch", hide_index=True)
        else:
            st.success(f"Timeout, 오류 응답, {overview.slow_threshold_ms}ms 초과 지연이 감지되지 않았습니다.")

    with tab_unsol:
        if not overview.unsol.empty:
            st.markdown(f"**Modem 상태 업데이트 이력(총 {kpi.unsol_count}건)**")
            st.dataframe(overview.unsol, width="stretch", hide_index=True)
        else:
            st.info("수집된 UNSL 이벤트 로그가 없습니다.")

def render_integrated_rf_call_timeline(report_data):
    st.subheader("통화 상태 및 RF 환경 통합 타임라인")
    st.markdown("통화 구간, RSRP 변화, SIP 오류 시점을 함께 표시합니다.")

    timeline = build_rf_call_timeline(report_data)
    if timeline.status == "no_signal_history":
        st.info("통합 타임라인을 구성할 RF 신호 이력이 부족합니다.")
        return

    fig = go.Figure()

    if timeline.rsrp_points:
        fig.add_trace(go.Scatter(
            x=[p.time_dt for p in timeline.rsrp_points],
            y=[p.rsrp_dbm for p in timeline.rsrp_points],
            mode='lines+markers',
            name='RSRP(dBm)',
            line=dict(color='#1f77b4', width=2.5),
            marker=dict(size=6, symbol='circle'),
            text=[p.hover_text for p in timeline.rsrp_points],
            hoverinfo='text'
        ))

    for span in timeline.call_spans:
        fig.add_vrect(
            x0=span.start_dt, x1=span.end_dt,
            fillcolor="rgba(255, 0, 0, 0.12)" if span.is_drop else "rgba(0, 255, 0, 0.12)",
            opacity=1,
            layer="below", line_width=1.5,
            line_color="rgba(255,0,0,0.4)" if span.is_drop else "rgba(0,255,0,0.4)",
            annotation_text=span.label, annotation_position="top left",
            annotation_font=dict(size=11, color="red" if span.is_drop else "green")
        )

    if timeline.sip_errors:
        fig.add_trace(go.Scatter(
            x=[e.time_dt for e in timeline.sip_errors],
            y=[-135] * len(timeline.sip_errors),  # sits under the RSRP band
            mode='markers+text',
            name='SIP 오류(4xx~6xx)',
            marker=dict(symbol='x', color='#d32f2f', size=11, line=dict(width=2)),
            text=[e.label for e in timeline.sip_errors],
            textposition="top center",
            textfont=dict(color='#d32f2f', size=10, weight='bold')
        ))

    fig.update_layout(
        yaxis_title="수신 신호 세기(RSRP dBm)",
        yaxis=dict(
            range=[-145, -45],
            tickmode='linear',
            dtick=10,
            showgrid=True,
            gridcolor='rgba(128,128,128,0.15)'
        ),
        xaxis=dict(
            showgrid=True,
            gridcolor='rgba(128,128,128,0.15)',
            tickformat="%m-%d\n%H:%M:%S"
        ),
        height=480,
        hovermode="x unified",
        plot_bgcolor='white',
        margin=dict(l=50, r=20, t=40, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )

    st.plotly_chart(fig, width="stretch")

def render_nitz_timeline(nitz_data):
    timeline = build_nitz_timeline(nitz_data)
    if timeline.status == "no_data":
        st.info("NITZ 수신 이력이 없습니다.")
        return

    st.markdown("### NITZ 타임존 변동")

    kpi = timeline.kpi
    is_unstable = kpi.stability == "unstable"

    col1, col2, col3 = st.columns(3)
    with col1: st.metric("최초 타임존", kpi.first_timezone)
    with col2: st.metric("최종 타임존", kpi.last_timezone, delta="타임존 변경됨" if kpi.change_count > 0 else "유지됨")
    with col3:
        st.metric(
            "타임존 변경 횟수", f"{kpi.change_count} 회",
            delta=NITZ_STABILITY_LABELS[kpi.stability],
            delta_color="inverse" if is_unstable else "normal"
        )

    st.divider()

    col_chart, col_map = st.columns([1, 1])

    with col_chart:
        st.markdown("**UTC 오프셋 변화 타임라인**")
        fig_line = px.line(timeline.offsets_frame(), x='log_time_dt', y='offset_num', line_shape='hv', markers=True,
                           labels={'log_time_dt': '시간', 'offset_num': 'UTC 오프셋 (+/-)'})
        fig_line.update_traces(line_color='#2ca02c')
        fig_line.update_layout(height=350, margin=dict(t=30, b=20, l=10, r=10))
        st.plotly_chart(fig_line, width="stretch")

    with col_map:
        st.markdown("**타임존 기반 예상 지역**")
        geo_df = timeline.geo_frame()
        if not geo_df.empty:
            fig_map = px.scatter_geo(
                geo_df,
                lat='lat', lon='lon',
                size='count',          # 오래 머문 지역일수록 원이 크게 표시됨
                color='offset',        # 시간대별로 색상 구분
                hover_name='region',
                hover_data={'lat': False, 'lon': False, 'count': True},
                projection="natural earth" # 부드러운 세계 지도 투영법
            )
            # 지도 스타일링 (바다색, 육지색 지정)
            fig_map.update_geos(
                showcountries=True, countrycolor="lightgray",
                showcoastlines=True, coastlinecolor="gray",
                showland=True, landcolor="#f4f4f4",
                showocean=True, oceancolor="#e0f3f8"
            )
            fig_map.update_layout(height=350, margin=dict(t=0, b=0, l=0, r=0))
            st.plotly_chart(fig_map, width="stretch")
        else:
            st.info("지도에 표시할 좌표 데이터가 없습니다.")

    if not timeline.changes.empty:
        with st.expander("상세 타임존 변경 이력"):
            st.dataframe(timeline.changes, hide_index=True, width="stretch")
