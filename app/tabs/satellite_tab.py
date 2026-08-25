import streamlit as st

import ui
from app.backend_client import (
    generate_satellite_report_with_optional_backend,
    get_satellite_overview_with_optional_backend,
)


def render_satellite_tab(engine):
    current_target = st.session_state.get("current_file") or "Unknown"
    current_base = current_target.replace("_payload.json", "") if current_target != "Unknown" else "Unknown"

    if current_base == "Unknown":
        st.warning("분석 대상 파일을 선택해 주십시오.")
        return

    overview = get_satellite_overview_with_optional_backend(current_base)
    sat_type = overview.get("sat_type")

    if sat_type == "SpaceX":
        ui.render_ntn_advanced_fw_analyzer(current_base, data=overview.get("ntn") or {})
    else:
        st.info("NTN 위성 통신 로그가 존재하지 않습니다.")

    st.divider()

    if not sat_type:
        return

    if not st.button(f"{sat_type} 위성망 리포트 생성", width="stretch"):
        return

    with st.spinner(f"{sat_type} 위성망 데이터를 정리하는 중입니다..."):
        report = generate_satellite_report_with_optional_backend(
            engine,
            current_base,
            sat_type,
            current_file=current_target,
        )

    final_text = report.get("answer", "")
    sat_thinking = report.get("thinking", "")

    st.markdown(f"### {sat_type} 위성망 분석 결과")

    if sat_thinking:
        with st.expander("처리 과정", expanded=False):
            st.markdown(f"```text\n{sat_thinking}\n```")

    st.info(final_text)

    if "chat_history" in st.session_state:
        st.session_state.chat_history.append({
            "role": "user",
            "content": f"{sat_type} 위성망 분석 요청"
        })
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": final_text
        })
