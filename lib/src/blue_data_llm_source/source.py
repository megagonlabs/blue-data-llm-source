"""LlmSource: a language model presented as a data source."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import inspect
import json
import threading
import time
from collections.abc import Generator
from concurrent.futures import Future, ThreadPoolExecutor, wait
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
    accepted_adapter_keys,
    check_adapter_keys,
    default_max_tokens_for,
    default_reasoning_effort_for,
)
from blue_data_llm_source.planning.bounds import (
    MODE_FLAT,
    MODE_PARTITIONED,
    MODES,
    PARTITIONED_MAX_CONTINUATIONS,
    PlanLimits,
)
from blue_data_llm_source.planning.cardinality import PARSE_PARSED, Cardinality, CardinalityEstimate
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
from blue_data_llm_source.planning.plan import (
    CHILD_WORDING_PARENT,
    CHILD_WORDINGS,
    ROOT,
    Gap,
    PartitionPolicy,
    build_plan,
    partition_keys,
)
from blue_data_llm_source.planning.receipt import PlanningReceipt
from blue_data_llm_source.planning.schema import (
    KEY_FROM_CALLER,
    KEY_FROM_NONE,
    SCHEMA_ENRICH,
    SCHEMA_MODES,
    SCHEMA_PRESERVE,
    Schema,
)
from blue_data_llm_source.planning.split_gate import (
    DEFAULT_SPLIT_THRESHOLD_ROWS,
    PROBE_ORIGIN,
    SPLIT_ALWAYS,
    SPLIT_EVIDENCE,
    SPLIT_POLICIES,
    ProbeOutcome,
)
from blue_data_llm_source.planning.transport import LeafTask
from blue_data_llm_source.redaction import redact
from blue_data_llm_source.reduction import aligner, verifier
from blue_data_llm_source.reduction.coverage import FAILED, LeafInput, LeafOutcome, reduce_leaves
from blue_data_llm_source.reduction.identity import key_identity, normalized_value
from blue_data_llm_source.reduction.obedience import (
    FILTER_DROP,
    FILTER_ENFORCEMENTS,
    FILTER_FLAG,
    contradictions,
)
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


# Accepted limit keys; reject misspellings instead of ignoring them.
_LIMIT_KEYS = frozenset(f.name for f in fields(Limits))

# The rates a cost bound reads in place of published prices.
_PRICE_KEYS: Final = ("llm.price_per_input_token", "llm.price_per_output_token")

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

# `mode`, `key` and `partition_key` are not PlanLimits fields, so list them here or the
# validator will reject keys the facade reads.
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
    "leaf_prompt",
    "child_wording",
    "filter_enforcement",
    "range_filters",
    "partition_guardrails",
    "split_policy",
    "split_threshold_rows",
}
"""The `plan.*` keys supported by the source settings interface.

This set is narrower than :class:`PlanLimits`. It excludes settings used only
for whole-request recommendations, the ambiguity threshold (which has no
score to compare here), and the planning retry count. In particular,
`limits.max_retries` bounds provider attempts, while `PlanLimits.max_retries`
sizes a recommendation. Direct :class:`PlanLimits` callers retain all fields.
"""

# The keys that carry no namespace. `connection` is the transport block every source in the
# data layer has, `cache_store` hands a client a cache object directly, and `budget` and
# `usage_sink` are the per-call channels a fetch writes into the map it passes down — an
# injected client constructed with either carries it back through the resolved map.
# The data registry adds `_class` at registration and `metadata` at sync.
_BARE_KEYS = frozenset(
    {
        "connection",
        "cache_store",
        "_class",
        "metadata",
        "commit",
        C.BUDGET_PROPERTY,
        C.USAGE_SINK_PROPERTY,
    }
)

# The namespaces with a closed key set of their own. A key inside one has already been
# accepted or refused by name before the whole-map check reads it.
_NAMESPACES: Final = ("llm.", "limits.", "plan.")

# Request fields that the data-layer surfaces read from `optional_properties` because their
# signatures have nowhere else to put them. `fetch` takes each as a parameter, so these
# fields are removed from the map before validating the property names.
_REQUEST_KEYS: Final = ("attrs", "attr_names", "context", "max_records")
_OP_KEYS: Final = frozenset({"op", "query", *_REQUEST_KEYS})


def _changes_model(configured: dict[str, Any], call: dict[str, Any]) -> bool:
    """Whether *call* names a model other than the one configured."""
    return "llm.model" in call and call["llm.model"] != configured.get("llm.model")


def _take_request_keys(options: dict[str, Any]) -> dict[str, Any]:
    """Remove the request fields from a data-layer options map and return them."""
    return {key: options.pop(key) for key in _REQUEST_KEYS if key in options}


def _accepted_properties() -> list[str]:
    """Every property key this source reads, under the name a caller writes."""
    return sorted(
        _BARE_KEYS
        | accepted_adapter_keys()
        | {f"limits.{name}" for name in _LIMIT_KEYS}
        | {f"plan.{name}" for name in _PLAN_KEYS}
    )


# How long an interrupted synchronous call inside a running loop waits for its owned fetch
# to finish cancelling before it re-raises. A further interrupt stops the wait at once.
_INTERRUPT_GRACE_SECONDS: Final = 5.0

# How often that call checks whether its caller's task has been cancelled while it waits.
_CANCEL_POLL_SECONDS: Final = 0.1

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


def _unanswered_refusal(
    denied: list[BudgetExceeded], runs: list[LeafRun], *, trusted_zero: bool = False
) -> str | None:
    """Find a limit refusal before filtering can remove a valid answer's rows."""
    refusals = [
        *denied,
        *(run.exception for run in runs if isinstance(run.exception, BudgetExceeded)),
    ]
    refused_limits = _ordered_distinct(
        [e.limit for e in refusals if e.limit in ("max_cost", "max_calls", "max_seconds")]
    )
    if (
        refused_limits
        and not trusted_zero
        and not any(
            run.answered
            or run.records
            or run.ordinary_failure
            or run.outcome in (Outcome.EMPTY_QUERY, Outcome.MODEL_RETURNED_NONE)
            for run in runs
        )
    ):
        return refused_limits[0]
    return None


def _with_admission_refusal(result: FetchResult, limit: str | None) -> FetchResult:
    if limit is not None and not result.records:
        result._with_admission_refusal(limit)  # pyright: ignore[reportPrivateUsage]
    return result


def _query_records(result: FetchResult) -> list[dict[str, Any]]:
    limit = result._admission_refusal  # pyright: ignore[reportPrivateUsage]
    if limit is not None:
        raise BudgetExceeded(
            f"{limit} prevented an extraction answer. "
            "Review source limits or optional_properties; use fetch for outcome metadata.",
            limit=limit,
        )
    return result.records


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
    """Wrap unclassified public-entry errors as ``LlmSourceError``.

    Existing subclasses retain their classification. ``BaseException`` remains outside
    this boundary so cancellation and process interrupts can unwind reservations.
    """
    try:
        yield
    except LlmSourceError:
        raise
    except Exception as e:
        # Preserve the original type and traceback for maintainers, and chain the wrapper
        # so callers can catch `LlmSourceError`.
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
    # Keep the first index and consume each identity once. A reverse dict would retain only
    # the last index for a repeated object, assigning both copies in `before` to the second
    # origin. JSON parsing creates distinct objects, but this helper should still handle
    # repeated identities consistently.
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


def _reported_allowance(max_tokens: object) -> int:
    """The output allowance the cost bound actually reserved.

    Use pricing's acceptance rule so the published allowance matches the reservation.
    """
    allowance = pricing.positive_finite(max_tokens)
    return int(allowance) if allowance is not None else 0


def _accumulated(per_attempt: float, attempts: int) -> float:
    """The total the ledger reaches after *attempts* charges of *per_attempt*.

    The ledger's own arithmetic, one addition per attempt, so a ceiling set to this figure
    admits every attempt it counts. ``limits.max_retries`` caps the attempts at 10,000, so
    the loop is bounded.
    """
    total = 0.0
    for _ in range(attempts):
        total += per_attempt
    return total


def _attempts_within(per_attempt: float, attempts: int, ceiling: float | None) -> int | None:
    """How many of *attempts* a cost *ceiling* admits, each charged its full bound.

    The ledger's own comparison, made the way it makes it: before each attempt, what is
    committed plus this attempt's bound must not pass the ceiling. A bound of zero fits
    every attempt.
    """
    if ceiling is None:
        return None
    fits = 0
    total = 0.0
    for _ in range(attempts):
        total += per_attempt
        if total > ceiling:
            break
        fits += 1
    return fits


# Keep base schemas and dispatch; sampling returns unavailable statistics as None.
_SERVED_NL_TOOLS = frozenset(
    {"list_databases", "list_collections", "get_schema", "execute_query", "get_sample"}
)


@dataclass(frozen=True)
class _DistributionControls:
    enabled: bool
    rules: tuple[IdentityPartitionRule, ...]


@dataclass(frozen=True)
class _ContractControls:
    """Prompt and child-construction settings, each with its own default.

    These change what a leaf is asked, not how many calls a plan may make.
    """

    leaf_prompt: str = prompts.KEYED_LEAF_FETCH_PROMPT_VERSION
    child_wording: str = CHILD_WORDING_PARENT
    filter_enforcement: str = FILTER_FLAG
    range_filters: bool = False
    partition_guardrails: bool = False
    split_policy: str = SPLIT_ALWAYS
    split_threshold_rows: int = DEFAULT_SPLIT_THRESHOLD_ROWS

    def partition_policy(self) -> PartitionPolicy:
        return PartitionPolicy(
            child_wording=self.child_wording,
            range_filters=self.range_filters,
            partition_guardrails=self.partition_guardrails,
        )


# Keys a tool schema can require that `execute_nl_tool` also takes as arguments. The base
# dispatcher reads each from the tool input and falls back to the argument, so a call
# supplying them the second way is complete and must not be refused as incomplete.
_TOOL_KEYS_FROM_ARGUMENTS = frozenset({"database", "collection"})


def _check_tool_input(tool: dict[str, Any], tool_input: object) -> None:
    if not isinstance(tool_input, dict) or any(
        type(key) is not str for key in cast("dict[object, Any]", tool_input)
    ):
        raise QueryError(
            "tool_input must be a dict with string field names. Pass per-call configuration "
            "through optional_properties"
        )
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        raise ConfigError("served tool input schema must declare valid properties")
    schema = cast("dict[str, Any]", schema)
    properties = schema.get("properties")
    if schema.get("type") != "object" or not isinstance(properties, dict):
        raise ConfigError("served tool input schema must declare valid properties")
    properties = cast("dict[object, Any]", properties)
    if any(
        type(key) is not str or not isinstance(value, dict) for key, value in properties.items()
    ):
        raise ConfigError("served tool input schema must declare valid properties")
    if cast("dict[str, Any]", tool_input).keys() - properties.keys() - _TOOL_KEYS_FROM_ARGUMENTS:
        raise QueryError(
            "tool_input contains unsupported fields. Pass per-call configuration through "
            "optional_properties"
        )


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
    instead, and the orchestrator-tool methods carry behavior this class narrows to the part
    it can answer. The contract test asserts that the methods this source must answer resolve
    here.
    """

    # The read-only paths of the data layer run an op without ``commit`` only when it is a
    # declared read. Every op this source serves answers from the model or prices that
    # answer, and none changes stored data, so all of them are reads.
    _READ_OPS: frozenset[str] = frozenset(C.OPS)
    _WRITE_OPS: frozenset[str] = frozenset()
    _transient_properties: frozenset[str] = getattr(
        DataSource, "_transient_properties", frozenset[str]()
    ) | frozenset({"cache_store"})

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
        # Send provider-facing defaults explicitly to avoid silent provider changes, but keep
        # them out of `properties`. The registry reads that map back when rebuilding a source
        # and treats every key as caller configuration. Storing defaults there made the
        # default model's effort and token allowance override settings for other models, and
        # made the default model and stream flag override an injected client's configuration.
        # Effort and allowance depend on the model, so `_resolved_properties` fills them for
        # the model selected by each call.
        self._defaults: dict[str, Any] = {
            "llm.model": DEFAULT_MODEL,
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
        self.properties = {"connection": connection}

    def _resolved_properties(self, per_call: dict[str, Any] | None) -> dict[str, Any]:
        """Merge defaults, injected client settings, source settings, and call settings.

        Source settings override client settings. Call ceilings can only tighten them;
        other call settings take precedence. Model and rates resolve together, and under a
        configured cost ceiling the rates come from the configuration alone.
        """
        chosen = self.properties
        defaults = self._defaults
        client_own: Any = getattr(self._client, "properties", None)
        from_client: dict[str, Any] = (
            dict(cast("dict[str, Any]", client_own)) if isinstance(client_own, dict) else {}
        )
        configured = {**from_client, **chosen}
        call = per_call or {}
        # Validate each declaration before precedence can hide it.
        for layer in (from_client, chosen, call):
            self._limits_from_properties(layer)
        resolved = {**defaults, **configured, **call}
        if configured.get("limits.max_cost") is not None:
            self._hold_rates_to_configuration({**defaults, **configured}, call, resolved)
        for name in _LIMIT_KEYS:
            key = f"limits.{name}"
            declared = [layer[key] for layer in (configured, call) if key in layer]
            bounded = [value for value in declared if value is not None]
            if bounded:
                resolved[key] = min(bounded)
        reasoning_is_explicit = any(
            "llm.reasoning_effort" in layer for layer in (from_client, chosen, per_call or {})
        )
        if not reasoning_is_explicit:
            reasoning_effort = default_reasoning_effort_for(resolved.get("llm.model"))
            if reasoning_effort is None:
                resolved.pop("llm.reasoning_effort", None)
            else:
                resolved["llm.reasoning_effort"] = reasoning_effort
        max_tokens_is_explicit = any(
            "llm.max_tokens" in layer for layer in (from_client, chosen, per_call or {})
        )
        if not max_tokens_is_explicit:
            resolved["llm.max_tokens"] = default_max_tokens_for(resolved.get("llm.model"))
        self._check_property_keys(resolved)
        return resolved

    @staticmethod
    def _hold_rates_to_configuration(
        configured: dict[str, Any], call: dict[str, Any], resolved: dict[str, Any]
    ) -> None:
        """Keep a configured cost ceiling bounded against rates the caller did not choose.

        A call could otherwise bound an unlisted model at whatever rates it supplied, and
        no ceiling the owner configured would bind it. Rates configured for one model do
        not follow a per-call change of model either: a listed model is then bounded at
        its published prices, and an unlisted one is refused by the bound.
        """
        asserted = [key for key in _PRICE_KEYS if key in call]
        if asserted:
            raise ConfigError(
                f"{' and '.join(asserted)} cannot be set per call when limits.max_cost is "
                f"configured on the source or its client, since that ceiling holds only "
                f"against published prices or rates configured with it. Configure the rates "
                f"on the source, or choose a model with published prices; "
                f"priceable_models() lists them"
            )
        if _changes_model(configured, call):
            for key in _PRICE_KEYS:
                # None rather than removed: an injected client merges its own properties
                # under these and would otherwise restore its own rates.
                resolved[key] = None

    def _refuse_unpriced_call_model(self, per_call: dict[str, Any] | None) -> None:
        """Refuse, with a remedy this caller can take, a model the configured ceiling cannot bound.

        The bound refuses it in any case, but its message offers supplying rates or dropping
        the ceiling, and a call can do neither under a configured one.
        """
        call = per_call or {}
        configured = self._resolved_properties(None)
        if configured.get("limits.max_cost") is None or not _changes_model(configured, call):
            return
        model = call["llm.model"]
        if isinstance(model, str) and model and pricing.rates_for(model) is None:
            raise ConfigError(
                f"no published prices for model {model!r}, so the limits.max_cost configured "
                f"on the source or its client cannot bound it; configured rates apply only to "
                f"the configured model. Choose a model with published prices "
                f"(priceable_models() lists them), or configure {model!r} and its rates "
                f"on the source"
            )

    def max_tokens_for_model(self, model: str) -> object:
        """Return the output allowance a call to *model* would use."""
        return self._resolved_properties({"llm.model": model}).get("llm.max_tokens")

    # Match the base class default so DataSource callers may omit properties.
    def _update_properties(self, properties: dict[str, Any] | None = None) -> None:
        if not properties:
            return
        # Anything the caller sets is theirs, not a default, and outranks a client's own.
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
        # Resolve all property layers so injected client ceilings enter the budget.
        merged = self._resolved_properties(overrides)
        return self._limits_from_properties(merged)

    @staticmethod
    def _limits_from_properties(merged: dict[str, Any]) -> Limits:
        return Limits(
            max_rows=merged.get("limits.max_rows"),
            max_seconds=merged.get("limits.max_seconds"),
            max_cost=merged.get("limits.max_cost"),
            max_calls=merged.get("limits.max_calls"),
            max_retries=merged.get("limits.max_retries", 2),
        )

    @staticmethod
    def _plan_limits(merged: dict[str, Any]) -> PlanLimits:
        """The plan shape for this request, read from the same three-layer property map.

        Read here rather than defaulted at each seam, so a caller's `plan.max_leaves` is the
        one the budget is sized against and the one the planner honors."""
        # `_PLAN_KEYS` rather than every PlanLimits field, so this reads exactly what the
        # validator accepts. Two different answers to "is this a plan setting" is how a key
        # gets refused at the door and honored inside, or the reverse.
        given = {
            key[len("plan.") :]: value
            for key, value in merged.items()
            if key.startswith("plan.") and key[len("plan.") :] in _PLAN_LIMIT_KEYS
        }
        if merged.get("plan.mode") == MODE_PARTITIONED:
            given.setdefault("max_continuations", PARTITIONED_MAX_CONTINUATIONS)
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

    @staticmethod
    def _check_filters(
        runs: list[Any], plan: Any, schema: Schema, *, drop: bool
    ) -> tuple[list[Any], int]:
        """Count the records that contradict their leaf's filter, and remove them on request."""
        leaves = {leaf.id: leaf for leaf in plan.leaves}
        checked: list[Any] = []
        total = 0
        for run in runs:
            leaf = leaves.get(run.leaf_id)
            if leaf is None or not leaf.constraints or not run.records:
                checked.append(run)
                continue
            # Column filters only: request scope, including a root constraint whose key
            # matches a column, is never counted or dropped.
            columns, _ = prompts.split_filters(
                leaf.constraints, schema.attrs, partition_keys(plan, leaf)
            )
            flags = contradictions(run.records, columns, schema.attrs)
            found = sum(flags)
            total += found
            if drop and found:
                kept = [record for record, flag in zip(run.records, flags, strict=True) if not flag]
                run = replace(run, records=kept)
            checked.append(run)
        return checked, total

    @staticmethod
    def _contract_controls(merged: dict[str, Any], mode: str) -> _ContractControls:
        """Read and check the contract switches, refusing one the chosen mode cannot honor.

        A switch accepted in a mode that never reaches it would be the setting this source's
        closed key set exists to prevent: set, echoed back, and changing nothing.
        """
        leaf_prompt = merged.get("plan.leaf_prompt", prompts.KEYED_LEAF_FETCH_PROMPT_VERSION)
        if not isinstance(leaf_prompt, str) or leaf_prompt not in prompts.LEAF_PROMPT_VERSIONS:
            raise ConfigError(
                f"unknown plan.leaf_prompt {leaf_prompt!r}; expected one of "
                f"{', '.join(prompts.LEAF_PROMPT_VERSIONS)}"
            )
        if leaf_prompt != prompts.KEYED_LEAF_FETCH_PROMPT_VERSION and mode == MODE_FLAT:
            raise ConfigError(
                f"plan.leaf_prompt={leaf_prompt!r} requires a non-flat plan.mode; flat mode "
                "sends the unversioned fetch prompt"
            )
        child_wording = merged.get("plan.child_wording", CHILD_WORDING_PARENT)
        if not isinstance(child_wording, str) or child_wording not in CHILD_WORDINGS:
            raise ConfigError(
                f"unknown plan.child_wording {child_wording!r}; expected one of "
                f"{', '.join(CHILD_WORDINGS)}"
            )
        if child_wording != CHILD_WORDING_PARENT and mode != MODE_PARTITIONED:
            raise ConfigError(
                f"plan.child_wording={child_wording!r} requires plan.mode='partitioned', the "
                "only mode that builds child leaves from a partition proposal"
            )
        enforcement = merged.get("plan.filter_enforcement", FILTER_FLAG)
        if not isinstance(enforcement, str) or enforcement not in FILTER_ENFORCEMENTS:
            raise ConfigError(
                f"unknown plan.filter_enforcement {enforcement!r}; expected one of "
                f"{', '.join(FILTER_ENFORCEMENTS)}"
            )
        if enforcement == FILTER_DROP and mode == MODE_FLAT:
            raise ConfigError(
                "plan.filter_enforcement='drop' requires a non-flat plan.mode; a flat leaf "
                "carries no filter to enforce"
            )
        range_filters = merged.get("plan.range_filters", False)
        if not isinstance(range_filters, bool):
            raise ConfigError("plan.range_filters must be a boolean")
        if range_filters and mode != MODE_PARTITIONED:
            raise ConfigError(
                "plan.range_filters requires plan.mode='partitioned', the only mode that "
                "builds child leaves from a partition proposal"
            )
        if range_filters and leaf_prompt != prompts.COMPLETE_LEAF_FETCH_PROMPT_VERSION:
            # Not a preference: a range filter crosses the executor boundary only on the wire
            # version that carries that contract, and the older versions have no range kind.
            raise ConfigError(
                "plan.range_filters requires plan.leaf_prompt='keyed-leaf-fetch-2'; range "
                "filters travel only on that contract's wire version"
            )
        guardrails = merged.get("plan.partition_guardrails", False)
        if not isinstance(guardrails, bool):
            raise ConfigError("plan.partition_guardrails must be a boolean")
        if guardrails and mode != MODE_PARTITIONED:
            raise ConfigError(
                "plan.partition_guardrails requires plan.mode='partitioned', the only mode "
                "that asks a model to propose a partition"
            )
        split_policy = merged.get(
            "plan.split_policy", SPLIT_EVIDENCE if mode == MODE_PARTITIONED else SPLIT_ALWAYS
        )
        if not isinstance(split_policy, str) or split_policy not in SPLIT_POLICIES:
            raise ConfigError(
                f"unknown plan.split_policy {split_policy!r}; expected one of "
                f"{', '.join(SPLIT_POLICIES)}"
            )
        if split_policy != SPLIT_ALWAYS and mode != MODE_PARTITIONED:
            raise ConfigError(
                f"plan.split_policy={split_policy!r} requires plan.mode='partitioned', the only "
                "mode whose root splits"
            )
        threshold = merged.get("plan.split_threshold_rows", DEFAULT_SPLIT_THRESHOLD_ROWS)
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
            raise ConfigError(
                f"plan.split_threshold_rows must be a non-negative integer, got {threshold!r}"
            )
        if threshold != DEFAULT_SPLIT_THRESHOLD_ROWS and split_policy != SPLIT_EVIDENCE:
            raise ConfigError(
                "plan.split_threshold_rows is read only under plan.split_policy='evidence'"
            )
        return _ContractControls(
            leaf_prompt=leaf_prompt,
            child_wording=child_wording,
            filter_enforcement=enforcement,
            range_filters=range_filters,
            partition_guardrails=guardrails,
            split_policy=split_policy,
            split_threshold_rows=threshold,
        )

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
        leaf_prompt: str = prompts.KEYED_LEAF_FETCH_PROMPT_VERSION,
        probed: ProbeOutcome | None = None,
    ) -> list[Any]:
        """Run all plan leaves concurrently under the client's parallelism limit.

        Collect every result before returning so the caller sees final budget spend.
        Ordinary and unexpected leaf errors become failed results, preserving sibling rows;
        cancellation still propagates. The all-failed decision is made downstream.
        """
        # Keep receipts per leaf so concurrent calls cannot exchange finish reasons or
        # costs. The run total combines these lists after execution.
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
                        prompt_version=leaf_prompt,
                        partition_keys=(
                            partition_keys(plan, leaf)
                            if leaf_prompt == prompts.COMPLETE_LEAF_FETCH_PROMPT_VERSION
                            else ()
                        ),
                        # Every leaf below a probed root is told what the probe holds.
                        held_exclusions=probed.held if probed is not None else (),
                        held_total=probed.held_total if probed is not None else 0,
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
                # The source passes properties positionally; keyword-only signatures cannot bind.
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

        Capability is declared by the client, not inferred from a ``budget`` attribute.

        The axes differ in what is provable. This source counts records itself, so rows
        always bind. Everything else depends on the client: one that drives the provider
        can be held to calls, time and cost, while a wrapper around a caller's function can
        prove only that it invoked the function once.
        """
        declared = _declared(client)

        # Include ancestor ceilings when checking the client's declared capabilities.
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
    def _check_property_keys(properties: dict[str, Any]) -> None:
        """Refuse a property key this source does not read.

        The whole map is closed, not the three namespaces inside it. A key outside them all
        is as ignorable as a misspelled one within them, and on this surface a setting the
        caller believes they made is the difference between the request they described and
        the one that is billed.
        """
        # First, because every check below reads a key as a string. A key that is not one
        # left as a bare AttributeError, which tells a caller what broke rather than what
        # to write.
        mistyped = sorted(
            (key for key in cast("dict[Any, Any]", properties) if not isinstance(key, str)),
            key=repr,
        )
        if mistyped:
            named = ", ".join(f"{key!r} ({type(key).__name__})" for key in mistyped)
            raise ConfigError(
                f"property keys must be strings: {named}. Accepted: {_accepted_properties()}"
            )
        # The adapter keys are a closed set too, and are checked here for the same reason:
        # only the built-in client reads them, so validating there let a typo through on
        # every other path.
        check_adapter_keys(properties)
        # Reject unknown limits so a misspelled ceiling cannot be ignored.
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
        if unknown or near_misses:
            raise ConfigError(
                f"unknown limit {', '.join(repr(k) for k in unknown + near_misses)}. "
                f"Accepted: {sorted('limits.' + name for name in _LIMIT_KEYS)} and "
                f"{sorted('plan.' + name for name in _PLAN_KEYS)}"
            )
        # Last, so a key that belongs to a namespace is refused by the check that knows what
        # that namespace offers and gets the shorter list to correct itself against.
        outside = sorted(
            key for key in properties if key not in _BARE_KEYS and not key.startswith(_NAMESPACES)
        )
        if outside:
            raise ConfigError(
                f"unknown property {', '.join(repr(k) for k in outside)}. "
                f"Accepted: {_accepted_properties()}"
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

        # Validate settings before the empty-query return. Report an empty query separately
        # from a model response containing no records.
        limits = self._limits(properties)
        merged = self._resolved_properties(properties)
        self._refuse_unpriced_call_model(properties)
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
                max_rows=cap if limits.max_rows is None else min(cap, limits.max_rows),
                max_seconds=limits.max_seconds,
                max_cost=limits.max_cost,
                max_calls=limits.max_calls,
                max_retries=limits.max_retries,
            )
            merged["limits.max_rows"] = limits.max_rows
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
        root_cardinality: CardinalityEstimate | None = None
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
        contract = self._contract_controls(merged, mode)

        # The root probe's receipts, kept apart from planning's so a probe that hit its output
        # allowance is not reported as a discarded planning response, and folded into the
        # run's accounting whichever way planning ends.
        probe_sink: list[Usage] = []
        probe_runs: list[LeafRun] = []

        async def probe(leaf: Any, effective: Schema) -> LeafRun:
            """The root leaf, asked as execution would ask it, with its continuations but
            without the empty-answer retry."""
            # Without a key, continuations cannot merge, as at execution below.
            probe_limits = replace(
                plan_limits,
                empty_leaf_retry=False,
                max_continuations=plan_limits.max_continuations if effective.primary_keys else 0,
            )
            task = LeafTask.from_leaf(
                leaf,
                caller_context=ctx,
                schema=effective,
                # The root's trusted row target applies to the probe as it would to the root
                # leaf: a target of zero asks nothing.
                settings=execution_settings(
                    provider_properties, probe_limits, run_budget, exact_rows=exact_rows
                ),
                prompt_version=contract.leaf_prompt,
            )
            run = await run_local_leaf(
                task, client=client, properties=provider_properties, usage_sink=probe_sink
            )
            probe_runs.append(run)
            return run

        planning_refusal: BudgetExceeded | None = None
        planning_receipt: PlanningReceipt | None = None
        probed: ProbeOutcome | None = None
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
                    leaf_prompt=contract.leaf_prompt,
                    partition_policy=contract.partition_policy(),
                    split_policy=contract.split_policy,
                    split_threshold_rows=contract.split_threshold_rows,
                    probe=probe if contract.split_policy == SPLIT_EVIDENCE else None,
                    probe_exact_rows=exact_rows,
                )
            except BudgetExceeded as e:
                call_usages.extend(probe_sink)
                meta.outcome = Outcome.STOPPED_BY_LIMIT
                meta.stop_reasons = _ordered_distinct([e.limit])
                meta.stopped_by = meta.stop_reasons[0]
                self._fold_usage(call_usages, meta, run_budget, self._attests(client))
                meta.gaps = [f"planning_unserved: {e}"]
                meta.latency_seconds = time.monotonic() - started
                return _with_admission_refusal(
                    FetchResult(records=[], meta=meta),
                    _unanswered_refusal([e], probe_runs, trusted_zero=exact_rows == 0),
                )
            plan = bundle.plan
            schema = bundle.schema
            call_usages.extend(probe_sink)
            probed = bundle.probe
            planning_refusal = bundle.refusal
            # Published only when the probe asked something. A zero-row target answers the
            # root without a call, and a decision about a call never made would say otherwise.
            if probed is not None and probed.run.attempts > 0:
                meta.split_decision = probed.decision
            if distribution.enabled:
                planning_receipt = PlanningReceipt(
                    configured_rule_digests=tuple(rule.digest for rule in distribution.rules),
                    facts=bundle.facts,
                    plan=bundle.plan,
                )
            meta.row_estimate = bundle.analysis.row_estimate
            root_cardinality = bundle.analysis.cardinality
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
                # Use the final attempt's reason. A client that wrote no usage has no reason.
                reason = written[-1].finish_reason if written else None
                return Answer(text, finish_reason=reason)

            return ask

        leaf_usages: list[list[Usage]] = []
        leaf_exact_rows = exact_rows if len(plan.leaves) == 1 else None
        if exact_rows is not None and leaf_exact_rows is None:
            planning_gaps.append(
                "exact_rows_not_leaf_bound: trusted root count cannot be applied to separate leaves"
            )
        # The probe's answer stands for the root leaf when the plan did not split after it:
        # accepted, or a split was called for and no proposal survived. Asking the root again
        # would repeat the same question for the same answer, truncation included.
        answered_by_probe = probed is not None and (
            not probed.split or [leaf.id for leaf in plan.leaves] == [ROOT]
        )
        try:
            runs = (
                [cast("ProbeOutcome", probed).run]
                if answered_by_probe
                else [
                    LeafRun(
                        leaf_id=leaf.id,
                        outcome=Outcome.STOPPED_BY_LIMIT,
                        stopped_by=planning_refusal.limit,
                        error=f"planning refused by {planning_refusal.limit}",
                        exception=planning_refusal,
                    )
                    for leaf in plan.leaves
                ]
                if planning_refusal is not None
                else await self._run_leaves(
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
                    leaf_prompt=contract.leaf_prompt,
                    probed=probed,
                )
            )
        except BudgetExceeded as e:
            meta.stop_reasons = _ordered_distinct([e.limit])
            meta.stopped_by = meta.stop_reasons[0]
            meta.outcome = Outcome.STOPPED_BY_LIMIT
            for sink in leaf_usages:
                call_usages.extend(sink)
            self._fold_usage(call_usages, meta, run_budget, self._attests(client))
            meta.gaps = list(planning_gaps)
            meta.gaps.append(f"execution_unserved: {e}")
            meta.latency_seconds = time.monotonic() - started
            result = _with_admission_refusal(
                FetchResult(records=[], meta=meta),
                _unanswered_refusal([e], probe_runs, trusted_zero=exact_rows == 0),
            )
            return (
                result.with_planning_receipt(planning_receipt)
                if planning_receipt is not None
                else result
            )

        # Folded back after the fan-out. Splitting the sink per leaf is an attribution
        # change, not an accounting one, and the totals a caller reads must not move.
        for sink in leaf_usages:
            call_usages.extend(sink)

        refusal = _unanswered_refusal(
            [planning_refusal] if planning_refusal is not None else [],
            [*runs, *([probed.run] if probed is not None and not answered_by_probe else [])],
            trusted_zero=exact_rows == 0,
        )

        # At the leaf and before reduction, because reduction merges fields across leaves that
        # returned the same key: a row removed after the merge has already lent its fields to a
        # record that stays, and the row cap would have been spent on it.
        runs, meta.filter_contradictions = self._check_filters(
            runs, plan, schema, drop=contract.filter_enforcement == FILTER_DROP
        )

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
                    stop_detail=(
                        str(run.exception)
                        if isinstance(run.exception, BudgetExceeded)
                        and run.stopped_by == "max_cost"
                        else None
                    ),
                    at_cap=run.at_cap,
                    recovered_prefix=run.recovered_prefix,
                    provider_output_limit=run.provider_output_limit,
                    recovery_attested=run.recovery_attested,
                ),
            )
            for run in runs
        ]

        # Children can recover the probe's truncation. A planning refusal leaves that
        # recovery unserved, so the probe's stop reasons still apply.
        run_stop_reasons = _run_stop_reasons(runs)
        if planning_refusal is not None and probed is not None and not answered_by_probe:
            run_stop_reasons = _ordered_distinct(run_stop_reasons, _run_stop_reasons([probed.run]))
        counted = list(runs)
        if probed is not None and not answered_by_probe and probed.run.ordinary_failure:
            # The split recovered from it, but a failure is still a failure: said, not hidden
            # behind the children's rows.
            # The exception's type only. Its message is provider or parser text, which a gap
            # would publish unredacted through every adapter.
            failure = probed.run.exception
            planning_gaps.append(
                f"probe_failed: {type(failure).__name__ if failure is not None else 'error'}"
            )
        if probed is not None and not answered_by_probe:
            root_run = replace(probed.run, leaf_id=PROBE_ORIGIN)
            counted.insert(0, root_run)
            # Its records enter reduction like any leaf's, under the origin "root": they
            # merge on key with the children's, count toward the row cap, and are attributed.
            # The outcome is plain, so the account does not report as a gap what the split
            # already answered.
            results.append(
                (
                    PROBE_ORIGIN,
                    root_run.records,
                    LeafOutcome(
                        leaf_id=PROBE_ORIGIN,
                        outcome=Outcome.OK,
                        rows=len(root_run.records),
                        calls=root_run.attempts,
                        answered=root_run.answered,
                    ),
                )
            )
        meta.observed_key_count = sum(run.observed_key_count for run in counted)
        meta.marginal_key_yields = [value for run in counted for value in run.marginal_key_yields]
        meta.incomplete_key_records = sum(run.incomplete_key_records for run in counted)
        meta.repaired_numbers = sum(run.repaired_numbers for run in counted)
        meta.normalization_collisions = [
            collision for run in counted for collision in run.normalization_collisions
        ]
        answered_any = any(run.answered for run in counted)
        retained_any = any(run.records for run in counted)
        failed_runs = counted
        if (
            any(run.ordinary_failure for run in failed_runs)
            and not answered_any
            and not retained_any
        ):
            self._fold_usage(call_usages, meta, run_budget, self._attests(client))
            stop_reasons = _ordered_distinct(
                run_stop_reasons,
                [planning_refusal.limit] if planning_refusal is not None else [],
                meta.stop_reasons,
                [meta.stopped_by] if meta.stopped_by else [],
            )
            error = _execution_failure(failed_runs, stop_reasons)
            raise error from error.__cause__

        records, coverage = reduce_leaves(plan, schema, results)

        attrs = list(schema.attrs)
        alignment = aligner.align(records, attrs)
        checked, mismatches = verifier.verify(alignment.records, attrs)
        answered = verifier.drop_empty(checked, alignment.matched, attrs)
        # Keep attribution aligned through every pass that changes the records list. It was
        # computed from the merged result, but later passes can drop or trim rows. Publishing
        # the original indices would attach origins to the wrong records or to none at all.
        deduped, origins = verifier.dedupe(
            answered, origins=_origins_for(coverage.record_origins, checked, answered)
        )
        result_gaps: list[str] = []
        if (
            schema.primary_keys
            and root_cardinality is not None
            and root_cardinality.parse_status == PARSE_PARSED
            and root_cardinality.lower is not None
        ):
            observed_before_cap = len(
                {
                    identity
                    for record in deduped
                    if (identity := key_identity(record, schema.primary_keys, schema.attrs))
                    is not None
                }
            )
            if observed_before_cap < root_cardinality.lower:
                result_gaps.append(
                    f"observed_coverage_short: {observed_before_cap} distinct complete "
                    "identities before the row cap, below the root model's lower "
                    f"estimate {root_cardinality.lower} (estimate-based warning)"
                )
        rows_before_cap = len(deduped)
        kept = run_budget.add_rows(rows_before_cap)
        row_cap_fired = kept < rows_before_cap
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

        # Reindex mismatches after dropping, deduplication, and capping so they refer to
        # records the caller receives.
        meta.type_mismatches = _renumber_by_record(mismatches, checked, deduped)

        meta.observed_keys = alignment.observed_keys
        meta.remapped_keys = alignment.remapped
        meta.unmapped_keys = alignment.unmapped
        meta.key_collisions = _renumber_by_record(alignment.collisions, checked, deduped)
        meta.unchecked_types = verifier.unchecked_types(attrs)
        meta.stop_reasons = _ordered_distinct(
            run_stop_reasons,
            [planning_refusal.limit] if planning_refusal is not None else [],
            ["max_rows"] if row_cap_fired else [],
        )
        self._fold_usage(call_usages, meta, run_budget, self._attests(client))
        meta.stop_reasons = _ordered_distinct(
            meta.stop_reasons,
            [meta.stopped_by] if meta.stopped_by else [],
        )
        meta.stopped_by = meta.stop_reasons[0] if meta.stop_reasons else None
        interrupted = coverage.run_outcome == Outcome.PARTIAL or planning_refusal is not None
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
        result = _with_admission_refusal(FetchResult(records=deduped, meta=meta), refusal)
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
        """Bound one flat fetch of *query* without contacting the provider.

        Use the client and arguments the fetch would use. The result reads no ``plan.*``
        settings and does not bound a non-flat run's total cost. The bound accumulates
        the per-attempt cost in ledger order across permitted retries (three
        attempts by default). When pricing is unavailable, return an unbounded
        :class:`Estimate` with a reason.
        """
        # Include injected client settings in the configuration used for pricing.
        merged = self._resolved_properties(properties)
        # The model actually configured, not the default it would fall back to. Reporting
        # the fallback alongside a reason saying none was configured is a contradiction a
        # caller has no way to resolve.
        configured = merged.get("llm.model")
        # Published, never used to look anything up, so it is masked once here: a
        # credential pasted into `llm.model` must not return through the estimate.
        model = redact(str(configured)) if configured else ""
        # Before the empty-request short circuit, so the closed key set is closed on this
        # route too and a caller can check its configuration without spending anything.
        limits = self._limits(properties)
        # Apply the chain's retry allowance and the call cap to the attempt count.
        attempts = self._attempts_reachable(Budget(limits), limits)

        text = checks.normalize_query(query)
        if not text:
            # An empty ask never reaches the model, so it cannot cost anything, and every
            # attempt it is allowed fits under any ceiling at a bound of zero.
            return Estimate(
                bounded=True,
                max_cost=0.0,
                max_cost_per_attempt=0.0,
                model=model,
                max_attempts=attempts,
                attempts_within_ceiling=_attempts_within(0.0, attempts, limits.max_cost),
            )

        wanted: list[Attr] = checks.normalize_attrs(attrs)
        ctx = checks.normalize_context(context)
        prompt = prompts.build_fetch_prompt(text, wanted, caller_context=ctx)
        prompt_bytes = len(prompt.encode("utf-8"))

        client = self._resolve_client(llm_fn, Budget(limits), default_properties=merged)
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
            per_attempt = Budget.checked_amount(
                "the attempt bound", bound_for_attempt(prompt, merged)
            )
        except ConfigError as e:
            return Estimate(
                bounded=False,
                # The message names the model, which is a value the caller supplied and
                # may have supplied wrongly; a pasted credential must not come back out.
                reason=redact(str(e)),
                model=model,
                prompt_bytes=prompt_bytes,
                max_attempts=attempts,
                # Known even where the price is not, so a caller does not lose it too.
                max_output_tokens=_reported_allowance(max_tokens),
            )

        return Estimate(
            bounded=True,
            attempts_within_ceiling=_attempts_within(per_attempt, attempts, limits.max_cost),
            # Match the ledger's per-attempt addition order so a ceiling set from this
            # estimate admits every attempt it includes.
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

        ``effective_retries`` uses the narrowest allowance in the budget chain, not
        just this request's ``limits.max_retries``. The client builds its retry policy
        from this figure.
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
        """Fetch synchronously, blocking the calling thread even inside a running loop."""

        def run() -> FetchResult:
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

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return run()

        context_snapshot = contextvars.copy_context()
        # The worker publishes its task here so an interrupt in this thread can cancel it.
        # The flag covers an interrupt that arrives before the task exists.
        owned: dict[str, Any] = {"task": None, "interrupted": False}
        owned_lock = threading.Lock()

        def worker() -> FetchResult:
            # Close owned async resources before releasing the loop entry.
            loop: asyncio.AbstractEventLoop | None = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    fetch_task = loop.create_task(
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
                    with owned_lock:
                        owned["task"] = fetch_task
                        if owned["interrupted"]:
                            fetch_task.cancel()
                    return loop.run_until_complete(fetch_task)
                finally:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                    # Match asyncio.run's five-minute executor shutdown bound.
                    loop.run_until_complete(
                        cast(asyncio.BaseEventLoop, loop).shutdown_default_executor(timeout=300)
                    )
                    loop.close()
                    asyncio.set_event_loop(None)
            finally:
                if loop is not None:
                    DefaultLlmClient._release_loop(loop)  # pyright: ignore[reportPrivateUsage]

        # The first Ctrl-C under asyncio.run only requests cancellation of the caller's task,
        # which cannot act on it while this thread waits. A new request is checked for between
        # waits instead. A plain loop callback has no current task to check.
        caller_cancelling: Any = getattr(asyncio.current_task(), "cancelling", None)
        requests_at_entry = caller_cancelling() if caller_cancelling is not None else 0
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-source")
        future: Future[FetchResult] | None = None
        try:
            # Submitted inside the try: an interrupt can arrive after the work is queued but
            # before the future is returned.
            future = executor.submit(context_snapshot.run, worker)
            while not future.done():
                wait([future], timeout=_CANCEL_POLL_SECONDS)
                if caller_cancelling is not None and caller_cancelling() > requests_at_entry:
                    if not future.done():
                        raise asyncio.CancelledError
            return future.result()
        except BaseException:
            if future is None or not future.done():
                # An interrupt or cancellation while waiting would otherwise leave the fetch
                # running unseen, or keep this thread blocked until every provider call had
                # answered.
                with owned_lock:
                    owned["interrupted"] = True
                    task = owned["task"]
                if task is not None:
                    with contextlib.suppress(RuntimeError):
                        task.get_loop().call_soon_threadsafe(task.cancel)
                if future is not None:
                    wait([future], timeout=_INTERRUPT_GRACE_SECONDS)
            raise
        finally:
            # Join a finished worker. One still running past the grace period is left to end,
            # and work that never started is dropped.
            executor.shutdown(wait=future is not None and future.done(), cancel_futures=True)

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
        # The budget retains contacted attempts and their charge, including ordinary
        # provider failures whose usage entries have unknown accounting.
        meta.calls = budget.calls
        meta.cost = budget.cost
        # A callable's provider work is outside this ledger. Accounting is known only
        # when every contacted attempt was priced; an unfamiliar model or failed
        # provider accounting may leave a bound or zero instead of a measured cost.
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

        Accepts natural-language text or an operation dict, including a JSON-encoded
        object carrying an ``op`` key. Other strings remain natural-language queries.
        Supported ops are listed in the error raised for an unknown one.
        """
        options = dict(optional_properties or {})
        request = _take_request_keys(options)
        # Check keys before dispatch so every operation rejects unknown settings.
        self._check_property_keys(options)

        if isinstance(query, str):
            stripped = query.strip()
            if stripped.startswith("{"):
                try:
                    decoded = json.loads(stripped)
                except (ValueError, TypeError, RecursionError):
                    pass
                else:
                    if isinstance(decoded, dict) and "op" in decoded:
                        query = cast("dict[str, Any]", decoded)

        if isinstance(query, dict):
            spec = cast("dict[str, Any]", query)
            if any(type(key) is not str for key in spec):
                raise QueryError(
                    "operation fields must be strings. "
                    f"Accepted fields: {', '.join(sorted(_OP_KEYS))}. "
                    "Put settings in optional_properties."
                )
            op = spec.get("op")
            if op is not None and not isinstance(op, str):
                raise QueryError(
                    f"unknown op: expected a string. Supported ops: {', '.join(C.OPS)}"
                )
            if op not in C.OPS:
                raise QueryError(f"unknown op {op!r}. Supported ops: {', '.join(C.OPS)}")
            unsupported = sorted(spec.keys() - _OP_KEYS)
            if unsupported:
                raise QueryError(
                    f"unsupported operation fields: {', '.join(unsupported)}. "
                    f"Accepted fields: {', '.join(sorted(_OP_KEYS))}. "
                    "Put settings in optional_properties."
                )
            if op == C.OP_MODELS:
                return [{"protocol": self.protocol, "variant": self.variant}]
            text = spec.get("query", "")
            # Read once for both ops, from both places, with the spec losing to a field the
            # caller put in `optional_properties`. Reading the estimate's attributes from the
            # spec alone priced a request without them while the fetch of the same arguments
            # sent them, and the difference is invisible to the caller reading the figure.
            request.setdefault("attrs", spec.get("attrs", spec.get("attr_names")))
            request.setdefault("context", spec.get("context"))
            request.setdefault("max_records", spec.get("max_records"))
            if op == C.OP_ESTIMATE:
                if request.get("max_records") is not None:
                    raise ConfigError(
                        "max_records does not change an estimate, which prices a flat fetch "
                        "without a row cap. Drop it, or bound spend with limits.max_cost."
                    )
                # Returned as a one-record list because that is what this surface returns.
                # A caller wanting the record itself calls estimate() directly.
                estimate = self.estimate(
                    text,
                    attrs=request.pop("attrs", None) or request.pop("attr_names", None),
                    context=request.pop("context", None),
                    properties=options or None,
                )
                return [asdict(estimate)]
        else:
            text = query

        result = self.fetch(
            text,
            attrs=request.pop("attrs", None) or request.pop("attr_names", None),
            context=request.pop("context", None),
            max_records=request.pop("max_records", None),
            properties=options or None,
        )
        return _query_records(result)

    def execute_query_nl(
        self,
        query: str,
        database: str | None = None,
        collection: str | None = None,
        schema: dict[str, Any] | None = None,
        llm_fn: Any = None,
        optional_properties: dict[str, Any] | None = None,
        read_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Run a natural-language query.

        For every other source ``llm_fn`` translates natural language into a native
        dialect. This source has no other dialect, so the callable is the model, and is
        used as the client for this call when one is supplied.
        """
        options = dict(optional_properties or {})
        request = _take_request_keys(options)
        result = self.fetch(
            query,
            attrs=request.pop("attrs", None) or request.pop("attr_names", None),
            context=request.pop("context", None),
            max_records=request.pop("max_records", None),
            properties=options or None,
            llm_fn=llm_fn,
        )
        return _query_records(result)

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
        read_only: bool = True,
    ) -> Any:
        """Run one advertised tool, or refuse by name.

        The served set is read from :meth:`get_nl_tools`, so what is advertised and what runs
        cannot diverge. ``execute_query`` is the one tool here that reaches the provider, and
        it takes its ceilings the way the query surface does: ``limits.*`` in
        *optional_properties*, which the base dispatcher hands on unchanged.
        """
        # Include validation and dispatch in the error boundary so callers receive
        # LlmSourceError for invalid tool input.
        with _in_hierarchy():
            served = {str(tool["name"]): tool for tool in self.get_nl_tools(database, collection)}
            if name not in served:
                raise ConfigError(
                    f"{name!r} is not a tool this source serves. Available: "
                    f"{', '.join(sorted(served))}. Use execute_query_nl for the "
                    f"natural-language surface"
                )
            _check_tool_input(served[name], tool_input)
            # Checked for every tool rather than only the one that spends, for the reason
            # execute_query checks it before reading the op: a ceiling key accepted on one
            # call and refused on the next is a ceiling a caller believes they have set. The
            # base dispatcher hands this map to `execute_query`, which reads the request
            # fields out of it, so they are not properties here either.
            forwarded = dict(optional_properties or {})
            _take_request_keys(forwarded)
            self._check_property_keys(forwarded)
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
                    read_only=read_only,
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
        read_only: bool = True,
        max_sample_size: int | None = None,
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

    def fetch_database_collection_entity_metadata(
        self, database: str, collection: str, entity: str
    ) -> dict[str, Any]:
        return {}

    def fetch_database_collection_entity_attributes(
        self, database: str, collection: str, entity: str
    ) -> dict[str, Any]:
        entities = self.fetch_database_collection_entities(database, collection)
        return entities.get(entity, {}).get("properties", {})

    def fetch_database_collection_relation_metadata(
        self, database: str, collection: str, relation: str
    ) -> dict[str, Any]:
        return {}

    def fetch_database_collection_relation_attributes(
        self, database: str, collection: str, relation: str
    ) -> dict[str, Any]:
        return {}

    def fetch_relation_stats(
        self, database: str, collection: str, relation: str
    ) -> dict[str, Any] | None:
        return None

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
        self, database: str, collection: str, entity: str, *, row_sample_size: int = 10
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
        self,
        database: str,
        properties: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return {}

    def create_database_collection(
        self,
        database: str,
        collection: str,
        properties: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return {}

    def create_database_collection_entity(
        self,
        database: str,
        collection: str,
        entity: str,
        properties: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return {}

    def create_database_collection_relation(
        self,
        database: str,
        collection: str,
        relation: str,
        properties: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return {}

    def import_data(
        self,
        source: Any,
        # Match the base class's positional argument order.
        database: str = "default",
        collection: str = "default",
        entity: str | None = None,
        mode: str = "append",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Not supported: there is no store here to import into.

        Materializing model knowledge into a real store is the receiving source's
        ``import_data``, with this source as its argument, not this method.
        """
        return {}

    # ---------------------------------------------------------- connection --
    def connect(self) -> dict[str, Any]:
        """No persistent connection is held; the client is per call."""
        return {}

    def disconnect(self) -> None:
        return None
