"""LlmSource: a language model presented as a data source."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Generator
from dataclasses import asdict, fields
from typing import Any, cast

from blue_data.source.source import DataSource  # pyright: ignore[reportMissingTypeStubs]

from blue_data_llm_source import _constants as C
from blue_data_llm_source import checks, prompts
from blue_data_llm_source.llm import pricing
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.llm.client import (
    DEFAULT_MODEL,
    DefaultLlmClient,
    FunctionClient,
    check_adapter_keys,
)
from blue_data_llm_source.reduction import aligner, parsing, verifier
from blue_data_llm_source.types import (
    Attr,
    BudgetAwareClient,
    BudgetExceeded,
    Capability,
    ConfigError,
    Estimate,
    ExecMeta,
    FetchResult,
    LlmSourceError,
    Outcome,
    QueryError,
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


def decide_outcome(
    records: list[dict[str, Any]],
    model_returned: list[dict[str, Any]],
    row_cap_fired: bool,
    stopped_by: str | None,
) -> str:
    """The one place a fetch's outcome is decided.

    Written as a single ordered rule rather than assignments spread through the fetch,
    because that arrangement kept producing combinations nobody chose: a ceiling crossed
    during settlement left an empty result reading ``model_returned_none`` while
    ``stopped_by`` named the limit, and a late rewrite that fired only when the outcome was
    still ``ok`` could not correct it.

    The order is the precedence. A limit that ended the run outranks what the run produced,
    because a caller deciding what to do next needs to know the answer was cut short before
    it can trust anything about its shape.
    """
    if stopped_by is not None:
        # The row cap is the one limit that can leave a usable result behind — but only when
        # it is the limit that actually ended the run. 10-data-model.md states the rule this
        # implements: "any other axis reports stopped_by_limit whether or not records
        # survived, because a caller cannot trust the shape of an answer that was cut
        # short." Testing `row_cap_fired` alone broke it, because a run can trip the row cap
        # *and* blow a deadline, and then the trim was reported as the whole story.
        if row_cap_fired and records and stopped_by == "max_rows":
            return Outcome.TRUNCATED
        return Outcome.STOPPED_BY_LIMIT
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


class LlmSource(DataSource):
    """Fetch structured records from a model's own knowledge.

    Two surfaces. ``fetch`` is the library API, and it returns a :class:`FetchResult`
    carrying metadata the data layer's signatures have nowhere to put. ``execute_query``
    and ``execute_query_nl`` are the data layer's own, inherited from ``DataSource`` and
    overridden here, so this class registers and answers like any other source.

    Base-class order matters and is not cosmetic. ``DataSource`` defines every method this
    class also defines. Most of its implementations return an empty result, so anything left
    unoverridden silently answers nothing rather than failing; the transaction methods raise
    instead, and the orchestrator-tool methods carry behaviour this class overrides with a
    refusal. The contract test asserts that the methods this source must answer resolve here.
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

    def _resolve_client(self, llm_fn: Any = None, budget: Budget | None = None) -> Any:
        if llm_fn is not None:
            return FunctionClient(llm_fn, budget=budget)
        if self._client is not None:
            return self._client
        return DefaultLlmClient(self.properties, budget=budget)

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
        near_misses = sorted(
            key for key in properties if key.startswith("limit.") or key == "limits"
        )
        if not unknown and not near_misses:
            return
        raise ConfigError(
            f"unknown limit {', '.join(repr(k) for k in unknown + near_misses)}. "
            f"Accepted: {sorted('limits.' + name for name in _LIMIT_KEYS)}"
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

        merged = self._resolved_properties(properties)
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
        client = self._resolve_client(llm_fn, run_budget)
        self._require_client_shape(client)
        self._require_bindable(client, run_budget)

        # Handed down per call rather than set on the client: one client can serve several
        # concurrent fetches, and an attribute would let them overwrite each other's ceiling.
        merged[C.BUDGET_PROPERTY] = run_budget

        prompt = prompts.build_fetch_prompt(text, wanted, ctx)
        meta = ExecMeta(requested_attrs=[a.name for a in wanted])

        # Token attribution comes from the entries this call is handed back, not from
        # slicing the client's shared history: two concurrent fetches through one reused
        # client read the same cursor and each take both entries, so each reports the
        # other's tokens. The client writes this call's usage into a list of its own.
        call_usages: list[Usage] = []
        merged[C.USAGE_SINK_PROPERTY] = call_usages
        try:
            raw = await client.acomplete(prompt, merged)
        except BudgetExceeded as e:
            meta.stopped_by = e.limit
            meta.outcome = Outcome.STOPPED_BY_LIMIT
            self._fold_usage(call_usages, meta, run_budget, self._attests(client))
            meta.latency_seconds = time.monotonic() - started
            return FetchResult(records=[], meta=meta)

        records = parsing.parse_records(raw, [a.name for a in wanted])
        alignment = aligner.align(records, wanted)
        checked, mismatches = verifier.verify(alignment.records, wanted)
        answered = verifier.drop_empty(checked, alignment.matched, wanted)
        deduped = verifier.dedupe(answered)

        kept = run_budget.add_rows(len(deduped))
        row_cap_fired = kept < len(deduped)
        if row_cap_fired:
            deduped = deduped[:kept]
            meta.truncated_to = kept
            meta.stopped_by = "max_rows"

        # `record_index` was assigned against the list `verify` saw, and dropping, deduping
        # and capping all renumber that list — so an index published to the caller pointed at
        # a different record than the one it described, or past the end of the result.
        # Renumbered here, against the records the caller actually receives.
        meta.type_mismatches = _renumber_by_record(mismatches, checked, deduped)

        meta.observed_keys = alignment.observed_keys
        meta.remapped_keys = alignment.remapped
        meta.unmapped_keys = alignment.unmapped
        meta.key_collisions = _renumber_by_record(alignment.collisions, checked, deduped)
        meta.unchecked_types = verifier.unchecked_types(wanted)
        self._fold_usage(call_usages, meta, run_budget, self._attests(client))
        meta.outcome = decide_outcome(
            records=deduped,
            model_returned=records,
            row_cap_fired=row_cap_fired,
            stopped_by=meta.stopped_by,
        )
        meta.latency_seconds = time.monotonic() - started
        return FetchResult(records=deduped, meta=meta)

    def estimate(
        self,
        query: Any,
        attrs: Any = None,
        context: Any = None,
        properties: dict[str, Any] | None = None,
        llm_fn: Any = None,
    ) -> Estimate:
        """The most one fetch of *query* could cost. See :meth:`_estimate_inner`."""
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
        """The most one fetch of *query* could cost, without contacting the provider.

        Asked of the client that would actually answer, using the same call arguments the
        attempt itself would build, so the figure cannot disagree with what the run reserves.
        Computing it separately is how an estimate came to differ from the reservation by
        three orders of magnitude for an injected client.

        A *fetch* is bounded, not an attempt. A fetch may retry, so the figure is the
        per-attempt bound times the attempts a fetch is permitted — with the shipped retry
        default that is three times what one attempt costs, and reporting the per-attempt
        figure as the fetch's meant a request sized against it was refused when it ran.

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
        attempts = self._max_attempts(limits)

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
        prompt = prompts.build_fetch_prompt(text, wanted, ctx)
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
    def _max_attempts(limits: Limits) -> int:
        """How many provider attempts one fetch is permitted.

        The retry allowance, narrowed by a call ceiling when one is configured: a run
        allowed one call cannot make three attempts however many retries it is given.
        """
        attempts = int(limits.max_retries) + 1
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
        if not meta.stopped_by:
            # What this call crossed, which may have been crossed on a parent the caller
            # owns. Reading only the child's own field lost a deadline the run went past
            # and returned a complete-looking answer.
            meta.stopped_by = budget.stopped_by or crossed
        elif meta.stopped_by == "max_rows" and crossed is not None and crossed != "max_rows":
            # A harder ceiling outranks the row cap. The row cap is the only axis that
            # leaves a usable result, and `truncated` is the only outcome the data model
            # defines that way — so reporting it while a deadline or a cost ceiling had
            # actually been crossed told a caller the answer was merely short when it was
            # cut off. The same blown deadline reported `stopped_by_limit`/`max_seconds`
            # without a row cap and `truncated`/`max_rows` with one, and a planner branching
            # on `outcome`, which the documentation instructs it to do, reused an answer
            # whose ceiling had been passed many times over.
            meta.stopped_by = crossed
        # Decided last. A ceiling crossed during settlement or by the deadline check is
        # still a ceiling, and leaving the default would return records as ok while
        # stopped_by names the limit that ended the run.
        if meta.stopped_by and meta.outcome == Outcome.OK:
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
    # samples, foreign keys — and a source answering from a model's own knowledge has none
    # of those. Inherited unchanged, this source advertised eight tools of which three
    # answered with silence and one raised a bare TypeError from a defect in the base
    # class's own dispatcher.
    #
    # v1.0 answers honestly instead: no tools, and a clear refusal if one is called. The
    # refusal matters more than the empty list. `execute_query_nl_tools` drives a loop of up
    # to ten provider calls that this source's ceilings never see, so inheriting it would
    # have put an unbounded spend path behind a method nobody here chose to offer.

    def get_nl_tools(
        self, database: str | None = None, collection: str | None = None
    ) -> list[dict[str, Any]]:
        """No tools. This source has no schema to inspect and no rows to sample."""
        return []

    def execute_nl_tool(
        self,
        name: str,
        tool_input: dict[str, Any],
        database: str | None = None,
        collection: str | None = None,
        optional_properties: dict[str, Any] | None = None,
    ) -> Any:
        """Refuse by name, since :meth:`get_nl_tools` advertises none."""
        raise ConfigError(
            f"this source serves no orchestrator tools, so {name!r} cannot be called. "
            f"Use fetch, or execute_query_nl for the natural-language surface"
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
            "this source does not serve the tool-driven query path in v1.0. Use "
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
    raise RuntimeError("fetch() was called from inside a running event loop. Use afetch() instead.")
