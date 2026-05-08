import os
import streamlit as st
import json
import time
import pandas as pd
import plotly.express as px
import re
import hashlib
from core.config import QUICK_PROMPTS, SATELLITE_PROMPTS

import ui_components as ui

# 1. 백엔드 엔진 및 자동화 모듈 불러오기
from ril_rag_chat import RilRagChat
from log_orchestrator import LogOrchestrator
from prepare_rag_payload import RagPayloadBuilder

from agent_tools import get_device_health_kpi

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

# ==========================================
# [신규 추가] 다중 파일 시계열 병합 함수
# ==========================================
def merge_log_files(file_paths, output_path):
    """여러 로그 파일을 읽어 시간(Timestamp) 기준으로 완벽히 교차 정렬하여 병합합니다."""
    import re
    # 안드로이드 로그 표준 시간 포맷 (예: 04-13 14:19:07.123)
    time_pattern = re.compile(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3})')
    all_lines = []

    for fp in file_paths:
        with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                match = time_pattern.search(line)
                # 시간이 없는 헤더/덤프 라인은 '00-00 00:00:00.000' 처리하여 최상단으로 밀어냄
                sort_key = match.group(1) if match else "00-00 00:00:00.000"
                all_lines.append((sort_key, line))

    # 추출한 Timestamp를 기준으로 전체 로그 오름차순 정렬
    all_lines.sort(key=lambda x: x[0])

    with open(output_path, 'w', encoding='utf-8') as f:
        for _, line in all_lines:
            f.write(line)

def generate_unique_key(prefix, data_string):
    hash_obj = hashlib.md5(data_string.encode('utf-8')).hexdigest()[:8]
    return f"{prefix}_{hash_obj}"

# 1. 페이지 기본 설정
st.set_page_config(page_title="RIL RAG Dashboard", page_icon="📡", layout="wide")

@st.cache_resource(show_spinner=False)
def get_ai_engine():
    """[핵심] 엔진을 매번 새로 만들지 않고 딱 한 번만 로드하여 메모리에 상주 (속도 극대화)"""
    return RilRagChat()

try:
    engine = get_ai_engine()
except Exception as e:
    st.error(f"엔진 초기화 실패. 터미널에서 ollama가 실행 중인지 확인하세요.\n에러: {e}")
    st.stop()

def init_session_states():
    """앱 전체에서 사용하는 상태 변수들을 한 곳에서 안전하게 초기화"""
    defaults = {
        "messages": [], "last_ids": [], "last_metas": [],
        "uploader_key": 0, "feedback_key": 0, "current_file": None
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

def reset_analysis_context():
    """파일이 바뀌거나 DB 초기화 시, 이전 대화 및 박제 대기열을 깔끔하게 포맷"""
    st.session_state.messages = []
    st.session_state.last_ids = []
    st.session_state.last_metas = []

def render_chat_interface(key_suffix="main", show_input=True):
    """메인 탭과 사이드바에서 공용으로 사용할 지능형 채팅 인터페이스"""

    # 1. 기존 메시지 렌더링 (차트 및 참고 로그 포함)
    for msg_idx, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

            # [차트 렌더링 로직] - 기존 tab_chat에 있던 코드와 동일
            if "metas" in msg and msg["metas"]:
                sig_history = []
                # OOS 데이터를 모아둘 리스트 준비
                reg_history = []
                reg_map = {
                    "IN_SERVICE": 0,
                    "OUT_OF_SERVICE": 1,
                    "EMERGENCY_ONLY": 2,
                    "POWER_OFF": 3
                }
                for i, meta in enumerate(msg["metas"]):
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
                            fig = px.pie(df_signal, names='Level', values='Value',
                                         title=f"📊 [자료 {i+1}] 신호 세기 분포", hole=0.4)
                            unique_key = generate_unique_key(f"chart_{key_suffix}_{msg_idx}_{i}", str(fig.to_json()[:100]))
                            st.plotly_chart(fig, use_container_width=True, key=unique_key)

                    if meta.get('log_type') == 'OOS_Event':
                        v_reg = meta.get('voice_reg', 'UNKNOWN').upper()
                        d_reg = meta.get('data_reg', 'UNKNOWN').upper()
                        slot = f"Slot{meta.get('slotId', '0')}"
                        time = meta.get('time')

                        if time:
                            reg_history.append({
                                "time": time,
                                "Status": reg_map.get(v_reg, -1),
                                "Type": "Voice", "Slot": slot,
                                "Label": v_reg
                            })

                            reg_history.append({
                                "time": time,
                                "Status": reg_map.get(d_reg, -1),
                                "Type": "Data", "Slot": slot,
                                "Label": d_reg
                            })
                    if meta.get('log_type') == 'Signal_Level':
                        # meta에 값이 제대로 있는지 방어 로직 추가
                        lvl = meta.get('level')
                        rt = meta.get('rat', 'Unknown')
                        sl = meta.get('slot', '0')
                        tm = meta.get('time')

                        if tm and lvl is not None:
                            sig_history.append({
                                "time": tm,
                                "Slot": f"Slot {sl}",
                                "RAT": str(rt),
                                "Level": int(lvl),
                                "Info": meta.get('raw_info', '')
                            })

            # [참고 로그 렌더링]
            if "references" in msg and msg["references"]:
                with st.expander(f"🔎 참고 로그 ({key_suffix})"):
                    st.markdown(msg["references"])

    # 2. 채팅 입력창 (Key 충돌 방지를 위해 suffix 사용)
    if show_input:
        if prompt := st.chat_input("질문하세요", key=f"chat_input_{key_suffix}"):
            st.session_state.messages.append({"role": "user", "content": prompt})

            with st.chat_message("assistant"):
                with st.spinner("분석 중..."):
                    current_target = st.session_state.get("current_file", None)
                    # 이전 대화 맥락 5개 유지하여 질문
                    answer, ids, metas = engine.ask(prompt, current_file=current_target, chat_history=st.session_state.messages[-5:])

                    # (여기에 ref_text 조립 로직 추가 - 기존 코드와 동일)
                    ref_text = ""
                    for i, meta in enumerate(metas):
                        known_solution = meta.get('known_solution')
                        solution_badge = " **[💡과거 해결사례 존재]**" if known_solution else ""
                        ref_text += f"### 자료 {i+1} (시간: {meta.get('time', 'N/A')}, 슬롯: {meta.get('slot', 'N/A')}){solution_badge}\n"

                        if known_solution:
                            ref_text += f"> **과거 분석 기록:** {known_solution}\n\n"

                        raw_data = meta.get('raw_logs', meta.get('raw_context', meta.get('raw_stack', '[]')))
                        # 🚀 복잡한 파싱 로직을 함수 한 줄로 처리!
                        raw_logs = ui.parse_raw_logs(raw_data)

                        # 화면 렌더링
                        if raw_logs:
                            ref_text += "```text\n"
                            for log in raw_logs[:10]:
                                ref_text += f"{log}\n"
                            if len(raw_logs) > 10:
                                ref_text += f"... (중략, 총 {len(raw_logs)} 라인) ...\n"
                            ref_text += "```\n"

                        raw_req = meta.get('raw_request')
                        raw_resp = meta.get('raw_response')
                        if raw_req or raw_resp:
                            ref_text += "```text\n"
                            if raw_req: ref_text += f"[REQ]  {raw_req}\n"
                            if raw_resp: ref_text += f"[RESP] {raw_resp}\n"
                            ref_text += "```\n"
                        ref_text += "---\n"

                    st.markdown(answer)
                    st.session_state.messages.append({
                        "role": "assistant", "content": answer,
                        "references": ref_text, "metas": metas
                    })
                    st.session_state.last_ids = ids
                    st.session_state.last_metas = metas
                    st.rerun()

# ==========================================
# ⚙️ [리팩토링] 파이프라인 비즈니스 로직 추상화
# ==========================================
def run_analysis_pipeline(uploaded_files, use_slice, start_t, end_t, ai_engine):
    """UI와 분리된 순수 백엔드 데이터 처리 파이프라인 (다중 파일 지원)"""
    start_total = time.time()
    progress_bar = st.progress(0)

    with st.status("🚀 통합 분석 파이프라인 가동 중...", expanded=True) as status:
        try:
            # 1. 파일 준비 및 슬라이싱
            os.makedirs("./temp_logs", exist_ok=True)
            saved_paths = []

            # 1. 업로드된 모든 파일을 디스크에 임시 저장
            for file in uploaded_files:
                path = os.path.join("./temp_logs", file.name)
                with open(path, "wb") as f:
                    f.write(file.getbuffer())
                saved_paths.append(path)

            # 🚨 2. 다중 파일 병합 로직 (핵심)
            if len(saved_paths) > 1:
                st.write(f"🔄 {len(saved_paths)}개의 파편화된 로그를 시간순으로 병합 중...")
                # 첫 번째 파일 이름을 기반으로 _merged라는 꼬리표를 붙임
                base_name = os.path.splitext(uploaded_files[0].name)[0] + "_merged"
                target_log_path = os.path.join("./temp_logs", f"{base_name}.txt")
                merge_log_files(saved_paths, target_log_path)
            else:
                # 단일 파일일 경우 그대로 사용
                target_log_path = saved_paths[0]
                base_name = os.path.splitext(uploaded_files[0].name)[0]

            # 3. 타임라인 슬라이싱
            if use_slice:
                st.write(f"✂️ 타임라인 슬라이싱 적용 중...")
                sliced_path = os.path.join("./temp_logs", f"sliced_{base_name}.txt")
                slice_log_by_time(target_log_path, sliced_path, start_t, end_t)
                target_log_path = sliced_path

            # 4. 통합 오케스트레이터 호출
            st.write("1️⃣ 모든 통신 스택 로그 교차 분석 중...")
            orchestrator = LogOrchestrator(target_log_path)

            report_path = f"./result/{base_name}_report.json"
            success = orchestrator.run_batch(report_path)
            progress_bar.progress(50)

            if success is False:
                raise RuntimeError("LogOrchestrator 분석 실패")

            if not os.path.exists(report_path):
                raise FileNotFoundError(f"report 파일이 생성되지 않았씁니다: {report_path}")

            if os.path.getsize(report_path) == 0:
                raise RuntimeError(f"report 파일이 비어 있습니다: {report_path}")

            # 5. RAG 페이로드 생성 및 적재
            st.write("2️⃣ RAG 지식 조각 생성 및 DB 임베딩 중...")
            builder = RagPayloadBuilder(report_path)
            payload_name = f"{base_name}_payload.json"
            builder.build_payload(payload_name)

            payload_path = os.path.join("./payloads", payload_name)
            ai_engine.ingest_file(payload_path, force=True)
            progress_bar.progress(100)

            status.update(label="✅ 분석 완료! 이제 대화와 대시보드를 확인하세요.", state="complete", expanded=False)
            st.session_state.current_file = f"{base_name}_payload.json"
            st.rerun()

        except Exception as e:
            status.update(label="❌ 파이프라인 실패", state="error")
            st.error(f"오류가 발생했습니다: {e}")

init_session_states() # 상태 초기화 실행

# ================================================
# st.session_state 초기화 구역 (보통 파일 최상단에 위치)
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

st.title("📡 안드로이드 RIL RAG 분석기")
st.markdown("단말 통신 로그를 원클릭으로 적재하고 AI와 분석을 시작하세요.")

# 세션 상태 초기화
if "messages" not in st.session_state: st.session_state.messages = []
if "last_ids" not in st.session_state: st.session_state.last_ids = []
if "last_metas" not in st.session_state: st.session_state.last_metas = []
if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0
if "feedback_key" not in st.session_state: st.session_state.feedback_key = 0
if "current_file" not in st.session_state: st.session_state.current_file = None

tab_chat, tab_dash, tab_boot, tab_ntn, tab_internet = st.tabs([
    "💬 로그 분석 및 대화",
    "📊 전사 로그 통계 대시보드",
    "📈 부팅/Crash/ANR/NITZ",
    "🛰️ 위성 통신",
    "🌐 인터넷 멈춤"])

# ==========================================
# 3. 사이드바: 파일 업로드 & 슬라이싱 옵션
# ==========================================
with st.sidebar:
    st.header("⚙️ 1-Click 자동 분석 파이프라인")
    # ✅ 여기서 엔진의 LLM 모델명과 임베딩 디바이스를 깔끔하게 출력
    st.info(f"🧠 활성 LLM: **{engine.llm_model_name}**\n\n⚡ 임베딩: **{str(engine.embed_model.device).upper()}**")
    uploaded_files = st.file_uploader(
        "📁 원시 로그 파일 업로드 (여러 개 동시 선택 가능)",
        type=['txt', 'log', '01', '02', '03', '04', '05', '06', '07', '08', '09', '10'],
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.uploader_key}"
    )

    st.divider()
    st.subheader("🔍 분석 세션 및 DB 관리")

    # 1. 기존 DB에 있는 파일 목록 불러오기
    existing_files = engine.get_all_files()

    if existing_files:
        # 셀렉트 박스로 파일 선택 (현재 세션 파일이 있으면 기본값으로 설정)
        default_idx = 0
        if st.session_state.current_file in existing_files:
            default_idx = existing_files.index(st.session_state.current_file) + 1

        selected_file = st.selectbox(
            "📁 기존 적재 파일 선택",
            options=["선택 안 함"] + existing_files,
            index=default_idx
        )

        if selected_file != "선택 안 함":
            if st.session_state.current_file != selected_file:
                st.session_state.current_file = selected_file
                st.toast(f"분석 대상이 '{selected_file}'로 변경되었습니다.")
                st.rerun()
    else:
        st.info("DB가 비어 있습니다. 로그를 먼저 업로드하세요.")
        st.session_state.current_file = None # 파일 리스트가 없으면 활성 파일도 초기화

    if st.session_state.current_file:
        st.success(f"활성 파일: `{st.session_state.current_file}`")

    # 2. DB 초기화 버튼 (매번 폴더 지울 필요 없음)
    if st.button("🗑️ 전체 DB 초기화", use_container_width=True, help="Vector DB의 모든 지식을 삭제합니다."):
        if engine.reset_db():
            # 🚨 [신규 추가] 물리적으로 남아있는 캐시 폴더들도 싹 날리고 새로 빈 폴더를 만들어 줍니다.
            import shutil
            for folder in ["./payloads", "./result", "./temp_logs"]:
                if os.path.exists(folder):
                    shutil.rmtree(folder) # 폴더 통째로 삭제
                os.makedirs(folder, exist_ok=True) # 깨끗한 빈 폴더로 재가동 준비

            st.session_state.current_file = None
            reset_analysis_context()
            st.success("DB와 물리적 파일이 모두 깔끔하게 비워졌습니다.")
            time.sleep(1)
            st.rerun()

    # [추가] 슬라이싱 UI
    use_slicing = st.checkbox("✂️ 특정 시간대만 잘라서 분석 (2GB 이상 권장)")
    start_time, end_time = "", ""
    if use_slicing:
        st.info("💡 에러 발생 시점 주변 5~10분만 잘라내면 분석 속도가 100배 빨라집니다.")
        col1, col2 = st.columns(2)
        with col1: start_time = st.text_input("시작 (예: 04-12 14:00:00)")
        with col2: end_time = st.text_input("종료 (예: 04-12 14:15:00)")

    if st.button("🚀 분석 및 DB 적재 시작", use_container_width=True, type="primary"):
        if not uploaded_files:  # 단일 객체가 아닌 빈 리스트인지 검사
            st.error("❌ 먼저 파일을 하나 이상 업로드해주세요.")
        elif use_slicing and (not start_time or not end_time):
            st.error("❌ 슬라이싱을 켰다면 시작/종료 시간을 모두 입력해주세요.")
        else:
            run_analysis_pipeline(uploaded_files, use_slicing, start_time, end_time, engine)

    st.divider()

    # [A] 스마트 카테고리 매퍼 함수 정의
    def get_recommended_category(text, categories):
        mapping = {
            "Call_Session": ["call", "드랍", "drop", "통화", "fail", "ims", "volte"],
            "Battery_Drain_Report": ["배터리", "battery", "drain", "광탈", "열", "thermal", "소모"],
            "OOS_Event": ["oos", "이탈", "서비스", "service", "reg", "등록"],
            "Signal_Level": ["신호", "signal", "안테나", "level", "수신"],
            "Network_DNS_Issue": ["dns", "차단", "block", "인터넷", "지연", "latency"]
        }
        text_lower = text.lower()
        for cat, keywords in mapping.items():
            if any(kw in text_lower for kw in keywords):
                return cat
        return "Total_Report" # 매칭 없거나 모호하면 전체 리포트로 유도

    # --- 사이드바 렌더링 파트 ---
    st.header("📝 사내 지식 베이스 (Track B)")

    if st.session_state.get("last_ids") and st.session_state.get("last_metas"):

        # [B] 카테고리 목록 구성 (Total_Report 추가)
        retrieved_types = list(set(m.get('log_type', 'Unknown') for m in st.session_state.last_metas if m))
        category_options = ["Total_Report"] + retrieved_types

        # 코멘트 입력창
        feedback = st.text_area("해결책 / 원인 코멘트 입력:", height=100, key=f"fb_{st.session_state.feedback_key}")

        # [A] 코멘트 입력에 따른 자동 카테고리 추천 로직
        recommended = get_recommended_category(feedback, category_options)
        default_idx = category_options.index(recommended) if recommended in category_options else 0

        target_type = st.selectbox("📌 박제 카테고리 (자동 추천됨)", category_options, index=default_idx)

        # [C] 심각도 선택 추가
        severity = st.radio("🚩 이슈 중요도", ["Critical", "Major", "Minor", "Info"], index=3, horizontal=True)

        if st.button("💾 DB에 지식 영구 박제", use_container_width=True):
            if feedback.strip():
                # 카테고리에 따른 ID 필터링 (Total_Report일 경우 전체 ID 선택)
                if target_type == "Total_Report":
                    target_ids = st.session_state.last_ids
                else:
                    target_ids = [doc_id for doc_id, meta in zip(st.session_state.last_ids, st.session_state.last_metas)
                                if meta and meta.get('log_type') == target_type]

                if target_ids:
                    engine.save_knowledge(target_ids, feedback, severity=severity)
                    st.toast(f"✅ {target_type}에 {severity} 등급으로 박제 완료!")
                    st.session_state.feedback_key += 1
                    st.rerun()

        st.divider()
        st.subheader("사이드바 코파일럿")
        render_chat_interface(key_suffix="sidebar")


# ==========================================
# [Tab 1] 대화 및 분석 창
# ==========================================
with tab_chat:
    st.info("💡 **AI 분석가에게 이렇게 물어보세요!** (카테고리를 명시하면 더 정확해집니다)")

    # 클릭하면 열리는 가이드북
    with st.expander("🔍 효율적인 분석을 위한 질문 예시 보기"):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("""
            **📞 통화 및 신호 분석**
            * "방금 발생한 **Call Fail** 원인이 뭐야?"
            * "통화 중 **IMS 에러** 기록 찾아줘"
            * "현재 **망 이탈(OOS)** 발생 구간이 있어?"
            """)
        with col2:
            st.markdown("""
            **🔋 성능 및 네트워크 분석**
            * "최근 1시간 동안 **배터리 광탈** 원인 분석해줘"
            * "특정 **앱(DNS) 차단**된 이력이 있어?"
            * "네트워크 **지연(Latency)** 통계 보여줘"
            """)
        st.caption("⚠️ '그거 찾아줘' 대신 '통화 에러 찾아줘'처럼 명칭을 포함하면 좋습니다.")

    # ==========================================
    # 🚀 [추가] 원클릭 자동 분석 버튼
    # ==========================================
    st.caption("💡 직접 질문하거나, 아래의 원클릭 분석 버튼을 사용해 완벽한 프롬프트를 전송하세요.")
    quick_prompt = None

    # 버튼을 3x3 그리드로 배치
    col_btn1, col_btn2, col_btn3 = st.columns(3)
    col_btn4, col_btn5, col_btn6 = st.columns(3)
    col_btn7, col_btn8, col_btn9 = st.columns(3)
    with col_btn1:
        if st.button("📞 통화 끊김(Drop) 분석", use_container_width=True):
            quick_prompt = QUICK_PROMPTS.get('call_drop')
    with col_btn2:
        if st.button("🌐 데이터 네트워크 이상 분석", use_container_width=True):
            quick_prompt = QUICK_PROMPTS.get('data_network_issue')
    with col_btn3:
        if st.button("🔋 배터리/크래시 분석", use_container_width=True):
            quick_prompt = QUICK_PROMPTS.get('battery_crash')
    with col_btn4:
        if st.button("🚫 망 등록(Reg) 및 OOS 분석", use_container_width=True):
            quick_prompt = QUICK_PROMPTS.get('network_oos')
    with col_btn5:
        if st.button("📶 안테나(Signal) 레벨 분석", use_container_width=True):
            quick_prompt = QUICK_PROMPTS.get('antenna_level_analysis')
    with col_btn6:
        if st.button("💬 VoLTE/SIP 상세 분석", use_container_width=True):
            quick_prompt = QUICK_PROMPTS.get('volte_sip_analysis')

    with col_btn7:
        if st.button("🌐 인터넷 멈춤 종합 분석", use_container_width=True):
            quick_prompt = QUICK_PROMPTS.get('internet_stall_analysis')

    st.divider()

    # ==========================================
    # 💬 대화 히스토리 및 차트/참고로그 렌더링
    # ==========================================
    render_chat_interface(key_suffix="main", show_input=False)

    # ==========================================
    # 💬 질문 입력 및 AI 분석 구역
    # ==========================================
    user_input = st.chat_input("에러 증상이나 궁금한 점을 입력하세요")

    # 버튼을 눌렀거나(quick_prompt), 직접 입력했거나(user_input) 둘 중 하나를 실행
    prompt = quick_prompt if quick_prompt else user_input

    if prompt:
        # 1. UI에 질문 표시 및 히스토리 저장
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # 2. AI 분석 진행
        with st.chat_message("assistant"):
            with st.spinner("로그를 분석하고 과거 사례를 탐색 중입니다... 🕵️‍♂️"):
                current_target = st.session_state.get("current_file", None)
                # 🚨 [수정] 현재 파일의 KPI 정보를 추출하여 에이전트에게 전달
                current_base = current_target.replace("_payload.json", "") if current_target else "Unknown"
                health_kpi_json = get_device_health_kpi(current_base) if current_base != "Unknown" else None
                answer, ids, metas = engine.ask(prompt,
                                                current_file=current_target,
                                                chat_history=st.session_state.messages[-5:],
                                                health_kpi=health_kpi_json)

                # [복구 완료] 원본 로그 텍스트 조립 구역
                ref_text = ""
                for i, meta in enumerate(metas):
                    known_solution = meta.get('known_solution')
                    solution_badge = " **[💡과거 해결사례 존재]**" if known_solution else ""
                    ref_text += f"### 자료 {i+1} (시간: {meta.get('time', 'N/A')}, 슬롯: {meta.get('slot', 'N/A')}){solution_badge}\n"

                    if known_solution:
                        ref_text += f"> **과거 분석 기록:** {known_solution}\n\n"

                    raw_data = meta.get('raw_logs', meta.get('raw_context', meta.get('raw_stack', '[]')))
                    # 🚀 복잡한 파싱 로직을 함수 한 줄로 처리!
                    raw_logs = ui.parse_raw_logs(raw_data)

                    # 화면 렌더링
                    if raw_logs:
                        ref_text += "```text\n"
                        for log in raw_logs[:10]:
                            ref_text += f"{log}\n"
                        if len(raw_logs) > 10:
                            ref_text += f"... (중략, 총 {len(raw_logs)} 라인) ...\n"
                        ref_text += "```\n"

                    raw_req = meta.get('raw_request')
                    raw_resp = meta.get('raw_response')
                    if raw_req or raw_resp:
                        ref_text += "```text\n"
                        if raw_req: ref_text += f"[REQ]  {raw_req}\n"
                        if raw_resp: ref_text += f"[RESP] {raw_resp}\n"
                        ref_text += "```\n"
                    ref_text += "---\n"

                st.markdown(answer)
                st.session_state.last_ids = ids
                st.session_state.last_metas = metas

        # 🚨 차트/참고 로그 유지를 위해 metas/references 함께 저장
        st.session_state.messages.append({
            "role": "assistant",
            "content": answer,
            "references": ref_text,
            "metas": metas
        })
        st.rerun()

# ==========================================
# [Tab 2] 대시보드 창
# ==========================================
with tab_dash:
    st.header("📈 전사 로그 데이터 시각화")
    st.markdown("Vector DB에 축적된 로그 데이터의 통계와 박제된 지식을 한눈에 확인합니다.")

    all_data = engine.collection.get(include=["metadatas"])
    if not all_data or not all_data.get("metadatas") or len(all_data["metadatas"]) == 0:
        st.info("DB가 비어있습니다. 첫 번째 로그 파일을 업로드해주세요!")
        # 🚨 중요: 여기서 실행을 멈춰야 아래에 있는 차트/데이터프레임 코드가 실행되지 않습니다.
    else:
        if all_data and all_data.get("metadatas"):
            meta_list = [m for m in all_data["metadatas"] if m is not None]
            df_all = pd.DataFrame(meta_list)

            # 🚨 [추가] 분석 범위 선택 스위치
            st.divider()
            view_mode = st.radio(
                "📊 분석 범위 선택",
                ["현재 활성 파일만", "전체 DB 히스토리 모음"],
                horizontal=True
            )

            # 데이터 필터링 로직
            if view_mode == "현재 활성 파일만" and st.session_state.current_file:
                df = df_all[df_all['source_file'] == st.session_state.current_file]
                st.info(f"📍 현재 분석 중인 파일: `{st.session_state.current_file}`")
            else:
                df = df_all
                st.info(f"🌐 전체 DB 데이터 분석 중 (총 {df_all['source_file'].nunique()}개 파일)")

            if meta_list:
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

                if view_mode == "현재 활성 파일만":
                    # web_app.py 내 '현재 활성 파일만' 섹션에 추가 제안
                    import plotly.graph_objects as go

                    def draw_combined_timeline(df):
                        fig = go.Figure()

                        # 1. 신호 세기 라인 차트 (Secondary Y-axis)
                        sig_df = df[df['log_type'] == 'Signal_Level']
                        fig.add_trace(go.Scatter(x=sig_df['time'], y=sig_df['level'], name="Signal Level", mode='lines+markers'))

                        # 2. 통화 드랍 이벤트 (Scatter - 큰 빨간 점)
                        call_df = df[df['log_type'] == 'Call_Session'].copy()
                        if not call_df.empty and 'status' in call_df.columns:
                            fail_calls = call_df[call_df['status'].str.contains('FAIL|DROP', na=False, case=False)]
                            if not fail_calls.empty:
                                hover_text = fail_calls['fail_reason'] if 'fail_reason' in fail_calls.columns else "Unknown Reason"
                                fig.add_trace(
                                    go.Scatter(x=fail_calls['time'], y=[3.5]*len(fail_calls),
                                            mode='markers', marker=dict(size=12, color='red', symbol='x'),
                                            name="Call Drop", text=hover_text, hoverinfo='text+x'))

                        # 3. 데이터 사용량 피크 (Bar)
                        data_df = df[df['log_type'] == 'Data_Usage']
                        fig.add_trace(go.Scatter(x=data_df['time'], y=data_df['total_mb'], name="Data Usage(MB)", fill='tozeroy'))

                        fig.update_layout(title="통합 로그 타임라인 분석", xaxis_title="시간", yaxis_title="상태/값")
                        st.plotly_chart(fig, use_container_width=True)

                    # ==========================================
                    # 📊 1. 핵심 지표 카드 변수 계산 (df에서 데이터 추출)
                    # ==========================================

                    # (1) 최고 데이터 앱 및 용량
                    du_df = df[df['log_type'] == 'Data_Usage'].copy()
                    if not du_df.empty:
                        du_df['total_mb'] = pd.to_numeric(du_df['total_mb'], errors='coerce')
                        top_1 = du_df.sort_values(by='total_mb', ascending=False).iloc[0]
                        top_app_name = top_1.get('app_name', 'Unknown')
                        top_app_mb = f"{top_1['total_mb']:,.2f}"
                    else:
                        top_app_name, top_app_mb = "기록 없음", "0"

                    # (2) 통화 성공률 및 드랍 건수
                    call_df = df[df['log_type'] == 'Call_Session'].copy()
                    if not call_df.empty:
                        total_calls = len(call_df)
                        if 'status' in call_df.columns:
                            drop_count = len(call_df[call_df['status'].str.contains('FAIL|DROP', na=False, case=False)])
                        else: drop_count = 0
                        success_rate = round(((total_calls - drop_count) / total_calls) * 100, 1) if total_calls > 0 else 100
                    else:
                        success_rate, drop_count = 100, 0 # 통화 기록이 없으면 기본값 100%

                    # (3) OOS(망 이탈) 발생 횟수
                    oos_df = df[df['log_type'] == 'OOS_Event'].copy()
                    if not oos_df.empty:
                        is_v_oos = oos_df['voice_reg'].astype(str).str.contains('OUT_OF_SERVICE|OOS', na=False, case=False) if 'voice_reg' in oos_df.columns else False
                        is_d_oos = oos_df['data_reg'].astype(str).str.contains('OUT_OF_SERVICE|OOS', na=False, case=False) if 'data_reg' in oos_df.columns else False

                        oos_count = len(oos_df[is_v_oos | is_d_oos])
                    else:
                        oos_count = 0

                    # (4) 평균 신호 세기 (기존 df 활용)
                    sig_df = df[df['log_type'] == 'Signal_Level'].copy()
                    avg_signal = sig_df['level'].mean() if not sig_df.empty else 0

                    # ==========================================
                    # 🖼️ 2. 핵심 지표 카드 UI 렌더링
                    # ==========================================
                    st.subheader("📊 단말 핵심 상태 지표")
                    col1, col2, col3, col4 = st.columns(4)

                    col1.metric("🔥 데이터 사용 1위", f"{top_app_name}", f"{top_app_mb} MB")
                    col2.metric("📡 평균 신호 세기", f"Level {avg_signal:.1f}")

                    # 통화 드랍이 있으면 빨간색(inverse), 없으면 정상(normal)
                    col3.metric("📞 통화 성공률", f"{success_rate}%", delta=f"-{drop_count} 건 실패", delta_color="inverse" if drop_count > 0 else "normal")

                    # OOS가 1번이라도 있으면 경고
                    col4.metric("🚨 OOS 발생 횟수", f"{oos_count} 회", delta="망 이탈 발생!" if oos_count > 0 else "안정적", delta_color="inverse" if oos_count > 0 else "normal")

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

                    st.divider()

                    # ==========================================
                    # 📈 [신규] 통합 로그 타임라인 (Call + Signal + SIP 오버레이)
                    # ==========================================
                    if st.session_state.current_file:
                        current_base_name = st.session_state.current_file.replace("_payload.json", "")
                        target_report_path = os.path.join("./result", f"{current_base_name}_report.json")

                        if os.path.exists(target_report_path):
                            try:
                                with open(target_report_path, 'r', encoding='utf-8') as _f:
                                    _loaded_report_data = json.load(_f)

                                # ui_components.py에 추가한 멋진 오버레이 차트 호출!
                                ui.render_integrated_rf_call_timeline(_loaded_report_data)
                            except Exception as e:
                                st.error(f"차트 렌더링 중 에러가 발생했습니다: {e}")
                        else:
                            st.info("📊 통합 타임라인을 그리기 위한 JSON 파일이 아직 생성되지 않았습니다.")
                    else:
                        st.info("파일을 먼저 선택해주세요.")

                    st.divider()

                    # ==========================================
                    # 🔍 2. 딥다이브 필터 (특정 앱 통신 분석)
                    # ==========================================
                    st.subheader("🔍 특정 앱 딥다이브 분석")

                    data_df = df[df['log_type'] == 'Data_Usage'].copy()
                    if not data_df.empty:
                        # 고유한 앱 이름 목록 추출 (NaN 제외)
                        app_list = data_df['app_name'].dropna().unique().tolist()

                        if app_list:
                            # 1위 앱을 기본값으로 세팅하기 위한 로직
                            top_app = data_df.groupby('app_name')['total_mb'].sum().idxmax()
                            default_idx = app_list.index(top_app) if top_app in app_list else 0

                            selected_app = st.selectbox("분석할 패키지(앱)를 선택하세요:", app_list, index=default_idx)

                            # 선택한 앱의 데이터만 필터링
                            target_app_df = data_df[data_df['app_name'] == selected_app]

                            # 사용 망(RAT)별 데이터 총합 계산
                            rat_summary = target_app_df.groupby('rat')['total_mb'].sum().reset_index()
                            rat_summary['total_mb'] = rat_summary['total_mb'].apply(lambda x: f"{x:,.2f} MB")

                            c1, c2 = st.columns([1, 2.5])
                            with c1:
                                st.markdown(f"**📡 [{selected_app}] 망별 요약**")
                                st.dataframe(rat_summary, hide_index=True, use_container_width=True)

                            with c2:
                                st.markdown(f"**📑 상세 사용 로그**")
                                display_cols = ['time', 'rat', 'total_mb', 'rx_bytes', 'tx_bytes']
                                actual_cols = [c for c in display_cols if c in target_app_df.columns]
                                st.dataframe(target_app_df[actual_cols], hide_index=True, use_container_width=True)
                        else:
                            st.info("데이터 사용량 기록이 없습니다.")
                    else:
                        st.info("데이터 사용량 로그를 찾을 수 없습니다.")

                    st.divider()
                    ui.render_battery_thermal_chart(df)

                    st.divider()
                    ui.render_network_timeseries_and_dns(df)

                    st.divider()
                    ui.render_dns_analysis_chart(df)

                    st.divider()
                    ui.render_call_history_summary(df)

                    st.divider()
                    ui.render_signal_level_timeline(df)

                    st.divider()
                    ui.render_service_state_timeline(df)

                    st.divider()
                    ui.render_data_usage_profiling(df)

                    # 🚨 [신규 추가] 대시보드에 SIP 사다리 차트 노출
                    st.divider()
                    # 1. 현재 선택된 파일의 기본 이름(base_name) 추출 (예: log_A_payload.json -> log_A)
                    current_base = st.session_state.current_file.replace("_payload.json", "") if st.session_state.current_file else ""
                    ui.render_ims_sip_flow(current_base)

                    st.divider()
                    # 3. 데이터 호 파일 스위칭 완벽 대응
                    current_dc_data = []
                    if current_base:
                        dc_json_path = f"./result/{current_base}_datacall.json"
                        if os.path.exists(dc_json_path):
                            with open(dc_json_path, 'r', encoding='utf-8') as f:
                                current_dc_data = json.load(f)

                    ui.render_data_call_analyzer(current_dc_data)

                    # ==========================================
                    # 🤖 AI 종합 기술 진단 리포트 (Powered by Gemma2 9B)
                    # ==========================================
                    st.subheader("🤖 AI 종합 기술 진단 리포트")
                    if st.button("📝 전체 로그 종합 분석 리포트 생성", use_container_width=True):
                        with st.spinner("모든 로그의 상관관계를 분석하여 전문 리포트를 작성 중입니다..."):

                            actual_file_name = df['source_file'].iloc[0] if not df.empty and 'source_file' in df.columns else "Unknown"
                            current_base = st.session_state.current_file.replace("_payload.json", "")
                            health_kpi_json = get_device_health_kpi(current_base)
                            # ---------------------------------------------------------
                            # 🧠 [강력한 프롬프트 조립]
                            # ---------------------------------------------------------
                            combined_query = f"""
                            [절대 팩트 데이터 강제 주입]
                            단말의 현재 상태를 나타내는 아래 JSON 지표들은 로그 파서를 통해 추출된 100% 정확한 팩트입니다.

                            {health_kpi_json}
                            위 팩트 데이터와 검색된 로그 문맥을 바탕으로 15년 차 무선 통신 수석 엔지니어의 관점에서 단말 상태를 종합 진단해.

                            [🚨 엄격한 답변 규칙 🚨]
                            1. JSON 데이터의 9가지 부문을 반드시 기반으로 원인(Root Cause)을 과감히 추론할 것.
                            2. '9_ril_sip_correlation' 항목에 연쇄 붕괴가 확인되었다면, 이를 리포트 최상단에 가장 강력한 원인으로 하이라이트할 것.
                            """

                            raw_result = engine.ask(combined_query, current_file=actual_file_name)

                            if isinstance(raw_result, (tuple, list)):
                                report_answer = raw_result[0]
                            else:
                                report_answer = raw_result

                            st.success("✅ 심층 진단 분석이 완료되었습니다.")
                            # 1. 자바스크립트 충돌 방지 (백틱 및 줄바꿈 안전 처리)
                            safe_report = report_answer.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$").replace("\n", "\\n")

                            # 2. 🚨 모든 HTML 태그를 무조건 맨 왼쪽 끝(들여쓰기 없음)에 붙여야 합니다!
                            st.markdown(f"""
<div style="position: relative; background-color: #f0f2f6; padding: 25px; border-radius: 10px; border-left: 5px solid #1f77b4; margin-bottom: 20px;">
<button onclick="copyReport()" style="position: absolute; top: 10px; right: 10px; padding: 6px 12px; background-color: #1f77b4; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 13px; font-weight: bold; transition: 0.3s;">복사하기 📋</button>
<div id="full-report-text" style="white-space: pre-wrap; font-size: 15px; color: #333; line-height: 1.6;">

{report_answer}

</div>
</div>

<script>
function copyReport() {{
    const reportText = `{safe_report}`;
    navigator.clipboard.writeText(reportText.replace(/\\n/g, '\\n')).then(() => {{
        alert('✅ 리포트가 클립보드에 복사되었습니다!\\n원하는 곳에 붙여넣기(Ctrl+V) 하세요.');
    }}).catch(err => {{
        console.error('복사 실패:', err);
    }});
}}
</script>
""", unsafe_allow_html=True)

                            # 🚨 [신규] 사이드바 '지식 박제' 활성화를 위한 상태 업데이트
                            all_db_data = engine.collection.get(where={"source_file": actual_file_name})
                            if all_db_data and all_db_data.get('ids'):
                                st.session_state.last_ids = all_db_data['ids']
                                st.session_state.last_metas = all_db_data['metadatas']
                                st.toast("💡 리포트 결과가 박제 대기열에 등록되었습니다. 왼쪽 사이드바에서 지식 베이스에 코멘트를 남겨보세요!", icon="📝")
            else:
                st.warning("데이터 형식이 올바르지 않습니다.")
        else:
            st.info("DB가 비어있습니다. 첫 번째 로그 파일을 업로드해주세요!")

# ==========================================
# 4. 🚨 맨 아래에 새로운 부팅 분석 탭 추가
# ==========================================
with tab_boot:
    st.subheader("🚀 Android 부팅 시퀀스 분석")

    current_target = st.session_state.get("current_file", None)

    if current_target:
        base_name = current_target.replace("_payload.json", "")
        report_path = f"./result/{base_name}_report.json"

        if os.path.exists(report_path):
            with open(report_path, 'r', encoding='utf-8') as f:
                report_data = json.load(f)

            # BootParser는 List를 반환하므로 동적 타입 대응
            boot_raw = report_data.get('boot_stats', [])

            # 하위 호환성 및 안정성 보장
            if isinstance(boot_raw, dict):
                events = boot_raw.get('events', [])
            else:
                events = boot_raw # 순수 List일 경우 그대로 사용

            if events:
                df_boot = pd.DataFrame(events)

                # 1. 런타임 동적 KPI 지표 계산 (List에서 자체 추출)
                st.markdown("#### 📊 핵심 부팅 마일스톤")
                c1, c2, c3 = st.columns(3)

                # Time_ms의 최대값을 전체 부팅 소요 시간으로 간주
                boot_complete = df_boot['Time_ms'].max() if 'Time_ms' in df_boot.columns else 0

                # 특정 키워드가 포함된 이벤트 시간 탐색 (없으면 0)
                voice_events = df_boot[df_boot['Event'].str.contains('Voice|RIL|Telephony', case=False, na=False)]
                voice_ready = voice_events['Time_ms'].max() if not voice_events.empty else "분석 불가"

                data_events = df_boot[df_boot['Event'].str.contains('Data|Network|Setup', case=False, na=False)]
                data_ready = data_events['Time_ms'].max() if not data_events.empty else "분석 불가"

                c1.metric("최종 부팅 시점 추정", f"{boot_complete:,} ms" if boot_complete else "N/A")
                c2.metric("Voice(RIL) Ready 시점", f"{voice_ready:,} ms" if isinstance(voice_ready, int) else voice_ready)
                c3.metric("Data(NW) Ready 시점", f"{data_ready:,} ms" if isinstance(data_ready, int) else data_ready)

                st.divider()

                # 2. 병목 지점 차트 렌더링
                st.write("#### 🚨 주요 병목 구간 분석 (Top 10)")

                # Delta_ms(구간 지연)가 큰 순서대로 정렬
                if 'Delta_ms' in df_boot.columns:
                    df_slow = df_boot[df_boot['Delta_ms'] > 0].sort_values("Delta_ms", ascending=False).head(10)

                    if not df_slow.empty:
                        fig_boot = px.bar(
                            df_slow, x='Delta_ms', y='Event', orientation='h',
                            color='Delta_ms', color_continuous_scale='Reds',
                            text='Delta_ms', title="부팅 지연 이벤트 (ms)",
                            labels={'Delta_ms': '지연 시간(ms)', 'Event': '이벤트 명'}
                        )
                        fig_boot.update_layout(yaxis={'categoryorder':'total ascending'}, height=450)
                        st.plotly_chart(fig_boot, use_container_width=True)
                else:
                    st.info("Delta_ms (구간 지연) 데이터가 존재하지 않아 병목 차트를 그릴 수 없습니다.")

                # 3. 전체 시퀀스 데이터 테이블
                with st.expander("📋 전체 부팅 시퀀스 타임라인 보기"):
                    if 'Time_ms' in df_boot.columns:
                        df_full = df_boot.sort_values("Time_ms")
                    else:
                        df_full = df_boot

                    st.dataframe(df_full, use_container_width=True)
            else:
                st.warning("분석 리포트 내에 부팅 이벤트 데이터가 없습니다. 로그가 `!@Boot` 포맷을 포함하고 있는지 확인하세요.")

            st.divider()
            ui.render_crash_analyzer(report_data)

            st.divider()
            ui.render_nitz_timeline(report_data.get("nitz_history", []))
        else:
            st.error(f"분석 리포트 파일(`{base_name}_report.json`)을 찾을 수 없습니다. 분석을 먼저 실행해 주세요.")
    else:
        st.warning("왼쪽 사이드바에서 분석할 로그 파일을 먼저 선택해 주세요.")

# ==========================================
# 🛰️ 신규: 위성(NTN) 통신 분석 탭
# ==========================================
with tab_ntn:
    current_target = st.session_state.get("current_file") or "Unknown"
    try:
      actual_file_name = df['source_file'].iloc[0] if not df.empty and 'source_file' in df.columns else "Unknown"
    except:
        actual_file_name = current_target
    current_base = current_target.replace("_payload.json", "") if current_target != "Unknown" else "Unknown"

    if current_base == "Unknown":
        st.warning("왼쪽 사이드바에서 분석할 로그 파일을 먼저 선택해 주세요.")
    else:
        sat_at_path = f"./result/{current_base}_sat_at.json"  # Tiantong
        ntn_fw_path = f"./result/{current_base}_ntn.json"     # SpaceX

        has_tiantong = False
        has_spacex = False

        # 🚨 1. Tiantong 진짜 데이터 유무 확인 (깡통 JSON 걸러내기)
        if os.path.exists(sat_at_path):
            try:
                with open(sat_at_path, 'r', encoding='utf-8') as f:
                    t_data = json.load(f)
                    # call_flow(로그 시퀀스)가 1개 이상 존재할 때만 True
                    if len(t_data.get("call_flow", [])) > 0:
                        has_tiantong = True
            except: pass

        # 🚨 2. SpaceX 진짜 데이터 유무 확인
        if os.path.exists(ntn_fw_path):
            try:
                with open(ntn_fw_path, 'r', encoding='utf-8') as f:
                    s_data = json.load(f)
                    # NTN 데이터가 비어있지 않은지 검사
                    if isinstance(s_data, dict) and any(v for v in s_data.values() if v):
                        has_spacex = True
                    elif isinstance(s_data, list) and len(s_data) > 0:
                        has_spacex = True
            except: pass

        sat_type = None

        # 3. 데이터가 존재하는 위성 타입에 맞춰 UI 독점 렌더링 (상호 배제)
        if has_tiantong:
            sat_type = "Tiantong"
            ui.render_sat_at_analyzer(current_base)
        elif has_spacex:
            sat_type = "SpaceX"
            ui.render_ntn_advanced_fw_analyzer(current_base)
        else:
            st.info("💡 이 로그에는 위성(NTN) 통신 관련 데이터(Tiantong 또는 SpaceX)가 존재하지 않거나 추출되지 않았습니다.")

        st.divider()
        if sat_type:
            if st.button(f"🛰️ {sat_type} 위성망 심층 진단", use_container_width=True):
                with st.spinner(f"{sat_type} 위성 데이터를 분석 중입니다..."):
                    health_kpi_json = get_device_health_kpi(current_base)
                    # 🚨 [하드코딩 제거] YAML에서 템플릿을 꺼내고, JSON 팩트를 동적으로 꽂아 넣습니다.
                    prompt_template = SATELLITE_PROMPTS.get(sat_type, "위성 분석 템플릿을 찾을 수 없습니다.")
                    sat_query = prompt_template.format(health_kpi_json=health_kpi_json)

                    raw_result = engine.ask(sat_query, current_file=actual_file_name)
                    final_text = raw_result[0] if isinstance(raw_result, tuple) else raw_result
                    if isinstance(final_text, str):
                        final_text = final_text.replace('\\n', '\n')

                    st.markdown(f"### 🤖 [AI {sat_type} 위성 진단 결과]")
                    st.info(final_text)

                    if "chat_history" in st.session_state:
                        st.session_state.chat_history.append({"role": "user", "content": f"{sat_type} 위성망 심층 진단해 줘."})
                        st.session_state.chat_history.append({"role": "assistant", "content": final_text})


with tab_internet:
    current_base = st.session_state.current_file.replace("_payload.json", "") if st.session_state.current_file else None
    ui.render_internet_stall_analyzer(current_base)