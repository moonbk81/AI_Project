import os
import streamlit as st
import json
import time
import pandas as pd
import plotly.express as px
import re  # [추가] 정규표현식 (슬라이싱 용도)

# 1. 백엔드 엔진 및 자동화 모듈 불러오기
from ril_rag_chat import RilRagChat
from telephony_log_summarizer import TelephonyLogSummarizer
from prepare_rag_payload import RagPayloadBuilder

# ==========================================
# [신규 추가] 대용량 로그 타임라인 슬라이서 함수
# ==========================================
def slice_log_by_time(input_path, output_path, start_time_str, end_time_str):
    """지정된 시간대의 로그만 스트리밍으로 추출하여 초고속으로 잘라냅니다."""
    pattern = re.compile(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})')
    written_lines = 0
    is_in_range = False

    with open(input_path, 'r', encoding='utf-8', errors='ignore') as fin, \
         open(output_path, 'w', encoding='utf-8') as fout:
        
        for line in fin:
            match = pattern.search(line)
            if match:
                current_time = match.group(1)
                if start_time_str <= current_time <= end_time_str:
                    is_in_range = True
                elif current_time > end_time_str:
                    break  # 최적화: 종료 시간 넘으면 루프 즉시 탈출
                else:
                    is_in_range = False

            if is_in_range:
                fout.write(line)
                written_lines += 1

    return written_lines

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

tab_chat, tab_dash = st.tabs(["💬 로그 분석 및 대화", "📊 전사 로그 통계 대시보드"])

# ==========================================
# 3. 사이드바: 파일 업로드 & 슬라이싱 옵션
# ==========================================
with st.sidebar:
    st.header("⚙️ 1-Click 자동 분석 파이프라인")
    uploaded_file = st.file_uploader(
        "📁 원시 로그 파일 업로드", 
        type=['txt', 'log'], 
        key=f"uploader_{st.session_state.uploader_key}"
    )
    
    st.divider()
    st.subheader(" 현재 분석 세션 정보")
    if "current_file" in st.session_state and st.session_state.current_file:
        st.success(f"활성 파일: '{st.session_state.current_file}'")
    else:
        st.warning("활성된 로그 파일이 없습니다.")
    
    # [추가] 슬라이싱 UI
    use_slicing = st.checkbox("✂️ 특정 시간대만 잘라서 분석 (2GB 이상 권장)")
    start_time, end_time = "", ""
    if use_slicing:
        st.info("💡 에러 발생 시점 주변 5~10분만 잘라내면 분석 속도가 100배 빨라집니다.")
        col1, col2 = st.columns(2)
        with col1: start_time = st.text_input("시작 (예: 04-12 14:00:00)")
        with col2: end_time = st.text_input("종료 (예: 04-12 14:15:00)")
    
    if st.button("🚀 분석 및 DB 적재 시작", use_container_width=True, type="primary"):
        if uploaded_file is None:
            st.error("❌ 먼저 파일을 업로드해주세요.")
        elif use_slicing and (not start_time or not end_time):
            st.error("❌ 슬라이싱을 켰다면 시작/종료 시간을 모두 입력해주세요.")
        else:
            with st.status("자동화 파이프라인 가동 중...", expanded=True) as status:
                try:
                    os.makedirs("./temp_logs", exist_ok=True)
                    temp_raw_path = os.path.join("./temp_logs", uploaded_file.name)

                    # 한 번에 메모리에 올리지 않고 64KB씩 안전하게 나눠서 디스크에 씁니다.
                    with open(temp_raw_path, "wb") as f:
                        while chunk := uploaded_file.read(65536):
                            f.write(chunk)

                    filename = uploaded_file.name
                    base_name = os.path.splitext(filename)[0]
                    target_log_path = temp_raw_path 
                    
                    # [추가] 슬라이싱 로직 가동
                    if use_slicing:
                        st.write(f"✂️ 타임라인 슬라이싱 중... ({start_time} ~ {end_time})")
                        sliced_path = os.path.join("./temp_logs", f"sliced_{filename}")
                        lines_kept = slice_log_by_time(temp_raw_path, sliced_path, start_time, end_time)
                        
                        if lines_kept == 0:
                            st.error("⚠️ 입력한 시간대에 해당하는 로그가 없습니다.")
                            st.stop()
                        st.write(f"✅ 슬라이싱 완료! (총 {lines_kept:,}줄 추출됨)")
                        target_log_path = sliced_path # 파서에게 넘길 대상을 가벼운 파일로 교체

                    os.makedirs("./result", exist_ok=True)
                    temp_json_path = f"./result/{base_name}_report.json"
                    payload_filename = f"{base_name}_payload.json"

                    st.write("1️⃣ 원시 로그 분석 및 필터링 중... (Parser)")
                    parser = TelephonyLogSummarizer(target_log_path) # 교체된 타겟 전달
                    parser.run_batch('all', temp_json_path)

                    st.write("2️⃣ RAG 맞춤형 지식 조각으로 변환 중...")
                    builder = RagPayloadBuilder(temp_json_path)
                    builder.build_payload(payload_filename) 

                    st.write("3️⃣ Vector DB 임베딩 및 적재 중...")
                    engine.ingest_folder("./payloads")

                    status.update(label="✅ 파이프라인 완료! 채팅창에 질문을 입력하세요.", state="complete", expanded=False)
                    
                    st.session_state.current_file = filename
                    # [수정] 새 파일 업로드 시 이전 대화 및 박제 대기열 초기화
                    st.session_state.last_ids = []
                    st.session_state.messages = []
                    
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
                recent_history = st.session_state.messages[-5:-1] if len(st.session_state.messages) > 1 else None

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
    
    all_data = engine.collection.get(include=["metadatas"])
    if all_data and all_data.get("metadatas"):
        meta_list = [m for m in all_data["metadatas"] if m is not None]
        if meta_list:
            df = pd.DataFrame(meta_list)
            
            col1, col2, col3 = st.columns(3)
            col1.metric("총 적재된 지식 조각", f"{len(df)} 건")
            col2.metric("분석된 로그 파일 수", f"{df['source_file'].nunique()} 개" if 'source_file' in df.columns else "0 개")
            col3.metric("해결된(박제된) 사례 수", f"{df['known_solution'].notna().sum()} 건" if 'known_solution' in df.columns else "0 건")
            
            st.divider()
            
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