import streamlit as st
import pandas as pd
import time

def _recommend_knowledge_category(text, categories):
    """코멘트 내용을 분석하여 적절한 로그 카테고리를 추천합니다."""
    mapping = {
        "Call_Session": ["call", "드랍", "drop", "통화", "fail", "ims", "volte"],
        "Battery_Drain_Report": ["배터리", "battery", "drain", "방전", "열", "thermal", "소모"],
        "OOS_Event": ["oos", "이탈", "서비스", "service", "reg", "등록"],
        "Signal_Level": ["신호", "signal", "안테나", "level", "수신"],
        "Network_DNS_Issue": ["dns", "차단", "block", "인터넷", "지연", "latency"],
        "Crash_Event": ["크래시", "crash", "죽었", "강제종료", "anr", "am_kill", "panic", "패닉"]
    }
    text_lower = text.lower()
    for cat, keywords in mapping.items():
        if any(kw in text_lower for kw in keywords):
            return cat
    return "Total_Report"

def render_knowledge_tab(engine):
    """사내 지식 베이스 메인 렌더링 함수"""
    st.title("📚 사내 장애 분석 지식 베이스 (Knowledge Base)")
    st.markdown("엔지니어들이 등록한 단말 장애 분석 사례와 해결 방안을 조회하고 새로운 지식을 등록합니다.")

    # 탭을 2개로 나누어 깔끔하게 구성
    tab_search, tab_register = st.tabs(["🔍 사례 검색 및 조회", "➕ 신규 사례 등록"])

    with tab_search:
        _render_search_ui(engine)

    with tab_register:
        _render_registration_ui(engine)

def _render_search_ui(engine):
    """등록된 지식 베이스를 조회, 필터링, 상세 보기하는 UI"""
    try:
        kb_data = engine.knowledge_collection.get()
    except Exception as e:
        st.error(f"지식 베이스 DB를 불러오는 중 오류가 발생했습니다: {e}")
        return

    if not kb_data or not kb_data.get("ids"):
        st.info("아직 등록된 지식/사례가 없습니다. '신규 사례 등록' 탭에서 분석 코멘트를 남겨보세요!")
        return

    rows = []
    for doc_id, doc, meta in zip(kb_data["ids"], kb_data["documents"], kb_data["metadatas"]):
        rows.append({
            "ID": doc_id[:8],
            "단말 모델": meta.get("model_name", "Unknown"),
            "AP (HW)": meta.get("hardware", "-"),
            "OS / SDK": meta.get("android_sdk", "-"),
            "Severity": meta.get("severity", "Normal"),
            "Radio 펌웨어": meta.get("radio", "-"),
            "Kernel": meta.get("kernel", "-"),
            "참조 로그 ID": meta.get("target_ids", "-"),
            "분석 및 해결방안": doc
        })

    df = pd.DataFrame(rows)

    st.subheader("필터링")
    col1, col2, col3, col4 = st.columns(4)

    models = ["전체"] + sorted(list(df["단말 모델"].astype(str).unique()))
    hws = ["전체"] + sorted(list(df["AP (HW)"].astype(str).unique()))
    sdks = ["전체"] + sorted(list(df["OS / SDK"].astype(str).unique()))
    severities = ["전체"] + sorted(list(df["Severity"].astype(str).unique()))

    selected_model = col1.selectbox("📱 단말 모델", models)
    selected_hw = col2.selectbox("⚙️ AP (Hardware)", hws)
    selected_sdk = col3.selectbox("🤖 Android SDK", sdks)
    selected_severity = col4.selectbox("🚨 심각도 (Severity)", severities)

    filtered_df = df.copy()
    if selected_model != "전체": filtered_df = filtered_df[filtered_df["단말 모델"] == selected_model]
    if selected_hw != "전체": filtered_df = filtered_df[filtered_df["AP (HW)"] == selected_hw]
    if selected_sdk != "전체": filtered_df = filtered_df[filtered_df["OS / SDK"] == selected_sdk]
    if selected_severity != "전체": filtered_df = filtered_df[filtered_df["Severity"] == selected_severity]

    st.markdown(f"**총 {len(filtered_df)}건의 사례가 검색되었습니다.**")
    st.dataframe(filtered_df[["단말 모델", "AP (HW)", "OS / SDK", "Severity", "분석 및 해결방안"]], width='stretch', hide_index=True)

    st.markdown("---")
    st.subheader("📖 상세 사례 목록")

    if filtered_df.empty:
        st.warning("조건에 맞는 검색 결과가 없습니다.")
    else:
        for idx, row in filtered_df.iterrows():
            sev = row["Severity"].lower()
            icon = "🔴" if sev in ["critical", "high"] else ("🟡" if sev == "major" or sev == "medium" else "🟢")
            title_summary = row['분석 및 해결방안'][:60].replace('\n', ' ') + "..."

            with st.expander(f"{icon} [{row['단말 모델']}] {title_summary}"):
                st.markdown(f"**📝 분석 코멘트 / 가이드:**\n\n{row['분석 및 해결방안']}")
                st.markdown("---")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("단말 모델", row["단말 모델"])
                c2.metric("AP (HW)", row["AP (HW)"])
                c3.metric("OS (SDK)", row["OS / SDK"])
                c4.metric("Severity", row["Severity"])
                st.markdown(f"- **Radio Firmware:** `{row['Radio 펌웨어']}`\n- **Kernel Version:** `{row['Kernel']}`")
                st.caption(f"참조 로그 Document ID: {row['참조 로그 ID']} | 지식 DB ID: {row['ID']}")

def _render_registration_ui(engine):
    """현재 세션의 분석 로그를 바탕으로 새로운 지식을 등록하는 UI"""
    st.markdown("### 💡 방금 분석한 로그 이슈를 지식 베이스에 박제합니다.")

    if not (st.session_state.get("last_ids") and st.session_state.get("last_metas")):
        st.warning("현재 대화 세션에서 참조된 로그 내역이 없습니다. 메인 탭에서 먼저 로그를 질의/분석해주세요.")
        return

    retrieved_types = list(set(m.get('log_type', 'Unknown') for m in st.session_state.last_metas if m))
    category_options = ["Total_Report"] + retrieved_types

    if "feedback_key" not in st.session_state:
        st.session_state.feedback_key = 0

    feedback = st.text_area(
        "해결 방안 및 분석 코멘트를 자유롭게 입력하세요:",
        height=200,
        key=f"fb_{st.session_state.feedback_key}",
        placeholder="예) RIL에서 Modem Not Responding(MNR) 발생 후 강제 패닉(Force CP CRASH) 유발됨. Radio 펌웨어 업데이트(XX 버전) 필요함."
    )

    recommended = _recommend_knowledge_category(feedback, category_options)
    default_idx = category_options.index(recommended) if recommended in category_options else 0

    col1, col2 = st.columns(2)
    target_type = col1.selectbox("분류 카테고리 (자동 추천)", category_options, index=default_idx)
    severity = col2.radio("이슈 중요도 (Severity)", ["Critical", "Major", "Minor", "Info"], index=0, horizontal=True)

    if st.button("🚀 사내 지식 베이스에 공식 사례 등록", type="primary", use_container_width=True):
        if feedback.strip():
            if target_type == "Total_Report":
                target_ids = st.session_state.last_ids
            else:
                target_ids = [
                    doc_id for doc_id, meta in zip(st.session_state.last_ids, st.session_state.last_metas)
                    if meta and meta.get('log_type') == target_type
                ]

            if target_ids:
                # 🚨 [수정] 현재 세션의 로그 메타데이터에서 단말 정보를 추출합니다.
                base_meta = {}
                for m in st.session_state.last_metas:
                    if m:
                        base_meta = m
                        break

                # RilRagChat 엔진이 요구하는 형식에 맞춰 build_info 딕셔너리 생성
                build_info_dict = {
                    "model_name": base_meta.get("model_name", "Unknown"),
                    "hardware": base_meta.get("hardware", "Unknown"),
                    "android_sdk": base_meta.get("android_sdk", "Unknown"),
                    "radio": base_meta.get("radio", "Unknown"),
                    "kernel": base_meta.get("kernel", "Unknown")
                }

                # 🚨 [수정] save_knowledge 호출 시 build_info 파라미터로 묶어서 전달
                engine.save_knowledge(
                    target_ids,
                    feedback,
                    severity=severity,
                    build_info=build_info_dict
                )

                st.success(f"✅ [{target_type}] 카테고리에 {severity} 등급으로 지식이 성공적으로 등록되었습니다!")

                # 등록 폼 초기화 트릭
                st.session_state.feedback_key += 1
                time.sleep(1.5)
                st.rerun()

        else:
            st.error("분석 코멘트가 비어있습니다. 내용을 입력해주세요.")

