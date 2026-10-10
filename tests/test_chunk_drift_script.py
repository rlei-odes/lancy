"""Intent: the drift report distinguishes a moved boundary from a changed count.

`scripts/chunk-drift.py` exists because chunking changes never reach documents
already in the store — cross-run dedup skips any file whose hash is present, so its
chunks stay as the code that first ingested them produced. The script's judgement
is `verdict()`, and the case that matters is the quiet one: the same number of
chunks holding different text. A count-only comparison calls that unchanged, which
is exactly the drift nobody notices.

Verified against the real corpus while writing it — a markdown file reported
"BOUNDARIES MOVED" with an identical chunk count and identical total characters,
because its stored chunks predate the markdown newline fix in e358443 and dedup had
preserved them through every re-index since.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "chunk-drift.py"


@pytest.fixture(scope="module")
def cd():
    """The script loaded as a module — its filename is not a valid identifier."""
    spec = importlib.util.spec_from_file_location("chunk_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeChunk:
    def __init__(self, content: str, mime_type: str = "text/markdown"):
        self.content = content
        self.mime_type = mime_type
        self.metadata: dict = {}


# ─── verdict: the three outcomes ──────────────────────────────────────────────


def test_identical_text_in_identical_order_is_identical(cd):
    fp = cd.fingerprint([FakeChunk("alpha"), FakeChunk("beta")])
    assert cd.verdict(fp, fp) == "identical"


def test_reordered_chunks_are_still_identical(cd):
    """Store order is not document order, so comparison has to be order-insensitive
    or every file would report drift."""
    a = cd.fingerprint([FakeChunk("alpha"), FakeChunk("beta")])
    b = cd.fingerprint([FakeChunk("beta"), FakeChunk("alpha")])
    assert cd.verdict(a, b) == "identical"


def test_same_count_with_different_text_is_a_moved_boundary(cd):
    """The quiet case, and the whole reason digests are compared rather than counts:
    the real corpus produced exactly this — 6 chunks before, 6 after, identical total
    characters, different text, because single newlines had become spaces."""
    before = cd.fingerprint([FakeChunk("one two\nthree four"), FakeChunk("x")])
    after = cd.fingerprint([FakeChunk("one two three four"), FakeChunk("x")])

    assert before["chunks"] == after["chunks"]
    assert before["chars"] == after["chars"]  # newline → space preserves length
    assert cd.verdict(before, after) == "BOUNDARIES MOVED"


def test_a_different_chunk_count_is_reported_as_such(cd):
    before = cd.fingerprint([FakeChunk("alpha")])
    after = cd.fingerprint([FakeChunk("alpha"), FakeChunk("beta")])
    assert cd.verdict(before, after) == "COUNT CHANGED"


def test_a_repeated_chunk_is_not_collapsed(cd):
    """Digests are compared as a multiset: two identical chunks differ from one."""
    before = cd.fingerprint([FakeChunk("same"), FakeChunk("same")])
    after = cd.fingerprint([FakeChunk("same")])
    assert cd.verdict(before, after) == "COUNT CHANGED"


# ─── fingerprint ──────────────────────────────────────────────────────────────


def test_fingerprint_counts_images_separately(cd):
    fp = cd.fingerprint([
        FakeChunk("text here"),
        FakeChunk("YmFzZTY0", mime_type="image/png"),
    ])
    assert fp["chunks"] == 2
    assert fp["images"] == 1


def test_fingerprint_flags_tiny_chunks(cd):
    fp = cd.fingerprint([FakeChunk("x" * 10), FakeChunk("y" * 500)])
    assert fp["tiny"] == 1


def test_fingerprint_tolerates_empty_content(cd):
    """ChunkRecord.content is nullable in the store."""
    empty = FakeChunk("")
    empty.content = None
    fp = cd.fingerprint([empty])
    assert fp == {
        "chunks": 1, "chars": 0, "digests": [cd.digest("")],
        "sizes": [0], "images": 0, "tiny": 1,
    }


def test_digest_is_stable_and_content_sensitive(cd):
    assert cd.digest("alpha") == cd.digest("alpha")
    assert cd.digest("alpha") != cd.digest("alpha ")


# ─── size_shape ───────────────────────────────────────────────────────────────


def test_size_shape_reports_the_distribution(cd):
    assert cd.size_shape([10, 20, 30]) == "min 10 · p50 20 · max 30"


def test_size_shape_handles_no_chunks(cd):
    assert cd.size_shape([]) == "—"
