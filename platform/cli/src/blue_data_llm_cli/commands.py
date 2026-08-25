"""The ``llm`` command."""

from __future__ import annotations

import json
import re
from typing import Any, NoReturn

import click
from blue_data_llm_source import ExecMeta, FetchResult, LlmSourceError, Outcome
from blue_data_llm_source import __version__ as library_version
from rich.console import Console
from rich.table import Table

from blue_data_llm_cli import config


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--verbose", is_flag=True, help="Print key alignment and stop reasons.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Query a language model as if it were a data source."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@cli.command()
@click.argument("query")
@click.option(
    "--attr",
    "attr",
    multiple=True,
    metavar="NAME[:TYPE]",
    help="Requested output attribute. Repeat for more. Types: int, float, bool, date.",
)
@click.option("--context", default=None, help="Extra text that clarifies the query.")
@click.option("--max-records", type=int, default=None, help="Stop after this many records.")
@click.option("--model", default=None, help="Model to call, e.g. gpt-4o.")
@click.option(
    "--max-tokens",
    # A range, not a bare int: zero or a negative allowance passed straight through to the
    # provider when no cost ceiling was set, because only the pricing path validates it and
    # that path does not run without one.
    type=click.IntRange(min=1),
    default=None,
    # The output allowance dominates the cost bound, so a cost ceiling below its floor
    # refuses every call before making one. Both READMEs said to lower this and only the
    # library could: the remedy the CLI documented was one the CLI did not offer.
    help="Output allowance per call. Lower it to lower the floor a cost ceiling must clear.",
)
@click.option("--limit-cost", type=float, default=None, help="Refuse a call past this spend.")
@click.option("--limit-seconds", type=float, default=None, help="Refuse a call past this age.")
@click.option("--limit-calls", type=int, default=None, help="Refuse more than this many calls.")
@click.option(
    "--rate-limit",
    # Deliberately untyped. Declared as a float, click validated the value before the
    # handler ran, so `--rate-limit fast` was answered with "not a valid float" — true, and
    # not the thing the caller needed to know. Nothing here parses it; it exists to be
    # refused with an answer whatever it holds.
    default=None,
    # Accepted so it can be refused with an answer. Left undeclared, it produced click's
    # generic "no such option", which tells a caller the flag is wrong but not that the
    # ceiling they wanted is unavailable in this build or what to reach for instead.
    help="Not supported in v1.1; deferred to v2.0. Kept so the refusal can say what to use.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.pass_context
def fetch(
    ctx: click.Context,
    query: str,
    attr: tuple[str, ...],
    context: str | None,
    max_records: int | None,
    model: str | None,
    max_tokens: int | None,
    limit_cost: float | None,
    limit_seconds: float | None,
    limit_calls: int | None,
    rate_limit: str | None,
    output_format: str,
) -> None:
    """Fetch records for QUERY from the model's own knowledge."""
    if rate_limit is not None:
        raise click.BadParameter(
            "client-side call spacing is deferred to v2.0. Cap concurrency with the "
            "library's 'llm.max_parallel', or throttle above this command where the whole "
            "request sequence is visible.",
            param_hint="--rate-limit",
        )
    try:
        attrs = config.build_attrs(attr)
    except ValueError as error:
        raise click.BadParameter(str(error), param_hint="--attr") from error

    # Inside the handler, like the fetch itself. Building the limits validates them, and a
    # negative or non-finite value raised a ConfigError out of the command as a traceback
    # with exit code 1 — so a script branching on the published exit table took the wrong
    # branch for an ordinary command-line correction.
    try:
        limits = config.build_limits(limit_cost, limit_seconds, limit_calls)
        properties = config.build_properties(model, limits, max_tokens)
        source = config.build_source()

        result = source.fetch(
            query,
            attrs=attrs or None,
            context=context,
            max_records=max_records,
            properties=properties or None,
        )
    except LlmSourceError as error:
        _fail(error)

    verbose = bool(ctx.obj and ctx.obj.get("verbose"))
    if output_format == "json":
        _print_json(result, verbose)
    else:
        _print_table(result, [a.name for a in attrs], verbose)

    # The result is printed first, then the exit code reports that a ceiling ended the run.
    # The library turns a crossed ceiling into an empty result rather than raising, so no
    # BudgetExceeded ever reaches the handler above and exit 7 was unreachable: a run that
    # blew its deadline printed a full table and exited 0, with nothing to see without
    # --verbose. A caller scripting this has to be able to tell without parsing the output.
    #
    # A row cap is deliberately excluded. It is the one ceiling that leaves a usable result,
    # which is why it reports `truncated` rather than `stopped_by_limit`, and exiting
    # non-zero for it would make `--max-records` look like a failure every time it worked.
    if result.meta.outcome == Outcome.STOPPED_BY_LIMIT:
        raise SystemExit(config.EXIT_BUDGET)


@cli.command()
def info() -> None:
    """Print the protocol, variant, and library version."""
    source = config.build_source()
    click.echo(f"protocol  {source.protocol}")
    click.echo(f"variant   {source.variant}")
    click.echo(f"version   {library_version}")


def _fail(error: LlmSourceError) -> NoReturn:
    click.echo(f"llm: {type(error).__name__}: {error}", err=True)
    raise SystemExit(config.exit_code_for(error))


def _print_json(result: FetchResult, verbose: bool) -> None:
    payload = {
        "records": result.records,
        "meta": config.meta_summary(result.meta, verbose=verbose),
    }
    click.echo(json.dumps(payload, indent=2, default=str))


def _print_table(result: FetchResult, requested: list[str], verbose: bool) -> None:
    # Markup off: a record value containing square brackets is data, not a style tag.
    console = Console(markup=False, highlight=False)
    columns = _columns(result.records, requested)
    if not result.records:
        console.print("(no records)")
    else:
        table = Table(show_header=True, header_style="bold")
        for column in columns:
            # Sanitized here rather than in `_columns`, because the column name is also the
            # key used to read each record below; stripping it there turned a key holding a
            # control character into a lookup that matched nothing and blanked the column.
            table.add_column(_printable(str(column)))
        for record in result.records:
            table.add_row(*[_cell(record.get(column)) for column in columns])
        console.print(table)

    console.print(_footer(len(result.records), result.meta), soft_wrap=True)
    if verbose:
        for line in _verbose_lines(result.meta):
            console.print(line, soft_wrap=True)


def _columns(records: list[dict[str, Any]], requested: list[str]) -> list[str]:
    columns = list(requested)
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)
    return columns


# C0 and C1 control characters, minus tab, which is the one a table can render. Rich's
# `strip_control_codes` leaves ESC (0x1b) in place and `markup=False` defends only against
# its own `[bold]` syntax, so a CSI sequence in a model's answer reached the terminal and
# was executed by it: a value could walk the cursor back and erase rows already drawn,
# leaving a screen showing one record under a footer reporting two. The benign case needs
# no adversary — a model quoting a CI log's colour codes corrupts column alignment, because
# rich counts the escape bytes toward the cell's width.
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _printable(text: str) -> str:
    """*text* with terminal control sequences removed."""
    return _CONTROLS.sub("", text)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _printable(value)
    if isinstance(value, (dict, list)):
        return _printable(json.dumps(value, default=str))
    return _printable(str(value))


def _footer(count: int, meta: ExecMeta) -> str:
    tokens = meta.prompt_tokens + meta.completion_tokens
    return (
        f"{count} {_plural(count, 'record')} | "
        f"model {meta.model or '(none)'} | "
        f"{meta.calls} {_plural(meta.calls, 'call')} | "
        f"{tokens} tokens | "
        f"${meta.cost:.4f}{'' if meta.accounting_known else ' (unknown)'} | "
        f"{meta.latency_seconds:.2f}s"
    )


def _verbose_lines(meta: ExecMeta) -> list[str]:
    lines: list[str] = []
    if meta.unchecked_types:
        lines.append("types this library cannot check: " + ", ".join(meta.unchecked_types))
    if meta.unmapped_keys:
        lines.append("unmapped keys: " + ", ".join(meta.unmapped_keys))
    if meta.remapped_keys:
        pairs = ", ".join(f"{k} -> {v}" for k, v in meta.remapped_keys.items())
        lines.append("remapped keys: " + pairs)
    lines.append(f"outcome: {meta.outcome}")
    if meta.stopped_by:
        lines.append(f"stopped by: {meta.stopped_by}")
    if meta.type_mismatches:
        lines.append(f"type mismatches: {len(meta.type_mismatches)}")
        for mismatch in meta.type_mismatches:
            lines.append(
                f"  record {mismatch['record_index']} {mismatch['attribute']}: "
                f"{mismatch['value']} is not {mismatch['declared_type']}"
            )
    if meta.key_collisions:
        lines.append(f"key collisions: {len(meta.key_collisions)}")
        for collision in meta.key_collisions:
            lines.append(
                f"  record {collision['record_index']} {collision['attribute']}: "
                f"kept {collision['kept_key']}, ignored {collision['ignored_key']}"
            )
    return lines


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"
