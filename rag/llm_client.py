"""LLM client utilities for RAG answer generation."""

import re
import os

from core.config import get_model_config
from rag.llm_provider import chat

# 대화를 잇는 데 실을 지난 메시지의 최대 개수.
#
# 로컬 LLM 이라 토큰 값을 걱정할 일은 없지만 컨텍스트 창은 유한하고, 그 앞에는
# 매번 새로 검색된 로그가 통째로 실린다. 오래된 대화가 로그를 밀어내면 정작
# 이번 질문의 근거가 잘린다.
MAX_HISTORY_MESSAGES = 10


def build_history(chat_history) -> list[dict]:
    """지난 대화를 모델이 받을 수 있는 모양으로 정리한다.

    chat template 은 대개 user 와 assistant 가 번갈아 나오고 user 로 시작하기를
    요구한다 -- Gemma 계열은 어긋나면 답변 대신 에러를 낸다. 화면의 기록은 그
    조건을 지키지 않을 수 있다: 답을 못 받은 질문(빈 assistant), PLM 에서 넘겨받아
    먼저 들어와 있던 질문, 오래된 쪽을 잘라 내면서 assistant 로 시작하게 된 꼬리.
    여기서 한 번 다듬어 두면 부르는 쪽이 그 사정을 몰라도 된다.
    """
    cleaned: list[dict] = []
    for message in chat_history or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        # 같은 역할이 잇달아 오면(사이의 답이 비어 있었다) 나중 것만 남긴다.
        if cleaned and cleaned[-1]["role"] == role:
            cleaned[-1] = {"role": role, "content": content}
            continue
        cleaned.append({"role": role, "content": content})

    cleaned = cleaned[-MAX_HISTORY_MESSAGES:]
    # 자르고 나면 assistant 로 시작할 수 있다. 이번 질문은 맨 뒤에 user 로 붙으므로
    # 기록은 user 로 시작해 assistant 로 끝나야 짝이 맞는다.
    while cleaned and cleaned[0]["role"] != "user":
        cleaned.pop(0)
    while cleaned and cleaned[-1]["role"] != "assistant":
        cleaned.pop()
    return cleaned


def call_llm(
    system_prompt: str,  # 💡 단일 prompt가 아닌 system_prompt와 user_query로 분리
    user_query: str,     # 💡 사용자 질문 분리
    model_name: str,
    model_config_registry: dict,
    is_bench: bool = False,
    chat_history=None,
) -> tuple[str, str]:
    """Call the configured LLM and split final answer from reasoning/thinking text."""
    cfg = get_model_config(model_name, model_config_registry).copy()

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
        #
        # 지난 대화가 그 사이에 들어간다. 이게 없으면 "방금 말한 그 MNR" 같은
        # 후속 질문에서 모델은 자기가 뭐라고 답했는지 모른 채 새로 검색된 로그만
        # 보고 답한다 -- 채팅처럼 보이지만 매번 첫 질문이었다.
        messages = [
            {"role": "system", "content": system_prompt},
            *build_history(chat_history),
            {"role": "user", "content": user_query}
        ]

        if os.getenv("RAG_DEBUG_PROMPT", "0") == "1":
            # 무엇이 나갔는지 -- 특히 지난 대화가 실렸는지 -- 는 답만 봐서는 모른다.
            print(f"[LLM_DEBUG] messages={[(m['role'], len(m['content'])) for m in messages]}")

        res = chat(
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
