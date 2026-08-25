"""Shared data contracts and the exception hierarchy."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


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


@dataclass
class Usage:
    """Per-call provider accounting."""

    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    latency_seconds: float = 0.0
    cache_hit: bool = False
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
    STOPPED_BY_LIMIT = "stopped_by_limit"
    TRUNCATED = "truncated"

    ALL = (
        OK,
        EMPTY_QUERY,
        MODEL_RETURNED_NONE,
        FILTERED_TO_EMPTY,
        STOPPED_BY_LIMIT,
        TRUNCATED,
    )


@dataclass
class ExecMeta:
    """What happened during a fetch, alongside the records themselves."""

    outcome: str = Outcome.OK
    model: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    latency_seconds: float = 0.0
    cache_hits: int = 0
    requested_attrs: list[str] = field(default_factory=list[str])
    observed_keys: list[str] = field(default_factory=list[str])
    unmapped_keys: list[str] = field(default_factory=list[str])
    remapped_keys: dict[str, str] = field(default_factory=dict[str, str])
    type_mismatches: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    key_collisions: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    # Declared type names this library cannot check. The name is sent to the model as an
    # instruction, so an unlisted one is legitimate; but a misspelled one checks nothing
    # while the caller believes their records are typed, so it is reported here — once,
    # against the request — rather than as a mismatch against every value.
    unchecked_types: list[str] = field(default_factory=list[str])
    truncated_to: int | None = None
    stopped_by: str | None = None
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
    """What one fetch could cost at most, computed without contacting the provider.

    For a caller deciding whether to run a request at all — a planner sizing a fan-out, or
    an operator checking a request against a ceiling before spending anything on it.

    ``max_cost`` is an upper bound, not a prediction. It reserves the whole output
    allowance at the model's output rate and bounds the prompt by its encoded length, so a
    typical call costs a fraction of it. Being wrong high is the only safe direction: a
    figure that can be exceeded is not a bound, and a decision made against one is a
    decision made against a guess.

    ``max_cost`` bounds the whole fetch: the per-attempt figure accumulated once per
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


@dataclass
class FetchResult:
    """Records returned by a fetch, with the metadata describing the run."""

    records: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    meta: ExecMeta = field(default_factory=ExecMeta)

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
