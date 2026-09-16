"""The ``llm`` command."""

from __future__ import annotations

import contextlib
import json
import re
import sys
from typing import Any, NoReturn

import click
from blue_data_llm_source import ExecMeta, FetchResult, LlmSourceError, Outcome
from blue_data_llm_source import __version__ as library_version
from rich.console import Console
from rich.table import Table

from blue_data_llm_cli import config


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--verbose", "-v", is_flag=True, help="Print key alignment and stop reasons.")
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
@click.option(
    "--effort",
    type=click.Choice(["low", "medium", "high"]),
    default="low",
    show_default=True,
    help=(
        "Allows up to 1/2/3 logical extraction asks per part when a primary key is "
        "available. It bounds asks, not billed attempts; exhausting a medium/high allowance "
        "reports a limit stop."
    ),
)
@click.option(
    "--plan-mode",
    default=None,
    help="How to decompose the ask: flat (default), partitioned, row_by_row.",
)
@click.option(
    "--plan-continuations",
    type=int,
    default=None,
    help=(
        "How many times a part may ask again. 0 by default, so each part asks once. "
        "It bounds asks, not billed attempts: an ask may still be retried."
    ),
)
@click.option(
    "--plan-key",
    default=None,
    help="Attribute identifying a record, used to merge duplicates. Must be a requested attr.",
)
@click.option(
    "--plan-keys",
    multiple=True,
    metavar="NAME",
    help="Ordered primary-key fields. Repeat in identity order.",
)
@click.option(
    "--plan-exact-rows",
    type=click.IntRange(min=0),
    default=None,
    help=(
        "Hard row target for a single-leaf plan. A multi-leaf plan does not apply it and "
        "reports exact_rows_not_leaf_bound."
    ),
)
@click.option(
    "--plan-exclusion-bytes",
    type=click.IntRange(min=0),
    default=None,
    help="Most encoded bytes of whole key exclusions in a continuation prompt.",
)
@click.option(
    "--plan-leaves",
    type=int,
    default=None,
    help="Most parts a split may run. Values past it are grouped, several to a part.",
)
@click.option(
    "--plan-partition-key",
    default=None,
    help="Preferred root split dimension. Descendants may choose other dimensions.",
)
@click.option("--plan-depth", type=int, default=None, help="Deepest partition level. Default: 2.")
@click.option(
    "--plan-partitions",
    type=int,
    default=None,
    help="Most children proposed for one partition node. Default: 4.",
)
@click.option(
    "--plan-target-rows",
    type=int,
    default=None,
    help="Row-count hint above which a descendant may split again. Default: 50.",
)
@click.option(
    "--plan-schema-mode",
    type=click.Choice(["preserve", "enrich"]),
    default=None,
    help="Preserve declared attributes (default), or allow planning to append inferred ones.",
)
@click.option(
    "--plan-planning-order",
    type=click.Choice(
        [
            "planning.research_order",
            "planning.schema_first",
            "planning.locked_sequential",
            "planning.locked_parallel",
            "planning.combined",
        ]
    ),
    default=None,
    help="Order used to obtain analysis, schema, and cardinality facts.",
)
@click.option(
    "--plan-expose-estimate/--no-plan-expose-estimate",
    default=None,
    help="Include or omit the soft row estimate in extraction prompts.",
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
    help="Not supported; deferred to v2.0. Kept so the refusal can say what to use.",
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
    effort: str,
    limit_cost: float | None,
    limit_seconds: float | None,
    limit_calls: int | None,
    plan_mode: str | None,
    plan_continuations: int | None,
    plan_key: str | None,
    plan_keys: tuple[str, ...],
    plan_exact_rows: int | None,
    plan_exclusion_bytes: int | None,
    plan_leaves: int | None,
    plan_partition_key: str | None,
    plan_depth: int | None,
    plan_partitions: int | None,
    plan_target_rows: int | None,
    plan_schema_mode: str | None,
    plan_planning_order: str | None,
    plan_expose_estimate: bool | None,
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

    has_plan_key = bool(plan_key and plan_key.strip()) or any(key.strip() for key in plan_keys)
    if (
        effort in {"medium", "high"}
        and plan_continuations is None
        and plan_mode in {None, "flat"}
        and not has_plan_key
    ):
        raise click.BadParameter(
            "medium/high effort needs --plan-key or --plan-keys in flat mode; add a requested "
            "key, select a non-flat mode that can infer one, or use --plan-continuations for "
            "explicit best-effort behavior.",
            param_hint="--effort",
        )

    # Inside the handler, like the fetch itself. Building the limits validates them, and a
    # negative or non-finite value raised a ConfigError out of the command as a traceback
    # with exit code 1 — so a script branching on the published exit table took the wrong
    # branch for an ordinary command-line correction.
    try:
        limits = config.build_limits(limit_cost, limit_seconds, limit_calls)
        properties = config.build_properties(
            model,
            limits,
            max_tokens,
            plan_mode,
            (
                plan_continuations
                if plan_continuations is not None
                else config.continuations_for_effort(effort)
            ),
            plan_key,
            plan_leaves,
            plan_partition_key,
            plan_depth,
            plan_partitions,
            plan_target_rows,
            plan_schema_mode,
            plan_keys,
            plan_exact_rows,
            plan_exclusion_bytes,
            plan_planning_order,
            plan_expose_estimate,
        )
        source = config.build_source()

        # Some provider SDK paths write diagnostics to stdout. Keep stdout owned by this
        # command so JSON output remains machine-readable while the diagnostics stay visible.
        with contextlib.redirect_stdout(sys.stderr):
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

    # The result is printed first; the outcome then distinguishes an initial budget refusal
    # from a retained partial answer. A row-only cap is excluded because it leaves a usable
    # result and reports `truncated`.
    if result.meta.outcome == Outcome.PARTIAL:
        # Before the budget check, matching outcome precedence. Any simultaneous ceiling
        # remains available in the stop fields.
        raise SystemExit(config.EXIT_PARTIAL)
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
    # Before the verbose block and outside it: a gap is why the row count is what it is, not
    # a diagnostic about how it was produced.
    for gap in result.meta.gaps:
        console.print(f"gap: {gap}", soft_wrap=True)
    stop_reasons = result.meta.stop_reasons or (
        [result.meta.stopped_by] if result.meta.stopped_by else []
    )
    for reason in stop_reasons:
        console.print(f"stopped by: {reason}", soft_wrap=True)
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
        f"row estimate {meta.row_estimate if meta.row_estimate is not None else 'unknown'} | "
        f"${meta.cost:.4f}{'' if meta.accounting_known else ' (unknown)'} | "
        f"{meta.latency_seconds:.2f}s"
    )


def _verbose_lines(meta: ExecMeta) -> list[str]:
    lines: list[str] = []
    if meta.primary_keys:
        lines.append("primary keys: " + ", ".join(meta.primary_keys))
    lines.append(f"observed complete keys: {meta.observed_key_count}")
    if meta.incomplete_key_records:
        lines.append(f"incomplete-key records: {meta.incomplete_key_records}")
    if meta.normalization_collisions:
        lines.append(f"normalization collisions: {len(meta.normalization_collisions)}")
    if meta.unchecked_types:
        lines.append("types this library cannot check: " + ", ".join(meta.unchecked_types))
    if meta.unmapped_keys:
        lines.append("unmapped keys: " + ", ".join(meta.unmapped_keys))
    if meta.remapped_keys:
        pairs = ", ".join(f"{k} -> {v}" for k, v in meta.remapped_keys.items())
        lines.append("remapped keys: " + pairs)
    lines.append(f"outcome: {meta.outcome}")
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
