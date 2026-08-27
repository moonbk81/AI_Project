"""Log analysis pipeline, free of any UI framework dependency.

The FastAPI backend (``backend/main.py``) drives this module. It stays free of
any UI import on purpose: this is where the layering inversion that pulled a
UI framework into the API process was cut out, and the split is worth keeping
even now that the only front end is the browser UI served from ``backend/``.
"""

from dataclasses import dataclass
import os
import re
from typing import Callable, Iterable, Optional

from log_orchestrator import LogOrchestrator
from prepare_rag_payload import RagPayloadBuilder


ProgressCallback = Optional[Callable[[str, Optional[int]], None]]


@dataclass
class AnalysisPipelineResult:
    base_name: str
    target_log_path: str
    report_path: str
    payload_path: str
    current_file: str


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


def save_uploaded_files(uploaded_files, temp_dir="./temp_logs"):
    """Persist upload-like objects (``.name`` + ``.getbuffer()``) and return local paths."""
    os.makedirs(temp_dir, exist_ok=True)
    saved_paths = []

    files_to_process = list(uploaded_files) if uploaded_files else []
    for file in files_to_process:
        original_name = file.name
        name, ext = os.path.splitext(original_name)
        counter = 1
        unique_name = original_name

        while os.path.exists(os.path.join(temp_dir, unique_name)):
            unique_name = f"{name}_{counter}{ext}"
            counter += 1

        path = os.path.join(temp_dir, unique_name)
        with open(path, "wb") as f:
            f.write(file.getbuffer())
        saved_paths.append(path)

    return saved_paths


def run_analysis_core(
    file_paths: Iterable[str],
    use_slice,
    start_t,
    end_t,
    ai_engine,
    progress_callback: ProgressCallback = None,
    temp_dir="./temp_logs",
    result_dir="./result",
    owner="",
):
    """Run the reusable analysis pipeline without any UI dependency.

    ``owner`` 는 올린 사람의 이름표(Knox ID)다. 결과 파일 이름이 올린 로그의
    파일명만으로 정해지면 한 서버를 여럿이 쓸 때 같은 이름을 올린 사람이 남의
    리포트와 적재분을 조용히 덮어쓴다. 이름표가 붙으면 서로 섞이지 않고, 같은
    사람이 같은 파일을 다시 돌리면 예전처럼 자기 것만 갱신된다.
    """
    saved_paths = list(file_paths or [])
    if not saved_paths:
        raise ValueError("분석할 파일이 없습니다.")

    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(result_dir, exist_ok=True)
    os.makedirs("./payloads", exist_ok=True)

    if len(saved_paths) > 1:
        if progress_callback:
            progress_callback(f"{len(saved_paths)}개의 로그 파일을 시간순으로 병합 중...", None)
        base_name = os.path.splitext(os.path.basename(saved_paths[0]))[0] + "_merged"
        target_log_path = os.path.join(temp_dir, f"{base_name}.txt")
        merge_log_files(saved_paths, target_log_path)
    else:
        target_log_path = saved_paths[0]
        base_name = os.path.splitext(os.path.basename(saved_paths[0]))[0]

    label = re.sub(r"[^A-Za-z0-9._-]", "_", str(owner or "").strip())
    if label:
        base_name = f"{base_name}__{label}"

    if use_slice:
        if progress_callback:
            progress_callback("타임라인 슬라이싱 적용 중...", None)
        sliced_path = os.path.join(temp_dir, f"sliced_{base_name}.txt")
        slice_log_by_time(target_log_path, sliced_path, start_t, end_t)
        target_log_path = sliced_path

    if progress_callback:
        progress_callback("통신 스택 로그 교차 분석 진행 중...", None)
    orchestrator = LogOrchestrator(target_log_path)
    report_path = os.path.join(result_dir, f"{base_name}_report.json")
    success = orchestrator.run_batch(report_path)
    if progress_callback:
        progress_callback("", 50)

    if success is False:
        raise RuntimeError("LogOrchestrator 분석 실패")
    if not os.path.exists(report_path):
        raise FileNotFoundError(f"Report 파일 누락: {report_path}")
    if os.path.getsize(report_path) == 0:
        raise RuntimeError(f"Report 파일 크기가 0입니다: {report_path}")

    if progress_callback:
        progress_callback("RAG 데이터셋 구성 및 Vector DB 임베딩 진행 중...", None)
    builder = RagPayloadBuilder(report_path)
    payload_name = f"{base_name}_payload.json"
    builder.build_payload(payload_name)

    payload_path = os.path.join("./payloads", payload_name)
    ai_engine.ingest_file(payload_path, force=True, uploaded_by=owner)
    if progress_callback:
        progress_callback("", 100)

    return AnalysisPipelineResult(
        base_name=base_name,
        target_log_path=target_log_path,
        report_path=report_path,
        payload_path=payload_path,
        current_file=payload_name,
    )
