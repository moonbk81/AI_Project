
"""Prompt construction helpers for RAG."""

def build_rag_prompt(
    system_role_prompt: str,
    domain_guidelines: str,
    tool_facts: str,
    formatted_logs: str,
    user_query: str,
) -> str:
    return (
        f"{system_role_prompt}\n\n"
        f"{domain_guidelines}\n\n"
        f"=== [분석 팩트 모음] ===\n{tool_facts}\n\n"
        f"=== [검색된 관련 로그] ===\n{formatted_logs}\n\n"
        f"사용자 질문: {user_query}"
    )
