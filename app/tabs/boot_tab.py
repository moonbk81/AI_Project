import plotly.express as px
import streamlit as st

import ui
from app.backend_client import get_result_json_with_optional_backend
from core.charts import SLOW_EVENT_LIMIT, build_boot_sequence


def _boot_ms(value):
    """A milestone the log never reported has no number to show."""
    return f"{value:,.0f} ms" if value is not None else "N/A"

def render_boot_tab():
    st.subheader("부팅 시퀀스 분석")

    current_target = st.session_state.get("current_file", None)
    if not current_target:
        st.warning("분석 대상 파일을 선택해 주십시오.")
        return

    base_name = current_target.replace("_payload.json", "")
    report_data = get_result_json_with_optional_backend(base_name, "report")

    if not report_data:
        st.error(f"분석 결과 파일을 찾을 수 없습니다. ({base_name}_report.json)")
        return

    sequence = build_boot_sequence(report_data)

    if sequence.status == "no_events":
        st.warning("부팅 이벤트 데이터가 없습니다.")
    else:
        st.markdown("#### 부팅 주요 구간 요약")

        milestones = sequence.milestones
        c1, c2, c3 = st.columns(3)
        c1.metric("부팅 완료", _boot_ms(milestones.boot_complete_ms))
        c2.metric("Voice(RIL) 준비", _boot_ms(milestones.voice_ready_ms))
        c3.metric("Data(NW) 준비", _boot_ms(milestones.data_ready_ms))

        st.divider()
        st.write(f"#### 부팅 지연 구간 Top {SLOW_EVENT_LIMIT}")

        if not sequence.has_deltas:
            st.info("Delta_ms 데이터가 존재하지 않아 병목 차트를 렌더링할 수 없습니다.")
        elif not sequence.slow_events.empty:
            fig_boot = px.bar(
                sequence.slow_events,
                x='Delta_ms',
                y='Event',
                orientation='h',
                color='Delta_ms',
                color_continuous_scale='Reds',
                text='Delta_ms',
                title="부팅 지연 이벤트(ms)",
                labels={'Delta_ms': '지연(ms)', 'Event': '이벤트'}
            )
            fig_boot.update_layout(yaxis={'categoryorder': 'total ascending'}, height=450)
            st.plotly_chart(fig_boot, width="stretch")

        with st.expander("부팅 시퀀스 상세 타임라인"):
            st.dataframe(sequence.timeline, width="stretch")

    st.divider()
    ui.render_crash_analyzer(report_data)

    st.divider()
    ui.render_binder_proxy_leaks(report_data.get("binder_warnings", []))

    st.divider()
    ui.render_nitz_timeline(report_data.get("nitz_history", []))
