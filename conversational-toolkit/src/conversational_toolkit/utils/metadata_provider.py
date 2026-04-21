from collections.abc import Generator
from contextvars import ContextVar
from contextlib import contextmanager
from typing import Any

metadata_context: ContextVar[list[dict[str, Any]] | None] = ContextVar("metadata", default=None)


class MetadataProvider:
    @staticmethod
    @contextmanager
    def get_manager() -> Generator[None, Any, None]:
        metadata_context.set([])
        try:
            yield
        finally:
            metadata_context.set([])

    @staticmethod
    def add_metadata(metadata: dict[str, Any]) -> None:
        prev = metadata_context.get()
        if prev is None:
            prev = []
        prev.append(metadata)
        metadata_context.set(prev)

    @staticmethod
    def get_metadata() -> list[dict[str, Any]]:
        return metadata_context.get() or []
