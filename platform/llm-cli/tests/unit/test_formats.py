"""The renderer, below the commands.

Every command routes through here, so the guards are checked once, here, against a result
built by hand. The per-command tests then check that each command honors all four formats
rather than re-checking what a format does.
"""

from __future__ import annotations

import csv
import io
import json

import click
import pytest
from click.testing import CliRunner

from blue_data_llm_cli.formats import FORMATS, Result, cell, defuse, key_value, printable, render

HOSTILE = "France\x1b[2Kmissing"


def written(result: Result, fmt: str) -> bytes:
    """Exactly the bytes the renderer puts on stdout for this result in this format."""

    @click.command()
    def once() -> None:
        render(result, fmt)

    outcome = CliRunner().invoke(once, [])
    assert outcome.exit_code == 0, outcome.output
    return outcome.stdout_bytes


def shown(result: Result, fmt: str) -> str:
    """The same, as text. `CliRunner` folds CRLF to LF, so bytes are the CSV question."""
    return written(result, fmt).decode()


@pytest.fixture
def records() -> Result:
    return Result(
        columns=("country", "capital"),
        rows=(("France", "Paris"), ("Germany", "Berlin")),
        payload={"records": [{"country": "France", "capital": "Paris"}], "meta": None},
        sentences=("2 records from gpt-4o.",),
        notes=("2 records | model gpt-4o",),
        empty="(no records)",
    )


def test_the_four_formats_are_the_declared_set() -> None:
    assert FORMATS == ("table", "json", "nl", "csv")


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_format_renders_something(records: Result, fmt: str) -> None:
    assert shown(records, fmt).strip()


# ------------------------------------------------------- control characters --
def test_the_strip_removes_escape_sequences_but_keeps_the_text() -> None:
    assert printable(HOSTILE) == "France[2Kmissing"
    assert "\x1b" not in cell(HOSTILE)


def test_a_tab_survives_because_a_table_can_render_one() -> None:
    assert printable("a\tb") == "a\tb"


@pytest.mark.parametrize("fmt", FORMATS)
def test_no_format_lets_a_control_character_through(fmt: str) -> None:
    result = Result(
        columns=("country",),
        rows=((HOSTILE,),),
        payload={"records": [{"country": HOSTILE}]},
        sentences=(f"Found {HOSTILE}.",),
    )
    assert "\x1b" not in shown(result, fmt)


def test_the_prose_format_strips_its_own_sentences() -> None:
    """The one format with no cell structure to hide behind, and C0 is not only ESC."""
    result = Result(
        columns=("c",),
        rows=(("x",),),
        payload={},
        sentences=("Found France\b\b\b\bGERMANY\a\x00 and x\x9b2Ky.",),
    )
    assert written(result, "nl") == b"Found FranceGERMANY and x2Ky.\n"


def test_a_table_title_is_stripped_too() -> None:
    """Rich does not strip ESC, and the title is built from a name a caller typed."""
    result = Result(columns=("c",), rows=(("x",),), payload={}, title="p\x1b[2J\x1b[31m!")
    assert b"\x1b" not in written(result, "table")


def test_a_control_character_in_a_column_name_is_stripped_too() -> None:
    result = Result(columns=(HOSTILE,), rows=(("x",),), payload={})
    assert "\x1b" not in shown(result, "table")
    assert "\x1b" not in shown(result, "csv")


# ------------------------------------------------------------------- table --
def test_a_table_carries_its_columns_rows_and_notes(records: Result) -> None:
    out = shown(records, "table")
    for expected in ("country", "capital", "France", "Paris", "2 records | model gpt-4o"):
        assert expected in out


def test_an_empty_table_says_what_is_missing_rather_than_drawing_nothing() -> None:
    empty = Result(columns=("country",), rows=(), payload={}, empty="(no records)")
    out = shown(empty, "table")
    assert "(no records)" in out
    assert "country" not in out


# -------------------------------------------------------------------- json --
def test_json_is_one_object_and_nothing_else(records: Result) -> None:
    payload = json.loads(shown(records, "json"))
    assert payload["records"] == [{"country": "France", "capital": "Paris"}]


def test_json_publishes_an_absent_value_as_null(records: Result) -> None:
    assert '"meta": null' in shown(records, "json")


def test_json_values_are_stripped_the_way_a_cell_is() -> None:
    result = Result(columns=(), rows=(), payload={"records": [{"country": HOSTILE}]})
    payload = json.loads(shown(result, "json"))
    assert payload["records"][0]["country"] == "France[2Kmissing"


# --------------------------------------------------------------------- csv --
def rows_of(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_csv_uses_the_same_columns_as_the_table(records: Result) -> None:
    assert rows_of(shown(records, "csv"))[0] == ["country", "capital"]


def test_csv_separates_records_with_crlf(records: Result) -> None:
    assert written(records, "csv").startswith(b"country,capital\r\n")


def test_csv_quotes_a_value_holding_a_separator() -> None:
    result = Result(columns=("note",), rows=(('a,b "c"',),), payload={})
    assert rows_of(shown(result, "csv"))[1] == ['a,b "c"']


@pytest.mark.parametrize("leader", ["=", "+", "-", "@", "\t"])
def test_csv_defuses_a_cell_a_spreadsheet_would_evaluate(leader: str) -> None:
    """These cells hold model output, so a formula in one is a model writing a formula."""
    assert defuse(f"{leader}cmd|'/c calc'!A0").startswith("'")


def test_a_defused_cell_arrives_quoted_rather_than_evaluated() -> None:
    result = Result(columns=("note",), rows=(("=1+1",),), payload={})
    assert rows_of(shown(result, "csv"))[1] == ["'=1+1"]


def test_an_ordinary_cell_is_left_alone() -> None:
    assert defuse("France") == "France"


def test_a_non_tabular_result_is_key_and_value() -> None:
    result = key_value({"protocol": "llm", "variant": "openai"})
    assert rows_of(shown(result, "csv")) == [
        ["key", "value"],
        ["protocol", "llm"],
        ["variant", "openai"],
    ]


# ---------------------------------------------------------------------- nl --
def test_nl_is_a_bare_paragraph(records: Result) -> None:
    assert shown(records, "nl").strip() == "2 records from gpt-4o."


def test_nl_falls_back_to_the_rows_rather_than_printing_nothing() -> None:
    result = key_value({"protocol": "llm"})
    assert shown(result, "nl").strip() == "key protocol, value llm."


def test_nl_says_so_when_there_is_nothing() -> None:
    empty = Result(columns=("country",), rows=(), payload={}, empty="(no records)")
    assert shown(empty, "nl").strip() == "(no records)"
