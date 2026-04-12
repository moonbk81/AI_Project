import os
import streamlit as st
import json
import time
import pandas as pd
import plotly.express as px  # 통계 그래프용

# 1. 백엔드 엔진 및 자동화 모듈 불러오기
from ril_rag_chat import RilRagChat
from telephony_log_summarizer import TelephonyLogSummarizer
from prepare_rag_payload import RagPayloadBuilder

# 1. 페이지 기본 설정
st.set_page_config(page_title="RIL 로그 분석기", page_icon="📡", layout="wide")

@st.cache_resource(show_spinner="AI 엔진과 Vector DB를 부팅 중입니다...")
def load_engine():
    return RilRagChat()

try:
    engine = load_engine()
except Exception as e:
    st.error(f"엔진 초기화 실패. 터미널에서 ollama가 실행 중인지 확인하세요.\n에러: {e}")
    st.stop()

st.title("📡 안드로이드 RIL RAG 분석기")
st.markdown("단말 통신 로그를 원클릭으로 적재하고 AI와 분석을 시작하세요.")

# 세션 상태 초기화
if "messages" not in st.session_state: st.session_state.messages = []
if "last_ids" not in st.session_state: st.session_state.last_ids = []
if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0
if "feedback_key" not in st.session_state: st.session_state.feedback_key = 0
if "current_file" not in st.session_state: st.session_state.current_file = None

# ==========================================
# [추가 1] 탭(Tab) 구조 분리 (분석 vs 대시보드)
# ==========================================
tab_chat, tab_dash = st.tabs(["💬 로그 분석 및 대화", "📊 전사 로그 통계 대시보드"])

# 사이드바 (두 탭 모두에서 보이도록 상단에 위치)
with st.sidebar:
    st.header("⚙️ 1-Click 자동 분석 파이프라인")
    uploaded_file = st.file_uploader(
        "📁 원시 로그 파일 업로드", 
        type=['txt', 'log'], 
        key=f"uploader_{st.session_state.uploader_key}"
    )
    
    if st.button("🚀 분석 및 DB 적재 시작", use_container_width=True, type="primary"):
        if uploaded_file is None:
            st.error("❌ 먼저 파일을 업로드해주세요.")
        else:
            with st.status("자동화 파이프라인 가동 중...", expanded=True) as status:
                try:
                    os.makedirs("./temp_logs", exist_ok=True)
                    temp_raw_path = os.path.join("./temp_logs", uploaded_file.name)

                    with open(temp_raw_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())

                    filename = uploaded_file.name
                    base_name = os.path.splitext(filename)[0]

                    os.makedirs("./result", exist_ok=True)
                    temp_json_path = f"./result/{base_name}_report.json"
                    payload_filename = f"{base_name}_payload.json"

                    st.write("1️⃣ 원시 로그 분석 및 필터링 중... (Parser)")
                    parser = TelephonyLogSummarizer(temp_raw_path)
                    parser.run_batch('all', temp_json_path)

                    st.write("2️⃣ RAG 맞춤형 지식 조각으로 변환 중... (Payload Builder)")
                    builder = RagPayloadBuilder(temp_json_path)
                    builder.build_payload(payload_filename) 

                    st.write("3️⃣ Vector DB 임베딩 및 적재 중... (BGE-M3)")
                    engine.ingest_folder("./payloads")

                    status.update(label="✅ 파이프라인 완료! 채팅창에 질문을 입력하세요.", state="complete", expanded=False)
                    st.session_state.current_file = filename
                    
                    st.toast(f"'{filename}' 분석 완료! 채팅창에 질문해주세요.", icon="✅")
                    st.session_state.uploader_key += 1 
                    time.sleep(1) 
                    st.rerun() 

                except Exception as e:
                    status.update(label="❌ 파이프라인 실패", state="error")
                    st.error(f"오류가 발생했습니다: {e}")

    st.divider()

    st.header("📝 사내 지식 베이스 (트랙 B)")
    if st.session_state.last_ids:
        feedback = st.text_area(
            "해결책 / 원인 코멘트 입력:", 
            height=150, 
            key=f"feedback_{st.session_state.feedback_key}" 
        )
        if st.button("💾 DB에 영구 박제", use_container_width=True):
            if feedback.strip():
                engine.save_knowledge(st.session_state.last_ids, feedback)
                st.toast("✅ 성공적으로 박제되었습니다!", icon="💾") 
                st.session_state.last_ids = []
                st.session_state.feedback_key += 1 
                time.sleep(0.5) 
                st.rerun() 
            else:
                st.warning("코멘트를 먼저 입력해주세요.")
    else:
        st.info("먼저 채팅창에서 로그 분석을 진행해주세요.")

# ==========================================
# [Tab 1] 대화 및 분석 창
# ==========================================
with tab_chat:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if "references" in msg and msg["references"]:
                with st.expander("🔎 참고 원본 로그 및 과거 사례 보기"):
                    st.markdown(msg["references"])

    if prompt := st.chat_input("에러 증상이나 궁금한 점을 입력하세요 (예: Call fail 원인 찾아줘)"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("로그를 분석하고 과거 사례를 탐색 중입니다... 🕵️‍♂️"):
                current_target = st.session_state.get("current_file", None)
                
                # [추가 2] 과거 대화 맥락(최근 4번의 턴)을 함께 넘겨줍니다.
                recent_history = st.session_state.messages[-5:-1] if len(st.session_state.messages) > 1 else None
                
                # engine.ask()에 chat_history 파라미터 추가 전달
                answer, ids, metas = engine.ask(prompt, current_file=current_target, chat_history=recent_history)
                st.markdown(answer)
                
                ref_text = ""
                for i, meta in enumerate(metas):
                    known_solution = meta.get('known_solution')
                    solution_badge = " **[💡과거 해결사례 존재]**" if known_solution else ""
                    ref_text += f"### 자료 {i+1} (시간: {meta.get('time', 'N/A')}, 슬롯: {meta.get('slot', 'N/A')}){solution_badge}\n"
                    
                    if known_solution: ref_text += f"> **과거 분석 기록:** {known_solution}\n\n"

                    raw_data = meta.get('raw_logs', meta.get('raw_context', meta.get('raw_stack', '[]')))
                    try: raw_logs = json.loads(raw_data) if isinstance(raw_data, str) else []
                    except: raw_logs = []

                    if raw_logs:
                        ref_text += "```text\n"
                        for log in raw_logs[:5]: ref_text += f"{log}\n"
                        if len(raw_logs) > 5: ref_text += "... (중략) ...\n"
                        ref_text += "```\n"
                    
                    raw_req, raw_resp = meta.get('raw_request'), meta.get('raw_response')
                    if raw_req or raw_resp:
                        ref_text += "```text\n"
                        if raw_req: ref_text += f"[REQ]  {raw_req}\n"
                        if raw_resp: ref_text += f"[RESP] {raw_resp}\n"
                        ref_text += "```\n"
                    ref_text += "---\n"
                
                if ref_text:
                    with st.expander("🔎 참고 원본 로그 및 과거 사례 보기"):
                        st.markdown(ref_text)
                
                st.session_state.last_ids = ids

        st.session_state.messages.append({
            "role": "assistant", 
            "content": answer,
            "references": ref_text
        })
        st.rerun()

# ==========================================
# [Tab 2] 대시보드 창
# ==========================================
with tab_dash:
    st.header("📈 전사 로그 데이터 시각화")
    st.markdown("Vector DB에 축적된 로그 데이터의 통계와 박제된 지식을 한눈에 확인합니다.")
    
    # DB에서 메타데이터만 쫙 긁어옵니다.
    all_data = engine.collection.get(include=["metadatas"])
    if all_data and all_data.get("metadatas"):
        meta_list = [m for m in all_data["metadatas"] if m is not None]
        if meta_list:
            df = pd.DataFrame(meta_list)
            
            # 상단 지표 요약
            col1, col2, col3 = st.columns(3)
            col1.metric("총 적재된 지식 조각", f"{len(df)} 건")
            col2.metric("분석된 로그 파일 수", f"{df['source_file'].nunique()} 개" if 'source_file' in df.columns else "0 개")
            col3.metric("해결된(박제된) 사례 수", f"{df['known_solution'].notna().sum()} 건" if 'known_solution' in df.columns else "0 건")
            
            st.divider()
            
            # 그래프 영역
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("🚩 에러 유형별 분포 (Log Type)")
                if 'log_type' in df.columns:
                    fig1 = px.pie(df, names='log_type', hole=0.4)
                    st.plotly_chart(fig1, use_container_width=True)
                else:
                    st.info("Log Type 데이터가 없습니다.")
                    
            with c2:
                st.subheader("📱 파일별 에러 비중")
                if 'source_file' in df.columns:
                    file_counts = df['source_file'].value_counts().reset_index()
                    file_counts.columns = ['source_file', 'count']
                    fig2 = px.bar(file_counts, x='count', y='source_file', orientation='h')
                    st.plotly_chart(fig2, use_container_width=True)
                else:
                    st.info("파일 이름 데이터가 없습니다.")
            
            st.divider()
            
            # 박제된 지식 리스트
            st.subheader("💡 사내 지식 베이스 (해결 사례 모음)")
            if 'known_solution' in df.columns:
                solution_df = df.dropna(subset=['known_solution'])[['source_file', 'log_type', 'known_solution']]
                if not solution_df.empty:
                    st.dataframe(solution_df, use_container_width=True)
                else:
                    st.info("아직 박제된 지식(해결책)이 없습니다. 로그 분석 후 코멘트를 달아주세요!")
            else:
                st.info("알려진 솔루션 데이터 필드가 없습니다.")
        else:
            st.warning("데이터 형식이 올바르지 않습니다.")
    else:
        st.info("DB가 비어있습니다. 첫 번째 로그 파일을 업로드해주세요!")