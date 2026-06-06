# 📡 Android RIL RAG Dashboard (통신 로그 AI 분석 파이프라인)

## 📖 개요 (Overview)
Android RIL(Radio Interface Layer) 및 Telephony 시스템 로그를 원클릭으로 파싱하고, 대형 언어 모델(LLM)과 RAG(Retrieval-Augmented Generation) 기술을 활용해 통신 장애 원인을 수석 엔지니어 수준으로 분석해 주는 자동화 대시보드 시스템입니다.

## ✨ 주요 기능 (Key Features)
* **LLM 기반 RAG 챗봇 (`ril_rag_chat.py`)**: Semantic / Hybrid Routing 기반으로 사용자 질의를 분석하고 Vector DB에서 관련 로그를 검색하여 장애 원인 및 RCA(Root Cause Analysis)를 수행합니다. Structured Event 기반 Guardrail Layer를 통해 Hallucination을 줄이고 사실 기반 응답을 강화합니다.
* **통합 로그 오케스트레이션 (`log_orchestrator.py`)**: 대용량 Android 로그를 분류·정규화하고 Call, OOS, Crash, ANR, Binder, Internet Stall 등 주요 이벤트를 추출합니다.
* **Analysis Bucket 기반 Pre-filter Layer (`parsers/analysis_bucket_builder.py`)**: Dumpstate를 단일 스캔하여 Crash, ANR, Binder, DataCall, Battery Thermal 등 Parser별 후보 로그를 사전 추출하고 Context Window를 구성하여 대용량 로그 분석 성능을 향상시킵니다.
* **Fact 기반 분석 (`agent_toolkit/`)**: Domain별 분석 모듈(Call, Network, Crash, Battery, Binder, NTN)을 통해 실제 로그 기반 KPI와 장애 팩트를 추출하여 LLM 환각(Hallucination)을 최소화합니다.
* **RCA(Event) 분석 레이어**: Binder Leak, Internet Stall, Telephony, Radio Power, OOS 이벤트에 대해 원인 후보를 구조적으로 분석하고 AI 응답 품질을 향상시킵니다.
* **Answer Guardrail Layer (`rag/answer_guardrails.py`)**: Crash/ANR 부재 확인, Binder Leak 부정 확인, Thread Exhaustion, RCA Correlation 등 고신뢰 패턴에 대해 구조화 이벤트 기반 응답을 생성합니다.
* **Knowledge Base 기능**: 과거 장애 분석 결과 및 해결 사례를 저장하고 향후 분석 시 RAG 기반 참고 자료로 활용합니다.
* **대화형 시각화 대시보드**: Streamlit + Plotly 기반으로 통화 이력, 신호 레벨, 배터리, 데이터 사용량, DNS 지연, 발열 등을 시각화합니다.

* **NTN(위성 통신) 분석**: Tiantong 및 SpaceX 기반 NTN 로그를 분석하고 위성망 상태를 진단합니다.

## 🎯 지원 분석 영역 (Supported Analysis Domains)

* Call Drop / Call Fail
* IMS / SIP Signaling 분석
* OOS (Out Of Service) 및 망 등록 이슈
* Internet Stall / Data Stall
* DNS Latency 및 DNS Failure
* Binder Warning / Binder Leak 분석
* ANR (Application Not Responding)
* Native / Java Crash 분석
* Battery Thermal / Battery Drain
* NTN (Tiantong / SpaceX) 위성 통신

## 📊 RAG 평가 체계 (RAG Evaluation)

* Golden Set 기반 정량 평가
* LLM-as-a-Judge 기반 응답 품질 평가
* Semantic / Hybrid Routing 성능 비교
* Retrieval 적합성 및 RCA 정확도 검증
* Overall Score 기반 품질 추적
* 신규 Parser 및 RCA Layer 추가 시 회귀 테스트 수행
* Golden Dataset 기반 Trap Case 검증 (Negative Check / Correlation / Absence Check)
* Structured RCA Event 활용 여부 평가
* LLM Hallucination 방지 및 Fact Grounding 검증
* Tool Routing 및 Domain Classification 정확도 검증
* Absence Check / Negative Check / Correlation 기반 Trap Case 검증
* Golden Dataset 기반 Regression Test로 리팩토링 영향도 추적

## 🧠 Supported Local Models
* gemma3:12b
* qwen2.5-coder:7b
* gemma4:26b
* gemma4:12b
* gemma3:4b


## 🔄 분석 파이프라인 흐름 (Analysis Pipeline)

```text
Android Dumpstate / Modem Log
            │
            ▼
       Parser Layer
            │
            ▼
 Analysis Bucket Builder
(Pre-filter / Context Window)
            │
            ▼
    Structured Events
            │
            ▼
         RCA Layer
(Binder / Telephony /
 Internet Stall / OOS)
            │
            ▼
     Chroma Vector DB
            │
            ▼
 Retrieval + Routing
(Semantic / Hybrid)
            │
            ▼
   Answer Guardrails
(Absence Check /
 Correlation /
 Negative Check /
 Thread Exhaustion)
            │
            ▼
            LLM
            │
            ▼
     Final RCA Answer
```

> 본 프로젝트는 단순히 로그를 벡터 DB에 저장하는 일반적인 RAG 구조가 아니라 **Parser → Analysis Bucket → Structured Event → RCA → Retrieval → Guardrail → LLM** 파이프라인을 사용하여 Hallucination을 최소화하고 Fact 기반 Root Cause Analysis 정확도를 높입니다.

## 🏗️ 시스템 아키텍처 및 폴더 구조 (Architecture & Structure)

```text
📦 AI_Project
 ┣ 📂 core/
 ┃ ┣ 📜 config.py
 ┃ ┣ 📜 constants.py
 ┃ ┗ 📜 telephony_constants.py
 ┣ 📂 parsers/
 ┃ ┣ 📜 base.py
 ┃ ┣ 📜 analysis_bucket_builder.py
 ┃ ┣ 📜 diagnostic_parser.py
 ┃ ┣ 📜 telephony_parser.py
 ┃ ┣ 📜 rilj_parser.py
 ┃ ┣ 📜 internet_stall_parser.py
 ┃ ┣ 📜 native_crash_parser.py
 ┃ ┣ 📜 battery_thermal_analyzer.py
 ┃ ┣ 📜 data_call_processor.py
 ┃ ┣ 📜 ims_sip_processor.py
 ┃ ┣ 📜 network_ts_analyzer.py
 ┃ ┣ 📜 ntn_processor.py
 ┃ ┣ 📜 sat_at_parser.py
 ┃ ┗ 📜 system_property_parser.py
 ┣ 📂 app/
 ┃ ┣ 📜 helpers.py
 ┃ ┣ 📜 pipeline.py
 ┃ ┣ 📜 sidebar.py
 ┃ ┣ 📜 chat_panel.py
 ┃ ┗ 📂 tabs/
 ┃   ┣ 📜 chat_tab.py
 ┃   ┣ 📜 dashboard_tab.py
 ┃   ┣ 📜 boot_tab.py
 ┃   ┣ 📜 satellite_tab.py
 ┃   ┣ 📜 internet_tab.py
 ┃   ┗ 📜 benchmark_tab.py
 ┣ 📂 rag/
 ┃ ┣ 📜 chroma_utils.py
 ┃ ┣ 📜 ingest.py
 ┃ ┣ 📜 llm_client.py
 ┃ ┣ 📜 prompt_builder.py
 ┃ ┣ 📜 retrieval.py
 ┃ ┣ 📜 routing.py
 ┃ ┣ 📜 query_classifiers.py
 ┃ ┗ 📜 answer_guardrails.py
 ┣ 📂 rag_builders/
 ┃ ┣ 📜 builder.py
 ┃ ┣ 📜 common.py
 ┃ ┣ 📜 telephony_builder.py
 ┃ ┣ 📜 network_builder.py
 ┃ ┣ 📜 crash_builder.py
 ┃ ┣ 📜 battery_builder.py
 ┃ ┣ 📜 binder_builder.py
 ┃ ┗ 📜 device_builder.py
 ┣ 📂 agent_toolkit/
 ┃ ┣ 📜 __init__.py
 ┃ ┣ 📜 common.py
 ┃ ┣ 📜 correlation.py
 ┃ ┣ 📜 call_tools.py
 ┃ ┣ 📜 network_tools.py
 ┃ ┣ 📜 crash_tools.py
 ┃ ┣ 📜 battery_tools.py
 ┃ ┣ 📜 binder_tools.py
 ┃ ┣ 📜 satellite_tools.py
 ┃ ┗ 📜 kpi_tools.py
 ┣ 📂 rca/
 ┃ ┣ 📜 __init__.py
 ┃ ┣ 📜 structured_event_renderer.py
 ┃ ┣ 📜 binder_rca.py
 ┃ ┣ 📜 internet_stall_rca.py
 ┃ ┗ 📜 telephony_rca.py
 ┣ 📂 ui/
 ┃ ┣ 📜 common.py
 ┃ ┣ 📜 crash_ui.py
 ┃ ┣ 📜 network_ui.py
 ┃ ┣ 📜 power_ui.py
 ┃ ┣ 📜 satellite_ui.py
 ┃ ┗ 📜 telephony_ui.py
 ┣ 📂 tools/
 ┃ ┣ 📜 eval_logger.py
 ┃ ┣ 📜 compare_routing_scores.py
 ┃ ┗ 📜 make_ppt.py
 ┣ 📂 tests/
 ┃ ┣ 📜 test_semantic_routing.py
 ┃ ┣ 📜 test_semantic_routing_fuzzy.py
 ┃ ┗ 📜 routing_score_logger.py
 ┣ 📂 scripts/
 ┃ ┣ 📜 benchmark_models.py
 ┃ ┗ 📜 bechmark_models.md
 ┣ 📜 web_app.py
 ┣ 📜 log_orchestrator.py
 ┣ 📜 prepare_rag_payload.py
 ┣ 📜 ril_rag_chat.py
 ┣ 📜 agent_tools.py
 ┣ 📜 benchmark_ui.py
 ┣ 📜 run_golden_eval.py
 ┣ 📜 run_rag_eval_csv.py
 ┣ 📜 ui_components.py
 ┣ 📜 config.yaml
 ┗ 📜 requirements.txt
```

> 위 구조는 소스 코드 중심으로 정리한 것이며, `.gitignore`에 포함된 로그/결과/임베딩/임시 산출물 디렉터리(`log/`, `payloads/`, `result/`, `temp_logs/`, `chroma_db/`, `.streamlit/`, `test_reports/`, `bge-m3-offline/`, `benchmark_results/`, `eval_logs/`, `log_for_evaluation/`, `debug_prompts/` 등)는 제외했습니다.
## 🔧 최근 리팩토링 (Recent Refactoring)

* Streamlit UI를 app/, tabs/ 구조로 분리하여 유지보수성 향상
* RAG Payload 생성 로직을 rag_builders/ 기반 Builder 패턴으로 재구성
* Fact 분석 로직을 agent_toolkit/ 기반 Domain Module 구조로 분리
* Analysis Bucket Builder 도입
  * 단일 스캔 기반 후보 로그 버킷 생성
  * Parser 반복 순회 최소화
  * Context Window 기반 로그 수집 구조 적용
* Binder Context 분리
  * UI 표시용 Binder Event와 RCA 분석용 Binder Context 분리
  * UI 상세 테이블 과다 출력 방지
* Binder RCA / Internet Stall RCA 레이어 추가
* Tool Registry 및 Intent Routing 구조 단순화
* 회귀 테스트 및 Golden Set 기반 품질 검증 체계 적용
* Retrieval Domain Boost 및 Query Classification 구조 추가
* Structured Event 기반 Answer Guardrail Layer 추가
* Binder Proxy Leak RCA 및 Correlation 분석 강화
* Golden Evaluation Trap Case 대응 로직 개선
* Crash/ANR Absence Check 및 Negative Check 응답 품질 개선

## 🚀 설치 및 실행 방법

1. **저장소 클론 및 폴더 진입**
   ```bash
   git clone [https://github.com/your-username/Android-RIL-RAG-Dashboard.git](https://github.com/your-username/Android-RIL-RAG-Dashboard.git)
   cd Android-RIL-RAG-Dashboard

2. **가상환경 생성 및 의존성 패키지 설치**
   ```bash
    python -m venv venv
    source venv/bin/activate  # Mac/Linux (Windows: venv\Scripts\activate)
    pip install -r requirements.txt

3. **로컬 AI모델 다운로드 및 실행**
   ```bash
    ollama run gemma4:e2b

4. **대시보드 앱 구동**
   ```bash
    streamlit run web_app.py
