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
        result = subprocess.run(['ollama', 'list'], capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split('\n')[1:]
        models = [line.split()[0] for line in lines if line]
        return models
    except Exception as e:
        st.warning("Ollama 모델 목록을 불러오지 못했습니다. Ollama가 실행 중인지 확인해 주십시오.")
        return ["gemma2:9b", "gemma3:12b", "qwen2.5-coder:7b"]

def get_latest_csv_files(output_dir="./benchmark_results"):
    """benchmark_result 폴더 내에서 가장 최신의 요약 및 상세 CSV 파일을 찾습니다."""
    if not os.path.exists(output_dir):
        return None, None

    summary_files = glob.glob(os.path.join(output_dir, "model_benchmark_summary_*.csv"))
    detail_files = [f for f in glob.glob(os.path.join(output_dir, "model_benchmark_*.csv")) if "summary" not in f]

    latest_summary = max(summary_files, key=os.path.getctime) if summary_files else None
    latest_detail = max(detail_files, key=os.path.getctime) if detail_files else None

    return latest_summary, latest_detail

def render_benchmark_dashboard():
    st.header("LLM Model Benchmark Dashboard")

    # ==========================================
    # 1. 벤치마크 실행 컨트롤 패널
    # ==========================================
    st.subheader("벤치마크 실행")

    active_payload_file = st.session_state.current_file
    col_file, col_mode, col_model = st.columns([1, 1, 2])
    with col_file:
        st.text_input("현재 대상 파일", value=active_payload_file, disabled=True)

    with col_mode:
        selected_routing_mode = st.radio(
            "라우팅 모드 설정",
            options=["semantic", "llm", "hybrid"],
            index=1
        )

    with col_model:
        available_models = get_installed_ollama_models()
        selected_models = st.multiselect(
            "벤치마크 수행 모델 선택",
            options=available_models,
            default=available_models[:1] if available_models else None
        )

    if st.button("선택한 모델로 벤치마크 실행 (Run Benchmark)", type="primary"):
        if not selected_models:
            st.error("최소 하나 이상의 모델을 선택해 주십시오.")
        else:
            cmd = [
                sys.executable, "-u", "-X", "utf8", "scripts/benchmark_models.py",
                "--models"
            ] + selected_models + [
                "--files", active_payload_file,
                "--routing-mode", selected_routing_mode
            ]

            with st.status(f"[{selected_routing_mode} 모드] 벤치마크 수행 중... 터미널 로그를 확인하십시오.", expanded=True) as status:
                log_placeholder = st.empty()
                logs = []

                try:
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        encoding='utf-8',
                        errors='replace'
                    )

                    for line in iter(process.stdout.readline, ''):
                        if line:
                            logs.append(line.strip())
                            display_logs = "\n".join(logs[-20:])
                            log_placeholder.code(display_logs, language="bash")

                    process.stdout.close()
                    return_code = process.wait()

                    if return_code == 0:
                        status.update(label="벤치마크 완료. 대시보드를 갱신합니다.", state="complete", expanded=False)
                        st.toast("벤치마크 실행 완료")
                        st.rerun()
                    else:
                        status.update(label="벤치마크 오류 발생", state="error", expanded=True)
                        st.error("위 로그 창에서 에러 원인을 확인해 주십시오.")

                except Exception as e:
                    status.update(label="예외 발생", state="error")
                    st.error(f"실행 중 예외가 발생했습니다: {str(e)}")

    st.divider()

    # ==========================================
    # 2. 결과 시각화 대시보드
    # ==========================================
    summary_csv_path, detail_csv_path = get_latest_csv_files()

    if summary_csv_path:
        st.markdown(f"**최신 결과 파일:** `{os.path.basename(summary_csv_path)}`")
        df_summary = pd.read_csv(summary_csv_path)

        st.subheader("Key Performance Indicators")
        cols = st.columns(len(df_summary))

        for i, row in df_summary.iterrows():
            model_name = row['model']
            score_pct = row['avg_auto_score'] * 100
            latency = row['avg_latency_sec']

            with cols[i]:
                st.metric(
                    label=f"{model_name}",
                    value=f"Score: {score_pct:.0f}%",
                    delta=f"{latency:.2f} sec",
                    delta_color="inverse"
                )

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("평균 응답 속도 (Latency)")
            fig_latency = px.bar(
                df_summary, x='model', y='avg_latency_sec', color='model',
                text_auto='.2f', title="Latency Comparison (Lower is Better)"
            )
            fig_latency.update_layout(showlegend=False)
            st.plotly_chart(fig_latency, width="stretch")

        with col2:
            st.subheader("라우팅 정확도 (Recall)")
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

        st.subheader("모델별 요약 지표")
        styled_df = df_summary.style \
            .highlight_max(axis=0, subset=['avg_routing_tool_recall', 'avg_auto_score'], color='#D4E6F1') \
            .highlight_min(axis=0, subset=['avg_latency_sec', 'error_count', 'hallucination_candidate_rate'], color='#FDEBD0')
        st.dataframe(styled_df, width="stretch")

        st.divider()

    # ==========================================
    # 3. 상세 분석 (Drill-down)
    # ==========================================
    if detail_csv_path:
        st.subheader("케이스별 상세 분석 (Drill-down)")
        df_detail = pd.read_csv(detail_csv_path)

        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            selected_model = st.selectbox("모델 필터", ["전체"] + list(df_detail['model'].unique()))
        with filter_col2:
            selected_case = st.selectbox("테스트 케이스 필터", ["전체"] + list(df_detail['case_id'].unique()))

        filtered_df = df_detail.copy()
        if selected_model != "전체": filtered_df = filtered_df[filtered_df['model'] == selected_model]
        if selected_case != "전체": filtered_df = filtered_df[filtered_df['case_id'] == selected_case]

        display_columns = [
            'model', 'case_id', 'category', 'latency_sec',
            'routing_tool_recall', 'routing_log_type_recall',
            'missing_tools', 'missing_log_types', 'auto_score'
        ]

        display_columns = [c for c in display_columns if c in filtered_df.columns]

        styled_detail_df = filtered_df[display_columns].style \
            .highlight_max(axis=0, subset=['routing_tool_recall', 'auto_score'], color='#D4E6F1') \
            .highlight_min(axis=0, subset=['latency_sec'], color='#FDEBD0') \
            .highlight_null(color='#E5E7E9')

        st.dataframe(styled_detail_df, width="stretch")

    # ==========================================
    # 4. 오프라인 RAG 성능 평가 (Golden Dataset)
    # ==========================================
    st.write("---")
    st.subheader("오프라인 RAG 성능 평가 (Golden Dataset)")
    st.markdown("`run_golden_eval.py` 스크립트를 파이프라인으로 호출하여 골든 데이터셋 기반 오프라인 평가를 실행합니다.")

    with st.expander("평가 파라미터 설정", expanded=True):
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            eval_dataset_path = st.text_input("골든 데이터셋 경로", value="eval_golden_dataset.json")
            judge_model_name = st.selectbox(
                "심판 모델 (Judge)",
                ["ollama/qwen2.5-coder:7b", "ollama/gemma4:26b", "ollama/gemma3:12b"],
                index=0
            )
        with col_b2:
            current_rag_model = st.session_state.get('active_model', 'gemma4:12b-mlx')
            target_rag_model = st.text_input("RAG 모델 (답변 생성용)", value=current_rag_model)
            ollama_url = st.text_input("Ollama 서버 주소", value="http://localhost:11434")

    output_csv = "csv/rag_golden_eval_details.csv"
    summary_csv = "csv/rag_golden_eval_summary.csv"

    if st.button("RAG 자동 채점 시작 (Golden Eval)", type="primary", width="stretch"):
        if not os.path.exists(eval_dataset_path):
            st.error(f"데이터셋 파일을 찾을 수 없습니다: {eval_dataset_path}")
        else:
            with st.spinner("심사위원 모델이 답변의 충실성 및 관련성을 채점 중입니다... (터미널 로그를 함께 확인해 주십시오)"):
                try:
                    from run_golden_eval import evaluate_golden_dataset

                    evaluate_golden_dataset(
                        dataset_path=eval_dataset_path,
                        output_csv=output_csv,
                        summary_csv=summary_csv,
                        judge_model=judge_model_name,
                        rag_model=target_rag_model,
                        ollama_base=ollama_url
                    )
                    st.success(f"RAG 평가 완료. 결과가 저장되었습니다.\n- 상세: `{output_csv}`\n- 요약: `{summary_csv}`")

                except Exception as ex:
                    st.error(f"평가 실행 중 오류 발생: {str(ex)}")

    # ==========================================
    # 5. 채점 결과 실시간 시각화 대시보드
    # ==========================================
    if os.path.exists(summary_csv) and os.path.exists(output_csv):
        st.write("#### RAG 평가 결과 레포트")

        try:
            df_sum = pd.read_csv(summary_csv)
            if not df_sum.empty:
                m_cols = st.columns(len(df_sum.columns))
                for i, col_name in enumerate(df_sum.columns):
                    val = df_sum.iloc[0][col_name]
                    if isinstance(val, (int, float)):
                        m_cols[i].metric(label=col_name.replace("avg_", "평균 ").upper(), value=f"{val:.3f}")
                    else:
                        m_cols[i].metric(label=col_name, value=str(val))
        except Exception as e:
            st.warning("요약 데이터를 불러오지 못했습니다.")

        with st.expander("질문별 세부 채점 점수 및 생성 답변 보기", expanded=True):
            try:
                df_detail = pd.read_csv(output_csv)
                score_cols = [c for c in df_detail.columns if 'score' in c or 'coverage' in c or 'risk' in c]
                st.dataframe(
                    df_detail.style.background_gradient(subset=score_cols, cmap="YlGn"),
                    width="stretch"
                )
            except Exception as e:
                st.error(f"상세 파일 로드 실패: {e}")