"""Budget limits configured on the source, seen from the fetch surface.

A budget is created per fetch and handed to the default client, so a limit reached
before the call stops the run and is reported in ExecMeta rather than raised.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, cast

import pytest

from blue_data_llm_source import Capability, LlmSource, Usage
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.llm.client import (
    DefaultLlmClient,
    FunctionClient,
    _WaitWithin,  # pyright: ignore[reportPrivateUsage]  # the cap under test
)
from blue_data_llm_source.types import (
    BudgetExceeded,
    ConfigError,
    TransportError,
)
from tests.doubles import LitellmStub, ScriptedClient, chat_payload

InstallStub = Callable[[LitellmStub], LitellmStub]

ONE_RECORD = '[{"name": "Ada", "age": 36}]'
THREE_RECORDS = '[{"name": "Ada"}, {"name": "Grace"}, {"name": "Katherine"}]'


def test_max_calls_stops_the_run_and_is_named_in_the_metadata(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload('[{"name": "Ada"}]')))
    source = LlmSource(properties={"limits.max_calls": 0})

    result = source.fetch("who?")

    assert result.records == []
    assert result.meta.stopped_by == "max_calls"
    assert stub.calls == []


def test_max_cost_stops_the_run_and_is_named_in_the_metadata(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload('[{"name": "Ada"}]'), cost=1.0))
    source = LlmSource(properties={"limits.max_cost": 0.0})

    result = source.fetch("who?")

    assert result.records == []
    assert result.meta.stopped_by == "max_cost"
    assert stub.calls == []


def test_max_seconds_stops_the_run_and_is_named_in_the_metadata(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload('[{"name": "Ada"}]')))
    source = LlmSource(properties={"limits.max_seconds": 0.0})

    result = source.fetch("who?")

    assert result.records == []
    assert result.meta.stopped_by == "max_seconds"
    assert stub.calls == []


def test_a_run_within_its_limits_returns_records(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload('[{"name": "Ada"}]'), cost=0.01))
    source = LlmSource(
        properties={
            "limits.max_calls": 4,
            "limits.max_cost": 1.0,
            "limits.max_seconds": 60.0,
        }
    )

    result = source.fetch("who?")

    assert result.records == [{"name": "Ada"}]
    assert result.meta.stopped_by is None
    assert result.meta.calls == 1


def test_per_call_properties_override_a_configured_limit(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload('[{"name": "Ada"}]')))
    source = LlmSource(properties={"limits.max_calls": 4})

    result = source.fetch("who?", properties={"limits.max_calls": 0})

    assert result.meta.stopped_by == "max_calls"
    assert stub.calls == []


# ----------------------------------------------- one limit contract, three paths --


class FakeClock:
    """A clock the test moves, so a deadline is crossed without waiting for one."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@contextmanager
def _installed(stub: LitellmStub):
    """Install a provider stub for a block that runs its own event loops."""
    import sys

    previous = sys.modules.get("litellm")
    sys.modules["litellm"] = stub.as_module()
    try:
        yield stub
    finally:
        if previous is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = previous


def _stub_fn(prompt: str) -> str:
    return ONE_RECORD


def _three_records_fn(prompt: str) -> str:
    return THREE_RECORDS


class _OpaqueClient:
    """A third-party client whose provider calls this source cannot see.

    No ``budget`` attribute, which is the point: it may answer from its own cache, make
    five calls, or make none, so one invocation is not evidence of one provider call.
    """

    def __init__(self, response: str = ONE_RECORD) -> None:
        self.response = response
        self.usages: list[Usage] = []

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        self.usages.append(Usage(cost=9.0, model="opaque"))
        return self.response

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self.complete(prompt, properties)


@pytest.mark.parametrize("limit", ["limits.max_calls", "limits.max_cost", "limits.max_seconds"])
def test_a_provider_limit_is_refused_for_a_client_that_cannot_hold_it(limit: str) -> None:
    """Better to refuse a ceiling than to report one that is not holding anything.

    An opaque client's usage is whatever it chooses to report. Bounding it on that gives
    the caller a number that looks like a limit and is not one.
    """
    source = LlmSource(properties={limit: 1}, client=_OpaqueClient())

    with pytest.raises(ConfigError, match=limit.replace(".", r"\.")):
        source.fetch("who?")


def test_rows_still_bind_for_a_client_that_cannot_hold_the_others() -> None:
    """This source counts the records itself, so the row cap needs nothing from the client."""
    source = LlmSource(properties={"limits.max_rows": 1}, client=_OpaqueClient(THREE_RECORDS))
    result = source.fetch("who?")

    assert len(result) == 1
    assert result.meta.stopped_by == "max_rows"


def test_an_opaque_client_is_fine_when_no_provider_limit_is_configured() -> None:
    source = LlmSource(client=_OpaqueClient())
    assert source.fetch("who?").records == [{"name": "Ada", "age": 36}]


def test_spending_the_last_allowed_call_is_not_a_stop_on_a_bindable_client() -> None:
    source = LlmSource(properties={"limits.max_calls": 1}, client=ScriptedClient([ONE_RECORD]))
    assert source.fetch("who?").meta.stopped_by is None


def test_a_call_ceiling_is_refused_for_a_callable() -> None:
    """max_calls counts provider entries, and a callable cannot prove how many it made.

    One invocation is not one provider call: the function may answer from memory, contact
    one provider, or fan out to several. Measuring something else and giving it the same
    name is the guess this refusal replaces.
    """
    source = LlmSource(properties={"limits.max_calls": 1})

    with pytest.raises(ConfigError, match=r"limits\.max_calls"):
        source.fetch("who?", llm_fn=_stub_fn)


def test_rows_still_bind_a_callable() -> None:
    source = LlmSource(properties={"limits.max_rows": 1})
    result = source.fetch("who?", llm_fn=_three_records_fn)

    assert len(result) == 1
    assert result.meta.stopped_by == "max_rows"


def test_a_cost_ceiling_refuses_a_bindable_client_before_it_spends() -> None:
    """Reserved, so the attempt never runs rather than being reported after it did.

    The client claims its estimate against the run budget before calling. Nine dollars
    against a fifty-cent ceiling is refused up front; under the old settle-afterwards
    accounting the money was already gone by the time anything noticed.
    """
    client = ScriptedClient([ONE_RECORD], cost=9.0)
    source = LlmSource(properties={"limits.max_cost": 0.5}, client=client)

    result = source.fetch("who?")

    assert result.records == []
    assert result.meta.stopped_by == "max_cost"
    assert result.meta.outcome == "stopped_by_limit"
    assert client.prompts == [], "the ceiling must refuse before the client is asked"
    assert result.meta.cost == 0.0


def test_spend_within_the_ceiling_is_charged_and_reported() -> None:
    """Accumulation needs a caller's budget; without one each fetch is its own run."""
    client = ScriptedClient([ONE_RECORD, ONE_RECORD], cost=0.30)
    source = LlmSource(client=client)
    budget = Budget(Limits(max_cost=0.5))

    first = source.fetch("who?", budget=budget)
    assert first.meta.cost == 0.30
    assert first.meta.stopped_by is None

    # 0.30 spent plus a 0.30 estimate crosses 0.50, so the second is refused before it runs.
    second = source.fetch("who?", budget=budget)
    assert second.meta.stopped_by == "max_cost"
    assert len(client.prompts) == 1


def test_without_a_caller_budget_each_fetch_is_bounded_on_its_own() -> None:
    """Stated because it is the difference between a per-call and a per-run ceiling."""
    client = ScriptedClient([ONE_RECORD, ONE_RECORD], cost=0.30)
    source = LlmSource(properties={"limits.max_cost": 0.5}, client=client)

    assert source.fetch("who?").meta.cost == 0.30
    assert source.fetch("who?").meta.cost == 0.30
    assert len(client.prompts) == 2


def test_a_caller_budget_accumulates_across_calls() -> None:
    """Two fetches under one budget cannot each spend the whole ceiling.

    Without this a ceiling means "per call" while a caller reading it means "per run".
    """
    budget = Budget(Limits(max_calls=2))
    source = LlmSource(client=ScriptedClient([ONE_RECORD, ONE_RECORD, ONE_RECORD]))

    assert source.fetch("one", budget=budget).meta.calls == 1
    assert source.fetch("two", budget=budget).meta.calls == 1

    third = source.fetch("three", budget=budget)
    assert third.records == []
    assert third.meta.stopped_by == "max_calls"
    assert budget.calls == 2, "the refused attempt was never claimed"


def test_a_derived_child_spends_from_what_the_parent_has_left() -> None:
    """A fan-out leaf draws on the parent rather than receiving a fresh allowance."""
    parent = Budget(Limits(max_calls=2))
    child = parent.derive(Limits(max_calls=5))

    child.reserve().settle(0.0)
    child.reserve().settle(0.0)

    with pytest.raises(BudgetExceeded):
        child.reserve()
    assert parent.calls == 2


def test_a_child_cannot_be_given_more_than_its_parent_holds() -> None:
    parent = Budget(Limits(max_cost=1.0))
    generous = parent.derive(Limits(max_cost=100.0))

    assert generous.remaining_cost() == 1.0


# ---------------------------------------- the run's budget reaches every knob, not just one --
def test_an_injected_default_client_honours_the_runs_retry_depth(
    litellm_stub: InstallStub,
) -> None:
    """Reservation bound but retry depth did not, so a limit half-applied.

    A DefaultLlmClient injected at construction carries its own budget. The run's limits
    have to reach the retryer and the timeout cap too, or max_retries silently comes from
    whichever budget the client happened to be built with.
    """
    stub = litellm_stub(LitellmStub(error=RuntimeError("boom")))
    client = DefaultLlmClient(budget=Budget(Limits(max_retries=9)))
    source = LlmSource(properties={"limits.max_retries": 0}, client=client)

    # A transport failure surfaces; only a budget stop comes back as a result.
    with pytest.raises(TransportError):
        source.fetch("who?")

    assert len(stub.calls) == 1, "the run's retry depth must win over the client's own"


def test_an_injected_default_client_caps_the_timeout_at_the_runs_remaining_time(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload(ONE_RECORD)))
    client = DefaultLlmClient(budget=Budget())
    source = LlmSource(properties={"limits.max_seconds": 30.0}, client=client)

    source.fetch("who?")

    assert stub.calls, "the call must have happened"
    timeout = stub.calls[0].get("timeout")
    assert timeout is not None and timeout <= 30.0


def test_a_ceiling_carried_only_by_the_callers_budget_is_still_checked() -> None:
    """The chain, not this fetch's own limits.

    Reading the child alone let a parent-only ceiling reach a client that had never claimed
    it could hold one, which is the refusal being bypassed rather than enforced.
    """
    source = LlmSource(client=FunctionClient(_stub_fn))
    parent = Budget(Limits(max_cost=0.5))

    with pytest.raises(ConfigError, match=r"limits\.max_cost"):
        source.fetch("who?", budget=parent)


# ---------------------------------------------- per-request attribution under concurrency --
class _BarrierClient:
    """One client serving two concurrent fetches, each reporting its own usage."""

    def __init__(self) -> None:
        self.usages: list[Usage] = []
        self._entered = 0
        self._both_in = asyncio.Event()

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        raise AssertionError("this double is for the async path")

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        self._entered += 1
        mine = self._entered
        if mine == 2:
            self._both_in.set()
        # Hold both calls inside the client at once, which is the shape that let two
        # fetches slice each other's entries out of a shared history.
        await self._both_in.wait()
        record = Usage(model=f"model-{mine}", prompt_tokens=mine * 10, completion_tokens=mine)
        self.usages.append(record)
        sink = (properties or {}).get("usage_sink")
        if isinstance(sink, list):
            cast("list[Usage]", sink).append(record)
        budget = (properties or {}).get("budget")
        if isinstance(budget, Budget):
            budget.reserve().settle(0.0)
        return ONE_RECORD


async def test_concurrent_fetches_do_not_import_each_others_attribution() -> None:
    """Each result reports what its own call used, not the sum of both."""
    client = _BarrierClient()
    source = LlmSource(client=client)

    first, second = await asyncio.gather(source.afetch("one"), source.afetch("two"))

    reported = sorted([first.meta.prompt_tokens, second.meta.prompt_tokens])
    assert reported == [10, 20], "each fetch must report only its own tokens"
    assert {first.meta.model, second.meta.model} == {"model-1", "model-2"}
    assert first.meta.calls == second.meta.calls == 1


@pytest.mark.parametrize("value", [True, "abc", None, 2.9, -1])
def test_a_malformed_retry_limit_is_refused_like_every_other_limit(value: object) -> None:
    """It was coerced with int() before reaching Limits, so it alone escaped validation.

    True became one, 2.9 became two, and a string raised a bare ValueError naming nothing.
    """
    source = LlmSource(
        properties={"limits.max_retries": value}, client=ScriptedClient([ONE_RECORD])
    )

    with pytest.raises(ConfigError, match="limits.max_retries"):
        source.fetch("who?")


def test_an_unlisted_model_with_supplied_rates_can_carry_a_cost_ceiling() -> None:
    """The documented escape hatch, exercised end to end rather than in the estimator alone."""
    stub_rates = {
        "llm.model": "brand-new-model",
        "llm.max_tokens": 64,
        "llm.price_per_input_token": 1e-6,
        "llm.price_per_output_token": 2e-6,
        "limits.max_cost": 1.0,
    }
    source = LlmSource(properties=stub_rates, client=ScriptedClient([ONE_RECORD], cost=0.01))
    result = source.fetch("who?")

    assert result.records == [{"name": "Ada", "age": 36}]
    assert result.meta.stopped_by is None


# ------------------------------------------------ the ceiling as an input to the query --
def test_a_cost_ceiling_supplied_with_the_query_binds_that_query(
    litellm_stub: InstallStub,
) -> None:
    """The ceiling belongs to the request, not only to how the source was configured.

    A caller that builds one source and asks many questions sets the ceiling per question,
    which is what `optional_properties` on the data-layer surface is for.
    """
    litellm_stub(LitellmStub(chat_payload(ONE_RECORD), cost=0.02))
    source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64})

    refused = source.execute_query(
        {"op": "LLM.FETCH", "query": "who?"},
        optional_properties={"limits.max_cost": 0.000001},
    )
    allowed = source.execute_query(
        {"op": "LLM.FETCH", "query": "who?"},
        optional_properties={"limits.max_cost": 1.0},
    )

    assert refused == []
    assert allowed == [{"name": "Ada", "age": 36}]


def test_a_per_query_ceiling_does_not_leak_into_the_next_query(
    litellm_stub: InstallStub,
) -> None:
    """Each query carries its own, so one tight request does not bind the one after it."""
    litellm_stub(LitellmStub(chat_payload(ONE_RECORD), cost=0.02))
    source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64})

    source.execute_query(
        {"op": "LLM.FETCH", "query": "who?"}, optional_properties={"limits.max_cost": 0.000001}
    )
    after = source.execute_query({"op": "LLM.FETCH", "query": "who?"})

    assert after == [{"name": "Ada", "age": 36}]


def test_a_misspelled_ceiling_on_the_query_is_refused_too() -> None:
    """The closed key set applies wherever the limits arrive from."""
    source = LlmSource(client=ScriptedClient([ONE_RECORD]))

    with pytest.raises(ConfigError, match="unknown limit"):
        source.execute_query("who?", optional_properties={"limits.max_cst": 0.5})


def test_a_deadline_crossed_on_the_callers_budget_reaches_the_result(
    litellm_stub: InstallStub,
) -> None:
    """Read from the child alone, the run returned a complete-looking answer past its deadline."""
    clock = FakeClock()
    litellm_stub(LitellmStub(chat_payload(ONE_RECORD)))
    parent = Budget(Limits(max_seconds=1.0), clock=clock)
    source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64})

    clock.advance(2.0)
    result = source.fetch("who?", budget=parent)

    assert result.meta.stopped_by == "max_seconds"
    assert result.meta.outcome == "stopped_by_limit"


def test_a_misspelled_limit_is_refused_even_for_an_empty_request() -> None:
    """A closed key set that only applies to requests reaching the model is not closed.

    An empty request is also the cheapest way to check configuration without spending.
    """
    source = LlmSource(client=ScriptedClient([]))

    with pytest.raises(ConfigError, match="unknown limit"):
        source.fetch("", properties={"limits.max_cst": 0.5})


def test_a_reused_client_does_not_accumulate_semaphores_across_contended_loops() -> None:
    """A reused client prunes semaphores bound to earlier event loops.

    A semaphore binds to its loop only once a caller waits on it. With two concurrent fetches
    and a parallelism of one, the wait binds it. The semaphore then references its loop,
    so a weak-keyed entry is never dropped.

    The map is bounded rather than empty: the loop that just closed is still present until
    the next call prunes it. Bounded is the property; zero is not achievable without the
    client knowing when a fetch it did not start has finished.
    """
    import asyncio
    import gc

    stub = LitellmStub(chat_payload(ONE_RECORD))
    with _installed(stub):
        client = DefaultLlmClient(
            {"llm.model": "gpt-4o-mini", "llm.max_tokens": 64, "llm.max_parallel": 1}
        )
        source = LlmSource(client=client)

        async def two_at_once() -> None:
            stub.hold = asyncio.Event()
            stub.all_entered = asyncio.Event()
            stub.expected = 1
            tasks = [
                asyncio.create_task(source.afetch("who?")),
                asyncio.create_task(source.afetch("who else?")),
            ]
            # With a parallelism of one, the second caller waits on the semaphore while the
            # first is held inside the provider. That wait is what binds the semaphore to
            # the loop; without it the map is never populated and the test measures nothing.
            await asyncio.wait_for(stub.all_entered.wait(), timeout=5)
            await asyncio.sleep(0)
            stub.hold.set()
            await asyncio.gather(*tasks)

        for _ in range(12):
            asyncio.run(two_at_once())
        gc.collect()

    entries = client._semaphores  # pyright: ignore[reportPrivateUsage]
    assert len(entries) <= 1, f"twelve contended loops left {len(entries)} entries"


async def test_max_parallel_binds_when_no_client_is_injected() -> None:
    """The bound must hold on the path a caller actually takes.

    The test above injects one `DefaultLlmClient` and shares it across both fetches, which
    is the only reason its bound held. `LlmSource` builds a fresh client per fetch unless
    the caller supplies one, and the semaphore registry used to be per instance — so on the
    default path every concurrent fetch got its own full-permit semaphore and
    `llm.max_parallel` bounded nothing at all. Five documents call it the main lever against
    provider rate limiting, and the MCP server cannot inject a client, so it had no lever.
    """
    import asyncio

    stub = LitellmStub(chat_payload(ONE_RECORD))
    with _installed(stub):
        source = LlmSource(
            properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64, "llm.max_parallel": 1}
        )
        stub.hold = asyncio.Event()
        tasks = [asyncio.create_task(source.afetch(f"q{i}")) for i in range(4)]
        # Long enough that four unbounded callers would all be inside the provider. The
        # assertion is on how many got in, not on how long they took.
        await asyncio.sleep(0.2)
        entered = stub.entered
        stub.hold.set()
        await asyncio.gather(*tasks)

    assert entered == 1, f"max_parallel=1 admitted {entered} concurrent provider calls"


def test_both_siblings_crossing_one_deadline_report_it() -> None:
    """A shared ceiling belongs to every call in flight when it is crossed.

    Each call evaluates the chain's ceilings for itself when it folds, rather than reading
    or inheriting an ancestor's recorded stop. A deadline that has passed has passed for
    every call that looks, so both siblings report it; an admission refusal reaches only
    the call it refused, as the exception, and so cannot appear here.
    """
    import asyncio

    clock = FakeClock()
    parent = Budget(Limits(max_seconds=1.0), clock=clock)
    stub = LitellmStub(chat_payload(ONE_RECORD))

    with _installed(stub):
        source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64})

        async def both() -> list[Any]:
            stub.hold = asyncio.Event()
            stub.all_entered = asyncio.Event()
            stub.expected = 2
            tasks = [
                asyncio.create_task(source.afetch("one", budget=parent)),
                asyncio.create_task(source.afetch("two", budget=parent)),
            ]
            # Both are inside the provider before the clock moves, so they cross the
            # deadline while in flight rather than starting after it has already passed.
            await asyncio.wait_for(stub.all_entered.wait(), timeout=5)
            clock.advance(2.0)
            stub.hold.set()
            return list(await asyncio.gather(*tasks))

        results = asyncio.run(both())

    assert [r.meta.stopped_by for r in results] == ["max_seconds", "max_seconds"]
    assert {r.meta.outcome for r in results} == {"stopped_by_limit"}


def test_a_request_made_after_a_deadline_reports_it_too() -> None:
    """A deadline that has passed has passed for everyone, including a later request."""
    clock = FakeClock()
    parent = Budget(Limits(max_seconds=1.0), clock=clock)
    clock.advance(2.0)
    parent.derive().note_exhausted()
    assert parent.stopped_by == "max_seconds"

    assert parent.derive().note_exhausted() == "max_seconds"


def test_a_refused_sibling_does_not_mark_calls_that_were_admitted() -> None:
    """Two admitted, completed calls reported as stopped because a third was refused.

    That is the opposite failure from losing a shared deadline, and the same mechanism
    caused both: a stop was being copied between calls rather than evaluated for each.
    """
    parent = Budget(Limits(max_calls=2))
    source = LlmSource(client=ScriptedClient([ONE_RECORD, ONE_RECORD]))

    first = source.fetch("one", budget=parent)
    second = source.fetch("two", budget=parent)
    third = source.fetch("three", budget=parent)

    assert [first.meta.outcome, second.meta.outcome] == ["ok", "ok"]
    assert first.meta.stopped_by is None and second.meta.stopped_by is None
    assert third.meta.stopped_by == "max_calls"


CEILING = 0.02


def _slow_provider(delay: float) -> Any:
    """A provider that ignores the timeout it is handed, which real ones do."""
    import types

    module = types.ModuleType("litellm")

    async def acompletion(**_: object) -> dict[str, Any]:
        await asyncio.sleep(delay)
        return {"choices": [{"message": {"content": "[]"}}], "usage": {}}

    module.acompletion = acompletion  # pyright: ignore[reportAttributeAccessIssue]

    def _unused_sync(**_: object) -> None:
        raise AssertionError("the source uses the async path")

    def _no_cost(**_: object) -> float:
        return 0.0

    module.completion = _unused_sync  # pyright: ignore[reportAttributeAccessIssue]
    module.completion_cost = _no_cost  # pyright: ignore[reportAttributeAccessIssue]
    return module


def _failing_provider() -> Any:
    """A provider that fails immediately, so what elapses is backoff and nothing else."""
    import types

    module = types.ModuleType("litellm")

    async def acompletion(**_: object) -> dict[str, Any]:
        raise RuntimeError("provider refused")

    def _unused_sync(**_: object) -> None:
        raise AssertionError("the source uses the async path")

    module.acompletion = acompletion  # pyright: ignore[reportAttributeAccessIssue]
    module.completion = _unused_sync  # pyright: ignore[reportAttributeAccessIssue]

    def _no_cost(**_: object) -> float:
        return 0.0

    module.completion_cost = _no_cost  # pyright: ignore[reportAttributeAccessIssue]
    return module


@contextmanager
def _installed_module(module: Any) -> Any:
    """Install a bare provider module, for the cases a LitellmStub cannot express."""
    import sys

    previous = sys.modules.get("litellm")
    sys.modules["litellm"] = module
    try:
        yield
    finally:
        if previous is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = previous


def _axis_and_elapsed(source: LlmSource, question: str = "who?") -> tuple[str | None, float]:
    import time

    started = time.monotonic()
    try:
        axis = source.fetch(question).meta.stopped_by
    except BudgetExceeded as e:
        axis = e.limit
    return axis, time.monotonic() - started


# Exercise three distinct waits: a slow provider, retry backoff after transport failure,
# and a second call queued on a semaphore. The tolerance is four times the ceiling, below
# the eight-to-twenty-times overrun seen when a wait is unbounded.


def test_a_deadline_cancels_a_provider_that_ignores_its_timeout() -> None:
    with _installed_module(_slow_provider(CEILING * 20)):
        source = LlmSource(
            properties={
                "llm.model": "gpt-4o-mini",
                "llm.max_tokens": 64,
                "limits.max_seconds": CEILING,
            }
        )
        axis, elapsed = _axis_and_elapsed(source)
    assert elapsed < CEILING * 4, f"{elapsed:.4f}s against a {CEILING}s ceiling"
    assert axis == "max_seconds"


def test_the_backoff_wait_is_capped_at_the_time_left() -> None:
    """Isolating the cap, because the surrounding deadline hides a regression in it.

    The end-to-end test below cannot see this: with the whole call under a deadline, an
    uncapped backoff is cancelled mid-sleep and reports the same axis after the same
    elapsed time. What the cap buys is a retry that still happens inside the remaining
    time rather than sleeping past it, and only this level can tell the two apart.
    """
    budget = Budget(Limits(max_seconds=10))
    wait = _WaitWithin(budget)

    class _State:
        attempt_number = 8  # far enough up the exponential to exceed any real headroom

    assert wait(_State()) > 0.5, "the exponential backoff is the figure being capped"

    spent = Budget(Limits(max_seconds=10), clock=iter([0.0, 9.99, 9.99, 9.99]).__next__)
    assert _WaitWithin(spent)(_State()) <= 0.02, "a backoff may not outlast the time left"

    assert _WaitWithin(Budget())(_State()) > 0.5, "no ceiling means no cap"


def test_a_deadline_caps_retry_backoff_end_to_end() -> None:
    """Fifty retries against a provider that fails at once, so only backoff can elapse."""
    with _installed_module(_failing_provider()):
        source = LlmSource(
            properties={
                "llm.model": "gpt-4o-mini",
                "llm.max_tokens": 64,
                "limits.max_seconds": CEILING,
                "limits.max_retries": 50,
            }
        )
        axis, elapsed = _axis_and_elapsed(source)
    assert elapsed < CEILING * 4, f"{elapsed:.4f}s of backoff against a {CEILING}s ceiling"
    assert axis == "max_seconds"


def test_a_deadline_cancels_a_wait_on_the_parallelism_semaphore() -> None:
    """One slot, one call holding it, and a second that can only be queued behind it.

    Counting provider entries is what makes this about the semaphore. Asserting only that
    the second call ended near the ceiling with the right axis held with the semaphore
    removed entirely, because the same slow provider and the same surrounding deadline
    produce the same timing whether the call queued or went straight through.
    """
    import time
    import types

    entered = asyncio.Event()
    entries = 0
    module = types.ModuleType("litellm")

    async def acompletion(**_: object) -> dict[str, Any]:
        nonlocal entries
        entries += 1
        entered.set()
        await asyncio.sleep(CEILING * 20)
        return {"choices": [{"message": {"content": "[]"}}], "usage": {}}

    def _no_cost(**_: object) -> float:
        return 0.0

    module.acompletion = acompletion  # pyright: ignore[reportAttributeAccessIssue]
    module.completion = _no_cost  # pyright: ignore[reportAttributeAccessIssue]
    module.completion_cost = _no_cost  # pyright: ignore[reportAttributeAccessIssue]

    async def exercise() -> tuple[str | None, float, int]:
        client = DefaultLlmClient(
            properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64, "llm.max_parallel": 1}
        )
        holder = asyncio.create_task(client.acomplete("holds the only slot"))
        await asyncio.wait_for(entered.wait(), timeout=CEILING * 30)
        started = time.monotonic()
        try:
            await client.acomplete("queued", {"budget": Budget(Limits(max_seconds=CEILING))})
            axis = None
        except BudgetExceeded as e:
            axis = e.limit
        elapsed = time.monotonic() - started
        # Read before the holder is released, so it reports what the queued call did.
        seen = entries
        holder.cancel()
        await asyncio.gather(holder, return_exceptions=True)
        return axis, elapsed, seen

    with _installed_module(module):
        axis, elapsed, seen = asyncio.run(exercise())
    assert seen == 1, f"the queued call reached the provider; the semaphore did not hold it: {seen}"
    assert elapsed < CEILING * 4, f"{elapsed:.4f}s queued against a {CEILING}s ceiling"
    assert axis == "max_seconds"


def test_an_inner_timeout_is_not_reported_as_the_deadline() -> None:
    """A store raising TimeoutError under an ample budget is its own failure, not a ceiling."""

    class Exploding:
        def get(self, key: str) -> None:
            raise TimeoutError("cache backend timed out")

        def set(self, key: str, value: Any) -> None:  # pragma: no cover - never reached
            raise AssertionError("get raises first")

    async def exercise() -> None:
        client = DefaultLlmClient(
            properties={
                "llm.model": "gpt-4o-mini",
                "llm.max_tokens": 64,
                "cache_store": Exploding(),
                "budget": Budget(Limits(max_seconds=60)),
            }
        )
        await client.acomplete("who?")

    with _installed_module(_slow_provider(0.0)), pytest.raises(TimeoutError, match="cache backend"):
        asyncio.run(exercise())


def test_an_inherited_deadline_names_itself_truthfully() -> None:
    """The binding ceiling may be the caller's, and the message may not invent a figure."""

    async def exercise() -> str:
        parent = Budget(Limits(max_seconds=0.005))
        client = DefaultLlmClient(
            properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64}, budget=parent.derive()
        )
        try:
            await client.acomplete("who?")
        except BudgetExceeded as e:
            return str(e)
        raise AssertionError("the inherited deadline did not stop the call")

    with _installed_module(_slow_provider(1.0)):
        message = asyncio.run(exercise())
    assert "None" not in message, message
    assert "inherited" in message
    assert "0.005" in message, message


def test_a_looser_local_ceiling_is_not_named_over_the_one_that_bound() -> None:
    """A child may carry its own ceiling and still be stopped by its caller's tighter one."""

    async def exercise() -> str:
        parent = Budget(Limits(max_seconds=CEILING))
        client = DefaultLlmClient(
            properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64},
            budget=parent.derive(Limits(max_seconds=1.0)),
        )
        try:
            await client.acomplete("who?")
        except BudgetExceeded as e:
            return str(e)
        raise AssertionError("the tighter inherited deadline did not stop the call")

    with _installed_module(_slow_provider(1.0)):
        message = asyncio.run(exercise())
    assert "1.0s" not in message, f"named the local ceiling that did not bind: {message}"
    assert f"{CEILING}s" in message, message


def test_a_provider_that_swallows_cancellation_cannot_return_a_late_success() -> None:
    """`wait_for` waits for the cancellation to be acknowledged, and may never get it.

    Work that catches CancelledError and goes on to return hands back a normal answer from
    after the deadline. The answer is real and the ceiling is not — which is worse than
    reporting late, because nothing tells the caller the limit failed. It cannot be stopped
    from here; it can be refused.
    """
    import types

    module = types.ModuleType("litellm")

    async def acompletion(**_: object) -> dict[str, Any]:
        try:
            await asyncio.sleep(CEILING * 20)
        except asyncio.CancelledError:
            await asyncio.sleep(CEILING * 2)  # "cleanup" that outlives the deadline
        return {"choices": [{"message": {"content": "[]"}}], "usage": {}}

    def _no_cost(**_: object) -> float:
        return 0.0

    module.acompletion = acompletion  # pyright: ignore[reportAttributeAccessIssue]
    module.completion = _no_cost  # pyright: ignore[reportAttributeAccessIssue]
    module.completion_cost = _no_cost  # pyright: ignore[reportAttributeAccessIssue]

    async def exercise() -> str | None:
        client = DefaultLlmClient(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64})
        try:
            await client.acomplete("who?", {"budget": Budget(Limits(max_seconds=CEILING))})
        except BudgetExceeded as e:
            return e.limit
        return None

    with _installed_module(module):
        axis = asyncio.run(exercise())
    assert axis == "max_seconds", "a late answer was returned as a success"


def test_work_that_never_yields_runs_past_the_ceiling_as_documented() -> None:
    """The exclusion in ADR-024, asserted rather than only written down.

    ``asyncio`` cannot interrupt code that is not awaiting, so a blocking cache store —
    standing here for every synchronous phase, since ``CacheStore`` is the one a caller
    supplies — runs to completion and is measured afterwards. This is what is left outside
    the ceiling after the waits inside it were bounded, and it is a test so that a later
    change cannot quietly re-assert the stronger claim.

    What must stay true is the reporting: the axis is still named, and named truthfully.
    """
    import time

    class Blocking:
        def get(self, key: str) -> None:
            time.sleep(CEILING * 5)

        def set(self, key: str, value: Any) -> None:
            return None

    async def exercise() -> tuple[str | None, float]:
        client = DefaultLlmClient(
            properties={
                "llm.model": "gpt-4o-mini",
                "llm.max_tokens": 64,
                "cache_store": Blocking(),
            }
        )
        started = time.monotonic()
        try:
            await client.acomplete("who?", {"budget": Budget(Limits(max_seconds=CEILING))})
            axis = None
        except BudgetExceeded as e:
            axis = e.limit
        return axis, time.monotonic() - started

    with _installed_module(_slow_provider(0.0)):
        axis, elapsed = asyncio.run(exercise())

    assert elapsed > CEILING, "if this ever holds the ceiling, ADR-024 and the README are stale"
    assert axis == "max_seconds", "the axis is reported truthfully even when reported late"


def test_a_provider_that_never_honours_cancellation_holds_the_caller() -> None:
    """The other end of ADR-024's exclusion, asserted so it cannot be quietly reclaimed.

    Cancelling is a request. The deadline makes it and then waits for the acknowledgement,
    and work that never acknowledges holds this call open. Letting go of it instead was
    tried and cost more than it bought: the caller's own cancellation stopped reaching the
    work, the abandoned task could be collected mid-flight or keep ``asyncio.run`` from
    returning, and it settled real cost into the caller's budget after a result reporting
    none had already been handed back.

    The provider signals when it is entered and when each cancellation reaches it, so this
    observes the deadline firing rather than only that nothing came back. Asserting
    lateness alone passed with the deadline removed entirely, which is the failure this
    test exists to detect.
    """
    import time
    import types

    entered = asyncio.Event()
    cancellations: list[float] = []
    relent = asyncio.Event()
    module = types.ModuleType("litellm")

    async def acompletion(**_: object) -> dict[str, Any]:
        entered.set()
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancellations.append(time.monotonic())
                if relent.is_set():
                    raise
                continue

    def _no_cost(**_: object) -> float:
        return 0.0

    module.acompletion = acompletion  # pyright: ignore[reportAttributeAccessIssue]
    module.completion = _no_cost  # pyright: ignore[reportAttributeAccessIssue]
    module.completion_cost = _no_cost  # pyright: ignore[reportAttributeAccessIssue]

    async def exercise() -> tuple[bool, float, list[float]]:
        client = DefaultLlmClient(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64})
        call = asyncio.ensure_future(
            client.acomplete("who?", {"budget": Budget(Limits(max_seconds=CEILING))})
        )
        await asyncio.wait_for(entered.wait(), timeout=CEILING * 30)
        started = time.monotonic()
        # `wait`, not `wait_for`: `wait_for` would block on the same withheld
        # acknowledgement and hang the suite rather than measuring it.
        done, _ = await asyncio.wait({call}, timeout=CEILING * 6)
        relent.set()
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        return bool(done), started, cancellations

    with _installed_module(module):
        finished, started, seen = asyncio.run(exercise())

    assert seen, "the deadline never cancelled the provider at all"
    # The first cancellation is the deadline's, and it must arrive at the ceiling rather
    # than at the end of the observation window. Without the deadline the only cancellation
    # would be the cleanup one, six ceilings later.
    assert seen[0] - started < CEILING * 3, (
        f"the first cancellation came {seen[0] - started:.4f}s in, not at the {CEILING}s ceiling"
    )
    assert not finished, (
        "the caller returned despite work that never acknowledged cancellation; if that now "
        "holds, the exclusion in ADR-024 and the README is stale"
    )


def test_client_side_rate_limiting_is_refused_by_name() -> None:
    """Withdrawn before release, and refused rather than accepted and ignored.

    Client-side spacing has to reconcile with per-attempt budget admission, and every
    arrangement let one corrupt the other: claimed before admission it charged an interval
    to calls the budget refused and let a waiter hold a shared allowance a live sibling then
    lost; claimed after it, retries entered the provider at the speed of the backoff. The
    key is refused by name so a caller who sets it learns that, instead of believing a
    ceiling is holding.
    """

    def refusal(build: Callable[[], object]) -> str:
        with pytest.raises(ConfigError) as caught:
            build()
        return str(caught.value)

    messages = [
        refusal(lambda: LlmSource(properties={"llm.rate_limit": 10}).fetch("who?")),
        refusal(lambda: LlmSource().fetch("who?", properties={"llm.rate_limit": 10})),
        refusal(
            lambda: LlmSource().execute_query("who?", optional_properties={"llm.rate_limit": 1})
        ),
        refusal(
            lambda: LlmSource().execute_query(
                {"op": "LLM.MODELS"}, optional_properties={"llm.rate_limit": 1}
            )
        ),
        refusal(lambda: DefaultLlmClient(properties={"llm.rate_limit": 10}).complete("who?")),
    ]
    for message in messages:
        # The key, why it is refused, and something to do instead. A bare "max_parallel"
        # match would also be satisfied by the generic accepted-keys list in an unknown-key
        # error.
        assert "llm.rate_limit" in message, message
        assert "does not do it" in message, message
        assert "llm.max_parallel" in message, message


def test_a_refused_ceiling_does_not_import_the_provider() -> None:
    """A request a ceiling was always going to refuse must not reach the provider library.

    Importing it is not contacting a provider, but the shipped adapter fetches a remote
    price map on first use. A CLI run under a cost ceiling it could never satisfy made that
    outbound lookup, printed the failure to standard output, and took about a second to
    report a refusal it could have made from local information alone.
    """
    import sys

    imported = False

    class Tripwire:
        # find_spec, not find_module: the latter was removed in 3.12, so a finder defining
        # it is never consulted and this test would pass whatever the code did.
        def find_spec(self, name: str, path: object = None, target: object = None) -> None:
            nonlocal imported
            if name == "litellm" or name.startswith("litellm."):
                imported = True
            return None

    previous = sys.modules.pop("litellm", None)
    sys.meta_path.insert(0, Tripwire())  # pyright: ignore[reportArgumentType]
    try:
        source = LlmSource(
            properties={
                "llm.model": "gpt-4o-mini",
                "llm.max_tokens": 4096,
                "limits.max_cost": 0.0000001,  # far below the output allowance's floor
            }
        )
        result = source.fetch("who?")
    finally:
        sys.meta_path.pop(0)
        if previous is not None:
            sys.modules["litellm"] = previous

    assert result.meta.stopped_by == "max_cost"
    assert result.meta.calls == 0
    assert not imported, "the provider library was imported for a call the ceiling refused"


def test_an_import_failure_leaves_the_budget_untouched() -> None:
    """Admission takes nothing, so an import that fails has nothing to give back.

    The order is check, import, claim. A missing provider extra therefore reaches the
    caller with the budget exactly as it found it: no counted attempt, no reserved bound.
    An earlier ordering claimed first and had to refund, and refunding was what made a
    sibling on a shared budget visible to a ceiling that had not been crossed.
    """
    import sys

    budget = Budget(Limits(max_calls=3))
    previous = sys.modules.pop("litellm", None)

    class Blocker:
        def find_spec(self, name: str, path: object = None, target: object = None) -> None:
            if name == "litellm":
                raise ImportError("no provider extra installed")
            return None

    sys.meta_path.insert(0, Blocker())  # pyright: ignore[reportArgumentType]
    try:
        source = LlmSource(properties={"llm.model": "gpt-4o-mini"})
        with pytest.raises(Exception):  # noqa: B017 - the kind is not what this pins
            source.fetch("who?", budget=budget)
    finally:
        sys.meta_path.pop(0)
        if previous is not None:
            sys.modules["litellm"] = previous

    assert budget.calls == 0, "a failed import counted an attempt"
    assert budget.committed == 0.0, "a failed import reserved cost"


def test_out_of_order_resolution_leaves_no_phantom_reservation() -> None:
    """A ceiling must not be crossed by a rounding remnant of claims already resolved.

    The total was a running sum: each estimate added on claim and subtracted on resolve.
    Resolved out of order that left a positive residue — 1.1e-16 in the reproduction — and
    an exact remaining-cost claim was then refused by a ceiling it fitted inside. The total
    is re-derived from the claims still outstanding instead, so it returns to exactly zero.
    """
    budget = Budget(Limits(max_cost=1.0))
    first = budget.reserve(0.2)
    second = budget.reserve(0.666)

    first.settle(0.1)
    second.release()

    assert budget.reserved == 0.0, f"residue of {budget.reserved!r} after every claim resolved"
    # 0.1 spent, so exactly 0.9 remains and a claim for it must be admitted.
    budget.reserve(0.9)
    assert budget.committed == pytest.approx(1.0)


def test_a_slow_import_does_not_refuse_a_sibling_on_a_shared_budget() -> None:
    """Admission takes nothing, so it cannot exhaust a ceiling on another caller's behalf.

    Claiming before the import made the claim visible to a sibling sharing the budget, which
    was then terminally refused on an allowance the final ledger said was free. The claim is
    the last thing that happens now, immediately before the provider is entered.
    """
    import sys
    import threading
    import time

    previous = sys.modules.pop("litellm", None)

    class SlowAndAbsent:
        def find_spec(self, name: str, path: object = None, target: object = None) -> None:
            if name == "litellm":
                time.sleep(0.3)
                raise ImportError("no provider extra installed")
            return None

    sys.meta_path.insert(0, SlowAndAbsent())  # pyright: ignore[reportArgumentType]
    parent = Budget(Limits(max_calls=1))
    seen: dict[str, str] = {}

    def run(tag: str, delay: float) -> None:
        time.sleep(delay)
        try:
            result = LlmSource(properties={"llm.model": "gpt-4o-mini"}).fetch("q", budget=parent)
            seen[tag] = f"stopped_by={result.meta.stopped_by}"
        except BaseException as e:  # noqa: BLE001 - the type is the assertion
            seen[tag] = type(e).__name__

    try:
        first = threading.Thread(target=run, args=("first", 0.0))
        second = threading.Thread(target=run, args=("second", 0.1))
        first.start()
        second.start()
        first.join()
        second.join()
    finally:
        sys.meta_path.pop(0)
        if previous is not None:
            sys.modules["litellm"] = previous

    # Both hit the same real problem. Neither is told a ceiling stopped it, because none did.
    assert seen["first"] == "ConfigError", seen
    assert seen["second"] == "ConfigError", seen
    assert parent.calls == 0 and parent.stopped_by is None, (parent.calls, parent.stopped_by)


def test_the_provider_timeout_reflects_the_time_left_after_the_import() -> None:
    """The cap is derived after the import, because the import spends part of the deadline.

    Derived before it, a provider that honours its timeout was handed the whole original
    allowance and answered well past the ceiling — the documented cap silently stopped
    meaning what it says.
    """
    import types

    seen: dict[str, Any] = {}
    module = types.ModuleType("litellm")
    ticks = iter([0.0, 0.0, 0.0, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8])

    def completion(**kwargs: object) -> dict[str, Any]:
        seen.update(kwargs)
        return {"choices": [{"message": {"content": "[]"}}], "usage": {}}

    module.completion = completion  # pyright: ignore[reportAttributeAccessIssue]

    def _no_cost(**_: object) -> float:
        return 0.0

    module.completion_cost = _no_cost  # pyright: ignore[reportAttributeAccessIssue]

    class SlowImport:
        """Advances the clock the way a real first import of the adapter does."""

        def find_spec(self, name: str, path: object = None, target: object = None) -> None:
            return None

    budget = Budget(Limits(max_seconds=1.0), clock=lambda: next(ticks))
    with _installed_module(module):
        sys_meta = __import__("sys").meta_path
        sys_meta.insert(0, SlowImport())  # pyright: ignore[reportArgumentType]
        try:
            client = DefaultLlmClient(properties={"llm.model": "gpt-4o-mini", "llm.timeout": 1.0})
            client.complete("who?", {"budget": budget})
        finally:
            sys_meta.pop(0)

    # 0.8 of the 1.0 ceiling is gone by the time the provider is entered.
    assert seen["timeout"] == pytest.approx(0.2, abs=0.01), seen["timeout"]


def test_an_exception_in_the_block_leaves_no_claim_outstanding() -> None:
    """The ordinary case: the block raises after the handle exists."""
    budget = Budget(Limits(max_calls=3, max_cost=1.0))

    with pytest.raises(KeyboardInterrupt), budget.attempt(0.4) as reservation:
        assert not reservation.resolved
        raise KeyboardInterrupt

    assert budget.calls == 0, "the interrupted attempt stayed counted"
    assert budget.committed == 0.0, "its bound stayed reserved"


def test_an_interruption_inside_the_claim_leaves_no_claim_outstanding() -> None:
    """The window that matters, which the block-level case above cannot reach.

    A signal or trace hook can land after the ledger has been mutated and before control
    returns to the caller. Built inside that call, the handle did not exist yet, so the
    cleanup had nothing to release and the claim stayed counted with nothing able to give
    it back. The handle is created first now and the finalizer holds it before the claim is
    taken, so this unwinds clean.
    """
    budget = Budget(Limits(max_calls=3, max_cost=1.0))
    real_claim = budget._claim  # pyright: ignore[reportPrivateUsage]
    fired: list[str] = []

    def claim_then_interrupt(*args: Any, **kwargs: Any) -> None:
        real_claim(*args, **kwargs)  # the family is now mutated
        fired.append("claimed")
        raise KeyboardInterrupt  # ...and control never reaches the caller

    budget._claim = claim_then_interrupt  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
    entered = False
    try:
        with budget.attempt(0.4):
            entered = True
    except KeyboardInterrupt:
        pass
    finally:
        budget._claim = real_claim  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]

    assert fired == ["claimed"], "the ledger was never mutated, so nothing was tested"
    assert not entered, "the block ran despite the interrupt"
    assert budget.calls == 0, "a claim survived with no handle to release it"
    assert budget.committed == 0.0
    assert budget.reserved == 0.0


def test_the_shared_semaphore_registry_does_not_grow_without_bound() -> None:
    """Sharing the registry fixed the ceiling; it also outlived the client that made it.

    The sweep drops closed loops, which was sufficient while the map was per instance and
    died with it. At class scope an entry keeps its loop reachable, so a loop that is
    abandoned rather than closed is pinned by the map itself along with its selector and
    descriptors — a hundred and fifty of them took a process from thirty-eight open
    descriptors to four hundred and eighty-eight.
    """
    import asyncio
    import gc

    from blue_data_llm_source.llm.client import DefaultLlmClient

    registry = DefaultLlmClient._semaphores  # pyright: ignore[reportPrivateUsage]
    registry.clear()
    try:
        for _ in range(80):
            loop = asyncio.new_event_loop()  # abandoned on purpose: never closed
            client = DefaultLlmClient({"llm.model": "gpt-4o-mini"})

            async def touch(bound: DefaultLlmClient = client) -> None:
                bound._semaphore({"llm.max_parallel": 2})  # pyright: ignore[reportPrivateUsage]

            loop.run_until_complete(touch())
        assert len(registry) <= 16, f"registry grew to {len(registry)} abandoned loops"
    finally:
        registry.clear()
        gc.collect()
