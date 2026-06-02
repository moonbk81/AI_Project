# ui_components.py
import os, json
import streamlit as st
import plotly.express as px
import pandas as pd
import plotly.graph_objects as go
import datetime

import json
import ast
import re

def parse_raw_logs(raw_data):
    """
    JSON, Python List, Text 등 다양한 포맷의 로그 데이터를 파싱하여 리스트로 반환합니다.
    """
    if isinstance(raw_data, list):
        raw_logs = raw_data
    elif isinstance(raw_data, str):
        raw_data_clean = raw_data.strip()
        try:
            raw_logs = json.loads(raw_data_clean)
            if not isinstance(raw_logs, list):
                raw_logs = [raw_data_clean]
        except Exception:
            try:
                raw_logs = ast.literal_eval(raw_data_clean)
                if not isinstance(raw_logs, list):
                    raw_logs = [raw_data_clean]
            except Exception:
                if raw_data_clean.startswith('[') and raw_data_clean.endswith(']'):
                    inner_text = raw_data_clean[1:-1]
                    if '", "' in inner_text:
                        raw_logs = inner_text.split('", "')
                    elif "', '" in inner_text:
                        raw_logs = inner_text.split("', '")
                    else:
                        raw_logs = [inner_text]
                    raw_logs = [log.strip(' "\'') for log in raw_logs]
                else:
                    clean_text = raw_data_clean.replace('\\n', '\n').replace('\\r', '')
                    raw_logs = clean_text.split('\n')
    else:
        raw_logs = []

    return [log for log in raw_logs if str(log).strip()]

def render_dns_analysis_chart(df):
    st.subheader("패키지 기반 DNS 세션 에러 매트릭스")

    dns_df = df[df['log_type'] == 'DNS_Query'].copy()

    if not dns_df.empty and 'return_code' in dns_df.columns and 'app_name' in dns_df.columns:
        error_dns_df = dns_df[~dns_df['return_code'].isin(['0', 'SUCCESS'])]

        if not error_dns_df.empty:
            dns_corr = error_dns_df.groupby(['app_name', 'return_code']).size().reset_index(name='count')
            fig_dns_corr = px.bar(
                dns_corr, x='app_name', y='count', color='return_code',
                title="DNS Failure Distribution by Package",
                labels={'app_name': 'Package Name', 'count': 'Frequency', 'return_code': 'Error Code'},
                barmode='stack', color_discrete_sequence=px.colors.qualitative.Pastel
            )
            fig_dns_corr.update_layout(xaxis_tickangle=-45, height=500)

            c1, c2 = st.columns([2, 1])
            with c1:
                st.plotly_chart(fig_dns_corr, width="stretch")
            with c2:
                st.markdown("**Error Matrix**")
                pivot_df = error_dns_df.pivot_table(index='app_name', columns='return_code', aggfunc='size', fill_value=0)
                st.dataframe(pivot_df, width="stretch")
        else:
            st.success("DNS Fail/Block 기록이 존재하지 않습니다. (정상)")
    else:
        st.warning("DNS 데이터 필드가 누락되었습니다.")

def render_battery_thermal_chart(df):
    st.subheader("Thermal & Power Consumption Analysis")

    thermal_df = df[df['log_type'] == 'Thermal_Stat'].copy()
    wl_df = df[df['log_type'] == 'Wakelock_Stat'].copy()
    cpu_df = df[df['log_type'] == 'Cpu_Usage_Stat'].copy()

    c1, c2, c3 = st.columns(3)

    common_height = 420
    common_margin = dict(l=10, r=10, t=30, b=130)

    with c1:
        st.markdown("**Wakelock Activation**")
        if not wl_df.empty:
            wl_df['times'] = pd.to_numeric(wl_df['times'], errors='coerce')
            fig_wl = px.bar(
                wl_df.head(10), x='app_name', y='times',
                labels={'app_name': 'Package', 'times': 'Count'},
                color='times', color_continuous_scale='Blues'
            )
            fig_wl.update_layout(xaxis_tickangle=-45, height=common_height, margin=common_margin, coloraxis_showscale=False)
            st.plotly_chart(fig_wl, use_container_width=True)
        else:
            st.info("Wakelock data not found")

    with c2:
        st.markdown("**Thermal Sensor Status**")
        if not thermal_df.empty:
            thermal_df['temperature'] = pd.to_numeric(thermal_df['temperature'], errors='coerce')
            thermal_df = thermal_df.dropna(subset=['temperature']).sort_values(by='temperature', ascending=False)
            fig_th = px.bar(
                thermal_df.head(10), x='sensor', y='temperature',
                color='temperature', color_continuous_scale=[(0, "green"), (0.5, "orange"), (1, "red")],
                range_color=[30, 50], labels={'sensor': 'Sensor', 'temperature': 'Temp(°C)'}
            )
            fig_th.add_hline(y=40, line_dash="dot", line_color="red", annotation_text="Warning Threshold (40°C)")
            fig_th.update_layout(xaxis_tickangle=-45, height=common_height, margin=common_margin, coloraxis_showscale=False)
            st.plotly_chart(fig_th, use_container_width=True)
        else:
            st.info("Thermal data not found")

    with c3:
        st.markdown("**Top 10 CPU Usage (%)**")
        if not cpu_df.empty:
            cpu_df['cpu_percent'] = pd.to_numeric(cpu_df['cpu_percent'], errors='coerce')
            cpu_df['process_label'] = cpu_df['process'].apply(lambda x: x[:18] + '...' if isinstance(x, str) and len(x) > 18 else x)

            fig_cpu = px.bar(
                cpu_df.head(10), x='process_label', y='cpu_percent',
                labels={'process_label': 'Process', 'cpu_percent': 'Usage(%)'},
                color='cpu_percent', color_continuous_scale='Reds',
                hover_data={'process': True}
            )
            fig_cpu.update_layout(xaxis_tickangle=-45, height=common_height, margin=common_margin, coloraxis_showscale=False)
            st.plotly_chart(fig_cpu, use_container_width=True)
        else:
            st.info("CPU usage data not found")

def render_call_history_summary(df):
    """전체 통화 세션 (Call History) 차트 및 표 렌더링"""
    st.subheader("Call Session History Summary")
    if 'log_type' in df.columns:
        call_df = df[df['log_type'] == 'Call_Session']
        if not call_df.empty:
            display_cols = [col for col in ['time', 'slot', 'status', 'fail_reason', 'call_id', 'source_file'] if col in call_df.columns]
            clean_call_df = call_df[display_cols].fillna("-").sort_values(by='time', ascending=False)

            col_chart, col_table = st.columns([1, 2])
            with col_chart:
                st.markdown("**Call Status Distribution**")
                if 'status' in call_df.columns:
                    fig_call = px.pie(call_df, names='status', hole=0.4, title="Call Success vs Failure Ratio")
                    st.plotly_chart(fig_call, width="stretch")
                else:
                    st.info("Status 데이터 필드가 누락되었습니다.")
            with col_table:
                st.markdown(f"**Call History Log (Total: {len(clean_call_df)})**")
                st.dataframe(clean_call_df, width="stretch", height=400)
        else:
            st.info("현재 분석 세션에 Call_Session 로그가 존재하지 않습니다.")

def render_signal_level_timeline(df):
    st.subheader("Signal Level & Quality Timeline by RAT")

    if 'log_type' in df.columns:
        sig_df = df[df['log_type'] == 'Signal_Level'].copy()

        if not sig_df.empty:
            sig_df['Level'] = pd.to_numeric(sig_df.get('level', 0), errors='coerce')

            def create_hover_text(row):
                info = []
                for col in [c for c in row.index if c.startswith('details_')]:
                    val = row[col]
                    if pd.notna(val) and str(val) != "None":
                        rat_name = col.replace('details_', '')
                        info.append(f"<b>{rat_name}</b>: {val}")
                return "<br>".join(info)

            sig_df['hover_detail'] = sig_df.apply(create_hover_text, axis=1)

            fig = px.line(
                sig_df, x='time', y='Level', color='rat', facet_row='slot',
                line_shape='hv', markers=True,
                title="Radio Access Technology (RAT) Signal Level Trend",
                hover_data={'hover_detail': True, 'raw_info': True}
            )

            fig.update_traces(
                hovertemplate="<b>%{customdata[0]}</b><br>Level: %{y}<br>Details:<br>%{customdata[1]}<extra></extra>",
                customdata=sig_df[['rat', 'hover_detail']].values
            )

            st.plotly_chart(fig, width="stretch")
        else:
            st.info("Signal_Level 데이터가 존재하지 않습니다.")

def render_data_usage_profiling(df):
    """셀룰러 데이터 사용량 프로파일링 차트 렌더링"""
    st.subheader("Cellular Data Usage Profiling")

    if 'log_type' in df.columns:
        du_df = df[df['log_type'] == 'Data_Usage'].copy()

        if not du_df.empty:
            du_df['total_mb'] = pd.to_numeric(du_df['total_mb'], errors='coerce')

            col_du1, col_du2 = st.columns(2)
            with col_du1:
                app_df = du_df.groupby('app_name')['total_mb'].sum().reset_index().sort_values(by='total_mb', ascending=False).head(10)
                fig_app = px.pie(app_df, values='total_mb', names='app_name', hole=0.4, title='Cumulative Data Usage Top 10 by Application (MB)')
                fig_app.update_traces(textposition='inside', textinfo='percent+label')
                st.plotly_chart(fig_app, use_container_width=True)

            with col_du2:
                rat_df = du_df.groupby('rat')['total_mb'].sum().reset_index()
                fig_rat = px.pie(
                    rat_df, values='total_mb', names='rat', title='Data Traffic Ratio by RAT', color='rat',
                    color_discrete_map={'LTE':'#1f77b4', '5G (NR)':'#ff7f0e', 'Unknown':'#7f7f7f'}
                )
                fig_rat.update_traces(textposition='inside', textinfo='percent+label')
                st.plotly_chart(fig_rat, use_container_width=True)

            if 'time' in du_df.columns:
                st.divider()
                st.markdown("##### Traffic Trend by Application (Stacked)")

                du_df['time_dt'] = pd.to_datetime(du_df['time'], errors='coerce')
                time_df = du_df.dropna(subset=['time_dt']).sort_values('time_dt')

                if not time_df.empty:
                    fig_time = px.bar(
                        time_df,
                        x='time_dt',
                        y='total_mb',
                        color='app_name',
                        labels={'time_dt': 'Log Time', 'total_mb': 'Usage (MB)', 'app_name': 'Application'},
                        barmode='stack'
                    )
                    fig_time.update_layout(
                        plot_bgcolor='rgba(0,0,0,0)',
                        xaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.2)'),
                        yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.2)'),
                        legend=dict(orientation="h", yanchor="bottom", y=-0.4, xanchor="center", x=0.5)
                    )
                    fig_time.update_traces(marker_line_width=0)
                    st.plotly_chart(fig_time, use_container_width=True)
                else:
                    st.info("Time-series parsing failed for Data Usage logs.")
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

    st.markdown("##### Data Traffic Timeline")

    fig = px.bar(
        data_df,
        x='time_dt',
        y='total_mb',
        color='app_name',
        title="Application Data Usage over Time (MB)",
        labels={'time_dt': 'Time', 'total_mb': 'Usage (MB)', 'app_name': 'Application'},
        barmode='stack'
    )

    fig.update_layout(
        plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.2)'),
        yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.2)')
    )

    st.plotly_chart(fig, use_container_width=True)

def render_network_timeseries_and_dns(df):
    st.subheader("DNS & Network Time-Series Analysis")

    if 'log_type' in df.columns:
        dns_df = df[df['log_type'] == 'Network_DNS_Issue'].copy()
        if not dns_df.empty:
            col_dns1, col_dns2 = st.columns(2)
            with col_dns1:
                st.markdown("**DNS Failure/Block Reasons**")
                fig_dns = px.pie(dns_df, names='suspected_reason', hole=0.4)
                st.plotly_chart(fig_dns, width="stretch")
            with col_dns2:
                st.markdown("**DNS Issues by Package**")
                pkg_counts = dns_df['package'].value_counts().reset_index()
                pkg_counts.columns = ['package', 'count']
                fig_pkg = px.bar(pkg_counts, x='count', y='package', orientation='h')
                st.plotly_chart(fig_pkg, width="stretch")

            st.markdown("**DNS Failure Details (Network Cross-Analysis)**")

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

            metric_choice = st.selectbox("Select Metric", ["DNS Avg Response Time (ms)", "DNS Error Rate (%)"])
            target_col = 'dns_avg' if "Response Time" in metric_choice else 'dns_err_rate'

            fig_ts = px.line(
                ts_df, x='time_dt', y=target_col, color='netId', hover_data=['transport'],
                markers=True, title=f"{metric_choice} Trend"
            )
            fig_ts.update_xaxes(tickformat="%m-%d\n%H:%M:%S", title="Time")
            fig_ts.update_layout(yaxis_title="Value")
            st.plotly_chart(fig_ts, width="stretch")
        else:
            st.info("Network Timeline Stat 데이터가 존재하지 않습니다.")

def render_ntn_advanced_fw_analyzer(current_base):
    st.subheader("NTN Roaming Policy & UI State Analysis (Starlink)")

    if not current_base:
        st.info("Target file is not selected.")
        return

    file_path = f"./result/{current_base}_ntn.json"
    if not os.path.exists(file_path):
        st.info("NTN data file not found.")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not data:
        st.error("No NTN data extracted from the log file.")
        return

    ntn_df = pd.DataFrame(data)

    real_ntn_events = ntn_df[ntn_df['event_type'] != 'RADIO_POWER']
    if real_ntn_events.empty:
        st.info("No NTN specific events found.")
        return

    expected_cols = ['ntn_plmn', 'data_policy', 'power_state', 'ntn_mode', 'last_ntn_mode', 'last_phone_mode', 'is_hysteresis', 'raw_info']
    for col in expected_cols:
        if col not in ntn_df.columns:
            ntn_df[col] = None

    ntn_df = ntn_df.sort_values('time').reset_index(drop=True)

    m_plmn = ntn_df['event_type'] == 'PLMN_MATCH'
    ntn_df.loc[m_plmn, 'keep'] = ntn_df[m_plmn]['ntn_plmn'] != ntn_df[m_plmn]['ntn_plmn'].shift(1)

    m_mode = ntn_df['event_type'] == 'NTN_MODE_NOTIFY'
    cond_internal_diff = ntn_df[m_mode]['last_ntn_mode'] != ntn_df[m_mode]['ntn_mode']
    cond_temporal_diff = ntn_df[m_mode]['ntn_mode'] != ntn_df[m_mode]['ntn_mode'].shift(1)
    ntn_df.loc[m_mode, 'keep'] = cond_internal_diff & cond_temporal_diff

    m_radio = ntn_df['event_type'] == 'RADIO_POWER'
    ntn_df.loc[m_radio, 'keep'] = ntn_df[m_radio]['power_state'] != ntn_df[m_radio]['power_state'].shift(1)

    ntn_df.loc[~(m_plmn | m_mode | m_radio), 'keep'] = True
    clean_df = ntn_df[ntn_df['keep'] == True].copy()

    plmn_logs = ntn_df[ntn_df['event_type'] == 'PLMN_MATCH']
    latest_plmn = plmn_logs.iloc[-1]['ntn_plmn'] if not plmn_logs.empty else "N/A"

    policy_logs = ntn_df[ntn_df['event_type'] == 'DATA_POLICY']
    latest_policy = policy_logs.iloc[-1]['data_policy'] if not policy_logs.empty else "N/A"

    ui_icon_status = "OFF"
    for _, row in ntn_df.iloc[::-1].iterrows():
        if row['event_type'] == 'NTN_MODE_NOTIFY':
            ui_icon_status = "ON (Real)" if str(row['ntn_mode']).upper() == 'ON' else "OFF"
            break
        elif row['event_type'] == 'HYSTERESIS_ICON_ON':
            ui_icon_status = "ON (Hysteresis)"
            break

    col1, col2, col3 = st.columns(3)
    col1.metric("Target Satellite PLMN", latest_plmn)
    col2.metric("Active Data Policy", latest_policy)
    col3.metric("Status Bar Icon State", ui_icon_status)

    st.divider()

    st.markdown("**NTN Entry Sequence & State Transition Timeline**")
    chart_df = clean_df[clean_df['event_type'] != 'DATA_POLICY'].copy()

    if not chart_df.empty:
        current_year = datetime.datetime.now().year
        chart_df['time_dt'] = pd.to_datetime(str(current_year) + "-" + chart_df['time'], errors='coerce')
        chart_df = chart_df.sort_values('time_dt')

        fig = px.scatter(
            chart_df, x='time_dt', y='event_type', color='event_type',
            hover_data=['ntn_plmn', 'last_ntn_mode', 'ntn_mode', 'is_hysteresis', 'power_state'],
            title="NTN Event Tracker (State Changes)",
            labels={'time_dt': 'Event Time', 'event_type': 'Event Type'}
        )
        fig.update_traces(marker=dict(size=14, symbol='diamond', line=dict(width=2, color='DarkSlateGrey')))
        fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")
        order = ['RADIO_POWER', 'PLMN_MATCH', 'HYSTERESIS_ICON_ON', 'NTN_MODE_NOTIFY']
        fig.update_layout(yaxis={'categoryorder': 'array', 'categoryarray': order})
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("No timeline events to display.")

    st.markdown("**NTN State Transition Details**")
    display_cols = [col for col in ['time', 'event_type', 'power_state', 'ntn_plmn', 'last_ntn_mode', 'ntn_mode', 'is_hysteresis', 'data_policy'] if col in clean_df.columns]
    final_table_df = clean_df[display_cols].fillna("-")
    st.dataframe(final_table_df, width="stretch")

def render_data_call_analyzer(data):
    st.subheader("RIL Data Call (SETUP_DATA_CALL) Analysis")

    if not data or len(data) == 0:
        st.info("No SETUP_DATA_CALL transaction history found.")
        return

    df = pd.DataFrame(data)

    expected_columns = ['status', 'latency_ms', 'event_type', 'req_time', 'apn',
                        'network', 'protocol', 'cause', 'cid']
    for col in expected_columns:
        if col not in df.columns:
            df[col] = 0 if col == 'latency_ms' else 'UNKNOWN'

    setup_df = df[df['event_type'] == 'DATA_SETUP']
    total_calls = len(setup_df)
    success_calls = len(setup_df[setup_df['status'] == 'SUCCESS'])
    fail_calls = total_calls - success_calls
    success_rate = (success_calls / total_calls) * 100 if total_calls > 0 else 0

    valid_latency = setup_df[setup_df['latency_ms'] > 0]['latency_ms']
    avg_latency = valid_latency.mean() if not valid_latency.empty else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Connections Attempted", f"{total_calls}")
    col2.metric("Success Rate", f"{success_rate:.1f} %")
    col3.metric("Failure Count", f"{fail_calls}")
    col4.metric("Avg Setup Latency", f"{avg_latency:.0f} ms")

    st.divider()

    st.markdown("**Data Call Transaction & Lifecycle**")

    chart_df = df[~((df['event_type'] == 'UNSOL_UPDATE') & (df.get('is_changed') == False))].copy()

    color_map = {
        "SUCCESS": "#2ecc71",
        "FAIL": "#e74c3c",
        "DORMANT": "#f1c40f",
        "ACTIVE": "#3498db",
        "DROP": "#8e44ad"
    }

    if not chart_df.empty:
        current_year = datetime.datetime.now().year
        chart_df['req_time_dt'] = pd.to_datetime(str(current_year) + "-" + chart_df['req_time'], errors='coerce')
        chart_df = chart_df.dropna(subset=['req_time_dt']).sort_values('req_time_dt')

        fig = px.scatter(
            chart_df, x='req_time_dt', y='apn', color='status',
            color_discrete_map=color_map,
            symbol='event_type',
            size=[15]*len(chart_df),
            hover_data=['event_type', 'network', 'protocol', 'cause', 'latency_ms', 'cid'],
            title="APN Data Call State Transition",
            labels={'req_time_dt': 'Time', 'apn': 'APN'}
        )
        fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")
        st.plotly_chart(fig, width="stretch", key="datacall_scatter_chart")
    else:
        st.info("No events to plot.")

    st.markdown("**Data Call Transaction Details**")
    st.dataframe(df, width="stretch")

def render_ims_sip_flow(current_base=None):
    st.subheader("VoLTE / IMS SIP Call Flow (Sequence Diagram)")

    if not current_base: return
    file_path = f"./result/{current_base}_ims_sip.json"
    if not os.path.exists(file_path):
        st.info("SIP Message Log is not available.")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not data:
        st.info("No SIP transactions recorded in this log.")
        return

    sip_df = pd.DataFrame(data)

    total_msgs = len(sip_df)
    error_msgs = len(sip_df[sip_df['is_error'] == True])

    col1, col2, col3 = st.columns(3)
    col1.metric("Total SIP Transactions", f"{total_msgs}")
    col2.metric("SIP Error Responses (4xx~6xx)", f"{error_msgs}", delta="Abnormal" if error_msgs > 0 else "Normal", delta_color="inverse" if error_msgs > 0 else "normal")

    try:
        sip_df['time_dt'] = pd.to_datetime(sip_df['time'], format='%m-%d %H:%M:%S.%f', errors='coerce')
        invite_time = sip_df[sip_df['method_code'].str.contains('INVITE', na=False)]['time_dt'].min()
        ok_time = sip_df[sip_df['method_code'].str.contains('200 OK', na=False)]['time_dt'].max()
        if pd.notna(invite_time) and pd.notna(ok_time) and ok_time >= invite_time:
            latency_ms = int((ok_time - invite_time).total_seconds() * 1000)
            col3.metric("Call Setup Latency (Max)", f"{latency_ms} ms")
        else:
            col3.metric("Call Setup Latency (Max)", "N/A")
    except:
        col3.metric("Call Setup Latency (Max)", "N/A")

    st.divider()

    sip_df = sip_df.sort_values('time')
    sip_df['y_pos'] = range(len(sip_df), 0, -1)

    fig = go.Figure()
    fig.add_shape(type="line", x0=0, y0=0, x1=0, y1=len(sip_df)+1, line=dict(color="lightgray", width=2, dash="dash"))
    fig.add_shape(type="line", x0=1, y0=0, x1=1, y1=len(sip_df)+1, line=dict(color="lightgray", width=2, dash="dash"))

    for idx, row in sip_df.iterrows():
        y = row['y_pos']
        method = row['method_code']
        cseq = row['cseq']
        is_error = row['is_error']
        time_str = row['time'].split(' ')[1]

        if is_error:
            color = "#e74c3c"
        elif "200 OK" in method or "202" in method:
            color = "#2ecc71"
        else:
            color = "#3498db"

        if "Tx" in row['direction']:
            x0, x1 = 0.05, 0.95
        else:
            x0, x1 = 0.95, 0.05

        fig.add_annotation(
            x=x1, y=y, ax=x0, ay=y,
            xref="x", yref="y", axref="x", ayref="y",
            text=f"<b>{method}</b><br><span style='font-size:10px'>{cseq}</span>",
            showarrow=True, arrowhead=2, arrowsize=1.5, arrowwidth=2, arrowcolor=color,
            font=dict(color=color, size=13), align="center", yshift=8
        )

        fig.add_annotation(
            x=-0.05, y=y, xref="x", yref="y",
            text=time_str, showarrow=False,
            font=dict(size=11, color="gray"), xanchor="right"
        )

    fig.update_layout(
        xaxis=dict(
            tickmode='array', tickvals=[0, 1],
            ticktext=['UE', 'IMS Network'],
            tickfont=dict(size=15, weight='bold'),
            range=[-0.2, 1.2], side="top", showgrid=False, zeroline=False
        ),
        yaxis=dict(showticklabels=False, range=[0, len(sip_df)+1], showgrid=False, zeroline=False),
        height=max(400, len(sip_df) * 45),
        margin=dict(l=120, r=50, t=80, b=20),
        plot_bgcolor='white', hovermode=False
    )

    st.plotly_chart(fig, width="stretch")

    st.markdown("**SIP Message Transaction Details**")
    display_cols = ['time', 'direction', 'msg_type', 'method_code', 'tid', 'cseq', 'raw_log']
    st.dataframe(sip_df[display_cols], width="stretch")

def render_crash_analyzer(report_data):
    st.subheader("System Crash & FATAL Error Analysis")

    # 💡 1. 여기서 원본 데이터를 original_crashes 라는 이름으로 꺼냅니다.
    original_crashes = report_data.get("crash_context", [])
    native_crash_data = report_data.get("native_crash_context", [])
    anr_data_list = report_data.get("anr_context", [])
    binder_warnings = report_data.get("binder_warnings", [])

    if isinstance(anr_data_list, dict) and anr_data_list:
        anr_data_list = [anr_data_list]

    if not original_crashes and not anr_data_list and not native_crash_data and not binder_warnings:
        st.success("No system crashes, ANRs, or FATAL exceptions detected in the log.")
        return

    # 💡 2. 꺼내온 원본(original_crashes)에서 am_kill, am_wtf, 일반 Crash를 분리합니다.
    system_kills = [c for c in original_crashes if c.get("type") == "SYSTEM_KILL"]
    system_wtfs = [c for c in original_crashes if c.get("type") == "SYSTEM_WTF"]

    # 💡 3. 분리하고 남은 일반 Crash들만 다시 crash_data 에 담아줍니다.
    # (이렇게 하면 아래쪽에 있는 기존 crash_data 렌더링 코드를 하나도 수정 안 해도 됩니다!)
    crash_data = [c for c in original_crashes if c.get("type") not in ("SYSTEM_KILL", "SYSTEM_WTF")]

    # ==========================================
    # 4. System Kill (am_kill) 렌더링
    if system_kills:
        st.error(f"**🚨 시스템 강제 종료 (am_kill) {len(system_kills)}건 감지!** 프로세스가 시스템 서버에 의해 강제로 죽었습니다.")
        kill_rows = []
        for k in system_kills:
            kill_rows.append({
                "발생 시간 (Time)": k.get("time", "Unknown"),
                "종료된 프로세스 (Target)": k.get("process", "Unknown"),
                "강제 종료 사유 (Reason)": k.get("top_method", "Unknown"),
                "트리거 원문 (Raw)": k.get("trigger", "")
            })
        df_kill = pd.DataFrame(kill_rows)
        st.dataframe(df_kill, use_container_width=True, hide_index=True)

    # 5. System WTF (am_wtf) 요약 렌더링 (대량 발생 방어)
    if system_wtfs:
        st.warning(f"**⚠️ 시스템 이상 징후 (am_wtf) {len(system_wtfs)}건 감지!** (What a Terrible Failure)")

        wtf_summary = {}
        for w in system_wtfs:
            proc = w.get("process", "Unknown")
            ts = w.get("time", "Unknown")
            if proc not in wtf_summary:
                wtf_summary[proc] = {"count": 0, "first": ts, "last": ts}
            wtf_summary[proc]["count"] += 1
            if ts != "Unknown":
                wtf_summary[proc]["last"] = ts

        summary_rows = []
        for proc, data in wtf_summary.items():
            summary_rows.append({
                "대상 프로세스 (Target)": proc,
                "발생 횟수 (Count)": f"{data['count']}회",
                "최초 발생 (First Seen)": data['first'],
                "최근 발생 (Last Seen)": data['last']
            })

        df_wtf_summary = pd.DataFrame(summary_rows)
        st.dataframe(df_wtf_summary, use_container_width=True, hide_index=True)

        with st.expander(f"🔍 최근 am_wtf 상세 로그 보기 (최신 20건 / 총 {len(system_wtfs)}건)"):
            wtf_rows = []
            for w in system_wtfs[-20:]:
                wtf_rows.append({
                    "발생 시간 (Time)": w.get("time", "Unknown"),
                    "대상 프로세스 (Target)": w.get("process", "Unknown"),
                    "트리거 원문 (Raw)": w.get("trigger", "")
                })

            df_wtf_recent = pd.DataFrame(wtf_rows)
            st.dataframe(df_wtf_recent, use_container_width=True, hide_index=True)

    if binder_warnings:
        binder_event_types = {
            "THREAD_EXHAUSTION", "TRANSACTION_DELAY", "BINDER_DELAY",
            "BINDER_TRANSACTION_FAILURE", "BINDER_BUFFER_ERROR", "REPEATED_BINDER_DELAY"
        }
        binder_event_rows = [
            b for b in binder_warnings
            if isinstance(b, dict) and b.get("type") in binder_event_types
        ]

        st.warning(
            f"Warning: {len(binder_event_rows)} Binder events (Delay/Failure/Exhaustion) detected. "
            "Examine carefully in correlation with ANR/Watchdog/Service restart instances."
        )
        with st.expander("Binder Event Details"):
            if binder_event_rows:
                binder_df = pd.DataFrame(binder_event_rows)[['time', 'type', 'desc']]
                max_display_rows = 300
                if len(binder_df) > max_display_rows:
                    st.caption(f"Displaying most recent {max_display_rows} entries. Total: {len(binder_df)}")
                    binder_df = binder_df.tail(max_display_rows)
                st.dataframe(binder_df, width="stretch")
            else:
                st.info("No Binder event details to display.")

        binder_context_summary = report_data.get("binder_context_summary", {})
        if binder_context_summary:
            with st.expander("Additional Binder Context Summary", expanded=False):
                signals = binder_context_summary.get("signals", {})
                checklist = binder_context_summary.get("checklist", [])
                if signals:
                    signal_df = pd.DataFrame([
                        {"Context": k, "Matched lines": v} for k, v in signals.items()
                    ])
                    st.dataframe(signal_df, width="stretch", hide_index=True)
                if checklist:
                    st.markdown("**Verification Checklist:**")
                    for item in checklist:
                        st.markdown(f"- {item}")

    if native_crash_data:
        st.error(f"Critical: {len(native_crash_data)} Native C/C++ crash(es) detected.")
        for n_crash in native_crash_data:
            ts = n_crash.get('timestamp', 'Time Unknown')
            process = n_crash.get('process', 'Unknown')
            signal = n_crash.get('signal', 'Unknown')

            with st.expander(f"[{ts}] {process} - NATIVE CRASH (Signal: {signal})"):
                st.markdown(f"**Abort Message:** `{n_crash.get('abort_message', 'none')}`")

                callstack = n_crash.get('callstack', [])
                if callstack:
                    st.markdown("**Native Callstack:**")
                    stack_df = pd.DataFrame(callstack)
                    st.dataframe(stack_df, hide_index=True, width="stretch")

                if 'cross_context_logs' in n_crash and n_crash['cross_context_logs']:
                    st.markdown("**Surrounding Context Log:**")
                    st.code("\n".join(n_crash['cross_context_logs']), language='log')

    if anr_data_list:
        st.error(f"Critical: {len(anr_data_list)} Application Not Responding (ANR) events detected.")

        for anr_data in anr_data_list:
            anr_time = anr_data.get('time', 'Unknown Time')
            anr_process = anr_data.get('process', 'Unknown Process')
            anr_reason = anr_data.get('reason', 'Unknown Reason')
            anr_pid = anr_data.get('process_info', {}).get('pid', 'Unknown')

            with st.expander(f"[{anr_time}] ANR - {anr_process} (PID: {anr_pid})"):
                st.markdown(f"**ANR Reason:** `{anr_reason}`")

                analysis_summary = anr_data.get('analysis_summary', {})
                if analysis_summary:
                    st.markdown("**ANR Analysis Summary:**")

                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Main Stack", "Present" if analysis_summary.get('has_main_stack') else "None")
                    c2.metric("Lock Contention", "Detected" if analysis_summary.get('has_lock_contention') else "None")
                    c3.metric("Binder Wait", "Detected" if analysis_summary.get('has_active_binder') else "None")
                    c4.metric("Pre-Logcat", "Present" if analysis_summary.get('has_pre_anr_logcat') else "None")

                    c5, c6, c7 = st.columns(3)
                    c5.metric("CPU Clue", "Present" if analysis_summary.get('has_cpu_hint') else "None")
                    c6.metric("System Server Clue", "Present" if analysis_summary.get('has_system_server_hint') else "None")
                    c7.metric("I/O Clue", "Present" if analysis_summary.get('has_io_hint') else "None")

                pre_anr_logs = anr_data.get('pre_anr_logcat', [])
                if pre_anr_logs:
                    with st.expander("View Pre-ANR Logcat Context", expanded=False):
                        st.caption("Logs immediately preceding the ANR detection.")
                        st.code("\n".join(pre_anr_logs[-120:]), language='log')

                context_analysis = anr_data.get('context_analysis', {})
                if context_analysis:
                    cpu_logs = context_analysis.get('cpu_logs', [])
                    system_server_logs = context_analysis.get('system_server_logs', [])
                    io_logs = context_analysis.get('io_logs', [])

                    if cpu_logs or system_server_logs or io_logs:
                        st.markdown("**Auxiliary Context Analysis:**")

                        tab_cpu, tab_system, tab_io = st.tabs(["CPU", "System Server", "I/O"])

                        with tab_cpu:
                            if cpu_logs:
                                st.caption("CPU usage/load related logs.")
                                st.code("\n".join(cpu_logs[-80:]), language='log')
                            else:
                                st.info("No CPU related clue logs found.")

                        with tab_system:
                            if system_server_logs:
                                st.caption("System server logs (ActivityManager, WindowManager, Watchdog, etc).")
                                st.code("\n".join(system_server_logs[-80:]), language='log')
                            else:
                                st.info("No system server clue logs found.")

                        with tab_io:
                            if io_logs:
                                st.caption("I/O delay/block suspected logs.")
                                st.code("\n".join(io_logs[-80:]), language='log')
                            else:
                                st.info("No I/O clue logs found.")

                lock_chain = anr_data.get('lock_chain', {})
                if lock_chain and lock_chain.get('blocker_thread'):
                    st.markdown("**Lock Contention / Deadlock Detected:**")
                    st.warning(f"Main thread is waiting for lock (`{lock_chain['lock_address']}`). "
                            f"(Occupying Thread TID: {lock_chain['blocker_thread']})")
                    if lock_chain.get('blocker_stack'):
                        st.markdown(f"**Occupying Thread (TID: {lock_chain['blocker_thread']}) Callstack:**")
                        st.code("\n".join(lock_chain['blocker_stack']), language='java')

                binder_txs = anr_data.get('active_binder_transactions', [])
                if binder_txs:
                    st.markdown("**Pending Binder Transactions (Outgoing):**")
                    binder_rows = []
                    for tx in binder_txs:
                        binder_rows.append({
                            "from_pid": tx.get('from_pid', '-'),
                            "from_tid": tx.get('from_tid', '-'),
                            "to_pid": tx.get('to_pid', '-'),
                            "to_tid": tx.get('to_tid', '-'),
                            "code": tx.get('code', '-'),
                            "raw": tx.get('raw', '')
                        })
                    st.dataframe(pd.DataFrame(binder_rows), width="stretch")

                main_stack = anr_data.get('main', {}).get('stack', [])
                if main_stack:
                    st.markdown("**Main Thread Callstack:**")
                    with st.expander("View Full Main Thread Stack", expanded=True):
                        st.code("\n".join(main_stack), language='java')

    if crash_data:
        st.error(f"Critical: {len(crash_data)} System Crash/FATAL exception(s) detected.")

        for i, crash in enumerate(crash_data):
            ts = crash.get('timestamp', 'Time Unknown')
            process = crash.get('process', 'Unknown Process')
            crash_type = crash.get('crash_type', 'FATAL EXCEPTION')

            with st.expander(f"[{ts}] {process} - {crash_type}"):
                raw_logs_str = str(crash.get('cross_context_logs', crash.get('raw_line', ''))).lower()
                if "transactiontoolargeexception" in raw_logs_str:
                    st.error("Diagnostic Cause: TransactionTooLargeException. Buffer overflow triggered by intent data exceeding 1MB limit.")
                if 'cross_context_logs' in crash and crash['cross_context_logs']:
                    st.markdown("**Surrounding Context Log:**")
                    st.code("\n".join(crash['cross_context_logs']), language='log')
                elif 'raw_line' in crash:
                    st.markdown("**Raw Crash Log:**")
                    st.code(crash['raw_line'], language='log')

def render_sat_at_analyzer(current_base=None):
    st.subheader("Satellite Modem Control Sequence & State (AT Command)")

    if not current_base: return
    file_path = f"./result/{current_base}_sat_at.json"
    if not os.path.exists(file_path): return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    metrics = data.get("metrics", {})
    flow = data.get("call_flow", [])
    reg_history = data.get("registration_history", [])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Satellite ARFCN", metrics.get("arfcn", "N/A"))
    c2.metric("Registration State", metrics.get("current_reg_state", "Unknown"))

    call_total = metrics.get('calls_total', 0)
    call_fail = metrics.get('calls_dropped_or_failed', 0)
    c3.metric("Voice Call (Total/Fail)", f"{call_total} / {call_fail}",
              delta=f"{call_fail} Drop" if call_fail > 0 else "Normal", delta_color="inverse")

    sms_rx = metrics.get('sms_rx', 0)
    sms_tx_succ = metrics.get('sms_tx_success', 0)
    sms_tx_fail = metrics.get('sms_tx_fail', 0)
    c4.metric("SMS (Rx/Tx Succ/Tx Fail)", f"{sms_rx} / {sms_tx_succ} / {sms_tx_fail}",
              delta=f"{sms_tx_fail} Fail" if sms_tx_fail > 0 else "Normal", delta_color="inverse")
    st.divider()

    if reg_history:
        st.write("#### Satellite Network Registration History")
        df_reg = pd.DataFrame(reg_history)

        fig_reg = px.line(
            df_reg, x="time", y="status_str", markers=True,
            hover_data=["raw"],
            labels={"time": "Time", "status_str": "State"}
        )
        fig_reg.update_traces(line_shape='hv', line_color='#E64A19', marker=dict(size=8))
        fig_reg.update_yaxes(categoryorder='array', categoryarray=["Deregistered (0)", "Searching", "Registered (1)"])
        fig_reg.update_layout(height=250, margin=dict(t=20, b=20))
        st.plotly_chart(fig_reg, width="stretch")
        st.divider()

    if flow:
        st.write("#### Call Control Full-Stack Sequence (AP ↔ RIL ↔ Modem)")
        fig = go.Figure()

        for idx, msg in enumerate(flow):
            time_str = msg['time']
            src = msg['src']
            dst = msg['dst']
            desc = msg['desc']
            is_highlight = msg.get('is_highlight', False)

            offset = 0.05
            x0 = src + offset if src < dst else src - offset
            x1 = dst - offset if src < dst else dst + offset

            y = len(flow) - idx

            if src == 0 or dst == 0: color = "#9c27b0" if is_highlight else "#ba68c8"
            else: color = "#d32f2f" if is_highlight else "#1f77b4"
            if "ERROR" in desc or "CEND" in desc: color = "red"

            fig.add_annotation(
                x=x1, y=y, ax=x0, ay=y, xref="x", yref="y", axref="x", ayref="y",
                text=f"<b>{desc}</b>", showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=1.5, arrowcolor=color,
                font=dict(color=color, size=11), align="center", yshift=8
            )
            fig.add_annotation(
                x=-0.2, y=y, xref="x", yref="y", text=time_str, showarrow=False,
                font=dict(size=10, color="gray"), xanchor="right"
            )

        fig.update_layout(
            xaxis=dict(
                tickmode='array', tickvals=[0, 1, 2],
                ticktext=['Android FW', 'RIL Daemon', 'Modem (CP)'],
                tickfont=dict(size=14, weight='bold'),
                range=[-0.5, 2.5], side="top", showgrid=False, zeroline=False
            ),
            yaxis=dict(showticklabels=False, range=[0, len(flow)+1], showgrid=False, zeroline=False),
            height=max(400, len(flow) * 35), margin=dict(l=150, r=50, t=60, b=20), plot_bgcolor="white"
        )
        st.plotly_chart(fig, width="stretch")

def render_service_state_timeline(df):
    st.subheader("Network Service State (Registration State) Timeline")

    if 'log_type' not in df.columns:
        return

    oos_df = df[df['log_type'] == 'OOS_Event'].copy()

    if oos_df.empty:
        st.success("Stable IN_SERVICE condition. No OOS or service state transitions recorded.")
        return

    records = []
    for _, row in oos_df.iterrows():
        time_val = row.get('time')
        slot = str(row.get('slot', row.get('slotId', '0')))
        v_reg = str(row.get('voice_reg', 'Unknown'))
        d_reg = str(row.get('data_reg', 'Unknown'))

        operator_info = row.get('operator', 'Unknown')
        radio_tech = row.get('rat', 'Unknown')

        def map_reg_state(reg_str):
            if not reg_str or reg_str == 'nan': return "UNKNOWN"
            if reg_str.startswith("0"): return "IN_SERVICE"
            if reg_str.startswith("1"): return "OUT_OF_SERVICE"
            if reg_str.startswith("2"): return "EMERGENCY_ONLY"
            if reg_str.startswith("3"): return "POWER_OFF"
            return "UNKNOWN"

        records.append({
            "time": time_val, "Slot": f"Slot {slot}", "Type": "Voice",
            "State": map_reg_state(v_reg), "Raw_Reg": v_reg,
            "Event": row.get('event', row.get('event_type', 'Unknown')),
            "Cause": row.get('candidate_reason', row.get('root_cause_candidate', 'None')),
            "Operator": operator_info, "Radio_Tech": radio_tech
        })
        records.append({
            "time": time_val, "Slot": f"Slot {slot}", "Type": "Data",
            "State": map_reg_state(d_reg), "Raw_Reg": d_reg,
            "Event": row.get('event', row.get('event_type', 'Unknown')),
            "Cause": row.get('candidate_reason', row.get('root_cause_candidate', 'None')),
            "Operator": operator_info, "Radio_Tech": radio_tech
        })

    state_df = pd.DataFrame(records)
    state_df = state_df.sort_values(by=['Slot', 'Type', 'time']).reset_index(drop=True)

    state_df['keep'] = state_df['State'] != state_df.groupby(['Slot', 'Type'])['State'].shift(1)
    state_df.loc[state_df.groupby(['Slot', 'Type']).head(1).index, 'keep'] = True

    clean_df = state_df[state_df['keep']].copy()

    if clean_df.empty:
        st.info("No significant state changes to display.")
        return

    import datetime
    current_year = datetime.datetime.now().year
    clean_df['time_dt'] = pd.to_datetime(str(current_year) + "-" + clean_df['time'], format='%Y-%m-%d %H:%M:%S.%f', errors='coerce')
    clean_df = clean_df.sort_values(by=['time_dt', 'Slot', 'Type']).reset_index(drop=True)

    clean_df['Label'] = clean_df.apply(
        lambda x: f"[{x['Radio_Tech']}] {x['Operator']}" if x['State'] == 'IN_SERVICE' else "",
        axis=1
    )

    category_order = ["POWER_OFF", "EMERGENCY_ONLY", "OUT_OF_SERVICE", "IN_SERVICE"]

    fig = px.line(
        clean_df, x='time_dt', y='State', color='Type', facet_row='Slot',
        line_shape='hv', markers=True,
        text='Label',
        title="Voice/Data Registration State Transition Timeline",
        labels={'time_dt': 'Event Time', 'State': 'State', 'Type': 'Connection Type'},
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

    chart_height = max(300, 200 * clean_df['Slot'].nunique())
    fig.update_layout(height=chart_height, hovermode="x unified", margin=dict(t=50, b=20))

    st.plotly_chart(fig, width="stretch")

def render_integrated_rf_call_timeline(report_data):
    st.subheader("Integrated Timeline: Call Status & RF Environment (RSRP dBm)")
    st.markdown("A cross-analysis timeline correlating active call windows, RSRP fluctuations, and SIP transaction errors.")

    signal_history = report_data.get("signal_level_history", [])
    if not signal_history:
        st.info("Insufficient RF signal history for integrated timeline generation.")
        return

    import datetime
    import re
    current_year = datetime.datetime.now().year

    sig_times = []
    rsrp_values = []
    hover_texts = []

    for s in signal_history:
        t_str = str(s.get("time", ""))[:14]
        try:
            dt = pd.to_datetime(f"{current_year}-{t_str}", format='%Y-%m-%d %H:%M:%S')

            details = s.get("details", {})
            rsrp_str = "Unknown"
            rat_type = s.get("rat", "LTE")

            if "LTE" in details and details["LTE"].get("RSRP") != "Unknown":
                rsrp_str = details["LTE"]["RSRP"]
                rat_type = "LTE"
            elif "NR" in details and details["NR"].get("RSRP") != "Unknown":
                rsrp_str = details["NR"]["RSRP"]
                rat_type = "NR"

            if rsrp_str != "Unknown":
                match = re.search(r'(-\d+)', rsrp_str)
                if match:
                    rsrp_val = int(match.group(1))
                    sig_times.append(dt)
                    rsrp_values.append(rsrp_val)

                    sinr_str = details.get(rat_type, {}).get("SINR", "Unknown")
                    rsrq_str = details.get(rat_type, {}).get("RSRQ", "Unknown")
                    hover_texts.append(
                        f"Time: {s.get('time')}<br>"
                        f"RAT: {rat_type} (Slot {s.get('slot', '0')})<br>"
                        f"Signal Level: {s.get('level')}<br>"
                        f"RSRP: {rsrp_str}<br>"
                        f"RSRQ: {rsrq_str}<br>"
                        f"SINR: {sinr_str}"
                    )
        except:
            pass

    fig = go.Figure()

    if sig_times:
        fig.add_trace(go.Scatter(
            x=sig_times, y=rsrp_values,
            mode='lines+markers',
            name='RSRP (dBm)',
            line=dict(color='#1f77b4', width=2.5),
            marker=dict(size=6, symbol='circle'),
            text=hover_texts,
            hoverinfo='text'
        ))

    sessions = report_data.get("call_sessions", [])
    for s in sessions:
        try:
            start_dt = pd.to_datetime(f"{current_year}-{s.get('start_time')[:14]}", format='%Y-%m-%d %H:%M:%S')
            end_time_str = s.get('end_time')
            if end_time_str:
                end_dt = pd.to_datetime(f"{current_year}-{end_time_str[:14]}", format='%Y-%m-%d %H:%M:%S')
            else:
                end_dt = start_dt + pd.Timedelta(seconds=5)

            status = str(s.get("status", "")).upper()
            is_drop = "DROP" in status or "FAIL" in status

            color = "rgba(255, 0, 0, 0.12)" if is_drop else "rgba(0, 255, 0, 0.12)"
            call_type = s.get("type", "CALL")
            label = f"{call_type} Drop ({s.get('id')})" if is_drop else f"{call_type} Completed"

            fig.add_vrect(
                x0=start_dt, x1=end_dt,
                fillcolor=color, opacity=1,
                layer="below", line_width=1.5,
                line_color="rgba(255,0,0,0.4)" if is_drop else "rgba(0,255,0,0.4)",
                annotation_text=label, annotation_position="top left",
                annotation_font=dict(size=11, color="red" if is_drop else "green")
            )
        except: pass

    sip_data = report_data.get("ims_sip_data", [])
    if sip_data:
        sip_errors = [m for m in sip_data if m.get("is_error")]
        if sip_errors:
            err_times, err_texts = [], []
            for e in sip_errors:
                try:
                    dt = pd.to_datetime(f"{current_year}-{e.get('time')[:14]}", format='%Y-%m-%d %H:%M:%S')
                    err_times.append(dt)
                    err_texts.append(f"{e.get('method_code', 'SIP Error')} ({e.get('direction', 'Tx')})")
                except: pass

            if err_times:
                fig.add_trace(go.Scatter(
                    x=err_times, y=[-135] * len(err_times),
                    mode='markers+text',
                    name='SIP Error (4xx~6xx)',
                    marker=dict(symbol='x', color='#d32f2f', size=11, line=dict(width=2)),
                    text=err_texts,
                    textposition="top center",
                    textfont=dict(color='#d32f2f', size=10, weight='bold')
                ))

    fig.update_layout(
        yaxis_title="Received Signal Strength (RSRP dBm)",
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

def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _safe_time_series(df, time_col="time"):
    if df.empty or time_col not in df.columns:
        return df

    current_year = datetime.datetime.now().year

    def parse_time(value):
        value = str(value).strip()
        if len(value) > 5 and value[2] == "-" and value.count("-") == 1:
            value = f"{current_year}-{value}"
        return pd.to_datetime(value, errors="coerce")

    df = df.copy()
    df["time_dt"] = df[time_col].apply(parse_time)
    return df.dropna(subset=["time_dt"]).sort_values("time_dt")

def render_internet_stall_analyzer(current_base, result_dir="./result"):
    st.subheader("Data Stall & Internet Connectivity Analysis")

    if not current_base:
        st.info("Target file is not selected.")
        return

    path = os.path.join(result_dir, f"{current_base}_internet_stall.json")
    data = _load_json(path, {})

    if not data:
        st.info(f"Data Stall analysis result not found. Expected: `{path}`")
        return

    kpi = data.get("kpi", {}) or {}
    root_summary = data.get("root_cause_summary", {}) or {}
    windows = data.get("stall_windows", []) or []
    timeline = data.get("timeline", []) or []

    st.markdown("### 1) Health Summary")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stall Window", kpi.get("stall_window_count", 0))
    c2.metric("High Risk", kpi.get("high_risk_window_count", 0))
    c3.metric("Primary Cause", kpi.get("primary_root_cause_candidate", "UNKNOWN"))
    c4.metric("Timeline Events", kpi.get("total_timeline_events", 0))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("DNS Issue", kpi.get("dns_issue_count", 0))
    c6.metric("Validation Fail", kpi.get("validation_fail_count", 0))
    c7.metric("Data Stall", kpi.get("data_stall_count", 0))
    c8.metric("RF Warning", kpi.get("rf_warning_count", 0))

    c9, c10, c11 = st.columns(3)
    c9.metric("DataCall Fail/Drop", kpi.get("data_call_fail_or_drop_count", 0))
    c10.metric("TCP/TLS Timeout", kpi.get("tcp_tls_timeout_count", 0))
    c11.metric("Power/Idle Hint", kpi.get("power_idle_hint_count", 0))

    st.divider()

    st.markdown("### 2) Root Cause Candidate")

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
            title="Root Cause Candidate Distribution",
            hover_data=["high", "medium", "low", "example_time", "example_trigger"]
        )
        st.plotly_chart(fig_root, width="stretch")
    else:
        st.info("No root cause candidates formulated.")

    st.divider()

    st.markdown("### 3) Layer Event Timeline")

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
                title="Stall-Related Events Across Network Layers"
            )
            fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")
            st.plotly_chart(fig, width="stretch")

            with st.expander("Raw Timeline Table", expanded=False):
                display_cols = [c for c in ["time", "layer", "event_type", "severity", "reason", "net_id", "apn", "cid", "raw"] if c in timeline_df.columns]
                st.dataframe(timeline_df[display_cols], width="stretch")
        else:
            st.warning("Timeline parsing failed for internet stall data.")
    else:
        st.info("No timeline events to display.")

    st.divider()

    st.markdown("### 4) High Risk Stall Windows")

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
            "Inspect Details for Stall Window",
            window_df["idx"].tolist(),
            format_func=lambda i: f"#{i} | {windows[i].get('center_time')} | {windows[i].get('trigger')}"
        )

        selected = windows[selected_idx]
        st.markdown("**Root Cause Candidates**")
        st.json(selected.get("root_cause_candidates", []))

        related = selected.get("related_events", []) or []
        related_df = pd.DataFrame(related)
        if not related_df.empty:
            related_df = _safe_time_series(related_df, "time")
            display_cols = [c for c in ["time", "layer", "event_type", "severity", "reason", "apn", "cid", "raw"] if c in related_df.columns]
            st.dataframe(related_df[display_cols], width="stretch")

            with st.expander("Raw Context Around Trigger Event", expanded=False):
                for e in related[:20]:
                    st.markdown(f"**[{e.get('time')}] {e.get('layer')} / {e.get('event_type')}**")
                    st.code(e.get("raw", ""), language="log")
                    ctx = e.get("context_before", [])
                    if ctx:
                        st.caption("Context before")
                        st.code("\n".join(ctx[-10:]), language="log")
    else:
        st.info("No stall windows identified.")

    st.divider()

    st.markdown("### 5) Details by Network Layer")

    if timeline_df.empty:
        return

    tab_dns, tab_datacall, tab_validation, tab_rf, tab_tcp, tab_power = st.tabs(
        ["DNS", "DataCall/Stall", "Validation", "RF", "TCP/TLS", "Power"]
    )

    def render_layer(tab, layer_names):
        with tab:
            layer_df = timeline_df[timeline_df["layer"].isin(layer_names)].copy()
            if layer_df.empty:
                st.info(f"No events documented for {layer_names} layer.")
                return

            count_df = layer_df["event_type"].value_counts().reset_index()
            count_df.columns = ["event_type", "count"]

            fig = px.bar(count_df, x="event_type", y="count", title=f"Event Distribution: {'/'.join(layer_names)}")
            st.plotly_chart(fig, width="stretch")

            display_cols = [c for c in ["time", "event_type", "severity", "reason", "net_id", "apn", "cid", "raw"] if c in layer_df.columns]
            st.dataframe(layer_df[display_cols], width="stretch")

    render_layer(tab_dns, ["DNS"])
    render_layer(tab_datacall, ["DATA_CALL", "DATA_STALL"])
    render_layer(tab_validation, ["VALIDATION", "NETWORK", "ROUTING"])
    render_layer(tab_rf, ["RF"])
    render_layer(tab_tcp, ["TCP_TLS"])
    render_layer(tab_power, ["POWER"])

def render_nitz_timeline(nitz_data):
    if not nitz_data:
        st.info("NITZ 수신 이력이 없습니다.")
        return

    st.markdown("### NITZ 타임존 및 변동 분석")
    df = pd.DataFrame(nitz_data)

    # 1. 시간 파싱
    df['log_time_dt'] = pd.to_datetime(df['log_time'], errors='coerce')
    df = df.dropna(subset=['log_time_dt']).sort_values('log_time_dt')

    # UTC 오프셋 숫자 추출 (예: UTC+9 -> 9.0)
    df['offset_num'] = df['timezone'].str.extract(r'UTC([+-]?\d+)').astype(float).fillna(0.0)

    # 실제 값이 바뀐 지점 추출
    df_changes = df[df['timezone'] != df['timezone'].shift()].copy()

    # 유지 시간 계산 및 노이즈 필터링
    if len(df_changes) > 1:
        df_changes['duration_sec'] = df_changes['log_time_dt'].diff().shift(-1).dt.total_seconds().fillna(601)
        significant_changes = df_changes[df_changes['duration_sec'] > 600].copy()
    else:
        significant_changes = df_changes

    duration_days = max(1, (df['log_time_dt'].max() - df['log_time_dt'].min()).days)
    flip_count = max(0, len(significant_changes) - 1)

    is_unstable = False
    if len(significant_changes) >= 3:
        significant_changes['rapid_check'] = significant_changes['log_time_dt'].diff(periods=2).dt.total_seconds()
        if not significant_changes[significant_changes['rapid_check'].abs() < 3600].empty:
            is_unstable = True

    # 2. 요약 KPI 대시보드
    col1, col2, col3 = st.columns(3)
    with col1: st.metric("최초 타임존", df['timezone'].iloc[0])
    with col2: st.metric("최종 타임존", df['timezone'].iloc[-1], delta="타임존 변경됨" if flip_count > 0 else "유지됨")
    with col3:
        status = "불안정 (핑퐁)" if is_unstable else "장기 체류" if duration_days > 30 else "안정"
        st.metric("타임존 변경 횟수", f"{flip_count} 회", delta=status, delta_color="inverse" if is_unstable else "normal")

    st.divider()

    # ==========================================
    # 3. UTC 오프셋 기반 세계 지도 (Geo Map) 매핑
    # ==========================================
    # 대표적인 UTC 오프셋별 위도/경도/지역명 매핑 (자주 테스트하는 로밍 지역 위주)
    UTC_GEO_MAP = {
        9.0: {"lat": 37.5665, "lon": 126.9780, "name": "Korea/Japan (UTC+9)"},
        8.0: {"lat": 39.9042, "lon": 116.4074, "name": "China/Singapore (UTC+8)"},
        7.0: {"lat": 13.7563, "lon": 100.5018, "name": "SE Asia (UTC+7)"},
        5.5: {"lat": 28.6139, "lon": 77.2090, "name": "India (UTC+5.5)"},
        4.0: {"lat": 25.2048, "lon": 55.2708, "name": "UAE/Dubai (UTC+4)"},
        3.0: {"lat": 55.7558, "lon": 37.6173, "name": "Russia/Middle East (UTC+3)"},
        2.0: {"lat": 48.8566, "lon": 2.3522, "name": "Central Europe (UTC+2)"},
        1.0: {"lat": 51.5074, "lon": -0.1278, "name": "UK/Western Europe (UTC+1)"},
        0.0: {"lat": 51.4826, "lon": 0.0077, "name": "GMT/UTC (UTC+0)"},
        -4.0: {"lat": -23.5505, "lon": -46.6333, "name": "Brazil/SA (UTC-4)"},
        -5.0: {"lat": 40.7128, "lon": -74.0060, "name": "US Eastern (UTC-5)"},
        -8.0: {"lat": 34.0522, "lon": -118.2437, "name": "US Pacific (UTC-8)"},
        -10.0: {"lat": 21.3069, "lon": -157.8583, "name": "Hawaii (UTC-10)"}
    }

    # 현재 로그에 존재하는 모든 고유 UTC 오프셋 추출
    unique_offsets = df['offset_num'].unique()
    geo_data = []

    for offset in unique_offsets:
        # 맵에 정확히 일치하는 오프셋이 없으면 근사치(round) 지역으로 맵핑
        closest_offset = min(UTC_GEO_MAP.keys(), key=lambda k: abs(k - offset))
        geo_info = UTC_GEO_MAP[closest_offset]

        # 해당 오프셋에 머문 로그 개수 계산 (원의 크기 조절용)
        count = len(df[df['offset_num'] == offset])

        geo_data.append({
            "offset": f"UTC{'+' if offset > 0 else ''}{offset}",
            "lat": geo_info["lat"],
            "lon": geo_info["lon"],
            "region": geo_info["name"],
            "count": count
        })

    geo_df = pd.DataFrame(geo_data)

    # UI 레이아웃 분할 (좌측: 타임라인, 우측: 지도)
    col_chart, col_map = st.columns([1, 1])

    with col_chart:
        st.markdown("** 시간대별 오프셋(UTC) 변화 타임라인**")
        fig_line = px.line(df, x='log_time_dt', y='offset_num', line_shape='hv', markers=True,
                           labels={'log_time_dt': '시간', 'offset_num': 'UTC 오프셋 (+/-)'})
        fig_line.update_traces(line_color='#2ca02c')
        fig_line.update_layout(height=350, margin=dict(t=30, b=20, l=10, r=10))
        st.plotly_chart(fig_line, use_container_width=True)

    with col_map:
        st.markdown("**타임존 기반 예상 체류 지역 (Estimated Region)**")
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
            st.plotly_chart(fig_map, use_container_width=True)
        else:
            st.info("지도에 표시할 좌표 데이터가 없습니다.")

    # 4. 상세 변화 이력 표 출력
    if not df_changes.empty:
        with st.expander("🔍 상세 타임존 변경 이력 보기"):
            display_df = df_changes[['log_time', 'timezone', 'nitz_raw']].rename(
                columns={'log_time': '변경 시간', 'timezone': '타임존', 'nitz_raw': '원본 NITZ 데이터'}
            )
            st.dataframe(display_df, hide_index=True, width="stretch")

def render_rilj_transactions(current_base=None):
    st.subheader("RILJ (Modem ↔ AP) Transaction Analysis")

    if not current_base:
        return

    report_path = f"./result/{current_base}_report.json"
    if not os.path.exists(report_path):
        return

    with open(report_path, 'r', encoding='utf-8') as f:
        report_data = json.load(f)

    rilj_data = report_data.get("rilj_transactions", {})
    completed = rilj_data.get("completed", [])
    timeouts = rilj_data.get("timeouts", [])
    unsol = rilj_data.get("unsol", [])

    if not completed and not timeouts and not unsol:
        st.info("No RILJ transaction data found in the session.")
        return

    SLOW_THRESHOLD = 500

    total_req = len(completed) + len(timeouts)
    errors = len([c for c in completed if c.get("is_error")])
    slows = len([c for c in completed if c.get("latency_ms", 0) > SLOW_THRESHOLD])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total RIL Requests", f"{total_req}")
    c2.metric("Timeouts", f"{len(timeouts)}", delta="Critical" if timeouts else "Normal", delta_color="inverse")
    c3.metric("Error Responses (Fail)", f"{errors}", delta="Error" if errors else "Normal", delta_color="inverse")
    c4.metric("Unsolicited Events (UNSL)", f"{len(unsol)}", delta="Modem Event" if unsol else "Normal")

    st.divider()

    tab_anomaly, tab_unsol = st.tabs(["Abnormal Transactions (Error/Delay/Timeout)", "Unsolicited Events (UNSL)"])

    with tab_anomaly:
        abnormal_rows = []
        for t in timeouts:
            abnormal_rows.append({
                "Status": "TIMEOUT", "Time": t["time"], "Command": t["command"],
                "Latency(ms)": "N/A", "Error Code": "NO_RESPONSE", "Details": t["details"]
            })

        for c in completed:
            if c.get("is_error"):
                abnormal_rows.append({
                    "Status": "ERROR", "Time": c["start_time"], "Command": c["command"],
                    "Latency(ms)": c["latency_ms"], "Error Code": c["error_msg"],
                    "Details": f"Req: {c['req_details']} | Resp: {c['resp_details']}"
                })
            elif c.get("latency_ms", 0) > SLOW_THRESHOLD:
                abnormal_rows.append({
                    "Status": "SLOW", "Time": c["start_time"], "Command": c["command"],
                    "Latency(ms)": c["latency_ms"], "Error Code": "SUCCESS", "Details": c["req_details"]
                })

        if abnormal_rows:
            df_abnormal = pd.DataFrame(abnormal_rows).sort_values(by="Time")
            st.dataframe(df_abnormal, width="stretch", hide_index=True)
        else:
            st.success("No timeouts, errors, or significant transaction delays (>500ms) detected. Normal state.")

    with tab_unsol:
        if unsol:
            st.markdown(f"**Modem Real-time Status Update History (Total: {len(unsol)})**")
            df_unsol = pd.DataFrame(unsol).sort_values(by="time")
            df_unsol.columns = ["Time", "Command", "Details"]
            st.dataframe(df_unsol, width="stretch", hide_index=True)
        else:
            st.info("No UNSL event logs collected.")

def render_binder_proxy_leaks(binder_warnings):
    # (앞부분 json 파싱 및 타입 검사 방어 코드는 이전과 동일하게 유지)
    if isinstance(binder_warnings, str):
        try: binder_warnings = json.loads(binder_warnings)
        except: return
    if not isinstance(binder_warnings, list):
        binder_warnings = [binder_warnings]

    histograms = []
    for w in binder_warnings:
        if isinstance(w, str):
            try: w = json.loads(w)
            except: continue

        # 💡 변경점: 타입을 BINDER_PROXY_HISTOGRAM 으로 스캔
        if isinstance(w, dict) and w.get("type") in ("BINDER_PROXY_HISTOGRAM", "BINDER_PROXY_LEAK"):
            histograms.append(w)

    if not histograms:
        return

    st.markdown("### 📊 Binder Proxy Histogram Analysis")

    for idx, hist in enumerate(histograms):
        max_count = hist.get("max_count", 0)
        is_leak = max_count > 1000 # 💡 판단은 UI 단계에서 수행

        # 💡 임계치에 따른 동적 UI 렌더링
        if is_leak:
            st.error(f"**🚨 [발생 시간: {hist.get('time', 'Unknown')}] 시스템 리소스 누수(Leak) 임계치 초과!**\n\n최대 Proxy 객체 수가 {max_count}개로, 특정 인터페이스의 등록/해제 불균형(메모리 릭)이 의심되어 am_kill 위험이 높습니다.")
        else:
            st.info(f"**ℹ️ [발생 시간: {hist.get('time', 'Unknown')}] Binder Proxy 객체 상태** (최대 {max_count}개 기록됨 - 정상 범위)")

        # (이하 raw 데이터를 파싱해서 Plotly 바 차트로 그리는 로직은 이전과 완전히 동일하게 유지)
        raw_lines = hist.get("raw", "").split('\n')
        data = []
        for line in raw_lines:
            match = re.search(r'([a-zA-Z_][a-zA-Z0-9\.\$]+)\s*x\s*(\d+)', line)
            if match:
                full_class = match.group(1)
                short_class = full_class.split('.')[-1]
                count = int(match.group(2))
                data.append({"Class": short_class, "FullClass": full_class, "Count": count})

        if data:
            df = pd.DataFrame(data)
            df = df.sort_values(by="Count", ascending=True)

            fig = px.bar(
                df,
                x="Count",
                y="Class",
                orientation='h',
                text="Count",
                hover_data=["FullClass"],
                color="Count",
                color_continuous_scale="Reds",
                title="Top 10 Binder Proxy Descriptor Histogram"
            )

            fig.update_layout(
                xaxis_title="Proxy Object Count",
                yaxis_title="Target Interface",
                height=400,
                margin=dict(l=20, r=20, t=40, b=20)
            )
            fig.update_traces(textposition='outside')

            st.plotly_chart(fig, use_container_width=True)
