"""The client's call path, with litellm replaced by a stub module."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.llm.client import DefaultLlmClient
from blue_data_llm_source.types import BudgetExceeded, ConfigError, TransportError
from tests.doubles import LitellmStub, chat_payload

InstallStub = Callable[[LitellmStub], LitellmStub]


def _complete(client: DefaultLlmClient) -> str:
    return client.complete("who?")


def _acomplete(client: DefaultLlmClient) -> str:
    return asyncio.run(client.acomplete("who?"))


def test_complete_returns_the_message_content(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload('[{"name": "Ada"}]')))
    assert DefaultLlmClient().complete("who?") == '[{"name": "Ada"}]'


async def test_acomplete_returns_the_message_content(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload('[{"name": "Ada"}]')))
    assert await DefaultLlmClient().acomplete("who?") == '[{"name": "Ada"}]'


def test_the_prompt_reaches_the_provider_as_a_user_message(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    DefaultLlmClient().complete("who is the president?")
    assert stub.calls[0]["messages"] == [{"role": "user", "content": "who is the president?"}]


def test_the_configured_model_reaches_the_provider(litellm_stub: InstallStub) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    DefaultLlmClient({"llm.model": "gpt-4o-mini"}).complete("who?")
    assert stub.calls[0]["model"] == "gpt-4o-mini"


def test_default_call_routes_to_openai_with_explicit_medium_reasoning(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    DefaultLlmClient({"llm.max_tokens": 25_000}).complete("who?")
    assert stub.calls[0]["model"] == "gpt-6-luna"
    assert stub.calls[0]["custom_llm_provider"] == "openai"
    assert stub.calls[0]["reasoning_effort"] == "medium"
    assert stub.calls[0]["max_completion_tokens"] == 25_000
    assert "temperature" not in stub.calls[0]
    assert "max_tokens" not in stub.calls[0]


def test_incompatible_litellm_is_refused_before_provider_contact(
    litellm_stub: InstallStub,
) -> None:
    stub = LitellmStub(chat_payload("[]"))
    stub.get_supported_openai_params = lambda **_kwargs: [  # type: ignore[method-assign]
        "max_completion_tokens"
    ]
    litellm_stub(stub)
    with pytest.raises(ConfigError, match="reasoning_effort"):
        DefaultLlmClient({"llm.max_tokens": 25_000}).complete("who?")
    assert stub.calls == []


def test_a_call_reserves_one_unit_of_the_call_budget(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload("[]")))
    budget = Budget(Limits(max_calls=2))
    client = DefaultLlmClient(budget=budget)

    client.complete("who?")
    client.complete("who else?")

    assert budget.calls == 2
    with pytest.raises(BudgetExceeded, match="call limit"):
        client.complete("and again?")


def test_reported_cost_is_settled_against_the_budget(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload("[]"), cost=0.125))
    budget = Budget()
    DefaultLlmClient(budget=budget).complete("who?")
    assert budget.cost == pytest.approx(0.125)


@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-6-luna-2026-09-22"])
def test_luna_hard_cost_ceiling_keeps_the_conservative_bound(
    litellm_stub: InstallStub,
    invoke: Callable[[DefaultLlmClient], str],
    model: str,
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]"), cost=0.000_001))
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient(
        {"llm.model": model, "llm.max_tokens": 25_000},
        budget=budget,
    )

    invoke(client)

    assert budget.cost > 0.01
    assert client.last_usage is not None
    assert client.last_usage.cost_known is False


@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-6-luna-2026-09-22"])
def test_luna_hard_cost_ceiling_keeps_a_higher_reported_cost(
    litellm_stub: InstallStub,
    invoke: Callable[[DefaultLlmClient], str],
    model: str,
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]"), cost=0.50))
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient(
        {"llm.model": model, "llm.max_tokens": 25_000},
        budget=budget,
    )

    invoke(client)

    assert budget.cost == pytest.approx(0.50)
    assert client.last_usage is not None
    assert client.last_usage.cost == pytest.approx(0.50)
    assert client.last_usage.cost_known is False


def test_gpt_4o_hard_cost_ceiling_keeps_higher_reported_cost(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]"), cost=0.02))
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient(
        {"llm.model": "gpt-4o", "llm.max_tokens": 512},
        budget=budget,
    )

    client.complete("who?")

    assert budget.cost == pytest.approx(0.02)
    assert client.last_usage is not None
    assert client.last_usage.cost_known is True


@pytest.mark.parametrize("invoke", [_complete, _acomplete], ids=["sync", "async"])
def test_calculator_cost_equal_to_reservation_is_known(
    litellm_stub: InstallStub, invoke: Callable[[DefaultLlmClient], str]
) -> None:
    properties = {"llm.model": "gpt-4o-mini", "llm.max_tokens": 64}
    reservation = DefaultLlmClient(properties).bound_for_attempt("who?", {})
    litellm_stub(LitellmStub(chat_payload("[]"), cost=reservation))
    budget = Budget(Limits(max_cost=reservation))
    client = DefaultLlmClient(properties, budget=budget)

    invoke(client)

    assert budget.cost == reservation
    assert client.last_usage is not None
    assert client.last_usage.cost_known is True


@pytest.mark.parametrize("invoke", [_complete, _acomplete], ids=["sync", "async"])
@pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-6-luna"])
@pytest.mark.parametrize("readable", [False, True], ids=["unreadable", "readable"])
def test_contacted_attempt_keeps_reservation_before_next_admission(
    litellm_stub: InstallStub,
    invoke: Callable[[DefaultLlmClient], str],
    model: str,
    readable: bool,
) -> None:
    payload = chat_payload(
        "[]",
        model=model,
        usage={
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    )
    payload["service_tier"] = "default"
    stub = LitellmStub(payload, cost=0.000_000_001)
    if not readable:

        def unreadable_cost(**_kwargs: object) -> float:
            raise ValueError("no calculator price")

        stub.completion_cost = unreadable_cost  # type: ignore[method-assign]
    litellm_stub(stub)
    properties = {"llm.model": model, "llm.max_tokens": 64}
    probe = DefaultLlmClient(properties)
    bound = probe.bound_for_attempt("who?", {})
    parent = Budget(Limits(max_cost=bound * 1.5))
    client = DefaultLlmClient(properties, budget=parent.derive())

    invoke(client)

    assert parent.cost == pytest.approx(bound)
    assert client.last_usage is not None
    assert client.last_usage.cost == pytest.approx(bound)
    assert client.last_usage.cost_known is False
    with pytest.raises(BudgetExceeded, match="cost limit"):
        invoke(client)
    assert len(stub.calls) == 1
    assert parent.cost == pytest.approx(bound)


@pytest.mark.parametrize("invoke", [_complete, _acomplete], ids=["sync", "async"])
def test_unlimited_cost_keeps_readable_calculator_figure(
    litellm_stub: InstallStub, invoke: Callable[[DefaultLlmClient], str]
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]"), cost=0.000_000_001))
    client = DefaultLlmClient({"llm.model": "gpt-4o-mini", "llm.max_tokens": 64})

    invoke(client)

    assert client.last_usage is not None
    assert client.last_usage.cost == 0.000_000_001
    assert client.last_usage.cost_known is True


def test_a_refused_call_never_reaches_the_provider(litellm_stub: InstallStub) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient(budget=Budget(Limits(max_calls=0)))

    with pytest.raises(BudgetExceeded):
        client.complete("who?")

    assert stub.calls == []


def test_the_recorded_usage_names_the_model_that_answered(
    litellm_stub: InstallStub,
) -> None:
    """The payload's model wins over the requested one.

    A routing alias, or a name the provider resolves to a dated build, made the record repeat
    the request rather than name the answer — and the data model defines this field as the
    model that answered.
    """
    answered = chat_payload("[]")
    answered["model"] = "provider-routed-2026-08-01"
    litellm_stub(LitellmStub(answered))
    client = DefaultLlmClient({"llm.model": "routing-alias"})
    client.complete("who?")
    assert client.last_usage is not None
    assert client.last_usage.model == "provider-routed-2026-08-01"


def test_the_requested_model_is_used_when_the_payload_names_none(
    litellm_stub: InstallStub,
) -> None:
    payload = chat_payload("[]")
    payload.pop("model", None)
    litellm_stub(LitellmStub(payload))
    client = DefaultLlmClient({"llm.model": "gpt-4o-mini"})
    client.complete("who?")
    assert client.last_usage is not None
    assert client.last_usage.model == "gpt-4o-mini"


def test_a_response_with_null_content_is_rejected(litellm_stub: InstallStub) -> None:
    payload = chat_payload("x")
    payload["choices"][0]["message"]["content"] = None
    litellm_stub(LitellmStub(payload))

    with pytest.raises(TransportError, match="content is not text"):
        DefaultLlmClient().complete("who?")


def test_a_client_reused_across_event_loops_still_answers(
    litellm_stub: InstallStub,
) -> None:
    # One semaphore per loop and parallelism, so a client reused across separate loops
    # answers in each of them. This asserts the answering; whether the map sheds closed
    # loops is asserted in the focused contended-loop test, which has to contend on
    # purpose because a semaphore binds to its loop only when a caller actually waits.
    litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient()

    for _ in range(3):
        assert asyncio.run(client.acomplete("who?")) == "[]"

    assert len(client.usages) == 3


def test_an_empty_choices_list_is_rejected(litellm_stub: InstallStub) -> None:
    payload = chat_payload("x")
    payload["choices"] = []
    litellm_stub(LitellmStub(payload))

    with pytest.raises(TransportError, match="empty choices list"):
        DefaultLlmClient().complete("who?")


def test_simultaneous_loop_map_access_reuses_only_its_own_semaphore() -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(4, timeout=5)

    def worker(_index: int) -> tuple[asyncio.AbstractEventLoop, asyncio.Semaphore]:
        loop = asyncio.new_event_loop()

        async def check() -> asyncio.Semaphore:
            client = DefaultLlmClient()
            barrier.wait()
            first = client._semaphore({"llm.max_parallel": 2})  # pyright: ignore[reportPrivateUsage]
            for _ in range(100):
                assert client._semaphore({"llm.max_parallel": 2}) is first  # pyright: ignore[reportPrivateUsage]
                assert DefaultLlmClient()._semaphore({"llm.max_parallel": 2}) is first  # pyright: ignore[reportPrivateUsage]
            barrier.wait()
            return first

        try:
            semaphore = loop.run_until_complete(check())
        finally:
            loop.close()
        DefaultLlmClient._release_loop(loop)  # pyright: ignore[reportPrivateUsage]
        with DefaultLlmClient._semaphore_lock:  # pyright: ignore[reportPrivateUsage]
            assert loop not in DefaultLlmClient._semaphores  # pyright: ignore[reportPrivateUsage]
        return loop, semaphore

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(worker, range(4)))
    assert len({id(sem) for _, sem in results}) == 4
    assert all(loop.is_closed() for loop, _ in results)


async def test_same_loop_parallel_bound_is_shared_by_clients(litellm_stub: InstallStub) -> None:
    release = asyncio.Event()
    stub = litellm_stub(LitellmStub(chat_payload("[]"), hold=release))
    stub.expected = 2
    stub.all_entered = asyncio.Event()
    clients = [DefaultLlmClient({"llm.max_parallel": 2}) for _ in range(6)]
    jobs = [asyncio.create_task(client.acomplete("items")) for client in clients]
    await asyncio.wait_for(stub.all_entered.wait(), timeout=5)
    assert stub.entered == 2
    semaphores = [client._semaphore({"llm.max_parallel": 2}) for client in clients]  # pyright: ignore[reportPrivateUsage]
    assert all(sem is semaphores[0] for sem in semaphores)
    release.set()
    assert await asyncio.gather(*jobs) == ["[]"] * 6
    assert stub.entered == 6
    loop = asyncio.get_running_loop()
    closed = asyncio.new_event_loop()
    closed.close()
    DefaultLlmClient._release_loop(closed)  # pyright: ignore[reportPrivateUsage]
    with DefaultLlmClient._semaphore_lock:  # pyright: ignore[reportPrivateUsage]
        assert loop in DefaultLlmClient._semaphores  # pyright: ignore[reportPrivateUsage]
