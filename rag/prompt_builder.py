
"""Prompt construction helpers for RAG."""

FINAL_ANSWER_STYLE = """=== [최종 답변 작성 규칙] ===
- 최종 답변은 PLM 코멘트에 그대로 등록해도 어색하지 않은 개발자 코멘트체로 작성한다.
- 이 시스템의 내부 이름은 답변에 쓰지 않는다. 읽는 사람은 이 저장소를 모른다.
  - 라우팅/intent/규칙/템플릿 이름: Call_Analysis, Call_Drop_Trap, Time_Context_Inference, Data_Call_Setup, Fallback_General, "Intent", "라우팅", "분류됨", "전용 출력 템플릿"
  - 도구 함수 이름: get_crash_anr_analytics, get_binder_warning_analytics 처럼 get_..._analytics 형태 전부. 도구를 썼다는 사실 자체를 말하지 않는다.
  - 문서 분류(log_type) 이름: Call_Session, Binder_Warning, System_Kill_Wtf_Event, RCA_Event 등. 근거의 출처를 밝힐 때는 "통화 세션 기록", "바인더 경고", "시스템 강제 종료 기록"처럼 우리말로 쓴다.
- 반대로 단말이 남긴 문자열은 원문 그대로 인용한다. am_kill, THREAD_EXHAUSTION, Too many Binders sent to SYSTEM, SIP 403, 에러 코드, 명령 이름, 시각은 읽는 사람의 근거다.
- 답변 구조는 원칙적으로 3~5문장 또는 짧은 불릿으로 작성하고, 결론 → 근거 → 다음 확인/조치 순서로 쓴다.
- 로그에 없는 일반론, 모델/시스템 한계 설명, 프롬프트 규칙 설명은 쓰지 않는다."""

def build_rag_prompt(system_role_prompt, domain_guidelines, tool_facts, formatted_logs):
    # 사용자 질문을 제외한 '시스템 지시사항 + 데이터'만 묶어서 반환합니다.
    return (
        f"{system_role_prompt}\n\n"
        f"{domain_guidelines}\n\n"
        f"{FINAL_ANSWER_STYLE}\n\n"
        f"=== [분석 팩트 모음] ===\n{tool_facts}\n\n"
        f"=== [검색된 관련 로그] ===\n{formatted_logs}"
    )
