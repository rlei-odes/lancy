"""Text clean-up applied to chunk content before a model reads it."""

import re

_PADDING = re.compile(r"(?<=\S) {2,}|(?<=\|) {2,}")
_SEPARATOR_CELL = re.compile(r"(:?)-{3,}(:?)")


def compact_markdown_tables(text: str) -> str:
    """Strip the alignment padding from markdown table rows.

    Cell padding and full-width separator dashes only line columns up for a human
    reading the raw text; models and markdown renderers do not need them. Only
    lines that start with `|` outside a fenced code block are changed, and only
    their padding: runs of spaces become one, separator cells become `---`
    (keeping `:` alignment markers). Leading indentation is kept.
    """
    lines = text.split("\n")
    in_code = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped.startswith("|"):
            continue
        indent = line[: len(line) - len(stripped)]
        row = _SEPARATOR_CELL.sub(r"\1---\2", stripped)
        lines[i] = indent + _PADDING.sub(" ", row)
    return "\n".join(lines)
