"""Network charts.

Rendering only: every series, KPI and table on this page is shaped by
`core.charts.network`, so the code here is limited to plotly styling, the
Streamlit layout and the two widgets the user drives (metric picker, stall
window picker).
"""

import plotly.express as px
import streamlit as st

from core.charts import (
    INTERNET_STALL_LAYER_TABS,
    build_data_usage_profile,
    build_dns_error_breakdown,
    build_dns_health_warnings,
    build_dns_issue_summary,
    build_internet_stall_report,
    build_network_timeline_stats,
)

RAT_COLORS = {'LTE': '#1f77b4', '5G (NR)': '#ff7f0e', 'Unknown': '#7f7f7f'}

USAGE_TIMELINE_LABELS = {'time_dt': '시간', 'total_mb': '사용량(MB)', 'app_name': '앱'}

def render_dns_analysis_chart(df):
    st.subheader("DNS 오류 현황")

    breakdown = build_dns_error_breakdown(df)
    if breakdown.status == "unavailable":
        st.warning("DNS 데이터 필드가 누락되었습니다.")
        return
    if breakdown.status == "no_errors":
        st.success("DNS Fail/Block 기록이 존재하지 않습니다. (정상)")
        return

    fig_dns_corr = px.bar(
        breakdown.counts, x='app_name', y='count', color='return_code',
        title="패키지별 DNS 오류 분포",
        labels={'app_name': 'Package Name', 'count': 'Frequency', 'return_code': 'Error Code'},
        barmode='stack', color_discrete_sequence=px.colors.qualitative.Pastel
    )
    fig_dns_corr.update_layout(xaxis_tickangle=-45, height=500)

    c1, c2 = st.columns([2, 1])
    with c1:
        st.plotly_chart(fig_dns_corr, width="stretch")
    with c2:
        st.markdown("**오류 코드별 건수**")
        st.dataframe(breakdown.pivot, width="stretch")

def _render_dns_health_warnings(df):
    warnings = build_dns_health_warnings(df)
    if not warnings:
        return

    for warning in warnings:
        st.error(
            f"🚨 **[Critical] DNS 서버 라우팅 장애 감지**\n\n"
            f"**NetId {warning.net_id}**의 DNS 서버(`{warning.server_ip}`)가 응답하지 않습니다.\n"
            f"- 상태 점수: **{warning.score}점**\n"
            f"- 타임아웃 발생: **{warning.timeout_count}회**\n\n"
            f"💡 {warning.description}"
        )
    st.divider() # 경고 박스 아래에 구분선 추가

def _render_dns_issues(df):
    summary = build_dns_issue_summary(df)
    if summary.status == "no_data":
        st.info("DNS Issue 데이터가 존재하지 않습니다.")
        return

    col_dns1, col_dns2 = st.columns(2)
    with col_dns1:
        st.markdown("**DNS 실패 및 차단 사유**")
        fig_dns = px.pie({'suspected_reason': summary.reasons}, names='suspected_reason', hole=0.4)
        st.plotly_chart(fig_dns, width="stretch")
    with col_dns2:
        st.markdown("**패키지별 DNS 이슈**")
        fig_pkg = px.bar(summary.package_counts, x='count', y='package', orientation='h')
        st.plotly_chart(fig_pkg, width="stretch")

    st.markdown("**DNS 상세 내역**")
    st.dataframe(summary.table, width="stretch", hide_index=True)

def _render_network_timeline_stats(df):
    stats = build_network_timeline_stats(df)
    if stats.status == "no_data":
        st.info("Network Timeline Stat 데이터가 존재하지 않습니다.")
        return
    if stats.status == "unparsable_time":
        st.warning("시간 포맷 변환 오류로 시계열 렌더링에 실패했습니다.")
        return

    metrics = {metric.label: metric for metric in stats.metrics}
    metric_choice = st.selectbox("지표 선택", list(metrics.keys()))
    selected = metrics[metric_choice]

    fig_ts = px.line(
        stats.frame, x='time_dt', y=selected.column, color='netId', hover_data=['transport'],
        markers=True, title=f"{metric_choice} 추이"
    )
    fig_ts.update_xaxes(tickformat="%m-%d\n%H:%M:%S", title="Time")
    fig_ts.update_layout(yaxis_title=selected.unit)
    st.plotly_chart(fig_ts, width="stretch")

    st.markdown("**DNS Spike 구간 (고지연 DNS 탐지)**")
    if not stats.spikes.empty:
        st.dataframe(stats.spikes, width="stretch", hide_index=True)
    else:
        st.success("고지연 DNS Spike 구간이 없습니다.")

def render_network_timeseries_and_dns(df):
    st.subheader("DNS 및 네트워크 추이")

    _render_dns_health_warnings(df)
    _render_dns_issues(df)
    _render_network_timeline_stats(df)

def render_data_usage_profiling(df):
    """셀룰러 데이터 사용량 프로파일링 차트 렌더링"""
    st.subheader("셀룰러 데이터 사용 현황")

    profile = build_data_usage_profile(df)
    if profile.status == "unavailable":
        return
    if profile.status == "no_data":
        st.info("Netstats 데이터가 존재하지 않습니다.")
        return

    col_du1, col_du2 = st.columns(2)
    with col_du1:
        fig_app = px.pie(profile.app_totals, values='total_mb', names='app_name', hole=0.4,
                         title='앱별 누적 데이터 사용량 Top 10 (MB)')
        fig_app.update_traces(textposition='inside', textinfo='percent+label')
        st.plotly_chart(fig_app, width="stretch")

    with col_du2:
        fig_rat = px.pie(
            profile.rat_totals, values='total_mb', names='rat', title='RAT별 데이터 사용 비율', color='rat',
            color_discrete_map=RAT_COLORS
        )
        fig_rat.update_traces(textposition='inside', textinfo='percent+label')
        st.plotly_chart(fig_rat, width="stretch")

    if profile.timeline_status == "absent":
        return

    st.divider()
    st.markdown("##### 앱별 데이터 사용 추이")

    if profile.timeline_status == "empty":
        st.info("데이터 사용량 시계열을 구성할 수 없습니다.")
        return

    fig_time = px.bar(
        profile.timeline,
        x='time_dt',
        y='total_mb',
        color='app_name',
        labels=USAGE_TIMELINE_LABELS,
        barmode='stack'
    )
    fig_time.update_layout(
        plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.2)'),
        yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.2)'),
        legend=dict(orientation="h", yanchor="bottom", y=-0.4, xanchor="center", x=0.5)
    )
    fig_time.update_traces(marker_line_width=0)
    st.plotly_chart(fig_time, width="stretch")

def _render_stall_kpi(kpi):
    st.markdown("### 1) 요약")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stall 구간", kpi.stall_window_count)
    c2.metric("고위험 구간", kpi.high_risk_window_count)
    c3.metric("주요 후보", kpi.primary_root_cause_candidate)
    c4.metric("이벤트 수", kpi.total_timeline_events)

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("DNS 이슈", kpi.dns_issue_count)
    c6.metric("검증 실패", kpi.validation_fail_count)
    c7.metric("Data Stall", kpi.data_stall_count)
    c8.metric("RF 경고", kpi.rf_warning_count)

    c9, c10, c11 = st.columns(3)
    c9.metric("DataCall 실패", kpi.data_call_fail_or_drop_count)
    c10.metric("TCP/TLS Timeout", kpi.tcp_tls_timeout_count)
    c11.metric("전원/Idle Hint", kpi.power_idle_hint_count)

def _render_stall_root_causes(root_causes):
    st.markdown("### 2) 원인 후보")

    if root_causes.empty:
        st.info("도출된 원인 후보가 없습니다.")
        return

    st.dataframe(root_causes, width="stretch")

    fig_root = px.bar(
        root_causes,
        x="category",
        y="count",
        title="원인 후보 분포",
        hover_data=["high", "medium", "low", "example_time", "example_trigger"]
    )
    st.plotly_chart(fig_root, width="stretch")

def _render_stall_timeline(report):
    st.markdown("### 3) 계층별 이벤트 타임라인")

    if report.timeline_status == "empty":
        st.info("표시할 타임라인 이벤트가 없습니다.")
        return
    if report.timeline_status == "unparsable_time":
        st.warning("인터넷 품질 타임라인을 구성할 수 없습니다.")
        return

    fig = px.scatter(
        report.timeline,
        x="time_dt",
        y="layer",
        color="severity",
        symbol="event_type",
        hover_data=report.timeline_hover_columns,
        title="네트워크 계층별 관련 이벤트"
    )
    fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")
    st.plotly_chart(fig, width="stretch")

    with st.expander("상세 이벤트 테이블", expanded=False):
        st.dataframe(report.timeline_table, width="stretch")

def _render_stall_windows(report):
    st.markdown("### 4) 고위험 구간")

    windows = report.windows
    if not windows:
        st.info("식별된 Stall 구간이 없습니다.")
        return

    window_df = report.windows_frame()
    st.dataframe(window_df, width="stretch")

    selected_idx = st.selectbox(
        "구간 상세 보기",
        window_df["idx"].tolist(),
        format_func=lambda i: f"#{i} | {windows[i].center_time} | {windows[i].trigger}"
    )

    selected = windows[selected_idx]
    st.markdown("**원인 후보**")
    st.json(selected.root_cause_candidates)

    if not selected.related_table.empty:
        st.dataframe(selected.related_table, width="stretch")

        with st.expander("Trigger 주변 원본 로그", expanded=False):
            for event in selected.related_events[:20]:
                st.markdown(f"**[{event.get('time')}] {event.get('layer')} / {event.get('event_type')}**")
                st.code(event.get("raw", ""), language="log")
                ctx = event.get("context_before", [])
                if ctx:
                    st.caption("직전 로그")
                    st.code("\n".join(ctx[-10:]), language="log")

def _render_stall_layers(report):
    st.markdown("### 5) 계층별 상세")

    if report.timeline_status != "ok":
        return

    tabs = st.tabs([layer_tab.title for layer_tab in INTERNET_STALL_LAYER_TABS])

    for tab, layer_tab in zip(tabs, INTERNET_STALL_LAYER_TABS):
        with tab:
            view = report.layer_view(layer_tab.layers)
            if view.status == "empty":
                st.info(f"{list(layer_tab.layers)} 계층 이벤트가 없습니다.")
                continue

            fig = px.bar(view.counts, x="event_type", y="count",
                         title=f"이벤트 분포: {'/'.join(layer_tab.layers)}")
            st.plotly_chart(fig, width="stretch")
            st.dataframe(view.table, width="stretch")

def render_internet_stall_analyzer(current_base, data=None):
    st.subheader("인터넷 연결 품질 분석")

    if not current_base:
        st.info("분석 대상 파일을 선택해 주세요.")
        return

    report = build_internet_stall_report(data)
    if report.status == "no_data":
        st.info("인터넷 품질 분석 결과가 없습니다.")
        return

    _render_stall_kpi(report.kpi)
    st.divider()
    _render_stall_root_causes(report.root_causes)
    st.divider()
    _render_stall_timeline(report)
    st.divider()
    _render_stall_windows(report)
    st.divider()
    _render_stall_layers(report)
