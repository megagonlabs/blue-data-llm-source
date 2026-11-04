"""Shared data contracts and the exception hierarchy."""

from __future__ import annotations

import json
import math
from collections.abc import Hashable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Final, Protocol, cast, runtime_checkable


def value_identity(value: object) -> Hashable:
    """A hashable stand-in for *value* that keeps values of different JSON types apart.

    Execution and reduction both decide whether two records are the same record, and each
    is a place where a plausible local rule quietly loses rows. ``str(value)`` reads JSON
    ``1`` and ``"1"`` as one record and drops the second. The value itself keeps those two
    apart but merges ``True`` with ``1``, because Python hashes a bool as the int it
    subclasses. Either rule alone is defensible; the two together mean the same pair of
    records is one row at one stage and two at the next, and neither stage reports it.

    Numbers are not split by Python type. JSON has a single number type, so ``1`` and
    ``1.0`` are one identity here and a model writing a key both ways does not produce two
    rows. A list or an object cannot be an identity as it stands, being unhashable, so it
    is identified by its canonical JSON text with keys sorted — JSON object order is not
    semantic, and two spellings of the same object are one value.
    """
    if isinstance(value, bool):
        # Ahead of int, because bool is a subtype of it. Fall through and True lands under
        # the number tag beside 1, which is the merge this function exists to prevent.
        return ("bool", value)
    if isinstance(value, (int, float)):
        return ("number", value)
    if isinstance(value, str):
        return ("string", value)
    return ("json", json.dumps(value, sort_keys=True, default=str, ensure_ascii=False))


class LlmSourceError(Exception):
    """Base for every error this package raises."""


class ConfigError(LlmSourceError):
    """Raised for rejected or missing configuration."""


class QueryError(LlmSourceError):
    """Raised for a malformed or unusable query."""


class TransportError(LlmSourceError):
    """Raised for provider transport failures and malformed provider responses."""

    def __init__(
        self, message: str, code: str | None = None, provider_error: Any | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.provider_error = provider_error


class ParseError(LlmSourceError):
    """Raised when a model response cannot be read as a record list."""

    def __init__(self, message: str, raw: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        # The raw text is carried because a caller debugging a parse failure needs the
        # response, and re-running the query is not guaranteed to reproduce it.
        self.raw = raw


class BudgetExceeded(LlmSourceError):
    """Raised before spend when a configured limit would be crossed."""

    def __init__(self, message: str, limit: str) -> None:
        super().__init__(message)
        self.message = message
        self.limit = limit


@dataclass(frozen=True)
class Attr:
    """One requested output attribute."""

    name: str
    type: str | None = None


def resolve_attr(name: str, attrs: Sequence[Attr]) -> Attr | None:
    """The schema attribute *name* refers to, matched without regard to case, or None.

    A constraint key comes from a model, which writes ``Year`` for a column declared
    ``year``. The match is taken only when exactly one attribute's name folds to the same
    text; two attributes that differ only by case make every spelling of that name
    ambiguous, including an exact one, and an ambiguous key names no column.
    """
    folded = name.strip().casefold()
    matches = [attr for attr in attrs if attr.name.strip().casefold() == folded]
    return matches[0] if len(matches) == 1 else None


JsonScalar = str | int | float | bool
"""A JSON scalar suitable for an exact, locally comparable constraint."""


def json_scalar(value: object, name: str = "constraint value") -> JsonScalar:
    """Validate and return one finite, non-empty JSON scalar."""
    if isinstance(value, str):
        if not value.strip():
            raise ConfigError(f"{name} must not be empty")
        return value
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ConfigError(f"{name} must be a finite JSON scalar")


@dataclass(frozen=True)
class Exact:
    """A filter satisfied by one value."""

    value: JsonScalar

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", json_scalar(self.value))


@dataclass(frozen=True)
class OneOf:
    """A filter satisfied by any one of several alternatives.

    A tuple rather than a list so the whole constraint is hashable and cannot be extended
    after the plan that sized itself against it was built.
    """

    values: tuple[JsonScalar, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "values",
            tuple(json_scalar(value, "constraint alternative") for value in self.values),
        )


@dataclass(frozen=True)
class Range:
    """A filter satisfied by any integer from ``lo`` to ``hi``, both included.

    Built only by the planner, from a proposal value written as a span such as
    ``"1946–1952"`` on a column declared as an integer, or from numeric bucketing, and only
    under ``plan.range_filters``. Without it the same value becomes an ``Exact`` filter on
    the literal text, which no record's year can equal, so a leaf asked for a span was asked
    for nothing a model could match.
    """

    lo: int
    hi: int

    def __post_init__(self) -> None:
        for name in ("lo", "hi"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"range {name} must be an integer, got {value!r}")
        if self.lo > self.hi:
            raise ConfigError(f"range lo {self.lo} is above hi {self.hi}")

    def contains(self, value: object) -> bool:
        """Whether *value* is an integer from ``lo`` to ``hi``.

        Integer-valued only: a range bounds an integer column, so ``2.5`` is not in
        ``Range(1, 3)`` even though it lies between the ends. A float with no fractional part
        counts, since JSON has one number type; a bool does not.
        """
        if isinstance(value, bool):
            return False
        if isinstance(value, float):
            if not value.is_integer():
                return False
        elif not isinstance(value, int):
            return False
        return self.lo <= value <= self.hi


Constraint = Exact | OneOf | Range
"""What a leaf may say about a field, tagged with which kind it is.

Tagged constraints keep exact values, alternatives, and ranges distinct through prompt
rendering. Joining alternatives with a comma would make ``['A, B', 'C']`` and
``['A', 'B', 'C']`` indistinguishable.
"""


@dataclass
class Usage:
    """Per-call provider accounting."""

    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    latency_seconds: float = 0.0
    cache_hit: bool = False
    finish_reason: str | None = None
    """Why the provider stopped generating, as it said it, or None when it said nothing.

    Execution and planning consume this field differently. ``"length"`` on an execution
    response is evidence that the output allowance fired and contributes an operational stop
    reason. A planning response is discarded after its proposal is read, so its finish reason
    remains diagnostic usage metadata rather than a result stop reason. ``"stop"`` and ``None``
    are absence of evidence rather than evidence of absence: the provider layer defaults a
    termination reason it does not recognize to ``"stop"``, so believing it would turn an
    unrecognized truncation into a claim that the answer is complete.

    None rather than ``"stop"`` when the field is missing or malformed. A provider that said
    nothing and a provider that reported a clean stop are different facts, and defaulting one
    to the other invents an attestation nobody made."""

    cost_known: bool = True
    """Whether ``cost`` is what the provider charged.

    False when a reservation or usage bound sets the ledger charge, provider accounting
    cannot be read, or the calculator cannot prove every billed category. Under a cost
    ceiling, a contacted attempt keeps at least its reservation. Without a ceiling, an
    unpriced call may report zero; that means unmeasured, not free."""


class Outcome:
    """Why a result looks the way it does.

    An empty result is a valid answer, not a failure, which is how the other sources in
    this layer treat it. But "the model says there are none", "everything it returned was
    filtered out", and "a limit stopped the run" are different facts, and a planner
    deciding whether to ask elsewhere needs to tell them apart. Collapsing them all into
    an empty list throws that away.
    """

    OK = "ok"
    EMPTY_QUERY = "empty_query"
    MODEL_RETURNED_NONE = "model_returned_none"
    FILTERED_TO_EMPTY = "filtered_to_empty"
    PARTIAL = "partial"
    """The request produced an answer, but some attempted work was interrupted.

    This can happen when part of a split plan fails or when a flat request returns records before
    a later continuation fails. A partial run holding no records is still partial when an answered
    attempt returned no rows and another attempted range was lost, because only the first range
    was observed.

    It takes ``outcome`` ahead of every cap, and the reason is which facts have somewhere
    else to live. A continuation cap has ``stopped_by``; a row trim has ``truncated_to``; a
    lost part has nowhere but here. So this field goes to the fact that would otherwise be
    erased, and the others are read where they already are. That does not invert the rule
    that a ceiling outranks what a run produced — that rule is about the shape of the answer,
    and this is not a claim about shape."""

    STOPPED_BY_LIMIT = "stopped_by_limit"
    TRUNCATED = "truncated"

    ALL = (
        OK,
        EMPTY_QUERY,
        MODEL_RETURNED_NONE,
        FILTERED_TO_EMPTY,
        PARTIAL,
        STOPPED_BY_LIMIT,
        TRUNCATED,
    )


STOPPED_BY_CONTINUATIONS: Final = "plan.max_continuations"
"""A leaf reached its continuation cap after an answer that was still adding records.

Completeness was not established, which is not the same as saying more records exist: the
last permitted response is identical whether the model had one row left or a thousand.

Prefixed, because it is a ``plan`` setting and not a ``limits`` one. A caller reading a
budget key here would go looking through ceilings they never crossed; a caller reading this
one knows which knob to raise."""

STOPPED_BY_OUTPUT: Final = "provider_output_limit"
"""The provider reported that its output allowance ended the response.

Named for the provider rather than the model because ``llm.max_tokens`` may be the caller's
own setting, and the adapter is the witness either way. Reported only where the provider
actually said so: a response that merely failed to parse whole is
:data:`STOPPED_BY_INCOMPLETE_RESPONSE`, which claims less.

Asking again the same way truncates in the same place, so this is not a ceiling to raise but
a request to make smaller — or an output allowance to widen."""

STOPPED_BY_INCOMPLETE_RESPONSE: Final = "response_incomplete"
"""A response did not parse whole and its complete records were kept.

What was observed, with no cause attached. The document ended mid-structure; whether an
output ceiling cut it or the model simply wrote it badly is not visible from the text, and
the commonest configuration — a caller's plain callable — can never report which. Claiming
the ceiling here would hand the strongest available explanation to the case with the least
evidence for it."""


@dataclass
class ExecMeta:
    """What happened during a fetch, alongside the records themselves."""

    gaps: list[str] = field(default_factory=list[str])
    """Observed failures, discarded output, interrupted work, or estimate-based warnings.

    Coverage warnings compare accepted estimates or complete identities before row capping
    with model lower estimates. They do not change the execution outcome or prove omissions.
    An empty list means no supported diagnostic fired. It does not establish that every
    real-world row was found.
    """

    outcome: str = Outcome.OK
    model: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    latency_seconds: float = 0.0
    cache_hits: int = 0
    requested_attrs: list[str] = field(default_factory=list[str])
    row_estimate: int | None = None
    """Accepted non-flat bootstrap row-count hint for the root slice.

    ``None`` means flat mode or no usable estimate. This is planning input reported for
    operations, not a measured row count or a completeness claim.
    """
    cardinality_estimate: dict[str, object] | None = None
    """Structured uncertain estimate; ``row_estimate`` remains its point-value alias."""
    primary_keys: list[str] = field(default_factory=list[str])
    observed_key_count: int = 0
    marginal_key_yields: list[int] = field(default_factory=list[int])
    incomplete_key_records: int = 0
    normalization_collisions: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    repaired_numbers: int = 0
    """Numbers the model wrote with thousands separators, such as ``4,470``, that were read
    as ``4470``. Such a response is not valid JSON; it is read only after this one repair,
    so a non-zero count says the parser changed the model's text before reading it."""
    split_decision: str | None = None
    """What the root probe decided under ``plan.split_policy="evidence"``, or None.

    ``probe_accepted``: the root answered in one call and the plan did not split.
    ``probe_split_length``: the provider said its output allowance ended the probe's answer.
    ``probe_split_incomplete``: the probe's answer ended mid-document and only its complete
    records were read, with no word from the provider on why.
    ``probe_split_shortfall``: the probe held fewer than half the keys an estimate above
    ``plan.split_threshold_rows`` expects. ``probe_split_failed``: the probe gave no usable
    answer. None under the default policy, when the root could not split and was not
    probed, or when ``plan.exact_rows=0`` answered the root without a call. Absent from
    :meth:`FetchResult.to_dict` and ``meta_view`` when None."""
    filter_contradictions: int = 0
    """Records whose checkable filter column contradicts the filter of the leaf that returned
    them, counted before reduction and once per record.

    Zero means none was observed, not that every leaf obeyed: a filter on an undeclared or
    untyped column, or a value its declared type cannot read, is not checked. Under
    ``plan.filter_enforcement="flag"`` these records are kept; under ``"drop"`` they are
    removed at their leaf and this still counts them. The comparison rule is in
    :mod:`blue_data_llm_source.reduction.obedience`."""
    observed_keys: list[str] = field(default_factory=list[str])
    unmapped_keys: list[str] = field(default_factory=list[str])
    remapped_keys: dict[str, str] = field(default_factory=dict[str, str])
    asked: str | None = None
    """The request as it was actually sent, when a model rewrote it. None when nothing did.

    Outside ``flat``, analysis may rewrite the request for retrieval; every part runs the
    rewritten query. The prompt asks the model to preserve scope, but this is not checked.
    Publishing the rewrite lets callers inspect any broadening or narrowing."""

    record_origins: list[list[str]] = field(default_factory=list[list[str]])
    """Which parts of a split request produced each record, parallel to ``records``.

    A list per record because overlapping parts can each return the same record and all of
    them are true. Empty when the run made no records.

    It follows the records rather than the merge that computed it. Between the merge and the
    caller, alignment rebuilds every record and three passes change the length of the list —
    empty records are dropped, exact duplicates are dropped again, and the row cap trims the
    tail — so a list published as the merge produced it would be indexed against records
    that no longer exist. Attribution lined up against the wrong rows is worse than none,
    because it looks right.

    It names the parts that returned a record. It does not claim they are the only parts
    that hold it."""

    type_mismatches: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    key_collisions: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    # Declared type names this library cannot check. The name is sent to the model as an
    # instruction, so an unlisted one is legitimate; but a misspelled one checks nothing
    # while the caller believes their records are typed, so it is reported here — once,
    # against the request — rather than as a mismatch against every value.
    unchecked_types: list[str] = field(default_factory=list[str])
    truncated_to: int | None = None

    stop_reasons: list[str] = field(default_factory=list[str])
    """Ordered distinct facts that limited extraction or discarded final output."""

    stopped_by: str | None = None
    """The highest-precedence member of ``stop_reasons``, or None when it is empty."""
    accounting_known: bool = True
    """Whether ``calls``, ``cost`` and the token counts describe provider work.

    False when the answer came from a caller-supplied ``llm_fn``. That callable may answer
    from memory, contact one provider, or fan out to several, and none of it is visible from
    here. ``FunctionClient`` therefore declares only the row capability. When this is
    False the accounting fields describe this library's own bookkeeping and nothing else, and
    a planner or audit log should treat provider spend as unknown rather than zero.
    """


@dataclass
class Estimate:
    """What one flat fetch could cost at the configured rates, without provider contact.

    For a caller deciding whether to run a request at all — a planner sizing a fan-out, or
    an operator checking a request against a ceiling before spending anything on it.

    ``max_cost`` is an upper bound at the package or configured rates, not a prediction. It
    reserves the whole output allowance at the model's output rate and bounds the prompt by
    its encoded length, so a typical call costs a fraction of it. A figure exceeded at
    those rates is not a bound, and a decision made against one is a guess.

    ``max_cost`` bounds the whole of a ``flat`` fetch, including retries. It accumulates
    the per-attempt figure by repeated addition, as the budget ledger does. Multiplication
    can round differently above five attempts and yield a bound below the ledger's charge.

    ``bounded`` is False when no bound can be established at all, with ``reason`` saying
    what is missing. That is a normal answer rather than an error, because "how much might
    this cost" is a reasonable question to ask about a model this package cannot price.
    """

    bounded: bool = False
    max_cost: float | None = None
    reason: str | None = None
    model: str = ""
    prompt_bytes: int = 0
    max_output_tokens: int = 0
    # A fetch may retry, so the whole-fetch bound is the per-attempt bound accumulated once
    # per attempt allowed, matching how the ledger accumulates it. Both are reported: a
    # caller sizing a ceiling needs the first, and a caller reading a receipt afterwards
    # will see the second.
    max_attempts: int = 1
    max_cost_per_attempt: float | None = None
    attempts_within_ceiling: int | None = None
    """How many of ``max_attempts`` fit under the request's cost ceiling, counted the way the
    ledger admits them, each at its per-attempt bound. ``0`` with attempts allowed means the
    first attempt is refused before any call. An empty request costs nothing, so all of its
    ``max_attempts`` fit. None when no cost ceiling is set or no bound could be given."""


@runtime_checkable
class PlanningReceiptProtocol(Protocol):
    def to_dict(self) -> dict[str, object]: ...


@dataclass
class FetchResult:
    """Records returned by a fetch, with the metadata describing the run."""

    records: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    meta: ExecMeta = field(default_factory=ExecMeta)

    @property
    def planning_receipt(self) -> PlanningReceiptProtocol | None:
        return getattr(self, "_planning_receipt", None)

    def with_planning_receipt(self, receipt: PlanningReceiptProtocol | None) -> FetchResult:
        self._planning_receipt = receipt
        return self

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = asdict(self)
        meta = cast("dict[str, object]", payload["meta"])
        # Written only when a root probe ran, so a result from the default policy serializes
        # exactly as it did before the field existed.
        if meta.get("split_decision") is None:
            meta.pop("split_decision", None)
        receipt = self.planning_receipt
        if receipt is not None:
            payload["planning_receipt"] = receipt.to_dict()
        return payload

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.records)

    @property
    def is_empty(self) -> bool:
        return not self.records


@runtime_checkable
class LlmClientProtocol(Protocol):
    """The model boundary. Any object with these two methods can be injected."""

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str: ...

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str: ...


class Capability:
    """What a client can be held to, per axis.

    Capabilities must be declared; a ``budget`` attribute alone does not prove that
    a client enforces a ceiling. A wrapper around a caller's function may bound
    calls without being able to bound cost or time.
    """

    CALLS = "calls"
    ROWS = "rows"
    SECONDS = "seconds"
    COST = "cost"
    # Not a ceiling but the same kind of claim: a client that can say what an attempt will
    # cost before making it. Only a client that drives the provider itself can, so an
    # estimate over an opaque one reports that it cannot be given rather than guessing.
    ESTIMATE = "estimate"
    """This client can be asked what one attempt will cost, and it respects the attempt
    allowance.

    Declaring it commits a client to two things, because ``estimate`` reports a whole-fetch
    bound and reaches it by accumulating the per-attempt figure once per permitted attempt:

    1. ``bound_for_attempt(prompt, properties)`` returns a true upper bound on one provider
       attempt with those arguments.
    2. One call makes at most ``budget.effective_retries() + 1`` provider attempts, where
       ``budget`` is the one handed down in ``properties``. A client with its own retry or
       fallback policy that ignores this can spend more than the estimate published, and a
       caller sizing a ceiling from that estimate then has an attempt refused.

    A client that cannot commit to the second simply omits this capability: the estimate then
    reports ``bounded=False`` with a reason instead of a figure the run can exceed."""

    ALL = frozenset({CALLS, ROWS, SECONDS, COST, ESTIMATE})


@runtime_checkable
class BudgetAwareClient(Protocol):
    """A client that states which ceilings it can be held to, and honors them.

    ``budget_capabilities`` is a method rather than an attribute so that satisfying this
    protocol takes a deliberate implementation instead of a name that happens to match. It
    returns the axes this client will enforce against the budget handed to it for a call;
    the source refuses any configured ceiling on an axis not returned.

    See :class:`~blue_data_llm_source.llm.budget.Budget` and :class:`Capability`.
    """

    def budget_capabilities(self) -> frozenset[str]: ...

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str: ...

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str: ...


@runtime_checkable
class CacheStore(Protocol):
    """Key-value cache for whole-fetch results."""

    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any) -> None: ...
