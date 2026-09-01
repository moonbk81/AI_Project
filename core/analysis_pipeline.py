"""Log analysis pipeline, free of any UI framework dependency.

The FastAPI backend (``backend/main.py``) drives this module. It stays free of
any UI import on purpose: this is where the layering inversion that pulled a
UI framework into the API process was cut out, and the split is worth keeping
even now that the only front end is the browser UI served from ``backend/``.
"""

from dataclasses import dataclass
import inspect
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
    """``start_time_str`` ~ ``end_time_str`` 구간만 뽑아 씁니다.

    로그 타임스탬프에는 연도가 없어 "MM-DD HH:MM:SS" 문자열로 비교합니다. 시작이
    종료보다 크면 사용자가 연말을 넘는 구간을 지정한 것이므로(12-31 23:00 ~
    01-01 01:00) 비교를 뒤집습니다.

    종료 시각을 지나면 즉시 break 하던 최적화는 제거했습니다. dumpstate 는 섹션을
    이어 붙여 시간이 되돌아가는 지점이 있고, 병합 로그는 더합니다. 그런 줄 하나가
    나오면 남은 파일 전체가 조용히 잘려나갔습니다.
    """
    pattern = re.compile(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})')
    written_lines = 0
    is_in_range = False

    crosses_year = bool(start_time_str) and bool(end_time_str) and start_time_str > end_time_str

    with open(input_path, 'r', encoding='utf-8', errors='ignore') as fin, \
         open(output_path, 'w', encoding='utf-8') as fout:
        for line in fin:
            match = pattern.search(line)
            if match:
                current_time = match.group(1)
                if crosses_year:
                    is_in_range = current_time >= start_time_str or current_time <= end_time_str
                else:
                    is_in_range = start_time_str <= current_time <= end_time_str

            if is_in_range:
                fout.write(line)
                written_lines += 1

    return written_lines


def merge_log_files(file_paths, output_path):
    """여러 로그를 시간순으로 합친다.

    타임스탬프가 없는 줄(backtrace, CPU usage 표, 스택 프레임 등)에 고정 키를 주면
    파일 맨 앞으로 끌려 올라가서 자기 앵커 줄과 떨어진다. 그러면 앵커 기준
    ±N 줄 window 를 뜨는 후단 파서가 정작 원인이 적힌 본문을 못 본다. 그래서
    직전 타임스탬프를 물려받게 하고, 같은 시각 안에서는 (파일, 원래 줄 번호)로
    원본 순서를 그대로 유지한다.
    """
    time_pattern = re.compile(r'^(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3})')

    # 타임스탬프에 연도가 없어서 "MM-DD" 문자열로 정렬하면 연말을 넘는 로그가
    # 뒤집힌다(01-01 이 12-31 앞으로 간다). 12월과 1월이 함께 보이면 연말을 넘은
    # 것으로 보고, 상반기를 다음 해로 취급한다. 단말 로그가 반년을 넘기지는 않는다.
    months = set()
    for fp in file_paths:
        with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                match = time_pattern.search(line)
                if match:
                    months.add(match.group(1)[:2])
    crosses_year = "12" in months and "01" in months

    def year_cycle(timestamp):
        if not crosses_year:
            return 0
        return 1 if timestamp[:2] <= "06" else 0

    all_lines = []
    for file_index, fp in enumerate(file_paths):
        # 첫 타임스탬프가 나오기 전의 머리말은 파일 맨 앞에 그대로 둔다.
        last_cycle, last_key = 0, "00-00 00:00:00.000"
        with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
            for line_number, line in enumerate(f):
                match = time_pattern.search(line)
                if match:
                    last_key = match.group(1)
                    last_cycle = year_cycle(last_key)
                all_lines.append((last_cycle, last_key, file_index, line_number, line))

    all_lines.sort(key=lambda x: (x[0], x[1], x[2], x[3]))

    with open(output_path, 'w', encoding='utf-8') as f:
        for *_, line in all_lines:
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
    defect_code="",
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

    def analysis_progress(message: str, value: int):
        if progress_callback:
            progress_callback(message, value)

    try:
        accepts_analysis_progress = "progress_callback" in inspect.signature(orchestrator.run_batch).parameters
    except (TypeError, ValueError):
        accepts_analysis_progress = False
    if accepts_analysis_progress:
        success = orchestrator.run_batch(report_path, progress_callback=analysis_progress)
    else:
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

    def payload_progress(done: int, total: int, label: str):
        if not progress_callback or total <= 0:
            return
        share = min(1.0, max(0.0, done / total))
        progress_callback(f"RAG payload 생성 중... ({label}, {done}/{total})", 50 + int(15 * share))

    try:
        accepts_payload_progress = "progress_callback" in inspect.signature(builder.build_payload).parameters
    except (TypeError, ValueError):
        accepts_payload_progress = False
    if accepts_payload_progress:
        builder.build_payload(payload_name, progress_callback=payload_progress)
    else:
        builder.build_payload(payload_name)
    if progress_callback:
        progress_callback("RAG payload 생성 완료. Vector DB 임베딩 시작...", 65)

    payload_path = os.path.join("./payloads", payload_name)

    def embedding_progress(done: int, total: int):
        if not progress_callback or total <= 0:
            return
        share = min(1.0, max(0.0, done / total))
        progress_callback(f"Vector DB 임베딩 진행 중... ({done}/{total})", 65 + int(34 * share))

    ingest_kwargs = {"force": True, "uploaded_by": owner, "defect_code": defect_code}
    try:
        accepts_progress = "progress_callback" in inspect.signature(ai_engine.ingest_file).parameters
    except (TypeError, ValueError):
        accepts_progress = False
    if accepts_progress:
        ingest_kwargs["progress_callback"] = embedding_progress
    ai_engine.ingest_file(payload_path, **ingest_kwargs)
    if progress_callback:
        progress_callback("", 100)

    return AnalysisPipelineResult(
        base_name=base_name,
        target_log_path=target_log_path,
        report_path=report_path,
        payload_path=payload_path,
        current_file=payload_name,
    )
