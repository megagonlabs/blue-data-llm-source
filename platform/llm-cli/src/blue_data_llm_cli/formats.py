"""One renderer, four formats, every command.

"Every command honours every format" is a claim about every command that returns a result,
and the only way to keep it is to have one implementation of it. So a command builds a
:class:`Result` — the columns, the rows, the JSON object, and a sentence or two of prose —
and hands it here.
Adding a format is a branch in :func:`render`; adding a command costs nothing.

The guard that is not optional: **model output is untrusted**. A record value is whatever a
language model wrote, and a CSI escape sequence in it walks the terminal cursor, which is
how a screen came to show one record under a footer reporting two. Rich's own
`strip_control_codes` leaves ESC in place and `markup=False` defends only against Rich's own
`[bold]` syntax, so the strip here is the one that matters, and it runs on every cell in
every format. In `csv` it is followed by a second guard: a cell starting with `=`, `+`, `-`,
`@`, tab or carriage return is prefixed with a quote, because a spreadsheet reads those as
formulas and these cells hold model output.

In `json` the strip runs on string *values*. Keys are left as `json.dumps` writes them: with
`ensure_ascii` on, every control character in a key is escaped, and a key is a token a
consumer matches on rather than text it prints, so rewriting one would break the match.

Stdout carries the result and nothing else. Progress, logs, warnings and errors are the
caller's other stream, so every format is safe to pipe.
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
# a CI log's colour codes corrupts column alignment, because Rich counts the escape bytes
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
    off: Rich's guesses about what a value *is* have no business colouring one.
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
        for index, name in enumerate(result.columns):
            # Sanitised here rather than where the columns were chosen: the column name is
            # also the key each record is read by, and stripping it there turned a key
            # holding a control character into a lookup that matched nothing.
            table.add_column(printable(str(name)), style="bold" if index == 0 else None)
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
