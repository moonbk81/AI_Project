# 📡 Android RIL RAG Dashboard (통신 로그 AI 분석 파이프라인)

## 📖 개요 (Overview)
Android RIL(Radio Interface Layer) 및 Telephony 시스템 로그를 원클릭으로 파싱하고, 대형 언어 모델(LLM)과 RAG(Retrieval-Augmented Generation) 기술을 활용해 통신 장애 원인을 수석 엔지니어 수준으로 분석해 주는 자동화 대시보드 시스템입니다.

## ✨ 주요 기능 (Key Features)
* **LLM 기반 RAG 챗봇 (`ril_rag_chat.py`)**: Semantic / Hybrid Routing 기반으로 사용자 질의를 분석하고 Vector DB에서 관련 로그를 검색하여 장애 원인 및 RCA(Root Cause Analysis)를 수행합니다.
* **통합 로그 오케스트레이션 (`log_orchestrator.py`)**: 대용량 Android 로그를 분류·정규화하고 Call, OOS, Crash, ANR, Binder, Internet Stall 등 주요 이벤트를 추출합니다.
* **Fact 기반 분석 (`agent_tools.py`)**: LLM 환각(Hallucination)을 최소화하기 위해 실제 로그 기반 KPI와 장애 팩트를 강제 주입합니다.
* **RCA(Event) 분석 레이어**: Binder, Internet Stall, Telephony 이벤트에 대해 원인 후보를 구조적으로 분석하고 AI 응답 품질을 향상시킵니다.
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

## 🏗️ 시스템 아키텍처 및 폴더 구조 (Architecture & Structure)

```text
📦 Android-RIL-RAG-Analyzer
 ┣ 📂 core/
 ┃ ┗ 시스템 설정 및 프롬프트
 ┣ 📂 parsers/
 ┃ ┗ Telephony, Binder, Crash, ANR, Internet Stall 등 도메인별 파서
 ┣ 📂 app/
 ┃ ┣ 📜 helpers.py          # 공통 유틸리티
 ┃ ┣ 📜 pipeline.py         # 로그 분석 및 RAG 적재 파이프라인
 ┃ ┣ 📜 sidebar.py          # Streamlit Sidebar UI
 ┃ ┣ 📜 chat_panel.py       # 채팅 UI 렌더링
 ┃ ┗ 📂 tabs/
 ┃   ┣ 📜 chat_tab.py
 ┃   ┣ 📜 dashboard_tab.py
 ┃   ┣ 📜 boot_tab.py
 ┃   ┣ 📜 satellite_tab.py
 ┃   ┣ 📜 internet_tab.py
 ┃   ┗ 📜 benchmark_tab.py
 ┣ 📂 payloads/
 ┃ ┗ Vector DB 적재용 Payload
 ┣ 📂 result/
 ┃ ┗ Parser 결과 JSON
 ┣ 📜 web_app.py            # Streamlit Entry Point
 ┣ 📜 log_orchestrator.py   # 전체 로그 분석 컨트롤러
 ┣ 📜 prepare_rag_payload.py # RAG 문서 생성기
 ┣ 📜 ril_rag_chat.py       # Intent Routing 및 AI 분석 엔진
 ┣ 📜 agent_tools.py        # KPI 추출 및 Fact 분석 도구
 ┗ 📜 ui_components.py      # Dashboard Widget 및 Visualization Library
```

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
