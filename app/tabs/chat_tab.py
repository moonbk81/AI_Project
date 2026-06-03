
import streamlit as st

from agent_tools import get_device_health_kpi
from app.chat_panel import render_chat_interface
from core.config import QUICK_PROMPTS
from ui.common import parse_raw_logs

def _build_reference_text(metas):
    ref_text = ""
    for i, meta in enumerate(metas):
        known_solution = meta.get('known_solution')
        solution_badge = " [과거 해결 사례 포함]" if known_solution else ""
        ref_text += f"### 자료 {i+1} (Time: {meta.get('time', 'N/A')}, Slot: {meta.get('slot', 'N/A')}){solution_badge}\n"

        if known_solution:
            ref_text += f"> **분석 기록:** {known_solution}\n\n"

        raw_data = meta.get('raw_logs', meta.get('raw_context', meta.get('raw_stack', '[]')))
        raw_logs = parse_raw_logs(raw_data)
        if raw_logs:
            ref_text += "```text\n"
            for log in raw_logs[:10]:
                ref_text += f"{log}\n"
            if len(raw_logs) > 10:
                ref_text += f"... (생략됨, 총 {len(raw_logs)} 라인) ...\n"
            ref_text += "```\n"

        raw_req = meta.get('raw_request')
        raw_resp = meta.get('raw_response')
        if raw_req or raw_resp:
            ref_text += "```text\n"
            if raw_req:
                ref_text += f"[REQ]  {raw_req}\n"
            if raw_resp:
                ref_text += f"[RESP] {raw_resp}\n"
            ref_text += "```\n"

        ref_text += "---\n"

    return ref_text

def _render_quick_prompt_guide():
    st.info("**AI 분석 질의 가이드** (분석 카테고리를 명시하면 정확도가 향상됩니다)")

    with st.expander("질문 예시 확인"):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(
                """
                **Call 및 무선 환경 분석**
                * "해당 로그에서 발생한 **Call Fail**의 주요 원인을 분석해 주십시오."
                * "통화 중 기록된 **IMS 에러** 로그를 추출해 주십시오."
                * "현재 **망 이탈(OOS)**이 발생한 구간 내역을 요약해 주십시오."
                """
            )
        with col2:
            st.markdown(
                """
                **단말 성능 및 네트워크 세션 분석**
                * "최근 1시간 내 발생한 **배터리 급방전** 원인을 리포트해 주십시오."
                * "특정 패키지에서 **DNS 차단**이 발생한 이력을 확인해 주십시오."
                * "네트워크 **지연(Latency)** 관련 통계를 시각화해 주십시오."
                """
            )

def _render_quick_prompt_buttons():
    st.caption("질의어를 직접 입력하거나, 하단의 Quick Prompt 버튼을 활용하십시오.")

    quick_prompt = None

    col_btn1, col_btn2, col_btn3 = st.columns(3)
    col_btn4, col_btn5, col_btn6 = st.columns(3)
    col_btn7, _, _ = st.columns(3)

    with col_btn1:
        if st.button("통화 끊김(Drop) 분석", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('call_drop')
    with col_btn2:
        if st.button("데이터 네트워크 이상 분석", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('data_network_issue')
    with col_btn3:
        if st.button("배터리/Crash 통합 분석", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('battery_crash')
    with col_btn4:
        if st.button("망 등록(Reg) 및 OOS 분석", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('network_oos')
    with col_btn5:
        if st.button("안테나(Signal) 레벨 분석", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('antenna_level_analysis')
    with col_btn6:
        if st.button("VoLTE/SIP 상세 분석", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('volte_sip_analysis')
    with col_btn7:
        if st.button("인터넷 응답 지연 종합 분석", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('internet_stall_analysis')

    return quick_prompt


def _get_current_health_kpi():
    current_target = st.session_state.get("current_file", None)
    if not current_target:
        return None

    current_base = current_target.replace("_payload.json", "")
    if not current_base:
        return None

    return get_device_health_kpi(current_base)

def _render_chat_answer(engine, prompt):
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("로그 데이터 및 과거 해결 사례를 분석 중입니다..."):
            current_target = st.session_state.get("current_file", None)
            health_kpi_json = _get_current_health_kpi()

            answer, ids, metas, thinking = engine.ask(
                prompt,
                current_file=current_target,
                chat_history=st.session_state.messages[-5:],
                health_kpi=health_kpi_json,
            )

            ref_text = _build_reference_text(metas)

            if thinking:
                with st.expander("AI Reasoning Trace"):
                    st.markdown(f"```text\n{thinking}\n```")

            st.markdown(answer)
            st.session_state.last_ids = ids
            st.session_state.last_metas = metas

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
        "references": ref_text,
        "metas": metas,
        "thinking": thinking,
    })
    st.rerun()

def render_chat_tab(engine):
    _render_quick_prompt_guide()
    quick_prompt = _render_quick_prompt_buttons()

    st.divider()
    render_chat_interface(engine, key_suffix="main", show_input=False)

    user_input = st.chat_input("에러 증상 또는 분석 요청 사항을 입력하십시오")
    prompt = quick_prompt if quick_prompt else user_input

    if prompt:
        _render_chat_answer(engine, prompt)