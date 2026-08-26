# Android RIL RAG Dashboard

Android RIL(Radio Interface Layer), Telephony, 시스템 로그를 파싱하고 Local/Ollama 또는 vLLM 기반 LLM + RAG로 장애 원인을 분석하는 FastAPI 기반 로그 분석 콘솔입니다. 기본 UI는 backend가 직접 서빙하는 브라우저 UI입니다.

이 프로젝트는 단순히 로그를 벡터 DB에 넣고 질의하는 구조가 아니라, `Parser -> Analysis Bucket -> Structured Event -> RAG Payload -> Retrieval/Rerank -> Guardrail -> LLM` 흐름으로 동작합니다. 목적은 통신 장애 RCA(Root Cause Analysis)를 로그 팩트 기반으로 생성하고, Local LLM의 환각을 줄이는 것입니다.

## 주요 기능

- **통합 로그 분석 파이프라인**
  - 다중 로그 업로드, 시간순 병합, 분석 리포트 생성, RAG payload 생성, ChromaDB 적재를 FastAPI backend job으로 실행합니다.
  - 진입점: `backend/main.py`, `core/analysis_pipeline.py`, `log_orchestrator.py`, `prepare_rag_payload.py`

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
- NTN / SpaceX satellite log
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
Configured LLM Answer
```

## LLM 연결 설정

기본값은 기존과 동일하게 로컬 Ollama를 사용합니다. DGX Spark의 vLLM OpenAI-compatible 서버를 사용하려면 앱 실행 전에 환경변수를 지정합니다.

```bash
export RAG_LLM_PROVIDER=vllm
export RAG_LLM_BASE_URL=http://10.253.68.95:3000/api/v1
export RAG_LLM_MODEL=qwen72b
export RAG_LLM_API_KEY=<your-api-key>
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

vLLM 실행 시 `--served-model-name qwen2.5-72b-instruct`처럼 별도 이름을 지정했다면 `RAG_LLM_MODEL`도 동일하게 맞춥니다.

## 실행

기본 실행 경로는 FastAPI backend입니다. 채팅 질의, 적재 파일 목록 조회, DB 초기화, 로그 업로드/분석 job, PLM 연동, 브라우저 UI 서빙을 모두 backend에서 처리합니다.

```bash
conda activate ai
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

실행 후 브라우저에서 `http://localhost:8080/` 또는 `http://localhost:8080/ui/`를 엽니다. `/`는 자동으로 `/ui/`로 이동합니다.

macOS/Linux에서는 아래 스크립트로 실행할 수 있습니다.

```bash
./scripts/start_backend.command
```

DGX vLLM을 사용할 때는 `RAG_LLM_PROVIDER`, `RAG_LLM_BASE_URL`, `RAG_LLM_MODEL`, `RAG_LLM_API_KEY`를 지정한 상태로 실행하면 backend 터미널에 전달됩니다.

### PLM 로컬 테스트 모드 (사내망 밖에서 작업할 때)

집에서는 사내 PLM 에 닿지 않아 PLM 화면이 전부 비어버립니다. 이 모드를 켜면
backend 가 샘플 결함·첨부·코멘트로 답하고, 코멘트/결함 등록 같은 쓰기는
**전송하지 않고** 성공한 것처럼 응답합니다.

```bash
PLM_LOCAL_TEST=1 python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

재시작 없이 바꾸려면 브라우저 UI 의 PLM 탭 상단 스위치를 쓰거나:

```bash
curl -X POST localhost:8080/plm/local-test -H 'Content-Type: application/json' -d '{"enabled": true}'
```

샘플 첨부의 ZIP 에는 실제로 열리는 로그가 들어 있어서 "로그 추출해 분석"까지
오프라인으로 확인할 수 있습니다. 브라우저 UI 의 PLM 탭 상단 스위치도 같은 상태를 봅니다.

### Open WebUI 등 OpenAI 호환 클라이언트에서 쓰기

backend는 OpenAI Chat Completions 규격을 그대로 흉내내는 엔드포인트를 함께
제공합니다. 이 RAG를 "모델 하나"로 취급하는 채팅 클라이언트에 붙일 수 있습니다.

```
GET  /v1/models
POST /v1/chat/completions
```

어떤 로그에 대한 질문인지는 **모델 이름**이 정합니다. 모델 목록에 적재된 파일이
그대로 나오므로, 클라이언트의 모델 선택기가 파일 선택기 역할을 합니다.

| 모델 | 질의 대상 |
|---|---|
| `ril-rag` | 적재된 전체 로그 |
| `ril-rag:<파일명>` | 그 파일만 |

Open WebUI에 연결하려면 *Settings → Connections* 에서 OpenAI 호환 연결을 추가하고
Base URL 에 `http://<backend-host>:8080/v1` 을 넣습니다. API 키는 검사하지
않으므로 아무 값이나 됩니다.

알아둘 점:

- 답변은 한 번에 생성되므로, `stream: true` 로 요청해도 완성된 답이 한 덩어리로
  전달됩니다(토큰 단위 타이핑 효과는 없음).
- 엔진이 내놓는 추론 과정은 `<think>` 블록으로 감싸 보냅니다. Open WebUI는 이걸
  접히는 "Thinking" 영역으로 렌더링합니다.
- 이 엔드포인트는 나머지 API와 마찬가지로 **인증이 없습니다.** 사내망 밖에
  노출하지 마십시오.
- 대시보드/PLM 탭 같은 화면은 채팅 규격으로 표현할 수 없으므로 backend 브라우저 UI에서 사용합니다.


Backend 모드의 자동 분석 파이프라인은 `POST /jobs/analyze`로 작업을 만들고 `GET /jobs/{job_id}` 또는 `GET /jobs`로 진행 상태를 조회합니다. 대시보드 metadata는 `GET /metadata`, 지식 베이스는 `GET /knowledge`와 `POST /knowledge`, 분석 결과 JSON은 `GET /results/{base_name}/{artifact}`에서 처리합니다. `GET /health`는 runtime, engine load 여부, active job 수를 반환합니다.

Backend 모드에서 `POST /db/reset`은 Vector DB와 backend의 `payloads/`, `result/`, `temp_logs/` 산출물, 메모리 job 상태를 함께 초기화합니다.

## 폴더 구조

```text
AI_Project/
  backend/
    main.py
    charts_api.py
    openai_api.py
    static/
      index.html
      styles.css
      js/
        app.js
        api.js
        views/
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
  tests/
    test_semantic_routing.py
    test_semantic_routing_fuzzy.py
    routing_test_cases.json
    routing_fuzzy_cases.json
    routing_score_logger.py
  scripts/
    start_backend.command
    benchmark_models.py
    bechmark_models.md
  ril_rag_chat.py
  log_orchestrator.py
  prepare_rag_payload.py
  run_golden_eval.py
  agent_tools.py
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
- `Data_Usage_Analysis`

### 모델 설정

`core/config.py`의 `MODEL_CONFIG`와 `DEFAULT_MODEL_BY_DEVICE`에서 모델별 context, batch size, top_k 등을 관리합니다.

현재 코드 기준 기본값:

- CPU/MPS: `gemma4:12b`
- CUDA: `gemma3:4b`

실행 중인 모델과 provider 상태는 브라우저 UI의 채팅 화면과 `GET /health`에서 확인할 수 있습니다.

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

## 로컬 실행 예시

Ollama 서버와 사용할 모델을 먼저 준비합니다.

```bash
ollama serve
ollama pull gemma4:12b
```

FastAPI backend 실행:

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

브라우저에서 `http://localhost:8080/ui/`를 엽니다. 주요 화면:

- `로그 분석`: 질문/답변 및 참조 로그 확인
- `통계 대시보드`: 분석 결과 기반 지표 확인
- `부팅·Crash·ANR·NITZ`: 부팅, crash, ANR, NITZ 관련 분석
- `위성 통신`: NTN/SpaceX 관련 분석
- `인터넷 품질`: Internet Stall, DNS, validation 관련 분석
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

Backend client 단위 테스트:

```bash
conda run -n ai python -m pytest tests/test_backend_api.py tests/test_backend_client.py
```

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
- `benchmark_results/`: 모델 benchmark 결과 (`scripts/benchmark_models.py` CLI 실행 시 생성)
- `test_reports/`: routing 테스트 로그
- `debug_prompts/`: `RAG_DEBUG_PROMPT=1` 설정 시 마지막 prompt/retrieval debug 자료

## 개발 메모

- README는 현재 코드 기준으로 정리되어 있으며, 로컬 실행 데이터 디렉터리는 구조 예시에서 제외했습니다.
- `requirements.txt`는 앱 핵심 의존성 위주입니다. 평가/테스트까지 포함한 개발 의존성 분리가 필요하면 별도 `requirements-dev.txt`로 분리하는 것이 좋습니다.
- `scripts/bechmark_models.md`는 현재 파일명을 그대로 반영했습니다. 의도한 이름이 `benchmark_models.md`라면 파일명 정리가 필요합니다.
