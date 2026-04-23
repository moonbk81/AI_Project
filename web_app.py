import os
import streamlit as st
import json
import time
import pandas as pd
import plotly.express as px
import re

import ui_components as ui

# 1. 백엔드 엔진 및 자동화 모듈 불러오기
from ril_rag_chat import RilRagChat
from telephony_log_summarizer import TelephonyLogSummarizer
from prepare_rag_payload import RagPayloadBuilder
# web_app.py 내 "🚀 분석 및 DB 적재 시작" 버튼 로직 부분 수정
from network_ts_analyzer import NetworkTimeSeriesAnalyzer
from boot_stat import BootStatAnalyzer
from ntn_processor import NtnProcessor

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

def render_chat_interface(key_suffix="main"):
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
                            st.plotly_chart(fig, use_container_width=True, key=f"chart_{msg_idx}_{i}")

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
                    try:
                        raw_logs = json.loads(raw_data) if isinstance(raw_data, str) else []
                    except:
                        raw_logs = []

                    if raw_logs:
                        ref_text += "```text\n"
                        for log in raw_logs[:5]: ref_text += f"{log}\n"
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
def run_analysis_pipeline(file, use_slice, start_t, end_t, ai_engine):
    """UI와 분리된 순수 백엔드 데이터 처리 파이프라인"""
    start_total = time.time()
    progress_bar = st.progress(0)

    with st.status("🚀 자동화 파이프라인 가동 중...", expanded=True) as status:
        try:
            # 1. 파일 안전 저장
            os.makedirs("./temp_logs", exist_ok=True)
            temp_raw_path = os.path.join("./temp_logs", file.name)
            with open(temp_raw_path, "wb") as f:
                while chunk := file.read(65536):
                    f.write(chunk)

            filename = file.name
            base_name = os.path.splitext(filename)[0]
            target_log_path = temp_raw_path

            # 2. 타임라인 슬라이싱
            if use_slice:
                st.write(f"✂️ 타임라인 슬라이싱 중... ({start_t} ~ {end_t})")
                sliced_path = os.path.join("./temp_logs", f"sliced_{filename}")
                lines_kept = slice_log_by_time(temp_raw_path, sliced_path, start_t, end_t)
                if lines_kept == 0:
                    st.error("⚠️ 입력한 시간대에 해당하는 로그가 없습니다.")
                    st.stop()
                st.write(f"✅ 슬라이싱 완료! (총 {lines_kept:,}줄 추출됨)")
                target_log_path = sliced_path

            # 3. 파서 가동 및 JSON 병합
            os.makedirs("./result", exist_ok=True)
            temp_json_path = f"./result/{base_name}_report.json"
            payload_filename = f"{base_name}_payload.json"

            st.write("1️⃣ 원시 로그 분석 및 필터링 중... (Parser)")
            parser = TelephonyLogSummarizer(target_log_path)
            parser.run_batch('all', temp_json_path)
            progress_bar.progress(25)

            st.write("1️⃣-1️⃣ DNS 및 네트워크 시계열 분석 중...")
            net_analyzer = NetworkTimeSeriesAnalyzer(target_log_path)
            net_report = net_analyzer.analyze()
            with open(temp_json_path, 'r', encoding='utf-8') as f:
                combined_report = json.load(f)
            combined_report['network_timeseries'] = net_report
            with open(temp_json_path, 'w', encoding='utf-8') as f:
                json.dump(combined_report, f, indent=4, ensure_ascii=False)
            progress_bar.progress(50)

            # 4. RAG 페이로드 변환 및 Vector DB 적재
            st.write("2️⃣ RAG 맞춤형 지식 조각으로 변환 중...")
            builder = RagPayloadBuilder(temp_json_path)
            builder.build_payload(payload_filename)
            progress_bar.progress(65)

            st.write("2️⃣-1️⃣ 위성(NTN) 특화 지식 추출 및 DB 중...")
            ntn_proc = NtnProcessor(target_log_path)
            ntn_parsed_data = ntn_proc.run_parser()

            # 🚨 UI 대시보드(차트) 렌더링을 위해 메인 report.json에 위성 데이터 병합
            if ntn_parsed_data:
                with open(temp_json_path, 'r', encoding='utf-8') as f:
                    main_report = json.load(f)

                # main_report가 리스트 구조일 경우 그대로 연장(extend)
                if isinstance(main_report, list):
                    main_report.extend(ntn_parsed_data)
                # 만약 딕셔너리 구조라면 (안전장치)
                elif isinstance(main_report, dict):
                    main_report['ntn_logs'] = ntn_parsed_data

                with open(temp_json_path, 'w', encoding='utf-8') as f:
                    json.dump(main_report, f, indent=4, ensure_ascii=False)

            # AI 코파일럿을 위한 Vector DB용 페이로드 생성
            ntn_proc.build_and_save_payloads("./payloads")
            progress_bar.progress(75)

            st.write("3️⃣ Vector DB 임베딩 및 적재 중...")
            ai_engine.ingest_folder("./payloads")
            progress_bar.progress(100)

            # 5. 마무리 및 상태 초기화
            status.update(label="✅ 파이프라인 완료! 채팅창에 질문을 입력하세요.", state="complete", expanded=False)
            end_total = time.time()
            st.sidebar.metric(label="최근 파싱 소요시간", value=f"{end_total - start_total:.2f}초")

            st.session_state.current_file = payload_filename
            st.session_state.uploader_key += 1
            reset_analysis_context() # 이전 맥락 초기화

            st.toast(f"'{filename}' 분석 완료! 채팅창에 질문해주세요.", icon="✅")
            time.sleep(1)
            st.rerun()

        except Exception as e:
            status.update(label="❌ 파이프라인 실패", state="error")
            st.error(f"오류가 발생했습니다: {e}")

init_session_states() # 상태 초기화 실행

# ================================================

st.title("📡 안드로이드 RIL RAG 분석기")
st.markdown("단말 통신 로그를 원클릭으로 적재하고 AI와 분석을 시작하세요.")

# 세션 상태 초기화
if "messages" not in st.session_state: st.session_state.messages = []
if "last_ids" not in st.session_state: st.session_state.last_ids = []
if "last_metas" not in st.session_state: st.session_state.last_metas = []
if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0
if "feedback_key" not in st.session_state: st.session_state.feedback_key = 0
if "current_file" not in st.session_state: st.session_state.current_file = None

tab_chat, tab_dash, tab_boot = st.tabs(["💬 로그 분석 및 대화", "📊 전사 로그 통계 대시보드", "📈 부팅 성능"])

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
            st.session_state.current_file = None
            reset_analysis_context()
            st.success("DB가 성공적으로 비워졌습니다.")
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
        current_device = engine.embed_model.device
        st.write(f"활성 장치: **{str(current_device).upper()}**")

        if uploaded_file is None:
            st.error("❌ 먼저 파일을 업로드해주세요.")
        elif use_slicing and (not start_time or not end_time):
            st.error("❌ 슬라이싱을 켰다면 시작/종료 시간을 모두 입력해주세요.")
        else:
            run_analysis_pipeline(uploaded_file, use_slicing, start_time, end_time, engine)

    st.divider()

    st.header("📝 사내 지식 베이스 (트랙 B)")
    if st.session_state.get("last_ids") and st.session_state.get("last_metas"):

        # 1. 방금 검색된 로그들의 타입(log_type)만 중복 없이 추출
        retrieved_types = list(set(
            m.get('log_type', 'Unknown') for m in st.session_state.last_metas if m
        ))

        # 2. 박제 타겟 선택 UI
        target_type = st.selectbox("📌 박제할 로그 카테고리 선택", retrieved_types)

        feedback = st.text_area(
            "해결책 / 원인 코멘트 입력:",
            height=150,
            key=f"feedback_{st.session_state.feedback_key}"
        )

        if st.button("💾 DB에 선택한 카테고리만 영구 박제", use_container_width=True):
            if feedback.strip():
                # 3. 🚨 타겟으로 선택한 log_type과 일치하는 ID만 골라내기
                target_ids = [
                    doc_id for doc_id, meta in zip(st.session_state.last_ids, st.session_state.last_metas)
                    if meta and meta.get('log_type') == target_type
                ]

                if target_ids:
                    engine.save_knowledge(target_ids, feedback)
                    st.toast(f"✅ {target_type} ({len(target_ids)}건)에 성공적으로 박제되었습니다!", icon="💾")
                else:
                    st.warning("선택한 카테고리에 해당하는 로그가 없습니다.")

                st.session_state.last_ids = []
                st.session_state.last_metas = []
                st.session_state.feedback_key += 1
                time.sleep(0.5)
                st.rerun()
            else:
                st.warning("코멘트를 먼저 입력해주세요.")
    else:
        st.info("먼저 채팅창에서 로그 분석을 진행해주세요.")

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

    # 버튼을 2x2 그리드로 배치
    col_btn1, col_btn2, col_btn3 = st.columns(3)
    col_btn4, col_btn5 = st.columns(2)
    with col_btn1:
        if st.button("📞 통화 끊김(Drop) 분석", use_container_width=True):
            quick_prompt = "Call Session 로그를 바탕으로 통화 끊김(Drop) 및 Fail 원인을 분석하고, 당시 OOS 이력이나 망 이탈 징후가 있었는지 확인해 줘."
    with col_btn2:
        if st.button("🌐 네트워크 이상 분석", use_container_width=True):
            quick_prompt = "Network Timeline Stat 및 DNS 로그를 분석해서, 지연(latency) 시간이 비정상적으로 튀는 이상 징후나 앱 차단 이력을 찾아내 줘."
    with col_btn3:
        if st.button("🔋 배터리/크래시 분석", use_container_width=True):
            quick_prompt = "Battery Drain Report와 Crash/ANR 로그를 확인해서 전력 광탈 원인과 비정상 종료된 프로세스가 있는지 분석해 줘."
    with col_btn4:
        if st.button("🚫 망 등록(Reg) 및 OOS 분석", use_container_width=True):
            quick_prompt = (
                "OOS_Event 로그에서 Slot ID별(Slot 0, Slot 1) voice_reg 및 data_reg 상태 변화를 분석해줘. "
                "시간대별로 각 슬롯의 망 등록 상태가 어떻게 변했는지 비교하고, "
                "특정 슬롯만 OOS 에 빠졌는지 아니면 전체 서비스가 이탈했는지 파악해라."
            )
    with col_btn5:
        if st.button("📶 안테나(Signal) 레벨 분석", use_container_width=True):
            quick_prompt = (
                "Signal_Level 로그를 분석해서 Slot별(Slot 0, Slot 1) 안테나 수신 레벨(0~5)이 시간대별로 어떻게 변했는지 파악해 줘."
                 "신호가 0이나 1로 뚝 떨어지는 수신 저하 구간이 있었는지 확인해라."
            )

    st.divider()

    # ==========================================
    # 💬 대화 히스토리 및 차트/참고로그 렌더링
    # ==========================================
    render_chat_interface(key_suffix="main")

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
                answer, ids, metas = engine.ask(prompt, current_file=current_target, chat_history=st.session_state.messages[-5:])

                # [복구 완료] 원본 로그 텍스트 조립 구역
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
                        for log in raw_logs[:5]: ref_text += f"{log}\n"
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
                    # 📈 1. 통합 로그 타임라인 차트 (Event Correlation)
                    # ==========================================
                    st.subheader("📈 통합 로그 타임라인 분석")

                    import plotly.graph_objects as go

                    # 데이터 사용량은 시계열 데이터가 아니므로 1개의 Y축만 사용하도록 심플하게 원복
                    fig = go.Figure()

                    # [A] 신호 세기 (Line Chart)
                    sig_df = df[df['log_type'] == 'Signal_Level'].copy()
                    if not sig_df.empty:
                        sig_df = sig_df.sort_values('time')
                        fig.add_trace(
                            go.Scatter(x=sig_df['time'], y=sig_df['level'], name="Signal Level", mode='lines+markers', line=dict(color='#1f77b4', width=2))
                        )

                    # [B] 통화 드랍 (Scatter - Red ❌)
                    call_df = df[df['log_type'] == 'Call_Session'].copy()
                    if not call_df.empty and 'status' in call_df.columns:
                        fail_calls = call_df[call_df['status'].str.contains('FAIL|DROP', na=False, case=False)]
                        if not fail_calls.empty:
                            hover_text = fail_calls['fail_reason'] if 'fail_reason' in fail_calls.columns else "Unknown Reason"
                            fig.add_trace(
                                go.Scatter(x=fail_calls['time'], y=[3.5]*len(fail_calls),
                                        mode='markers', marker=dict(size=12, color='red', symbol='x'),
                                        name="Call Drop", text=hover_text, hoverinfo='text+x'))


                    # 레이아웃 튜닝
                    fig.update_layout(
                        height=450,
                        hovermode="x unified",
                        margin=dict(l=0, r=0, t=30, b=0),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                    )

                    # 🚨 [핵심 픽스] X축 글자 떡짐 방지 적용!
                    fig.update_xaxes(nticks=15, tickangle=-45)
                    fig.update_yaxes(title_text="Signal Level (0~5)", range=[-0.5, 5.5], dtick=1)

                    st.plotly_chart(fig, use_container_width=True)
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
                    ui.render_data_usage_profiling(df)

                    st.divider()
                    ui.render_ntn_advanced_fw_analyzer(df)

                    # ==========================================
                    # 🤖 AI 종합 기술 진단 리포트 (Powered by Gemma2 9B)
                    # ==========================================
                    st.subheader("🤖 AI 종합 기술 진단 리포트 (Powered by Gemma2 9B)")
                    if st.button("📝 전체 로그 종합 분석 리포트 생성"):
                        with st.spinner("9B 엔진이 모든 로그의 상관관계를 분석하여 전문 리포트를 작성 중입니다..."):

                            actual_file_name = df['source_file'].iloc[0] if not df.empty and 'source_file' in df.columns else "Unknown"

                            # 🚨 [핵심 해결책] Vector DB를 믿지 않고, df에서 직접 팩트 데이터를 강제 추출!
                            fact_data = "데이터 사용량 기록 없음"
                            fact_call = "통화 기록 없음"

                            if not df.empty and 'log_type' in df.columns:
                                # 1. 데이터 사용량 Top 3 텍스트화
                                du_df = df[df['log_type'] == 'Data_Usage'].copy()
                                if not du_df.empty:
                                    du_df['total_mb'] = pd.to_numeric(du_df['total_mb'], errors='coerce')
                                    top_du = du_df.sort_values(by='total_mb', ascending=False).head(3)
                                    fact_data = ", ".join([f"{r['app_name']} ({r.get('rat','Unknown')}망 {r['total_mb']}MB)" for _, r in top_du.iterrows()])

                                # 2. 통화 드랍/실패 이력 텍스트화
                                call_df = df[df['log_type'] == 'Call_Session'].copy()
                                if not call_df.empty:
                                    fail_calls = call_df[call_df['status'].astype(str).str.contains('FAIL|DROP', na=False, case=False)]
                                    if not fail_calls.empty:
                                        fact_call = ", ".join([f"{r.get('status', 'Unknown')} (원인: {r.get('fail_reason', 'N/A')})" for _, r in fail_calls.iterrows()])
                                    else:
                                        fact_call = "모든 통화 100% 정상 성공 (드랍 없음)"

                            combined_query = f"""
                            [절대 팩트 데이터 강제 주입]
                            - 데이터 사용량 Top 3 앱 현황: {fact_data}
                            - 통화 이력 및 상태 기록: {fact_call} (※ 기록이 없다면 드랍/실패 없이 모두 100% 정상 통화된 것임)

                            위 팩트 데이터와 검색된 로그를 바탕으로 15년 차 수석 엔지니어의 관점에서 단말 상태를 진단해.

                            [🚨 엄격한 답변 규칙 🚨]
                            1. "추가 데이터가 필요하다", "확증하기 어렵다" 같은 방어적이거나 원론적인 변명은 절대 금지.
                            2. 주어진 데이터 안에서 가장 확률이 높은 '근본 원인(Root Cause)' 가설을 과감하게 제시할 것.
                            3. 비정상적으로 막대한 데이터(수십~수백 GB)를 쓴 항목이 있다면, 신호 세기와 무관하게 이것이 모뎀 부하 및 배터리 광탈의 주범임을 강력하게 지적할 것.
                            4. 통화 기록이 없다면 "망과 기지국 상태가 매우 양호하여 단 한 건의 드랍도 발생하지 않음"으로 긍정 평가할 것.
                            """

                            raw_result = engine.ask(combined_query, current_file=actual_file_name)

                            # 튜플/리스트 분리 방어 코드 (어제 적용하신 것 유지)
                            if isinstance(raw_result, (tuple, list)):
                                report_answer = raw_result[0]
                            else:
                                report_answer = raw_result

                            st.success("✅ 분석이 완료되었습니다.")
                            st.markdown(f"""
                            <div style="background-color: #f0f2f6; padding: 20px; border-radius: 10px; border-left: 5px solid #ff4b4b;">
                                {report_answer}
                            </div>
                            """, unsafe_allow_html=True)
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
        st.info(f"현재 분석 대상: `{current_target}`")

        if st.button("🏁 부팅 로그 추출 및 성능 시각화 실행"):
            with st.spinner("부팅 로그를 수집하여 분석 중입니다..."):
                db_results = engine.collection.get(where={"source_file": current_target})
                metas = db_results.get("metadatas", [])
                # 2. 낡은 파싱 로직 싹 제거! 엔진이 가져온 metas를 그대로 던져줍니다.
                analyzer = BootStatAnalyzer(metas)

                # 3. 데이터프레임이 정상적으로 채워졌는지 확인
                if not analyzer.df.empty:
                    summary = analyzer.get_summary()

                    if summary:
                        # KPI 지표 렌더링
                        c1, c2, c3 = st.columns(3)
                        c1.metric("부팅 완료 시간", f"{summary.get('boot_complete', 0):,} ms" if summary.get('boot_complete') else "N/A")
                        c2.metric("Voice Ready (Total)", f"{summary.get('total_voice_ms', 0):,} ms" if summary.get('total_voice_ms') else "N/A")
                        c3.metric("Data Ready (Total)", f"{summary.get('total_data_ms', 0):,} ms" if summary.get('total_data_ms') else "N/A")

                        # 병목 지점 차트 렌더링
                        st.write("### 🚨 주요 병목 지점")
                        df_bot = analyzer.df[analyzer.df['Delta_ms'] > 0].sort_values("Delta_ms", ascending=False)
                        if not df_bot.empty:
                            fig = px.bar(df_bot, x='Delta_ms', y='Event', orientation='h',
                                         color='Delta_ms', color_continuous_scale='Reds',
                                         text='Delta_ms', title="부팅 지연 이벤트 분석")
                            fig.update_layout(yaxis={'categoryorder':'total ascending'})
                            st.plotly_chart(fig, use_container_width=True)

                        # 전체 시퀀스 데이터
                        with st.expander("📋 전체 부팅 시퀀스 데이터 보기"):
                            st.dataframe(analyzer.df, use_container_width=True)
                    else:
                        st.warning("데이터는 찾았으나 분석 가능한 부팅 이벤트 마일스톤이 없습니다.")
                else:
                    st.error("현재 파일에서 부팅 데이터를 찾을 수 없습니다. 덤프 파일을 다시 파싱하고 DB에 적재(초기화 후 재적재)했는지 확인해 주세요.")
    else:
        st.warning("왼쪽 사이드바에서 분석할 로그 파일을 먼저 선택해 주세요.")