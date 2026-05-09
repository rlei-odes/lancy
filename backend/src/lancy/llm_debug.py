"""
LLM debug shim — wraps any LLM and logs full prompts and responses to disk.

The enabled flag is in-memory only and resets to False on restart.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path

from conversational_toolkit.llms.base import LLM, LLMMessage

log = logging.getLogger("uvicorn")

_enabled: bool = False
_log_path: Path | None = None
_MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MB


def configure(log_path: Path) -> None:
    global _log_path
    _log_path = log_path


def get_log_path() -> Path | None:
    return _log_path


def is_enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> None:
    global _enabled
    _enabled = value
    log.info(f"LLM debug mode {'enabled' if value else 'disabled'}")


def _fmt_conversation(conversation: list[LLMMessage]) -> str:
    parts = []
    for msg in conversation:
        text = " ".join(c.text or "" for c in msg.content if c.text)
        parts.append(f"[{msg.role.upper()}]\n{text}")
    return "\n\n".join(parts)


def _maybe_rotate() -> None:
    if _log_path is None or not _log_path.exists():
        return
    if _log_path.stat().st_size >= _MAX_LOG_BYTES:
        bak = _log_path.with_suffix(".log.bak")
        _log_path.rename(bak)
        log.info(f"LLM debug log rotated to {bak}")


def _write_entry(conversation: list[LLMMessage], response_text: str) -> None:
    if _log_path is None:
        return
    try:
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        _maybe_rotate()
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sep = "=" * 72
        entry = (
            f"\n{sep}\n"
            f"[{ts}]\n"
            f"{sep}\n"
            f"--- PROMPT ---\n{_fmt_conversation(conversation)}\n\n"
            f"--- RESPONSE ---\n{response_text}\n"
        )
        with _log_path.open("a") as f:
            f.write(entry)
    except Exception as exc:
        log.warning(f"LLM debug log write failed: {exc}")


class DebugLLM(LLM):
    """Transparent wrapper that logs prompts and responses when debug mode is enabled."""

    def __init__(self, inner: LLM) -> None:
        super().__init__()
        self._inner = inner
        self.tools = inner.tools

    async def generate(self, conversation: list[LLMMessage]) -> LLMMessage:
        result = await self._inner.generate(conversation)
        if _enabled:
            text = " ".join(c.text or "" for c in result.content if c.text)
            _write_entry(conversation, text)
        return result

    async def generate_stream(self, conversation: list[LLMMessage]) -> AsyncGenerator[LLMMessage, None]:
        accumulated: list[str] = []
        async for chunk in self._inner.generate_stream(conversation):
            if _enabled:
                for c in chunk.content:
                    if c.text:
                        accumulated.append(c.text)
            yield chunk
        if _enabled:
            _write_entry(conversation, "".join(accumulated))
