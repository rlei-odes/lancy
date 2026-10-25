"""Intent: a file that cannot be indexed is tried once per KB, not on every run, and says why.

A file that yields no chunks stores nothing, so the store cannot tell it from a
new file: every incremental run chunked it again. On a real KB that was 24
"new" files per run — a corrupt .docx, an image-heavy PDF that took docling
minutes — re-done each time for the same empty result. And the chunker's error
was swallowed in the worker process: the log only said "No chunks produced".

The contract:
  - the chunker's own error reaches the log and the ingestion log;
  - a file that errored, gave no chunks, timed out or crashed the worker is
    remembered by its hash and skipped by incremental runs;
  - a full re-index forgets the list and tries every file again;
  - the progress banner learns how each file ended.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio
import concurrent.futures
import zipfile
from pathlib import Path

import pytest

from lancy import ingestion
from lancy.feature0_baseline_rag import load_chunks
from lancy.ingestion import run_ingestion
from lancy.kb_stats import forget_failed, load_failed_files
from lancy.kb_router import KBInfo


class Store:
    async def get_file_hashes(self):
        return set()

    async def count(self):
        return 0


@pytest.fixture
def corrupt(tmp_path) -> KBInfo:
    data = tmp_path / "data"
    data.mkdir()
    # A zip without its main document part, as a damaged Word file often is.
    with zipfile.ZipFile(data / "Backup.docx", "w") as docx:
        docx.writestr("[Content_Types].xml", "<Types/>")
    return KBInfo(name="test", id="test", vs_path=str(tmp_path / "vs"), data_dirs=[str(data)])


@pytest.fixture
def in_thread(monkeypatch):
    """Chunk in a thread: the spawn process pool costs seconds per test and behaves the same here."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(ingestion, "_get_chunking_pool", lambda: pool)
    monkeypatch.setattr(ingestion, "make_vector_store", lambda **kwargs: Store())
    monkeypatch.setattr(ingestion, "_get_cached_embedding_model", lambda kb: object())
    yield
    pool.shutdown()


def run(kb: KBInfo, db_dir: Path, reset: bool = False) -> None:
    asyncio.run(run_ingestion(kb, reset=reset, db_dir=db_dir, cfg=None))


def test_the_chunkers_error_is_raised_instead_of_an_empty_result(corrupt):
    path = Path(corrupt.data_dirs[0]) / "Backup.docx"

    assert load_chunks(include_files=[path]) == []  # the default still skips the file
    with pytest.raises(RuntimeError, match="Could not find main document part"):
        load_chunks(include_files=[path], raise_errors=True)


def test_a_failed_file_is_remembered_with_its_reason(corrupt, in_thread, tmp_path):
    run(corrupt, tmp_path)

    (entry,) = load_failed_files(tmp_path, "test").values()
    assert entry["source_file"] == "Backup.docx"
    assert entry["reason"] == "error" and entry["error"]
    assert ingestion._index_status["last_file"]["outcome"] == "error"


def test_an_incremental_run_skips_a_file_that_failed_before(corrupt, in_thread, tmp_path, monkeypatch):
    run(corrupt, tmp_path)
    submitted: list = []
    pool = ingestion._get_chunking_pool()
    monkeypatch.setattr(pool, "submit", lambda *a, **k: submitted.append(a) or pytest.fail("chunked again"))

    run(corrupt, tmp_path)

    assert submitted == []
    assert ingestion._index_status["skipped_failed"] == 1


def test_a_full_reindex_tries_it_again(corrupt, in_thread, tmp_path):
    run(corrupt, tmp_path)
    first = load_failed_files(tmp_path, "test")

    run(corrupt, tmp_path, reset=True)

    again = load_failed_files(tmp_path, "test")
    assert again.keys() == first.keys()
    assert next(iter(again.values()))["at"] >= next(iter(first.values()))["at"]
    assert ingestion._index_status["skipped_failed"] == 0


def test_forgetting_a_file_lets_the_next_incremental_run_try_it(corrupt, in_thread, tmp_path, monkeypatch):
    """Retrying one file must not need a full re-index of the whole KB."""
    run(corrupt, tmp_path)
    (h,) = load_failed_files(tmp_path, "test")

    assert forget_failed(tmp_path, "test", h) == 0
    run(corrupt, tmp_path)

    assert ingestion._index_status["skipped_failed"] == 0
    assert h in load_failed_files(tmp_path, "test")  # tried again, and failed again
