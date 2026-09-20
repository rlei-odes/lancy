"""Intent: a usable ranking is salvaged from whatever a small model emits.

Observed in production (gemma-4-26b via vLLM, no grammar constraint on the
utility LLM): the model wrapped the list in a second array, closed the object
early, then wrote a page of self-correcting commentary and a second JSON block
that hit the token cap mid-array:

    {"ranking": [
        [3, 5, 1, 7, 9, 11, 0, 12, 13, 2, 4, 8, 10, 6, 14]
    }
    *Self-correction on the ranking logic:* ...

`_llm_rerank` took `text[find("{"):rfind("}")+1]` and `json.loads`, so the
whole ranking was discarded and retrieval silently fell back to the base
order — even though all fifteen indices were sitting there, correctly ordered.
Small models are the ones most likely to be used for reranking, so the parser
has to tolerate their output rather than demand strict JSON.

What must NOT be salvaged: a response with no ranking at all. Inventing an
order would be worse than falling back, because the fallback is at least the
base retriever's honest ranking and is reported as such.
"""

import pytest

from conversational_toolkit.retriever.reranking_retriever import _extract_ranking

FIFTEEN = [3, 5, 1, 7, 9, 11, 0, 12, 13, 2, 4, 8, 10, 6, 14]


# ─── the shapes small models actually produce ─────────────────────────────────


def test_the_production_failure_is_salvaged():
    """Verbatim from the 2026-09-20 log: nested array, unclosed bracket, prose."""
    text = (
        '```json\n{\n  "ranking": [\n    [3, 5, 1, 7, 9, 11, 0, 12, 13, 2, 4, 8, 10, 6, 14]\n}\n```'
        "\n\n*Self-correction on the ranking logic for the final output:*\n"
        "1. **[3]** (Best Practices) - Directly addresses workload migration.\n"
        "2. **[5]** (Define your business goals) - Addresses alignment.\n"
        '\n*Refined JSON output:*\n\n```json\n{\n  "ranking": [3, 5, 1, 7, 9, 11, 0, 12, 13, 2, 4'
    )

    assert _extract_ranking(text) == FIFTEEN


def test_a_plain_valid_response_still_works():
    assert _extract_ranking('{"ranking": [2, 0, 1]}') == [2, 0, 1]


def test_markdown_fences_are_tolerated():
    assert _extract_ranking('```json\n{"ranking": [2, 0, 1]}\n```') == [2, 0, 1]


def test_a_nested_list_is_flattened():
    assert _extract_ranking('{"ranking": [[2, 0, 1]]}') == [2, 0, 1]


def test_commentary_before_the_json_is_ignored():
    text = 'Sure! Here is the ranking you asked for:\n\n{"ranking": [1, 0]}'

    assert _extract_ranking(text) == [1, 0]


def test_commentary_after_the_list_does_not_leak_in():
    """The prose is full of bracketed indices — none may be read as ranking."""
    text = '{"ranking": [1, 0]}\n\nReasoning: [1] is best because... then [0], and [7].'

    assert _extract_ranking(text) == [1, 0]


def test_a_truncated_list_keeps_what_arrived():
    """Cut off by the token cap: a partial ranking beats no ranking."""
    assert _extract_ranking('{"ranking": [4, 2, 0') == [4, 2, 0]


def test_a_repeated_index_is_not_returned_twice():
    """A duplicate would place the same chunk in the results twice."""
    assert _extract_ranking('{"ranking": [1, 0, 1, 2]}') == [1, 0, 2]


# ─── what must not be salvaged ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["", "I cannot rank these documents.", '{"result": "no ranking here"}', '{"ranking": []}'],
    ids=["empty", "refusal", "wrong-key", "empty-list"],
)
def test_a_response_with_no_ranking_raises(text):
    """The caller's fallback to the base order is the honest outcome here."""
    with pytest.raises(ValueError):
        _extract_ranking(text)
