import os
import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import datetime
import numpy as np

def render_call_history_summary(df):
    """전체 통화 세션 (Call History) 차트 및 표 렌더링"""
    st.subheader("통화 세션 현황")
    if 'log_type' in df.columns:
        call_df = df[df['log_type'] == 'Call_Session']
        if not call_df.empty:
            display_cols = [col for col in ['time', 'slot', 'status', 'fail_reason', 'call_id', 'source_file'] if col in call_df.columns]
            clean_call_df = call_df[display_cols].fillna("-").sort_values(by='time', ascending=False)

            col_chart, col_table = st.columns([1, 2])
            with col_chart:
                st.markdown("**통화 상태 분포**")
                if 'status' in call_df.columns:
                    fig_call = px.pie(call_df, names='status', hole=0.4, title="통화 성공/실패 비율")
                    st.plotly_chart(fig_call, width="stretch")
                else:
                    st.info("Status 데이터 필드가 누락되었습니다.")
            with col_table:
                st.markdown(f"**통화 이력 상세(총 {len(clean_call_df)}건)**")
                st.dataframe(clean_call_df, width="stretch", height=400)
        else:
            st.info("현재 분석 세션에 Call_Session 로그가 존재하지 않습니다.")

def render_signal_level_timeline(df):
    st.subheader("RAT별 신호 세기 추이")

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
                title="RAT별 Signal Level 변화",
                hover_data={'hover_detail': True, 'raw_info': True}
            )

            fig.update_traces(
                hovertemplate="<b>%{customdata[0]}</b><br>Level: %{y}<br>Details:<br>%{customdata[1]}<extra></extra>",
                customdata=sig_df[['rat', 'hover_detail']].values
            )

            st.plotly_chart(fig, width="stretch")
        else:
            st.info("Signal Level 데이터가 없습니다.")

def render_service_state_timeline(df):
    st.subheader("망 등록 상태 추이")

    if 'log_type' not in df.columns:
        return

    oos_df = df[df['log_type'] == 'OOS_Event'].copy()

    if oos_df.empty:
        st.success("IN_SERVICE 상태가 유지되었으며, OOS 또는 등록 상태 전이가 감지되지 않았습니다.")
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
        st.info("표시할 주요 상태 변화가 없습니다.")
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

    chart_height = max(300, 200 * clean_df['Slot'].nunique())
    fig.update_layout(height=chart_height, hovermode="x unified", margin=dict(t=50, b=20))

    st.plotly_chart(fig, width="stretch")

def render_data_call_analyzer(data):
    st.subheader("Data Call 설정 현황")

    if not data or len(data) == 0:
        st.info("SETUP_DATA_CALL 이력이 없습니다.")
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
    col1.metric("연결 시도", f"{total_calls}")
    col2.metric("성공률", f"{success_rate:.1f} %")
    col3.metric("실패 건수", f"{fail_calls}")
    col4.metric("평균 설정 지연", f"{avg_latency:.0f} ms")

    st.divider()

    st.markdown("**Data Call 상태 전이**")

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
            title="APN별 Data Call 상태 전이",
            labels={'req_time_dt': '시간', 'apn': 'APN'}
        )
        fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")
        st.plotly_chart(fig, width="stretch", key="datacall_scatter_chart")
    else:
        st.info("표시할 이벤트가 없습니다.")

    st.markdown("**Data Call 상세 이력**")
    st.dataframe(df, width="stretch")

def render_ims_sip_flow(current_base=None):
    st.subheader("VoLTE / IMS SIP 흐름")

    if not current_base: return
    file_path = f"./result/{current_base}_ims_sip.json"
    if not os.path.exists(file_path):
        st.info("SIP 메시지 로그가 없습니다.")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not data:
        st.info("기록된 SIP transaction이 없습니다.")
        return

    sip_df = pd.DataFrame(data)

    total_msgs = len(sip_df)
    error_msgs = len(sip_df[sip_df['is_error'] == True])

    col1, col2, col3 = st.columns(3)
    col1.metric("SIP transaction", f"{total_msgs}")
    col2.metric("SIP 오류 응답(4xx~6xx)", f"{error_msgs}", delta="이상" if error_msgs > 0 else "정상", delta_color="inverse" if error_msgs > 0 else "normal")

    try:
        sip_df['time_dt'] = pd.to_datetime(sip_df['time'], format='%m-%d %H:%M:%S.%f', errors='coerce')
        invite_time = sip_df[sip_df['method_code'].str.contains('INVITE', na=False)]['time_dt'].min()
        ok_time = sip_df[sip_df['method_code'].str.contains('200 OK', na=False)]['time_dt'].max()
        if pd.notna(invite_time) and pd.notna(ok_time) and ok_time >= invite_time:
            latency_ms = int((ok_time - invite_time).total_seconds() * 1000)
            col3.metric("통화 설정 지연(Max)", f"{latency_ms} ms")
        else:
            col3.metric("통화 설정 지연(Max)", "N/A")
    except:
        col3.metric("통화 설정 지연(Max)", "N/A")

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
            ticktext=['UE', 'IMS 망'],
            tickfont=dict(size=15, weight='bold'),
            range=[-0.2, 1.2], side="top", showgrid=False, zeroline=False
        ),
        yaxis=dict(showticklabels=False, range=[0, len(sip_df)+1], showgrid=False, zeroline=False),
        height=max(400, len(sip_df) * 45),
        margin=dict(l=120, r=50, t=80, b=20),
        plot_bgcolor='white', hovermode=False
    )

    st.plotly_chart(fig, width="stretch")

    st.markdown("**SIP 메시지 상세**")
    display_cols = ['time', 'direction', 'msg_type', 'method_code', 'tid', 'cseq', 'raw_log']
    st.dataframe(sip_df[display_cols], width="stretch")

def render_rilj_transactions(current_base=None):
    st.subheader("RILJ transaction 현황")

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
        st.info("RILJ transaction 데이터가 없습니다.")
        return

    SLOW_THRESHOLD = 500

    total_req = len(completed) + len(timeouts)
    errors = len([c for c in completed if c.get("is_error")])
    slows = len([c for c in completed if c.get("latency_ms", 0) > SLOW_THRESHOLD])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("RIL 요청", f"{total_req}")
    c2.metric("Timeout", f"{len(timeouts)}", delta="주의" if timeouts else "정상", delta_color="inverse")
    c3.metric("오류 응답", f"{errors}", delta="오류" if errors else "정상", delta_color="inverse")
    c4.metric("UNSL 이벤트", f"{len(unsol)}", delta="Modem 이벤트" if unsol else "정상")

    st.divider()

    tab_anomaly, tab_unsol = st.tabs(["이상 transaction", "UNSL 이벤트"])

    with tab_anomaly:
        abnormal_rows = []
        for t in timeouts:
            abnormal_rows.append({
                "Status": "TIMEOUT", "Time": t["time"], "Command": t["command"],
                "Latency(ms)": None, "Error Code": "NO_RESPONSE", "Details": t["details"]
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
            st.success("Timeout, 오류 응답, 500ms 초과 지연이 감지되지 않았습니다.")

    with tab_unsol:
        if unsol:
            st.markdown(f"**Modem 상태 업데이트 이력(총 {len(unsol)}건)**")
            df_unsol = pd.DataFrame(unsol).sort_values(by="time")
            df_unsol.columns = ["Time", "Command", "Details"]
            st.dataframe(df_unsol, width="stretch", hide_index=True)
        else:
            st.info("수집된 UNSL 이벤트 로그가 없습니다.")

def render_integrated_rf_call_timeline(report_data):
    st.subheader("통화 상태 및 RF 환경 통합 타임라인")
    st.markdown("통화 구간, RSRP 변화, SIP 오류 시점을 함께 표시합니다.")

    signal_history = report_data.get("signal_level_history", [])
    if not signal_history:
        st.info("통합 타임라인을 구성할 RF 신호 이력이 부족합니다.")
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
            name='RSRP(dBm)',
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
            label = f"{call_type} 실패/Drop ({s.get('id')})" if is_drop else f"{call_type} 완료"

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
                    name='SIP 오류(4xx~6xx)',
                    marker=dict(symbol='x', color='#d32f2f', size=11, line=dict(width=2)),
                    text=err_texts,
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
    if not nitz_data:
        st.info("NITZ 수신 이력이 없습니다.")
        return

    st.markdown("### NITZ 타임존 변동")
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
        st.markdown("**UTC 오프셋 변화 타임라인**")
        fig_line = px.line(df, x='log_time_dt', y='offset_num', line_shape='hv', markers=True,
                           labels={'log_time_dt': '시간', 'offset_num': 'UTC 오프셋 (+/-)'})
        fig_line.update_traces(line_color='#2ca02c')
        fig_line.update_layout(height=350, margin=dict(t=30, b=20, l=10, r=10))
        st.plotly_chart(fig_line, width="stretch")

    with col_map:
        st.markdown("**타임존 기반 예상 지역**")
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

    # 4. 상세 변화 이력 표 출력
    if not df_changes.empty:
        with st.expander("상세 타임존 변경 이력"):
            display_df = df_changes[['log_time', 'timezone', 'nitz_raw']].rename(
                columns={'log_time': '변경 시간', 'timezone': '타임존', 'nitz_raw': '원본 NITZ 데이터'}
            )
            st.dataframe(display_df, hide_index=True, width="stretch")
