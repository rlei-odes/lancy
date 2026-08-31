"""Intent: one embedding model per process, and the API key never leaves as plaintext.

`build_embedding_model()` puts a fresh SentenceTransformer on the GPU every call
(~2.1 GiB for bge-m3). KB switch, session config save and reindex all rebuild
agents, and each rebuilt model stayed pinned by the retriever it was bound into —
VRAM ran out after a handful of rebuilds. `get_shared_embedding_model` is the
single-entry cache that fixed it.

The cache key includes the API key so rotating it in the Settings UI invalidates a
cached remote client, but only its digest is stored so the secret never reaches a
log line or a traceback. Both properties are asserted here.

The real builder is stubbed throughout — these tests never load a model.
"""

import threading

import pytest

from lancy import feature0_baseline_rag as f0


class FakeModel:
    def __init__(self, name: str):
        self.name = name


@pytest.fixture(autouse=True)
def clean_cache():
    f0.clear_embedding_cache()
    yield
    f0.clear_embedding_cache()


@pytest.fixture
def builder(monkeypatch):
    """Replace the real builder and record every call it receives."""
    calls = []

    def fake_build(backend, model_name, **kwargs):
        calls.append((backend, model_name, kwargs))
        return FakeModel(f"{backend}/{model_name}")

    monkeypatch.setattr(f0, "build_embedding_model", fake_build)
    return calls


# ─── the behaviour the cache exists for ───────────────────────────────────────


def test_same_config_returns_the_identical_instance(builder):
    """Not just an equal model — the same object, or the VRAM is allocated twice."""
    first = f0.get_shared_embedding_model("huggingface", "bge-m3")
    second = f0.get_shared_embedding_model("huggingface", "bge-m3")

    assert first is second
    assert len(builder) == 1


def test_changed_model_builds_a_new_instance(builder):
    first = f0.get_shared_embedding_model("huggingface", "bge-m3")
    second = f0.get_shared_embedding_model("huggingface", "e5-large")

    assert first is not second
    assert len(builder) == 2


def test_only_one_entry_is_kept(builder):
    """A different embedding config means every pooled KB is rebuilt anyway."""
    first = f0.get_shared_embedding_model("huggingface", "bge-m3")
    f0.get_shared_embedding_model("huggingface", "e5-large")
    back_again = f0.get_shared_embedding_model("huggingface", "bge-m3")

    assert back_again is not first, "the displaced model should not be resurrected"
    assert len(builder) == 3


def test_clear_releases_the_cached_model(builder):
    first = f0.get_shared_embedding_model("huggingface", "bge-m3")
    f0.clear_embedding_cache()
    second = f0.get_shared_embedding_model("huggingface", "bge-m3")

    assert first is not second
    assert len(builder) == 2


def test_concurrent_callers_build_the_model_only_once(monkeypatch):
    """The lock is held across construction — two rebuilds racing is what
    pushed a nearly full GPU over the edge."""
    calls = []
    started = threading.Barrier(8)

    def slow_build(backend, model_name, **kwargs):
        calls.append(model_name)
        threading.Event().wait(0.05)  # widen the window a real load would open
        return FakeModel(model_name)

    monkeypatch.setattr(f0, "build_embedding_model", slow_build)

    results = []

    def worker():
        started.wait()
        results.append(f0.get_shared_embedding_model("huggingface", "bge-m3"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1, f"model built {len(calls)}x concurrently"
    assert len({id(r) for r in results}) == 1, "callers got different instances"


# ─── the API key is identity, but never plaintext ─────────────────────────────


def test_rotating_the_api_key_invalidates_the_cache(builder):
    first = f0.get_shared_embedding_model(
        "custom", "text-embedding-3", custom_base_url="https://x", custom_api_key="old-key"
    )
    second = f0.get_shared_embedding_model(
        "custom", "text-embedding-3", custom_base_url="https://x", custom_api_key="new-key"
    )

    assert first is not second


def test_api_key_is_never_stored_in_plaintext(builder):
    secret = "sk-super-secret-value"
    f0.get_shared_embedding_model(
        "custom", "text-embedding-3", custom_base_url="https://x", custom_api_key=secret
    )

    stored = repr(f0._emb_cached_key)
    assert secret not in stored, "the raw API key is retained in the cache key"


def test_log_description_carries_no_key_material(builder):
    secret = "sk-super-secret-value"
    f0.get_shared_embedding_model(
        "custom", "text-embedding-3", custom_base_url="https://x", custom_api_key=secret
    )

    described = f0._describe(f0._emb_cached_key)
    assert secret not in described
    assert described == "custom/text-embedding-3"
