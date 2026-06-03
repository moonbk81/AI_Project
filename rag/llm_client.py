
"""LLM client utilities for RAG answer generation."""

import re

import ollama

def call_llm(
    prompt: str,
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

    is_think = model_name.startswith("gemma4")

    try:
        res = ollama.chat(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            options=cfg,
            think=is_think,
        )

        raw_content = res["message"].get("content", "").strip()
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
