"""Intent: Explorer tools read the KB the session selected, not the pool's global active one.

Selecting a KB that is already in the pool is session-local — the frontend stores
the id and sends it per request, without moving the pool's shared active pointer.
`POST /rag/chunks`, `GET /rag/store-info` (the chunk browser's file list),
`POST /rag/document-stats` and `POST /rag/analyze-document` (batch analysis)
therefore take a `kb_id`:

  - `kb_id` reads that KB's store, even when another KB is globally active
  - a `kb_id` that is not loaded is a 404 rather than a silent fallback to the
    active KB, which would show chunks from the wrong corpus
  - without `kb_id` the active KB is used, as before
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lancy.rag_router import create_rag_router


class FakeChunk:
    def __init__(self, content: str, source_file: str):
        self.id = f"c-{source_file}"
        self.content = content
        self.title = source_file
        self.metadata = {"source_file": source_file}


class FakeStore:
    def __init__(self, chunks: list[FakeChunk]):
        self.chunks = chunks

    async def get_chunks_by_filter(self, filters=None, limit=None, offset=0):
        return self.chunks[offset : offset + limit if limit else None]

    async def count(self) -> int:
        return len(self.chunks)


ACTIVE_STORE = FakeStore([FakeChunk("standard text", "standard.pdf")])
OTHER_STORE = FakeStore([FakeChunk("other text", "other.pdf")])


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(
        create_rag_router(
            db_dir=tmp_path,
            vector_store_factory=lambda: ACTIVE_STORE,
            vs_by_kb_factory=lambda kb_id: {"standard": ACTIVE_STORE, "other": OTHER_STORE}.get(kb_id),
            rebuild_callback=None,
            prompts_dir=tmp_path / "prompts",
        )
    )
    return TestClient(app)


def _files(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [c["metadata"]["source_file"] for c in resp.json()["chunks"]]


def test_kb_id_reads_that_kb_not_the_active_one(client):
    assert _files(client.post("/api/v1/rag/chunks", json={"kb_id": "other"})) == ["other.pdf"]


def test_unloaded_kb_id_is_404_not_a_fallback(client):
    resp = client.post("/api/v1/rag/chunks", json={"kb_id": "missing"})
    assert resp.status_code == 404
    assert "missing" in resp.json()["detail"]


def test_without_kb_id_uses_the_active_kb(client):
    assert _files(client.post("/api/v1/rag/chunks", json={})) == ["standard.pdf"]


def test_store_info_kb_id_lists_that_kbs_files(client):
    resp = client.get("/api/v1/rag/store-info", params={"kb_id": "other"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["file_list"] == ["other.pdf"]


def test_store_info_unloaded_kb_id_is_404_not_a_fallback(client):
    resp = client.get("/api/v1/rag/store-info", params={"kb_id": "missing"})
    assert resp.status_code == 404
    assert "missing" in resp.json()["detail"]


def test_store_info_without_kb_id_uses_the_active_kb(client):
    resp = client.get("/api/v1/rag/store-info")
    assert resp.status_code == 200, resp.text
    assert resp.json()["file_list"] == ["standard.pdf"]


def test_document_stats_kb_id_reads_that_kb(client):
    resp = client.post("/api/v1/rag/document-stats", json={"source_files": ["other.pdf"], "kb_id": "other"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["stats"][0]["chunk_count"] == 1


def test_document_stats_unloaded_kb_id_is_404(client):
    resp = client.post("/api/v1/rag/document-stats", json={"source_files": ["x.pdf"], "kb_id": "missing"})
    assert resp.status_code == 404


def test_analyze_document_unloaded_kb_id_is_404_not_a_fallback(client):
    resp = client.post(
        "/api/v1/rag/analyze-document",
        json={"source_file": "standard.pdf", "prompt": "Q", "response_schema": {}, "kb_id": "missing"},
    )
    assert resp.status_code == 404
    assert "missing" in resp.json()["detail"]
