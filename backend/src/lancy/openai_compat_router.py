"""
OpenAI-compatible /v1/chat/completions endpoint.

Compatible with Open WebUI, LibreChat, AnythingLLM and any other client
that speaks the OpenAI chat API.

Endpoints:
    POST /v1/chat/completions  — chat with RAG context (stream or non-stream)
    GET  /v1/models            — list available models

Streaming: stream=true returns Server-Sent Events (SSE) in OpenAI chunk format.
Note: the LLM call is not truly streamed at the token level; the full response
is generated first and then forwarded as a single SSE chunk (plus stop chunk).
This is transparent to clients.
"""

import json
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from conversational_toolkit.agents.base import QueryWithContext
from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles


# ─── Request / response models ────────────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str = Field(..., max_length=50)
    content: str = Field(..., max_length=32_000)


# The model id meaning "whichever KB is currently active", for clients that do
# not care which one answers.
ACTIVE_KB_MODEL = "rag-assistant"


class ChatCompletionRequest(BaseModel):
    model: str = Field(ACTIVE_KB_MODEL, max_length=200)
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=200)
    stream: bool = False
    # `temperature` and `max_tokens` are deliberately absent: generation settings
    # belong to the KB's own configuration, not to the caller. Pydantic ignores
    # unknown fields, so clients that always send them still work — they just no
    # longer look as though they were honoured.


# ─── Router factory ───────────────────────────────────────────────────────────


def create_openai_compat_router(agent) -> APIRouter:
    """
    Args:
        agent: A DispatchingAgent. Needs `await answer(query, kb_id=None)`
               returning an AgentAnswer with .content and .sources, and
               `loaded_kbs()` listing the KBs a caller may address.
    """
    router = APIRouter(prefix="/v1")

    @router.post("/chat/completions")
    async def chat_completions(req: ChatCompletionRequest):
        # ── Build history + extract last user query ────────────────────────
        history: list[LLMMessage] = []
        query = ""

        for msg in req.messages:
            if msg.role == "user":
                # Every user turn goes into history; last one becomes the query
                history.append(
                    LLMMessage(
                        role=Roles.USER,
                        content=[MessageContent(type="text", text=msg.content)],
                    )
                )
                query = msg.content
            elif msg.role == "assistant":
                history.append(
                    LLMMessage(
                        role=Roles.ASSISTANT,
                        content=[MessageContent(type="text", text=msg.content)],
                    )
                )
            # system messages: ignored — agent uses its own system prompt

        if not query:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "No user message found in messages.",
                        "type": "invalid_request_error",
                        "param": "messages",
                    }
                },
            )

        # Drop the last user message from history (it becomes the query)
        if history and history[-1].role == Roles.USER:
            history = history[:-1]

        # ── Resolve the target KB ─────────────────────────────────────────
        # Only KBs already in the pool are addressable. Loading one on demand
        # could evict the pool on an embedding mismatch, so an API caller must
        # not be able to reach a KB the UI has not loaded.
        kb_id = None if req.model == ACTIVE_KB_MODEL else req.model
        if kb_id is not None and kb_id not in {kb.id for kb in agent.loaded_kbs()}:
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "message": (
                            f"Model '{req.model}' is not available. "
                            f"Call GET /v1/models for the loaded knowledge bases."
                        ),
                        "type": "invalid_request_error",
                        "param": "model",
                        "code": "model_not_found",
                    }
                },
            )

        # ── RAG call ──────────────────────────────────────────────────────
        answer = await agent.answer(
            QueryWithContext(query=query, history=history), kb_id=kb_id
        )
        content = answer.content[0].text if answer.content else ""

        # Append source references
        if answer.sources:
            sources_lines = ["\n\n---\n**Sources:**"]
            seen = set()
            for src in answer.sources:
                label = f"{src.title} ({src.metadata.get('source_file', '?')})"
                if label not in seen:
                    sources_lines.append(f"- {label}")
                    seen.add(label)
            content += "\n".join(sources_lines)

        msg_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        # ── Streaming response ─────────────────────────────────────────────
        if req.stream:

            async def event_stream() -> AsyncIterator[bytes]:
                # Single content chunk
                chunk = {
                    "id": msg_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": req.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": content},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()

                # Stop chunk
                stop_chunk = {
                    "id": msg_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": req.model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(stop_chunk)}\n\n".encode()
                yield b"data: [DONE]\n\n"

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        # ── Non-streaming response ─────────────────────────────────────────
        return {
            "id": msg_id,
            "object": "chat.completion",
            "created": created,
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    @router.get("/models")
    async def list_models():
        """OpenAI-compatible model list (required by most clients).

        One entry per loaded KB, so a client can pick which one answers, plus
        the alias for "whichever is active". KBs that are not loaded are
        deliberately absent — they are not addressable.
        """
        created = int(time.time())
        models = [
            {
                "id": ACTIVE_KB_MODEL,
                "object": "model",
                "created": created,
                "owned_by": "lancy",
            }
        ]
        models += [
            {
                "id": kb.id,
                "object": "model",
                "created": created,
                "owned_by": "lancy",
                "name": kb.name,
            }
            for kb in agent.loaded_kbs()
        ]
        return {"object": "list", "data": models}

    return router
