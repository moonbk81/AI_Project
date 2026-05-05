# ui_components.py
import os, json
import streamlit as st
import plotly.express as px
import pandas as pd
import plotly.graph_objects as go
import datetime

import json
import ast

def parse_raw_logs(raw_data):
    """
    다양한 형태(JSON, 파이썬 리스트, 특수문자가 섞인 기형적 배열, 일반 텍스트 등)의
    로그 데이터를 안전하게 파싱하여 리스트 형태로 반환합니다.
    """
    if isinstance(raw_data, list):
        raw_logs = raw_data
    elif isinstance(raw_data, str):
        raw_data_clean = raw_data.strip()
        try:
            # 1단계: 정석적인 JSON 파싱
            raw_logs = json.loads(raw_data_clean)
            if not isinstance(raw_logs, list):
                raw_logs = [raw_data_clean]
        except Exception:
            try:
                # 2단계: 파이썬 문법의 리스트 문자열 (ast 모듈)
                raw_logs = ast.literal_eval(raw_data_clean)
                if not isinstance(raw_logs, list):
                    raw_logs = [raw_data_clean]
            except Exception:
                # 3단계: 기형적인 배열 문자열 강제 분리
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
                    # 4단계: 최후의 수단 (줄바꿈 분리)
                    clean_text = raw_data_clean.replace('\\n', '\n').replace('\\r', '')
                    raw_logs = clean_text.split('\n')
    else:
        raw_logs = []

    # 빈 줄 제거 후 반환
    return [log for log in raw_logs if str(log).strip()]

def render_dns_analysis_chart(df):
    """패키지별 DNS 차단/실패 상세 원인 분석 차트 렌더링"""
    st.subheader("🎯 패키지별 DNS 차단/실패 상세 원인 분석")

    dns_df = df[df['log_type'] == 'DNS_Query'].copy()

    if not dns_df.empty and 'return_code' in dns_df.columns and 'app_name' in dns_df.columns:
        error_dns_df = dns_df[~dns_df['return_code'].isin(['0', 'SUCCESS'])]

        if not error_dns_df.empty:
            dns_corr = error_dns_df.groupby(['app_name', 'return_code']).size().reset_index(name='count')
            fig_dns_corr = px.bar(
                dns_corr, x='app_name', y='count', color='return_code',
                title="어떤 앱이 어떤 이유로 DNS 통신에 실패했는가?",
                labels={'app_name': '패키지명 (App)', 'count': '발생 횟수', 'return_code': '에러 코드'},
                barmode='stack', color_discrete_sequence=px.colors.qualitative.Pastel
            )
            fig_dns_corr.update_layout(xaxis_tickangle=-45, height=500)

            c1, c2 = st.columns([2, 1])
            with c1:
                st.plotly_chart(fig_dns_corr, use_container_width=True)
            with c2:
                st.markdown("**📊 상세 에러 매트릭스**")
                pivot_df = error_dns_df.pivot_table(index='app_name', columns='return_code', aggfunc='size', fill_value=0)
                st.dataframe(pivot_df, use_container_width=True)
        else:
            st.success("🎉 분석된 로그 내에 DNS 차단/실패 기록이 없습니다. (모두 정상)")
    else:
        st.warning("⚠️ DNS 로그 데이터가 없거나 컬럼을 추출하지 못했습니다.")

def render_battery_thermal_chart(df):
    """발열 및 Wakelock 분석 차트 렌더링"""
    st.subheader("🔥 발열 및 배터리 드레인(Wakelock) 분석")

    thermal_df = df[df['log_type'] == 'Thermal_Stat'].copy()
    wl_df = df[df['log_type'] == 'Wakelock_Stat'].copy()

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**🔋 Wakelock (잠들지 못하는 앱) Top 10**")
        if not wl_df.empty:
            wl_df['times'] = pd.to_numeric(wl_df['times'], errors='coerce')
            fig_wl = px.bar(
                wl_df, x='app_name', y='times', title="앱별 AP 기상 강제 호출 횟수",
                hover_data=['duration'], labels={'app_name': '패키지명', 'times': '깨운 횟수', 'duration': '점유 시간'},
                color='times', color_continuous_scale='Blues'
            )
            fig_wl.update_layout(xaxis_tickangle=-45, height=400)
            st.plotly_chart(fig_wl, use_container_width=True)
        else:
            st.info("Wakelock 기록이 없습니다.")

    with c2:
        st.markdown("**🌡️ 기기 내부 주요 센서 발열(Thermal) 상태**")
        if not thermal_df.empty:
            thermal_df['temperature'] = pd.to_numeric(thermal_df['temperature'], errors='coerce')
            thermal_df = thermal_df.dropna(subset=['temperature']).sort_values(by='temperature', ascending=False)
            fig_th = px.bar(
                thermal_df, x='sensor', y='temperature', title="센서별 현재 온도 (°C)",
                color='temperature', color_continuous_scale=[(0, "green"), (0.5, "orange"), (1, "red")],
                range_color=[30, 50], labels={'sensor': '센서명', 'temperature': '온도(°C)'}
            )
            fig_th.add_hline(y=40, line_dash="dot", line_color="red", annotation_text="발열 경계선 (40°C)")
            fig_th.update_layout(xaxis_tickangle=-45, height=400)
            st.plotly_chart(fig_th, use_container_width=True)
        else:
            st.info("발열(Thermal) 기록이 없습니다.")

def render_call_history_summary(df):
    """전체 통화 세션 (Call History) 차트 및 표 렌더링"""
    st.subheader("📞 전체 통화 세션 (Call History) 요약")
    if 'log_type' in df.columns:
        call_df = df[df['log_type'] == 'Call_Session']
        if not call_df.empty:
            display_cols = [col for col in ['time', 'slot', 'status', 'fail_reason', 'call_id', 'source_file'] if col in call_df.columns]
            clean_call_df = call_df[display_cols].fillna("-").sort_values(by='time', ascending=False)

            col_chart, col_table = st.columns([1, 2])
            with col_chart:
                st.markdown("**📊 통화 상태(Status) 비율**")
                if 'status' in call_df.columns:
                    fig_call = px.pie(call_df, names='status', hole=0.4, title="전체 Call 성공/실패 분포")
                    st.plotly_chart(fig_call, use_container_width=True)
                else:
                    st.info("상태(status) 데이터가 없습니다.")
            with col_table:
                st.markdown(f"**📋 전체 통화 이력 (총 {len(clean_call_df)}건)**")
                st.dataframe(clean_call_df, use_container_width=True, height=400)
        else:
            st.info("현재 DB에 적재된 통화(Call_Session) 로그가 없습니다.")

def render_signal_level_timeline(df):
    """RAT별 안테나 수신 레벨 타임라인 렌더링"""
    st.subheader("📶 RAT별 안테나 수신 레벨 타임라인")
    if 'log_type' in df.columns:
        sig_df = df[df['log_type'] == 'Signal_Level'].copy()
        if not sig_df.empty:
            if 'level' not in sig_df.columns and 'max_level' in sig_df.columns:
                sig_df['level'] = sig_df['max_level']
            if 'rat' not in sig_df.columns:
                sig_df['rat'] = 'Unknown'
            if 'level' in sig_df.columns:
                sig_df['Level'] = pd.to_numeric(sig_df['level'], errors='coerce')
                sig_df = sig_df[(sig_df['Level'] >= 0) & (sig_df['Level'] <= 5)]

                current_year = datetime.datetime.now().year
                sig_df['time_dt'] = pd.to_datetime(str(current_year) + "-" + sig_df['time'], format='%Y-%m-%d %H:%M:%S.%f', errors='coerce')

                sig_df['Slot'] = "Slot " + sig_df['slot'].astype(str)
                sig_df['RAT'] = sig_df['rat'].astype(str)
                sig_df = sig_df.sort_values(by=["time_dt", "Slot", "RAT"])

                fig_sig_dash = px.line(
                    sig_df, x='time_dt', y='Level', color='RAT', facet_row='Slot',
                    line_shape='hv', markers=len(sig_df) < 50,
                    title=f"전체 시간대별 통신망(RAT) 안테나 수신 변화 (총 {len(sig_df):,}건 데이터)",
                    labels={"Level": "안테나 칸 수", "time_dt": "시간", "Slot": "유심 슬롯", "RAT": "통신망"},
                    hover_data=['raw_info'], height=600
                )
                fig_sig_dash.update_traces(line=dict(width=1.5), opacity=0.85)
                fig_sig_dash.update_xaxes(nticks=15, tickangle=-45, showgrid=True, gridcolor='rgba(128,128,128,0.2)')
                fig_sig_dash.update_yaxes(range=[-0.5, 5.5], dtick=1, title_text="안테나 칸", showgrid=True, gridcolor='rgba(128,128,128,0.2)')
                fig_sig_dash.update_layout(hovermode="x unified")
                fig_sig_dash.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
                st.plotly_chart(fig_sig_dash, use_container_width=True)
            else:
                st.warning("안테나 데이터를 찾았지만, 레벨(Level) 값을 읽을 수 없는 구형 포맷입니다.")
        else:
            st.info("현재 분석 대상 로그에 안테나(Signal_Level) 데이터가 없습니다.")

def render_data_usage_profiling(df):
    """셀룰러 데이터 사용량 프로파일링 차트 렌더링"""
    st.subheader("📊 셀룰러 데이터 사용량 프로파일링")
    if 'log_type' in df.columns:
        du_df = df[df['log_type'] == 'Data_Usage'].copy()
        if not du_df.empty:
            du_df['total_mb'] = pd.to_numeric(du_df['total_mb'], errors='coerce')
            col_du1, col_du2 = st.columns(2)
            with col_du1:
                app_df = du_df.groupby('app_name')['total_mb'].sum().reset_index().sort_values(by='total_mb', ascending=False).head(10)
                fig_app = px.pie(app_df, values='total_mb', names='app_name', hole=0.4, title='📱 앱별 데이터 사용량 Top 10 (MB)')
                fig_app.update_traces(textposition='inside', textinfo='percent+label')
                st.plotly_chart(fig_app, use_container_width=True)
            with col_du2:
                rat_df = du_df.groupby('rat')['total_mb'].sum().reset_index()
                fig_rat = px.pie(
                    rat_df, values='total_mb', names='rat', title='📶 통신망(RAT)별 데이터 처리 비중', color='rat',
                    color_discrete_map={'LTE':'#1f77b4', '5G (NR)':'#ff7f0e', 'Unknown (망 통합 합산)':'#7f7f7f'}
                )
                fig_rat.update_traces(textposition='inside', textinfo='percent+label')
                st.plotly_chart(fig_rat, use_container_width=True)
        else:
            st.info("현재 분석 대상 로그에 데이터 사용량(Netstats) 기록이 없습니다.")

def render_network_timeseries_and_dns(df):
    """DNS 에러 통계 및 네트워크 시계열 분석 차트 렌더링 (시간 정렬 버그 픽스)"""
    st.subheader("🌐 DNS 및 네트워크 시계열 분석")

    if 'log_type' in df.columns:
        # 1. DNS 이슈 요약 (기존 유지)
        dns_df = df[df['log_type'] == 'Network_DNS_Issue'].copy()
        if not dns_df.empty:
            col_dns1, col_dns2 = st.columns(2)
            with col_dns1:
                st.markdown("**🚫 DNS 차단/실패 사유**")
                fig_dns = px.pie(dns_df, names='suspected_reason', hole=0.4)
                st.plotly_chart(fig_dns, use_container_width=True)
            with col_dns2:
                st.markdown("**📦 패키지별 DNS 이슈 발생 건수**")
                pkg_counts = dns_df['package'].value_counts().reset_index()
                pkg_counts.columns = ['package', 'count']
                fig_pkg = px.bar(pkg_counts, x='count', y='package', orientation='h')
                st.plotly_chart(fig_pkg, use_container_width=True)
        else:
            st.info("적재된 DNS 이슈 데이터가 없습니다.")

        # 2. 네트워크 시계열 데이터 (라인 차트)
        ts_df = df[df['log_type'] == 'Network_Timeline_Stat'].copy()
        if not ts_df.empty:
            ts_df['dns_avg'] = pd.to_numeric(ts_df['dns_avg'], errors='coerce')
            ts_df['dns_err_rate'] = pd.to_numeric(ts_df['dns_err_rate'], errors='coerce')

            # 🚨 [버그 수정] 강력하고 안전한 시간 파서 도입
            def safe_parse_time(t):
                t_str = str(t).strip()
                # 연도가 빠진 "MM-DD" 형태인지 확인 (예: 04-12 14:00:00)
                if len(t_str) > 5 and t_str[2] == '-' and t_str.count('-') == 1:
                    current_year = datetime.datetime.now().year
                    t_str = f"{current_year}-{t_str}"
                return pd.to_datetime(t_str, errors='coerce')

            # 적용 및 정렬
            ts_df['time_dt'] = ts_df['time'].apply(safe_parse_time)
            ts_df = ts_df.dropna(subset=['time_dt']).sort_values(by='time_dt')

            # 🚨 방어 로직: 변환 후 데이터가 모두 날아갔는지 체크
            if ts_df.empty:
                st.warning("⚠️ 시계열 데이터를 변환할 수 없습니다. (시간 포맷 오류)")
                return

            # netId를 명시적 문자열로 변환 (숫자일 경우 그라데이션 색상으로 변하는 것 방지)
            ts_df['netId'] = ts_df['netId'].astype(str)

            metric_choice = st.selectbox("확인할 지표 선택", ["DNS 평균 응답 시간(ms)", "DNS 에러율(%)"])
            target_col = 'dns_avg' if "응답 시간" in metric_choice else 'dns_err_rate'

            fig_ts = px.line(
                ts_df, x='time_dt', y=target_col, color='netId', hover_data=['transport'],
                markers=True, title=f"시간대별 {metric_choice} 변화"
            )
            fig_ts.update_xaxes(tickformat="%m-%d\n%H:%M:%S", title="발생 시간")
            fig_ts.update_layout(yaxis_title="수치")
            st.plotly_chart(fig_ts, use_container_width=True)
        else:
            st.info("시계열 그래프를 그릴 수 있는 상세 지표가 DB에 없습니다. 로그를 다시 분석해 주세요.")

def render_ntn_advanced_fw_analyzer(current_base):
    """Starlink (Direct-to-Cell) 위성 로밍 및 UI 아이콘 상태 분석 (독립 모듈형)"""
    st.subheader("🛰️ Starlink / NTN 로밍 정책 및 UI 상태 분석")

    if not current_base:
        st.info("💡 분석 대상 파일이 선택되지 않았습니다.")
        return

    file_path = f"./result/{current_base}_ntn.json"
    if not os.path.exists(file_path):
        st.info("💡 현재 파일에 대한 위성(NTN)로그 분석 결과가 없습니다.")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not data:
        st.error("🚨 추출 결과 0건: 로그 파일 내에 위성(NTN) 데이터가 없습니다.")
        return

    ntn_df = pd.DataFrame(data)

    real_ntn_events = ntn_df[ntn_df['event_type'] != 'RADIO_POWER']
    if real_ntn_events.empty:
        st.info("현재 분석 대상 로그에 위성 관련 데이터가 없습니다.")
        return

    # 🚨 KeyError 방어
    expected_cols = ['ntn_plmn', 'data_policy', 'power_state', 'ntn_mode', 'last_ntn_mode', 'last_phone_mode', 'is_hysteresis', 'raw_info']
    for col in expected_cols:
        if col not in ntn_df.columns:
            ntn_df[col] = None

    ntn_df = ntn_df.sort_values('time').reset_index(drop=True)

    # =========================================================
    # 🧹 [핵심] 상태 전이(State Transition) 기반 중복 로그 필터링
    # =========================================================
    # 1. PLMN_MATCH: 이전 로그와 PLMN 값이 다를 때만 남김
    m_plmn = ntn_df['event_type'] == 'PLMN_MATCH'
    ntn_df.loc[m_plmn, 'keep'] = ntn_df[m_plmn]['ntn_plmn'] != ntn_df[m_plmn]['ntn_plmn'].shift(1)

    # 2. NTN_MODE_NOTIFY: 한 로그 내의 last_ntn_mode 와 ntn_mode 가 다를 때만 '진짜 변화'로 간주
    m_mode = ntn_df['event_type'] == 'NTN_MODE_NOTIFY'
    cond_internal_diff = ntn_df[m_mode]['last_ntn_mode'] != ntn_df[m_mode]['ntn_mode']
    cond_temporal_diff = ntn_df[m_mode]['ntn_mode'] != ntn_df[m_mode]['ntn_mode'].shift(1)
    ntn_df.loc[m_mode, 'keep'] = cond_internal_diff & cond_temporal_diff
    # 3. RADIO_POWER: 모뎀 ON/OFF 상태가 바뀔 때만 남김
    m_radio = ntn_df['event_type'] == 'RADIO_POWER'
    ntn_df.loc[m_radio, 'keep'] = ntn_df[m_radio]['power_state'] != ntn_df[m_radio]['power_state'].shift(1)

    # 4. 나머지 이벤트(HYSTERESIS 등)는 일단 유지
    ntn_df.loc[~(m_plmn | m_mode | m_radio), 'keep'] = True

    # 필터링 완료된 깔끔한 데이터프레임
    clean_df = ntn_df[ntn_df['keep'] == True].copy()

    # ---------------------------------------------------------
    # 📊 1. 상단 핵심 지표 (KPI) - 여기는 필터링 전 원본 데이터의 최신값 사용
    # ---------------------------------------------------------
    plmn_logs = ntn_df[ntn_df['event_type'] == 'PLMN_MATCH']
    latest_plmn = plmn_logs.iloc[-1]['ntn_plmn'] if not plmn_logs.empty else "N/A"

    policy_logs = ntn_df[ntn_df['event_type'] == 'DATA_POLICY']
    latest_policy = policy_logs.iloc[-1]['data_policy'] if not policy_logs.empty else "N/A"

    ui_icon_status = "OFF ⚪"
    for _, row in ntn_df.iloc[::-1].iterrows():
        if row['event_type'] == 'NTN_MODE_NOTIFY':
            ui_icon_status = "ON (Real) 🟢" if str(row['ntn_mode']).upper() == 'ON' else "OFF ⚪"
            break
        elif row['event_type'] == 'HYSTERESIS_ICON_ON':
            ui_icon_status = "ON (Hysteresis 유지) 🟡"
            break

    col1, col2, col3 = st.columns(3)
    col1.metric("연결 대상 Satellite PLMN", latest_plmn)
    col2.metric("활성 데이터 정책 (Policy)", latest_policy)
    col3.metric("단말 상태표시줄 위성 아이콘", ui_icon_status)

    st.divider()

    # ---------------------------------------------------------
    # 📈 2. 타임라인 차트 (DATA_POLICY 제외 & 중복 제거 완료)
    # ---------------------------------------------------------
    st.markdown("**🧭 위성망 진입 시퀀스 및 상태 전이(State Transition) 타임라인**")

    # 차트 그릴 때 DATA_POLICY는 제외함
    chart_df = clean_df[clean_df['event_type'] != 'DATA_POLICY'].copy()

    if not chart_df.empty:
        # 🚨 [PRO FIX] 시간 역전 해결
        current_year = datetime.datetime.now().year
        chart_df['time_dt'] = pd.to_datetime(str(current_year) + "-" + chart_df['time'], errors='coerce')
        chart_df = chart_df.sort_values('time_dt')

        fig = px.scatter(
            chart_df, x='time_dt', y='event_type', color='event_type',
            hover_data=['ntn_plmn', 'last_ntn_mode', 'ntn_mode', 'is_hysteresis', 'power_state'],
            title="시간대별 주요 이벤트 추적 (값 변경 시점에만 점 표시)",
            labels={'time_dt': '발생 시간', 'event_type': '이벤트 종류'}
        )

        fig.update_traces(marker=dict(size=14, symbol='diamond', line=dict(width=2, color='DarkSlateGrey')))
        fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")
        order = ['RADIO_POWER', 'PLMN_MATCH', 'HYSTERESIS_ICON_ON', 'NTN_MODE_NOTIFY']
        fig.update_layout(yaxis={'categoryorder': 'array', 'categoryarray': order})
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("표시할 타임라인 이벤트가 없습니다.")

    # ---------------------------------------------------------
    # 📋 3. 상세 로그 테이블 (중복이 제거된 깔끔한 이력)
    # ---------------------------------------------------------
    st.markdown("**📋 NTN 상태 전이 상세 이력 (변화 시점만 기록)**")
    display_cols = [col for col in ['time', 'event_type', 'power_state', 'ntn_plmn', 'last_ntn_mode', 'ntn_mode', 'is_hysteresis', 'data_policy'] if col in clean_df.columns]

    final_table_df = clean_df[display_cols].fillna("-")
    st.dataframe(final_table_df, use_container_width=True)

def render_data_call_analyzer(data):
    """RIL SETUP_DATA_CALL (데이터 호) 분석 렌더러"""
    st.subheader("🌐 RIL 데이터 호 (SETUP_DATA_CALL) 분석")

    # 1. 전달받은 data가 비어있는지 먼저 체크 (유령 데이터 방지)
    if not data or len(data) == 0:
        st.info("현재 분석 대상 로그에 데이터 호(SETUP_DATA_CALL) 연결 시도 내역이 없습니다.")
        return

    # 2. 데이터가 있을 경우에만 DataFrame 생성 및 렌더링 진행
    df = pd.DataFrame(data)

    expected_columns = ['status', 'latency_ms', 'event_type', 'req_time', 'apn',
                        'network', 'protocol', 'cause', 'cid']
    for col in expected_columns:
        if col not in df.columns:
            df[col] = 0 if col == 'latency_ms' else 'UNKNOWN'

    setup_df = df[df['event_type'] == 'DATA_SETUP']
    # 상단 KPI 카드 계산
    total_calls = len(setup_df)
    success_calls = len(setup_df[setup_df['status'] == 'SUCCESS'])
    fail_calls = total_calls - success_calls
    success_rate = (success_calls / total_calls) * 100 if total_calls > 0 else 0

    # 레이턴시가 있는 정상 연결만 평균 계산 (에러 방지)
    valid_latency = setup_df[setup_df['latency_ms'] > 0]['latency_ms']
    avg_latency = valid_latency.mean() if not valid_latency.empty else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 데이터 연결 시도", f"{total_calls} 회")
    col2.metric("성공률", f"{success_rate:.1f} %")
    col3.metric("실패 횟수", f"{fail_calls} 회")
    col4.metric("평균 응답 지연 (Latency)", f"{avg_latency:.0f} ms")

    st.divider()

    # 타임라인 산점도 (생애주기 및 상태 시각화)
    st.markdown("**📈 데이터 호 트랜잭션 및 상태 전이(Lifecycle)**")

    chart_df = df[~((df['event_type'] == 'UNSOL_UPDATE') & (df.get('is_changed') == False))].copy()

    # 상태별 색상
    color_map = {
        "SUCCESS": "#2ecc71",   # 초록
        "FAIL": "#e74c3c",      # 빨강
        "DORMANT": "#f1c40f",   # 노랑
        "ACTIVE": "#3498db",    # 파랑
        "DROP 💥": "#8e44ad"    # 보라
    }

    if not chart_df.empty:
        # 🚨 [PRO FIX] 시간 역전 해결
        current_year = datetime.datetime.now().year
        chart_df['req_time_dt'] = pd.to_datetime(str(current_year) + "-" + chart_df['req_time'], errors='coerce')
        chart_df = chart_df.dropna(subset=['req_time_dt']).sort_values('req_time_dt')

        fig = px.scatter(
            chart_df, x='req_time_dt', y='apn', color='status',
            color_discrete_map=color_map,
            symbol='event_type',
            size=[15]*len(chart_df),
            hover_data=['event_type', 'network', 'protocol', 'cause', 'latency_ms', 'cid'],
            title="시간대별 APN 데이터 호 상태 변화 (마우스 오버 시 상세 원인 확인)",
            labels={'req_time_dt': '시간', 'apn': '대상 APN'}
        )
        fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")
        st.plotly_chart(fig, use_container_width=True, key="datacall_scatter_chart")
    else:
        st.info("차트에 표시할 이벤트가 없습니다.")

    # 상세 로그 추적 테이블
    st.markdown("**📋 데이터 호 트랜잭션 상세 내역**")
    st.dataframe(df, use_container_width=True)

def render_ims_sip_flow(current_base=None):
    """VoLTE/IMS SIP Call Flow (사다리 차트) 시각화"""
    st.subheader("💬 VoLTE / IMS SIP Call Flow (Sequence Diagram)")

    if not current_base: return
    file_path = f"./result/{current_base}_ims_sip.json"
    if not os.path.exists(file_path):
        st.info("💡 IMS SIP 로그 분석 결과가 없습니다.")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not data:
        st.info("현재 분석 대상 로그에 SIP 메시지가 없습니다.")
        return

    sip_df = pd.DataFrame(data)

    # 상단 에러 통계 KPI
    total_msgs = len(sip_df)
    error_msgs = len(sip_df[sip_df['is_error'] == True])

    col1, col2, col3 = st.columns(3)
    col1.metric("총 SIP 트랜잭션", f"{total_msgs} 건")
    col2.metric("SIP 에러 (4xx~6xx)", f"{error_msgs} 건", delta="비정상" if error_msgs > 0 else "정상", delta_color="inverse" if error_msgs > 0 else "normal")

    # 지연 시간(Call Setup Latency) 추정: 첫 INVITE부터 200 OK까지
    try:
        sip_df['time_dt'] = pd.to_datetime(sip_df['time'], format='%m-%d %H:%M:%S.%f', errors='coerce')
        invite_time = sip_df[sip_df['method_code'].str.contains('INVITE', na=False)]['time_dt'].min()
        ok_time = sip_df[sip_df['method_code'].str.contains('200 OK', na=False)]['time_dt'].max()
        if pd.notna(invite_time) and pd.notna(ok_time) and ok_time >= invite_time:
            latency_ms = int((ok_time - invite_time).total_seconds() * 1000)
            col3.metric("최대 트랜잭션 지연(Latency)", f"{latency_ms} ms")
        else:
            col3.metric("최대 트랜잭션 지연", "N/A")
    except:
        col3.metric("최대 트랜잭션 지연", "N/A")

    st.divider()

    # ---------------------------------------------------------
    # 📈 사다리 차트 (Sequence Diagram) 렌더링
    # ---------------------------------------------------------
    sip_df = sip_df.sort_values('time')
    sip_df['y_pos'] = range(len(sip_df), 0, -1) # Y축은 시간이 흐를수록 아래로(내림차순)

    fig = go.Figure()

    # 단말(0)과 망(1)을 나타내는 두 개의 세로 기둥(점선) 추가
    fig.add_shape(type="line", x0=0, y0=0, x1=0, y1=len(sip_df)+1, line=dict(color="lightgray", width=2, dash="dash"))
    fig.add_shape(type="line", x0=1, y0=0, x1=1, y1=len(sip_df)+1, line=dict(color="lightgray", width=2, dash="dash"))

    for idx, row in sip_df.iterrows():
        y = row['y_pos']
        method = row['method_code']
        cseq = row['cseq']
        is_error = row['is_error']
        time_str = row['time'].split(' ')[1] # 시간만 표시 (예: 10:50:14.706)

        # 에러는 빨간색, 정상 응답(2xx)은 초록색, 요청은 파란색
        if is_error:
            color = "#e74c3c"
        elif "200 OK" in method or "202" in method:
            color = "#2ecc71"
        else:
            color = "#3498db"

        # Tx (단말 -> 망) / Rx (망 -> 단말) 좌표 설정
        if "Tx" in row['direction']:
            x0, x1 = 0.05, 0.95
        else:
            x0, x1 = 0.95, 0.05

        # 메시지 화살표 그리기
        fig.add_annotation(
            x=x1, y=y, ax=x0, ay=y,
            xref="x", yref="y", axref="x", ayref="y",
            text=f"<b>{method}</b><br><span style='font-size:10px'>{cseq}</span>",
            showarrow=True, arrowhead=2, arrowsize=1.5, arrowwidth=2, arrowcolor=color,
            font=dict(color=color, size=13), align="center", yshift=8
        )

        # 왼쪽 타임스탬프 텍스트
        fig.add_annotation(
            x=-0.05, y=y, xref="x", yref="y",
            text=time_str, showarrow=False,
            font=dict(size=11, color="gray"), xanchor="right"
        )

    # 레이아웃 정리
    fig.update_layout(
        xaxis=dict(
            tickmode='array', tickvals=[0, 1],
            ticktext=['📱 단말 (UE)', '🌐 IMS 망 (Network)'],
            tickfont=dict(size=15, weight='bold'),
            range=[-0.2, 1.2], side="top", showgrid=False, zeroline=False
        ),
        yaxis=dict(showticklabels=False, range=[0, len(sip_df)+1], showgrid=False, zeroline=False),
        height=max(400, len(sip_df) * 45), # 메시지가 많아지면 차트 길이를 자동으로 늘림
        margin=dict(l=120, r=50, t=80, b=20),
        plot_bgcolor='white', hovermode=False
    )

    st.plotly_chart(fig, use_container_width=True)

    # ---------------------------------------------------------
    # 📋 상세 로그 테이블
    # ---------------------------------------------------------
    st.markdown("**📋 SIP 메시지 트랜잭션 상세**")
    display_cols = ['time', 'direction', 'msg_type', 'method_code', 'tid', 'cseq', 'raw_log']
    st.dataframe(sip_df[display_cols], use_container_width=True)

def render_crash_analyzer(report_data):
    """시스템 크래시 및 FATAL 에러 분석 UI"""
    st.subheader("💥 시스템 크래시 및 FATAL 에러 분석")

    crash_data = report_data.get("crash_context", [])

    if not crash_data:
        st.success("💡 분석된 로그 내에 심각한 시스템 크래시나 FATAL 에러가 발견되지 않았습니다.")
        return

    st.error(f"🚨 총 {len(crash_data)}건의 크래시/FATAL 에러가 감지되었습니다!")

    for i, crash in enumerate(crash_data):
        # 타임스탬프와 프로세스명으로 아코디언(Expander) 제목 생성
        ts = crash.get('timestamp', 'Time Unknown')
        process = crash.get('process', 'Unknown Process')
        crash_type = crash.get('crash_type', 'FATAL EXCEPTION')

        with st.expander(f"[{ts}] {process} - {crash_type}"):
            # 주변 로그(Time-Window Glue)가 수집되어 있다면 함께 출력
            if 'cross_context_logs' in crash and crash['cross_context_logs']:
                st.markdown("**주변 컨텍스트 로그 (크래시 전후):**")
                st.code("\n".join(crash['cross_context_logs']), language='log')
            elif 'raw_line' in crash:
                st.markdown("**크래시 원문 로그:**")
                st.code(crash['raw_line'], language='log')

def render_sat_at_analyzer(current_base=None):
    """독자 위성 모뎀(AT Command) 시퀀스 및 CREG 상태 렌더러"""
    st.subheader("🛰️ 위성 모뎀 제어 시퀀스 & 상태 (AT Command)")

    if not current_base: return
    file_path = f"./result/{current_base}_sat_at.json"
    if not os.path.exists(file_path): return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    metrics = data.get("metrics", {})
    flow = data.get("call_flow", [])
    reg_history = data.get("registration_history", [])

    # 1. 핵심 KPI 카드
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("위성 ARFCN", metrics.get("arfcn", "N/A"))
    c2.metric("최종 망 등록 상태", metrics.get("current_reg_state", "Unknown"))
    # 💡 통화 시도 대비 실패율
    call_total = metrics.get('calls_total', 0)
    call_fail = metrics.get('calls_dropped_or_failed', 0)
    c3.metric("Voice Call (총/실패)", f"{call_total} 회 / {call_fail} 회",
              delta=f"-{call_fail} Drop" if call_fail > 0 else "정상", delta_color="inverse")

    # 💡 SMS 발신 성공/실패율
    sms_rx = metrics.get('sms_rx', 0)
    sms_tx_succ = metrics.get('sms_tx_success', 0)
    sms_tx_fail = metrics.get('sms_tx_fail', 0)
    c4.metric("SMS (수신/발신성공/실패)", f"{sms_rx} / {sms_tx_succ} / {sms_tx_fail}",
              delta=f"-{sms_tx_fail} Fail" if sms_tx_fail > 0 else "정상", delta_color="inverse")
    st.divider()

    # ==========================================
    # 🚨 [신규 추가] CREG Registration 상태 추이 그래프
    # ==========================================
    if reg_history:
        st.write("#### 📡 위성망 등록 상태 전이 (Registration History)")
        df_reg = pd.DataFrame(reg_history)

        # 시간의 흐름에 따라 상태가 어떻게 변했는지 계단형(Step) 차트로 표현
        fig_reg = px.line(
            df_reg, x="time", y="status_str", markers=True,
            hover_data=["raw"],
            labels={"time": "시간", "status_str": "상태"}
        )
        fig_reg.update_traces(line_shape='hv', line_color='#E64A19', marker=dict(size=8))
        fig_reg.update_yaxes(categoryorder='array', categoryarray=["Deregistered (0)", "Searching", "Registered (1)"])
        fig_reg.update_layout(height=250, margin=dict(t=20, b=20))
        st.plotly_chart(fig_reg, use_container_width=True)
        st.divider()

       # ==========================================
    # 3. 3-Tier Call Flow 시퀀스 다이어그램 (AP ↔ RIL ↔ CP)
    # ==========================================
    if flow:
        st.write("#### 💬 통화 제어 풀스택 시퀀스 (AP ↔ RIL ↔ Modem)")
        fig = go.Figure()

        for idx, msg in enumerate(flow):
            time_str = msg['time']
            src = msg['src']
            dst = msg['dst']
            desc = msg['desc']
            is_highlight = msg.get('is_highlight', False)

            # 축 기준 좌표 (0: AP, 1: RIL, 2: CP)
            # 선이 노드에 살짝 닿지 않게 여백(offset) 부여
            offset = 0.05
            x0 = src + offset if src < dst else src - offset
            x1 = dst - offset if src < dst else dst + offset

            y = len(flow) - idx

            # 색상 결정
            if src == 0 or dst == 0: color = "#9c27b0" if is_highlight else "#ba68c8" # AP-RIL 구간 (보라색)
            else: color = "#d32f2f" if is_highlight else "#1f77b4" # RIL-CP 구간 (빨강/파랑)
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

        # 3축 레이아웃 적용
        fig.update_layout(
            xaxis=dict(
                tickmode='array', tickvals=[0, 1, 2],
                ticktext=['📱 Android<br>(Framework)', '⚙️ RIL<br>(Daemon)', '🛰️ Modem<br>(CP)'],
                tickfont=dict(size=14, weight='bold'),
                range=[-0.5, 2.5], side="top", showgrid=False, zeroline=False
            ),
            yaxis=dict(showticklabels=False, range=[0, len(flow)+1], showgrid=False, zeroline=False),
            height=max(400, len(flow) * 35), margin=dict(l=150, r=50, t=60, b=20), plot_bgcolor="white"
        )
        st.plotly_chart(fig, use_container_width=True)

def render_service_state_timeline(df):
    """일반 셀룰러 망의 서비스 상태(Service State) 변경 타임라인을 렌더링합니다."""
    st.subheader("📶 통신망 서비스 상태 (Registration State) 타임라인")

    if 'log_type' not in df.columns:
        return

    oos_df = df[df['log_type'] == 'OOS_Event'].copy()

    if oos_df.empty:
        st.success("🎉 분석된 로그 내에 서비스 상태가 변경되거나 망 이탈(OOS)이 발생한 기록이 없습니다. (안정적인 IN_SERVICE 유지)")
        return

    # 데이터 정제
    records = []
    for _, row in oos_df.iterrows():
        time_val = row.get('time')
        slot = str(row.get('slot', row.get('slotId', '0')))
        v_reg = str(row.get('voice_reg', 'Unknown'))
        d_reg = str(row.get('data_reg', 'Unknown'))

        # Parser가 넘겨준 "0(IN_SERVICE)" 등에서 상태 규격화
        def map_reg_state(reg_str):
            if not reg_str or reg_str == 'nan': return "UNKNOWN"
            if reg_str.startswith("0"): return "IN_SERVICE"
            if reg_str.startswith("1"): return "OUT_OF_SERVICE"
            if reg_str.startswith("2"): return "EMERGENCY_ONLY"
            if reg_str.startswith("3"): return "POWER_OFF"
            return "UNKNOWN"

        records.append({
            "time": time_val,
            "Slot": f"Slot {slot}",
            "Type": "Voice",
            "State": map_reg_state(v_reg),
            "Raw_Reg": v_reg,
            "Event": row.get('event', row.get('event_type', 'Unknown')),
            "Cause": row.get('candidate_reason', row.get('root_cause_candidate', 'None'))
        })
        records.append({
            "time": time_val,
            "Slot": f"Slot {slot}",
            "Type": "Data",
            "State": map_reg_state(d_reg),
            "Raw_Reg": d_reg,
            "Event": row.get('event', row.get('event_type', 'Unknown')),
            "Cause": row.get('candidate_reason', row.get('root_cause_candidate', 'None'))
        })

    state_df = pd.DataFrame(records)
    # 시간 순 정렬
    state_df = state_df.sort_values(by=['Slot', 'Type', 'time']).reset_index(drop=True)

    # 🧹 [핵심] 상태가 변경된 시점만 점(Marker)을 남기기 위한 필터링 로직
    state_df['keep'] = state_df['State'] != state_df.groupby(['Slot', 'Type'])['State'].shift(1)

    # 맨 첫 번째 로그는 무조건 표시하도록 보완
    state_df.loc[state_df.groupby(['Slot', 'Type']).head(1).index, 'keep'] = True

    clean_df = state_df[state_df['keep']].copy()

    if clean_df.empty:
        st.info("표시할 상태 변화가 없습니다.")
        return

    current_year = datetime.datetime.now().year
    clean_df['time_dt'] = pd.to_datetime(str(current_year) + "-" + clean_df['time'], format='%Y-%m-%d %H:%M:%S.%f', errors='coerce')

    # 시간 순으로 전체 데이터프레임 완벽 정렬
    clean_df = clean_df.sort_values(by=['time_dt', 'Slot', 'Type']).reset_index(drop=True)

    # Y축 카테고리 순서 고정
    category_order = ["POWER_OFF", "EMERGENCY_ONLY", "OUT_OF_SERVICE", "IN_SERVICE"]

    # 🚨 [버그 수정 2] x축을 'time_dt'로 변경
    fig = px.line(
        clean_df, x='time_dt', y='State', color='Type', facet_row='Slot',
        line_shape='hv', markers=True,
        title="시간대별 Voice / Data 등록 상태 변화 (상태 변경 시점에만 마커 표시)",
        labels={'time_dt': '발생 시간', 'State': '현재 상태', 'Type': '통신 유형'},
        hover_data=['Event', 'Cause', 'Raw_Reg'],
        category_orders={"State": category_order}
    )

    fig.update_traces(marker=dict(size=8, line=dict(width=1, color='DarkSlateGrey')))
    fig.update_yaxes(categoryorder='array', categoryarray=category_order)

    # 🚨 [추가] X축 포맷 예쁘게 다듬기 (연도 감추고 월-일 시:분:초)
    fig.update_xaxes(tickformat="%m-%d\n%H:%M:%S")

    chart_height = max(300, 200 * clean_df['Slot'].nunique())
    fig.update_layout(height=chart_height, hovermode="x unified", margin=dict(t=50, b=20))

    st.plotly_chart(fig, use_container_width=True)

def render_integrated_rf_call_timeline(report_data):
    """
    통화 세션(배경색), Signal Level(꺾은선), SIP 에러(마커)를
    하나의 차트에 오버레이(Overlay)하여 교차 분석 타임라인을 제공합니다.
    """
    st.subheader("📊 [통합 타임라인] 통화 상태 & 무선 환경(RF) 교차 분석")
    st.markdown("통화 구간(배경색) 내에서 발생한 **신호 급감(꺾은선)**과 **SIP 에러(빨간 마커)**의 상관관계를 시각적으로 확인합니다.")

    # 1. 안테나 신호 데이터 (꺾은선)
    signal_history = report_data.get("signal_level_history", [])
    if not signal_history:
        st.info("신호 레벨(Signal Level) 이력이 없어 통합 차트를 생성할 수 없습니다.")
        return

    import datetime
    current_year = datetime.datetime.now().year

    sig_times, sig_levels = [], []
    for s in signal_history:
        t_str = str(s.get("time", ""))[:14]
        try:
            dt = pd.to_datetime(f"{current_year}-{t_str}", format='%Y-%m-%d %H:%M:%S')
            sig_times.append(dt)
            sig_levels.append(int(s.get("level", s.get("max_level", 0))))
        except: pass

    fig = go.Figure()

    # 꺾은선 차트 추가
    fig.add_trace(go.Scatter(
        x=sig_times, y=sig_levels,
        mode='lines+markers',
        name='Signal Level',
        line=dict(color='royalblue', width=2),
        marker=dict(size=6, symbol='circle')
    ))

    # 2. 통화 세션 데이터 (배경 하이라이트)
    sessions = report_data.get("telephony", {}).get("sessions", [])
    for s in sessions:
        try:
            start_dt = pd.to_datetime(f"{current_year}-{s.get('start_time')[:14]}", format='%Y-%m-%d %H:%M:%S')
            # end_time이 없으면 시작 시간 + 5초로 임시 박스 생성
            end_time_str = s.get('end_time')
            if end_time_str:
                end_dt = pd.to_datetime(f"{current_year}-{end_time_str[:14]}", format='%Y-%m-%d %H:%M:%S')
            else:
                end_dt = start_dt + pd.Timedelta(seconds=5)

            status = str(s.get("status", "")).upper()
            is_drop = "DROP" in status or "FAIL" in status
            color = "rgba(255, 0, 0, 0.15)" if is_drop else "rgba(0, 255, 0, 0.15)"
            label = "Call Drop 🚨" if is_drop else f"{s.get('type', 'CALL')} (정상)"

            fig.add_vrect(
                x0=start_dt, x1=end_dt,
                fillcolor=color, opacity=1,
                layer="below", line_width=1, line_color="red" if is_drop else "green",
                annotation_text=label, annotation_position="top left"
            )
        except: pass

    # 3. SIP 에러 데이터 (빨간색 X 마커)
    sip_data = report_data.get("ims_sip_data", [])
    sip_errors = [m for m in sip_data if m.get("is_error")]
    if sip_errors:
        err_times, err_texts = [], []
        for e in sip_errors:
            try:
                dt = pd.to_datetime(f"{current_year}-{e.get('time')[:14]}", format='%Y-%m-%d %H:%M:%S')
                err_times.append(dt)
                err_texts.append(e.get("method_code", "SIP Error"))
            except: pass

        # 에러 마커는 보기 쉽게 y=0(바닥) 라인에 배치
        fig.add_trace(go.Scatter(
            x=err_times, y=[0]*len(err_times),
            mode='markers+text',
            name='SIP Error',
            marker=dict(symbol='x', color='red', size=14, line=dict(width=2, color='darkred')),
            text=err_texts,
            textposition="top center",
            textfont=dict(color='red', size=11)
        ))

    # 차트 레이아웃 최적화
    fig.update_layout(
        yaxis_title="안테나 신호 (Level 0~4)",
        yaxis=dict(range=[-0.5, 4.5], tickmode='linear', tick0=0, dtick=1),
        height=350,
        hovermode="x unified",
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )

    st.plotly_chart(fig, use_container_width=True)