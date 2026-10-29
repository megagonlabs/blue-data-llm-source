"""The ``blue-llmdb`` command tree: profile, data, model, and metadata.

For profile settings, precedence is command option, environment variable, profile,
then built-in default. The resolver handles the bottom three tiers. Request flags,
including the ``--plan-*`` controls, are command-line only so they cannot carry over
to a later query. ``--effort`` is a profile setting because it sets the continuation
allowance.

This module declares arguments and prints results. ``config`` translates options
for the library; ``formats`` renders results.
"""

from __future__ import annotations

import contextlib
import math
import os
from typing import Any, cast

import click
from blue_data_llm_source import (
    ExecMeta,
    LlmSource,
    Outcome,
    can_bound,
    meta_view,
    priceable_models,
    published_defaults,
    rates_for,
    redact,
)
from blue_data_llm_source import __version__ as library_version
from blue_data_llm_source.llm.client import default_max_tokens_for
from blue_data_llm_source.llm.models import entry_for
from blue_data_llm_source.planning import MODES, PLANNING_ORDERS, SCHEMA_MODES
from blue_data_llm_source.planning.plan import CHILD_WORDINGS
from blue_data_llm_source.planning.split_gate import SPLIT_POLICIES
from blue_data_llm_source.prompts import LEAF_PROMPT_VERSIONS
from blue_data_llm_source.reduction.obedience import FILTER_ENFORCEMENTS

from blue_data_llm_cli import config, formats
from blue_data_llm_cli.profile import cli_spec, profile_group, profile_store, resolver_for
from blue_data_llm_cli.profile_adapter import ConfigResolver, ProfileError, ProfileStore

# Use the library's closed sets so invalid plan options fail during CLI parsing.


class _EmptyProfileStore(ProfileStore):
    """Default settings for a read-only command when no profile home can be created."""

    def selected(self) -> str:
        return "default"

    def exists(self, name: str) -> bool:
        return name == "default"

    def values(self, name: str) -> dict[str, str]:
        return {}


# ------------------------------------------------------------------ #
# Root                                                                #
# ------------------------------------------------------------------ #
@click.group(cls=config.Group, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(
    version=config.cli_version(), prog_name=config.PROGRAM, message="%(prog)s %(version)s"
)
@click.option(
    "--profile",
    "profile_name",
    envvar="BLUE_DATA_PROFILE",
    default=None,
    help="Configuration profile. Overrides BLUE_DATA_PROFILE. "
    "Defaults to the selection in ~/.blue-llmdb/.selected.profile.",
)
@click.pass_context
def cli(ctx: click.Context, profile_name: str | None) -> None:
    """blue-llmdb: query a language model as if it were a data source.

    Configuration lives in profiles: `blue-llmdb profile config` to set values,
    `blue-llmdb profile inspect` to see them and where each came from. Every setting
    `profile inspect` lists is read from the command option first, then the environment,
    then the profile, then the built-in default. A flag with no parameter there is read
    from the command line and nowhere else.
    """
    home = ""
    resolver: ConfigResolver | None = None
    failure: config.CliError | None = None
    read_only_fallback_failure: config.CliError | None = None

    # Anything this callback refuses is held rather than raised. Click has not parsed the
    # subcommand's arguments yet, so a failure reported from here could only ever be plain
    # text; `config.start` raises it as the command body's first act, where `--format` is
    # known and a JSON consumer gets an object like any other failure.
    try:
        home = config.cli_home()

        # A fresh install gets its `default` profile here rather than being told to run
        # `profile add` before anything works. Keyed on the profiles file, so a profile
        # removed on purpose is not resurrected on the next command.
        store = profile_store(home)
        try:
            store.initialize()
        except ProfileError as error:
            if (
                profile_name is not None
                or ctx.invoked_subcommand not in {"data", "model", "metadata"}
                or os.path.lexists(home)
                or not str(error).startswith(f"cannot create {home}:")
            ):
                raise
            read_only_fallback_failure = config.failure_for(error)
            store = _EmptyProfileStore(home)

        # Refuse a blank name before profile selection can fall back to the default.
        if profile_name is not None and not profile_name.strip():
            raise config.CliError(
                config.USAGE,
                f"--profile needs a profile name. Run `{config.PROGRAM} profile list` to see them.",
            )

        # A missing profile would silently omit its configured ceilings. Profile
        # commands still need to run so a stale selection can be repaired.
        if profile_name and not store.exists(profile_name):
            raise config.CliError(
                config.PROFILE,
                f"no such profile: {profile_name!r}. Run `{config.PROGRAM} profile list` to "
                f"see them, or `{config.PROGRAM} profile add {profile_name}` to make it.",
            )
        if profile_name is None and ctx.invoked_subcommand != "profile":
            selected = store.selected()
            if not store.exists(selected):
                source = (
                    ".selected.profile"
                    if os.path.lexists(store.selected_path)
                    else "the implicit default"
                )
                raise config.CliError(
                    config.PROFILE,
                    f"selected profile {selected!r} from {source} does not exist. "
                    f"Run `{config.PROGRAM} profile list` to see available profiles, "
                    f"then `{config.PROGRAM} profile select NAME` to choose one.",
                )

        # Apply the profile before other commands run. Resolved values go into the
        # environment, where the provider SDK reads its configuration.
        managing = ctx.invoked_subcommand == "profile"
        resolver = (
            ConfigResolver(cli_spec(), store, profile=profile_name)
            if read_only_fallback_failure is not None
            else resolver_for(home, profile_name, allow_missing=managing)
        )
        # Undone when the command finishes, and registered before the export rather than
        # after it: an export that fails halfway has still written part of what it resolved.
        ctx.call_on_close(resolver.restore)
        try:
            resolver.export()
        except config.STORE_ERRORS:
            if not managing:
                raise
            # Profile commands call no provider, and a stored value that does not resolve
            # must not block the command that repairs it.
            resolver.restore()
    except (click.Abort, click.exceptions.Exit):
        raise
    except Exception as error:
        failure = config.failure_for(error)

    ctx.obj = {
        "home": home,
        "profile": resolver.profile if resolver is not None else (profile_name or ""),
        "config": resolver,
        "failure": failure,
        "read_only_fallback_failure": read_only_fallback_failure,
    }


cli.add_command(profile_group)


# ------------------------------------------------------------------ #
# Shared option sets                                                  #
# ------------------------------------------------------------------ #
# Declared once and applied to both `data` commands, so an option that appears on both has
# the same name, type, default and meaning on both by construction rather than by review.
_REQUEST_OPTIONS = [
    click.option(
        "--attr",
        "attr",
        multiple=True,
        metavar="NAME[:TYPE]",
        help=(
            "Requested output attribute. Repeat for more. Checked types: int, integer, float, "
            "number, bool, boolean, date, str, string, text."
        ),
    ),
    click.option("--context", default=None, help="Extra text that clarifies the query."),
    click.option(
        "--model",
        default=None,
        help="Model to call, e.g. gpt-6-luna or gpt-4o. Default: the profile's model.",
    ),
    click.option(
        "--max-tokens",
        # Validate the allowance even when no cost ceiling is configured.
        type=click.IntRange(min=1),
        default=None,
        # The output allowance dominates the cost bound, so a cost ceiling below its floor
        # refuses every call before making one.
        help="Output allowance per call. Lower it to lower the floor a cost ceiling must clear.",
    ),
    click.option(
        "--reasoning-effort",
        type=click.Choice(["none", "minimal", "low", "medium", "high", "xhigh", "max"]),
        default=None,
        help="Model reasoning effort. GPT-6 Luna defaults to medium; GPT-4o rejects it.",
    ),
    click.option("--temperature", type=float, default=None, help="Sampling temperature."),
    click.option("--timeout", type=float, default=None, help="Seconds to wait for one call."),
    click.option(
        "--price-in",
        type=float,
        default=None,
        help="Your input rate per token. Makes a model the price table does not carry bounded.",
    ),
    click.option(
        "--price-out",
        type=float,
        default=None,
        help="Your output rate per token. Pairs with --price-in.",
    ),
    click.option("--limit-cost", type=float, default=None, help="Refuse a call past this spend."),
    click.option("--limit-seconds", type=float, default=None, help="Refuse a call past this age."),
    click.option("--limit-calls", type=int, default=None, help="Refuse more than this many calls."),
    click.option("--max-retries", type=int, default=None, help="Retries allowed per ask."),
    click.option("--variant", default=None, help="Source variant within the llm protocol."),
    click.option(
        "--log",
        "-l",
        is_flag=True,
        default=False,
        help="Log each step of this run on stderr, and a traceback if one fails.",
    ),
    click.option(
        "--log-limit",
        type=int,
        default=None,
        help="Most characters per logged line. Default: the profile's BLUE_DATA_CLI_LOG_LIMIT.",
    ),
    config.format_option,
]

_PLAN_OPTIONS = [
    click.option(
        "--effort",
        type=click.Choice(config.EFFORTS),
        default=None,
        help="Continuation allowance: low, medium, high allow 1/2/3 extraction asks per part. "
        "Default: the profile's BLUE_DATA_LLM_SOURCE_EFFORT, else low.",
    ),
    click.option(
        "--plan-mode",
        type=click.Choice(MODES),
        default=None,
        help="How to decompose the ask. Default: flat.",
    ),
    click.option(
        "--plan-continuations",
        type=int,
        default=None,
        help="How many times a part may ask again. 0 by default, so each part asks once. "
        "It bounds asks, not billed attempts: an ask may still be retried.",
    ),
    click.option(
        "--plan-key",
        default=None,
        help="Attribute identifying a record, used to merge duplicates. Must be a requested attr.",
    ),
    click.option(
        "--plan-keys",
        multiple=True,
        metavar="NAME",
        help="Ordered primary-key fields. Repeat in identity order.",
    ),
    click.option(
        "--plan-exact-rows",
        type=click.IntRange(min=0),
        default=None,
        help="Hard row target for a single-leaf plan. A multi-leaf plan does not apply it and "
        "reports exact_rows_not_leaf_bound.",
    ),
    click.option(
        "--plan-exclusion-bytes",
        type=click.IntRange(min=0),
        default=None,
        help="Most encoded bytes of whole key exclusions in a continuation prompt.",
    ),
    click.option(
        "--plan-leaves",
        type=int,
        default=None,
        help=(
            "Most parts a split may run. Values past it are grouped, several to a part. "
            "Default: 100."
        ),
    ),
    click.option(
        "--plan-partition-key",
        default=None,
        help="Preferred root split dimension. Descendants may choose other dimensions.",
    ),
    click.option(
        "--plan-depth", type=int, default=None, help="Deepest partition level. Default: 2."
    ),
    click.option(
        "--plan-partitions",
        type=int,
        default=None,
        help="Most children proposed for one partition node. Default: 20.",
    ),
    click.option(
        "--plan-target-rows",
        type=int,
        default=None,
        help="Row-count hint above which a descendant may split again. Default: 50.",
    ),
    click.option(
        "--plan-schema-mode",
        type=click.Choice(SCHEMA_MODES),
        default=None,
        help="Preserve declared attributes (default), or allow planning to append inferred ones.",
    ),
    click.option(
        "--plan-planning-order",
        type=click.Choice(PLANNING_ORDERS),
        default=None,
        help="Order used to obtain analysis, schema, and cardinality facts.",
    ),
    click.option(
        "--plan-expose-estimate/--no-plan-expose-estimate",
        default=None,
        help="Include or omit the soft row estimate in extraction prompts.",
    ),
    click.option(
        "--plan-leaf-prompt",
        type=click.Choice(LEAF_PROMPT_VERSIONS),
        default=None,
        help="Leaf prompt contract for non-flat modes. keyed-leaf-fetch-2 asks for every "
        "matching record. Default: keyed-leaf-fetch-1.",
    ),
    click.option(
        "--plan-child-wording",
        type=click.Choice(CHILD_WORDINGS),
        default=None,
        help="Child leaf wording in partitioned mode: the parent's, or canonical wording "
        "derived from the request and the child's filters. Default: parent.",
    ),
    click.option(
        "--plan-filter-enforcement",
        type=click.Choice(FILTER_ENFORCEMENTS),
        default=None,
        help="Keep (flag) or remove at the leaf (drop) records that contradict their leaf's "
        "filter. Both count them. Default: flag.",
    ),
    click.option(
        "--plan-range-filters/--no-plan-range-filters",
        default=None,
        help="Read integer-span partition values as range filters. Needs partitioned mode "
        "and --plan-leaf-prompt keyed-leaf-fetch-2. Default: off.",
    ),
    click.option(
        "--plan-partition-guardrails/--no-plan-partition-guardrails",
        default=None,
        help="Send the partition-2 proposal prompt and refuse numeric keys bucketed like a "
        "measurement. Needs partitioned mode. Default: off.",
    ),
    click.option(
        "--plan-split-policy",
        type=click.Choice(SPLIT_POLICIES),
        default=None,
        help="Whether the root always splits, or is asked once first and splits only on a cut, "
        "failed or short answer (evidence). Needs partitioned mode. Default: always.",
    ),
    click.option(
        "--plan-split-threshold-rows",
        type=click.IntRange(min=0),
        default=None,
        help="Under --plan-split-policy evidence, the estimate above which a probe holding "
        "fewer than half the expected keys splits. Default: 120.",
    ),
]


def _apply(options: list[Any], command: Any) -> Any:
    for option in reversed(options):
        command = option(command)
    return command


def request_options(command: Any) -> Any:
    return _apply(_REQUEST_OPTIONS, command)


def plan_options(command: Any) -> Any:
    return _apply(_PLAN_OPTIONS, command)


class EstimateCommand(config.Command):
    """`data estimate` prices one flat fetch, so a plan flag here has an answer, not a typo.

    The library would accept a `plan.*` setting on this path and ignore it, which is exactly
    what its own closed key set exists to prevent. Refusing is stricter, and the refusal says
    where the flag does work.
    """

    def usage_hint(self, error: click.UsageError) -> str:
        name = str(getattr(error, "option_name", "") or "")
        if name == "--max-records" or name == "--effort" or name.startswith("--plan-"):
            return (
                f" `estimate` prices one flat fetch and reads no plan setting, so {name} would "
                f"change nothing here; it belongs to `{config.PROGRAM} data query`."
            )
        return ""


# ------------------------------------------------------------------ #
# data                                                                #
# ------------------------------------------------------------------ #
@cli.group("data")
def data_group() -> None:
    """Query the model and price a query before running it."""


@data_group.command("query")
@click.argument("query")
# Query only. `estimate` reads no response, so there is nothing for it to show verbosely,
# and a flag accepted there and ignored is the kind of setting this CLI refuses.
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show key alignment, type mismatches and collisions.",
)
@click.option(
    "--max-records",
    type=click.IntRange(min=1),
    default=None,
    help="Row ceiling for this request. It is the only flag for this knob: the library "
    "assigns it to limits.max_rows, overwriting any configured value.",
)
@click.option(
    "--max-parallel", type=click.IntRange(min=1), default=None, help="Calls in flight at once."
)
@click.option(
    "--cache/--no-cache",
    "cache",
    default=None,
    help="Answer a repeated call from the on-disk cache.",
)
@click.option("--cache-dir", default=None, help="Where cached answers are kept.")
@click.option(
    "--rate-limit",
    # Accept any value for this unsupported option so the handler can give the same
    # actionable refusal for every input.
    default=None,
    help="Not supported. Kept so the refusal can say what to use instead.",
)
@request_options
@plan_options
@click.pass_obj
def data_query(
    obj: dict[str, Any],
    query: str,
    attr: tuple[str, ...],
    context: str | None,
    max_records: int | None,
    model: str | None,
    max_tokens: int | None,
    reasoning_effort: str | None,
    temperature: float | None,
    timeout: float | None,
    max_parallel: int | None,
    cache: bool | None,
    cache_dir: str | None,
    price_in: float | None,
    price_out: float | None,
    limit_cost: float | None,
    limit_seconds: float | None,
    limit_calls: int | None,
    max_retries: int | None,
    variant: str | None,
    effort: str | None,
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
    plan_leaf_prompt: str | None,
    plan_child_wording: str | None,
    plan_filter_enforcement: str | None,
    plan_range_filters: bool | None,
    plan_partition_guardrails: bool | None,
    plan_split_policy: str | None,
    plan_split_threshold_rows: int | None,
    rate_limit: str | None,
    output_format: str | None,
    verbose: bool,
    log: bool,
    log_limit: int | None,
) -> None:
    """Fetch records for QUERY from the model's own knowledge.

    A setting `blue-llmdb profile inspect` lists is read from the command option first,
    then the environment, then the profile, then the built-in default. The `--plan-*`
    controls are not among them: they describe one ask rather than a deployment, and a
    partition key or a row target left in a profile would apply itself to the next,
    unrelated query. `--effort` is the exception, because a continuation allowance is an
    appetite for spending.

    A ceiling applies to one request, the profile supplies the default for each request,
    and every run prints which ceilings applied and where each came from.
    """
    published = config.Output(output_format)
    with config.reporting(published, log=log):
        resolver = config.start(obj)
        fmt = published.resolve(resolver)
        reporter = config.Reporter(fmt, log, config.resolve_log_limit(resolver, log_limit))
        if rate_limit is not None:
            raise config.CliError(config.USAGE, f"--rate-limit: {config.RATE_LIMIT_REASON}")
        attrs = _attrs(attr)
        requested_names = {item.name for item in attrs}
        if requested_names and any(
            key not in requested_names for key in ((plan_key,) if plan_key else ()) + plan_keys
        ):
            raise config.CliError(
                config.USAGE,
                "--plan-key and --plan-keys must name attributes supplied with --attr.",
            )

        settings = config.Settings(resolver)
        properties = _adapter_properties(
            settings,
            model=model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            timeout=timeout,
            max_parallel=max_parallel,
            cache=cache,
            cache_dir=cache_dir,
            price_in=price_in,
            price_out=price_out,
        )
        properties.update(
            _limit_properties(settings, limit_cost, limit_seconds, limit_calls, max_retries)
        )
        max_rows = settings.take("limits.max_rows", max_records, "BLUE_DATA_LLM_SOURCE_MAX_RECORDS")
        properties.update(
            _plan_settings(
                settings,
                resolver,
                effort=effort,
                plan_mode=plan_mode,
                plan_continuations=plan_continuations,
                plan_key=plan_key,
                plan_keys=plan_keys,
                plan_exact_rows=plan_exact_rows,
                plan_exclusion_bytes=plan_exclusion_bytes,
                plan_leaves=plan_leaves,
                plan_partition_key=plan_partition_key,
                plan_depth=plan_depth,
                plan_partitions=plan_partitions,
                plan_target_rows=plan_target_rows,
                plan_schema_mode=plan_schema_mode,
                plan_planning_order=plan_planning_order,
                plan_expose_estimate=plan_expose_estimate,
                plan_leaf_prompt=plan_leaf_prompt,
                plan_child_wording=plan_child_wording,
                plan_filter_enforcement=plan_filter_enforcement,
                plan_range_filters=plan_range_filters,
                plan_partition_guardrails=plan_partition_guardrails,
                plan_split_policy=plan_split_policy,
                plan_split_threshold_rows=plan_split_threshold_rows,
            )
        )
        chosen_variant = settings.take(
            "connection.variant", variant, "BLUE_DATA_LLM_SOURCE_VARIANT"
        )
        _check_floors(settings)
        source = config.build_source(chosen_variant)

        reporter.line(f"profile {obj['profile']}: {settings.footer()}")
        reporter.line(f"query: {query}")
        with reporter.status("Querying…"):
            # Provider stdout is untrusted text and cannot share the result stream.
            with open(os.devnull, "w", encoding="utf-8") as sink:
                with contextlib.redirect_stdout(sink):
                    result = source.fetch(
                        query,
                        attrs=attrs or None,
                        context=context,
                        max_records=max_rows,
                        properties=properties or None,
                    )
        reporter.line(
            f"outcome {result.meta.outcome}: {len(result.records)} record(s), "
            f"{result.meta.calls} call(s), ${result.meta.cost:.4f}"
        )

        columns = _columns(result.records, [a.name for a in attrs])
        rendered = formats.tabular(
            columns,
            result.records,
            {
                "records": result.records,
                "meta": meta_view(result.meta, verbose=verbose),
                "settings": settings.envelope(),
            },
            sentences=_query_sentences(result.records, result.meta, settings),
            # Built only for the format that prints them: the verbose block reads fields
            # off diagnostic records, and no other format has any use for the result.
            notes=(
                _query_notes(result.records, result.meta, settings, verbose)
                if fmt == "table"
                else ()
            ),
            empty="(no records)",
        )
    formats.render(rendered, fmt)
    if fmt == "csv" and result.meta.outcome != Outcome.OK:
        details = [f"outcome: {result.meta.outcome}"]
        details.extend(f"gap: {redact(gap)}" for gap in result.meta.gaps)
        details.extend(
            f"stopped by: {_stop_line(reason, result.meta)}"
            for reason in _stop_reasons(result.meta)
        )
        click.echo(formats.printable("; ".join(details)).replace("\n", " "), err=True)

    # The result is printed first; the outcome then distinguishes an initial budget refusal
    # from a retained partial answer. A row-only cap is excluded because it leaves a usable
    # result and reports `truncated`.
    if result.meta.outcome == Outcome.PARTIAL:
        # Before the budget check, matching outcome precedence. Any simultaneous ceiling
        # remains available in the stop fields.
        raise SystemExit(config.EXIT_PARTIAL)
    if result.meta.outcome == Outcome.STOPPED_BY_LIMIT:
        raise SystemExit(config.EXIT_BUDGET)


@data_group.command("estimate", cls=EstimateCommand)
@click.argument("query")
@request_options
@click.pass_obj
def data_estimate(
    obj: dict[str, Any],
    query: str,
    attr: tuple[str, ...],
    context: str | None,
    model: str | None,
    max_tokens: int | None,
    reasoning_effort: str | None,
    temperature: float | None,
    timeout: float | None,
    price_in: float | None,
    price_out: float | None,
    limit_cost: float | None,
    limit_seconds: float | None,
    limit_calls: int | None,
    max_retries: int | None,
    variant: str | None,
    output_format: str | None,
    log: bool,
    log_limit: int | None,
) -> None:
    """Bound what one fetch of QUERY could cost, contacting no provider.

    A setting `blue-llmdb profile inspect` lists is read from the command option first,
    then the environment, then the profile, then the built-in default; every other flag
    here is read from the command line and nowhere else.

    \b
    This bounds one flat fetch, including retries. Non-flat requests may make planning
    and extraction calls with different prompts; this figure does not bound their total.
    --limit-calls and --max-retries change this number, because the bound accumulates once
    per attempt that can actually happen.
    """
    published = config.Output(output_format)
    with config.reporting(published, log=log):
        resolver = config.start(obj, read_only=True)
        fmt = published.resolve(resolver)
        reporter = config.Reporter(fmt, log, config.resolve_log_limit(resolver, log_limit))
        attrs = _attrs(attr)
        settings = config.Settings(resolver)
        properties = _adapter_properties(
            settings,
            model=model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            timeout=timeout,
            price_in=price_in,
            price_out=price_out,
        )
        properties.update(
            _limit_properties(settings, limit_cost, limit_seconds, limit_calls, max_retries)
        )
        chosen_variant = settings.take(
            "connection.variant", variant, "BLUE_DATA_LLM_SOURCE_VARIANT"
        )
        _check_floors(settings)
        source = config.build_source(chosen_variant)

        reporter.line(f"profile {obj['profile']}: {settings.footer()}")
        with reporter.status("Estimating…"):
            estimate = source.estimate(
                query, attrs=attrs or None, context=context, properties=properties or None
            )
        reporter.line(f"bounded {estimate.bounded}: max cost {estimate.max_cost}")

        published = {
            "bounded": estimate.bounded,
            "max_cost": estimate.max_cost,
            "max_cost_per_attempt": estimate.max_cost_per_attempt,
            "max_attempts": estimate.max_attempts,
            # A library message published as a field rather than raised, so it reaches the
            # caller without passing the boundary that masks the raised ones.
            "reason": redact(estimate.reason) if estimate.reason else estimate.reason,
            "model": estimate.model,
            "prompt_bytes": estimate.prompt_bytes,
            "max_output_tokens": estimate.max_output_tokens,
            "attempts_within_ceiling": estimate.attempts_within_ceiling,
        }
        notes = [settings.footer()]
        ceiling = properties.get("limits.max_cost")
        fit = _ceiling_sentence(published, ceiling)
        if fit:
            notes.append(fit)
        rendered = formats.key_value(
            published,
            payload={**published, "settings": settings.envelope()},
            sentences=_estimate_sentences(published, settings, ceiling),
            notes=tuple(notes),
        )
    formats.render(rendered, fmt)


# ------------------------------------------------------------------ #
# model                                                               #
# ------------------------------------------------------------------ #
@cli.group("model")
def model_group() -> None:
    """List, inspect and choose the model a query runs against."""


@model_group.command("list")
@config.format_option
@click.pass_obj
def model_list(obj: dict[str, Any], output_format: str | None) -> None:
    """List the models this package carries a published price for.

    Any model name the provider accepts can be used. These are the ones a cost ceiling can
    be bounded against without supplying --price-in and --price-out.
    """
    published = config.Output(output_format)
    with config.reporting(published):
        fmt = published.resolve(config.start(obj, read_only=True))
        models = list(priceable_models())
        rendered = formats.tabular(
            ("model",),
            [{"model": name} for name in models],
            {"models": models},
            sentences=(f"{len(models)} models have a published price: {', '.join(models)}.",),
            empty="(no priced models)",
        )
    formats.render(rendered, fmt)


@model_group.command("inspect")
@click.argument("name")
@config.format_option
@click.pass_obj
def model_inspect(obj: dict[str, Any], name: str, output_format: str | None) -> None:
    """Show what is known about NAME: its published rates and whether it can be bounded."""
    published = config.Output(output_format)
    with config.reporting(published):
        resolver = config.start(obj, read_only=True)
        fmt = published.resolve(resolver)
        configured = resolver.get("BLUE_DATA_LLM_SOURCE_MAX_TOKENS")
        # `is None`, not a truth test: a configured 0 is a number, and silently replacing it
        # with the library default while honoring -5 would treat configured numbers inconsistently.
        allowance = default_max_tokens_for(name) if configured is None else int(configured)
        rates = rates_for(name)
        published = {
            "model": name,
            "input_price_per_token": rates[0] if rates else None,
            "output_price_per_token": rates[1] if rates else None,
            "max_tokens": allowance,
            "can_bound": can_bound(name, allowance),
            # The model's published contract and rates. The profile's own settings override
            # them for a query.
            "published_defaults": published_defaults(name),
        }
        rendered = formats.key_value(
            _flattened(published),
            payload=published,
            sentences=(_model_sentence(name, rates, allowance),),
        )
    formats.render(rendered, fmt)


@model_group.command("select")
@click.argument("name")
@click.pass_obj
def model_select(obj: dict[str, Any], name: str) -> None:
    """Make NAME the model this profile's queries run against.

    Stored as the profile's BLUE_DATA_LLM_MODEL, so `--model` still overrides it for one
    request and `profile inspect` shows where it came from.
    """
    reporter = config.Reporter()
    with config.reporting("table"):
        config.start(obj)
        if not name.strip():
            raise config.CliError(config.USAGE, "a model name cannot be blank.")
        target = str(obj["profile"])
        store = profile_store(obj["home"])
        store.set_value(target, "BLUE_DATA_LLM_MODEL", name, create=True)
        if rates_for(name) is None:
            reporter.warn(
                f"{name} has no published price here, so a cost ceiling cannot be bounded "
                f"against it. Give the rates with --price-in and --price-out, or with "
                f"BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN and "
                f"BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN in the profile."
            )
    formats.console().print(f"Selected model: {name} (profile {target})", style="bold")


# ------------------------------------------------------------------ #
# metadata                                                            #
# ------------------------------------------------------------------ #
@cli.group("metadata")
def metadata_group() -> None:
    """Inspect what this source is."""


@metadata_group.command("inspect")
@config.format_option
@click.pass_obj
def metadata_inspect(obj: dict[str, Any], output_format: str | None) -> None:
    """Print the protocol, the variant, and the version of the library underneath."""
    published = config.Output(output_format)
    with config.reporting(published):
        resolver = config.start(obj, read_only=True)
        fmt = published.resolve(resolver)
        variant = resolver.get("BLUE_DATA_LLM_SOURCE_VARIANT")
        source: LlmSource = config.build_source(variant)
        published = dict(source.fetch_metadata())
        published["version"] = library_version
        rendered = formats.key_value(
            published,
            sentences=(
                f"Protocol {published.get('protocol')}, variant {published.get('variant')}, "
                f"library version {library_version}.",
            ),
        )
    formats.render(rendered, fmt)


# ------------------------------------------------------------------ #
# Translation helpers                                                 #
# ------------------------------------------------------------------ #
def _attrs(values: tuple[str, ...]) -> list[Any]:
    try:
        return config.build_attrs(values)
    except ValueError as error:
        raise config.CliError(config.USAGE, f"--attr: {error}") from None


def _adapter_properties(
    settings: config.Settings,
    *,
    model: str | None,
    max_tokens: int | None,
    reasoning_effort: str | None,
    temperature: float | None,
    timeout: float | None,
    price_in: float | None,
    price_out: float | None,
    max_parallel: int | None = None,
    cache: bool | None = None,
    cache_dir: str | None = None,
) -> dict[str, Any]:
    """The `llm.*` settings, each recorded with the tier it came from.

    `llm.stream` is absent on purpose: streaming has no meaning for a command whose result
    is a parsed record set.
    """
    pairs = (
        ("llm.model", model, "BLUE_DATA_LLM_MODEL"),
        ("llm.max_tokens", max_tokens, "BLUE_DATA_LLM_SOURCE_MAX_TOKENS"),
        (
            "llm.reasoning_effort",
            reasoning_effort,
            "BLUE_DATA_LLM_SOURCE_REASONING_EFFORT",
        ),
        ("llm.temperature", temperature, "BLUE_DATA_LLM_SOURCE_TEMPERATURE"),
        ("llm.timeout", timeout, "BLUE_DATA_LLM_SOURCE_TIMEOUT"),
        ("llm.max_parallel", max_parallel, "BLUE_DATA_LLM_SOURCE_MAX_PARALLEL"),
        ("llm.cache", cache, "BLUE_DATA_LLM_SOURCE_CACHE"),
        ("llm.cache_dir", cache_dir, "BLUE_DATA_LLM_SOURCE_CACHE_DIR"),
        ("llm.price_per_input_token", price_in, "BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN"),
        ("llm.price_per_output_token", price_out, "BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN"),
    )
    out: dict[str, Any] = {}
    for key, option, parameter in pairs:
        value = settings.take(key, option, parameter)
        if value is not None:
            out[key] = value
    return out


def _limit_properties(
    settings: config.Settings,
    limit_cost: float | None,
    limit_seconds: float | None,
    limit_calls: int | None,
    max_retries: int | None,
) -> dict[str, Any]:
    cost = settings.take("limits.max_cost", limit_cost, "BLUE_DATA_LLM_SOURCE_LIMIT_COST")
    seconds = settings.take(
        "limits.max_seconds", limit_seconds, "BLUE_DATA_LLM_SOURCE_LIMIT_SECONDS"
    )
    calls = settings.take("limits.max_calls", limit_calls, "BLUE_DATA_LLM_SOURCE_LIMIT_CALLS")
    retries = settings.take("limits.max_retries", max_retries, "BLUE_DATA_LLM_SOURCE_MAX_RETRIES")
    limits = config.build_limits(cost, seconds, calls, retries)
    out = config.limit_properties(limits)
    if retries is not None:
        # Passed even when it equals the library's own default, so an injected client's
        # configuration cannot quietly supply a different one.
        out["limits.max_retries"] = limits.max_retries
    return out


def _plan_settings(
    settings: config.Settings,
    resolver: Any,
    *,
    effort: str | None,
    plan_continuations: int | None,
    **plan: Any,
) -> dict[str, Any]:
    """The `plan.*` settings, with the effort shorthand folded into the allowance."""
    chosen, source = _effort(resolver, effort)
    continuations = (
        plan_continuations
        if plan_continuations is not None
        else config.continuations_for_effort(chosen)
    )
    _refuse_keyless_effort(chosen, source, plan_continuations, plan)

    out = config.plan_properties(plan_continuations=continuations, **plan)
    for key, value in out.items():
        if key != "plan.max_continuations":
            settings.applied[key] = value
            settings.source[key] = "flag"
    settings.applied["plan.max_continuations"] = continuations
    settings.source["plan.max_continuations"] = "flag" if plan_continuations is not None else source
    return out


def _effort(resolver: Any, option: str | None) -> tuple[str, str]:
    if option is not None:
        return option, "flag"
    value = resolver.get("BLUE_DATA_LLM_SOURCE_EFFORT")
    if value is None:
        return "low", "default"
    chosen = str(value)
    if chosen not in config.EFFORTS:
        raise config.CliError(
            config.PROFILE,
            f"BLUE_DATA_LLM_SOURCE_EFFORT is {chosen!r}; expected one of "
            f"{', '.join(config.EFFORTS)}. Run `{config.PROGRAM} profile config "
            "BLUE_DATA_LLM_SOURCE_EFFORT VALUE` to fix it.",
        )
    return chosen, resolver.source_of("BLUE_DATA_LLM_SOURCE_EFFORT")


def _refuse_keyless_effort(
    effort: str, source: str, plan_continuations: int | None, plan: dict[str, Any]
) -> None:
    """Medium and high need something to merge on, and flat mode cannot infer one.

    The message names where the effort came from, because blaming ``--effort`` for a value
    the caller never typed sends them looking at the wrong thing.
    """
    keys: tuple[str, ...] = tuple(plan.get("plan_keys") or ())
    key = plan.get("plan_key")
    has_key = bool(key and str(key).strip()) or any(str(k).strip() for k in keys)
    mode = plan.get("plan_mode")
    if (
        effort in {"medium", "high"}
        and plan_continuations is None
        and mode in {None, "flat"}
        and not has_key
    ):
        where = "--effort" if source == "flag" else f"BLUE_DATA_LLM_SOURCE_EFFORT ({source})"
        raise config.CliError(
            config.USAGE,
            f"{where}: {effort} effort needs --plan-key or --plan-keys in flat mode; add a "
            f"requested key, select a non-flat mode that can infer one, or use "
            f"--plan-continuations for explicit best-effort behavior.",
        )


_FLOORS = ("llm.max_tokens", "limits.max_rows")
"""Settings whose ``>= 1`` flag range also applies to profile and environment values."""


def _check_floors(settings: config.Settings) -> None:
    for key in _FLOORS:
        value = settings.applied.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise config.CliError(
                config.PROFILE,
                f"{key} must be a whole number of at least 1, got {value!r} "
                f"(from {settings.source[key]}).",
            )


def _columns(records: list[dict[str, Any]], requested: list[str]) -> list[str]:
    columns = list(requested)
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)
    return columns


# ------------------------------------------------------------------ #
# Prose and footers                                                   #
# ------------------------------------------------------------------ #
def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"


def _reported_model(meta: ExecMeta, absent: str) -> str:
    """The model a run reported, masked.

    The model name can contain caller-supplied text. Mask it before writing the table
    footer or prose summary; machine formats use ``meta_view``.
    """
    return redact(meta.model) if meta.model else absent


def _footer(count: int, meta: ExecMeta) -> str:
    tokens = meta.prompt_tokens + meta.completion_tokens
    return (
        f"{count} {_plural(count, 'record')} | "
        f"model {_reported_model(meta, '(none)')} | "
        f"{meta.calls} {_plural(meta.calls, 'call')} | "
        f"{tokens} tokens | "
        f"row estimate {meta.row_estimate if meta.row_estimate is not None else 'unknown'} | "
        f"${meta.cost:.4f}{'' if meta.accounting_known else ' (unknown)'} | "
        f"{meta.latency_seconds:.2f}s"
    )


def _stop_reasons(meta: ExecMeta) -> list[str]:
    return meta.stop_reasons or ([meta.stopped_by] if meta.stopped_by else [])


_COST_REMEDY = (
    "No call fit under the cost ceiling: one attempt's bound is above it. "
    "Lower --max-tokens, which sets most of that bound, or raise --limit-cost."
)


def _refused_before_any_call(meta: ExecMeta) -> bool:
    """Whether the cost ceiling refused the run before a single call was made."""
    return meta.calls == 0 and "max_cost" in _stop_reasons(meta)


def _stop_line(reason: str, meta: ExecMeta) -> str:
    """The stop axis, and for a run the cost ceiling refused outright, what to change."""
    if reason == "max_cost" and _refused_before_any_call(meta):
        return f"{reason}. {_COST_REMEDY}"
    return reason


# The same fact `nl` states in a sentence, for the outcomes that are a reason. `ok` has no
# entry: it is the default `ExecMeta` carries and asserts nothing the row count does not
# already show, so it prints no line, the way an empty gap list and an empty stop list do.
_OUTCOME_SENTENCES = {
    Outcome.EMPTY_QUERY: "The query was empty, so no model was asked.",
    Outcome.MODEL_RETURNED_NONE: "The model returned no records.",
    Outcome.FILTERED_TO_EMPTY: "Every record the model returned was filtered out.",
    Outcome.PARTIAL: "Some of the attempted work was interrupted, so this answer is partial.",
    Outcome.STOPPED_BY_LIMIT: "A ceiling stopped the run before it finished.",
    Outcome.TRUNCATED: "The records are what fits under the row cap.",
}


def _query_notes(
    records: list[dict[str, Any]], meta: ExecMeta, settings: config.Settings, verbose: bool
) -> tuple[str, ...]:
    notes = [_footer(len(records), meta)]
    # Show the outcome in table output so distinct empty-result reasons remain visible.
    if meta.outcome != Outcome.OK:
        notes.append(f"outcome: {meta.outcome}")
    # Before the verbose block and outside it: a gap is why the row count is what it is, not
    # a diagnostic about how it was produced.
    notes.extend(f"gap: {redact(gap)}" for gap in meta.gaps)
    notes.extend(f"stopped by: {_stop_line(reason, meta)}" for reason in _stop_reasons(meta))
    if verbose:
        notes.extend(_verbose_lines(meta))
    notes.append(settings.footer())
    return tuple(notes)


def _query_sentences(
    records: list[dict[str, Any]], meta: ExecMeta, settings: config.Settings
) -> tuple[str, ...]:
    spend = f"${meta.cost:.4f}" + ("" if meta.accounting_known else " (accounting unknown)")
    sentences = [
        f"{len(records)} {_plural(len(records), 'record')} "
        f"from {_reported_model(meta, 'no model')} "
        f"in {meta.calls} {_plural(meta.calls, 'call')}, costing {spend}."
    ]
    outcome = _OUTCOME_SENTENCES.get(meta.outcome)
    if outcome:
        sentences.append(outcome)
    stops = _stop_reasons(meta)
    sentences.append(
        f"The run was stopped by {', '.join(stops)}." if stops else "Nothing stopped the run early."
    )
    if _refused_before_any_call(meta):
        sentences.append(_COST_REMEDY)
    sentences.append(settings.ceilings())
    return tuple(sentences)


def _estimate_sentences(
    published: dict[str, Any], settings: config.Settings, ceiling: object = None
) -> tuple[str, ...]:
    if not published["bounded"]:
        reason = str(published["reason"] or "no reason was given").rstrip(".")
        return (f"This request cannot be bounded: {reason}.", settings.ceilings())
    fit = _ceiling_sentence(published, ceiling)
    return (
        f"One flat fetch of this request costs at most ${published['max_cost']:.4f} against "
        f"{published['model']}, across {published['max_attempts']} attempt(s).",
        *((fit,) if fit else ()),
        "Non-flat requests may make planning and extraction calls with different prompts; "
        "this figure does not bound their total cost.",
        settings.ceilings(),
    )


def _ceiling_sentence(published: dict[str, Any], ceiling: object) -> str | None:
    """Whether the cost ceiling admits the attempts this estimate covers, and if not, why."""
    fits = published.get("attempts_within_ceiling")
    if fits is None or not isinstance(ceiling, (int, float)):
        return None
    if fits == 0 and published["max_attempts"] > 0:
        needed, limit = _distinct_amounts(float(published["max_cost_per_attempt"]), float(ceiling))
        return (
            f"The first attempt needs ${needed}, above the ${limit} ceiling; lower "
            f"--max-tokens or raise the ceiling."
        )
    return f"At this ceiling {fits} of {published['max_attempts']} attempts fit."


def _distinct_amounts(first: float, second: float) -> tuple[str, str]:
    """Two dollar amounts written with as many digits as it takes to tell them apart.

    Four fixed decimals printed a $0.000041 bound against a $0 ceiling as $0.0000 against
    $0.0000, which reads as a refusal for no reason.
    """

    def written(amount: float, digits: int) -> str:
        if amount == 0:
            return "0"
        places = max(4, digits - 1 - math.floor(math.log10(abs(amount))))
        text = f"{amount:.{places}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    for digits in range(3, 18):
        pair = written(first, digits), written(second, digits)
        if pair[0] != pair[1]:
            return pair
    return repr(first), repr(second)


def _flattened(values: dict[str, Any]) -> dict[str, Any]:
    """One row per value for the table formats, with a nested mapping spelled as dotted keys."""
    rows: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, dict):
            for inner, item in cast("dict[str, Any]", value).items():
                rows[f"{key}.{inner}"] = (
                    ", ".join(cast("list[str]", item)) if isinstance(item, list) else item
                )
        else:
            rows[key] = value
    return rows


def _model_sentence(name: str, rates: tuple[float, float] | None, allowance: int) -> str:
    if rates is None:
        return (
            f"{name} has no published price here, so a cost ceiling needs --price-in and "
            f"--price-out."
        )
    entry = entry_for(name)
    # The bound carries the regional uplift for the models subject to it, so the floor does.
    floor = rates[1] * allowance * (entry.regional_uplift if entry is not None else 1.0)
    return (
        f"{name} is priced at {rates[0]:.10g} per input token and {rates[1]:.10g} per output "
        f"token, so a cost ceiling has to clear about ${floor:.4f} at {allowance} output tokens."
    )


def _verbose_lines(meta: ExecMeta) -> list[str]:
    lines: list[str] = []
    if meta.primary_keys:
        lines.append("primary keys: " + ", ".join(redact(key) for key in meta.primary_keys))
    lines.append(f"observed complete keys: {meta.observed_key_count}")
    if meta.incomplete_key_records:
        lines.append(f"incomplete-key records: {meta.incomplete_key_records}")
    if meta.normalization_collisions:
        lines.append(f"normalization collisions: {len(meta.normalization_collisions)}")
    if meta.unchecked_types:
        # Masked the way `meta_view` masks it for `--format json`, so the caller's own text
        # is not quoted back differently depending on the format asked for.
        lines.append(
            "types this library cannot check: "
            + ", ".join(redact(name) for name in meta.unchecked_types)
        )
    if meta.unmapped_keys:
        lines.append("unmapped keys: " + ", ".join(meta.unmapped_keys))
    if meta.remapped_keys:
        pairs = ", ".join(f"{k} -> {v}" for k, v in meta.remapped_keys.items())
        lines.append("remapped keys: " + pairs)
    if meta.truncated_to is not None:
        lines.append(f"truncated to: {meta.truncated_to}")
    if meta.cache_hits:
        lines.append(f"cache hits: {meta.cache_hits}")
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
    return [redact(line) for line in lines]


__all__ = ["cli"]
