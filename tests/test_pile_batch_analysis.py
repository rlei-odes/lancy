"""Intent: batch analysis reads a pile's own store, and only its owner's.

`document-stats` and `analyze-document` normally resolve a store through the KB
pool. A pile is never a pool resident — it has no retrievers, no BM25 index and
needs no embedding model, because this path reads chunk text by metadata and never
embeds anything. So both endpoints take a `pile_id` and resolve it through an
injected `pile_store_factory` instead.

The contract pinned down here:

  - `pile_id` reads the pile's store, never the active KB's
  - another session's pile is a 404, so the response cannot confirm it exists
  - `pile_id` and `kb_id` together are rejected rather than silently preferring one
  - with no factory wired the endpoints say so (503) instead of falling back to the
    KB, which would answer from the wrong corpus
"""

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lancy.rag_router import create_rag_router

ME = {"x-session-id": "session-aaa"}
THEM = {"x-session-id": "session-bbb"}


class FakeChunk:
    def __init__(self, content: str, source_file: str, mime_type: str = "text/plain"):
        self.id = f"c-{source_file}-{len(content)}"
        self.content = content
        self.title = source_file
        self.mime_type = mime_type
        self.metadata = {"source_file": source_file}


class FakeStore:
    """Only the two methods these endpoints reach."""

    def __init__(self, chunks: list[FakeChunk]):
        self.chunks = chunks
        self.disposed = False

    async def get_chunks_by_filter(self, filters: dict | None = None) -> list[FakeChunk]:
        if not filters:
            return list(self.chunks)
        out = []
        for c in self.chunks:
            ok = True
            for key, want in filters.items():
                have = c.metadata.get(key)
                ok = ok and (have in want if isinstance(want, list) else have == want)
            if ok:
                out.append(c)
        return out

    async def count(self) -> int:
        return len(self.chunks)


KB_STORE = FakeStore([FakeChunk("corpus text", "kb-doc.pdf")])
PILE_STORE = FakeStore(
    [
        FakeChunk("my first page", "mine.pdf"),
        FakeChunk("my second page", "mine.pdf"),
    ]
)


@pytest.fixture
def ctx(tmp_path):
    state: dict = {"opened": [], "wire_piles": True}

    @asynccontextmanager
    async def pile_store_factory(pile_id: str, owner_id: str | None):
        state["opened"].append((pile_id, owner_id))
        # Mirrors open_pile_store: the ownership check *is* the lookup, so a pile
        # that is not this owner's is indistinguishable from one that is absent.
        if pile_id == "p-mine" and owner_id == "session-aaa":
            yield PILE_STORE
        else:
            yield None

    def build(wire: bool) -> TestClient:
        app = FastAPI()
        app.include_router(
            create_rag_router(
                db_dir=tmp_path,
                vector_store_factory=lambda: KB_STORE,
                rebuild_callback=None,
                prompts_dir=tmp_path / "prompts",
                pile_store_factory=pile_store_factory if wire else None,
            )
        )
        return TestClient(app)

    state["build"] = build
    state["client"] = build(True)
    return state


def _stats(client, body, headers=ME):
    return client.post("/api/v1/rag/document-stats", json=body, headers=headers)


def _analyze(client, body, headers=ME):
    return client.post("/api/v1/rag/analyze-document", json=body, headers=headers)


SCHEMA = {"type": "object", "properties": {"question1": {"type": "string"}}}


# ─── document-stats ───────────────────────────────────────────────────────────


def test_stats_without_a_pile_read_the_active_kb(ctx):
    r = _stats(ctx["client"], {"source_files": ["kb-doc.pdf"]})
    assert r.status_code == 200
    assert r.json()["stats"][0]["chunk_count"] == 1
    assert ctx["opened"] == []  # the pile factory was never consulted


def test_stats_with_a_pile_read_the_piles_store(ctx):
    r = _stats(ctx["client"], {"source_files": ["mine.pdf"], "pile_id": "p-mine"})
    assert r.status_code == 200
    stat = r.json()["stats"][0]
    assert stat["chunk_count"] == 2
    assert stat["char_count"] == len("my first page") + len("my second page")
    assert ctx["opened"] == [("p-mine", "session-aaa")]


def test_a_kb_document_is_invisible_through_a_pile(ctx):
    """Proof the pile store replaced the KB store rather than supplementing it."""
    r = _stats(ctx["client"], {"source_files": ["kb-doc.pdf"], "pile_id": "p-mine"})
    assert r.status_code == 200
    assert r.json()["stats"][0]["chunk_count"] == 0


def test_stats_on_another_sessions_pile_are_404(ctx):
    r = _stats(ctx["client"], {"source_files": ["mine.pdf"], "pile_id": "p-mine"}, THEM)
    assert r.status_code == 404


def test_stats_on_an_unprocessed_pile_are_404(ctx):
    r = _stats(ctx["client"], {"source_files": ["x.pdf"], "pile_id": "p-unprocessed"})
    assert r.status_code == 404


def test_stats_say_so_when_piles_are_not_wired(ctx):
    """Falling back to the KB here would answer from the wrong corpus entirely."""
    client = ctx["build"](False)
    r = _stats(client, {"source_files": ["mine.pdf"], "pile_id": "p-mine"})
    assert r.status_code == 503


# ─── analyze-document ─────────────────────────────────────────────────────────


def test_analyze_reads_the_piles_store(ctx):
    """No prompt template exists in this fixture, so a 500 from the template lookup
    is proof the pile's chunks were found — an empty fetch returns 200/no_chunks."""
    r = _analyze(
        ctx["client"],
        {
            "source_file": "mine.pdf",
            "pile_id": "p-mine",
            "prompt": "q",
            "response_schema": SCHEMA,
        },
    )
    assert r.status_code == 500
    assert "template" in r.json()["detail"].lower()
    assert ctx["opened"] == [("p-mine", "session-aaa")]


def test_analyze_reports_no_chunks_for_a_document_outside_the_pile(ctx):
    r = _analyze(
        ctx["client"],
        {
            "source_file": "kb-doc.pdf",
            "pile_id": "p-mine",
            "prompt": "q",
            "response_schema": SCHEMA,
        },
    )
    assert r.status_code == 200
    assert r.json()["skipped"] == "no_chunks"


def test_analyze_on_another_sessions_pile_is_404(ctx):
    r = _analyze(
        ctx["client"],
        {
            "source_file": "mine.pdf",
            "pile_id": "p-mine",
            "prompt": "q",
            "response_schema": SCHEMA,
        },
        THEM,
    )
    assert r.status_code == 404


def test_analyze_rejects_a_pile_and_a_kb_together(ctx):
    """Silently preferring one would answer from a corpus the caller did not name."""
    r = _analyze(
        ctx["client"],
        {
            "source_file": "mine.pdf",
            "pile_id": "p-mine",
            "kb_id": "default",
            "prompt": "q",
            "response_schema": SCHEMA,
        },
    )
    assert r.status_code == 422


def test_analyze_still_requires_exactly_one_document_identifier(ctx):
    r = _analyze(
        ctx["client"],
        {
            "pile_id": "p-mine",
            "prompt": "q",
            "response_schema": SCHEMA,
        },
    )
    assert r.status_code == 422


def test_analyze_says_so_when_piles_are_not_wired(ctx):
    client = ctx["build"](False)
    r = _analyze(
        client,
        {
            "source_file": "mine.pdf",
            "pile_id": "p-mine",
            "prompt": "q",
            "response_schema": SCHEMA,
        },
    )
    assert r.status_code == 503
