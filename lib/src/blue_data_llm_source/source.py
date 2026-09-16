"""LlmSource: a language model presented as a data source."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Generator
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Final, cast

from blue_data.source.source import DataSource  # pyright: ignore[reportMissingTypeStubs]

from blue_data_llm_source import _constants as C
from blue_data_llm_source import checks, prompts
from blue_data_llm_source.execution.local import execution_settings, run_local_leaf
from blue_data_llm_source.execution.mapper import Answer, LeafRun, arun_leaf
from blue_data_llm_source.llm import pricing
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.llm.client import (
    DEFAULT_MODEL,
    DefaultLlmClient,
    FunctionClient,
    check_adapter_keys,
)
from blue_data_llm_source.planning.bounds import (
    MODE_FLAT,
    MODE_PARTITIONED,
    MODES,
    PlanLimits,
)
from blue_data_llm_source.planning.cardinality import Cardinality
from blue_data_llm_source.planning.context import (
    PLANNING_SCHEMA_FIRST,
    validate_planning_order,
)
from blue_data_llm_source.planning.coordinator import coordinate_plan
from blue_data_llm_source.planning.partition_safety import (
    CALLER_DECLARED,
    FINITE_DOMAIN,
    RULE_VERSION,
    SOURCE_CONFIGURATION,
    IdentityPartitionRule,
)
from blue_data_llm_source.planning.plan import Gap, build_plan
from blue_data_llm_source.planning.receipt import PlanningReceipt
from blue_data_llm_source.planning.schema import (
    KEY_FROM_CALLER,
    KEY_FROM_NONE,
    SCHEMA_ENRICH,
    SCHEMA_MODES,
    SCHEMA_PRESERVE,
    Schema,
)
from blue_data_llm_source.planning.transport import LeafTask
from blue_data_llm_source.reduction import aligner, verifier
from blue_data_llm_source.reduction.coverage import FAILED, LeafInput, LeafOutcome, reduce_leaves
from blue_data_llm_source.reduction.identity import key_identity, normalized_value
from blue_data_llm_source.types import (
    STOPPED_BY_CONTINUATIONS,
    STOPPED_BY_INCOMPLETE_RESPONSE,
    STOPPED_BY_OUTPUT,
    Attr,
    BudgetAwareClient,
    BudgetExceeded,
    Capability,
    ConfigError,
    Estimate,
    ExecMeta,
    FetchResult,
    LlmSourceError,
    OneOf,
    Outcome,
    ParseError,
    QueryError,
    TransportError,
    Usage,
)


def _declared(client: Any) -> frozenset[str]:
    """What this client says it can be held to. Empty for one that says nothing."""
    if not isinstance(client, BudgetAwareClient):
        return frozenset()
    reported = client.budget_capabilities()
    return frozenset(reported) if reported else frozenset()


# The closed set of limit knobs, so a misspelling is refused rather than ignored.
_LIMIT_KEYS = frozenset(f.name for f in fields(Limits))

_PLAN_LIMIT_KEYS = frozenset(
    {
        "max_leaves",
        "max_depth",
        "max_partitions",
        "target_rows_per_leaf",
        "max_continuations",
        "max_excluded_keys",
        "max_exclusion_bytes",
        "max_strategy_candidates",
    }
)
"""The `PlanLimits` fields a `plan.*` setting can reach."""

# `mode`, `key` and `partition_key` are not PlanLimits fields and have to be named, or the
# validator refuses a key the facade reads — two halves of one setting disagreeing, which is
# the shape this closed key set exists to catch.
_PLAN_KEYS = _PLAN_LIMIT_KEYS | {
    "mode",
    "key",
    "keys",
    "exact_rows",
    "partition_key",
    "schema_mode",
    "planning_order",
    "expose_estimate",
    "distribution_search",
    "identity_partitions",
}
"""The `plan.*` namespace, held apart from `limits.*` rather than merged into it.

Written out rather than read off :class:`PlanLimits`, which is the wider object. Deriving
the set from its fields accepted every field it has, including four this release's pipeline
never reaches: two that only feed the full-design worst case, a threshold compared against
an ambiguity score no shipped path produces, and a retry count that duplicates the one the
client actually retries on. A setting accepted, echoed back in the error message as
available, and then unable to change any outcome is worse than one refused — the caller
sets it, reads the default's behaviour, and has nothing to tell them why.

Retries are the clearest case. Both objects carry a `max_retries` and they are not the same
setting: `limits.max_retries` bounds the attempts inside a single call and is what the
budget is sized against, while the `PlanLimits` field sizes a recommendation nothing on this
path asks for. Offering both here is how a caller sets one and moves the other. A library
caller holding :class:`PlanLimits` directly still has every field; what closes is the
settings door, which cannot deliver them."""

_STOP_REASON_PRECEDENCE: Final = (
    "max_seconds",
    "max_cost",
    "max_calls",
    STOPPED_BY_OUTPUT,
    STOPPED_BY_INCOMPLETE_RESPONSE,
    "max_rows",
    STOPPED_BY_CONTINUATIONS,
)


def _ordered_distinct(*groups: list[str]) -> list[str]:
    """Distinct stop reasons in the public precedence order."""
    seen: set[str] = set()
    observed: list[str] = []
    for group in groups:
        for reason in group:
            if reason and reason not in seen:
                seen.add(reason)
                observed.append(reason)
    return [reason for reason in _STOP_REASON_PRECEDENCE if reason in seen] + [
        reason for reason in observed if reason not in _STOP_REASON_PRECEDENCE
    ]


def _run_stop_reasons(runs: list[Any]) -> list[str]:
    """Every extraction limit observed across the leaves."""
    reasons: list[str] = []
    for run in runs:
        if run.stopped_by:
            reasons.append(cast("str", run.stopped_by))
        if run.provider_output_limit:
            reasons.append(STOPPED_BY_OUTPUT)
        if run.recovered_prefix:
            reasons.append(STOPPED_BY_INCOMPLETE_RESPONSE)
        if run.at_cap:
            reasons.append(STOPPED_BY_CONTINUATIONS)
    return _ordered_distinct(reasons)


def _execution_failure(runs: list[Any], stop_reasons: list[str]) -> LlmSourceError:
    """A fresh package exception for a run with no answered extraction response."""
    failures = [run.exception for run in runs if run.ordinary_failure and run.exception is not None]
    cause = failures[0]
    limit_text = f"; limits also stopped work: {', '.join(stop_reasons)}" if stop_reasons else ""
    generic_message = (
        f"extraction failed without an answered response: {type(cause).__name__}: {cause}"
        f"{limit_text}"
    )
    compatible_message = f"{cause}{limit_text}"
    error: LlmSourceError
    if isinstance(cause, ParseError):
        error = ParseError(compatible_message, raw=cause.raw)
    elif isinstance(cause, TransportError):
        error = TransportError(
            compatible_message,
            code=cause.code,
            provider_error=cause.provider_error,
        )
    elif isinstance(cause, ConfigError):
        error = ConfigError(compatible_message)
    else:
        error = LlmSourceError(generic_message)
    error.__cause__ = cause
    return error


def _gap_strings(notes: list[Any], leaf_count: int, extra: list[str]) -> list[str]:
    """Small, deterministic diagnostics from planning, execution, and final shaping."""
    gaps = list(extra)
    gaps.extend(
        f"{note.code}: {note.detail}"
        for note in notes
        if note.code != Gap.ESTIMATE_UNKNOWN
        and not (leaf_count == 1 and note.code == Gap.DEDUP_KEY_ABSENT)
    )
    return list(dict.fromkeys(gaps))


def decide_outcome(
    records: list[dict[str, Any]],
    model_returned: list[dict[str, Any]],
    row_cap_fired: bool,
    stopped_by: str | None,
    *,
    answered: bool | None = None,
    interrupted: bool = False,
    stop_reasons: list[str] | None = None,
) -> str:
    """Classify a fetch from its final records and observed execution facts.

    The four positional arguments retain the original flat-fetch contract. Planning-aware
    callers also supply whether an extraction answered, whether work was interrupted, and
    every stop reason so a retained partial answer outranks a simultaneous limit.
    """
    answered = bool(model_returned) if answered is None else answered
    reasons = _ordered_distinct(
        stop_reasons or [],
        [stopped_by] if stopped_by else [],
        ["max_rows"] if row_cap_fired else [],
    )
    if interrupted and (answered or records):
        return Outcome.PARTIAL
    if any(reason != "max_rows" for reason in reasons):
        return Outcome.STOPPED_BY_LIMIT
    if row_cap_fired:
        return Outcome.TRUNCATED if records else Outcome.STOPPED_BY_LIMIT
    if records:
        return Outcome.OK
    if not model_returned:
        return Outcome.MODEL_RETURNED_NONE
    return Outcome.FILTERED_TO_EMPTY


@contextlib.contextmanager
def _in_hierarchy() -> Generator[None]:
    """Convert anything escaping a public entry point into the documented hierarchy.

    Four documents promise every error this package raises is an ``LlmSourceError``, and the
    CLI's exit-code table and the MCP server's error format are both built on that promise.
    It was nevertheless maintained by per-site discipline across dozens of call sites, with
    no chokepoint anywhere — so it held everywhere someone remembered and failed everywhere
    they did not: an oversized literal raised ``OverflowError`` out of coercion, a deeply
    nested response raised ``RecursionError``, a corrupt cache file raised
    ``UnicodeDecodeError``, an exhausted descriptor table raised ``sqlite3.OperationalError``,
    and a caller-supplied cache store raised whatever it liked.

    Patching each site would leave the promise resting on the same discipline that already
    failed. This makes it structural; the per-site guards remain as defence in depth, and
    where one of them classifies an error better than this can, it still wins by running
    first.

    ``BaseException`` is deliberately not caught: a cancellation, a ``KeyboardInterrupt`` or
    a ``SystemExit`` is not this package's to reclassify, and swallowing one would break the
    reservation lifecycle that depends on those unwinding cleanly.
    """
    try:
        yield
    except LlmSourceError:
        raise
    except Exception as e:
        # Chained, never replaced: the original type and traceback are what a maintainer
        # needs, and the wrapper is what a caller's `except LlmSourceError` needs.
        raise LlmSourceError(f"{type(e).__name__}: {e}") from e


def _origins_for(
    origins: list[list[str]], before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[list[str]]:
    """Carry *origins* from the *before* list onto the records that survived into *after*.

    Matched by identity for the same reason :func:`_renumber_by_record` is: two records can
    be equal and only one of them survives, and equality would attribute the survivor to the
    wrong part. Alignment and verification are one-for-one and order-preserving, so a list
    parallel to the merged records is still parallel to what they produce; the passes that
    change the list's length are the ones this follows.
    """
    # First index wins, and unseen identities are consumed as they are matched. A dict built
    # the other way keeps only the *last* index for a repeated object, so the same dictionary
    # appearing twice in `before` would attribute both copies to the second one's origin and
    # lose the first. JSON parsing builds distinct objects, so no public path reaches that
    # today; a helper whose docstring is unrestricted and whose behaviour is not is how the
    # next caller gets surprised.
    at: dict[int, list[int]] = {}
    for index, record in enumerate(before):
        at.setdefault(id(record), []).append(index)
    out: list[list[str]] = []
    for record in after:
        queue = at.get(id(record))
        index = queue.pop(0) if queue else None
        out.append(list(origins[index]) if index is not None and index < len(origins) else [])
    return out


def _renumber_by_record(
    mismatches: list[dict[str, Any]], before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Point each mismatch at the record it describes in the returned list.

    Matched by identity rather than equality, because two records can be equal and only one
    of them survives deduplication — equality would move a mismatch onto the wrong one.
    """
    position = {id(record): index for index, record in enumerate(after)}
    out: list[dict[str, Any]] = []
    for mismatch in mismatches:
        index = mismatch.get("record_index")
        if not isinstance(index, int) or not 0 <= index < len(before):
            continue
        moved = position.get(id(before[index]))
        if moved is None:
            # Its record was dropped as empty, deduplicated away, or cut by the row cap.
            continue
        out.append({**mismatch, "record_index": moved})
    return out


# Above this, the loop stops reproducing the ledger and multiplies instead. The loop exists
# so the published figure accumulates in the same order the ledger will, and float addition
# makes that differ from multiplication in the last bits — but no run makes ten thousand
# attempts, and `estimate` is documented as contacting no provider, so an unbounded loop
# turned a pre-flight check into minutes of arithmetic for a large `limits.max_retries`.
_LEDGER_FIDELITY_LIMIT = 10_000


def _reported_allowance(max_tokens: object) -> int:
    """The output allowance the cost bound actually reserved.

    `isinstance(max_tokens, int)` reported 0 for a float allowance while pricing accepted it
    and reserved the whole thing, so the published estimate disagreed with the reservation
    it describes. The predicate pricing uses is the one that decides, so it is the one asked.
    """
    allowance = pricing.positive_finite(max_tokens)
    return int(allowance) if allowance is not None else 0


def _accumulated(per_attempt: float, attempts: int) -> float:
    """The total a ledger reaches by adding *per_attempt* one attempt at a time."""
    if attempts > _LEDGER_FIDELITY_LIMIT:
        return per_attempt * attempts
    total = 0.0
    for _ in range(attempts):
        total += per_attempt
    return total


# The base class's tool set is relational — databases, collections, columns, row samples,
# foreign keys — and four of its eight tools answer usefully from a source whose only
# knowledge is a model's. Of the rest, three return an empty result with nothing said, and
# `get_sample` raises a bare TypeError out of the base dispatcher itself, which passes
# `row_sample_size` to a `fetch_entity_stats` that has no such parameter. Advertising a tool
# that answers with silence is the defect this package exists to prevent, so what is
# advertised is these four.
_SERVED_NL_TOOLS = frozenset({"list_databases", "list_collections", "get_schema", "execute_query"})


@dataclass(frozen=True)
class _DistributionControls:
    enabled: bool
    rules: tuple[IdentityPartitionRule, ...]


# Keys a tool schema can require that `execute_nl_tool` also takes as arguments. The base
# dispatcher reads each from the tool input and falls back to the argument, so a call
# supplying them the second way is complete and must not be refused as incomplete.
_TOOL_KEYS_FROM_ARGUMENTS = frozenset({"database", "collection"})


def _missing_tool_input(tool: dict[str, Any], tool_input: dict[str, Any]) -> list[str]:
    """Required keys this call did not supply, read off the advertised schema.

    Listing them here instead would be a second copy of the schema the caller was handed, and
    the copy is what drifts. The check is worth making because the base dispatcher indexes a
    required key directly — ``tool_input["query"]`` — so an absent one otherwise arrives as a
    ``KeyError`` naming a dictionary key, and the caller here is a model that sees only the
    message text.
    """
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        return []
    required = cast("dict[str, Any]", schema).get("required")
    if not isinstance(required, list):
        return []
    return [
        str(key)
        for key in cast("list[Any]", required)
        if key not in _TOOL_KEYS_FROM_ARGUMENTS and key not in tool_input
    ]


class LlmSource(DataSource):
    """Fetch structured records from a model's own knowledge.

    Two surfaces. ``fetch`` is the library API, and it returns a :class:`FetchResult`
    carrying metadata the data layer's signatures have nowhere to put. ``execute_query``
    and ``execute_query_nl`` are the data layer's own, inherited from ``DataSource`` and
    overridden here, so this class registers and answers like any other source.

    Base-class order matters and is not cosmetic. ``DataSource`` defines every method this
    class also defines. Most of its implementations return an empty result, so anything left
    unoverridden silently answers nothing rather than failing; the transaction methods raise
    instead, and the orchestrator-tool methods carry behaviour this class narrows to the part
    it can answer. The contract test asserts that the methods this source must answer resolve
    here.
    """

    def __init__(
        self,
        name: str = "llm",
        properties: dict[str, Any] | None = None,
        client: Any | None = None,
    ) -> None:
        self.name = name
        self.properties: dict[str, Any] = {}
        self._initialize_properties()
        self._update_properties(properties)
        self._client = client

    # ------------------------------------------------------------ properties --
    def _initialize_properties(self) -> None:
        # The provider defaults the source this replaces documented, carried forward so
        # behaviour does not silently change with a provider's own defaults. Every one is
        # overridable through properties.
        defaults = {
            "llm.model": DEFAULT_MODEL,
            "llm.max_tokens": 4096,
            "llm.temperature": 0,
            "llm.stream": False,
        }
        connection = {
            "protocol": C.PROTOCOL,
            "variant": C.DEFAULT_VARIANT,
            "host": "",
            "port": None,
            "user": "",
            "password": "",
        }
        self.properties = {"connection": connection, **defaults}
        # Which of the above are this source's defaults rather than a caller's choice. An
        # injected client carries its own configuration, and these must not overwrite it:
        # the source's `gpt-4o` replaced a client's private model while that client's rate
        # overrides survived, so a call was priced at one model's rates and made against
        # another, and a cost ceiling was crossed by the difference.
        self._defaulted = set(defaults)

    def _resolved_properties(self, per_call: dict[str, Any] | None) -> dict[str, Any]:
        """Three layers, in the order a caller would expect them to win.

        Source defaults first, then whatever an injected client was constructed with, then
        the caller's own — on the source or with the call. Flattening the first two let a
        default overwrite a client's deliberate configuration while the rest of that
        configuration survived, which is how a call came to be priced at one model's rates
        and made against another.
        """
        chosen = {k: v for k, v in self.properties.items() if k not in self._defaulted}
        defaults = {k: v for k, v in self.properties.items() if k in self._defaulted}
        client_own: Any = getattr(self._client, "properties", None)
        from_client: dict[str, Any] = (
            dict(cast("dict[str, Any]", client_own)) if isinstance(client_own, dict) else {}
        )
        resolved = {**defaults, **from_client, **chosen, **(per_call or {})}
        # Validated here, on the result, rather than only on what the caller passed. An
        # injected client's own properties reached the provider without ever crossing the
        # closed boundary: `llm.stream=True` was forwarded instead of refused by name, and
        # `llm.max_parallel=0` built a semaphore with no permits and waited.
        self._check_limit_keys(resolved)
        check_adapter_keys(resolved)
        return resolved

    # The default matches the base class, which gained one upstream. Without it this override
    # is incompatible: a caller holding a `DataSource` may call it with no argument, and ours
    # would raise where the base class accepts it.
    def _update_properties(self, properties: dict[str, Any] | None = None) -> None:
        if not properties:
            return
        # Anything the caller sets is theirs, not a default, and outranks a client's own.
        self._defaulted -= set(properties)
        for key, value in properties.items():
            if key == "connection" and isinstance(value, dict):
                self.properties["connection"].update(value)
            else:
                self.properties[key] = value
        # Protocol names the source family and is not caller-settable. Variant names
        # the implementation within it and is.
        self.properties["connection"]["protocol"] = C.PROTOCOL

    @property
    def protocol(self) -> str:
        return str(self.properties["connection"]["protocol"])

    @property
    def variant(self) -> str:
        return str(self.properties["connection"].get("variant", C.DEFAULT_VARIANT))

    def _limits(self, overrides: dict[str, Any] | None = None) -> Limits:
        # Built from the same three-layer map every other setting is read from. Merging only
        # this source's properties meant a ceiling an injected client carried in its own
        # configuration was validated and then never entered the budget: a client configured
        # with `limits.max_calls: 0` reached the provider, and `estimate` published three
        # attempts for a client configured for one.
        merged = self._resolved_properties(overrides)
        return Limits(
            max_rows=merged.get("limits.max_rows"),
            max_seconds=merged.get("limits.max_seconds"),
            max_cost=merged.get("limits.max_cost"),
            max_calls=merged.get("limits.max_calls"),
            # Unconverted, so Limits validates it the way it validates every other
            # field. int() here turned True into 1 and "abc" into a bare ValueError.
            max_retries=merged.get("limits.max_retries", 2),
        )

    @staticmethod
    def _plan_limits(merged: dict[str, Any]) -> PlanLimits:
        """The plan shape for this request, read from the same three-layer property map.

        Read here rather than defaulted at each seam, so a caller's `plan.max_leaves` is the
        one the budget is sized against and the one the planner honours."""
        # `_PLAN_KEYS` rather than every PlanLimits field, so this reads exactly what the
        # validator accepts. Two different answers to "is this a plan setting" is how a key
        # gets refused at the door and honoured inside, or the reverse.
        given = {
            key[len("plan.") :]: value
            for key, value in merged.items()
            if key.startswith("plan.") and key[len("plan.") :] in _PLAN_LIMIT_KEYS
        }
        return PlanLimits(**given)

    @staticmethod
    def _declared_keys(merged: dict[str, Any], wanted: list[Attr]) -> tuple[str, ...]:
        """Read ordered ``plan.keys`` and the one-cycle singular alias."""
        raw_keys = merged.get("plan.keys")
        alias = merged.get("plan.key")
        if raw_keys is None:
            keys = (alias,) if alias is not None else ()
        else:
            if not isinstance(raw_keys, (list, tuple)):
                raise ConfigError(f"plan.keys must be a list of strings, got {raw_keys!r}")
            keys = tuple(cast("list[Any] | tuple[Any, ...]", raw_keys))
            if alias is not None and keys != (alias,):
                raise ConfigError("plan.keys and plan.key disagree")
        if any(not isinstance(key, str) or not key.strip() for key in keys):
            raise ConfigError(f"plan.keys must contain non-empty strings, got {keys!r}")
        cleaned = tuple(cast("str", key).strip() for key in keys)
        if len(set(cleaned)) != len(cleaned):
            raise ConfigError("plan.keys must not contain duplicates")
        names = {attr.name for attr in wanted}
        missing = [key for key in cleaned if key not in names]
        if wanted and missing:
            raise ConfigError(f"plan.keys {missing!r} are not among the requested attributes")
        return cleaned

    @staticmethod
    def _exact_rows(merged: dict[str, Any]) -> int | None:
        value = merged.get("plan.exact_rows")
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"plan.exact_rows must be a non-negative integer, got {value!r}")
        return value

    def _distribution_controls(
        self,
        merged: dict[str, Any],
        per_call: dict[str, Any] | None,
        wanted: list[Attr],
    ) -> _DistributionControls:
        enabled = merged.get("plan.distribution_search", False)
        if not isinstance(enabled, bool):
            raise ConfigError("plan.distribution_search must be a boolean")
        if enabled and merged.get("plan.mode", MODE_FLAT) != MODE_PARTITIONED:
            raise ConfigError("plan.distribution_search requires plan.mode='partitioned'")

        max_candidates = merged.get(
            "plan.max_strategy_candidates",
            PlanLimits().max_strategy_candidates,
        )
        if (
            isinstance(max_candidates, bool)
            or not isinstance(max_candidates, int)
            or max_candidates < 0
        ):
            raise ConfigError("plan.max_strategy_candidates must be a non-negative integer")
        if enabled and max_candidates < 1:
            raise ConfigError(
                "plan.max_strategy_candidates must be at least 1 when distribution search "
                "is enabled"
            )

        client_properties = getattr(self._client, "properties", None)
        if isinstance(client_properties, dict) and "plan.identity_partitions" in client_properties:
            raise ConfigError(
                "plan.identity_partitions cannot come from an injected provider client"
            )

        per_call_rules = per_call is not None and "plan.identity_partitions" in per_call
        if per_call_rules:
            raw_rules = cast("dict[str, Any]", per_call)["plan.identity_partitions"]
            expected_provenance = CALLER_DECLARED
        else:
            raw_rules = self.properties.get("plan.identity_partitions", [])
            expected_provenance = SOURCE_CONFIGURATION
        if not isinstance(raw_rules, list):
            raise ConfigError("plan.identity_partitions must be a list")

        rules: list[IdentityPartitionRule] = []
        declared_types = {attr.name: attr.type for attr in wanted}
        for raw_rule in cast("list[object]", raw_rules):
            rule = IdentityPartitionRule.from_dict(raw_rule)
            if rule.contract_version != RULE_VERSION:
                raise ConfigError("identity partition rule contract version is unsupported")
            if rule.kind != FINITE_DOMAIN:
                raise ConfigError("identity partition rule kind is unsupported")
            if rule.provenance != expected_provenance:
                raise ConfigError(
                    f"identity partition rule provenance must be {expected_provenance!r} "
                    "for its configuration origin"
                )
            identities = [
                normalized_value(value, declared_types.get(rule.dimension))
                for value in rule.allowed_domain
            ]
            if len(set(identities)) != len(identities):
                raise ConfigError("identity partition rule domain contains duplicate values")
            rules.append(rule)

        if len({rule.digest for rule in rules}) != len(rules):
            raise ConfigError("plan.identity_partitions must not contain duplicate rules")

        raw_keys = merged.get("plan.keys")
        if not enabled and raw_keys is None:
            return _DistributionControls(enabled=False, rules=tuple(rules))
        if raw_keys is None:
            raise ConfigError("plan.keys is required when distribution search is enabled")
        keys = self._declared_keys(merged, wanted)
        for rule in rules:
            if tuple(rule.ordered_primary_keys) != keys:
                raise ConfigError(
                    "every identity partition rule must match the ordered plan.keys exactly"
                )
            if rule.dimension not in keys:
                raise ConfigError("identity partition rule dimension must be one of plan.keys")
        if enabled and not any(rule.applies_to({}) for rule in rules):
            raise ConfigError(
                "distribution search requires at least one identity partition rule for the root"
            )
        return _DistributionControls(enabled=enabled, rules=tuple(rules))

    async def _run_leaves(
        self,
        plan: Any,
        make_ask: Any,
        wanted: list[Attr],
        plan_limits: PlanLimits,
        schema: Schema,
        ctx: str,
        mode: str,
        client: object,
        properties: dict[str, Any],
        budget: Budget,
        exact_rows: int | None,
        sinks: list[list[Usage]] | None = None,
    ) -> list[Any]:
        """Every leaf of the plan, concurrently, bounded by the client's own parallelism.

        A leaf that raises comes back as a failed result rather than an exception: its
        siblings have already been paid for, and letting one propagate discards their rows
        to report a failure that belongs to one of them.

        Every leaf, including the ones that raise. ``gather`` without ``return_exceptions``
        hands the first exception back the moment it is raised and leaves the siblings
        running: on the async surface the caller is returned to with money committed and not
        yet settled, reads its own budget, and sees a figure that keeps moving after the
        call it asked about has finished. Collecting every result first costs the latency of
        the slowest sibling and makes the spend the caller reads final.

        ``arun_leaf`` classifies the failures it knows. One it does not — a recursion or
        memory failure out of the parser rather than out of the model — would otherwise be
        the one class of leaf exception this method's own contract does not cover, so it is
        wrapped here into the same failed result. A request whose leaves all failed still
        raises: that decision belongs downstream, where the whole plan is known."""
        # A receipt list per leaf, not one for the run. Leaves execute concurrently below,
        # so a shared sink records which calls happened and not which leaf made them — and
        # anything a leaf has to conclude from its own last call then reads whichever entry
        # arrived most recently, from any leaf. The run total is unchanged: the caller sees
        # these folded together afterwards.
        per_leaf: list[list[Usage]] = [[] for _ in plan.leaves]
        if sinks is not None:
            sinks.extend(per_leaf)
        if mode == MODE_FLAT:
            tasks = [
                arun_leaf(
                    leaf,
                    make_ask(sink),
                    attrs=wanted,
                    limits=plan_limits,
                    primary_keys=schema.primary_keys,
                    exact_rows=exact_rows,
                    context=ctx,
                )
                for leaf, sink in zip(plan.leaves, per_leaf, strict=True)
            ]
        else:
            settings = execution_settings(properties, plan_limits, budget, exact_rows=exact_rows)
            tasks = [
                run_local_leaf(
                    LeafTask.from_leaf(
                        leaf,
                        caller_context=ctx,
                        schema=schema,
                        settings=settings,
                    ),
                    client=client,
                    properties=properties,
                    usage_sink=sink,
                )
                for leaf, sink in zip(plan.leaves, per_leaf, strict=True)
            ]
        settled = await asyncio.gather(*tasks, return_exceptions=True)

        runs: list[Any] = []
        for leaf, result in zip(plan.leaves, settled, strict=True):
            if not isinstance(result, BaseException):
                runs.append(result)
            elif isinstance(result, asyncio.CancelledError):
                # Cancellation is the caller's own decision arriving, not this leaf's
                # outcome. Reporting it as a failed leaf would swallow a shutdown.
                raise result
            else:
                runs.append(
                    LeafRun(
                        leaf_id=leaf.id,
                        outcome=Outcome.STOPPED_BY_LIMIT,
                        stopped_by=result.limit if isinstance(result, BudgetExceeded) else None,
                        error=f"{type(result).__name__}: {result}",
                        exception=result,
                    )
                )
        return runs

    @staticmethod
    def _attests(client: Any) -> bool:
        """Whether this client can vouch for the calls, cost and tokens it reports.

        Read from the client that actually answered, not from which argument the caller
        used. Deciding it from ``llm_fn is None`` meant the same opaque callable reported
        its accounting as unknown through ``llm_fn=`` and as known through ``client=``, and
        an injected client implementing only the required pair reported zero calls and zero
        cost as measured fact.

        A client that declares it can be held to the call and cost ceilings is a client that
        knows what it spent; one that does not, does not. That is the same declaration the
        ceilings already rely on, so there is nothing new for an implementer to get wrong.
        """
        declared = getattr(client, "budget_capabilities", None)
        if declared is None:
            return False
        try:
            capabilities = declared()
        except Exception:  # pragma: no cover - a client that cannot answer, cannot attest
            return False
        return Capability.CALLS in capabilities and Capability.COST in capabilities

    def _resolve_client(
        self,
        llm_fn: Any = None,
        budget: Budget | None = None,
        default_properties: dict[str, Any] | None = None,
    ) -> Any:
        if llm_fn is not None:
            return FunctionClient(llm_fn, budget=budget)
        if self._client is not None:
            return self._client
        return DefaultLlmClient(
            self.properties if default_properties is None else default_properties,
            budget=budget,
        )

    @staticmethod
    def _require_client_shape(client: Any) -> None:
        """Refuse a client that cannot answer, with a message naming the contract.

        Checked at resolution rather than left to the first call, where the failure arrived
        as a raw ``TypeError`` or ``AttributeError`` outside this library's error hierarchy
        and told a caller what broke instead of what to implement.
        """
        for name in ("complete", "acomplete"):
            if not callable(getattr(client, name, None)):
                raise ConfigError(
                    f"the supplied client does not implement {name}(prompt, properties). "
                    f"A client needs complete(prompt, properties) and "
                    f"acomplete(prompt, properties), and may declare budget_capabilities() "
                    f"to be held to a provider-level ceiling."
                )
        for name in ("complete", "acomplete"):
            method = getattr(client, name)
            try:
                signature = inspect.signature(method)
            except (TypeError, ValueError):  # pragma: no cover - exotic callables
                continue
            try:
                # Bound the way the source actually calls it, rather than counted. Counting
                # parameters accepted `acomplete(prompt, *, properties=None)` and
                # `acomplete(prompt, **kwargs)`, which cannot take a second positional
                # argument and so still failed with the raw TypeError this replaces.
                signature.bind("prompt", {})
            except TypeError as e:
                raise ConfigError(
                    f"the supplied client's {name}{signature} cannot be called as "
                    f"{name}(prompt, properties), which is how this source invokes it. "
                    f"properties carries the run's budget and the usage sink, so a client "
                    f"that cannot receive it positionally cannot be bounded."
                ) from e

    @staticmethod
    def _require_bindable(client: Any, budget: Budget) -> None:
        """Refuse a ceiling this client cannot be held to, per axis.

        Capability is declared by the client, not inferred from its shape. Inferring it
        from an attribute named ``budget`` accepted a client carrying ``budget = None``,
        which then made unbounded calls under a configured ceiling — a limit that was
        present, accepted, and enforcing nothing.

        The axes differ in what is provable. This source counts records itself, so rows
        always bind. Everything else depends on the client: one that drives the provider
        can be held to calls, time and cost, while a wrapper around a caller's function can
        prove only that it invoked the function once.
        """
        declared = _declared(client)

        # The whole chain, not this fetch's own limits. A ceiling carried only by a
        # caller's budget is still a ceiling, and reading the child alone let a parent-only
        # max_cost reach a client that had never claimed it could hold one.
        configured = budget.configured_axes()
        unbindable = [
            name
            for name, axis in (
                ("limits.max_calls", Capability.CALLS),
                ("limits.max_cost", Capability.COST),
                ("limits.max_seconds", Capability.SECONDS),
            )
            if axis in configured and axis not in declared
        ]
        if not unbindable:
            return
        raise ConfigError(
            f"{', '.join(unbindable)} cannot be enforced against this client. It declares "
            f"{sorted(declared) or 'no budget capabilities'}, and a ceiling this source "
            f"cannot hold is worse than no ceiling because a caller believes it. Use a "
            f"client that enforces those axes, drop those limits, or use limits.max_rows, "
            f"which this source enforces itself."
        )

    @staticmethod
    def _check_limit_keys(properties: dict[str, Any]) -> None:
        """Refuse a misspelled limit rather than running unbounded under it.

        ``limits.max_cst`` was accepted and ignored, so a run that looked bounded was not.
        That is the defect class this package exists to prevent, and the adapter keys are
        already checked the same way.
        """
        # The adapter keys are a closed set too, and are checked here for the same reason:
        # only the built-in client reads them, so validating there let a typo through on
        # every other path.
        check_adapter_keys(properties)
        unknown = sorted(
            key
            for key in properties
            if key.startswith("limits.") and key[len("limits.") :] not in _LIMIT_KEYS
        )
        # The plan namespace is checked the same way and for the same reason: a misspelled
        # `plan.max_leaves` is a plan shape the caller chose and never got, which reads to
        # them as the planner disagreeing rather than as a typo.
        unknown += sorted(
            key
            for key in properties
            if key.startswith("plan.") and key[len("plan.") :] not in _PLAN_KEYS
        )
        near_misses = sorted(
            key for key in properties if key.startswith("limit.") or key in ("limits", "plan")
        )
        if not unknown and not near_misses:
            return
        raise ConfigError(
            f"unknown limit {', '.join(repr(k) for k in unknown + near_misses)}. "
            f"Accepted: {sorted('limits.' + name for name in _LIMIT_KEYS)} and "
            f"{sorted('plan.' + name for name in _PLAN_KEYS)}"
        )

    # ------------------------------------------------------------------ fetch --
    async def _afetch_inner(
        self,
        # Any rather than str: an absent or wrong-typed query is a caller mistake this
        # method is specified to handle, returning empty for the first and raising a
        # readable QueryError for the second. Annotating str would describe a narrower
        # contract than the one the tests pin.
        query: Any,
        attrs: Any = None,
        context: Any = None,
        max_records: int | None = None,
        properties: dict[str, Any] | None = None,
        llm_fn: Any = None,
        budget: Budget | None = None,
    ) -> FetchResult:
        """Fetch records for *query*.

        A *budget* supplied by the caller accumulates across every call it is handed to,
        which is how a session or a planner bounds a whole unit of work rather than each
        request separately. Omitted, one is built for this call from the configured limits.
        """
        started = time.monotonic()
        wanted: list[Attr] = checks.normalize_attrs(attrs)
        ctx = checks.normalize_context(context)
        cap = checks.require_positive_int(max_records, "max_records")

        # An empty query is an empty answer, matching how the other sources in this layer
        # treat an absent result, and matching the behaviour this source replaces. The
        # reason is reported rather than lost: the caller can tell "you asked nothing"
        # from "the model knows nothing".
        # Before the empty-query short circuit: a closed key set that only applies to
        # requests that reach the model is not a closed key set, and an empty request is
        # the cheapest way to validate configuration without spending anything.
        limits = self._limits(properties)
        merged = self._resolved_properties(properties)
        distribution = self._distribution_controls(merged, properties, wanted)

        text = checks.normalize_query(query)
        if not text:
            return FetchResult(
                records=[],
                meta=ExecMeta(
                    outcome=Outcome.EMPTY_QUERY,
                    requested_attrs=[a.name for a in wanted],
                    latency_seconds=time.monotonic() - started,
                ),
            )

        if cap is not None:
            limits = Limits(
                max_rows=cap,
                max_seconds=limits.max_seconds,
                max_cost=limits.max_cost,
                max_calls=limits.max_calls,
                max_retries=limits.max_retries,
            )
        # A caller's budget accumulates across every call it is handed to, so this fetch
        # draws a child from it rather than spending it directly: the configured limits
        # apply to this call, and never beyond what the caller has left.
        run_budget = Budget(limits) if budget is None else budget.derive(limits)
        provider_properties = dict(merged)
        provider_properties.pop("plan.distribution_search", None)
        provider_properties.pop("plan.identity_partitions", None)
        provider_properties.pop("plan.max_strategy_candidates", None)
        client = self._resolve_client(
            llm_fn,
            run_budget,
            default_properties=provider_properties,
        )
        self._require_client_shape(client)
        self._require_bindable(client, run_budget)

        # Handed down per call rather than set on the client: one client can serve several
        # concurrent fetches, and an attribute would let them overwrite each other's ceiling.
        provider_properties[C.BUDGET_PROPERTY] = run_budget

        plan_limits = self._plan_limits(merged)
        mode = str(merged.get("plan.mode", MODE_FLAT))
        if mode not in MODES:
            raise ConfigError(f"unknown plan.mode {mode!r}; expected one of {', '.join(MODES)}")

        meta = ExecMeta(requested_attrs=[a.name for a in wanted])

        # Token attribution comes from the entries this call is handed back, not from
        # slicing the client's shared history: two concurrent fetches through one reused
        # client read the same cursor and each take both entries, so each reports the
        # other's tokens. The client writes this call's usage into a list of its own.
        call_usages: list[Usage] = []
        provider_properties[C.USAGE_SINK_PROPERTY] = call_usages

        declared_keys = self._declared_keys(merged, wanted)
        exact_rows = self._exact_rows(merged)
        declared_schema = Schema(
            attrs=wanted,
            primary_keys=declared_keys,
            key_from=KEY_FROM_CALLER if declared_keys else KEY_FROM_NONE,
        )
        planning_gaps: list[str] = []
        schema_mode = merged.get("plan.schema_mode", SCHEMA_PRESERVE)
        if schema_mode not in SCHEMA_MODES:
            raise ConfigError(
                f"unknown plan.schema_mode {schema_mode!r}; expected one of "
                f"{', '.join(SCHEMA_MODES)}"
            )
        if mode == MODE_FLAT and schema_mode == SCHEMA_ENRICH:
            raise ConfigError("flat mode cannot enrich schema without a planning call")
        planning_order = validate_planning_order(
            merged.get("plan.planning_order", PLANNING_SCHEMA_FIRST)
        )
        expose_estimate = merged.get("plan.expose_estimate", True)
        if not isinstance(expose_estimate, bool):
            raise ConfigError("plan.expose_estimate must be a boolean")

        planning_receipt: PlanningReceipt | None = None
        if mode == MODE_FLAT:
            schema = declared_schema
            plan = build_plan(
                mode=MODE_FLAT,
                ask=text,
                constraints={},
                schema=schema,
                estimate=Cardinality.UNKNOWN,
                limits=plan_limits,
            )
        else:
            partition_key = merged.get("plan.partition_key")
            if partition_key is not None and (
                not isinstance(partition_key, str) or not partition_key.strip()
            ):
                raise ConfigError(
                    f"plan.partition_key must be a non-empty string, got {partition_key!r}"
                )
            try:
                bundle = await coordinate_plan(
                    mode=mode,
                    original_query=text,
                    caller_context=ctx,
                    caller_constraints={},
                    declared_schema=declared_schema,
                    schema_mode=cast("str", schema_mode),
                    limits=plan_limits,
                    client=client,
                    budget=run_budget,
                    properties=provider_properties,
                    partition_key=partition_key,
                    planning_order=planning_order,
                    distribution_search=distribution.enabled,
                    identity_partition_rules=distribution.rules,
                )
            except BudgetExceeded as e:
                meta.outcome = Outcome.STOPPED_BY_LIMIT
                meta.stop_reasons = _ordered_distinct([e.limit])
                meta.stopped_by = meta.stop_reasons[0]
                self._fold_usage(call_usages, meta, run_budget, self._attests(client))
                meta.gaps = [f"planning_unserved: {e}"]
                meta.latency_seconds = time.monotonic() - started
                return FetchResult(records=[], meta=meta)
            plan = bundle.plan
            schema = bundle.schema
            if distribution.enabled:
                planning_receipt = PlanningReceipt(
                    configured_rule_digests=tuple(rule.digest for rule in distribution.rules),
                    facts=bundle.facts,
                    plan=bundle.plan,
                )
            meta.row_estimate = bundle.analysis.row_estimate
            meta.cardinality_estimate = bundle.analysis.cardinality.to_dict()
            planning_gaps = list(bundle.diagnostics)
            if any(usage.finish_reason == "length" for usage in bundle.usages):
                planning_gaps.append(
                    "planning_output_limit: the provider ended a discarded planning "
                    "response at its output limit"
                )
            if bundle.analysis.ask.strip() != text.strip():
                meta.asked = bundle.analysis.ask

        if any(
            isinstance(value, OneOf) and not value.values
            for leaf in plan.leaves
            for value in leaf.constraints.values()
        ):
            meta.outcome = Outcome.MODEL_RETURNED_NONE
            meta.gaps = list(planning_gaps)
            self._fold_usage(call_usages, meta, run_budget, self._attests(client))
            meta.latency_seconds = time.monotonic() - started
            result = FetchResult(records=[], meta=meta)
            return (
                result.with_planning_receipt(planning_receipt)
                if planning_receipt is not None
                else result
            )

        execution_limits = plan_limits
        meta.primary_keys = list(schema.primary_keys)
        if not schema.primary_keys:
            execution_limits = replace(
                plan_limits,
                max_continuations=0,
                empty_leaf_retry=False,
            )
            if plan_limits.max_continuations:
                planning_gaps.append(
                    "key_unavailable: continuation was disabled because no identifying "
                    "column was established"
                )

        def make_ask(sink: list[Usage]) -> Any:
            """A model boundary that writes its receipts where this leaf can read them."""
            props = {**provider_properties, C.USAGE_SINK_PROPERTY: sink}

            async def ask(prompt: str) -> Answer:
                # Where this call's receipts begin. Reading `sink[-1]` afterwards assumed
                # every call leaves exactly one entry, which the client protocol does not
                # require: a client that writes none for this call hands back the *previous*
                # call's reason, and the attestation then describes a response it was never
                # made about. Only entries appended between these two marks are this call's.
                before = len(sink)
                text = await client.acomplete(prompt, props)
                written = sink[before:]
                # The last of them on a retried call, which is the attempt that produced the
                # text being returned. None when the call wrote nothing, which is the honest
                # answer for a client that reports no accounting.
                reason = written[-1].finish_reason if written else None
                return Answer(text, finish_reason=reason)

            return ask

        leaf_usages: list[list[Usage]] = []
        leaf_exact_rows = exact_rows if len(plan.leaves) == 1 else None
        if exact_rows is not None and leaf_exact_rows is None:
            planning_gaps.append(
                "exact_rows_not_leaf_bound: trusted root count cannot be applied to separate leaves"
            )
        try:
            runs = await self._run_leaves(
                plan,
                make_ask,
                list(schema.attrs),
                execution_limits,
                schema,
                ctx,
                mode,
                client,
                provider_properties,
                run_budget,
                leaf_exact_rows,
                leaf_usages,
            )
        except BudgetExceeded as e:
            meta.stop_reasons = _ordered_distinct([e.limit])
            meta.stopped_by = meta.stop_reasons[0]
            meta.outcome = Outcome.STOPPED_BY_LIMIT
            self._fold_usage(call_usages, meta, run_budget, self._attests(client))
            meta.gaps = list(planning_gaps)
            meta.gaps.append(f"execution_unserved: {e}")
            meta.latency_seconds = time.monotonic() - started
            result = FetchResult(records=[], meta=meta)
            return (
                result.with_planning_receipt(planning_receipt)
                if planning_receipt is not None
                else result
            )

        # Folded back after the fan-out. Splitting the sink per leaf is an attribution
        # change, not an accounting one, and the totals a caller reads must not move.
        for sink in leaf_usages:
            call_usages.extend(sink)

        results: list[LeafInput] = [
            (
                run.leaf_id,
                run.records,
                LeafOutcome(
                    leaf_id=run.leaf_id,
                    outcome=(
                        FAILED
                        if run.ordinary_failure
                        else Outcome.STOPPED_BY_LIMIT
                        if run.provider_output_limit
                        else run.outcome
                    ),
                    rows=len(run.records),
                    calls=run.attempts,
                    answered=run.answered,
                    failed=run.ordinary_failure,
                    unserved=run.budget_denied,
                    at_cap=run.at_cap,
                    recovered_prefix=run.recovered_prefix,
                    provider_output_limit=run.provider_output_limit,
                    recovery_attested=run.recovery_attested,
                ),
            )
            for run in runs
        ]

        run_stop_reasons = _run_stop_reasons(runs)
        meta.observed_key_count = sum(run.observed_key_count for run in runs)
        meta.marginal_key_yields = [value for run in runs for value in run.marginal_key_yields]
        meta.incomplete_key_records = sum(run.incomplete_key_records for run in runs)
        meta.normalization_collisions = [
            collision for run in runs for collision in run.normalization_collisions
        ]
        answered_any = any(run.answered for run in runs)
        retained_any = any(run.records for run in runs)
        if any(run.ordinary_failure for run in runs) and not answered_any and not retained_any:
            self._fold_usage(call_usages, meta, run_budget, self._attests(client))
            stop_reasons = _ordered_distinct(
                run_stop_reasons,
                meta.stop_reasons,
                [meta.stopped_by] if meta.stopped_by else [],
            )
            error = _execution_failure(runs, stop_reasons)
            raise error from error.__cause__

        records, coverage = reduce_leaves(plan, schema, results)

        attrs = list(schema.attrs)
        alignment = aligner.align(records, attrs)
        checked, mismatches = verifier.verify(alignment.records, attrs)
        answered = verifier.drop_empty(checked, alignment.matched, attrs)
        # Attribution follows the records through every pass that changes the list. It was
        # computed against what the merge produced, and three passes below that one drop or
        # trim rows — publishing it unfollowed would index it against records that no longer
        # exist, which is worse than publishing nothing because it looks right.
        deduped, origins = verifier.dedupe(
            answered, origins=_origins_for(coverage.record_origins, checked, answered)
        )
        rows_before_cap = len(deduped)
        kept = run_budget.add_rows(rows_before_cap)
        row_cap_fired = kept < rows_before_cap
        result_gaps: list[str] = []
        if row_cap_fired:
            deduped = deduped[:kept]
            origins = origins[:kept]
            meta.truncated_to = kept
            result_gaps.append(
                f"result_rows_discarded: global row cap kept {kept} of {rows_before_cap} record(s)"
            )
        meta.observed_key_count = len(
            {
                identity
                for record in deduped
                if (identity := key_identity(record, schema.primary_keys, schema.attrs)) is not None
            }
        )
        meta.record_origins = origins

        # `record_index` was assigned against the list `verify` saw, and dropping, deduping
        # and capping all renumber that list — so an index published to the caller pointed at
        # a different record than the one it described, or past the end of the result.
        # Renumbered here, against the records the caller actually receives.
        meta.type_mismatches = _renumber_by_record(mismatches, checked, deduped)

        meta.observed_keys = alignment.observed_keys
        meta.remapped_keys = alignment.remapped
        meta.unmapped_keys = alignment.unmapped
        meta.key_collisions = _renumber_by_record(alignment.collisions, checked, deduped)
        meta.unchecked_types = verifier.unchecked_types(attrs)
        meta.stop_reasons = _ordered_distinct(
            run_stop_reasons,
            ["max_rows"] if row_cap_fired else [],
        )
        self._fold_usage(call_usages, meta, run_budget, self._attests(client))
        meta.stop_reasons = _ordered_distinct(
            meta.stop_reasons,
            [meta.stopped_by] if meta.stopped_by else [],
        )
        meta.stopped_by = meta.stop_reasons[0] if meta.stop_reasons else None
        interrupted = coverage.run_outcome == Outcome.PARTIAL
        meta.outcome = decide_outcome(
            deduped,
            records,
            row_cap_fired,
            meta.stopped_by,
            answered=answered_any,
            interrupted=interrupted,
            stop_reasons=meta.stop_reasons,
        )
        meta.gaps = _gap_strings(
            coverage.gaps,
            len(plan.leaves),
            [*planning_gaps, *result_gaps],
        )
        meta.latency_seconds = time.monotonic() - started
        result = FetchResult(records=deduped, meta=meta)
        return (
            result.with_planning_receipt(planning_receipt)
            if planning_receipt is not None
            else result
        )

    def estimate(
        self,
        query: Any,
        attrs: Any = None,
        context: Any = None,
        properties: dict[str, Any] | None = None,
        llm_fn: Any = None,
    ) -> Estimate:
        """The most one flat fetch of *query* could cost. See :meth:`_estimate_inner`."""
        with _in_hierarchy():
            return self._estimate_inner(
                query, attrs=attrs, context=context, properties=properties, llm_fn=llm_fn
            )

    def _estimate_inner(
        self,
        query: Any,
        attrs: Any = None,
        context: Any = None,
        properties: dict[str, Any] | None = None,
        llm_fn: Any = None,
    ) -> Estimate:
        """The most one flat fetch of *query* could cost, without contacting the provider.

        Asked of the client that would actually answer, using the same call arguments the
        attempt itself would build, so the figure cannot disagree with what the run reserves.
        Computing it separately is how an estimate came to differ from the reservation by
        three orders of magnitude for an injected client.

        **This does not price a decomposed request.** It reads no ``plan.*`` setting, so a
        plan with parts or continuations costs this figure times the calls it makes. Size a
        ceiling for a split request from its part count, or ask in ``flat`` mode, which is
        what this describes.

        A *fetch* is bounded, not an attempt. A fetch may retry, so the figure is the
        per-attempt bound accumulated once per attempt permitted, added the way the ledger
        adds it — with the shipped retry default that is three attempts' worth, and
        reporting the per-attempt figure as the fetch's meant a request sized against it
        was refused when it ran.

        Returns an unbounded :class:`Estimate` with a reason rather than raising: asking
        what a request might cost is reasonable even where the answer is unknown, and the
        honest answer to that is that it cannot be given.
        """
        # Resolved the same way a fetch resolves them, so the estimate is priced against the
        # configuration the run will actually use — including an injected client's own.
        merged = self._resolved_properties(properties)
        # The model actually configured, not the default it would fall back to. Reporting
        # the fallback alongside a reason saying none was configured is a contradiction a
        # caller has no way to resolve.
        configured = merged.get("llm.model")
        model = str(configured) if configured else ""
        # Before the empty-request short circuit, so the closed key set is closed on this
        # route too and a caller can check its configuration without spending anything.
        limits = self._limits(properties)
        # What can actually happen: the chain's retry allowance, under the call cap. The
        # allowance itself comes from the same accessor the client's retry policy and
        # admission both read, so the three agree about the quantity and differ only where
        # they are meant to.
        attempts = self._attempts_reachable(Budget(limits), limits)

        text = checks.normalize_query(query)
        if not text:
            # An empty ask never reaches the model, so it cannot cost anything.
            return Estimate(
                bounded=True,
                max_cost=0.0,
                max_cost_per_attempt=0.0,
                model=model,
                max_attempts=attempts,
            )

        wanted: list[Attr] = checks.normalize_attrs(attrs)
        ctx = checks.normalize_context(context)
        prompt = prompts.build_fetch_prompt(text, wanted, caller_context=ctx)
        prompt_bytes = len(prompt.encode("utf-8"))

        client = self._resolve_client(llm_fn, Budget(limits))
        bound_for_attempt = getattr(client, "bound_for_attempt", None)
        if not callable(bound_for_attempt) or Capability.ESTIMATE not in _declared(client):
            return Estimate(
                bounded=False,
                reason=(
                    "this client does not report what an attempt will cost, so a bound "
                    "cannot be given for it. Only a client that drives the provider itself "
                    "knows the model and rates the call will use."
                ),
                model=model,
                prompt_bytes=prompt_bytes,
                max_attempts=attempts,
            )

        max_tokens = merged.get("llm.max_tokens")
        try:
            per_attempt = float(cast("float", bound_for_attempt(prompt, merged)))
        except ConfigError as e:
            return Estimate(
                bounded=False,
                reason=str(e),
                model=model,
                prompt_bytes=prompt_bytes,
                max_attempts=attempts,
                # Known even where the price is not, so a caller does not lose it too.
                max_output_tokens=_reported_allowance(max_tokens),
            )

        return Estimate(
            bounded=True,
            # Accumulated the way the ledger will accumulate it, one attempt at a time.
            # Neither the product nor `fsum` is that: the ledger settles each charge and then
            # compares the running total against the next bound, and two-stage aggregation
            # lands an ulp above a single-shot sum. Publishing the smaller figure meant a
            # ceiling set to exactly it refused the last attempt it had accounted for.
            max_cost=_accumulated(per_attempt, attempts),
            max_cost_per_attempt=per_attempt,
            model=model,
            prompt_bytes=prompt_bytes,
            max_output_tokens=_reported_allowance(max_tokens),
            max_attempts=attempts,
        )

    @staticmethod
    def _attempts_allowed(budget: Budget) -> int:
        """How many attempts the retry allowance permits, before any other ceiling.

        ``effective_retries`` and not ``limits.max_retries``: a caller's budget accumulates
        across every call it is handed to, and a fetch derives a child from it, so the
        allowance that binds is the narrowest anywhere in the chain. Read from the request's
        own limits, this saw a retry depth a parent had already forbidden — then refused a
        request the parent could comfortably afford, reporting a call ceiling never
        approached. Adding a redundant retry setting to the request made the same request
        run, which is the clearest statement of that defect: two spellings of one budget
        disagreeing. The client builds its own retry policy from exactly this figure.
        """
        return max(1, budget.effective_retries() + 1)

    @classmethod
    def _attempts_reachable(cls, budget: Budget, limits: Limits) -> int:
        """How many attempts can actually happen, which is the allowance under the call cap.

        Held apart from :meth:`_attempts_allowed` because the two answer different
        questions, and collapsing them is wrong in one direction or the other.

        This one bounds what a run can spend, so every ceiling that clamps it belongs in it:
        a run allowed one call cannot make three attempts however many retries it is given,
        and an estimate that said otherwise would overstate what the request can cost.

        Admission asks the opposite question — whether a fan-out fits inside ``max_calls`` —
        and there the call cap is the capacity being tested, not a bound on the demand.
        Sizing the demand from it tests a number against itself and admits whatever it is
        given.
        """
        attempts = cls._attempts_allowed(budget)
        if limits.max_calls is not None:
            attempts = min(attempts, int(limits.max_calls))
        return max(0, attempts)

    async def afetch(
        self,
        query: Any,
        attrs: Any = None,
        context: Any = None,
        max_records: int | None = None,
        properties: dict[str, Any] | None = None,
        llm_fn: Any = None,
        budget: Budget | None = None,
    ) -> FetchResult:
        """Fetch records for *query*. See :meth:`_afetch_inner` for the full contract."""
        with _in_hierarchy():
            return await self._afetch_inner(
                query,
                attrs=attrs,
                context=context,
                max_records=max_records,
                properties=properties,
                llm_fn=llm_fn,
                budget=budget,
            )

    def fetch(
        self,
        query: Any,
        attrs: Any = None,
        context: Any = None,
        max_records: int | None = None,
        properties: dict[str, Any] | None = None,
        llm_fn: Any = None,
        budget: Budget | None = None,
    ) -> FetchResult:
        """Synchronous wrapper over afetch."""
        # The loop check comes before the coroutine is built. Building it first and then
        # raising would leave an un-awaited coroutine behind and emit a RuntimeWarning
        # pointing at this library rather than at the caller's mistake.
        _reject_running_loop()
        return asyncio.run(
            self.afetch(
                query,
                attrs=attrs,
                context=context,
                max_records=max_records,
                properties=properties,
                llm_fn=llm_fn,
                budget=budget,
            )
        )

    @staticmethod
    def _fold_usage(
        usages: list[Usage], meta: ExecMeta, budget: Budget, accounting_known: bool = True
    ) -> None:
        """Fold this call's accounting into the result.

        Counts and spend come from the budget, which claimed them under a lock. Token
        detail and the model name come from the usage entries this call was handed, which
        no other call can see — recovering them by slicing a client's shared history let
        two concurrent fetches each report the other's tokens.
        """
        for usage in usages:
            meta.prompt_tokens += usage.prompt_tokens
            meta.completion_tokens += usage.completion_tokens
            if usage.cache_hit:
                meta.cache_hits += 1
            if usage.model:
                meta.model = usage.model
        # Provider entries and spend, from the authority that admitted them. A contacted
        # attempt that failed produced no usage entry and still cost a call.
        meta.calls = budget.calls
        meta.cost = budget.cost
        # A callable's provider work is invisible from here, so these describe this
        # library's own bookkeeping rather than what was actually spent. Saying so is the
        # difference between "unknown" and a confident zero.
        # ...and only if every contacted attempt could actually be priced. The default
        # client is entitled to answer an unfamiliar model when no cost ceiling is
        # configured, and the provider's own accounting can fail for one that is familiar.
        # Either way the figure is a bound or a zero, not a measurement, and reporting it as
        # known makes an unpriced call look free.
        priced = all(usage.cost_known for usage in usages if not usage.cache_hit)
        meta.accounting_known = accounting_known and priced
        crossed = budget.note_exhausted()
        meta.stop_reasons = _ordered_distinct(
            meta.stop_reasons,
            [meta.stopped_by] if meta.stopped_by else [],
            [budget.stopped_by] if budget.stopped_by else [],
            [crossed] if crossed else [],
        )
        meta.stopped_by = meta.stop_reasons[0] if meta.stop_reasons else None
        if meta.stop_reasons and meta.outcome == Outcome.OK:
            meta.outcome = Outcome.STOPPED_BY_LIMIT

    # -------------------------------------------------------- source surface --
    def execute_query(
        self,
        query: Any,
        database: str | None = None,
        collection: str | None = None,
        optional_properties: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a query and return records.

        Accepts a natural-language string, or a dict carrying an ``op`` key. Supported
        ops are listed in the error raised for an unknown one.
        """
        options = dict(optional_properties or {})
        # Before the op is read, so a bad key is refused on every op rather than on the ones
        # that happen to reach a fetch. LLM.MODELS answered from local state and returned
        # before the check, which made the same ceiling accepted on one op and refused on
        # the next.
        self._check_limit_keys(options)
        check_adapter_keys(options)

        if isinstance(query, dict):
            spec = cast("dict[str, Any]", query)
            op = spec.get("op")
            if op == C.OP_MODELS:
                return [{"protocol": self.protocol, "variant": self.variant}]
            if op == C.OP_ESTIMATE:
                # Returned as a one-record list because that is what this surface returns.
                # A caller wanting the record itself calls estimate() directly.
                estimate = self.estimate(
                    spec.get("query", ""),
                    attrs=spec.get("attrs", spec.get("attr_names")),
                    context=spec.get("context"),
                    properties=options or None,
                )
                return [asdict(estimate)]
            if op != C.OP_FETCH:
                raise QueryError(f"unknown op {op!r}. Supported ops: {', '.join(C.OPS)}")
            text = spec.get("query", "")
            options.setdefault("attrs", spec.get("attrs", spec.get("attr_names")))
            options.setdefault("context", spec.get("context"))
            options.setdefault("max_records", spec.get("max_records"))
        else:
            text = query

        result = self.fetch(
            text,
            attrs=options.pop("attrs", None) or options.pop("attr_names", None),
            context=options.pop("context", None),
            max_records=options.pop("max_records", None),
            properties=options or None,
        )
        return result.records

    def execute_query_nl(
        self,
        query: str,
        database: str | None = None,
        collection: str | None = None,
        schema: dict[str, Any] | None = None,
        llm_fn: Any = None,
        optional_properties: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a natural-language query.

        For every other source ``llm_fn`` translates natural language into a native
        dialect. This source has no other dialect, so the callable is the model, and is
        used as the client for this call when one is supplied.
        """
        options = dict(optional_properties or {})
        return self.fetch(
            query,
            attrs=options.pop("attrs", None) or options.pop("attr_names", None),
            context=options.pop("context", None),
            max_records=options.pop("max_records", None),
            properties=options or None,
            llm_fn=llm_fn,
        ).records

    def translate_nl_to_native_query(
        self, query: str, schema: dict[str, Any] | None, llm_fn: Any
    ) -> str | None:
        """Natural language is this source's native form, so this is the identity."""
        return query or None

    # -------------------------------------------------------- the tool path --
    # The shared base class grew a tool-calling interface for orchestrators: a source
    # advertises tools, and a caller invokes them one at a time. It is built around the
    # relational shape the rest of the data layer has — databases, collections, columns,
    # samples, foreign keys — and a source answering from a model's own knowledge has only
    # part of that. `_SERVED_NL_TOOLS` records which of the eight it can answer and what the
    # others do instead.
    #
    # Both methods below lean on the base rather than reimplementing it. The advertisement is
    # the base's own definitions filtered by name, so a published schema cannot drift from the
    # dispatcher that reads it, and the dispatch is the base's own. Filtering is also the safe
    # direction to fail in: a tool added upstream stays unadvertised until someone here has
    # checked it answers, and one renamed upstream drops out of the advertisement rather than
    # being advertised and then failing when called.
    #
    # `execute_query_nl_tools` is the exception and stays refused. It drives a loop of up to
    # ten provider calls that this source's ceilings never see, so serving it would put an
    # unbounded spend path behind a method nobody here chose to offer.

    def get_nl_tools(
        self, database: str | None = None, collection: str | None = None
    ) -> list[dict[str, Any]]:
        """The base class's tool definitions, less the ones this source cannot answer."""
        offered = cast(
            "list[dict[str, Any]]",
            super().get_nl_tools(  # pyright: ignore[reportUnknownMemberType]
                database=database, collection=collection
            ),
        )
        return [tool for tool in offered if tool.get("name") in _SERVED_NL_TOOLS]

    def execute_nl_tool(
        self,
        name: str,
        tool_input: dict[str, Any],
        database: str | None = None,
        collection: str | None = None,
        optional_properties: dict[str, Any] | None = None,
    ) -> Any:
        """Run one advertised tool, or refuse by name.

        The served set is read from :meth:`get_nl_tools`, so what is advertised and what runs
        cannot diverge. ``execute_query`` is the one tool here that reaches the provider, and
        it takes its ceilings the way the query surface does: ``limits.*`` in
        *optional_properties*, which the base dispatcher hands on unchanged.
        """
        # Everything is inside the chokepoint, not only the delegation. A name that is not a
        # string, a tool input that is not a mapping, and a required key the dispatcher
        # indexes directly all leave here as an LlmSourceError, which is what four documents
        # promise and what an orchestrating model's `except` clause is written against.
        with _in_hierarchy():
            served = {str(tool["name"]): tool for tool in self.get_nl_tools(database, collection)}
            if name not in served:
                raise ConfigError(
                    f"{name!r} is not a tool this source serves. Available: "
                    f"{', '.join(sorted(served))}. Use execute_query_nl for the "
                    f"natural-language surface"
                )
            # Checked for every tool rather than only the one that spends, for the reason
            # execute_query checks it before reading the op: a ceiling key accepted on one
            # call and refused on the next is a ceiling a caller believes they have set.
            self._check_limit_keys(dict(optional_properties or {}))
            missing = _missing_tool_input(served[name], tool_input)
            if missing:
                raise QueryError(
                    f"{name} requires {', '.join(missing)}, and this call supplied "
                    f"{sorted(tool_input) or 'nothing'}"
                )
            return cast(
                "Any",
                super().execute_nl_tool(  # pyright: ignore[reportUnknownMemberType]
                    name,
                    tool_input,
                    database=database,
                    collection=collection,
                    optional_properties=optional_properties,
                ),
            )

    def execute_query_nl_tools(
        self,
        query: str,
        database: str | None = None,
        collection: str | None = None,
        llm_fn: Any = None,
        optional_properties: dict[str, Any] | None = None,
        max_iterations: int = 10,
        extra_tools: list[dict[str, Any]] | None = None,
        extra_tool_fn: Any = None,
    ) -> list[dict[str, Any]]:
        """Refuse, and name the path that is bounded."""
        raise ConfigError(
            "this source does not serve the tool-driven query path. Use "
            "execute_query_nl, whose spend and time ceilings this source enforces"
        )

    # ------------------------------------------------------------- metadata --
    def fetch_metadata(self) -> dict[str, Any]:
        return {"protocol": self.protocol, "variant": self.variant}

    def fetch_schema(self) -> dict[str, Any]:
        return {}

    def fetch_databases(self) -> list[str]:
        return [C.DATABASE]

    def fetch_database_metadata(self, database: str) -> dict[str, Any]:
        return {}

    def fetch_database_schema(self, database: str) -> dict[str, Any]:
        return {}

    def fetch_database_collections(self, database: str) -> list[str]:
        return [C.COLLECTION]

    def fetch_database_collection_metadata(self, database: str, collection: str) -> dict[str, Any]:
        return {}

    def fetch_database_collection_entities(self, database: str, collection: str) -> dict[str, Any]:
        """One virtual entity, so a registry sync has something to walk."""
        return {
            C.ENTITY: {
                "properties": {
                    C.ENTITY_ATTRIBUTE: {"type": C.ENTITY_ATTRIBUTE_TYPE},
                }
            }
        }

    def fetch_database_collection_relations(self, database: str, collection: str) -> dict[str, Any]:
        return {}

    def fetch_source_stats(self) -> dict[str, Any] | None:
        return None

    def fetch_database_stats(self, database: str) -> dict[str, Any] | None:
        return None

    def fetch_collection_stats(
        self,
        database: str,
        collection: str,
        entities: Any = None,
        relations: Any = None,
        sample_limit: int | None = None,
    ) -> dict[str, Any] | None:
        return None

    def fetch_entity_stats(
        self, database: str, collection: str, entity: str
    ) -> dict[str, Any] | None:
        return None

    def fetch_property_stats(
        self,
        database: str,
        collection: str,
        entity: str,
        property_name: str,
        sample_limit: int | None = None,
    ) -> dict[str, Any] | None:
        return None

    # ------------------------------------------------------------------ write --
    # A model has nothing to write to. These exist so a registry that walks the full
    # source surface reaches a defined method rather than an AttributeError, and each
    # returns the same empty result the surface specifies for an unsupported operation.
    def create_database(
        self, database: str, properties: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {}

    def create_database_collection(
        self, database: str, collection: str, properties: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {}

    def create_database_collection_entity(
        self,
        database: str,
        collection: str,
        entity: str,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {}

    def create_database_collection_relation(
        self,
        database: str,
        collection: str,
        relation: str,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {}

    def import_data(
        self,
        source: Any,
        # `database` before `collection`, matching the base class. They were the other way
        # round here, so a positional call reaching this override bound each to the other's
        # value. The contract test compares against the installed base and caught it when
        # the data layer settled on this order.
        database: str = "default",
        collection: str = "default",
        entity: str | None = None,
        mode: str = "append",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Not supported: there is no store here to import into.

        Materialising model knowledge into a real store is the receiving source's
        ``import_data``, with this source as its argument, not this method.
        """
        return {}

    # ---------------------------------------------------------- connection --
    def connect(self) -> dict[str, Any]:
        """No persistent connection is held; the client is per call."""
        return {}

    def disconnect(self) -> None:
        return None


def _reject_running_loop() -> None:
    """Raise if a synchronous entry point is called from inside an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise ConfigError("fetch() was called from inside a running event loop. Use afetch() instead.")
