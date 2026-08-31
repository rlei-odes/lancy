"""Intent: undo the line wrapping document extraction adds, without flattening blocks.

`_normalize_newlines` exists because Docling breaks a paragraph across several
lines to match the page layout — those newlines are an artifact and should become
spaces. The bug was that it collapsed *every* newline, so a markdown table arrived
in the index as one line of pipes and stopped parsing as a table for both the
source viewer and the LLM.

Both halves of that intent are pinned here: wrapped prose is still joined, and
anything that opens a markdown block keeps its own line.
"""

import pytest

from conversational_toolkit.chunking.markdown_chunker import MarkdownChunker
from conversational_toolkit.chunking.pdf_chunker import PDFChunker


@pytest.fixture
def normalize():
    return PDFChunker()._normalize_newlines


# ─── the behaviour the function exists for ────────────────────────────────────


def test_wrapped_prose_is_joined_into_one_line(normalize):
    """A newline inside a paragraph is page layout, not authorial intent."""
    wrapped = "Der Bericht beschreibt die\nErgebnisse des vierten\nQuartals."
    assert normalize(wrapped) == "Der Bericht beschreibt die Ergebnisse des vierten Quartals."


def test_blank_lines_separate_paragraphs(normalize):
    """A blank line ends the fold — the next paragraph starts its own line."""
    text = "Erster Absatz\nwurde umbrochen.\n\nZweiter Absatz."
    assert normalize(text) == "Erster Absatz wurde umbrochen.\n\nZweiter Absatz."


# ─── the regression: block structure must survive ─────────────────────────────


def test_table_rows_keep_their_own_lines(normalize):
    """The reported bug: a table arrived as `| A | B | |---|---| | 1 | 2 |`."""
    table = "| Region | Umsatz |\n|---|---|\n| Zürich | 120 |\n| Bern | 80 |"
    assert normalize(table) == table


def test_bullet_list_items_are_not_run_together(normalize):
    for bullet in ("-", "*", "+"):
        items = f"{bullet} erster Punkt\n{bullet} zweiter Punkt"
        assert normalize(items) == items, f"bullet {bullet!r} was flattened"


def test_numbered_list_items_are_not_run_together(normalize):
    for marker in ("1.", "1)"):
        items = f"{marker} erster Punkt\n2{marker[-1]} zweiter Punkt"
        assert normalize(items) == items, f"marker {marker!r} was flattened"


def test_headings_and_blockquotes_keep_their_own_lines(normalize):
    text = "# Titel\n\n## Unterkapitel\n\n> Zitat"
    assert normalize(text) == text


def test_fenced_code_passes_through_verbatim(normalize):
    """Inside a fence every newline is significant, block-opening or not."""
    code = "```python\nx = 1\ny = 2\n```"
    assert normalize(code) == code


def test_prose_wrapped_between_two_tables_still_joins(normalize):
    """The two rules coexist: fold prose, keep the blocks around it."""
    text = (
        "| A | B |\n|---|---|\n| 1 | 2 |\n"
        "\n"
        "Dieser Satz wurde\nvom Layout umbrochen.\n"
        "\n"
        "- Punkt eins\n- Punkt zwei"
    )
    expected = (
        "| A | B |\n|---|---|\n| 1 | 2 |\n"
        "\n"
        "Dieser Satz wurde vom Layout umbrochen.\n"
        "\n"
        "- Punkt eins\n- Punkt zwei"
    )
    assert normalize(text) == expected


# ─── authored sources are exempt ──────────────────────────────────────────────


def test_markdown_chunker_never_normalizes():
    """.md/.txt are authored, so every newline is intentional — nothing to undo."""
    text = "Zeile eins\nZeile zwei\n\n| A |\n|---|\n| 1 |"
    assert MarkdownChunker()._normalize_newlines(text) == text
