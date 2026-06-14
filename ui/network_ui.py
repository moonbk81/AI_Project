import os
import json
import datetime
import pandas as pd
import plotly.express as px
import streamlit as st
from .common import _load_json, _safe_time_series

def render_dns_analysis_chart(df):
    st.subheader("DNS 오류 현황")

    dns_df = df[df['log_type'] == 'DNS_Query'].copy()

    if not dns_df.empty and 'return_code' in dns_df.columns and 'app_name' in dns_df.columns:
        error_dns_df = dns_df[~dns_df['return_code'].isin(['0', 'SUCCESS'])]

        if not error_dns_df.empty:
            dns_corr = error_dns_df.groupby(['app_name', 'return_code']).size().reset_index(name='count')
            fig_dns_corr = px.bar(
                dns_corr, x='app_name', y='count', color='return_code',
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
                pivot_df = error_dns_df.pivot_table(index='app_name', columns='return_code', aggfunc='size', fill_value=0)
                st.dataframe(pivot_df, width="stretch")
        else:
            st.success("DNS Fail/Block 기록이 존재하지 않습니다. (정상)")
    else:
        st.warning("DNS 데이터 필드가 누락되었습니다.")

def render_network_timeseries_and_dns(df):
    st.subheader("DNS 및 네트워크 추이")

    if 'log_type' in df.columns:
        dns_df = df[df['log_type'] == 'Network_DNS_Issue'].copy()
        if not dns_df.empty:
            col_dns1, col_dns2 = st.columns(2)
            with col_dns1:
                st.markdown("**DNS 실패 및 차단 사유**")
                fig_dns = px.pie(dns_df, names='suspected_reason', hole=0.4)
                st.plotly_chart(fig_dns, width="stretch")
            with col_dns2:
                st.markdown("**패키지별 DNS 이슈**")
                pkg_counts = dns_df['package'].value_counts().reset_index()
                pkg_counts.columns = ['package', 'count']
                fig_pkg = px.bar(pkg_counts, x='count', y='package', orientation='h')
                st.plotly_chart(fig_pkg, width="stretch")

            st.markdown("**DNS 상세 내역**")

            display_cols = ['time', 'net_id', 'package', 'result', 'suspected_reason']
            exist_cols = [c for c in display_cols if c in dns_df.columns]
            detail_df = dns_df[exist_cols].copy()

            col_rename_map = {
                'time': 'Time',
                'net_id': 'NetID',
                'package': 'Package',
                'result': 'Result/Error Code',
                'suspected_reason': 'Suspected Reason'
            }
            detail_df.rename(columns=col_rename_map, inplace=True)
            st.dataframe(detail_df, width="stretch", hide_index=True)

        else:
            st.info("DNS Issue 데이터가 존재하지 않습니다.")

        ts_df = df[df['log_type'] == 'Network_Timeline_Stat'].copy()
        if not ts_df.empty:
            ts_df['dns_avg'] = pd.to_numeric(ts_df['dns_avg'], errors='coerce')
            ts_df['dns_err_rate'] = pd.to_numeric(ts_df['dns_err_rate'], errors='coerce')

            if 'dns_max' in ts_df.columns:
                ts_df['dns_max'] = pd.to_numeric(ts_df['dns_max'], errors='coerce')

            if 'dns_delayed_cnt' in ts_df.columns:
                ts_df['dns_delayed_cnt'] = pd.to_numeric(ts_df['dns_delayed_cnt'], errors='coerce')

            if 'dns_blocked_cnt' in ts_df.columns:
                ts_df['dns_blocked_cnt'] = pd.to_numeric(ts_df['dns_blocked_cnt'], errors='coerce')

            def safe_parse_time(t):
                t_str = str(t).strip()
                if len(t_str) > 5 and t_str[2] == '-' and t_str.count('-') == 1:
                    current_year = datetime.datetime.now().year
                    t_str = f"{current_year}-{t_str}"
                return pd.to_datetime(t_str, errors='coerce')

            ts_df['time_dt'] = ts_df['time'].apply(safe_parse_time)
            ts_df = ts_df.dropna(subset=['time_dt']).sort_values(by='time_dt')

            if ts_df.empty:
                st.warning("시간 포맷 변환 오류로 시계열 렌더링에 실패했습니다.")
                return

            ts_df['netId'] = ts_df['netId'].astype(str)

            metric_choice = st.selectbox("지표 선택", ["DNS 평균 응답 시간(ms)", "DNS 오류율(%)"])
            target_col = 'dns_avg' if "응답 시간" in metric_choice else 'dns_err_rate'

            fig_ts = px.line(
                ts_df, x='time_dt', y=target_col, color='netId', hover_data=['transport'],
                markers=True, title=f"{metric_choice} 추이"
            )
            fig_ts.update_xaxes(tickformat="%m-%d\n%H:%M:%S", title="Time")
            fig_ts.update_layout(yaxis_title="Value")
            st.plotly_chart(fig_ts, width="stretch")

            st.markdown("**DNS Spike 구간 (고지연 DNS 탐지)**")

            spike_df = ts_df[
                (ts_df['dns_avg'] >= 1000) |
                (pd.to_numeric(ts_df.get('dns_delayed_cnt', 0), errors='coerce').fillna(0) > 0)
            ].copy()

            if not spike_df.empty:
                display_cols = [
                    c for c in [
                        'time',
                        'netId',
                        'transport',
                        'dns_avg',
                        'dns_max',
                        'dns_err_rate',
                        'dns_delayed_cnt',
                        'dns_blocked_cnt'
                    ]
                    if c in spike_df.columns
                ]

                st.dataframe(
                    spike_df[display_cols]
                    .sort_values('dns_avg', ascending=False),
                    width="stretch",
                    hide_index=True
                )
            else:
                st.success("고지연 DNS Spike 구간이 없습니다.")
        else:
            st.info("Network Timeline Stat 데이터가 존재하지 않습니다.")

def render_data_usage_profiling(df):
    """셀룰러 데이터 사용량 프로파일링 차트 렌더링"""
    st.subheader("셀룰러 데이터 사용 현황")

    if 'log_type' in df.columns:
        du_df = df[df['log_type'] == 'Data_Usage'].copy()

        if not du_df.empty:
            du_df['total_mb'] = pd.to_numeric(du_df['total_mb'], errors='coerce')

            col_du1, col_du2 = st.columns(2)
            with col_du1:
                app_df = du_df.groupby('app_name')['total_mb'].sum().reset_index().sort_values(by='total_mb', ascending=False).head(10)
                fig_app = px.pie(app_df, values='total_mb', names='app_name', hole=0.4, title='앱별 누적 데이터 사용량 Top 10 (MB)')
                fig_app.update_traces(textposition='inside', textinfo='percent+label')
                st.plotly_chart(fig_app, width="stretch")

            with col_du2:
                rat_df = du_df.groupby('rat')['total_mb'].sum().reset_index()
                fig_rat = px.pie(
                    rat_df, values='total_mb', names='rat', title='RAT별 데이터 사용 비율', color='rat',
                    color_discrete_map={'LTE':'#1f77b4', '5G (NR)':'#ff7f0e', 'Unknown':'#7f7f7f'}
                )
                fig_rat.update_traces(textposition='inside', textinfo='percent+label')
                st.plotly_chart(fig_rat, width="stretch")

            if 'time' in du_df.columns:
                st.divider()
                st.markdown("##### 앱별 데이터 사용 추이")

                du_df['time_dt'] = pd.to_datetime(du_df['time'], errors='coerce')
                time_df = du_df.dropna(subset=['time_dt']).sort_values('time_dt')

                if not time_df.empty:
                    fig_time = px.bar(
                        time_df,
                        x='time_dt',
                        y='total_mb',
                        color='app_name',
                        labels={'time_dt': '시간', 'total_mb': '사용량(MB)', 'app_name': '앱'},
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
                else:
                    st.info("데이터 사용량 시계열을 구성할 수 없습니다.")
        else:
            st.info("Netstats 데이터가 존재하지 않습니다.")

def render_data_usage_timeline(df):
    """시간대별 앱 데이터 사용 추이를 시각화합니다."""
    data_df = df[df['log_type'] == 'Data_Usage'].copy()
    if data_df.empty:
        return

    data_df['time_dt'] = pd.to_datetime(data_df['time'], errors='coerce')
    data_df = data_df.dropna(subset=['time_dt']).sort_values('time_dt')
    data_df['total_mb'] = pd.to_numeric(data_df['total_mb'], errors='coerce').fillna(0)

    st.markdown("##### 데이터 사용 타임라인")

    fig = px.bar(
        data_df,
        x='time_dt',
        y='total_mb',
        color='app_name',
        title="시간대별 앱 데이터 사용량(MB)",
        labels={'time_dt': '시간', 'total_mb': '사용량(MB)', 'app_name': '앱'},
        barmode='stack'
    )

    fig.update_layout(
        plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.2)'),
        yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.2)')
    )

    st.plotly_chart(fig, width="stretch")

def render_internet_stall_analyzer(current_base, result_dir="./result"):
    st.subheader("인터넷 연결 품질 분석")

    if not current_base:
        st.info("분석 대상 파일을 선택해 주세요.")
        return

    path = os.path.join(result_dir, f"{current_base}_internet_stall.json")
    data = _load_json(path, {})

    if not data:
        st.info(f"인터넷 품질 분석 결과가 없습니다. 확인 경로: `{path}`")
        return

    kpi = data.get("kpi", {}) or {}
    root_summary = data.get("root_cause_summary", {}) or {}
    windows = data.get("stall_windows", []) or []
    timeline = data.get("timeline", []) or []

    st.markdown("### 1) 요약")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stall 구간", kpi.get("stall_window_count", 0))
    c2.metric("고위험 구간", kpi.get("high_risk_window_count", 0))
    c3.metric("주요 후보", kpi.get("primary_root_cause_candidate", "UNKNOWN"))
    c4.metric("이벤트 수", kpi.get("total_timeline_events", 0))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("DNS 이슈", kpi.get("dns_issue_count", 0))
    c6.metric("검증 실패", kpi.get("validation_fail_count", 0))
    c7.metric("Data Stall", kpi.get("data_stall_count", 0))
    c8.metric("RF 경고", kpi.get("rf_warning_count", 0))

    c9, c10, c11 = st.columns(3)
    c9.metric("DataCall 실패", kpi.get("data_call_fail_or_drop_count", 0))
    c10.metric("TCP/TLS Timeout", kpi.get("tcp_tls_timeout_count", 0))
    c11.metric("전원/Idle Hint", kpi.get("power_idle_hint_count", 0))

    st.divider()

    st.markdown("### 2) 원인 후보")

    if root_summary:
        root_rows = []
        for category, info in root_summary.items():
            confidence = info.get("confidence", {}) or {}
            examples = info.get("examples", []) or []
            root_rows.append({
                "category": category,
                "count": info.get("count", 0),
                "high": confidence.get("high", 0),
                "medium": confidence.get("medium", 0),
                "low": confidence.get("low", 0),
                "example_time": examples[0].get("time") if examples else "-",
                "example_trigger": examples[0].get("trigger") if examples else "-"
            })

        root_df = pd.DataFrame(root_rows)
        st.dataframe(root_df, width="stretch")

        fig_root = px.bar(
            root_df,
            x="category",
            y="count",
            title="원인 후보 분포",
            hover_data=["high", "medium", "low", "example_time", "example_trigger"]
        )
        st.plotly_chart(fig_root, width="stretch")
    else:
        st.info("도출된 원인 후보가 없습니다.")

    st.divider()

    st.markdown("### 3) 계층별 이벤트 타임라인")

    timeline_df = pd.DataFrame(timeline)
    if not timeline_df.empty:
        timeline_df = _safe_time_series(timeline_df, "time")
        if not timeline_df.empty:
            if "severity" not in timeline_df.columns:
                timeline_df["severity"] = "info"
            if "layer" not in timeline_df.columns:
                timeline_df["layer"] = "UNKNOWN"

            fig = px.scatter(
                timeline_df,
                x="time_dt",
                y="layer",
                color="severity",
                symbol="event_type",
                hover_data=[col for col in ["time", "event_type", "reason", "net_id", "apn", "cid"] if col in timeline_df.columns],
                title="네트워크 계층별 관련 이벤트"
            )
            fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")
            st.plotly_chart(fig, width="stretch")

            with st.expander("상세 이벤트 테이블", expanded=False):
                display_cols = [c for c in ["time", "layer", "event_type", "severity", "reason", "net_id", "apn", "cid", "raw"] if c in timeline_df.columns]
                st.dataframe(timeline_df[display_cols], width="stretch")
        else:
            st.warning("인터넷 품질 타임라인을 구성할 수 없습니다.")
    else:
        st.info("표시할 타임라인 이벤트가 없습니다.")

    st.divider()

    st.markdown("### 4) 고위험 구간")

    if windows:
        window_rows = []
        for idx, w in enumerate(windows):
            candidates = w.get("root_cause_candidates", []) or []
            primary = candidates[0] if candidates else {}
            window_rows.append({
                "idx": idx,
                "center_time": w.get("center_time"),
                "trigger": w.get("trigger"),
                "severity_score": w.get("severity_score"),
                "primary_category": primary.get("category", "UNKNOWN"),
                "confidence": primary.get("confidence", "unknown"),
                "layer_counts": json.dumps(w.get("layer_counts", {}), ensure_ascii=False)
            })

        window_df = pd.DataFrame(window_rows).sort_values("severity_score", ascending=False)
        st.dataframe(window_df, width="stretch")

        selected_idx = st.selectbox(
            "구간 상세 보기",
            window_df["idx"].tolist(),
            format_func=lambda i: f"#{i} | {windows[i].get('center_time')} | {windows[i].get('trigger')}"
        )

        selected = windows[selected_idx]
        st.markdown("**원인 후보**")
        st.json(selected.get("root_cause_candidates", []))

        related = selected.get("related_events", []) or []
        related_df = pd.DataFrame(related)
        if not related_df.empty:
            related_df = _safe_time_series(related_df, "time")
            display_cols = [c for c in ["time", "layer", "event_type", "severity", "reason", "apn", "cid", "raw"] if c in related_df.columns]
            st.dataframe(related_df[display_cols], width="stretch")

            with st.expander("Trigger 주변 원본 로그", expanded=False):
                for e in related[:20]:
                    st.markdown(f"**[{e.get('time')}] {e.get('layer')} / {e.get('event_type')}**")
                    st.code(e.get("raw", ""), language="log")
                    ctx = e.get("context_before", [])
                    if ctx:
                        st.caption("직전 로그")
                        st.code("\n".join(ctx[-10:]), language="log")
    else:
        st.info("식별된 Stall 구간이 없습니다.")

    st.divider()

    st.markdown("### 5) 계층별 상세")

    if timeline_df.empty:
        return

    tab_dns, tab_datacall, tab_validation, tab_rf, tab_tcp, tab_power = st.tabs(
        ["DNS", "DataCall/Stall", "Validation", "RF", "TCP/TLS", "전원"]
    )

    def render_layer(tab, layer_names):
        with tab:
            layer_df = timeline_df[timeline_df["layer"].isin(layer_names)].copy()
            if layer_df.empty:
                st.info(f"{layer_names} 계층 이벤트가 없습니다.")
                return

            count_df = layer_df["event_type"].value_counts().reset_index()
            count_df.columns = ["event_type", "count"]

            fig = px.bar(count_df, x="event_type", y="count", title=f"이벤트 분포: {'/'.join(layer_names)}")
            st.plotly_chart(fig, width="stretch")

            display_cols = [c for c in ["time", "event_type", "severity", "reason", "net_id", "apn", "cid", "raw"] if c in layer_df.columns]
            st.dataframe(layer_df[display_cols], width="stretch")

    render_layer(tab_dns, ["DNS"])
    render_layer(tab_datacall, ["DATA_CALL", "DATA_STALL"])
    render_layer(tab_validation, ["VALIDATION", "NETWORK", "ROUTING"])
    render_layer(tab_rf, ["RF"])
    render_layer(tab_tcp, ["TCP_TLS"])
    render_layer(tab_power, ["POWER"])