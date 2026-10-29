"""Render command results as table, JSON, natural language, or CSV.

Commands pass a :class:`Result` containing columns, rows, a JSON object, and prose.
Model output is untrusted: control sequences are stripped from cells in every format.
Rich's ``strip_control_codes`` leaves ESC in place, and ``markup=False`` only handles
Rich markup. CSV also quotes cells starting with ``=``, ``+``, ``-``, ``@``, tab,
or carriage return to prevent spreadsheet formula execution.

JSON string values are stripped. Keys keep ``json.dumps`` escaping with
``ensure_ascii`` because consumers match keys exactly. Results go to stdout;
progress, logs, warnings, and errors go to stderr.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import click
from rich.console import Console
from rich.table import Table

FORMATS = ("table", "json", "nl", "csv")
"""Every value `--format` accepts, on every command that returns a result."""

# C0 and C1 control characters, minus tab and newline, which are the two a cell can carry:
# csv quotes them and json escapes them. A benign case needs no adversary — a model quoting
# a CI log's color codes corrupts column alignment, because Rich counts the escape bytes
# toward the cell's width.
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

# What a spreadsheet reads as the start of a formula rather than as text.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def printable(text: str) -> str:
    """*text* with terminal control sequences removed."""
    return _CONTROLS.sub("", text)


def cell(value: Any) -> str:
    """One value as a string a terminal can be handed."""
    if value is None:
        return ""
    if isinstance(value, str):
        return printable(value)
    if isinstance(value, (dict, list)):
        return printable(json.dumps(value, default=str))
    return printable(str(value))


def scrub(value: Any) -> Any:
    """Every string inside a JSON payload, stripped the same way a table cell is."""
    if isinstance(value, str):
        return printable(value)
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return [scrub(item) for item in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def defuse(text: str) -> str:
    """A CSV cell a spreadsheet will read as text rather than as a formula."""
    if text[:1] in _FORMULA_LEADERS:
        return f"'{text}"
    return text


@dataclass(frozen=True)
class Result:
    """One command's answer, in the one shape all four formats render.

    `payload` is the JSON object, and it is the command's job to keep its keys stable: the
    same command and flags always produce the same keys, and a value that is absent is
    `null` rather than a key that went missing. `--verbose` is part of "the same flags": it
    adds diagnostic keys, which is what asking for them means.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    payload: dict[str, Any]
    sentences: tuple[str, ...] = ()
    """The `nl` paragraph, one sentence per entry."""
    notes: tuple[str, ...] = field(default=())
    """Lines printed under a `table`: footers, gaps, stop reasons, resolved settings."""
    title: str | None = None
    empty: str | None = None
    """What a table says instead of drawing nothing — `(no records)`, and what to try."""


def key_value(
    values: Mapping[str, Any], payload: dict[str, Any] | None = None, **extra: Any
) -> Result:
    """A result that is not a list of things: its columns are `key` and `value`.

    The rows are *values*; `payload` overrides the JSON object where a command publishes
    more than it tabulates.
    """
    return Result(
        columns=("key", "value"),
        rows=tuple((name, value) for name, value in values.items()),
        payload=dict(values) if payload is None else payload,
        **extra,
    )


def tabular(
    columns: Sequence[str],
    records: Sequence[Mapping[str, Any]],
    payload: dict[str, Any],
    **extra: Any,
) -> Result:
    """A result that is a list of things, read out of dictionaries in column order."""
    return Result(
        columns=tuple(columns),
        rows=tuple(tuple(record.get(column) for column in columns) for record in records),
        payload=payload,
        **extra,
    )


def console() -> Console:
    """The stdout console every format writes through.

    Markup off: a record value holding square brackets is data, not a style tag. Highlight
    off: Rich's guesses about what a value *is* have no business coloring one.
    """
    return Console(markup=False, highlight=False)


def render(result: Result, fmt: str, out: Console | None = None) -> None:
    """Write *result* to stdout in *fmt*."""
    if fmt == "json":
        _render_json(result)
    elif fmt == "csv":
        _render_csv(result)
    elif fmt == "nl":
        _render_nl(result)
    else:
        _render_table(result, out or console())


def _render_json(result: Result) -> None:
    click.echo(json.dumps(scrub(result.payload), indent=2, default=str))


def _render_csv(result: Result) -> None:
    buffer = io.StringIO()
    # RFC 4180: CRLF between records, and a quote only where one is needed.
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([defuse(printable(name)) for name in result.columns])
    for row in result.rows:
        writer.writerow([defuse(cell(value)) for value in row])
    click.echo(buffer.getvalue(), nl=False)


def _render_nl(result: Result) -> None:
    # Stripped here rather than trusted from the command: a sentence is built out of model
    # output — the model name, a stop reason, a profile name someone typed — and prose is
    # the one format with no structure around a cell to hide behind.
    sentences = result.sentences or _sentences_from(result)
    click.echo(" ".join(printable(sentence) for sentence in sentences))


def _render_table(result: Result, out: Console) -> None:
    if not result.rows and result.empty:
        out.print(result.empty, style="dim", soft_wrap=True)
    else:
        title = printable(result.title) if result.title else None
        table = Table(show_header=True, header_style="bold", title=title)
        profile_table = result.columns == ("Parameter", "Value", "Source")
        for index, name in enumerate(result.columns):
            # Sanitised here rather than where the columns were chosen: the column name is
            # also the key each record is read by, and stripping it there turned a key
            # holding a control character into a lookup that matched nothing.
            minimum = (
                max(len(str(row[index])) for row in result.rows)
                if profile_table and name == "Parameter" and result.rows
                else None
            )
            table.add_column(
                printable(str(name)),
                style="bold" if index == 0 else None,
                min_width=minimum,
                max_width={"Value": 12, "Source": 15}.get(name) if profile_table else None,
            )
        for row in result.rows:
            table.add_row(*[cell(value) for value in row])
        out.print(table)
    for note in result.notes:
        out.print(printable(note), soft_wrap=True)


def _sentences_from(result: Result) -> tuple[str, ...]:
    """A fallback paragraph, so no command can answer `--format nl` with nothing."""
    if not result.rows:
        return (result.empty or "Nothing to report.",)
    return tuple(
        ", ".join(f"{name} {cell(value)}" for name, value in zip(result.columns, row)) + "."
        for row in result.rows
    )
