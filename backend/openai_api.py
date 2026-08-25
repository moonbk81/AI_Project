"""OpenAI-compatible chat endpoint.

Lets a chat client that speaks the OpenAI API — Open WebUI in particular — use
this project's RAG as if it were a model. It is a thin translation layer over
`engine.ask()`: nothing about retrieval or prompting lives here.

Which log the question is about is carried by the model id, so the model picker
doubles as the file picker:

    ril-rag                 전체 적재 로그
    ril-rag:radio.log       그 파일만

Note that, like the rest of this API, the endpoint is unauthenticated: any
`Authorization` header the client sends is accepted and ignored.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterator, List, Optional
import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1", tags=["openai"])

MODEL_ID = "ril-rag"
# "ril-rag:<file>" pins the answer to one ingested log.
FILE_SEPARATOR = ":"

_OWNER = "ril-rag"


class ChatMessage(BaseModel):
    role: str = "user"
    # Chat clients may send content as a list of typed parts rather than text.
    content: Any = ""


class ChatCompletionRequest(BaseModel):
    model: str = MODEL_ID
    messages: List[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    # Accepted and ignored: sampling is decided by this project's model config.
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


def target_file(model_id: str) -> Optional[str]:
    """The ingested log a model id points at, or None for "all of them"."""
    if not model_id or FILE_SEPARATOR not in model_id:
        return None
    _, _, file_name = model_id.partition(FILE_SEPARATOR)
    return file_name.strip() or None


def message_text(content: Any) -> str:
    """Flatten OpenAI message content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        ]
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def split_conversation(messages: List[ChatMessage]) -> tuple:
    """Return (question, history) the way `engine.ask()` wants them.

    The last user message is the question; everything before it is history.
    A system message is dropped: this project builds its own prompt.
    """
    turns = [
        {"role": message.role, "content": message_text(message.content)}
        for message in messages
        if message.role in ("user", "assistant")
    ]

    for index in range(len(turns) - 1, -1, -1):
        if turns[index]["role"] == "user":
            return turns[index]["content"], turns[:index]

    return "", turns


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


def _answer_text(answer: str, thinking: str) -> str:
    """Prepend the reasoning as a <think> block, which chat clients fold away."""
    if thinking:
        return f"<think>\n{thinking.strip()}\n</think>\n\n{answer}"
    return answer


def _stream_chunks(completion_id: str, model: str, created: int, text: str) -> Iterator[str]:
    """SSE chunks for `stream: true`.

    `engine.ask()` answers in one go, so the whole reply arrives as a single
    delta rather than token by token.
    """
    def chunk(delta: Dict[str, Any], finish_reason: Optional[str]) -> str:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield chunk({"role": "assistant"}, None)
    yield chunk({"content": text}, None)
    yield chunk({}, "stop")
    yield "data: [DONE]\n\n"


@router.get("/models")
def list_models() -> Dict[str, Any]:
    """One model for the whole database, plus one per ingested log file."""
    from backend.main import get_engine

    created = int(time.time())
    models = [{"id": MODEL_ID, "object": "model", "created": created, "owned_by": _OWNER}]

    try:
        files = get_engine().get_all_files() or []
    except Exception:
        files = []  # an empty database must not break the model list

    models.extend(
        {
            "id": f"{MODEL_ID}{FILE_SEPARATOR}{file_name}",
            "object": "model",
            "created": created,
            "owned_by": _OWNER,
        }
        for file_name in files
    )

    return {"object": "list", "data": models}


@router.post("/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    from backend.main import get_engine

    question, history = split_conversation(req.messages)
    answer, _ids, _metas, thinking = get_engine().ask(
        question,
        current_file=target_file(req.model),
        chat_history=history or None,
    )
    text = _answer_text(answer, thinking or "")

    completion_id = _completion_id()
    created = int(time.time())

    if req.stream:
        return StreamingResponse(
            _stream_chunks(completion_id, req.model, created, text),
            media_type="text/event-stream",
        )

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        # Token counts are not tracked; the fields exist because clients read them.
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
