"""Shared data contracts and the exception hierarchy."""

from __future__ import annotations

import json
import math
from collections.abc import Hashable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any, Final, Protocol, TypeAlias, runtime_checkable


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


JsonScalar: TypeAlias = str | int | float | bool
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


Constraint = Exact | OneOf
"""What a leaf may say about a field, tagged with which of the two it is.

The tag is the point. A filter held as an untagged scalar or list is one absent-minded
``join``, ``str()``, or "treat every entry as required" away from asking a different
question than the caller asked, and each of those reads as ordinary code. Held as a tagged
pair, the same mistakes stop type-checking: there is no string to join and no sequence to
iterate without first saying which kind of constraint is in hand.

That is not hypothetical tidiness. Joined with ``", "``, the alternatives ``['A, B', 'C']``
and ``['A', 'B', 'C']`` produce one identical prompt, so a request about one region whose
name contains a comma and a request about two regions become the same question, and nothing
in the result says which was answered.
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
    termination reason it does not recognise to ``"stop"``, so believing it would turn an
    unrecognised truncation into a claim that the answer is complete.

    None rather than ``"stop"`` when the field is missing or malformed. A provider that said
    nothing and a provider that reported a clean stop are different facts, and defaulting one
    to the other invents an attestation nobody made."""

    cost_known: bool = True
    """Whether ``cost`` is what the provider charged.

    False when the provider's own accounting could not be read for this attempt. The
    reservation's bound then stands as the charge for enforcement, which is the safe thing to
    do with a ceiling, but it is not a measurement — and where no cost ceiling is configured
    there is no bound either, so the figure is zero and means nothing. Reporting that zero as
    a known cost is how an unpriced call comes to look free."""


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
    """Observed failures, discarded output, or interrupted work.

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
    observed_keys: list[str] = field(default_factory=list[str])
    unmapped_keys: list[str] = field(default_factory=list[str])
    remapped_keys: dict[str, str] = field(default_factory=dict[str, str])
    asked: str | None = None
    """The request as it was actually sent, when a model rewrote it. None when nothing did.

    Outside ``flat``, an analysis call restates the request for retrieval, and that restated
    text becomes the query every part runs. It has to *be* the query rather than sit beside
    the caller's original, because a request like "I don't know how many countries are
    there?" is a statement of ignorance shaped like a question and is not retrievable as
    written; the rewrite is what makes it answerable.

    Which means the rewrite can also broaden or narrow what was asked, and the only thing
    against that is a sentence in the analysis prompt telling the model not to. That is an
    instruction, not a check. Nothing here guards it: the example that makes the rewrite
    worth keeping is precisely the one that defeats any cheap relatedness test, since a
    useful rewrite of it shares almost no words with the original, and a guard loose enough
    to pass it would catch nothing.

    So the change is reported instead of prevented. Reporting costs nothing, cannot misfire,
    and turns an invisible substitution into one a caller can see — which is the same move
    as publishing attribution, and the honest half of a limitation that stays published."""

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
    here — which is why ``FunctionClient`` declares only the row capability. Reporting
    ``calls=1`` and ``cost=0.0`` for it looked exact and was invented: a callable making five
    provider calls and spending real money reported one call and nothing spent. When this is
    False the accounting fields describe this library's own bookkeeping and nothing else, and
    a planner or audit log should treat provider spend as unknown rather than zero.
    """


@dataclass
class Estimate:
    """What one flat fetch could cost at most, computed without contacting the provider.

    For a caller deciding whether to run a request at all — a planner sizing a fan-out, or
    an operator checking a request against a ceiling before spending anything on it.

    ``max_cost`` is an upper bound, not a prediction. It reserves the whole output
    allowance at the model's output rate and bounds the prompt by its encoded length, so a
    typical call costs a fraction of it. Being wrong high is the only safe direction: a
    figure that can be exceeded is not a bound, and a decision made against one is a
    decision made against a guess.

    ``max_cost`` bounds the whole of a ``flat`` fetch: the per-attempt figure accumulated once per
    attempt, added the way the budget ledger adds it rather than multiplied. A fetch may
    retry, so the per-attempt figure each reservation claims is not what a caller should
    size a ceiling against — reporting it as the fetch's meant a request sized against its
    own estimate was refused when a single transient failure consumed the ceiling. The
    accumulation is not pedantry: repeated addition and multiplication diverge above five
    attempts, and a ceiling set from the product would refuse the final attempt of a
    request sized against its own estimate.

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

    Declared rather than inferred. An earlier version accepted any client carrying an
    attribute named ``budget`` as evidence that ceilings would bind, which a client with
    ``budget = None`` satisfied while making unbounded calls. Attribute presence is not
    evidence of behaviour, and the axes differ: a wrapper around a caller's function can
    prove it made one call and cannot prove what that call cost or how long it may run.
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
    reports ``bounded=False`` with a reason, which is the honest answer rather than a figure
    the run can exceed."""

    ALL = frozenset({CALLS, ROWS, SECONDS, COST, ESTIMATE})


@runtime_checkable
class BudgetAwareClient(Protocol):
    """A client that states which ceilings it can be held to, and honours them.

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
