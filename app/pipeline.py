"""Streamlit wrapper around the analysis pipeline.

The pipeline itself lives in ``core.analysis_pipeline`` so that the FastAPI
backend can run it without importing Streamlit. Only the UI-facing wrapper
below belongs here.
"""

import streamlit as st

from core.analysis_pipeline import (
    AnalysisPipelineResult,
    ProgressCallback,
    merge_log_files,
    run_analysis_core,
    save_uploaded_files,
    slice_log_by_time,
)

# Re-exported for callers that still import these from app.pipeline.
__all__ = [
    "AnalysisPipelineResult",
    "ProgressCallback",
    "merge_log_files",
    "run_analysis_core",
    "run_analysis_pipeline",
    "save_uploaded_files",
    "slice_log_by_time",
]


def run_analysis_pipeline(uploaded_files, use_slice, start_t, end_t, ai_engine):
    progress_bar = st.progress(0)

    with st.status("통합 분석 파이프라인 가동 중...", expanded=True) as status:
        try:
            saved_paths = save_uploaded_files(uploaded_files)

            def update_progress(message, progress=None):
                if message:
                    st.write(message)
                if progress is not None:
                    progress_bar.progress(progress)

            result = run_analysis_core(
                saved_paths,
                use_slice,
                start_t,
                end_t,
                ai_engine,
                progress_callback=update_progress,
            )

            status.update(label="분석 완료. 대시보드에서 결과를 확인하십시오.", state="complete", expanded=False)
            st.session_state.current_file = result.current_file
            # Don't clear messages - preserve chat history in the Log Analysis tab
            # Messages should only be cleared when switching files

            # Don't rerun - keep current screen state
            st.success("Analysis complete. Current screen state is preserved.")
        except Exception as e:
            status.update(label="파이프라인 실행 오류", state="error")
            st.error(f"System Error: {e}")
