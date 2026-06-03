
import pandas as pd
import plotly.express as px
import streamlit as st

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