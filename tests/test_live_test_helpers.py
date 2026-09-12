"""Intent: the live test's own logic, especially the guard on deletion.

`scripts/live-test.py` runs against a real deployment and deletes knowledge
bases. Everything else about it is throwaway, but two things are not: it must
never delete a KB it did not create, and its grading must not report a passing
answer as a miss (or the report stops being read).

The script itself is not exercised here — that needs a running stack. These are
its pure helpers, loaded by path because the filename has a hyphen.
"""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("live_test", REPO / "scripts" / "live-test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lt = _load()


# ─── the deletion guard ───────────────────────────────────────────────────────


def test_the_test_kb_prefix_is_distinctive():
    """A real KB must never plausibly collide with the throwaway namespace."""
    assert lt.TEST_KB_PREFIX
    assert lt.TEST_KB_PREFIX.startswith("zz-")


@pytest.mark.parametrize(
    "kb_id, deletable",
    [
        ("zz-livetest-chromadb", True),
        ("zz-livetest-pgvector", True),
        ("wb-local", False),
        ("default", False),
        ("", False),
        ("livetest-chromadb", False),          # prefix missing
        ("my-zz-livetest-kb", False),          # prefix not at the start
    ],
)
def test_only_prefixed_kbs_are_deletable(kb_id, deletable):
    assert kb_id.startswith(lt.TEST_KB_PREFIX) is deletable


# ─── grading ──────────────────────────────────────────────────────────────────


def test_a_satisfying_answer_reports_no_misses():
    spec = {"expect_all": ["lara"], "expect_any": ["not part"], "expect_sources": ["cat.pdf"]}

    assert lt.grade("The Lara Pallet is not part of the portfolio.", ["Catalog (cat.pdf)"], spec) == []


def test_grading_is_case_insensitive():
    """The model varies capitalisation run to run; that must not read as a miss."""
    spec = {"expect_all": ["LARA"], "expect_any": ["NOT PART"]}

    assert lt.grade("the lara pallet is not part of it", [], spec) == []


def test_a_missing_required_term_is_reported():
    misses = lt.grade("An unrelated answer.", [], {"expect_all": ["lara"]})

    assert len(misses) == 1 and "lara" in misses[0]


def test_expect_any_passes_on_a_single_alternative():
    spec = {"expect_any": ["not part", "does not", "no product"]}

    assert lt.grade("the catalog does not list it", [], spec) == []


def test_expect_any_fails_only_when_every_alternative_is_absent():
    spec = {"expect_any": ["not part", "does not"]}

    assert lt.grade("it is available today", [], spec)


def test_a_forbidden_term_is_reported():
    misses = lt.grade("The Lara Pallet is a wooden pallet.", [], {"forbid": ["wooden pallet"]})

    assert len(misses) == 1 and "forbidden" in misses[0]


def test_an_uncited_source_is_reported():
    misses = lt.grade("An answer.", ["Other (other.pdf)"], {"expect_sources": ["epd.pdf"]})

    assert len(misses) == 1 and "epd.pdf" in misses[0]


def test_a_source_named_in_the_body_counts_as_cited():
    """The model often names the file inline instead of in the sources block."""
    spec = {"expect_sources": ["epd.pdf"]}

    assert lt.grade("As stated in epd.pdf, the figure is verified.", [], spec) == []


def test_an_empty_spec_never_reports_a_miss():
    assert lt.grade("anything at all", [], {}) == []


# ─── response parsing ─────────────────────────────────────────────────────────


def test_the_answer_and_sources_are_split():
    payload = {
        "choices": [
            {"message": {"content": "The answer.\n\n---\n**Sources:**\n- Title (a.pdf)\n- Other (b.pdf)"}}
        ]
    }

    body, sources = lt.parse_answer(payload)

    assert body.startswith("The answer.")
    assert "**Sources:**" not in body
    assert sources == ["Title (a.pdf)", "Other (b.pdf)"]


def test_an_answer_without_a_sources_block_parses():
    payload = {"choices": [{"message": {"content": "Just an answer."}}]}

    assert lt.parse_answer(payload) == ("Just an answer.", [])


# ─── the KB payload ───────────────────────────────────────────────────────────


def test_the_test_kb_inherits_the_embedding_of_the_live_one():
    """A mismatch would raise EmbeddingConflict and evict the real KB."""
    active = {"embedding_backend": "local", "embedding_model": "BAAI/bge-m3", "nomic_prefix": False}

    payload = lt.kb_payload(active, "zz-livetest-chromadb", "chromadb")

    assert payload["embedding_backend"] == "local"
    assert payload["embedding_model"] == "BAAI/bge-m3"
    assert payload["nomic_prefix"] is False


def test_the_store_type_and_name_are_overridden():
    active = {"embedding_model": "m", "vs_type": "pgvector", "name": "Real KB"}

    payload = lt.kb_payload(active, "zz-livetest-chromadb", "chromadb")

    assert payload["vs_type"] == "chromadb"
    assert payload["name"] == "zz-livetest-chromadb"


def test_the_live_kbs_secrets_are_not_copied_into_the_test_kb():
    """GET /kb returns masked values; copying them would store the mask."""
    active = {
        "embedding_model": "m",
        "vs_connection_string": "postgresql://u:***@h:5432/d",
        "embedding_custom_api_key": "********",
    }

    payload = lt.kb_payload(active, "zz-livetest-chromadb", "chromadb")

    assert payload["vs_connection_string"] == ""
    assert "embedding_custom_api_key" not in payload
