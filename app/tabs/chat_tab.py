
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
    st.info("**질의 가이드**  분석 대상과 증상을 함께 입력하면 관련 근거를 더 정확히 확인할 수 있습니다.")

    with st.expander("질문 예시"):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(
                """
                **통화 및 무선 환경**
                * "Call Fail 원인과 관련 로그를 확인해 주세요."
                * "통화 중 IMS 오류가 발생했는지 확인해 주세요."
                * "OOS 발생 구간과 원인을 요약해 주세요."
                """
            )
        with col2:
            st.markdown(
                """
                **단말 상태 및 네트워크**
                * "배터리 소모와 관련된 이상 로그를 확인해 주세요."
                * "특정 앱에서 DNS 차단이 있었는지 확인해 주세요."
                * "인터넷 지연 또는 연결 실패 구간을 요약해 주세요."
                """
            )

def _render_quick_prompt_buttons():
    st.caption("직접 입력하거나 아래 빠른 질문을 선택할 수 있습니다.")

    quick_prompt = None

    col_btn1, col_btn2, col_btn3 = st.columns(3)
    col_btn4, col_btn5, col_btn6 = st.columns(3)
    col_btn7, _, _ = st.columns(3)

    with col_btn1:
        if st.button("통화 끊김 확인", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('call_drop')
    with col_btn2:
        if st.button("데이터 연결 확인", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('data_network_issue')
    with col_btn3:
        if st.button("배터리·Crash 확인", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('battery_crash')
    with col_btn4:
        if st.button("망 등록/OOS 확인", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('network_oos')
    with col_btn5:
        if st.button("Signal Level 확인", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('antenna_level_analysis')
    with col_btn6:
        if st.button("VoLTE/SIP 확인", width="stretch"):
            quick_prompt = QUICK_PROMPTS.get('volte_sip_analysis')
    with col_btn7:
        if st.button("인터넷 지연 확인", width="stretch"):
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
        with st.spinner("관련 로그와 분석 기록을 확인하는 중입니다..."):
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
                with st.expander("처리 과정", expanded=False):
                    st.markdown(f"```text\n{thinking}\n```")

            st.markdown(answer)
            st.session_state.last_ids = ids
            st.session_state.last_metas = metas

            # Show PLM Comment button directly after answer
            active_defect = st.session_state.get('plm_active_defect_code')
            if active_defect:
                st.divider()
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.caption(f"📌 활성 결함: `{active_defect}`")
                with col2:
                    if st.button("📝 Comment로 등록", key="plm_comment_button"):
                        st.session_state.plm_current_analysis_result = {
                            'answer': answer,
                            'from_chat': True
                        }
                        st.info("💬 PLM 결함 관리 > 댓글 탭에서 등록할 수 있습니다")

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
        "references": ref_text,
        "metas": metas,
        "thinking": thinking,
    })
    # Do NOT call st.rerun() here - it causes infinite loops

def render_chat_tab(engine):
    _render_quick_prompt_guide()
    quick_prompt = _render_quick_prompt_buttons()

    st.divider()

    # Check for PLM problem query - only auto-analyze if it's newly added
    plm_problem = st.session_state.get('plm_problem_query')
    plm_problem_analyzed = st.session_state.get('plm_problem_analyzed', False)

    if plm_problem and not plm_problem_analyzed:
        # First time seeing this PLM problem - auto-analyze it
        st.info(
            f"📋 **PLM 결함에서 문제 내용을 가져왔습니다**  \n"
            f"결함 코드: `{plm_problem.get('defect_code')}`  \n"
            f"제목: {plm_problem.get('defect_title')}"
        )

        st.divider()

        # Show refined vs original content comparison
        problem_content = plm_problem.get('content', '')
        original_content = plm_problem.get('original_content', problem_content)

        if original_content != problem_content:
            with st.expander("📝 문제 내용 (정제된 내용 / 원본)", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**✅ 정제된 내용**")
                    st.text(problem_content)
                with col2:
                    st.markdown("**📋 원본 내용**")
                    st.text(original_content)
                st.caption("💡 정제된 내용이 분석에 사용됩니다.")
            st.divider()

        # Auto-analyze the PLM problem
        auto_prompt = f"PLM 결함 분석:\n결함 코드: {plm_problem.get('defect_code')}\n\n**문제 내용:**\n{problem_content}\n\n위 문제에 대해 분석해 주세요."

        render_chat_interface(engine, key_suffix="main", show_input=False)
        _render_chat_answer(engine, auto_prompt)

        # Mark as analyzed so we don't loop
        st.session_state.plm_problem_analyzed = True

    elif plm_problem and plm_problem_analyzed:
        # Already analyzed, show info and clear button
        st.info(
            f"📋 **PLM 결함 분석 중**  \n"
            f"결함 코드: `{plm_problem.get('defect_code')}`"
        )

        col1, col2 = st.columns([3, 1])
        with col2:
            if st.button("❌ 삭제 및 초기화", key="clear_plm_query"):
                st.session_state.plm_problem_query = None
                st.session_state.plm_problem_analyzed = False
                st.rerun()

        st.divider()

    render_chat_interface(engine, key_suffix="main", show_input=False)

    st.divider()

    # Placeholder text changes based on PLM query availability
    placeholder_text = "증상 또는 확인할 내용을 입력하세요"
    if plm_problem and plm_problem_analyzed:
        placeholder_text = "추가 질문을 입력하세요"

    user_input = st.chat_input(placeholder_text)

    # Determine prompt source priority: quick_prompt > user_input
    prompt = None

    if quick_prompt:
        prompt = quick_prompt
    elif user_input:
        prompt = user_input

    if prompt:
        _render_chat_answer(engine, prompt)