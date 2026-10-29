"""Caching: a repeated call is answered from the store and costs nothing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.llm.client import DefaultLlmClient
from blue_data_llm_source.types import BudgetExceeded, CacheStore, ConfigError
from tests.doubles import LitellmStub, MemoryCache, chat_payload

InstallStub = Callable[[LitellmStub], LitellmStub]


class ProtocolOnlyCache:
    """A cache with exactly the surface ``CacheStore`` declares, and nothing more."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self.data.get(key)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


class GetOnlyCache:
    def get(self, key: str) -> None:
        return None


class NoGetCache:
    get: None = None

    def set(self, key: str, value: object) -> None:
        pass


def test_a_repeated_call_is_answered_from_the_cache(
    litellm_stub: InstallStub, cache: MemoryCache
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload('[{"name": "Ada"}]')))
    client = DefaultLlmClient({"cache_store": cache})

    first = client.complete("who?")
    second = client.complete("who?")

    assert first == second == '[{"name": "Ada"}]'
    assert len(stub.calls) == 1


def test_a_cache_hit_consumes_no_cost_budget(litellm_stub: InstallStub, cache: MemoryCache) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]"), cost=0.25))
    budget = Budget(Limits(max_cost=1.0, max_calls=1))
    client = DefaultLlmClient(
        {"cache_store": cache, "llm.model": "gpt-4o-mini", "llm.max_tokens": 512}, budget=budget
    )

    client.complete("who?")
    assert budget.cost == pytest.approx(0.25)
    assert budget.calls == 1

    client.complete("who?")
    assert budget.cost == pytest.approx(0.25)
    assert budget.calls == 1
    assert budget.stopped_by is None
    assert len(stub.calls) == 1


def test_a_cache_hit_is_recorded_as_a_free_call(
    litellm_stub: InstallStub, cache: MemoryCache
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]", usage={"prompt_tokens": 11}), cost=0.25))
    client = DefaultLlmClient({"cache_store": cache})

    client.complete("who?")
    client.complete("who?")

    hit = client.usages[-1]
    assert hit.cache_hit is True
    assert hit.cost == 0.0
    assert hit.prompt_tokens == 0
    assert client.usages[0].cache_hit is False


def test_a_different_prompt_is_a_different_cache_entry(
    litellm_stub: InstallStub, cache: MemoryCache
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient({"cache_store": cache})

    client.complete("who?")
    client.complete("who else?")

    assert len(stub.calls) == 2
    assert len(cache) == 2


def test_a_different_model_is_a_different_cache_entry(
    litellm_stub: InstallStub, cache: MemoryCache
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient({"cache_store": cache})

    client.complete("who?", {"llm.model": "gpt-4o"})
    client.complete("who?", {"llm.model": "gpt-4o-mini"})

    assert len(stub.calls) == 2


async def test_the_async_path_shares_the_same_cache(
    litellm_stub: InstallStub, cache: MemoryCache
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient({"cache_store": cache})

    client.complete("who?")
    await client.acomplete("who?")

    assert len(stub.calls) == 1


def test_no_cache_store_means_every_call_reaches_the_provider(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient()

    client.complete("who?")
    client.complete("who?")

    assert len(stub.calls) == 2


def test_the_protocol_only_cache_satisfies_the_declared_cache_store() -> None:
    assert isinstance(ProtocolOnlyCache(), CacheStore)


def test_a_store_built_to_the_declared_protocol_can_be_used(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient({"cache_store": ProtocolOnlyCache()})
    assert client.complete("who?") == "[]"


@pytest.mark.parametrize(
    "store",
    [
        {},
        GetOnlyCache(),
        NoGetCache(),
    ],
)
def test_invalid_cache_methods_fail_before_provider_contact(
    litellm_stub: InstallStub, store: object
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient({"cache_store": store})

    with pytest.raises(ConfigError, match="cache_store.*synchronous get.*set"):
        client.complete("who?")
    assert stub.calls == []


@pytest.mark.parametrize("asynchronous_method", ["get", "set"])
async def test_async_cache_methods_fail_before_provider_contact(
    litellm_stub: InstallStub, asynchronous_method: str
) -> None:
    class AsyncGet:
        async def get(self, key: str) -> None:
            return None

        def set(self, key: str, value: object) -> None:
            pass

    class AsyncSet:
        def get(self, key: str) -> None:
            return None

        async def set(self, key: str, value: object) -> None:
            pass

    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient(
        {"cache_store": AsyncGet() if asynchronous_method == "get" else AsyncSet()}
    )

    with pytest.raises(ConfigError, match="cache_store.*synchronous get.*set"):
        await client.acomplete("who?")
    assert stub.calls == []


def test_a_cache_that_cannot_store_does_not_destroy_a_paid_answer(
    litellm_stub: InstallStub,
) -> None:
    """By the time the write happens the provider has been contacted and billed."""

    class WriteOnlyFails:
        def get(self, key: str) -> None:
            return None

        def set(self, key: str, value: object) -> None:
            raise RuntimeError("backing store is full")

    litellm_stub(LitellmStub(chat_payload('[{"name": "Ada"}]')))
    client = DefaultLlmClient({"llm.model": "gpt-4o-mini", "cache_store": WriteOnlyFails()})
    assert client.complete("who?") == '[{"name": "Ada"}]'


def test_a_cache_hit_checks_the_deadline_on_both_entry_points() -> None:
    """Both entry points check the deadline before serving a cache hit.

    A hit consumes no attempt, tokens, or cost, but still obeys the time ceiling.
    """
    import asyncio

    props: dict[str, Any] = {"llm.model": "gpt-4o-mini", "llm.max_tokens": 64}
    store = MemoryCache()
    warm = DefaultLlmClient({**props, "cache_store": store})
    _, key = warm._cache_for(  # pyright: ignore[reportPrivateUsage]  # warming by hand
        {**props, "cache_store": store},
        warm._call_kwargs("who?", props),  # pyright: ignore[reportPrivateUsage]
    )
    assert key is not None
    store.set(key, {"choices": [{"message": {"content": "[]"}}], "model": "m"})

    spent = DefaultLlmClient(
        {**props, "cache_store": store, "budget": Budget(Limits(max_seconds=0))}
    )
    with pytest.raises(BudgetExceeded) as sync_stop:
        spent.complete("who?")
    assert sync_stop.value.limit == "max_seconds"

    with pytest.raises(BudgetExceeded) as async_stop:
        asyncio.run(spent.acomplete("who?"))
    assert async_stop.value.limit == "max_seconds"

    # An ample deadline still serves the hit from both, which is what the check must not cost.
    ample = DefaultLlmClient(
        {**props, "cache_store": store, "budget": Budget(Limits(max_seconds=60))}
    )
    assert ample.complete("who?") == "[]"
    assert asyncio.run(ample.acomplete("who?")) == "[]"
