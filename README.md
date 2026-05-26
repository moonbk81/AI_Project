# 📡 Android RIL RAG Dashboard (통신 로그 AI 분석 파이프라인)

## 📖 개요 (Overview)
Android RIL(Radio Interface Layer) 및 Telephony 시스템 로그를 원클릭으로 파싱하고, 대형 언어 모델(LLM)과 RAG(Retrieval-Augmented Generation) 기술을 활용해 통신 장애 원인을 수석 엔지니어 수준으로 분석해 주는 자동화 대시보드 시스템입니다.

## ✨ 주요 기능 (Key Features)
* **LLM 기반 RAG 챗봇 (`ril_rag_chat.py`)**: 의미 기반(Semantic) 및 하이브리드 라우팅을 통해 사용자 질문의 의도를 파악하고, Vector DB(Chroma)에서 연관 로그를 검색하여 정확한 원인 분석 리포트를 제공합니다.
* **통합 로그 오케스트레이션 (`log_orchestrator.py`)**: 방대한 단말 로그를 효율적으로 스플릿하고, CS Call(통화 절단), 망 이탈(OOS), Crash/ANR, Binder 지연 등 핵심 이벤트의 시간적 상관관계를 분석하는 파이프라인입니다.
* **대화형 시각화 대시보드 (`ui_components.py`)**: Streamlit과 Plotly를 활용하여 데이터 사용량, 무선 신호(RSRP) 타임라인, 발열 및 배터리 드레인, CPU 점유율 등을 직관적인 차트로 렌더링합니다.
* **Fact 기반 팩트 추출 (`agent_tools.py`)**: LLM의 환각(Hallucination) 현상을 막기 위해, 파싱된 로그에서 100% 확실한 시스템 KPI(Health Indicator)와 장애 팩트만 추출하여 AI의 프롬프트에 강제 주입합니다.

## 🏗️ 시스템 아키텍처 및 폴더 구조 (Architecture & Structure)

프로젝트는 크게 **1) 데이터 파이프라인**, **2) RAG/AI 엔진**, **3) UI 대시보드** 세 가지 계층으로 분리되어 있습니다.

```text
📦 Android-RIL-RAG-Analyzer
 ┣ 📂 core/                # 시스템 설정 및 프롬프트 (config.yaml 등)
 ┣ 📂 parsers/             # 도메인별 로그 파서 모음 (Telephony, Binder, Crash 등)
 ┣ 📂 payloads/            # Vector DB에 적재될 Chunking 및 메타데이터 JSON
 ┣ 📂 result/              # 파싱이 완료된 중간 결과물 JSON (UI 렌더링용)
 ┣ 📜 web_app.py           # Streamlit 메인 실행 파일 (Entry Point)
 ┣ 📜 ui_components.py     # 대시보드 차트 및 UI 렌더링 모듈
 ┣ 📜 log_orchestrator.py  # 로그 분배 및 파이프라인 병합 컨트롤러
 ┣ 📜 prepare_rag_payload.py # RAG 임베딩을 위한 Document/Metadata 규격화 포장
 ┣ 📜 ril_rag_chat.py      # LLM 통신, Vector DB 검색 및 인텐트 라우팅
 ┗ 📜 agent_tools.py       # 분석 팩트 요약 및 단말 상태(KPI) 도출 헬퍼
