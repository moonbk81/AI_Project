# rag/prompt_template.py

def get_domain_guidelines(query_lower: str, log_guidelines: dict, prompts: dict) -> list:
    guidelines = []

    # [Call Drop 판정]
    if any(k in query_lower for k in ["call drop", "드랍", "통화 끊김", "통화 장애", "끊김"]):
        rule = log_guidelines.get('Call_Drop_Rule') or prompts.get('Call_Drop_Rule', "")
        if rule: guidelines.append(f"### [Call Drop 판정 최우선 규칙]\n{rule}")

    # [위성 통신 및 기본 페르소나]
    if any(k in query_lower for k in ["spacex", "starlink", "ntn", "스페이스엑스"]):
        rule = prompts.get('SpaceX', "")
        if rule: guidelines.append(f"### [위성 통신 규칙 - SpaceX]\n{rule}")
    elif any(k in query_lower for k in ["tiantong", "티엔통", "천통", "at command"]):
        rule = prompts.get('Tiantong', "")
        if rule: guidelines.append(f"### [위성 통신 규칙 - Tiantong]\n{rule}")
    else:
        base_p = prompts.get('base_persona', "")
        if base_p: guidelines.append(f"### [기본 분석 원칙]\n{base_p}")

    # [통화 유형별]
    if any(k in query_lower for k in ["cs call", "cs 통화"]):
        guidelines.append("### [CS Call 전용 분석]\nCS 통화의 거절 사유(Release Cause)를 우선적으로 찾아 요약하십시오.")
    elif any(k in query_lower for k in ["ps call", "volte", "ims"]):
        guidelines.append("### [PS(VoLTE) Call 전용 분석]\n비정상 종료 발생 시 시간과 에러 코드만 추출하십시오.")

    return guidelines

def format_system_wtf_stats(wtf_stats: dict) -> str:
    """SYSTEM_WTF 통계 데이터를 LLM용 프롬프트 텍스트로 변환"""
    if not wtf_stats: return ""

    lines = ["### [시스템 장애 통계]"]
    for proc, info in wtf_stats.items():
        lines.append(f"- 프로세스 '{proc}': 총 {info['count']}회 발생 (최초: {info['first_time']}, 최후: {info['last_time']})")

    lines.append("(※ 이 통계가 질문의 '발생 횟수'에 대한 정답 팩트입니다.)")
    return "\n".join(lines)

def format_structured_analysis(structured_answer: str) -> str:
    """사전 분석 결과를 LLM 프롬프트에 주입하기 위한 포맷팅"""
    if not structured_answer:
        return ""

    return (
        f"🚨 [시스템 사전 분석 결론 - 최우선 반영할 것]:\n"
        f"{structured_answer}\n"
        f"(※ 위 사전 분석 결과를 바탕으로, 사용자가 요청한 출력 양식에 맞게 렌더링하십시오.)"
    )
