"""The default model client.

It validates override keys and response shapes, strips reserved fields after
each merge and control fields before provider calls, and reserves spend before
contact. Accounting cannot raise; cache hits cost nothing but record latency.
Each event loop has its own concurrency bound. Clients implementing
``complete`` and ``acomplete`` can be injected without subclassing this one.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import math
import os
import stat
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, ClassVar, Protocol, cast

from tenacity import (
    AsyncRetrying,
    AttemptManager,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.stop import stop_base
from tenacity.wait import wait_base

from blue_data_llm_source import _constants as C
from blue_data_llm_source.llm import models, pricing
from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.types import (
    BudgetExceeded,
    Capability,
    ConfigError,
    TransportError,
    Usage,
)

# Reject unknown llm.* overrides so the recorded configuration describes the run.
_ACCEPTED = {
    "model",
    "reasoning_effort",
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
        "client-side call spacing has to reconcile with per-attempt budget admission, and "
        "this library does not do it. Use 'llm.max_parallel' to cap concurrency, or "
        "throttle above this library where the whole request sequence is visible"
    ),
}
# Stripped after every merge so an override cannot smuggle transport fields through.
_RESERVED = {"api", "service_url", "variant", "protocol"}


DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_REASONING_EFFORT = "medium"
REASONING_EFFORTS = frozenset(models.EFFORT_ORDER)


def default_max_tokens_for(model: object) -> int:
    """Return the model's default output allowance, or the legacy allowance."""
    contract = models.contract_for(model)
    return contract.default_max_tokens if contract is not None else models.LEGACY_OUTPUT_ALLOWANCE


def default_reasoning_effort_for(model: object) -> str | None:
    """Return the model's default reasoning effort, when it has one."""
    contract = models.contract_for(model)
    return contract.default_reasoning_effort if contract is not None else None


def _calculator_accounting_incomplete(
    model: object, bound: float, properties: dict[str, Any]
) -> bool:
    """Whether response categories prevent treating the calculator cost as measured.

    A ceiling retains its reservation in either case. Extra billing categories and
    caller rates can make the calculator figure incomplete, so only a higher figure
    can affect their ledger charge.
    """
    if bound <= 0:
        return False
    if (
        properties.get("llm.price_per_input_token") is not None
        or properties.get("llm.price_per_output_token") is not None
    ):
        return True
    entry = models.entry_for(model)
    return (
        entry is None
        or entry.cache_write is not None
        or entry.long_context is not None
        or entry.regional_uplift != 1.0
    )


# Served tiers billed at no more than the standard rates in the table. Fast mode reports
# `priority`, and `auto` or a missing tier does not say which rates applied.
_STANDARD_OR_CHEAPER_TIERS = frozenset({"default", "flex"})


def _count(value: Any) -> int | None:
    """A non-negative integer token count, or None."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _usage_bound(
    model: object, payload: dict[str, Any], properties: dict[str, Any]
) -> float | None:
    """A conservative charge from reported usage, or None.

    The figure uses this package's rates, a matching answered model, a standard or
    cheaper service tier, and valid token counts. It can raise a contacted attempt's
    charge above its reservation. Without a ceiling it supplies an unmeasured figure
    when the calculator cannot price the response.
    """
    if (
        properties.get("llm.price_per_input_token") is not None
        or properties.get("llm.price_per_output_token") is not None
    ):
        return None
    entry = models.entry_for(model)
    answered = payload.get("model")
    # Compared by value, so an alias answered by the model it routes to still qualifies:
    # the two carry the same rates.
    if entry is None or not isinstance(answered, str) or models.entry_for(answered) != entry:
        return None
    if payload.get("service_tier") not in _STANDARD_OR_CHEAPER_TIERS:
        return None
    raw: Any = payload.get("usage")
    usage = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    details_raw: Any = usage.get("prompt_tokens_details")
    details = cast("dict[str, Any]", details_raw) if isinstance(details_raw, dict) else {}
    prompt = _count(usage.get("prompt_tokens"))
    completion = _count(usage.get("completion_tokens"))
    cached = _count(details.get("cached_tokens"))
    if prompt is None or completion is None or cached is None or cached > prompt:
        return None
    # Counts that cannot describe a contacted call are not an account of one. Every request
    # sends a prompt, and a text answer cannot cost zero output tokens; taken at face value,
    # all-zero counts price a billed call at nothing and release its reservation.
    if prompt == 0 or (completion == 0 and _answered_text(payload)):
        return None
    tier = entry.long_context
    input_factor = tier.input_multiplier if tier is not None else 1.0
    output_factor = tier.output_multiplier if tier is not None else 1.0
    uncached_rate = max(entry.input, entry.cache_write or 0.0)
    cached_rate = entry.cached_input if entry.cached_input is not None else entry.input
    figure = entry.regional_uplift * (
        (prompt - cached) * uncached_rate * input_factor
        + cached * cached_rate * input_factor
        + completion * entry.output * output_factor
    )
    return figure if math.isfinite(figure) else None


def _retained_cost(
    model: object,
    payload: dict[str, Any],
    properties: dict[str, Any],
    reservation: float,
    reported: float,
) -> float:
    """Keep the reservation and any higher readable accounting figure."""
    usage_bound = _usage_bound(model, payload, properties)
    return max(reservation, reported, usage_bound or 0.0)


def accepted_adapter_keys() -> frozenset[str]:
    """The full ``llm.*`` names this client accepts.

    Read by the source's own property check, so the two refusals cannot disagree about
    which overrides exist.
    """
    return frozenset(f"llm.{name}" for name in _ACCEPTED)


def _check_adapter_flag_types(properties: dict[str, Any]) -> None:
    for name in ("llm.stream", "llm.cache"):
        if name in properties and not isinstance(properties[name], bool):
            raise ConfigError(f"{name!r} must be a bool")
    if "llm.cache_dir" in properties and not isinstance(properties["llm.cache_dir"], str):
        raise ConfigError("'llm.cache_dir' must be a string")


def check_adapter_keys(properties: dict[str, Any]) -> None:
    """Refuse an unknown or out-of-range ``llm.*`` override, whichever client will answer.

    Checked at the source boundary rather than inside the call-argument builder, because
    only the built-in client reaches that builder: a typo alongside an injected client or an
    ``llm_fn`` was accepted and ignored, which is exactly what a closed key set is for.

    A key inside the set but holding a value its consumer cannot use is the same fault with
    a better disguise, so the ranges are checked here too. Each range is the one its reader
    already has: the wire transport refuses a ``model`` that is not a non-empty string, a
    ``max_output_tokens`` that is not a positive integer and a ``temperature`` that is not
    finite, and :meth:`_with_deadline` refuses a timeout that is not a finite positive
    number. None of those run before an unbounded request reaches a billed provider call.
    """
    model = properties.get("llm.model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        # The bound the wire transport already keeps for a task's own `model`, lifted to
        # where every client passes. `_call_kwargs` tests the name for truth, so anything
        # truthy — an integer, a list — was forwarded to the provider as a model to call.
        raise ConfigError(
            f"'llm.model' must be a non-empty string, or None for the default, got {model!r}"
        )
    parallel = properties.get("llm.max_parallel")
    if parallel is not None and (
        isinstance(parallel, bool) or not isinstance(parallel, int) or parallel < 1
    ):
        # Reject zero before creating a semaphore with no permits.
        raise ConfigError(
            f"'llm.max_parallel' must be a whole number of at least 1, got {parallel!r}"
        )
    allowance = properties.get("llm.max_tokens")
    if allowance is not None and (
        isinstance(allowance, bool) or not isinstance(allowance, int) or allowance < 1
    ):
        # `None` stays legal and means no cap, which is the state `bound_cost` refuses to
        # price and `llmdb_inspect_model` reports as an unsupported cost ceiling.
        raise ConfigError(
            f"'llm.max_tokens' must be a whole number of at least 1, or None for no cap, "
            f"got {allowance!r}"
        )
    temperature = properties.get("llm.temperature")
    if temperature is not None and (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(temperature)
    ):
        # Finite is the whole claim. The usable range differs by provider — two for one
        # family, one for another — so a numeric bound here would refuse a value the
        # provider accepts, which is worse than the silence it replaces.
        raise ConfigError(f"'llm.temperature' must be a finite number, got {temperature!r}")
    effort = properties.get("llm.reasoning_effort")
    if effort is not None and (not isinstance(effort, str) or effort not in REASONING_EFFORTS):
        raise ConfigError(
            f"'llm.reasoning_effort' must be one of {sorted(REASONING_EFFORTS)}, got {effort!r}"
        )
    _check_contract(model or DEFAULT_MODEL, effort, temperature, quoted=True)
    timeout = properties.get("llm.timeout")
    if timeout is not None and _positive_finite_number(timeout) is None:
        raise ConfigError(
            f"'llm.timeout' must be a finite positive number of seconds, got {timeout!r}"
        )
    _check_adapter_flag_types(properties)
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
            f"Accepted: {sorted(accepted_adapter_keys())}"
        )


def _check_contract(model: str, effort: Any, temperature: Any, *, quoted: bool = False) -> None:
    """Refuse an effort or a temperature the model's published contract rules out.

    Run before anything is reserved, so a request the provider would reject costs nothing.
    A model the table does not carry is checked only for the one rule every reasoning model
    shares, and otherwise left to the provider.
    """
    effort_key, temperature_key = (
        ("'llm.reasoning_effort'", "'llm.temperature'")
        if quoted
        else ("llm.reasoning_effort", "llm.temperature")
    )
    contract = models.contract_for(model)
    if contract is not None:
        if effort is not None and effort not in contract.reasoning_efforts:
            if not contract.reasoning_efforts:
                raise ConfigError(f"{model} does not support {effort_key}")
            raise ConfigError(f"{model} does not support reasoning effort {effort!r}")
        if temperature is not None and contract.temperature == models.TEMPERATURE_NEVER:
            raise ConfigError(f"{model} does not accept {temperature_key} at any reasoning effort")
        effort = effort or contract.default_reasoning_effort
    if effort not in {None, "none"} and temperature is not None:
        raise ConfigError(f"{temperature_key} is not supported when {effort_key} is not 'none'")


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

    def get_supported_openai_params(
        self, *, model: str, custom_llm_provider: str | None = None
    ) -> list[str] | None: ...

    def get_llm_provider(
        self, model: str, custom_llm_provider: str | None = None
    ) -> tuple[str, str, str | None, str | None]: ...


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
        # Bound open diskcache backends, each of which holds SQLite descriptors. Eviction
        # closes the least recently opened backend; close() releases the rest.
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

        # Check the deadline before serving a cache hit. A hit spends no call, tokens,
        # or cost, but it cannot bypass the time ceiling.
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
        """Answer *prompt* within the available asynchronous wait time.

        The deadline covers the semaphore queue, provider wait, and retries. It
        cannot interrupt synchronous work that never yields or work that ignores
        cancellation; the latter holds the call open until it acknowledges the
        request. A caller requiring a hard stop must enforce it outside this library.

        A late completion or transport failure raises ``BudgetExceeded`` for
        ``max_seconds``. An inner ``TimeoutError`` propagates if budget remains.
        """
        merged = {**self.properties, **(properties or {})}
        budget = self._budget_for(merged) or self.budget
        remaining = budget.remaining_seconds()
        if remaining is None:
            return await self._answer(prompt, merged)
        if remaining <= 0:
            raise BudgetExceeded(_deadline_message(budget), limit="max_seconds")
        # Keep cancellation and cost accounting attached to the caller's task.
        # A detached task could continue spending after the caller returned.
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
        # Work may suppress cancellation and return late. Report the expired
        # ceiling even when wait_for hands back a normal answer.
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
            # implementation cannot be bounded by the deadline from here: canceling the
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
        _check_adapter_flag_types(properties)
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
        for optional in ("reasoning_effort", "temperature"):
            if kwargs.get(optional) is None:
                kwargs.pop(optional, None)

        kwargs.setdefault("model", properties.get("llm.model") or DEFAULT_MODEL)
        if not kwargs.get("model"):
            raise ConfigError("no model configured (set llm.model)")
        model = str(kwargs["model"])
        contract = models.contract_for(model)
        # Checked against the caller's own settings, before any default is filled in, so a
        # temperature the caller set is refused by name rather than by the provider.
        _check_contract(model, kwargs.get("reasoning_effort"), kwargs.get("temperature"))
        if contract is not None:
            if contract.reasoning_efforts:
                kwargs["reasoning_effort"] = (
                    kwargs.get("reasoning_effort") or contract.default_reasoning_effort
                )
            if contract.default_temperature is not None and "temperature" not in kwargs:
                kwargs["temperature"] = contract.default_temperature
            if (
                contract.output_token_parameter == "max_completion_tokens"
                and "max_tokens" in kwargs
            ):
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
        # A name without a contract keeps `max_tokens`, which the provider library translates
        # for a model it knows to need `max_completion_tokens`. Asserting the newer parameter
        # for a model nobody has described here would be a guess about its contract.
        if models.pins_openai(model):
            kwargs["custom_llm_provider"] = "openai"
        return kwargs

    @staticmethod
    def _check_provider_compatibility(provider: _Provider, kwargs: dict[str, Any]) -> None:
        """Fail locally when LiteLLM cannot route or shape the request for this model.

        Runs after the import and before the attempt is claimed, so either refusal is
        uncharged. Only the two failures named here are converted. Anything the provider
        library raises inside the attempt is charged as it always was, because nothing
        there proves the request never left the process.
        """
        model = str(kwargs.get("model", ""))
        provider_name = cast("str | None", kwargs.get("custom_llm_provider"))
        try:
            provider.get_llm_provider(model=model, custom_llm_provider=provider_name)
        except Exception as error:
            # Only the missing-provider failure is converted. Anything else is met again
            # inside the attempt and reported the way every other adapter error is.
            if "LLM Provider NOT provided" in str(error):
                # An OpenAI name is the one case where the provider is known; for any other
                # family, suggesting `openai/` would send the call to the wrong provider.
                remedy = (
                    f"Name the provider in the model, as in 'openai/{model}'"
                    if models.openai_family(model)
                    else "Name the provider LiteLLM should route it to, as in 'provider/model'"
                )
                raise ConfigError(
                    f"the installed LiteLLM cannot tell which provider serves {model!r}. {remedy}"
                ) from error
        contract = models.contract_for(model)
        if contract is None or not contract.reasoning_efforts:
            return
        lookup = models.lookup_name(model)
        try:
            supported = provider.get_supported_openai_params(
                model=lookup, custom_llm_provider="openai"
            )
        except Exception as error:
            raise ConfigError(
                f"the installed LiteLLM does not recognize {lookup}. Install a "
                "LiteLLM release whose OpenAI adapter supports reasoning_effort and "
                "max_completion_tokens for this model"
            ) from error
        missing = {"reasoning_effort", "max_completion_tokens"} - set(supported or ())
        if missing:
            raise ConfigError(
                f"the installed LiteLLM cannot call {lookup}: its OpenAI adapter "
                f"does not support {', '.join(sorted(missing))} for this model"
            )

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
            for name in ("get", "set"):
                method = getattr(store, name, None)
                if (
                    not callable(method)
                    or inspect.iscoroutinefunction(method)
                    or inspect.isasyncgenfunction(method)
                    or inspect.iscoroutinefunction(getattr(method, "__call__", None))
                    or inspect.isasyncgenfunction(getattr(method, "__call__", None))
                ):
                    raise ConfigError(
                        "cache_store must provide synchronous get(key) and "
                        "set(key, value) callables"
                    )
            return store, json.dumps(kwargs, sort_keys=True, default=str)
        if not properties.get("llm.cache"):
            return None, None
        # Bound per directory so a per-call cache_dir override gets its own cache
        # instead of silently reusing the first one opened.
        cache_dir = str(properties.get("llm.cache_dir", ".llm_cache"))
        expanded = os.path.expandvars(os.path.expanduser(cache_dir))
        if "$" in expanded or (os.name == "nt" and "%" in expanded):
            raise ConfigError("llm.cache_dir has an unresolved environment variable")
        path = Path(expanded)
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = path.resolve(strict=True)
            mode = path.lstat()
        except (OSError, RuntimeError):
            raise ConfigError("llm.cache_dir cannot be opened") from None
        if not stat.S_ISDIR(mode.st_mode) or (
            os.name == "posix" and (mode.st_uid != os.getuid() or mode.st_mode & 0o077)
        ):
            raise ConfigError(
                "llm.cache_dir must be an owned, private directory (mode 0700); "
                "choose a private directory or change its permissions"
            )
        checked_dir = str(path)
        if "$" in checked_dir or (os.name == "nt" and "%" in checked_dir):
            raise ConfigError("llm.cache_dir has an unresolved environment variable")
        cache = self._caches.get(checked_dir)
        if cache is None:
            try:
                import diskcache  # pyright: ignore[reportMissingTypeStubs]
            except ImportError as e:
                raise ConfigError(
                    "llm.cache requested but diskcache is not installed "
                    "(pip install 'blue-data-llm-source[llm]')"
                ) from e
            cache = diskcache.Cache(checked_dir)
            while len(self._caches) >= _MAX_OPEN_CACHES:
                _, evicted = self._caches.popitem(last=False)
                _close_quietly(evicted)
            self._caches[checked_dir] = cache
        else:
            # Most recently used, so the bound evicts the coldest backend rather than
            # whichever happened to be opened first.
            self._caches.move_to_end(checked_dir)
        return cache, json.dumps(kwargs, sort_keys=True, default=str)

    def close(self) -> None:
        """Close cache backends; later calls reopen them as needed."""
        while self._caches:
            _, cache = self._caches.popitem()
            _close_quietly(cache)

    def __enter__(self) -> DefaultLlmClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # Share semaphores across client instances within an event loop so max_parallel bounds
    # concurrent fetches. Closed loops are removed below.
    _semaphores: ClassVar[OrderedDict[asyncio.AbstractEventLoop, dict[int, asyncio.Semaphore]]] = (
        OrderedDict()
    )

    _semaphore_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def _release_loop(cls, loop: asyncio.AbstractEventLoop) -> None:
        with cls._semaphore_lock:
            cls._semaphores.pop(loop, None)

    def _semaphore(self, properties: dict[str, Any]) -> asyncio.Semaphore:
        max_parallel = int(properties.get("llm.max_parallel", 8))
        loop = asyncio.get_running_loop()
        with self._semaphore_lock:
            # A closed loop can have no live waiters, so its semaphores are dead weight and the
            # only thing keeping it reachable. Dropped here rather than left for a weak
            # reference that the semaphore itself prevents from ever firing.
            for stale in [known for known in self._semaphores if known.is_closed()]:
                del self._semaphores[stale]
            per_loop = self._semaphores.get(loop)
            if per_loop is None:
                per_loop = {}
                # The class-level map pins loop resources, including abandoned loops.
                # Evict the least recently used entry when it reaches the limit.
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

        A failed provider attempt may have been billed. Record unknown tokens and cost so
        a later successful retry does not make the fetch appear fully measured.
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

        Cost is included because :func:`pricing.bound_cost` bounds an attempt at the
        package or configured rates, or refuses with a ConfigError before contact.
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
        allowance = kwargs.get("max_completion_tokens", kwargs.get("max_tokens"))
        return pricing.bound_cost(text, str(kwargs.get("model", "")), allowance, properties)

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
        # Reserve each attempt so retries consume the call limit.
        effective = budget or self.budget
        merged = merged or {}
        last_provider_error: TransportError | None = None

        def attempts() -> Iterator[AttemptManager]:
            try:
                yield from self._retryer(effective)
            except BudgetExceeded as limit:
                if limit.limit != "max_calls" or last_provider_error is None:
                    raise
                # No answer exists, and the refusal only prevented another retry. Keep
                # the provider failure as the visible cause without exposing its text.
                raise TransportError(
                    "Provider request failed; max_calls stopped retries.",
                    code=last_provider_error.code,
                    provider_error=last_provider_error.provider_error,
                ) from limit

        for attempt in attempts():
            with attempt:
                # Check the bound before importing the provider, whose first import may
                # contact a price service. Claim the budget after import so another call
                # does not see a reservation for an uncharged import failure. A concurrent
                # claim can still exhaust the budget between the check and attempt().
                bound = self._estimate(self._with_deadline(kwargs, effective), effective, merged)
                effective.check_admissible(bound)
                provider = self._import_provider()
                self._check_provider_compatibility(provider, kwargs)
                # Provider import consumes time; pass the remaining deadline to the call.
                # The cost bound depends on model, prompt, and allowance, so it is unchanged.
                call_kwargs = self._with_deadline(kwargs, effective)
                # attempt() installs cleanup before claiming the budget.
                with effective.attempt(bound) as reservation:
                    try:
                        payload, cost = _call_provider(provider, call_kwargs)
                    except TransportError as error:
                        # Contacted and failed. The estimate stands as the charge rather than
                        # being released, or every failed retry would be free.
                        reservation.keep_unpriced()
                        self._record_unpriced(kwargs, merged)
                        last_provider_error = error
                        raise
                    except Exception as e:
                        reservation.keep_unpriced()
                        self._record_unpriced(kwargs, merged)
                        last_provider_error = TransportError(
                            str(e), code="provider_error", provider_error=e
                        )
                        raise last_provider_error from e
                    except BaseException:
                        # A canceled task or an interrupt is still an attempt that reached the
                        # provider. Leaving its claim outstanding poisons a caller-owned budget
                        # for everything after it, so the estimate stands and the exit is
                        # re-raised unchanged.
                        reservation.keep_unpriced()
                        raise
                    if cost is None:
                        usage_bound = _usage_bound(kwargs.get("model"), payload, merged)
                        if usage_bound is not None:
                            charged = max(reservation.estimate, usage_bound)
                            reservation.settle(charged)
                            return payload, charged, False
                        reservation.keep_unpriced()
                        return payload, reservation.estimate, False
                    if _calculator_accounting_incomplete(kwargs.get("model"), bound, merged):
                        conservative_cost = _retained_cost(
                            kwargs.get("model"), payload, merged, reservation.estimate, cost
                        )
                        reservation.settle(conservative_cost)
                        return payload, conservative_cost, False
                    charged = max(reservation.estimate, cost)
                    reservation.settle(charged)
                    return payload, charged, charged == cost
        raise TransportError("retry policy exhausted without a result", code="no_result")

    async def _acall(
        self,
        kwargs: dict[str, Any],
        budget: Budget | None = None,
        merged: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], float, bool]:
        effective = budget or self.budget
        merged = merged or {}
        last_provider_error: TransportError | None = None

        async def attempts() -> AsyncIterator[AttemptManager]:
            try:
                async for attempt in self._aretryer(effective):
                    yield attempt
            except BudgetExceeded as limit:
                if limit.limit != "max_calls" or last_provider_error is None:
                    raise
                raise TransportError(
                    "Provider request failed; max_calls stopped retries.",
                    code=last_provider_error.code,
                    provider_error=last_provider_error.provider_error,
                ) from limit

        async for attempt in attempts():
            with attempt:
                # Use the same check, import, and claim order as the sync path.
                bound = self._estimate(self._with_deadline(kwargs, effective), effective, merged)
                effective.check_admissible(bound)
                provider = self._import_provider()
                self._check_provider_compatibility(provider, kwargs)
                # Provider import consumes time; pass the remaining deadline to the call.
                # The cost bound depends on model, prompt, and allowance, so it is unchanged.
                call_kwargs = self._with_deadline(kwargs, effective)
                # attempt() installs cleanup before claiming the budget.
                with effective.attempt(bound) as reservation:
                    try:
                        payload, cost = await _acall_provider(provider, call_kwargs)
                    except TransportError as error:
                        reservation.keep_unpriced()
                        self._record_unpriced(kwargs, merged)
                        last_provider_error = error
                        raise
                    except Exception as e:
                        reservation.keep_unpriced()
                        self._record_unpriced(kwargs, merged)
                        last_provider_error = TransportError(
                            str(e), code="provider_error", provider_error=e
                        )
                        raise last_provider_error from e
                    except BaseException:
                        # A canceled task or an interrupt is still an attempt that reached the
                        # provider, so the estimate stands and the exit is re-raised unchanged.
                        reservation.keep_unpriced()
                        raise
                    if cost is None:
                        usage_bound = _usage_bound(kwargs.get("model"), payload, merged)
                        if usage_bound is not None:
                            charged = max(reservation.estimate, usage_bound)
                            reservation.settle(charged)
                            return payload, charged, False
                        reservation.keep_unpriced()
                        return payload, reservation.estimate, False
                    if _calculator_accounting_incomplete(kwargs.get("model"), bound, merged):
                        conservative_cost = _retained_cost(
                            kwargs.get("model"), payload, merged, reservation.estimate, cost
                        )
                        reservation.settle(conservative_cost)
                        return payload, conservative_cost, False
                    charged = max(reservation.estimate, cost)
                    reservation.settle(charged)
                    return payload, charged, charged == cost
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

    A provider may stream an empty usage container, or one with zero counts. The assembler
    can fill those values from a local tokenizer. Only counts in the raw chunks attest
    provider accounting.
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
    # Validate content inside the guarded attempt, before settlement and caching, so a
    # malformed response can be retried.
    if not choices:
        raise TransportError("provider returned an empty choices list", code="bad_response")
    first: Any = cast("list[Any]", choices)[0]
    message: Any = cast("dict[str, Any]", first).get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        raise TransportError("provider response has no message content", code="bad_response")
    # Require text before settling or caching the provider response.
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
    cost = _cost_of(response)
    if cost == 0.0:
        # Zero may mean missing pricing or usage. Keep the cost unknown for a contacted call
        # rather than releasing its reservation as a free call.
        return payload, None
    return payload, cost


def _answered_text(payload: dict[str, Any]) -> bool:
    """Whether the first choice carries non-empty text, which no provider bills at zero."""
    choices: Any = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    first: Any = cast("list[Any]", choices)[0]
    message: Any = cast("dict[str, Any]", first).get("message") if isinstance(first, dict) else None
    content: Any = (
        cast("dict[str, Any]", message).get("content") if isinstance(message, dict) else None
    )
    return isinstance(content, str) and content != ""


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
    # NaN can arrive as a value and must not become a zero-cost measurement.
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

        A callable may answer from memory, contact one provider, or fan out to several.
        Provider calls and cost are not observable here, so ``limits.max_calls`` cannot be
        enforced for a callable.
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
