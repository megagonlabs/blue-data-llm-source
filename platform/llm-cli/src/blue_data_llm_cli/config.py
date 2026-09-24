"""Option translation and the CLI's own vocabulary: errors, tiers, progress.

Every mapping between what the user typed and what the library accepts lives here, so
``commands.py`` is argument declaration and printing and nothing else.

Three things beyond translation belong here because they are one decision each, and a
decision made twice is a decision that drifts:

*The error vocabulary.* ``ERROR: <CODE> <description>`` on stderr, one line, and an exit
code a script can branch on without parsing the message. Click's own parse failures are
converted to the same shape by the group and command classes below, so a caller sees one
format whether the CLI refused the flag or the library refused the value. Under
``--format json`` the same failure is an object on stdout, from the moment Click has parsed
the arguments: the parse itself is the only thing outside that, because until it finishes
there is no ``--format`` to honour.

*The tier a value came from.* The resolver owns environment, profile and default; a command
option sits above all three and never reaches the resolver at all. :class:`Settings` records
both the value and its tier as each option is read, which is what the ``settings`` envelope
publishes and what the table footer prints.

*Where the not-result goes.* Progress, logs and warnings are stderr; stdout carries the
result. :class:`Reporter` is the only thing that writes to stderr during a run, and it stays
quiet under ``--format json`` and ``--format csv`` where a spinner would otherwise be the
one thing standing between a caller and a clean pipe.
"""

from __future__ import annotations

import contextlib
import json
import os
import traceback
from collections.abc import Generator, Mapping, Sequence
from importlib import metadata
from pathlib import Path
from typing import IO, Any, cast

import click
from blue_data_llm_source import (
    Attr,
    BudgetExceeded,
    ConfigError,
    Limits,
    LlmSource,
    LlmSourceError,
    ParseError,
    QueryError,
    TransportError,
    redact,
)
from blue_data_llm_source.llm import DEFAULT_MODEL
from rich.console import Console

from blue_data_llm_cli import __version__
from blue_data_llm_cli.configstore import ConfigResolver, StoreError
from blue_data_llm_cli.formats import FORMATS, printable

PROGRAM = "blue-llmdb"
CLI_HOME_DEFAULT = "~/.blue-llmdb"
HOME_VARIABLE = "BLUE_DATA_LLM_SOURCE_HOME"

# --------------------------------------------------------------- exit codes --
# 2 belongs to Click, which uses it for usage errors, so the library errors start at 3. A
# caller can branch on the cause without parsing the message.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_QUERY = 3
EXIT_CONFIG = 4
EXIT_PROFILE = 4
EXIT_TRANSPORT = 5
EXIT_PARSE = 6
EXIT_BUDGET = 7
EXIT_PARTIAL = 8
"""Extraction returned an answered result after some work was interrupted.

Its own code rather than 7, which names a budget ceiling, or 0, which would make an
interrupted result look complete. A split leaf can fail beside an answered sibling, and a
flat leaf can retain records parsed before a late continuation failure."""

USAGE = "USAGE"
QUERY = "QUERY"
CONFIG = "CONFIG"
PROFILE = "PROFILE"
TRANSPORT = "TRANSPORT"
PARSE = "PARSE"
BUDGET = "BUDGET"
PARTIAL = "PARTIAL"
INTERNAL = "INTERNAL"

EXIT_FOR_CODE = {
    USAGE: EXIT_USAGE,
    QUERY: EXIT_QUERY,
    # Two codes on one exit status, told apart by the token. CONFIG is a library setting the
    # source rejected; PROFILE is a profile operation this CLI rejected. They share a status
    # because a script branching on "your configuration is wrong" wants one branch.
    CONFIG: EXIT_CONFIG,
    PROFILE: EXIT_PROFILE,
    TRANSPORT: EXIT_TRANSPORT,
    PARSE: EXIT_PARSE,
    BUDGET: EXIT_BUDGET,
    PARTIAL: EXIT_PARTIAL,
    INTERNAL: EXIT_ERROR,
}

_LIBRARY_CODES: tuple[tuple[type[LlmSourceError], str], ...] = (
    (QueryError, QUERY),
    (ConfigError, CONFIG),
    (TransportError, TRANSPORT),
    (ParseError, PARSE),
    (BudgetExceeded, BUDGET),
)

RATE_LIMIT_REASON = (
    "client-side call spacing has to reconcile with per-attempt budget admission, and this "
    "package does not do it. Cap concurrency with --max-parallel, or throttle above this "
    "command where the whole request sequence is visible."
)


class CliError(click.ClickException):
    """One line on stderr: ``ERROR: <CODE> <description>``, and an exit code to branch on."""

    def __init__(self, code: str, message: str) -> None:
        # Masked once, at construction, rather than at each of the two places an error is
        # written. A message is built out of a library message, a provider SDK's exception
        # text and whatever the caller typed, and it leaves here by two doors -- the stderr
        # line and the JSON envelope -- which is one door too many to mask at.
        super().__init__(redact(message))
        self.code = code

    @property
    def exit_code(self) -> int:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Click reads this to decide the process status; the code decides it here."""
        return EXIT_FOR_CODE[self.code]

    def format_message(self) -> str:
        return self.message

    def show(self, file: IO[Any] | None = None) -> None:
        # Stripped like any other output, and already masked by the constructor. The message
        # carries a profile name someone typed and a library message that can quote what a
        # model returned, and this is the last path that reaches a terminal.
        click.echo(f"ERROR: {self.code} {printable(self.message)}", err=True)

    def envelope(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


def code_for(error: LlmSourceError) -> str:
    for kind, code in _LIBRARY_CODES:
        if isinstance(error, kind):
            return code
    return INTERNAL


def exit_code_for(error: LlmSourceError) -> int:
    return EXIT_FOR_CODE[code_for(error)]


def _remedy_for(error: LlmSourceError) -> str:
    """What to do instead, where this CLI can know it.

    Only where it can. The library's messages name library settings and the caller typed
    flags, so translating the two cases the CLI is sure about is worth more than guessing at
    the rest.
    """
    text = str(error).lower()
    if isinstance(error, TransportError) and any(
        hint in text for hint in ("credential", "api key", "api_key", "authentication")
    ):
        return f" Set the key with `{PROGRAM} profile config OPENAI_API_KEY`."
    if isinstance(error, ConfigError) and "no published prices" in text:
        return " From this command those are --price-in, --price-out and --limit-cost."
    return ""


def cli_error_for(error: LlmSourceError) -> CliError:
    return CliError(code_for(error), f"{type(error).__name__}: {error}{_remedy_for(error)}")


def as_usage(error: click.UsageError, hint: str = "") -> CliError:
    """A Click parse failure in this CLI's own error shape.

    This is the one failure outside the JSON error contract, and the boundary is the parse
    itself: until Click has read the arguments there is no ``--format`` to honour, so a
    parse failure is always plain text on stderr and always exit 2. Everything the CLI does
    after that — including the root callback's own work, which :func:`start` re-raises from
    inside the command body — is published in the requested format. A machine consumer
    wanting structured errors for bad flags validates its own flags; a CLI cannot do that
    for them without reimplementing Click's parser.
    """
    where = f" Try '{error.ctx.command_path} --help'." if error.ctx is not None else ""
    return CliError(USAGE, f"{error.format_message()}{hint}{where}")


class Command(click.Command):
    """A command whose parse failures speak the same error vocabulary as everything else."""

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: click.Context | None = None,
        **extra: Any,
    ) -> click.Context:
        try:
            return super().make_context(info_name, args, parent, **extra)
        except click.UsageError as error:
            raise as_usage(error, self.usage_hint(error)) from None

    def usage_hint(self, error: click.UsageError) -> str:
        """Extra guidance for a parse failure. Empty unless a subclass knows better."""
        return ""


class Group(click.Group):
    """The same, for a group and for the subcommands and subgroups it creates."""

    command_class = Command
    # `type` means "the class of the group this was created from", so every level down the
    # tree keeps the error shape without being told to.
    group_class = type

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: click.Context | None = None,
        **extra: Any,
    ) -> click.Context:
        try:
            return super().make_context(info_name, args, parent, **extra)
        except click.UsageError as error:
            raise as_usage(error) from None

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except click.UsageError as error:
            raise as_usage(error) from None


class Output:
    """The format an error is published in, which the run refines as it learns it.

    A command knows three things about its format at three different moments: what
    ``--format`` said, which Click has parsed before the body starts; what the profile's
    ``BLUE_DATA_CLI_FORMAT`` says, which needs the store read; and nothing at all, if
    reading the store is what failed. Holding it in one mutable object is what lets the
    prologue publish a failure in the best format known when it happens rather than in the
    one it was going to use.
    """

    def __init__(self, requested: str | None = None) -> None:
        self.requested = requested
        self.value = requested or "table"

    def resolve(self, resolver: ConfigResolver) -> str:
        """Settle on the profile's default where no flag was given, and record it."""
        self.value = resolve_format(resolver, self.requested)
        return self.value


def failure_for(error: Exception) -> CliError:
    """This CLI's error shape for anything a run raises, wherever it was raised."""
    if isinstance(error, CliError):
        return error
    if isinstance(error, LlmSourceError):
        return cli_error_for(error)
    if isinstance(error, StoreError):
        return CliError(PROFILE, str(error))
    # Everything this CLI has not classified. Left to the interpreter it printed the whole
    # chain, provider SDK frames included, on a path that has no `--log` to ask for that;
    # the type name says what happened and the traceback stays behind --log.
    return CliError(INTERNAL, f"{type(error).__name__}: {error}")


def start(obj: Mapping[str, Any]) -> ConfigResolver:
    """Raise what the root callback could not, and hand over the resolver it built.

    The callback runs before Click has parsed the subcommand's arguments, so a failure
    reported from there has no ``--format`` to honour. It holds the failure instead, and
    every command raises it here — first thing, inside the reporting context — where the
    requested format is known.
    """
    failure = obj.get("failure")
    if isinstance(failure, CliError):
        raise failure
    return cast("ConfigResolver", obj["config"])


@contextlib.contextmanager
def reporting(fmt: str | Output, log: bool = False) -> Generator[None, None, None]:
    """Turn what a command body raises into this CLI's error contract.

    Under ``--format json`` the error is also written to stdout as
    ``{"error": {"code": ..., "message": ...}}``, so a machine consumer reading stdout gets
    an object either way. Click prints the stderr line regardless.
    """
    published = fmt if isinstance(fmt, Output) else Output(fmt)
    try:
        yield
    except CliError as error:
        _publish(published.value, error)
        raise
    except (click.Abort, click.exceptions.Exit, click.ClickException):
        # Click's own control flow: an abort, an early exit, and a parse failure the group
        # converts to this CLI's shape one level up. None of them is a failure to report.
        raise
    except Exception as error:
        failure = failure_for(error)
        if log:
            _trace()
        _publish(published.value, failure)
        raise failure from None


def _trace() -> None:
    """The failing traceback on stderr, masked.

    Formatted and then written rather than handed to ``traceback.print_exc``, which writes
    straight to the stream: these frames quote a provider SDK's own exception, and this is
    the one output path where nothing else stands between that text and the terminal.
    """
    click.echo(redact(traceback.format_exc()), err=True, nl=False)


def _publish(fmt: str, error: CliError) -> None:
    if fmt == "json":
        click.echo(json.dumps(error.envelope(), indent=2))


# ---------------------------------------------------------------- the tiers --
class Settings:
    """What this invocation resolved and passed to the library, and where each came from.

    Its scope is exactly that, and no more. It is **not** a report of the library's
    effective configuration: that resolution is three layers deep and private, and claiming
    to report it would be claiming knowledge the CLI does not have. What the CLI knows is
    what it was given and what it passed on, which is the thing a caller sizing a ceiling
    actually needs.

    Keys are the library's own dotted names, so the same spelling appears in the CLI, in the
    profile file and in the MCP schema.
    """

    def __init__(self, resolver: ConfigResolver) -> None:
        self._resolver = resolver
        self.applied: dict[str, Any] = {}
        self.source: dict[str, str] = {}

    def take(self, key: str, option: Any, parameter: str | None = None) -> Any:
        """The winning value for *key*, recorded with its tier.

        A command option wins outright, because it never reaches the resolver: that is the
        top of the precedence chain and the reason ``profile inspect`` cannot report a
        ``flag`` tier. A property this invocation did not set is recorded nowhere.
        """
        if option is not None:
            self.applied[key] = option
            self.source[key] = "flag"
            return option
        if parameter is None:
            return None
        found = self._resolver.resolve(parameter)
        if found.value is None:
            return None
        self.applied[key] = found.value
        self.source[key] = found.source
        return found.value

    def envelope(self) -> dict[str, Any]:
        return {
            "applied": {key: _published(key, value) for key, value in self.applied.items()},
            "source": dict(self.source),
        }

    def footer(self) -> str:
        pairs = ", ".join(
            f"{key}={_published(key, self.applied[key])} ({self.source[key]})"
            for key in sorted(self.applied)
        )
        return f"settings: {pairs}" if pairs else "settings: (none)"

    def ceilings(self) -> str:
        """The ceilings only: a prose answer should not read as a configuration dump."""
        bounds = sorted(key for key in self.applied if key.startswith("limits."))
        if not bounds:
            return "No ceiling was set."
        named = ", ".join(f"{key.split('.', 1)[1]} {self.applied[key]}" for key in bounds)
        return f"Ceilings applied: {named}."


MASKED_SETTINGS = frozenset({"llm.model"})
"""Applied settings whose value is masked wherever the envelope is published.

The model name only. It is the one setting a run also echoes back through the metadata, and
it is masked there for a reason that applies here too: a deployment that pasted a credential
into it gets the value printed in the default table footer. Nothing else is masked, because
the rule reads shape and a path is high-entropy by construction — masking ``llm.cache_dir``
turned a cache directory into ``***`` on an ordinary machine, which is the footer failing at
the one thing it is for.
"""


def _published(key: str, value: Any) -> Any:
    """One applied value as it may leave.

    Masked where the envelope is published rather than where the value is taken, so what
    reaches the library stays exactly what the caller asked for.
    """
    return redact(value) if key in MASKED_SETTINGS and isinstance(value, str) else value


# -------------------------------------------------------------- the streams --
def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 3, 0)] + "..."


class Reporter:
    """Everything that is not the result: the spinner, the log lines, the warnings.

    All of it on stderr, so every format is safe to pipe. The spinner is suppressed outright
    under the machine formats rather than left to Rich's own terminal detection: both
    sibling CLIs rely on that detection and both corrupt their JSON under ``--log``, which
    is the same bug one layer up.
    """

    def __init__(self, fmt: str = "table", log: bool = False, log_limit: int = 80) -> None:
        self.console = Console(stderr=True, markup=False, highlight=False)
        self.machine_readable = fmt in {"json", "csv"}
        self.log = log
        self.log_limit = log_limit

    def status(self, label: str) -> Any:
        if self.machine_readable or not self.console.is_terminal:
            return contextlib.nullcontext()
        return self.console.status(label)

    def line(self, text: str) -> None:
        """One step of this run, under ``--log``."""
        if self.log:
            # Stripped and masked like any other cell. A log line carries the query the user
            # typed and the outcome the library reported, and it is the one output path that
            # would otherwise reach a terminal unsanitised. Masked before the truncation,
            # not after: cutting a value in half leaves a fragment that matches nothing.
            self.console.print(
                printable(truncate(redact(text), self.log_limit)), style="dim", soft_wrap=True
            )

    def warn(self, text: str) -> None:
        self.console.print(printable(redact(text)), style="yellow", soft_wrap=True)


def format_option(command: Any) -> Any:
    """`--format`, declared once rather than on twelve commands.

    Every command that returns a result carries it, and every one of them accepts all four
    values. Re-declaring the option per command is how a CLI ends up with a format one
    command has never heard of — both siblings do it thirty-odd times each.
    """
    return click.option(
        "--format",
        "output_format",
        type=click.Choice(FORMATS),
        default=None,
        show_default="the profile's BLUE_DATA_CLI_FORMAT, else table",
        help="Output format.",
    )(command)


def resolve_format(resolver: ConfigResolver, option: str | None) -> str:
    """The flag, else the profile's ``BLUE_DATA_CLI_FORMAT``, else ``table``."""
    if option:
        return option
    configured = _resolved(resolver, "BLUE_DATA_CLI_FORMAT")
    if not configured:
        return "table"
    if configured not in FORMATS:
        raise CliError(
            PROFILE,
            f"BLUE_DATA_CLI_FORMAT is {configured!r}; expected one of {', '.join(FORMATS)}",
        )
    return str(configured)


def resolve_log_limit(resolver: ConfigResolver, option: int | None) -> int:
    """The flag, else the profile's ``BLUE_DATA_CLI_LOG_LIMIT``, else 80.

    ``is None`` rather than a truth test: a configured ``0`` is a limit of zero, and reading
    it as "unset" gave the same value two meanings depending on which tier supplied it.
    """
    if option is not None:
        return option
    configured = _resolved(resolver, "BLUE_DATA_CLI_LOG_LIMIT")
    return 80 if configured is None else int(configured)


def _resolved(resolver: ConfigResolver, parameter: str) -> Any:
    """One parameter, with a cast failure reported rather than raised as a traceback.

    These two are read before the reporting context exists, because they decide what that
    context is, so they carry their own handler.
    """
    try:
        return resolver.get(parameter)
    except StoreError as error:
        raise CliError(PROFILE, str(error)) from None


# ----------------------------------------------------------------- the home --
def cli_home() -> str:
    """Where this CLI keeps its own state: the profiles and the selection.

    Deliberately not a profile parameter — it is the directory the profiles themselves are
    read from, so it can only come from the environment.
    """
    configured = os.environ.get(HOME_VARIABLE)
    if configured is not None and not configured.strip():
        # `Path("")` is the working directory, so an empty export quietly moved the whole
        # profile store to wherever the command was run from and wrote a `.profiles` there.
        raise CliError(
            PROFILE,
            f"{HOME_VARIABLE} is exported with an empty value, which would put the profile "
            f"store in the working directory. Unset it, or give it a path.",
        )
    return str(Path(configured or CLI_HOME_DEFAULT).expanduser())


def cli_version() -> str:
    """The version ``--version`` prints.

    The installed distribution is the truth where there is one. A source checkout has no
    distribution to ask and the package literal is what the build reads, so the two agree by
    construction, and a test pins both against ``pyproject.toml``.
    """
    try:
        return metadata.version("blue-data-llm-cli")
    except metadata.PackageNotFoundError:
        return __version__


# ------------------------------------------------------- option translation --
_EFFORT_CONTINUATIONS = {"low": 0, "medium": 1, "high": 2}
EFFORTS = tuple(_EFFORT_CONTINUATIONS)


def continuations_for_effort(effort: str) -> int:
    """The continuation allowance for a CLI effort level.

    A CLI convenience over ``plan.max_continuations`` rather than library policy: the
    library has no notion of effort, and an explicit ``--plan-continuations`` wins.
    """
    try:
        return _EFFORT_CONTINUATIONS[effort]
    except KeyError as error:
        choices = ", ".join(_EFFORT_CONTINUATIONS)
        raise ValueError(f"unknown effort {effort!r}; expected one of {choices}") from error


def parse_attr(value: str) -> Attr:
    """Parse one ``--attr`` value, in either ``NAME`` or ``NAME:TYPE`` form."""
    name, _, declared = value.partition(":")
    name = name.strip()
    declared = declared.strip()
    if not name:
        raise ValueError(f"{value!r} has no attribute name; use NAME or NAME:TYPE")
    return Attr(name=name, type=declared or None)


def build_attrs(values: Sequence[str]) -> list[Attr]:
    """Parse every ``--attr`` value in the order they were given."""
    return [parse_attr(value) for value in values]


def build_limits(
    limit_cost: float | None = None,
    limit_seconds: float | None = None,
    limit_calls: int | None = None,
    max_retries: int | None = None,
) -> Limits:
    """Collect the ceiling options into the library's Limits.

    An unset ``max_retries`` is left to the dataclass default rather than passed as None:
    the library's default is 2, and None is not a retry allowance.
    """
    if max_retries is None:
        return Limits(max_cost=limit_cost, max_seconds=limit_seconds, max_calls=limit_calls)
    return Limits(
        max_cost=limit_cost,
        max_seconds=limit_seconds,
        max_calls=limit_calls,
        max_retries=max_retries,
    )


def limit_properties(limits: Limits) -> dict[str, Any]:
    """Render Limits as the ``limits.*`` property keys the source reads."""
    out: dict[str, Any] = {}
    if limits.max_rows is not None:
        out["limits.max_rows"] = limits.max_rows
    if limits.max_seconds is not None:
        out["limits.max_seconds"] = limits.max_seconds
    if limits.max_cost is not None:
        out["limits.max_cost"] = limits.max_cost
    if limits.max_calls is not None:
        out["limits.max_calls"] = limits.max_calls
    if limits.max_retries != Limits().max_retries:
        out["limits.max_retries"] = limits.max_retries
    return out


def default_max_tokens() -> int:
    """The output allowance a request gets when nobody sets one.

    Read from a bare source rather than written down again here: it is the library's number,
    and a second copy of it is a second thing to get wrong when the library changes its mind.
    """
    return int(cast("int", LlmSource().max_tokens_for_model(DEFAULT_MODEL)))


def plan_properties(
    plan_mode: str | None = None,
    plan_continuations: int | None = None,
    plan_key: str | None = None,
    plan_keys: Sequence[str] = (),
    plan_exact_rows: int | None = None,
    plan_exclusion_bytes: int | None = None,
    plan_leaves: int | None = None,
    plan_partition_key: str | None = None,
    plan_depth: int | None = None,
    plan_partitions: int | None = None,
    plan_target_rows: int | None = None,
    plan_schema_mode: str | None = None,
    plan_planning_order: str | None = None,
    plan_expose_estimate: bool | None = None,
) -> dict[str, Any]:
    """The `plan.*` settings, under the names the library reads.

    Passed through rather than translated, so a value refused here is refused with the
    library's own message naming the setting the caller wrote.
    """
    out: dict[str, Any] = {}
    if plan_mode is not None:
        out["plan.mode"] = plan_mode
    if plan_continuations is not None:
        out["plan.max_continuations"] = plan_continuations
    if plan_key is not None:
        out["plan.key"] = plan_key
    if plan_keys:
        out["plan.keys"] = list(plan_keys)
    if plan_exact_rows is not None:
        out["plan.exact_rows"] = plan_exact_rows
    if plan_exclusion_bytes is not None:
        out["plan.max_exclusion_bytes"] = plan_exclusion_bytes
    if plan_leaves is not None:
        out["plan.max_leaves"] = plan_leaves
    if plan_partition_key is not None:
        out["plan.partition_key"] = plan_partition_key
    if plan_depth is not None:
        out["plan.max_depth"] = plan_depth
    if plan_partitions is not None:
        out["plan.max_partitions"] = plan_partitions
    if plan_target_rows is not None:
        out["plan.target_rows_per_leaf"] = plan_target_rows
    if plan_schema_mode is not None:
        out["plan.schema_mode"] = plan_schema_mode
    if plan_planning_order is not None:
        out["plan.planning_order"] = plan_planning_order
    if plan_expose_estimate is not None:
        out["plan.expose_estimate"] = plan_expose_estimate
    return out


def build_source(variant: str | None = None) -> LlmSource:
    """Construct the source a command runs against.

    Tests replace this to inject a client, which is the only seam they need. The variant is
    a connection field rather than an ``llm.*`` override, so it is set on the source rather
    than passed with the call.
    """
    if variant:
        return LlmSource(properties={"connection": {"variant": variant}})
    return LlmSource()
