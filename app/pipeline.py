"""Analysis pipeline orchestration for the Streamlit web app."""

import os
import re

import streamlit as st

from log_orchestrator import LogOrchestrator
from prepare_rag_payload import RagPayloadBuilder

def slice_log_by_time(input_path, output_path, start_time_str, end_time_str):
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
                    break
                else:
                    is_in_range = False

            if is_in_range:
                fout.write(line)
                written_lines += 1

    return written_lines

def merge_log_files(file_paths, output_path):
    time_pattern = re.compile(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3})')
    all_lines = []

    for fp in file_paths:
        with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                match = time_pattern.search(line)
                sort_key = match.group(1) if match else "00-00 00:00:00.000"
                all_lines.append((sort_key, line))

    all_lines.sort(key=lambda x: x[0])

    with open(output_path, 'w', encoding='utf-8') as f:
        for _, line in all_lines:
            f.write(line)

def run_analysis_pipeline(uploaded_files, use_slice, start_t, end_t, ai_engine):
    progress_bar = st.progress(0)

    with st.status("통합 분석 파이프라인 가동 중...", expanded=True) as status:
        try:
            os.makedirs("./temp_logs", exist_ok=True)
            saved_paths = []

            for file in uploaded_files:
                original_name = file.name
                name, ext = os.path.splitext(original_name)
                counter = 1
                unique_name = original_name

                while os.path.exists(os.path.join("./temp_logs", unique_name)):
                    unique_name = f"{name}_{counter}{ext}"
                    counter += 1

                path = os.path.join("./temp_logs", unique_name)
                with open(path, "wb") as f:
                    f.write(file.getbuffer())
                saved_paths.append(path)

            if len(saved_paths) > 1:
                st.write(f"{len(saved_paths)}개의 로그 파일을 시간순으로 병합 중...")
                base_name = os.path.splitext(os.path.basename(saved_paths[0]))[0] + "_merged"
                target_log_path = os.path.join("./temp_logs", f"{base_name}.txt")
                merge_log_files(saved_paths, target_log_path)
            else:
                target_log_path = saved_paths[0]
                base_name = os.path.splitext(os.path.basename(saved_paths[0]))[0]

            if use_slice:
                st.write("타임라인 슬라이싱 적용 중...")
                sliced_path = os.path.join("./temp_logs", f"sliced_{base_name}.txt")
                slice_log_by_time(target_log_path, sliced_path, start_t, end_t)
                target_log_path = sliced_path

            st.write("통신 스택 로그 교차 분석 진행 중...")
            orchestrator = LogOrchestrator(target_log_path)
            report_path = f"./result/{base_name}_report.json"
            success = orchestrator.run_batch(report_path)
            progress_bar.progress(50)

            if success is False:
                raise RuntimeError("LogOrchestrator 분석 실패")
            if not os.path.exists(report_path):
                raise FileNotFoundError(f"Report 파일 누락: {report_path}")
            if os.path.getsize(report_path) == 0:
                raise RuntimeError(f"Report 파일 크기가 0입니다: {report_path}")

            st.write("RAG 데이터셋 구성 및 Vector DB 임베딩 진행 중...")
            builder = RagPayloadBuilder(report_path)
            payload_name = f"{base_name}_payload.json"
            builder.build_payload(payload_name)

            payload_path = os.path.join("./payloads", payload_name)
            ai_engine.ingest_file(payload_path, force=True)
            progress_bar.progress(100)

            status.update(label="분석 완료. 대시보드에서 결과를 확인하십시오.", state="complete", expanded=False)
            st.session_state.current_file = f"{base_name}_payload.json"
            st.session_state.messages = []
            st.rerun()
        except Exception as e:
            status.update(label="파이프라인 실행 오류", state="error")
            st.error(f"System Error: {e}")