"""LLM client utilities for RAG answer generation."""

import re
import os
import ollama

def call_llm(
    system_prompt: str,  # 💡 단일 prompt가 아닌 system_prompt와 user_query로 분리
    user_query: str,     # 💡 사용자 질문 분리
    model_name: str,
    model_config_registry: dict,
    is_bench: bool = False,
) -> tuple[str, str]:
    """Call Ollama and split final answer from model reasoning/thinking text."""
    cfg = model_config_registry.get(
        model_name,
        model_config_registry.get("default")
    ).copy()

    if is_bench:
        cfg["num_ctx"] = 8192

    # Defensive guard 1: if num_predict is accidentally set too low
    if not is_bench:
        try:
            num_predict = int(cfg.get("num_predict", 0) or 0)
        except (TypeError, ValueError):
            num_predict = 0
        if 0 < num_predict < 256:
            cfg["num_predict"] = 1024

    # 💡 Defensive guard 2: Gemma 모델의 반복 페널티(repeat_penalty) 안전장치
    # Gemma 계열은 repeat_penalty가 1.05를 넘어가면 1글자만 뱉고 멈추는 현상이 잦으므로 강제 보정
    if "gemma" in model_name.lower():
        try:
            current_penalty = float(cfg.get("repeat_penalty", 1.0))
            if current_penalty > 1.05:
                if os.getenv("RAG_DEBUG_PROMPT") == "1":
                    print(f"[LLM_DEBUG] Gemma 모델 보호: repeat_penalty를 {current_penalty}에서 1.0으로 강제 하향합니다.")
                cfg["repeat_penalty"] = 1.0
        except (TypeError, ValueError):
            pass

    is_think = model_name.startswith("gemma4")

    try:
        if os.getenv("RAG_DEBUG_PROMPT") == "1":
            print(f"[LLM_DEBUG] model={model_name} options={cfg}")

        # 💡 [핵심 수정] System 룰과 User 질문을 명확히 분리하여 Role 전달
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ]

        res = ollama.chat(
            model=model_name,
            messages=messages,
            options=cfg,
            think=is_think,
        )

        raw_content = res["message"].get("content", "").strip()
        if os.getenv("RAG_DEBUG_PROMPT") == "1":
            print("[LLM_DEBUG] raw_content repr:", repr(raw_content))
            print("[LLM_DEBUG] raw_content len:", len(raw_content))

        thinking = res["message"].get("reasoning", "")
        clean_content = raw_content

        if not thinking:
            think_match = re.search(
                r"<think>(.*?)</think>",
                raw_content,
                flags=re.DOTALL | re.IGNORECASE,
            )
            if think_match:
                thinking = think_match.group(1).strip()
                clean_content = re.sub(
                    r"<think>.*?</think>",
                    "",
                    raw_content,
                    flags=re.DOTALL | re.IGNORECASE,
                ).strip()
            else:
                channel_match = re.search(
                    r"<\|channel>thought(.*?)(<channel\|>|</|\|>|$)",
                    raw_content,
                    flags=re.DOTALL,
                )
                if channel_match:
                    thinking = channel_match.group(1).strip()
                    clean_content = re.sub(
                        r"<\|channel>thought.*?<channel\|>",
                        "",
                        raw_content,
                        flags=re.DOTALL,
                    ).strip()

        if clean_content.startswith("<unused"):
            clean_content = "분석 결과 생성 중 모델이 일찍 종료되었습니다. (Context 가 부족할 수 있습니다.)"

        if not clean_content and thinking:
            clean_content = "분석 과정(Thinking)은 완료되었으나, 최종 답변이 비어있습니다. AI의 생각 과정을 참고해주세요."

        return clean_content, thinking

    except Exception as e:
        return f"LLM 추론 중 에러가 발생했습니다: {str(e)}", ""