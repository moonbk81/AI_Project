import pandas as pd
import plotly.express as px
import streamlit as st

from app.helpers import generate_unique_key
from ui.common import parse_raw_logs


def _render_assistant_visual_references(msg, key_suffix, msg_idx):
    sig_history = []
    reg_history = []
    reg_map = {
        "IN_SERVICE": 0, "OUT_OF_SERVICE": 1,
        "EMERGENCY_ONLY": 2, "POWER_OFF": 3
    }

    for i, meta in enumerate(msg.get("metas", [])):
        if meta.get('log_type') == 'Battery_Drain_Report':
            signal_data = {
                "None": float(meta.get("signal_strength_distribution_none", 0.0)),
                "Poor": float(meta.get("signal_strength_distribution_poor", 0.0)),
                "Moderate": float(meta.get("signal_strength_distribution_moderate", 0.0)),
                "Good": float(meta.get("signal_strength_distribution_good", 0.0)),
                "Great": float(meta.get("signal_strength_distribution_great", 0.0))
            }
            filtered_data = {k: v for k, v in signal_data.items() if v > 0}
            if filtered_data:
                df_signal = pd.DataFrame(list(filtered_data.items()), columns=['Level', 'Value'])
                fig = px.pie(
                    df_signal,
                    names='Level',
                    values='Value',
                    title=f"[Reference {i+1}] Signal Strength Distribution",
                    hole=0.4
                )
                unique_key = generate_unique_key(f"chart_{key_suffix}_{msg_idx}_{i}", str(fig.to_json()[:100]))
                st.plotly_chart(fig, width="stretch", key=unique_key)

        if meta.get('log_type') == 'OOS_Event':
            v_reg = meta.get('voice_reg', 'UNKNOWN').upper()
            d_reg = meta.get('data_reg', 'UNKNOWN').upper()
            slot = f"Slot{meta.get('slotId', '0')}"
            time = meta.get('time')
            if time:
                reg_history.append({"time": time, "Status": reg_map.get(v_reg, -1), "Type": "Voice", "Slot": slot, "Label": v_reg})
                reg_history.append({"time": time, "Status": reg_map.get(d_reg, -1), "Type": "Data", "Slot": slot, "Label": d_reg})

        if meta.get('log_type') == 'Signal_Level':
            lvl = meta.get('level')
            rt = meta.get('rat', 'Unknown')
            sl = meta.get('slot', '0')
            tm = meta.get('time')
            if tm and lvl is not None:
                sig_history.append({"time": tm, "Slot": f"Slot {sl}", "RAT": str(rt), "Level": int(lvl), "Info": meta.get('raw_info', '')})


def _build_reference_text(metas):
    ref_text = ""
    for i, meta in enumerate(metas):
        known_solution = meta.get('known_solution')
        solution_badge = " [과거 해결 사례 포함]" if known_solution else ""
        ref_text += f"### 자료 {i+1} (시간: {meta.get('time', 'N/A')}, 슬롯: {meta.get('slot', 'N/A')}){solution_badge}\n"
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


def _render_existing_messages(key_suffix, show_plm_button=False):
    for msg_idx, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant" and msg.get("thinking"):
                with st.expander("AI Reasoning Trace"):
                    st.markdown(f"```text\n{msg['thinking']}\n```")

            st.markdown(msg["content"])

            if "metas" in msg and msg["metas"]:
                _render_assistant_visual_references(msg, key_suffix, msg_idx)

            if "references" in msg and msg["references"]:
                with st.expander(f"Reference Logs ({key_suffix})"):
                    st.markdown(msg["references"])

            # Show PLM Comment button for last assistant message
            if show_plm_button and msg["role"] == "assistant" and msg_idx == len(st.session_state.messages) - 1:
                st.divider()
                # Import here to avoid circular dependency
                from app.tabs.chat_tab import _render_plm_comment_button
                _render_plm_comment_button(msg["content"])


def render_chat_interface(engine, key_suffix="main", show_input=True, show_plm_button=False):
    _render_existing_messages(key_suffix, show_plm_button=show_plm_button)

    if not show_input:
        return

    if prompt := st.chat_input("질의어를 입력하십시오", key=f"chat_input_{key_suffix}"):
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("assistant"):
            with st.spinner("분석 진행 중..."):
                current_target = st.session_state.get("current_file", None)
                clean_history = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state.messages[-5:]
                ]

                answer, ids, metas, thinking = engine.ask(
                    prompt,
                    current_file=current_target,
                    chat_history=clean_history
                )

                ref_text = _build_reference_text(metas)

                if thinking:
                    with st.expander("AI Reasoning Trace"):
                        st.markdown(f"```text\n{thinking}\n```")

                st.markdown(answer)
                st.session_state.messages.append({
                    "role": "assistant", "content": answer,
                    "references": ref_text, "metas": metas, "thinking": thinking
                })
                st.session_state.last_ids = ids
                st.session_state.last_metas = metas
                st.rerun()
