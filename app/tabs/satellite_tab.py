import streamlit as st

import ui
from agent_tools import get_device_health_kpi
from app.backend_client import ask_with_optional_backend, get_result_json_with_optional_backend
from core.config import SATELLITE_PROMPTS

def _detect_satellite_type(current_base, sat_at_data=None, ntn_data=None):
    has_tiantong = False
    has_spacex = False

    try:
        if sat_at_data and len(sat_at_data.get("call_flow", [])) > 0:
            has_tiantong = True
    except Exception:
        pass

    try:
        if isinstance(ntn_data, dict) and any(v for v in ntn_data.values() if v):
            has_spacex = True
        elif isinstance(ntn_data, list) and len(ntn_data) > 0:
            has_spacex = True
    except Exception:
        pass

    if has_tiantong:
        return "Tiantong"
    if has_spacex:
        return "SpaceX"
    return None


def render_satellite_tab(engine):
    current_target = st.session_state.get("current_file") or "Unknown"
    current_base = current_target.replace("_payload.json", "") if current_target != "Unknown" else "Unknown"

    if current_base == "Unknown":
        st.warning("분석 대상 파일을 선택해 주십시오.")
        return

    sat_at_data = get_result_json_with_optional_backend(current_base, "sat_at", default={})
    ntn_data = get_result_json_with_optional_backend(current_base, "ntn", default={})
    sat_type = _detect_satellite_type(current_base, sat_at_data=sat_at_data, ntn_data=ntn_data)

    if sat_type == "Tiantong":
        ui.render_sat_at_analyzer(current_base, data=sat_at_data)
    elif sat_type == "SpaceX":
        ui.render_ntn_advanced_fw_analyzer(current_base, data=ntn_data)
    else:
        st.info("NTN 위성 통신 로그가 존재하지 않습니다.")

    st.divider()

    if not sat_type:
        return

    if st.button(f"{sat_type} 위성망 리포트 생성", width="stretch"):
        with st.spinner(f"{sat_type} 위성망 데이터를 정리하는 중입니다..."):
            health_kpi_json = get_device_health_kpi(current_base)
            prompt_template = SATELLITE_PROMPTS.get(sat_type, "Prompt template not found.")
            sat_query = prompt_template.format(health_kpi_json=health_kpi_json)

            raw_result = ask_with_optional_backend(engine, sat_query, current_file=current_target)

            final_text = raw_result[0] if isinstance(raw_result, (tuple, list)) else raw_result
            sat_thinking = raw_result[3] if isinstance(raw_result, (tuple, list)) and len(raw_result) > 3 else ""

            if isinstance(final_text, str):
                final_text = final_text.replace("\\n", "\n")

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
