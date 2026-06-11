import json
import os

import pandas as pd
import plotly.express as px
import streamlit as st

import ui

def render_boot_tab():
    st.subheader("부팅 시퀀스 분석")

    current_target = st.session_state.get("current_file", None)
    if not current_target:
        st.warning("분석 대상 파일을 선택해 주십시오.")
        return

    base_name = current_target.replace("_payload.json", "")
    report_path = f"./result/{base_name}_report.json"

    if not os.path.exists(report_path):
        st.error(f"분석 결과 파일을 찾을 수 없습니다. ({base_name}_report.json)")
        return

    with open(report_path, 'r', encoding='utf-8') as f:
        report_data = json.load(f)

    boot_raw = report_data.get('boot_stats', [])
    events = boot_raw.get('events', []) if isinstance(boot_raw, dict) else boot_raw

    if events:
        df_boot = pd.DataFrame(events)

        st.markdown("#### 부팅 주요 구간 요약")

        c1, c2, c3 = st.columns(3)

        boot_complete = df_boot['Time_ms'].max() if 'Time_ms' in df_boot.columns else 0

        voice_events = df_boot[
            df_boot['Event'].str.contains('Voice|RIL|Telephony', case=False, na=False)
        ] if 'Event' in df_boot.columns else pd.DataFrame()

        voice_ready = voice_events['Time_ms'].max() if not voice_events.empty else "N/A"

        data_events = df_boot[
            df_boot['Event'].str.contains('Data|Network|Setup', case=False, na=False)
        ] if 'Event' in df_boot.columns else pd.DataFrame()

        data_ready = data_events['Time_ms'].max() if not data_events.empty else "N/A"

        c1.metric(
            "부팅 완료",
            f"{boot_complete:,} ms" if boot_complete else "N/A"
        )
        c2.metric(
            "Voice(RIL) 준비",
            f"{voice_ready:,} ms" if isinstance(voice_ready, (int, float)) else voice_ready
        )
        c3.metric(
            "Data(NW) 준비",
            f"{data_ready:,} ms" if isinstance(data_ready, (int, float)) else data_ready
        )

        st.divider()
        st.write("#### 부팅 지연 구간 Top 10")

        if 'Delta_ms' in df_boot.columns:
            df_slow = (
                df_boot[df_boot['Delta_ms'] > 0]
                .sort_values("Delta_ms", ascending=False)
                .head(10)
            )

            if not df_slow.empty:
                fig_boot = px.bar(
                    df_slow,
                    x='Delta_ms',
                    y='Event',
                    orientation='h',
                    color='Delta_ms',
                    color_continuous_scale='Reds',
                    text='Delta_ms',
                    title="부팅 지연 이벤트(ms)",
                    labels={
                        'Delta_ms': '지연(ms)',
                        'Event': '이벤트'
                    }
                )

                fig_boot.update_layout(
                    yaxis={'categoryorder': 'total ascending'},
                    height=450
                )

                st.plotly_chart(fig_boot, width="stretch")
        else:
            st.info("Delta_ms 데이터가 존재하지 않아 병목 차트를 렌더링할 수 없습니다.")

        with st.expander("부팅 시퀀스 상세 타임라인"):
            df_full = (
                df_boot.sort_values("Time_ms")
                if 'Time_ms' in df_boot.columns
                else df_boot
            )
            st.dataframe(df_full, width="stretch")
    else:
        st.warning("부팅 이벤트 데이터가 없습니다.")

    st.divider()
    ui.render_crash_analyzer(report_data)

    st.divider()
    ui.render_binder_proxy_leaks(report_data.get("binder_warnings", []))

    st.divider()
    ui.render_nitz_timeline(report_data.get("nitz_history", []))