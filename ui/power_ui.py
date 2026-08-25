"""Power and thermal charts.

Rendering only: the series come from `core.charts.power`.
"""

import plotly.express as px
import streamlit as st

from core.charts import build_power_thermal_panel

COMMON_HEIGHT = 420
COMMON_MARGIN = dict(l=10, r=10, t=30, b=130)

def render_battery_thermal_chart(df):
    st.subheader("전력 및 발열 현황")

    panel = build_power_thermal_panel(df)

    c1, c2, c3 = st.columns(3)

    with c1:
        st.markdown("**Wakelock 발생 현황**")
        if panel.wakelocks.status == "no_data":
            st.info("Wakelock 데이터가 없습니다.")
        else:
            fig_wl = px.bar(
                panel.wakelocks.frame, x='app_name', y='times',
                labels={'app_name': '패키지', 'times': '건수'},
                color='times', color_continuous_scale='Blues'
            )
            fig_wl.update_layout(xaxis_tickangle=-45, height=COMMON_HEIGHT, margin=COMMON_MARGIN, coloraxis_showscale=False)
            st.plotly_chart(fig_wl, width="stretch")

    with c2:
        st.markdown("**온도 센서 상태**")
        if panel.thermals.status == "no_data":
            st.info("온도 센서 데이터가 없습니다.")
        else:
            fig_th = px.bar(
                panel.thermals.frame, x='sensor', y='temperature',
                color='temperature', color_continuous_scale=[(0, "green"), (0.5, "orange"), (1, "red")],
                range_color=[30, 50], labels={'sensor': '센서', 'temperature': '온도(°C)'}
            )
            fig_th.add_hline(
                y=panel.thermal_warning_c, line_dash="dot", line_color="red",
                annotation_text=f"주의 기준({panel.thermal_warning_c}°C)"
            )
            fig_th.update_layout(xaxis_tickangle=-45, height=COMMON_HEIGHT, margin=COMMON_MARGIN, coloraxis_showscale=False)
            st.plotly_chart(fig_th, width="stretch")

    with c3:
        st.markdown("**CPU 사용률 Top 10**")
        if panel.cpu.status == "no_data":
            st.info("CPU 사용률 데이터가 없습니다.")
        else:
            fig_cpu = px.bar(
                panel.cpu.frame, x='process_label', y='cpu_percent',
                labels={'process_label': '프로세스', 'cpu_percent': '사용률(%)'},
                color='cpu_percent', color_continuous_scale='Reds',
                hover_data={'process': True}
            )
            fig_cpu.update_layout(xaxis_tickangle=-45, height=COMMON_HEIGHT, margin=COMMON_MARGIN, coloraxis_showscale=False)
            st.plotly_chart(fig_cpu, width="stretch")
