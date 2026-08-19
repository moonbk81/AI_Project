import pandas as pd
import plotly.express as px
import streamlit as st

from app.helpers import generate_unique_key


def _render_assistant_visual_references(msg, key_suffix, msg_idx):
    """Render charts for reference metadata attached to an assistant message.

    Only Battery_Drain_Report produces a chart today. OOS_Event / Signal_Level
    branches used to accumulate reg_history / sig_history lists that nothing ever
    read or plotted, so they were removed rather than left as silent no-ops.
    """
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


def _render_existing_messages(key_suffix):
    for msg_idx, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                # 로그 원본을 먼저 표시 (사용자가 근거를 먼저 확인하고 분석 읽음)
                if "references" in msg and msg["references"]:
                    with st.expander(f"📄 Reference Logs (근거 로그 원문)", expanded=True):
                        st.markdown(msg["references"])

                # AI 추론 과정 (선택사항)
                if msg.get("thinking"):
                    with st.expander("🧠 AI Reasoning Trace"):
                        st.markdown(f"```text\n{msg['thinking']}\n```")

                # 분석 결과 (마지막)
                st.markdown(msg["content"])

                # 시각화 자료
                if "metas" in msg and msg["metas"]:
                    _render_assistant_visual_references(msg, key_suffix, msg_idx)


def render_chat_history(key_suffix="main"):
    """Render the conversation so far.

    The chat input and the LLM call live in app/tabs/chat_tab.py
    (``_render_chat_answer``). This module only replays history: it used to carry
    a second, parallel chat-input implementation that every caller disabled via
    ``show_input=False``, and which — being unreachable — had silently drifted
    out of sync (it never passed ``health_kpi``). Keeping one input path avoids
    that class of divergence.
    """
    _render_existing_messages(key_suffix)
