import streamlit as st
import pandas as pd
import plotly.express as px
import os
import subprocess
import glob
import sys
import re

def get_installed_ollama_models():
    """로컬에 설치된 Ollama 모델 목록을 가져옵니다."""
    try:
        # ollama list 명령어 실행
        result = subprocess.run(['ollama', 'list'], capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split('\n')[1:] # 첫 줄(헤더) 제외
        models = [line.split()[0] for line in lines if line]
        return models
    except Exception as e:
        st.warning("Ollama 모델 목록을 불러오지 못했습니다. Ollama가 실행 중인지 확인해주세요.")
        # 기본값 폴백 (테스트용)
        return ["gemma2:9b", "gemma3:12b", "qwen2.5-coder:7b"]

def get_latest_csv_files(output_dir="./benchmark_results"):
    """benchmark_result 폴더 내에서 가장 최신의 요약 및 상세 CSV 파일을 찾습니다."""
    if not os.path.exists(output_dir):
        return None, None

    summary_files = glob.glob(os.path.join(output_dir, "model_benchmark_summary_*.csv"))
    detail_files = [f for f in glob.glob(os.path.join(output_dir, "model_benchmark_*.csv")) if "summary" not in f]

    # 수정 시간(getctime) 기준으로 가장 마지막에 생성된 파일 추출
    latest_summary = max(summary_files, key=os.path.getctime) if summary_files else None
    latest_detail = max(detail_files, key=os.path.getctime) if detail_files else None

    return latest_summary, latest_detail

def render_benchmark_dashboard():
    st.header("📊 LLM Model Benchmark Dashboard")

    # ==========================================
    # 1. 벤치마크 실행 컨트롤 패널 (최상단)
    # ==========================================
    st.subheader("▶️ 벤치마크 실행")

    # 세션 또는 앱 상태에서 활성화된 파일명 가져오기 (실제 앱의 로직에 맞게 키 변경 필요)
    # active_payload_file = st.session_state.get('active_file', 'dumpstate_4_payload.json')
    active_payload_file = st.session_state.current_file
    col_file, col_mode, col_model = st.columns([1, 1, 2])
    with col_file:
        st.text_input("현재 대상 파일", value=active_payload_file, disabled=True)

    with col_mode:
        selected_routing_mode = st.radio(
            "🛤️ 라우팅 모드 설정",
            options=["semantic", "llm", "hybrid"],
            index=1 # 기본값 llm
        )

    with col_model:
        available_models = get_installed_ollama_models()
        selected_models = st.multiselect(
            "벤치마크 수행 모델 선택",
            options=available_models,
            default=available_models[:1] if available_models else None
        )

    if st.button("🚀 선택한 모델로 벤치마크 실행 (Run Benchmark)", type="primary"):
        if not selected_models:
            st.error("최소 하나 이상의 모델을 선택해주세요.")
        else:
            # -u 옵션을 추가하여 파이썬 print 버퍼링을 강제로 끄고 즉시 출력되게 함
            cmd = [
                sys.executable, "-u", "scripts/benchmark_models.py",
                "--models"
            ] + selected_models + [
                "--files", active_payload_file,
                "--routing-mode", selected_routing_mode
            ]

            # 🎨 st.status를 사용해 펼쳤다 접을 수 있는 실시간 진행 상태 UI 생성
            with st.status(f"[{selected_routing_mode} 모드] 벤치마크 수행 중... 터미널 로그를 확인하세요.", expanded=True) as status:
                log_placeholder = st.empty()
                logs = []

                try:
                    # Popen을 사용하여 서브프로세스를 비동기적으로 실행하고, 출력을 파이프로 연결
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, # 에러 로그도 터미널 창에 같이 표시
                        text=True,
                        bufsize=1,
                        encoding='utf-8',
                        errors='replace'
                    )

                    # 한 줄씩 읽어서 UI 터미널 창에 즉시 업데이트
                    for line in iter(process.stdout.readline, ''):
                        if line:
                            logs.append(line.strip())
                            # UI가 버벅이지 않도록 최근 20줄만 잘라서 보여줌
                            display_logs = "\n".join(logs[-20:])
                            log_placeholder.code(display_logs, language="bash")

                    # 프로세스 종료 대기
                    process.stdout.close()
                    return_code = process.wait()

                    if return_code == 0:
                        status.update(label="✅ 벤치마크 완료! 대시보드를 갱신합니다.", state="complete", expanded=False)
                        st.toast("✅ 벤치마크 실행 완료!")
                        st.rerun() # 결과 갱신을 위해 즉시 새로고침
                    else:
                        status.update(label="❌ 벤치마크 오류 발생", state="error", expanded=True)
                        st.error("위 로그 창에서 에러 원인을 확인하세요.")

                except Exception as e:
                    status.update(label="❌ 예외 발생", state="error")
                    st.error(f"실행 중 예외가 발생했습니다: {str(e)}")

    st.divider()

    # ==========================================
    # 2. 결과 시각화 대시보드
    # ==========================================
    summary_csv_path, detail_csv_path = get_latest_csv_files()

    if not summary_csv_path:
        st.info("실행된 벤치마크 결과가 없습니다. 위에서 벤치마크를 실행해주세요.")
        return

    st.markdown(f"**최신 결과 파일:** `{os.path.basename(summary_csv_path)}`")
    df_summary = pd.read_csv(summary_csv_path)

    # 2-1. 상단 KPI 요약
    st.subheader("💡 Key Performance Indicators")
    cols = st.columns(len(df_summary))

    for i, row in df_summary.iterrows():
        model_name = row['model']
        score_pct = row['avg_auto_score'] * 100
        latency = row['avg_latency_sec']

        with cols[i]:
            st.metric(
                label=f"🤖 {model_name}",
                value=f"Score: {score_pct:.0f}%",
                delta=f"{latency:.2f} sec",
                delta_color="inverse" # 시간이 짧을수록 좋으므로 inverse 적용
            )

    # 2-2. 차트 시각화 영역
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("⏱️ 평균 응답 속도 (Latency)")
        fig_latency = px.bar(
            df_summary, x='model', y='avg_latency_sec', color='model',
            text_auto='.2f', title="Latency Comparison (Lower is Better)"
        )
        fig_latency.update_layout(showlegend=False)
        st.plotly_chart(fig_latency, width="stretch")

    with col2:
        st.subheader("🎯 라우팅 정확도 (Recall)")
        df_recall = df_summary[['model', 'avg_routing_tool_recall', 'avg_routing_log_type_recall']]
        df_recall_melted = df_recall.melt(id_vars='model', var_name='Metric', value_name='Score')
        df_recall_melted['Metric'] = df_recall_melted['Metric'].replace({
            'avg_routing_tool_recall': 'Tool Recall', 'avg_routing_log_type_recall': 'Log Type Recall'
        })

        fig_recall = px.bar(
            df_recall_melted, x='model', y='Score', color='Metric', barmode='group',
            text_auto='.2f', title="Tool vs Log Type Recall"
        )
        st.plotly_chart(fig_recall, width="stretch")

    # 2-3. 요약 테이블 (파스텔 톤 적용)
    st.subheader("📝 모델별 요약 지표")
    styled_df = df_summary.style \
        .highlight_max(axis=0, subset=['avg_routing_tool_recall', 'avg_auto_score'], color='#D4E6F1') \
        .highlight_min(axis=0, subset=['avg_latency_sec', 'error_count', 'hallucination_candidate_rate'], color='#FDEBD0')
    st.dataframe(styled_df, width="stretch")

    st.divider()

    # ==========================================
    # 3. 상세 분석 (Drill-down)
    # ==========================================
    if detail_csv_path:
        st.subheader("🔍 케이스별 상세 분석 (Drill-down)")
        df_detail = pd.read_csv(detail_csv_path)

        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            selected_model = st.selectbox("🤖 모델 필터", ["전체"] + list(df_detail['model'].unique()))
        with filter_col2:
            selected_case = st.selectbox("📌 테스트 케이스 필터", ["전체"] + list(df_detail['case_id'].unique()))

        filtered_df = df_detail.copy()
        if selected_model != "전체": filtered_df = filtered_df[filtered_df['model'] == selected_model]
        if selected_case != "전체": filtered_df = filtered_df[filtered_df['case_id'] == selected_case]

        display_columns = [
            'model', 'case_id', 'category', 'latency_sec',
            'routing_tool_recall', 'routing_log_type_recall',
            'missing_tools', 'missing_log_types', 'auto_score'
        ]

        # 없는 컬럼 방어 코드
        display_columns = [c for c in display_columns if c in filtered_df.columns]

        styled_detail_df = filtered_df[display_columns].style \
            .highlight_max(axis=0, subset=['routing_tool_recall', 'auto_score'], color='#D4E6F1') \
            .highlight_min(axis=0, subset=['latency_sec'], color='#FDEBD0') \
            .highlight_null(color='#E5E7E9')

        st.dataframe(styled_detail_df, width="stretch")

    # ==========================================
    # 🚀 신규 기능: 로컬 LLM RAG 자동 평가(Judge) UI
    # ==========================================
    st.write("---")
    st.subheader("⚖️ 오프라인 RAG 성능 평가 (LLM-as-a-judge)")

    # 1. 평가 대상이 될 JSONL 데이터셋 목록 조회
    eval_log_dir = "./eval_logs"
    jsonl_files = glob.glob(os.path.join(eval_log_dir, "rag_eval_dataset_*.jsonl"))

    if not jsonl_files:
        st.info("💡 벤치마크를 수행하면 모델별 RAG 평가 데이터셋(*.jsonl)이 여기에 표시됩니다.")
    else:
        # 사용자가 보기 편하게 파일명만 추출
        file_options = {os.path.basename(f): f for f in jsonl_files}
        selected_jsonl_name = st.selectbox("📂 평가할 모델 데이터셋 선택", list(file_options.keys()))
        selected_jsonl_path = file_options[selected_jsonl_name]

        # 모델명 동적 추출 (rag_eval_dataset_model_name.jsonl -> model_name)
        match = re.search(r"rag_eval_dataset_(.*?)\.jsonl", selected_jsonl_name)
        model_tag = match.group(1) if match else "evaluated"

        # 판정(Judge)에 사용할 모델 선택
        st.markdown("**심사위원 모델 설정 (Ollama)**")
        judge_model = st.selectbox(
            "🤖 채점을 진행할 대형 모델 선택 (추천: 12B 이상)",
            ["ollama/gemma3:12b", "ollama/qwen2.5-coder:7b", "ollama/gemma4-e2b:q4"],
            index=0
        )

        eval_col1, eval_col2 = st.columns([1, 4])
        with eval_col1:
            # ✨ 핵심: UI에서 평가 스크립트를 직접 실행하는 버튼
            run_eval_btn = st.button("🔥 RAG 자동 채점 시작", type="primary", use_container_width=True)

        # 출력 파일 경로 정의
        output_csv = f"./benchmark_results/rag_eval_details_{model_tag}.csv"
        summary_csv = f"./benchmark_results/rag_eval_summary_{model_tag}.csv"

        if run_eval_btn:
            with st.spinner("🤖 심사위원 모델이 답변의 충실성 및 관련성을 채점 중입니다... (시간이 소요될 수 있습니다)"):
                try:
                    # cmd 명령어 조립 및 실행
                    cmd = [
                        "python", "run_rag_eval_csv.py",
                        "--log-file", selected_jsonl_path,
                        "--judge-model", judge_model,
                        "--output", output_csv,
                        "--summary-output", summary_csv
                    ]

                    # 실시간 subprocess 실행
                    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    st.success(f"🎉 RAG 평가 완료! 결과가 저장되었습니다.\n- 상세: {output_csv}\n- 요약: {summary_csv}")

                except subprocess.CalledProcessError as e:
                    st.error(f"❌ 평가 스크립트 실행 중 오류가 발생했습니다.\n{e.stderr}")
                except Exception as ex:
                    st.error(f"❌ 알 수 없는 오류 발생: {str(ex)}")

        # ==========================================
        # 📊 채점 결과 실시간 시각화 대시보드
        # ==========================================
        if os.path.exists(summary_csv) and os.path.exists(output_csv):
            st.write("#### 📈 RAG 평가 결과 레포트")

            # 1. 요약 정보 요약 카드(Metrics) 표시
            try:
                df_sum = pd.read_csv(summary_csv)
                if not df_sum.empty:
                    m_cols = st.columns(len(df_sum.columns))
                    for i, col_name in enumerate(df_sum.columns):
                        val = df_sum.iloc[0][col_name]
                        # 수치형 데이터는 예쁘게 포맷팅
                        if isinstance(val, (int, float)):
                            m_cols[i].metric(label=col_name.replace("avg_", "평균 ").upper(), value=f"{val:.3f}")
                        else:
                            m_cols[i].metric(label=col_name, value=str(val))
            except Exception as e:
                st.warning("요약 데이터를 불러오지 못했습니다.")

            # 2. 세부 채점 내역 리스트 (Drill-down)
            with st.expander("🔍 질문별 세부 채점 점수 및 생성 답변 보기"):
                try:
                    df_detail = pd.read_csv(output_csv)
                    # 주요 점수 컬럼 하이라이트팅하여 데이터프레임 출력
                    score_cols = [c for c in df_detail.columns if 'score' in c or 'faithfulness' in c or 'answer_relevance' in c]
                    st.dataframe(
                        df_detail.style.background_gradient(subset=score_cols, cmap="YlGn"),
                        use_container_width=True
                    )
                except Exception as e:
                    st.error(f"상세 파일 로드 실패: {e}")
