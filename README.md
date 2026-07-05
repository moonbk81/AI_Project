# Android RIL RAG Dashboard

Android RIL(Radio Interface Layer), Telephony, 시스템 로그를 파싱하고 Local LLM + RAG로 장애 원인을 분석하는 Streamlit 기반 로그 분석 콘솔입니다.

이 프로젝트는 단순히 로그를 벡터 DB에 넣고 질의하는 구조가 아니라, `Parser -> Analysis Bucket -> Structured Event -> RAG Payload -> Retrieval/Rerank -> Guardrail -> LLM` 흐름으로 동작합니다. 목적은 통신 장애 RCA(Root Cause Analysis)를 로그 팩트 기반으로 생성하고, Local LLM의 환각을 줄이는 것입니다.

## 주요 기능

- **통합 로그 분석 파이프라인**
  - 다중 로그 업로드, 시간순 병합, 분석 리포트 생성, RAG payload 생성, ChromaDB 적재를 Streamlit UI에서 실행합니다.
  - 진입점: `web_app.py`, `app/pipeline.py`, `log_orchestrator.py`, `prepare_rag_payload.py`

- **Android Telephony/RIL 도메인 파서**
  - Call Session, IMS/SIP, OOS, Radio Power, DataCall, DNS, Internet Stall, Crash/ANR, Native Crash, Binder, Battery/Thermal, NTN, Satellite AT 로그를 분석합니다.
  - 주요 구현: `parsers/`

- **Analysis Bucket 기반 사전 필터링**
  - 대용량 dumpstate/logcat을 한 번 스캔해 parser별 후보 로그 버킷과 context window를 구성합니다.
  - 구현: `parsers/analysis_bucket_builder.py`

- **RAG 챗봇 및 라우팅**
  - Semantic / Hybrid / LLM 라우팅 모드를 지원합니다.
  - 질문 intent에 따라 필요한 `log_type`과 분석 tool을 선택하고, ChromaDB 검색 결과를 rerank합니다.
  - 구현: `ril_rag_chat.py`, `rag/routing.py`, `rag/retrieval.py`

- **Fact 기반 도메인 분석 도구**
  - Call, Network, Crash, Battery, Binder, Satellite, KPI 분석을 deterministic tool fact로 추출합니다.
  - 구현: `agent_toolkit/`

- **Structured Event / Answer Guardrail**
  - Crash/ANR 부재 확인, Binder proxy leak, Thread Exhaustion, Call Drop trap 같은 고위험 질의에서 확정 팩트를 우선 주입합니다.
  - 구현: `rca/structured_event_renderer.py`, `rag/answer_guardrails.py`

- **분석 사례 관리**
  - 현재 분석에서 참조된 로그와 엔지니어 코멘트를 지식 베이스에 저장하고, 이후 유사 질의에 참고 컨텍스트로 주입합니다.
  - 구현: `app/tabs/knowledge_tab.py`, `RilRagChat.save_knowledge()`

- **Golden Evaluation**
  - Golden dataset 기반으로 RAG 답변을 생성하고, 별도 LLM judge로 accuracy/evidence/safety를 평가합니다.
  - 구현: `run_golden_eval.py`, `eval_golden_dataset.json`, `csv/`

## 지원 분석 영역

- Call Drop / Call Fail / Normal Release 오판 방지
- IMS / SIP signaling
- OOS(Out Of Service), 망 등록/복구 이슈
- Radio Power, Airplane Mode 전후 이벤트
- DataCall setup failure
- Internet Stall / Data Stall / Network validation failure
- DNS latency, DNS failure, policy block
- Binder warning, Binder proxy leak, Binder thread exhaustion
- System Kill / System WTF
- Java Crash / Native Crash / ANR
- Battery drain / Thermal / CPU usage
- Boot, Build Info, System Property, NITZ
- NTN / SpaceX / Tiantong satellite log
- Data usage

## 분석 파이프라인

```text
Raw Android Log / Dumpstate
        |
        v
app/pipeline.py
  - upload
  - merge
  - optional slice
        |
        v
log_orchestrator.py
  - AnalysisBucketBuilder
  - domain parsers
  - result/*_report.json
        |
        v
prepare_rag_payload.py
rag_builders/
  - payloads/*_payload.json
        |
        v
rag/ingest.py
  - SentenceTransformer embedding
  - ChromaDB collection
        |
        v
RilRagChat.ask()
  - routing
  - retrieval/rerank
  - tool facts
  - structured event renderer
  - guardrails
        |
        v
Ollama Local LLM Answer
```

## 폴더 구조

```text
AI_Project/
  app/
    pipeline.py
    sidebar.py
    chat_panel.py
    tabs/
      chat_tab.py
      dashboard_tab.py
      boot_tab.py
      satellite_tab.py
      internet_tab.py
      benchmark_tab.py
      knowledge_tab.py
  agent_toolkit/
    call_tools.py
    network_tools.py
    crash_tools.py
    battery_tools.py
    binder_tools.py
    satellite_tools.py
    kpi_tools.py
    correlation.py
  core/
    config.py
    constants.py
    golden_matcher.py
    telephony_constants.py
  parsers/
    analysis_bucket_builder.py
    diagnostic_parser.py
    telephony_parser.py
    rilj_parser.py
    data_call_processor.py
    ims_sip_processor.py
    internet_stall_parser.py
    native_crash_parser.py
    battery_thermal_analyzer.py
    network_ts_analyzer.py
    ntn_processor.py
    sat_at_parser.py
    system_property_parser.py
    call/
      ims_call_parser.py
      cs_call_state_machine.py
  rag/
    answer_guardrails.py
    chroma_utils.py
    domain_boosts.py
    ingest.py
    llm_client.py
    prompt_builder.py
    prompt_template.py
    query_classifiers.py
    rerank_injections.py
    retrieval.py
    routing.py
  rag_builders/
    builder.py
    common.py
    telephony_builder.py
    network_builder.py
    crash_builder.py
    battery_builder.py
    binder_builder.py
    device_builder.py
  rca/
    structured_event_renderer.py
  ui/
    common.py
    telephony_ui.py
    network_ui.py
    crash_ui.py
    power_ui.py
    satellite_ui.py
  tests/
    test_semantic_routing.py
    test_semantic_routing_fuzzy.py
    routing_test_cases.json
    routing_fuzzy_cases.json
    routing_score_logger.py
  scripts/
    benchmark_models.py
    bechmark_models.md
  web_app.py
  ril_rag_chat.py
  log_orchestrator.py
  prepare_rag_payload.py
  run_golden_eval.py
  benchmark_ui.py
  agent_tools.py
  ui_components.py
  config.yaml
  requirements.txt
```

> `log/`, `payloads/`, `result/`, `temp_logs/`, `chroma_db/`, `benchmark_results/`, `eval_logs/`, `test_reports/`, `debug_prompts/` 등은 실행 중 생성되거나 로컬 데이터가 쌓이는 디렉터리입니다.

## 핵심 설정

### 라우팅 설정

`config.yaml`의 `routing_map`이 질문 intent, 실행 tool, 검색 대상 `log_type`을 정의합니다.

주요 intent:

- `Call_Analysis`
- `Call_Drop_Trap`
- `Time_Context_Inference`
- `Network_OOS`
- `DNS_Latency`
- `Data_Call_Setup`
- `Internet_Stall`
- `Battery_Thermal`
- `Crash_ANR`
- `System_Kill_WTF`
- `Radio_Power`
- `Nitz_Time_Analysis`
- `NTN_SpaceX`
- `Tiantong_Satellite`
- `Data_Usage_Analysis`

### 모델 설정

`core/config.py`의 `MODEL_CONFIG`와 `DEFAULT_MODEL_BY_DEVICE`에서 모델별 context, batch size, top_k 등을 관리합니다.

현재 코드 기준 기본값:

- CPU/MPS: `gemma4:12b`
- CUDA: `gemma3:4b`

Streamlit 사이드바에서는 설치된 Ollama 모델 목록을 조회해 선택할 수 있습니다.

## 설치

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

평가/테스트 스크립트까지 실행하려면 현재 코드에서 추가로 다음 패키지가 필요할 수 있습니다.

```bash
pip install -r requirements-dev.txt
```

임베딩 모델은 실행 환경에 따라 다르게 로드됩니다.

- CUDA/CPU: 프로젝트 루트의 `bge-m3-offline/` 경로를 우선 사용
- MPS: `BAAI/bge-m3` Hugging Face 모델명을 사용

오프라인 환경에서는 `bge-m3-offline/` 준비 여부를 확인해야 합니다.

## 실행

Ollama 서버와 사용할 모델을 먼저 준비합니다.

```bash
ollama serve
ollama pull gemma4:12b
```

Streamlit 앱 실행:

```bash
streamlit run web_app.py
```

앱에서 사용하는 주요 탭:

- `로그 분석`: 질문/답변 및 참조 로그 확인
- `통계 대시보드`: 분석 결과 기반 지표 확인
- `부팅·Crash·ANR·NITZ`: 부팅, crash, ANR, NITZ 관련 분석
- `위성 통신`: NTN/Tiantong/SpaceX 관련 분석
- `인터넷 품질`: Internet Stall, DNS, validation 관련 분석
- `평가 결과`: benchmark/golden eval 결과 확인
- `지식 베이스`: 분석 사례 조회 및 등록

## Golden Evaluation

기본 실행:

```bash
python run_golden_eval.py \
  --dataset eval_golden_dataset.json \
  --judge-model ollama/qwen2.5-coder:7b \
  --rag-model gemma4:12b \
  --ollama-base http://localhost:11434
```

특정 케이스만 실행:

```bash
python run_golden_eval.py \
  --test-id TC-018 \
  --test-id TC-019 \
  --judge-model ollama/qwen2.5-coder:7b \
  --rag-model gemma3:4b
```

특정 category만 실행:

```bash
python run_golden_eval.py \
  --category Call_Drop_Trap \
  --category System_Bottleneck
```

출력:

- 상세 결과: `csv/rag_golden_eval_details.csv`
- 요약 결과: `csv/rag_golden_eval_summary.csv`

## 테스트

Semantic routing 테스트:

```bash
pytest tests/test_semantic_routing.py
pytest tests/test_semantic_routing_fuzzy.py
```

주의:

- `RilRagChat()` 초기화 과정에서 ChromaDB와 embedding model을 로드하므로 테스트가 무겁습니다.
- `tests/routing_score_logger.py`가 `test_reports/` 아래에 라우팅 점수 로그를 남길 수 있습니다.

## 주요 산출물

- `result/*_report.json`: parser/orchestrator 분석 결과
- `payloads/*_payload.json`: ChromaDB 적재용 RAG payload
- `chroma_db/`: persistent ChromaDB 저장소
- `csv/rag_golden_eval_*.csv`: Golden evaluation 결과
- `benchmark_results/`: 모델 benchmark 결과
- `test_reports/`: routing 테스트 로그
- `debug_prompts/`: `RAG_DEBUG_PROMPT=1` 설정 시 마지막 prompt/retrieval debug 자료

## 개발 메모

- README는 현재 코드 기준으로 정리되어 있으며, 로컬 실행 데이터 디렉터리는 구조 예시에서 제외했습니다.
- `requirements.txt`는 앱 핵심 의존성 위주입니다. 평가/테스트까지 포함한 개발 의존성 분리가 필요하면 별도 `requirements-dev.txt`로 분리하는 것이 좋습니다.
- `scripts/bechmark_models.md`는 현재 파일명을 그대로 반영했습니다. 의도한 이름이 `benchmark_models.md`라면 파일명 정리가 필요합니다.
