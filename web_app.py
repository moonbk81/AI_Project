# web_app.py
import os
import streamlit as st
import json
import time

# 1. 백엔드 엔진 및 자동화 모듈 불러오기
from ril_rag_chat import RilRagChat
from telephony_log_summarizer import TelephonyLogSummarizer
from prepare_rag_payload import RagPayloadBuilder

# 1. 페이지 기본 설정
st.set_page_config(page_title="RIL 로그 분석기", page_icon="📡", layout="wide")

# 2. 백엔드 엔진 로드 (캐싱하여 매번 새로고침 될 때마다 모델을 다시 부르는 것 방지)
@st.cache_resource(show_spinner="AI 엔진과 Vector DB를 부팅 중입니다...")
def load_engine():
    return RilRagChat()

try:
    engine = load_engine()
except Exception as e:
    st.error(f"엔진 초기화 실패. 터미널에서 ollama가 실행 중인지 확인하세요.\n에러: {e}")
    st.stop()

# 3. 화면 타이틀 및 사이드바 (지식 박제용)
st.title("📡 안드로이드 RIL RAG 분석기")
st.markdown("단말 통신 로그를 원클릭으로 적재하고 AI와 분석을 시작하세요.")

# 채팅 기록 저장을 위한 세션 상태 초기화
if "messages" not in st.session_state: st.session_state.messages = []
if "last_ids" not in st.session_state: st.session_state.last_ids = []
if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0
if "feedback_key" not in st.session_state: st.session_state.feedback_key = 0

# 3. 사이드바: 1-Click 파이프라인 및 지식 박제
with st.sidebar:
    st.header("⚙️ 1-Click 자동 분석 파이프라인")
    st.write("원시 로그(Raw Log) 파일을 아래에 드래그 앤 드롭하면 파싱부터 DB 적재까지 한 번에 진행됩니다.")
    
    # [수정된 부분] 텍스트 입력 대신 파일 업로더 위젯 사용
    uploaded_file = st.file_uploader(
        "📁 원시 로그 파일 업로드", 
        type=['txt', 'log'], 
        key=f"uploader_{st.session_state.uploader_key}" # Key가 바뀌면 위젯이 리셋됨
    )
    
    if st.button("🚀 분석 및 DB 적재 시작", use_container_width=True, type="primary"):
        if uploaded_file is None:
            st.error("❌ 먼저 파일을 업로드해주세요.")
        else:
            with st.status("자동화 파이프라인 가동 중...", expanded=True) as status:
                try:
                    # 0. 업로드된 파일을 읽기 위해 임시 폴더에 저장
                    os.makedirs("./temp_logs", exist_ok=True)
                    temp_raw_path = os.path.join("./temp_logs", uploaded_file.name)

                    # 브라우저 메모리에 있는 파일을 물리 디스크에 기록
                    with open(temp_raw_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())

                    filename = uploaded_file.name
                    base_name = os.path.splitext(filename)[0]

                    os.makedirs("./result", exist_ok=True)
                    temp_json_path = f"./result/{base_name}_report.json"
                    payload_filename = f"{base_name}_payload.json"

                    # [Step 1] 파서 실행 (임시 저장된 파일 경로 전달)
                    st.write("1️⃣ 원시 로그 분석 및 필터링 중... (Parser)")
                    parser = TelephonyLogSummarizer(temp_raw_path)
                    parser.run_batch('all', temp_json_path)

                    # [Step 2] 페이로드 변환
                    st.write("2️⃣ RAG 맞춤형 지식 조각으로 변환 중... (Payload Builder)")
                    builder = RagPayloadBuilder(temp_json_path)
                    builder.build_payload(payload_filename) 

                    # [Step 3] Vector DB 적재
                    st.write("3️⃣ Vector DB 임베딩 및 적재 중... (BGE-M3)")
                    engine.ingest_folder("./payloads")

                    status.update(label="✅ 파이프라인 완료! 채팅창에 질문을 입력하세요.", state="complete", expanded=False)
                    # [여기에 추가!] 이번 턴의 주인공 파일을 메모리에 기억시킵니다.
                    st.session_state.current_file = filename
                    # ==========================================
                    # [수정 3] 업로더 초기화 및 화면 새로고침
                    # ==========================================
                    st.toast(f"'{filename}' 분석 완료! 채팅창에 질문해주세요.", icon="✅")
                    st.session_state.uploader_key += 1 # Key를 +1 증가시켜 다음 렌더링 때 비워지게 만듦
                    time.sleep(1) # 알림을 볼 수 있게 1초 대기
                    st.rerun() # 화면 강제 새로고침

                except Exception as e:
                    status.update(label="❌ 파이프라인 실패", state="error")
                    st.error(f"오류가 발생했습니다: {e}")

    st.divider() # 사이드바 구분선

    st.header("📝 사내 지식 베이스 (트랙 B)")
    if st.session_state.last_ids:
        # ==========================================
        # [수정 4] 텍스트 에어리어에 고유 Key 부여
        # ==========================================
        feedback = st.text_area(
            "해결책 / 원인 코멘트 입력:", 
            height=150, 
            key=f"feedback_{st.session_state.feedback_key}" # Key 부여
        )
        
        if st.button("💾 DB에 영구 박제", use_container_width=True):
            if feedback.strip():
                engine.save_knowledge(st.session_state.last_ids, feedback)
                
                # ==========================================
                # [수정 5] 코멘트 저장 후 입력창 초기화
                # ==========================================
                st.toast("✅ 성공적으로 박제되었습니다!", icon="💾") # 우측 하단에 세련된 팝업 알림
                st.session_state.last_ids = []
                st.session_state.feedback_key += 1 # Key 증가로 텍스트 지우기
                time.sleep(0.5) 
                st.rerun() 
            else:
                st.warning("코멘트를 먼저 입력해주세요.")
    else:
        st.info("먼저 채팅창에서 로그 분석을 진행해주세요.")

# 4. 기존 채팅 기록 화면에 뿌리기
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "references" in msg and msg["references"]:
            with st.expander("🔎 참고 원본 로그 및 과거 사례 보기"):
                st.markdown(msg["references"])

# 5. 하단 채팅 입력창 및 AI 추론
if prompt := st.chat_input("에러 증상이나 궁금한 점을 입력하세요 (예: Call fail 원인 찾아줘)"):
    # 사용자 질문 출력
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # AI 응답 및 검색 처리
    with st.chat_message("assistant"):
        with st.spinner("로그를 분석하고 과거 사례를 탐색 중입니다... 🕵️‍♂️"):
            # [수정!] 기억해둔 파일 이름을 함께 넘겨줍니다.
            current_target = st.session_state.get("current_file", None)
            answer, ids, metas = engine.ask(prompt, current_file=current_target)
            st.markdown(answer)
            
            # 참고 자료 (Context) 마크다운 포맷팅
            ref_text = ""
            for i, meta in enumerate(metas):
                known_solution = meta.get('known_solution')
                solution_badge = " **[💡과거 해결사례 존재]**" if known_solution else ""
                ref_text += f"### 자료 {i+1} (시간: {meta.get('time', 'N/A')}, 슬롯: {meta.get('slot', 'N/A')}){solution_badge}\n"
                
                if known_solution:
                    ref_text += f"> **과거 분석 기록:** {known_solution}\n\n"

                raw_data = meta.get('raw_logs', meta.get('raw_context', meta.get('raw_stack', '[]')))
                try:
                    raw_logs = json.loads(raw_data) if isinstance(raw_data, str) else []
                except:
                    raw_logs = []

                if raw_logs:
                    ref_text += "```text\n"
                    for log in raw_logs[:5]: 
                        ref_text += f"{log}\n"
                    if len(raw_logs) > 5: ref_text += "... (중략) ...\n"
                    ref_text += "```\n"
                
                raw_req = meta.get('raw_request')
                raw_resp = meta.get('raw_response')
                if raw_req or raw_resp:
                    ref_text += "```text\n"
                    if raw_req: ref_text += f"[REQ]  {raw_req}\n"
                    if raw_resp: ref_text += f"[RESP] {raw_resp}\n"
                    ref_text += "```\n"
                
                ref_text += "---\n"
            
            if ref_text:
                with st.expander("🔎 참고 원본 로그 및 과거 사례 보기"):
                    st.markdown(ref_text)
            
            # 지식 저장을 위해 현재 검색된 ID들 기억
            st.session_state.last_ids = ids

    # 세션에 답변 저장 및 사이드바 갱신을 위한 재실행
    st.session_state.messages.append({
        "role": "assistant", 
        "content": answer,
        "references": ref_text
    })
    st.rerun()