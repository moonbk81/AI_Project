from datetime import datetime
import os
import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

def render_ntn_advanced_fw_analyzer(current_base):
    st.subheader("NTN 로밍 정책 및 UI 상태")

    if not current_base:
        st.info("분석 대상 파일을 선택해 주세요.")
        return

    file_path = f"./result/{current_base}_ntn.json"
    if not os.path.exists(file_path):
        st.info("NTN 분석 결과 파일이 없습니다.")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not data:
        st.error("로그에서 추출된 NTN 데이터가 없습니다.")
        return

    ntn_df = pd.DataFrame(data)

    real_ntn_events = ntn_df[ntn_df['event_type'] != 'RADIO_POWER']
    if real_ntn_events.empty:
        st.info("NTN 관련 이벤트가 없습니다.")
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
    col1.metric("대상 위성 PLMN", latest_plmn)
    col2.metric("적용 데이터 정책", latest_policy)
    col3.metric("상태바 아이콘", ui_icon_status)

    st.divider()

    st.markdown("**NTN 진입 및 상태 전이 타임라인**")
    chart_df = clean_df[clean_df['event_type'] != 'DATA_POLICY'].copy()

    if not chart_df.empty:
        current_year = datetime.now().year
        chart_df['time_dt'] = pd.to_datetime(str(current_year) + "-" + chart_df['time'], errors='coerce')
        chart_df = chart_df.sort_values('time_dt')

        fig = px.scatter(
            chart_df, x='time_dt', y='event_type', color='event_type',
            hover_data=['ntn_plmn', 'last_ntn_mode', 'ntn_mode', 'is_hysteresis', 'power_state'],
            title="NTN 상태 전이 이벤트",
            labels={'time_dt': '이벤트 시간', 'event_type': '이벤트 유형'}
        )
        fig.update_traces(marker=dict(size=14, symbol='diamond', line=dict(width=2, color='DarkSlateGrey')))
        fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")
        order = ['RADIO_POWER', 'PLMN_MATCH', 'HYSTERESIS_ICON_ON', 'NTN_MODE_NOTIFY']
        fig.update_layout(yaxis={'categoryorder': 'array', 'categoryarray': order})
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("표시할 타임라인 이벤트가 없습니다.")

    st.markdown("**NTN 상태 전이 상세**")
    display_cols = [col for col in ['time', 'event_type', 'power_state', 'ntn_plmn', 'last_ntn_mode', 'ntn_mode', 'is_hysteresis', 'data_policy'] if col in clean_df.columns]
    final_table_df = clean_df[display_cols].fillna("-")
    st.dataframe(final_table_df, width="stretch")

def render_sat_at_analyzer(current_base=None):
    st.subheader("위성 모뎀 제어 상태")

    if not current_base: return
    file_path = f"./result/{current_base}_sat_at.json"
    if not os.path.exists(file_path): return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    metrics = data.get("metrics", {})
    flow = data.get("call_flow", [])
    reg_history = data.get("registration_history", [])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("위성 ARFCN", metrics.get("arfcn", "N/A"))
    c2.metric("등록 상태", metrics.get("current_reg_state", "Unknown"))

    call_total = metrics.get('calls_total', 0)
    call_fail = metrics.get('calls_dropped_or_failed', 0)
    c3.metric("음성 통화(전체/실패)", f"{call_total} / {call_fail}",
              delta=f"{call_fail}건 실패" if call_fail > 0 else "정상", delta_color="inverse")

    sms_rx = metrics.get('sms_rx', 0)
    sms_tx_succ = metrics.get('sms_tx_success', 0)
    sms_tx_fail = metrics.get('sms_tx_fail', 0)
    c4.metric("SMS(Rx/Tx 성공/Tx 실패)", f"{sms_rx} / {sms_tx_succ} / {sms_tx_fail}",
              delta=f"{sms_tx_fail}건 실패" if sms_tx_fail > 0 else "정상", delta_color="inverse")
    st.divider()

    if reg_history:
        st.write("#### 위성망 등록 이력")
        df_reg = pd.DataFrame(reg_history)

        fig_reg = px.line(
            df_reg, x="time", y="status_str", markers=True,
            hover_data=["raw"],
            labels={"time": "시간", "status_str": "상태"}
        )
        fig_reg.update_traces(line_shape='hv', line_color='#E64A19', marker=dict(size=8))
        fig_reg.update_yaxes(categoryorder='array', categoryarray=["Deregistered (0)", "Searching", "Registered (1)"])
        fig_reg.update_layout(height=250, margin=dict(t=20, b=20))
        st.plotly_chart(fig_reg, width="stretch")
        st.divider()

    if flow:
        st.write("#### 통화 제어 시퀀스(AP ↔ RIL ↔ Modem)")
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
