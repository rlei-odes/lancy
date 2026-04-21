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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from conversational_toolkit.agents.base import QueryWithContext
from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles


# ─── Request / response models ────────────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "rag-assistant"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


# ─── Router factory ───────────────────────────────────────────────────────────


def create_openai_compat_router(agent) -> APIRouter:
    """
    Args:
        agent: A CustomRAG instance (or any Agent with an async `answer` method)
               whose `answer()` returns an AgentAnswer with .content and .sources.
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
            return {
                "error": {
                    "message": "No user message found in messages.",
                    "type": "invalid_request_error",
                }
            }

        # Drop the last user message from history (it becomes the query)
        if history and history[-1].role == Roles.USER:
            history = history[:-1]

        # ── RAG call ──────────────────────────────────────────────────────
        answer = await agent.answer(QueryWithContext(query=query, history=history))
        content = answer.content[0].text if answer.content else ""

        # Append source references
        if answer.sources:
            sources_lines = ["\n\n---\n**Quellen:**"]
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
        """OpenAI-compatible model list (required by most clients)."""
        return {
            "object": "list",
            "data": [
                {
                    "id": "rag-assistant",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "sdsc",
                }
            ],
        }

    return router
