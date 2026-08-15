"""Provider-neutral chat helpers for Ollama and OpenAI-compatible vLLM."""

from __future__ import annotations

import os
import json
from typing import Any, Dict, List, Optional


DEFAULT_VLLM_BASE_URL = "http://localhost:8000/v1"
DEFAULT_VLLM_MODEL = "Qwen/Qwen2.5-72B-Instruct"


def get_llm_provider() -> str:
    """Return the active LLM provider name."""
    return os.getenv("RAG_LLM_PROVIDER", "ollama").strip().lower()


def get_default_llm_model(fallback_model: str) -> str:
    """Allow deployment environments to override the Streamlit default model."""
    return os.getenv("RAG_LLM_MODEL", "").strip() or fallback_model


def get_llm_runtime_label(model_name: Optional[str] = None) -> str:
    """Human-readable runtime label for logs/UI."""
    provider = get_llm_provider()
    if provider == "vllm":
        base_url = os.getenv("RAG_LLM_BASE_URL", DEFAULT_VLLM_BASE_URL)
        model = model_name or os.getenv("RAG_LLM_MODEL", DEFAULT_VLLM_MODEL)
        return f"vLLM/OpenAI-compatible - {model} @ {base_url}"
    return f"Local Ollama - {model_name or os.getenv('RAG_LLM_MODEL', 'default')}"


def _vllm_chat(
    model: str,
    messages: List[Dict[str, str]],
    options: Optional[Dict[str, Any]] = None,
    response_format: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    import requests

    base_url = os.getenv("RAG_LLM_BASE_URL", DEFAULT_VLLM_BASE_URL).rstrip("/")
    api_key = os.getenv("RAG_LLM_API_KEY", "EMPTY")
    timeout = float(os.getenv("RAG_LLM_TIMEOUT", "300"))
    model = model or os.getenv("RAG_LLM_MODEL", DEFAULT_VLLM_MODEL)
    options = options or {}

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": float(options.get("temperature", 0.1)),
        "max_tokens": int(options.get("max_tokens", options.get("num_predict", 2048))),
        "stream": False,
    }

    for src, dst in (
        ("top_p", "top_p"),
        ("stop", "stop"),
        ("frequency_penalty", "frequency_penalty"),
        ("presence_penalty", "presence_penalty"),
        ("repeat_penalty", "repetition_penalty"),
    ):
        if src in options and options[src] is not None:
            payload[dst] = options[src]

    if response_format:
        payload["response_format"] = response_format

    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    message = data.get("choices", [{}])[0].get("message", {})
    content = message.get("content") or ""
    reasoning = (
        message.get("reasoning_content")
        or message.get("reasoning")
        or message.get("tool_calls", "")
    )
    if not isinstance(reasoning, str):
        reasoning = json.dumps(reasoning, ensure_ascii=False)
    return {"message": {"content": content, "reasoning": reasoning}}


def chat(
    model: str,
    messages: List[Dict[str, str]],
    options: Optional[Dict[str, Any]] = None,
    response_format: Optional[Dict[str, str]] = None,
    think: bool = False,
) -> Dict[str, Any]:
    """Call the configured LLM provider using an Ollama-like response shape."""
    provider = get_llm_provider()
    if provider == "vllm":
        return _vllm_chat(
            model=model,
            messages=messages,
            options=options,
            response_format=response_format,
        )
    if provider != "ollama":
        raise ValueError(f"Unsupported RAG_LLM_PROVIDER: {provider}")
    import ollama

    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "options": options,
    }
    if response_format:
        kwargs["format"] = "json"
    if think:
        kwargs["think"] = think
    return ollama.chat(**kwargs)
