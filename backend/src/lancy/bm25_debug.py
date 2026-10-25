"""
BM25 debug recorder — keeps the recent keyword searches for the admin BM25 Debug tab.

One entry per question: each query variant keyword search ran (the question,
expanded queries), the terms it was analysed into, the language detected for
it, and its top hits with their scores and matched terms.

In memory only and off by default, like LLM debug: the entries hold users'
questions, so nothing is kept unless an admin turns recording on, and a
restart turns it off and empties it.
"""

from __future__ import annotations

import contextvars
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("uvicorn")

MAX_SEARCHES = 50

_enabled: bool = False
_searches: deque[dict[str, Any]] = deque(maxlen=MAX_SEARCHES)

# The question being answered. Set by the agent before retrieval; the variant
# searches run in tasks that inherit it, which is how they find their entry.
_current: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("bm25_debug_search", default=None)


def is_enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> None:
    global _enabled
    _enabled = value
    if not value:
        _searches.clear()
    log.info(f"BM25 debug recording {'enabled' if value else 'disabled'}")


def begin(question: str) -> None:
    """Open the entry for a question; the keyword searches made for it are recorded into it."""
    if not _enabled:
        _current.set(None)
        return
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": question,
        "variants": [],
    }
    _searches.appendleft(entry)
    _current.set(entry)


def record(search: dict[str, Any]) -> None:
    """BM25Retriever search listener: add one keyword search to the current question's entry."""
    if not _enabled:
        return
    entry = _current.get()
    if entry is None:  # a search outside a question, e.g. started before recording was on
        return
    entry["variants"].append(search)


def searches() -> list[dict[str, Any]]:
    """The recorded questions, newest first."""
    return list(_searches)
