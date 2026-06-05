
"""Prompt construction helpers for RAG."""

def build_rag_prompt(system_role_prompt, domain_guidelines, tool_facts, formatted_logs):
    # 사용자 질문을 제외한 '시스템 지시사항 + 데이터'만 묶어서 반환합니다.
    return (
        f"{system_role_prompt}\n\n"
        f"{domain_guidelines}\n\n"
        f"=== [분석 팩트 모음] ===\n{tool_facts}\n\n"
        f"=== [검색된 관련 로그] ===\n{formatted_logs}"
    )