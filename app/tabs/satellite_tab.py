import json
import os

import streamlit as st

import ui
from agent_tools import get_device_health_kpi
from core.config import SATELLITE_PROMPTS

def _detect_satellite_type(current_base):
    sat_at_path = f"./result/{current_base}_sat_at.json"
    ntn_fw_path = f"./result/{current_base}_ntn.json"
    has_tiantong = False
    has_spacex = False

    if os.path.exists(sat_at_path):
        try:
            with open(sat_at_path, "r", encoding="utf-8") as f:
                t_data = json.load(f)
                if len(t_data.get("call_flow", [])) > 0:
                    has_tiantong = True
        except Exception:
            pass

    if os.path.exists(ntn_fw_path):
        try:
            with open(ntn_fw_path, "r", encoding="utf-8") as f:
                s_data = json.load(f)
                if isinstance(s_data, dict) and any(v for v in s_data.values() if v):
                    has_spacex = True
                elif isinstance(s_data, list) and len(s_data) > 0:
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

    sat_type = _detect_satellite_type(current_base)

    if sat_type == "Tiantong":
        ui.render_sat_at_analyzer(current_base)
    elif sat_type == "SpaceX":
        ui.render_ntn_advanced_fw_analyzer(current_base)
    else:
        st.info("NTN 위성 통신 로그가 존재하지 않습니다.")

    st.divider()

    if not sat_type:
        return

    if st.button(f"{sat_type} 위성망 심층 진단 실행", width="stretch"):
        with st.spinner(f"Analyzing {sat_type} Satellite Data..."):
            health_kpi_json = get_device_health_kpi(current_base)
            prompt_template = SATELLITE_PROMPTS.get(sat_type, "Prompt template not found.")
            sat_query = prompt_template.format(health_kpi_json=health_kpi_json)

            raw_result = engine.ask(sat_query, current_file=current_target)

            final_text = raw_result[0] if isinstance(raw_result, (tuple, list)) else raw_result
            sat_thinking = raw_result[3] if isinstance(raw_result, (tuple, list)) and len(raw_result) > 3 else ""

            if isinstance(final_text, str):
                final_text = final_text.replace("\\n", "\n")

            st.markdown(f"### [AI Analysis: {sat_type} Network Diagnostic]")

            if sat_thinking:
                with st.expander("AI Reasoning Trace"):
                    st.markdown(f"```text\n{sat_thinking}\n```")

            st.info(final_text)

            if "chat_history" in st.session_state:
                st.session_state.chat_history.append({
                    "role": "user",
                    "content": f"{sat_type} Network Diagnostic requested."
                })
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": final_text
                })