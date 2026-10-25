"""Intent: table padding never costs model context, and nothing but padding is removed.

docling pads markdown table cells with spaces so columns line up, and draws
separator rows with dashes as wide as the column. Neither carries meaning: a
glossary chunk from the CASDEAI course was 66 % padding (8,010 characters, 2,744
of text). Every model that reads a chunk paid for it — the reranker, which only
looked at the first 400 characters, saw a heading and two column names.

The contract for `compact_markdown_tables`:
  - table rows lose padding runs; separator cells shrink to `---`, keeping the
    `:` alignment markers;
  - everything that is not a table row is returned exactly as it was —
    prose, indentation, and code blocks, even code that starts with `|`.
"""

from conversational_toolkit.utils.text import compact_markdown_tables


def test_table_padding_and_separator_width_are_removed():
    table = (
        "| Artefakt   | Definition                     |\n"
        "|------------|--------------------------------|\n"
        "| Hub        | Enthält die Business Keys      |"
    )

    assert compact_markdown_tables(table) == (
        "| Artefakt | Definition |\n|---|---|\n| Hub | Enthält die Business Keys |"
    )


def test_alignment_markers_survive():
    assert compact_markdown_tables("| a | b | c |\n|:-----|-----:|:----:|") == "| a | b | c |\n|:---|---:|:---:|"


def test_text_outside_tables_is_untouched():
    text = "## Heading\n\nSome    spaced   prose.\n    indented line\n- list  item"

    assert compact_markdown_tables(text) == text


def test_code_blocks_are_untouched_even_when_lines_start_with_a_pipe():
    text = "```\n| keep    this   |\n|-------|\n```\n| a    | b |"

    assert compact_markdown_tables(text) == "```\n| keep    this   |\n|-------|\n```\n| a | b |"


def test_leading_indentation_of_a_table_row_is_kept():
    assert compact_markdown_tables("  |  a   |  b  |") == "  | a | b |"
