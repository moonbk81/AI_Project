
"""Prompt construction helpers for RAG."""

FINAL_ANSWER_STYLE = """=== [최종 답변 작성 규칙] ===
- 최종 답변은 PLM 코멘트에 그대로 등록해도 어색하지 않은 개발자 코멘트체로 작성한다.
- 내부 라우팅명, intent 이름, 규칙명, 템플릿명은 사용자에게 노출하지 않는다. 예: Call_Analysis, Call_Drop_Trap, Time_Context_Inference, Data_Call_Setup, Fallback_General, "Intent", "라우팅", "분류됨", "전용 출력 템플릿".
- log_type 이름은 원문 필드 근거가 필요할 때만 괄호 없이 자연어로 풀어 쓴다. 예: Call_Session 대신 "통화 세션", Binder_Warning 대신 "Binder 경고".
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
