"""Intent: the script and the Explorer tab read the same identifier list the same way.

`scripts/batch-analyze.py` and the batch tab are two front ends onto one endpoint.
The tab's `parseIdList` carries the comment "Mirrors read_id_list() in
scripts/batch-analyze.py", and that mirror is load-bearing: a user verifies a batch
in the UI and then runs the same file headless overnight. If the two disagree about
what counts as a comment or a duplicate, the two runs analyse different documents.

The parity cases below are duplicated verbatim in
`frontend/tests/batch-analysis.test.ts` — change one, change the other.

`validate_schema` is the preflight that makes a misconfigured batch fail in a
second rather than once per document.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "batch-analyze.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("batch_analyze", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


batch_analyze = _load_script()


@pytest.fixture
def id_file(tmp_path):
    def write(text: str) -> Path:
        path = tmp_path / "docs.txt"
        path.write_text(text, encoding="utf-8")
        return path

    return write


# ─── read_id_list: identical rules to the UI ──────────────────────────────────


def test_drops_blank_lines_and_comments(id_file):
    ids, duplicates = batch_analyze.read_id_list(
        id_file("# Kommentar\n\na.pdf\n  \nb.pdf\n# noch einer")
    )

    assert ids == ["a.pdf", "b.pdf"]
    assert duplicates == 0


def test_trims_surrounding_whitespace(id_file):
    ids, _ = batch_analyze.read_id_list(id_file("  a.pdf  \n\tb.pdf\t"))

    assert ids == ["a.pdf", "b.pdf"]


def test_collapses_duplicates_to_first_occurrence(id_file):
    ids, duplicates = batch_analyze.read_id_list(id_file("a.pdf\nb.pdf\na.pdf\na.pdf"))

    assert ids == ["a.pdf", "b.pdf"]
    assert duplicates == 2


def test_keeps_a_hash_that_is_not_at_the_start_of_the_line(id_file):
    ids, _ = batch_analyze.read_id_list(id_file("Bericht #3.pdf"))

    assert ids == ["Bericht #3.pdf"]


def test_preserves_the_order_the_file_gives(id_file):
    ids, _ = batch_analyze.read_id_list(id_file("z.pdf\na.pdf\nm.pdf"))

    assert ids == ["z.pdf", "a.pdf", "m.pdf"], "the list must not be sorted"


def test_an_empty_list_exits_rather_than_starting_an_empty_run(id_file):
    """The UI shows a validation error here; the script has no UI, so it stops."""
    with pytest.raises(SystemExit):
        batch_analyze.read_id_list(id_file("\n\n# only a comment\n"))


def test_handles_umlauts_and_crlf(id_file):
    ids, _ = batch_analyze.read_id_list(id_file("Bericht Zürich.pdf\r\nAnhang.pdf\r\n"))

    assert ids == ["Bericht Zürich.pdf", "Anhang.pdf"]


# ─── validate_schema: catch it before the first LLM call ──────────────────────


def _valid_schema() -> dict:
    return {
        "type": "object",
        "properties": {"question1": {"type": "string"}},
        "required": ["question1"],
        "additionalProperties": False,
    }


def test_a_well_formed_schema_has_no_findings():
    assert batch_analyze.validate_schema(_valid_schema()) == ([], [])


def test_a_required_field_with_no_definition_is_an_error():
    """Strict decoding rejects it outright, so this must not reach the backend."""
    schema = _valid_schema()
    schema["required"].append("question2")

    errors, _ = batch_analyze.validate_schema(schema)

    assert any("question2" in e for e in errors)


def test_a_property_missing_from_required_is_a_warning_not_an_error():
    """Only the strict backends bite, so the run is allowed to proceed."""
    schema = _valid_schema()
    schema["properties"]["question2"] = {"type": "string"}

    errors, warnings = batch_analyze.validate_schema(schema)

    assert errors == []
    assert any("question2" in w for w in warnings)


def test_a_non_object_top_level_type_is_an_error():
    schema = _valid_schema()
    schema["type"] = "array"

    errors, _ = batch_analyze.validate_schema(schema)

    assert any("object" in e for e in errors)


@pytest.mark.parametrize(
    "properties",
    [pytest.param({}, id="empty"), pytest.param(None, id="missing"), pytest.param([], id="wrong type")],
)
def test_no_usable_properties_is_an_error(properties):
    """There would be no CSV columns to fill."""
    schema = _valid_schema()
    if properties is None:
        del schema["properties"]
    else:
        schema["properties"] = properties

    errors, _ = batch_analyze.validate_schema(schema)

    assert errors and "properties" in errors[0]


def test_a_non_list_required_is_an_error():
    schema = _valid_schema()
    schema["required"] = "question1"

    errors, _ = batch_analyze.validate_schema(schema)

    assert any("required" in e for e in errors)


def test_the_schema_the_ui_generates_passes_the_script_preflight():
    """The two front ends must agree: buildSchema() output has to survive this check.

    Mirrors what frontend buildSchema() produces for one row of each type.
    """
    ui_schema = {
        "type": "object",
        "properties": {
            "question1": {"description": "Wer?", "type": "string"},
            "question2": {"description": "Fertig?", "type": "boolean"},
            "question3": {"description": "Wie viele?", "type": ["number", "null"]},
            "question4": {"description": "Status?", "type": "string", "enum": ["yes", "no"]},
        },
        "required": ["question1", "question2", "question3", "question4"],
        "additionalProperties": False,
    }

    assert batch_analyze.validate_schema(ui_schema) == ([], [])
