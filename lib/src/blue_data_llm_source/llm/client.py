"""The default model client.

Derived from the client shipped in the operator library, which was itself derived from
the research prototype. The behaviours kept from it are the ones that were learned the
hard way: a closed override key set so an unknown knob fails loudly, control keys that
never reach the provider, reserved fields stripped after every merge, accounting that
cannot raise, cache hits that cost nothing but still record latency, per-event-loop
concurrency bounds, and response-shape validation before extraction.

Two things are new. Injection is structural rather than an isinstance check against this
class, so any object with ``complete`` and ``acomplete`` can be substituted. And spend is
reserved before the provider is contacted rather than recorded afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from collections import OrderedDict
from collections.abc import AsyncIterable, Callable, Iterable
from typing import Any, ClassVar, Protocol, cast

from tenacity import (
    AsyncRetrying,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.stop import stop_base
from tenacity.wait import wait_base

from blue_data_llm_source import _constants as C
from blue_data_llm_source.llm import pricing
from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.types import (
    BudgetExceeded,
    Capability,
    ConfigError,
    TransportError,
    Usage,
)

# Accepted llm.* overrides. An unknown key raises rather than passing through, because a
# silently ignored setting is how the prior implementation ended up with recorded run
# configs that did not describe what ran.
_ACCEPTED = {
    "model",
    "temperature",
    "max_tokens",
    "stream",
    "timeout",
    "cache",
    "cache_dir",
    "max_parallel",
    "price_per_input_token",
    "price_per_output_token",
}
# Consumed here, never forwarded to the provider.
_CONTROL = {
    "cache",
    "cache_dir",
    "max_parallel",
    "price_per_input_token",
    "price_per_output_token",
}
# Client-side call spacing must coordinate with per-attempt budget admission. Until one
# mechanism owns both, the setting is refused by name instead of claiming a limit it cannot keep.
_DEFERRED = {
    "rate_limit": (
        "client-side call spacing is deferred to v2.0. Use 'llm.max_parallel' to cap "
        "concurrency, or throttle above this library where the whole request sequence is "
        "visible"
    ),
}
# Stripped after every merge so an override cannot smuggle transport fields through.
_RESERVED = {"api", "service_url", "variant", "protocol"}


def check_adapter_keys(properties: dict[str, Any]) -> None:
    """Refuse an unknown ``llm.*`` override, whichever client will answer.

    Checked at the source boundary rather than inside the call-argument builder, because
    only the built-in client reaches that builder: a typo alongside an injected client or an
    ``llm_fn`` was accepted and ignored, which is exactly what a closed key set is for.
    """
    parallel = properties.get("llm.max_parallel")
    if parallel is not None and (
        isinstance(parallel, bool) or not isinstance(parallel, int) or parallel < 1
    ):
        # Validated here rather than where the semaphore is built. A negative value escaped
        # the public fetch as a raw `ValueError` from asyncio, outside this package's error
        # hierarchy, and zero built a semaphore with no permits that waited for ever unless
        # a time ceiling happened to be set.
        raise ConfigError(
            f"'llm.max_parallel' must be a whole number of at least 1, got {parallel!r}"
        )
    deferred = sorted(key for key in properties if key.startswith("llm.") and key[4:] in _DEFERRED)
    if deferred:
        first = deferred[0]
        raise ConfigError(f"{first!r} is not supported: {_DEFERRED[first[4:]]}")
    unknown = sorted(
        key for key in properties if key.startswith("llm.") and key[4:] not in _ACCEPTED
    )
    if unknown:
        raise ConfigError(
            f"unknown adapter override {', '.join(repr(k) for k in unknown)}. "
            f"Accepted: {sorted('llm.' + k for k in _ACCEPTED)}"
        )


DEFAULT_MODEL = "gpt-4o"


class _Provider(Protocol):
    """The litellm entry points this client calls.

    litellm carries its own annotations, but many of them are bare ``List`` and ``dict``,
    so a strict checker sees the whole signature as unknown. Declaring the surface we
    actually use keeps the call sites checked and says in one place what this client
    needs from the provider library.
    """

    def completion(self, **kwargs: Any) -> Any: ...

    async def acompletion(self, **kwargs: Any) -> Any: ...

    def stream_chunk_builder(self, chunks: list[Any]) -> Any: ...


class _StopAtDeadline(stop_base):
    """Stop retrying once the run's time ceiling has passed."""

    def __init__(self, budget: Budget) -> None:
        self._budget = budget

    def __call__(self, retry_state: Any) -> bool:
        remaining = self._budget.remaining_seconds()
        return remaining is not None and remaining <= 0


class _WaitWithin(wait_base):
    """Exponential backoff, never longer than the run has left.

    Waiting past the deadline and then refusing the attempt bounds nothing: the time is
    already spent. Capping the wait is what makes the axis bound the run rather than a
    limit on when an attempt may begin.
    """

    def __init__(self, budget: Budget) -> None:
        self._budget = budget
        self._backoff = wait_exponential(multiplier=0.1, max=2)

    def __call__(self, retry_state: Any) -> float:
        planned = float(self._backoff(retry_state))
        remaining = self._budget.remaining_seconds()
        return planned if remaining is None else max(0.0, min(planned, remaining))


def _deadline_message(budget: Budget) -> str:
    """Name the ceiling that actually bound, which may be a caller's rather than this one's.

    Reading only this budget's own limit produced "time limit reached (Nones)" whenever the
    deadline was inherited — every caller-owned budget — and named a looser local ceiling
    whenever the caller's was the tighter of the two.
    """
    limit, is_own = budget.binding_deadline()
    if limit is None:
        return "time limit reached"
    if is_own:
        return f"time limit reached ({limit}s)"
    return f"time limit reached ({limit}s, inherited from the caller's budget)"


def _positive_finite_number(value: Any) -> float | None:
    """A finite positive number, or None. Shared by every numeric adapter override."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _safe_int(value: Any) -> int:
    # Accounting is total by contract. Anything inconvertible counts as zero rather than
    # breaking a call that already succeeded.
    try:
        return int(value or 0)
    except Exception:
        return 0


class DefaultLlmClient:
    """Calls a provider through litellm and reports what each call cost."""

    def __init__(
        self,
        properties: dict[str, Any] | None = None,
        budget: Budget | None = None,
    ) -> None:
        self.properties: dict[str, Any] = dict(properties or {})
        self.budget = budget or Budget()
        self.last_usage: Usage | None = None
        self.usages: list[Usage] = []
        # Ordered and bounded. Every distinct `llm.cache_dir` opens a diskcache backend
        # holding SQLite descriptors, and nothing ever closed them: a retained client
        # rotating cache directories — one per tenant, per day, per run — accumulated one
        # open backend per directory until the process ran out of file descriptors, at which
        # point unrelated `open()` calls elsewhere in the program started failing too. The
        # least recently opened backend is closed when the bound is reached, and `close()`
        # releases them all.
        self._caches: OrderedDict[str, Any] = OrderedDict()
        # Keyed by the loop object, not by its address: an address-keyed map grew without
        # bound and could hand a live call a semaphore belonging to a closed loop whose
        # address had been recycled.
        #
        # A weak key is not enough on its own. A semaphore binds to its loop as soon as a
        # caller actually waits on it, so the map's own value holds the key alive and
        # nothing is ever collected. Closed loops are dropped on access instead, which is
        # cheap and does not depend on what the value happens to reference.

    # ------------------------------------------------------------------ public --
    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        started = time.monotonic()
        merged = {**self.properties, **(properties or {})}
        kwargs = self._call_kwargs(prompt, merged)

        # Elapsed time is admitted before the cache is read, which is what `acomplete` does
        # and what the architecture document promises for a cache hit: it consumes no
        # attempt, no tokens and no cost, "and admission still checks elapsed time." Only
        # the async path did it, so a run already past its deadline kept answering from
        # cache through this one indefinitely. Reachable only by a caller driving the client
        # directly, since the library's own path is async, but the two entry points should
        # not disagree about whether a ceiling has been crossed.
        deadline_budget = self._budget_for(merged) or self.budget
        remaining_seconds = deadline_budget.remaining_seconds()
        # `is not None` rather than a truth test: a budget with no time left reports exactly
        # 0.0, which is falsy, so `or` would read a spent deadline as unlimited headroom.
        if remaining_seconds is not None and remaining_seconds <= 0:
            raise BudgetExceeded(_deadline_message(deadline_budget), limit="max_seconds")

        cache, key = self._cache_for(merged, kwargs)
        if cache is not None and key is not None:
            # get/set only, which is what the published CacheStore protocol declares.
            # Driving the store with `in` and `[]` would work for diskcache and break
            # any store written to the documented interface.
            payload = cache.get(key)
            if payload is not None:
                self._record(kwargs, payload, started, cache_hit=True, properties=merged)
                return _extract_text(payload)

        payload, cost, cost_known = self._call(kwargs, self._budget_for(merged), merged)
        if cache is not None and key is not None:
            _cache(cache, key, payload)
        self._record(
            kwargs,
            payload,
            started,
            cache_hit=False,
            cost=cost,
            cost_known=cost_known,
            properties=merged,
        )
        return _extract_text(payload)

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        """Answer *prompt*, cancelling any wait that outlives the run's time ceiling.

        The deadline surrounds the whole awaited path rather than the retry loop alone.
        Capping only retry backoff would leave the semaphore queue and provider wait outside it.

        What this does not bound is work that never yields, and there is no way to bound it
        from here: ``asyncio`` cannot interrupt code that is not awaiting, and a thread only
        defers the cost to loop shutdown. ``max_seconds`` is therefore a bound on admission
        and on waiting, not on the run. ADR-024 and the library README hold the one list of
        exclusions; a caller needing a hard stop imposes it above this library.

        A run that finished over its ceiling raises rather than returning a late answer as a
        success: a ceiling that fails silently is worse than one that reports late.

        The deadline cancels the wait and then waits for acknowledgement. Work that does not
        acknowledge cancellation holds the call open; abandoning it would detach accounting
        and caller cancellation from the provider work.

        Expiry becomes ``BudgetExceeded`` naming ``max_seconds``, including when the work
        ends in a transport failure after the deadline has already passed: the run was over
        before the failure mattered, and reporting the transport error instead hides which
        ceiling ended it. A ``TimeoutError`` raised from inside while budget remains is the
        caller's own failure and propagates unchanged.
        """
        merged = {**self.properties, **(properties or {})}
        budget = self._budget_for(merged) or self.budget
        remaining = budget.remaining_seconds()
        if remaining is None:
            return await self._answer(prompt, merged)
        if remaining <= 0:
            raise BudgetExceeded(_deadline_message(budget), limit="max_seconds")
        # `wait_for` rather than a detached task. Detaching released the caller from work
        # that declines cancellation, and cost more than it bought: cancelling the caller no
        # longer reached the child, the child could be collected mid-flight or hold
        # `asyncio.run` open at shutdown, and it settled real cost into the caller's budget
        # after a result reporting zero had already been returned. A library sharing one
        # loop with its caller has no isolation boundary to offer, so the honest move is the
        # one ADR-024 already made: bound what can be bounded and document the rest.
        try:
            answer = await asyncio.wait_for(self._answer(prompt, merged), timeout=remaining)
        except (TimeoutError, asyncio.TimeoutError) as e:
            # Only when the budget really is spent. A cache store or a client raising its own
            # TimeoutError is not this ceiling, and reporting it as one hid the real failure
            # behind a limit that had not expired.
            left = budget.remaining_seconds()
            if left is not None and left > 0:
                raise
            raise BudgetExceeded(_deadline_message(budget), limit="max_seconds") from e
        except TransportError:
            expired = budget.remaining_seconds()
            if expired is not None and expired <= 0:
                raise BudgetExceeded(_deadline_message(budget), limit="max_seconds") from None
            raise
        # `wait_for` waits for the cancellation to be acknowledged. Work that catches
        # CancelledError and goes on to return hands back a normal answer from after the
        # deadline, which is the ceiling failing silently rather than late. The work itself
        # cannot be stopped from here, but a run that finished over its ceiling reports that
        # rather than succeeding.
        left = budget.remaining_seconds()
        if left is not None and left <= 0:
            raise BudgetExceeded(_deadline_message(budget), limit="max_seconds")
        return answer

    async def _answer(self, prompt: str, merged: dict[str, Any]) -> str:
        started = time.monotonic()
        kwargs = self._call_kwargs(prompt, merged)

        cache, key = self._cache_for(merged, kwargs)
        if cache is not None and key is not None:
            # get/set only, which is what the published CacheStore protocol declares.
            # Driving the store with `in` and `[]` would work for diskcache and break
            # any store written to the documented interface.
            #
            # Read directly. A cache store is a caller's object, and a blocking
            # implementation cannot be bounded by the deadline from here: cancelling the
            # await does not stop the work, and moving it to a thread only defers the cost
            # to loop shutdown. The ceiling's scope is documented rather than overstated.
            payload = cache.get(key)
            if payload is not None:
                self._record(kwargs, payload, started, cache_hit=True, properties=merged)
                return _extract_text(payload)

        async with self._semaphore(merged):
            payload, cost, cost_known = await self._acall(kwargs, self._budget_for(merged), merged)
        if cache is not None and key is not None:
            _cache(cache, key, payload)
        self._record(
            kwargs,
            payload,
            started,
            cache_hit=False,
            cost=cost,
            properties=merged,
            cost_known=cost_known,
        )
        return _extract_text(payload)

    # --------------------------------------------------------------- internals --
    def _call_kwargs(self, prompt: str, properties: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"messages": [{"role": "user", "content": prompt}]}

        for key, value in properties.items():
            if not key.startswith("llm."):
                continue
            short = key[4:]
            if short in _DEFERRED:
                raise ConfigError(f"{key!r} is not supported: {_DEFERRED[short]}")
            if short not in _ACCEPTED:
                raise ConfigError(
                    f"unknown adapter override '{key}'. Accepted: "
                    f"{sorted('llm.' + k for k in _ACCEPTED)}"
                )
            if short not in _CONTROL:
                kwargs[short] = value

        for reserved in _RESERVED:
            kwargs.pop(reserved, None)

        kwargs.setdefault("model", properties.get("llm.model") or DEFAULT_MODEL)
        if not kwargs.get("model"):
            raise ConfigError("no model configured (set llm.model)")
        return kwargs

    def _with_deadline(
        self, kwargs: dict[str, Any], budget: Budget | None = None
    ) -> dict[str, Any]:
        """Cap the provider timeout at the time budget still left."""
        existing_raw = kwargs.get("timeout")
        if existing_raw is not None:
            checked = _positive_finite_number(existing_raw)
            if checked is None:
                raise ConfigError(
                    f"llm.timeout must be a finite positive number, got {existing_raw!r}. "
                    f"NaN survives min() and would be sent to the provider as a timeout."
                )
        remaining = (budget or self.budget).remaining_seconds()
        if remaining is None:
            return kwargs
        out = dict(kwargs)
        existing = out.get("timeout")
        out["timeout"] = remaining if existing is None else min(float(existing), remaining)
        return out

    def _cache_for(
        self, properties: dict[str, Any], kwargs: dict[str, Any]
    ) -> tuple[Any | None, str | None]:
        store = properties.get("cache_store")
        if store is not None:
            return store, json.dumps(kwargs, sort_keys=True, default=str)
        if not properties.get("llm.cache"):
            return None, None
        # Bound per directory so a per-call cache_dir override gets its own cache
        # instead of silently reusing the first one opened.
        cache_dir = str(properties.get("llm.cache_dir", ".llm_cache"))
        cache = self._caches.get(cache_dir)
        if cache is None:
            try:
                import diskcache  # pyright: ignore[reportMissingTypeStubs]
            except ImportError as e:
                raise ConfigError(
                    "llm.cache requested but diskcache is not installed "
                    "(pip install 'blue-data-llm-source[llm]')"
                ) from e
            cache = diskcache.Cache(cache_dir)
            while len(self._caches) >= _MAX_OPEN_CACHES:
                _, evicted = self._caches.popitem(last=False)
                _close_quietly(evicted)
            self._caches[cache_dir] = cache
        else:
            # Most recently used, so the bound evicts the coldest backend rather than
            # whichever happened to be opened first.
            self._caches.move_to_end(cache_dir)
        return cache, json.dumps(kwargs, sort_keys=True, default=str)

    def close(self) -> None:
        """Release every cache backend this client has opened.

        A library that documents itself as embeddable in a long-lived service has to give
        callers a way to let go of its handles. Safe to call more than once, and the client
        stays usable afterwards — a later call reopens what it needs.
        """
        while self._caches:
            _, cache = self._caches.popitem()
            _close_quietly(cache)

    def __enter__(self) -> DefaultLlmClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # Shared by every instance, not per instance. `LlmSource` builds a fresh client for each
    # fetch unless the caller injects one, so a per-instance registry gave every concurrent
    # fetch its own full-permit semaphore and the ceiling bounded nothing at all on the
    # default path — forty concurrent fetches at max_parallel=1 ran forty provider calls at
    # once. The documented scope is per event loop, which is what this restores; the entries
    # are keyed by loop and dropped when the loop closes, so sharing adds no lifetime.
    _semaphores: ClassVar[OrderedDict[asyncio.AbstractEventLoop, dict[int, asyncio.Semaphore]]] = (
        OrderedDict()
    )

    def _semaphore(self, properties: dict[str, Any]) -> asyncio.Semaphore:
        max_parallel = int(properties.get("llm.max_parallel", 8))
        loop = asyncio.get_running_loop()
        # A closed loop can have no live waiters, so its semaphores are dead weight and the
        # only thing keeping it reachable. Dropped here rather than left for a weak
        # reference that the semaphore itself prevents from ever firing.
        for stale in [known for known in self._semaphores if known.is_closed()]:
            del self._semaphores[stale]
        per_loop = self._semaphores.get(loop)
        if per_loop is None:
            per_loop = {}
            # Bounded, and least-recently-used first. Sweeping only closed loops was enough
            # while this map lived on the instance and died with it; at class scope an entry
            # outlives the client that made it, so a loop that is abandoned rather than
            # closed is pinned alive by the map itself — and with it every selector and
            # descriptor it holds. A hundred and fifty abandoned loops took a process from
            # thirty-eight open descriptors to four hundred and eighty-eight. The sibling
            # `_caches` map is bounded for exactly this reason.
            while len(self._semaphores) >= _MAX_TRACKED_LOOPS:
                self._semaphores.popitem(last=False)
            self._semaphores[loop] = per_loop
        else:
            self._semaphores.move_to_end(loop)
        sem = per_loop.get(max_parallel)
        if sem is None:
            sem = asyncio.Semaphore(max_parallel)
            per_loop[max_parallel] = sem
        return sem

    @staticmethod
    def _sink(properties: dict[str, Any]) -> list[Usage] | None:
        """Where this call should report its usage, when the caller supplied a place."""
        candidate = properties.get(C.USAGE_SINK_PROPERTY)
        return cast("list[Usage]", candidate) if isinstance(candidate, list) else None

    def _record_unpriced(self, kwargs: dict[str, Any], properties: dict[str, Any]) -> None:
        """Note a contacted attempt whose cost could not be read.

        A failed attempt reached the provider and may have been billed, and it appended no
        usage record at all — so a later successful retry was the only entry the result saw
        and the whole fetch reported its accounting as measured. The entry carries no tokens
        and no cost, because none are known; what it carries is that fact.
        """
        record = Usage(model=str(kwargs.get("model", "")), cost_known=False)
        self.last_usage = record
        self.usages.append(record)
        sink = self._sink(properties)
        if sink is not None:
            sink.append(record)

    def _record(
        self,
        kwargs: dict[str, Any],
        payload: dict[str, Any],
        started: float,
        cache_hit: bool,
        cost: float = 0.0,
        properties: dict[str, Any] | None = None,
        cost_known: bool = True,
    ) -> None:
        raw = payload.get("usage")
        usage_dict = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        # What the payload says answered, falling back to what was asked for. A routing alias
        # or a name the provider resolves to a dated model reported the request rather than
        # the answer, and the data model defines this field as the model that answered.
        answered = payload.get("model")
        record = Usage(
            model=str(answered)
            if isinstance(answered, str) and answered
            else str(kwargs.get("model", "")),
            prompt_tokens=0 if cache_hit else _safe_int(usage_dict.get("prompt_tokens")),
            completion_tokens=(0 if cache_hit else _safe_int(usage_dict.get("completion_tokens"))),
            cost=0.0 if cache_hit else cost,
            latency_seconds=time.monotonic() - started,
            cache_hit=cache_hit,
            # A cache hit really did cost nothing; anything else is known only if the
            # provider's own accounting could be read.
            cost_known=True if cache_hit else cost_known,
            finish_reason=_finish_reason(payload),
        )
        self.last_usage = record
        self.usages.append(record)
        sink = self._sink(properties or {})
        if sink is not None:
            # This call's own list. `usages` stays as the client's history for a caller
            # that wants it; a fetch reads only what it was handed.
            sink.append(record)

    def _retryer(self, budget: Budget | None = None) -> Retrying:
        """Build the retry policy from the configured limit.

        The policy is built per call rather than fixed by a decorator at import, because
        a decorator freezes ``stop_after_attempt`` before any configuration is read. That
        is how ``max_retries`` becomes a setting the code accepts and ignores, which is
        the specific failure this package is written to avoid.
        """
        effective = budget or self.budget
        attempts = max(1, effective.effective_retries() + 1)
        return Retrying(
            retry=retry_if_exception_type(TransportError),
            # The deadline stops the loop as well as the attempt count. Without it the wait
            # between attempts runs past a wall-clock ceiling unchecked: a one-millisecond
            # limit spent a hundred milliseconds in backoff before refusing the next
            # attempt, so the axis bounded when a call could start and not how long the
            # fetch took.
            stop=stop_after_attempt(attempts) | _StopAtDeadline(effective),
            wait=_WaitWithin(effective),
            reraise=True,
        )

    def _aretryer(self, budget: Budget | None = None) -> AsyncRetrying:
        effective = budget or self.budget
        attempts = max(1, effective.effective_retries() + 1)
        return AsyncRetrying(
            retry=retry_if_exception_type(TransportError),
            stop=stop_after_attempt(attempts) | _StopAtDeadline(effective),
            wait=_WaitWithin(effective),
            reraise=True,
        )

    @staticmethod
    def _import_provider() -> _Provider:
        """The provider library, or a message naming the extra that supplies it.

        Lazy because the extra is optional and the tests stub the module. Raised as a
        ConfigError for the same reason the cache import is: a missing optional extra is
        a configuration problem the caller can fix, not a transport failure.
        """
        try:
            import litellm
        except ImportError as e:
            raise ConfigError(
                "the llm provider is not installed (pip install 'blue-data-llm-source[llm]')"
            ) from e
        return cast("_Provider", litellm)

    def budget_capabilities(self) -> frozenset[str]:
        """Every axis. This client makes the provider call, so it can enforce all of them.

        Cost is included because :func:`pricing.bound_cost` either produces a figure that
        cannot be exceeded or refuses, and the refusal reaches the caller as a ConfigError
        naming what is missing rather than as a ceiling that quietly does not hold.
        """
        return Capability.ALL

    @staticmethod
    def _budget_for(properties: dict[str, Any]) -> Budget | None:
        """The run's budget for this call, if the caller handed one down."""
        candidate = properties.get(C.BUDGET_PROPERTY)
        return candidate if isinstance(candidate, Budget) else None

    def bound_for_attempt(self, prompt: str, properties: dict[str, Any]) -> float:
        """The bound this client would reserve for one attempt at *prompt*.

        Built from the same call arguments the attempt itself uses, so an estimate and the
        reservation cannot disagree about the model, the output cap, or the rates. Computing
        the figure a second way is how they came to differ by three orders of magnitude for
        an injected client, and by a substituted default model on the built-in one.

        Raises ConfigError when no bound can be established, which is the same refusal a
        configured cost ceiling would meet.
        """
        # Merged, like every other path. Reading only the per-call dict meant a client
        # constructed with its own model or rates estimated against defaults and admitted
        # against its own configuration, so an estimate inside the ceiling was followed by a
        # fetch refused before the provider was contacted — the one thing this figure exists
        # to prevent.
        merged = {**self.properties, **properties}
        return self._bound(self._call_kwargs(prompt, merged), merged)

    @staticmethod
    def _bound(kwargs: dict[str, Any], properties: dict[str, Any]) -> float:
        messages = cast("list[dict[str, Any]]", kwargs.get("messages") or [])
        text = "".join(str(m.get("content", "")) for m in messages)
        return pricing.bound_cost(
            text, str(kwargs.get("model", "")), kwargs.get("max_tokens"), properties
        )

    def _estimate(
        self, kwargs: dict[str, Any], budget: Budget, properties: dict[str, Any]
    ) -> float:
        """The bound reserved for one attempt, or nothing when no cost ceiling is set.

        Computed only when it will be enforced. A run with no cost ceiling should not be
        refused because this package has never heard of its model.
        """
        if budget.remaining_cost() is None:
            return 0.0
        return self._bound(kwargs, properties)

    def _call(
        self,
        kwargs: dict[str, Any],
        budget: Budget | None = None,
        merged: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], float, bool]:
        # Reserved per attempt rather than per operation. Reserving once around the whole
        # retry loop lets a run with two retries make three times the calls its limit
        # allows, which is the difference between a limit and a suggestion.
        effective = budget or self.budget
        merged = merged or {}
        for attempt in self._retryer(effective):
            with attempt:
                # Everything that can fail locally happens before the reservation, so a
                # failure there is genuinely uncharged.
                #
                # Admitted, then imported, then claimed. The bound needs no provider, and
                # importing one is not free: the shipped adapter fetches a remote price map
                # on first use, so a request a ceiling was always going to refuse should not
                # reach the network to be told so. Claiming before the import instead was
                # worse — the claim was visible to a sibling on a shared budget, which was
                # then terminally refused on an allowance the final ledger said was free.
                # This check takes nothing, so it cannot refuse anyone. The window it opens
                # is that a call admitted here may be refused by `reserve` a moment later,
                # after the import has run; that costs one wasted import and is the accepted
                # side of the trade.
                bound = self._estimate(self._with_deadline(kwargs, effective), effective, merged)
                effective.check_admissible(bound)
                provider = self._import_provider()
                # Re-derived after the import rather than before it. Importing can
                # consume part of the deadline without exhausting it, and the earlier
                # figure then sent the provider a timeout longer than the run had left:
                # one that honoured it answered at 1.8s under a 1.0s budget. The cost
                # bound does not move, since it reads model, prompt and allowance.
                call_kwargs = self._with_deadline(kwargs, effective)
                # `attempt`, not `reserve`. Its cleanup is installed before the claim
                # is taken, so there is no instant in which a claim exists and nothing
                # holds a handle that will resolve it — where an interrupt landed.
                with effective.attempt(bound) as reservation:
                    try:
                        payload, cost = _call_provider(provider, call_kwargs)
                    except TransportError:
                        # Contacted and failed. The estimate stands as the charge rather than
                        # being released, or every failed retry would be free.
                        reservation.keep_unpriced()
                        self._record_unpriced(kwargs, merged)
                        raise
                    except Exception as e:
                        reservation.keep_unpriced()
                        self._record_unpriced(kwargs, merged)
                        raise TransportError(str(e), code="provider_error", provider_error=e) from e
                    except BaseException:
                        # A cancelled task or an interrupt is still an attempt that reached the
                        # provider. Leaving its claim outstanding poisons a caller-owned budget
                        # for everything after it, so the estimate stands and the exit is
                        # re-raised unchanged.
                        reservation.keep_unpriced()
                        raise
                    if cost is None:
                        # Priced by its reservation. An unreadable cost is not a free call.
                        reservation.keep_unpriced()
                        return payload, reservation.estimate, False
                    reservation.settle(cost)
                    return payload, cost, True
        raise TransportError("retry policy exhausted without a result", code="no_result")

    async def _acall(
        self,
        kwargs: dict[str, Any],
        budget: Budget | None = None,
        merged: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], float, bool]:
        effective = budget or self.budget
        merged = merged or {}
        async for attempt in self._aretryer(effective):
            with attempt:
                # Admitted, imported, then claimed. The sync path above explains the order
                # and what it costs; there is one account of it, not two.
                bound = self._estimate(self._with_deadline(kwargs, effective), effective, merged)
                effective.check_admissible(bound)
                provider = self._import_provider()
                # Re-derived after the import rather than before it. Importing can
                # consume part of the deadline without exhausting it, and the earlier
                # figure then sent the provider a timeout longer than the run had left:
                # one that honoured it answered at 1.8s under a 1.0s budget. The cost
                # bound does not move, since it reads model, prompt and allowance.
                call_kwargs = self._with_deadline(kwargs, effective)
                # `attempt`, not `reserve`. Its cleanup is installed before the claim
                # is taken, so there is no instant in which a claim exists and nothing
                # holds a handle that will resolve it — where an interrupt landed.
                with effective.attempt(bound) as reservation:
                    try:
                        payload, cost = await _acall_provider(provider, call_kwargs)
                    except TransportError:
                        reservation.keep_unpriced()
                        self._record_unpriced(kwargs, merged)
                        raise
                    except Exception as e:
                        reservation.keep_unpriced()
                        self._record_unpriced(kwargs, merged)
                        raise TransportError(str(e), code="provider_error", provider_error=e) from e
                    except BaseException:
                        # A cancelled task or an interrupt is still an attempt that reached the
                        # provider, so the estimate stands and the exit is re-raised unchanged.
                        reservation.keep_unpriced()
                        raise
                    if cost is None:
                        reservation.keep_unpriced()
                        return payload, reservation.estimate, False
                    reservation.settle(cost)
                    return payload, cost, True
        raise TransportError("retry policy exhausted without a result", code="no_result")


def _call_provider(
    provider: _Provider, call_kwargs: dict[str, Any]
) -> tuple[dict[str, Any], float | None]:
    """One attempt, as a payload and what it cost, however the provider chose to send it."""
    response: Any = provider.completion(**call_kwargs)
    if not call_kwargs.get("stream"):
        return _materialize(response)
    assembled, usage_reported = _assemble(provider, _drain(response))
    return _materialize(assembled, usage_reported=usage_reported)


async def _acall_provider(
    provider: _Provider, call_kwargs: dict[str, Any]
) -> tuple[dict[str, Any], float | None]:
    response: Any = await provider.acompletion(**call_kwargs)
    if not call_kwargs.get("stream"):
        return _materialize(response)
    assembled, usage_reported = _assemble(provider, await _adrain(response))
    return _materialize(assembled, usage_reported=usage_reported)


def _drain(response: Any) -> list[Any]:
    """Read a streamed response into memory, chunk by chunk.

    Nothing partial can be handed back from here. A fetch parses one whole JSON document
    into records, so the caller sees an answer only after the last chunk has arrived, and a
    stream therefore takes at least as long as the same call unstreamed. It is supported
    because some provider configurations and proxies require or default to it, not to make
    anything arrive sooner.
    """
    if not hasattr(response, "__iter__"):
        raise TransportError(
            "'llm.stream' was set but the provider did not return a stream",
            code="bad_response",
        )
    return list(cast("Iterable[Any]", response))


async def _adrain(response: Any) -> list[Any]:
    """The same, for a provider that hands back an asynchronous iterator."""
    if hasattr(response, "__aiter__"):
        return [chunk async for chunk in cast("AsyncIterable[Any]", response)]
    # A wrapper that is only synchronously iterable is read as one. Reading it holds the
    # loop, which is the exclusion `acomplete` already documents for work that does not
    # yield, and is better than refusing a stream this path can plainly read.
    return _drain(response)


def _assemble(provider: _Provider, chunks: list[Any]) -> tuple[Any, bool]:
    """One complete response rebuilt from *chunks*, and whether the provider priced it.

    The provider library's own assembler does the rebuilding, so what comes back carries the
    same fields a non-streamed response does and nothing downstream needs to know which it
    was. Hand-assembling the chunks would be a second copy of a contract this package does
    not own.

    Whether the call can be priced is read from the chunks, not from the assembled response.
    The assembler always attaches a usage block, counting the text with a local tokenizer
    when the stream carried no figures of its own, and a price computed from those counts is
    not what the provider charged.
    """
    usage_reported = any(_carries_usage(chunk) for chunk in chunks)
    try:
        assembled: Any = provider.stream_chunk_builder(chunks)
    except Exception as e:
        raise TransportError(
            "provider stream could not be assembled into a response",
            code="bad_response",
            provider_error=e,
        ) from e
    if assembled is None:
        # What the assembler answers for a stream that carried no chunks at all.
        raise TransportError("provider returned an empty stream", code="bad_response")
    return assembled, usage_reported


_USAGE_COUNTS = ("prompt_tokens", "completion_tokens", "total_tokens")


def _carries_usage(chunk: Any) -> bool:
    """Whether one chunk brought the provider's own accounting with it.

    Presence of a usage container is not accounting. A provider may stream an empty one, or
    one whose counts are all zero, and the assembler then fills the figures in from a local
    tokenizer — so reading presence as "the provider reported this" settles a number the
    provider never sent. It is the same fabrication the raw-chunk check exists to avoid,
    arriving through a container rather than through a missing key.

    Only counts that are actually there count.
    """
    reported = getattr(chunk, "usage", None)
    if reported is None and isinstance(chunk, dict):
        reported = cast("dict[str, Any]", chunk).get("usage")
    if reported is None:
        return False
    carrier: Any = reported
    if isinstance(reported, dict):
        block = cast("dict[str, Any]", reported)
        counts: list[Any] = [block.get(name) for name in _USAGE_COUNTS]
    else:
        counts = [getattr(carrier, name, None) for name in _USAGE_COUNTS]
    return any(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in counts)


def _materialize(response: Any, usage_reported: bool = True) -> tuple[dict[str, Any], float | None]:
    try:
        raw: Any = response.model_dump() if hasattr(response, "model_dump") else response
    except Exception as e:
        raise TransportError(
            "provider response could not be materialized",
            code="bad_response",
            provider_error=e,
        ) from e
    payload = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    choices: Any = payload.get("choices")
    if not isinstance(choices, list):
        raise TransportError(
            "provider returned a response with no choices list", code="bad_response"
        )
    # Checked here, inside the guarded attempt, rather than when the text is pulled out.
    # An empty list or a null content — what a refusal or a non-text completion looks like —
    # passed this check, settled the attempt, and was written to the cache; only then did
    # extraction raise, outside the retry loop. So the documented retries never ran and one
    # transient malformed response became a permanently cached failure.
    if not choices:
        raise TransportError("provider returned an empty choices list", code="bad_response")
    first: Any = cast("list[Any]", choices)[0]
    message: Any = cast("dict[str, Any]", first).get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        raise TransportError("provider response has no message content", code="bad_response")
    # Text, not merely present. Rejecting only `None` let a list, mapping or number settle,
    # cache, and then fail in `str()` outside the retry guard — so a transient anomaly became
    # a permanently cached failure, which is the thing the check exists to prevent.
    if not isinstance(cast("dict[str, Any]", message).get("content"), str):
        raise TransportError("provider response message content is not text", code="bad_response")
    if not usage_reported:
        # A stream that carried no accounting of its own. The assembled response still has a
        # usage block, because the assembler fills one in from a local tokenizer, and neither
        # those counts nor a price derived from them is what the provider billed. Dropping it
        # puts the call on the path every unreadable cost already takes: the reservation
        # stands at its bound and the result reports its accounting as unknown. Pricing the
        # filled-in block instead would settle a figure nobody was charged.
        return {key: value for key, value in payload.items() if key != "usage"}, None
    return payload, _cost_of(response)


def _cost_of(response: Any) -> float | None:
    """What the call cost, or None when the provider's accounting could not be read.

    None is not zero. Converting an accounting failure to a free call is how a ceiling
    comes to admit every remaining attempt while the running total stays where it started.
    """
    try:
        import litellm

        value = float(litellm.completion_cost(completion_response=response))
    except Exception:
        return None
    # NaN arrives as a value rather than an exception, compares false against every
    # ceiling, and passes max(0.0, NaN) as zero. Unreadable is the honest reading.
    if not math.isfinite(value) or value < 0:
        return None
    return value


# How many diskcache backends one client keeps open at once. Small: a caller using more
# than a couple of cache directories at a time is rotating them, not fanning out across
# them, and each open backend costs SQLite descriptors.
_MAX_OPEN_CACHES = 8

# How many event loops the shared semaphore registry tracks at once. A process runs a handful
# of loops at a time; beyond that the oldest entry is dropped, which costs at worst a fresh
# semaphore for a loop that is still live and still bounded by its own permits.
_MAX_TRACKED_LOOPS = 16


def _close_quietly(cache: Any) -> None:
    """Close a cache backend; a store that cannot close is not the caller's problem."""
    with contextlib.suppress(Exception):
        cache.close()


def _cache(cache: Any, key: str, payload: Any) -> None:
    """Write *key* to *cache*; a store that cannot save an answer must not destroy it.

    Only the write is guarded. A failing `get` still propagates, because it happens before
    the provider is contacted — nothing is lost yet, and a caller whose store is broken
    should hear about it rather than silently pay full price for every request. By the time
    this runs the provider has been contacted and billed, so losing that answer to a cache
    error is the one outcome worth ruling out. The shipped `FileCache` already swallows its
    own write failures; a caller-supplied store had no such guarantee, and the documentation
    invites callers to supply one.
    """
    try:
        cache.set(key, payload)
    except Exception:
        pass


def _finish_reason(payload: dict[str, Any]) -> str | None:
    """What the provider said about why it stopped, or None when it said nothing usable.

    Only a string is a reason. A missing field, a null, or a value of some other shape is
    reported as no reason at all rather than coerced, because every consumer above treats an
    absent reason as "nothing was established" and a present one as an attestation.
    """
    choices: list[Any] = payload.get("choices") or []
    if not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    reason: Any = cast("dict[str, Any]", first).get("finish_reason")
    return reason if isinstance(reason, str) and reason else None


def _extract_text(payload: dict[str, Any]) -> str:
    choices: list[Any] = payload.get("choices") or []
    if not choices:
        raise TransportError("provider returned an empty choices list", code="bad_response")
    message: dict[str, Any] = choices[0].get("message") or {}
    content = message.get("content")
    if content is None:
        raise TransportError("provider response has no message content", code="bad_response")
    return str(content)


class FunctionClient:
    """Wraps a plain ``(prompt: str) -> str`` callable as a client.

    This is what makes the platform's ``llm_fn`` parameter useful here. For every other
    source that callable translates natural language into a native dialect. This source
    has no other dialect, so the callable is simply the model, and is used as one.
    """

    def __init__(self, fn: Callable[[str], Any], budget: Budget | None = None) -> None:
        self._fn = fn
        self.budget = budget or Budget()
        self.last_usage: Usage | None = None
        self.usages: list[Usage] = []

    def _record(self, started: float, properties: dict[str, Any] | None = None) -> None:
        record = Usage(model="injected", latency_seconds=time.monotonic() - started)
        self.last_usage = record
        self.usages.append(record)
        sink = (properties or {}).get(C.USAGE_SINK_PROPERTY)
        if isinstance(sink, list):
            cast("list[Usage]", sink).append(record)

    def budget_capabilities(self) -> frozenset[str]:
        """Rows only.

        This wraps a function the caller supplied and can prove exactly one thing about it:
        that it was invoked once. It cannot know whether that function contacts a provider,
        how many times, what it is billed, or how to stop it at a deadline.

        Calls were claimed here at first, on the reasoning that one invocation is one call.
        That is the same guess as reporting its cost at zero: a callable may answer from
        memory, contact one provider, or fan out to several, and none of that is visible
        from here. ``limits.max_calls`` counts provider entries, so it is refused for a
        callable rather than measured against something else and given the same name.
        """
        return frozenset({Capability.ROWS})

    def _budget_for(self, properties: dict[str, Any] | None) -> Budget:
        """The run's budget when one was handed down, this client's otherwise."""
        candidate = (properties or {}).get(C.BUDGET_PROPERTY)
        return candidate if isinstance(candidate, Budget) else self.budget

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        started = time.monotonic()
        # Claimed at no cost, and not because a call ceiling binds here — it does not, and
        # budget_capabilities says so. The claim exists so there is something to resolve on
        # every exit path, including cancellation, which is what keeps a shared budget's
        # headroom from leaking when a caller's function raises.
        # `attempt`, for the reason the default client uses it: its cleanup is installed
        # before the claim is taken, so an interrupt between the two cannot leave a counted
        # claim with no handle to resolve it.
        with self._budget_for(properties).attempt() as reservation:
            try:
                out = self._fn(prompt)
            except BaseException:
                reservation.keep_unpriced()
                raise
            reservation.settle(0.0)
        self._record(started, properties)
        return str(out) if out is not None else ""

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        started = time.monotonic()
        with self._budget_for(properties).attempt() as reservation:
            try:
                result = self._fn(prompt)
                if asyncio.iscoroutine(result):
                    result = await result
            except BaseException:
                reservation.keep_unpriced()
                raise
            reservation.settle(0.0)
        # Recorded here as well as in complete(). The async path is the one afetch uses,
        # so omitting it would report zero calls for a fetch that demonstrably made one.
        self._record(started, properties)
        return str(result) if result is not None else ""
