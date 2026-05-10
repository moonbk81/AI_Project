import streamlit as st
import pandas as pd
import plotly.express as px
import os
import subprocess
import glob
import sys

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

def get_latest_csv_files(output_dir="./benchmark_result"):
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
    col_file, col_model = st.columns([1, 2])
    with col_file:
        st.text_input("현재 활성화된 파일 (Target File)", value=active_payload_file, disabled=True)

    with col_model:
        available_models = get_installed_ollama_models()
        selected_models = st.multiselect(
            "벤치마크를 수행할 모델을 선택하세요",
            options=available_models,
            default=available_models[:1] if available_models else None
        )

    if st.button("🚀 선택한 모델로 벤치마크 실행 (Run Benchmark)", type="primary"):
        if not selected_models:
            st.error("최소 하나 이상의 모델을 선택해주세요.")
        else:
            with st.spinner(f"[{', '.join(selected_models)}] 벤치마크 수행 중... (시간이 다소 소요될 수 있습니다)"):
                try:
                    # benchmark_models.py 실행 커맨드 구성
                    cmd = [
                        sys.executable, "benchmark_models.py",
                        "--models"
                    ] + selected_models + [
                        "--files", active_payload_file
                    ]

                    # 실행 및 대기
                    process = subprocess.run(cmd, capture_output=True, text=True)

                    if process.returncode == 0:
                        st.success("✅ 벤치마크가 성공적으로 완료되었습니다! 결과를 새로고침합니다.")
                        # 캐시 무효화 및 화면 갱신을 위해 rerun
                        st.rerun()
                    else:
                        st.error(f"❌ 벤치마크 실행 중 오류가 발생했습니다.\n\n{process.stderr}")
                except Exception as e:
                    st.error(f"❌ 예외 발생: {str(e)}")

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
        st.plotly_chart(fig_latency, use_container_width=True)

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
        st.plotly_chart(fig_recall, use_container_width=True)

    # 2-3. 요약 테이블 (파스텔 톤 적용)
    st.subheader("📝 모델별 요약 지표")
    styled_df = df_summary.style \
        .highlight_max(axis=0, subset=['avg_routing_tool_recall', 'avg_auto_score'], color='#D4E6F1') \
        .highlight_min(axis=0, subset=['avg_latency_sec', 'error_count', 'hallucination_candidate_rate'], color='#FDEBD0')
    st.dataframe(styled_df, use_container_width=True)

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

        st.dataframe(styled_detail_df, use_container_width=True)