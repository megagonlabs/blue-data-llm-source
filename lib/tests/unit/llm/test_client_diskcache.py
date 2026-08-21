"""The client's on-disk cache branch.

Every test here carries ``uses_local_store``: diskcache is backed by a local sqlite file,
which the offline guard blocks by default. Remote drivers stay blocked throughout.

This is the one part of the ported client whose tests do not carry over from the project
it was cloned from. That client drives its store with ``key in cache`` and ``cache[key]``;
this one uses ``get`` and ``set`` so a store written to the published ``CacheStore``
protocol works. Their cache tests therefore exercise a code path this package no longer
has, and this file covers the one it does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blue_data_llm_source.llm.client import DefaultLlmClient
from blue_data_llm_source.types import ConfigError
from tests.doubles import LitellmStub, chat_payload

# A priced model and an output cap, because a cost ceiling needs a bound it can claim.
BASE = {"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}


def _client(tmp_path: Path, **props: Any) -> tuple[DefaultLlmClient, dict[str, Any]]:
    merged = {**BASE, "llm.cache": True, "llm.cache_dir": str(tmp_path / "c"), **props}
    return DefaultLlmClient(merged), merged


@pytest.mark.uses_local_store
def test_a_second_identical_call_is_served_from_disk(tmp_path: Path, litellm_stub: Any) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client, _ = _client(tmp_path)

    first = client.complete("same prompt")
    second = client.complete("same prompt")

    assert first == second
    assert len(stub.calls) == 1, "the second call must not reach the provider"
    assert client.usages[0].cache_hit is False
    assert client.usages[1].cache_hit is True


@pytest.mark.uses_local_store
def test_a_cache_hit_costs_nothing_but_still_records_latency(
    tmp_path: Path, litellm_stub: Any
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]"), cost=0.5))
    client, _ = _client(tmp_path)

    client.complete("p")
    client.complete("p")

    hit = client.usages[1]
    assert hit.cost == 0.0
    assert hit.prompt_tokens == 0
    assert hit.completion_tokens == 0
    assert hit.latency_seconds >= 0.0


@pytest.mark.uses_local_store
def test_a_cache_hit_consumes_no_cost_budget(tmp_path: Path, litellm_stub: Any) -> None:
    """A limit must not be spent twice for one real call."""
    from blue_data_llm_source.llm.budget import Budget, Limits

    litellm_stub(LitellmStub(chat_payload("[]"), cost=0.4))
    merged = {**BASE, "llm.cache": True, "llm.cache_dir": str(tmp_path / "c")}
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient(merged, budget=budget)

    client.complete("p")
    spent_after_real_call = budget.cost
    client.complete("p")

    assert budget.cost == spent_after_real_call


@pytest.mark.uses_local_store
def test_a_different_prompt_is_a_different_entry(tmp_path: Path, litellm_stub: Any) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client, _ = _client(tmp_path)

    client.complete("one")
    client.complete("two")

    assert len(stub.calls) == 2


@pytest.mark.uses_local_store
def test_a_different_model_is_a_different_entry(tmp_path: Path, litellm_stub: Any) -> None:
    """The key covers the whole call, so changing the model cannot serve a stale answer."""
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client, merged = _client(tmp_path)

    client.complete("p")
    client.complete("p", {**merged, "llm.model": "other"})

    assert len(stub.calls) == 2


@pytest.mark.uses_local_store
def test_each_cache_dir_is_isolated(tmp_path: Path, litellm_stub: Any) -> None:
    """A per-call directory override gets its own store, not the first one opened."""
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client, merged = _client(tmp_path)

    client.complete("p")
    client.complete("p", {**merged, "llm.cache_dir": str(tmp_path / "other")})

    assert len(stub.calls) == 2


@pytest.mark.uses_local_store
def test_entries_survive_a_new_client_against_the_same_directory(
    tmp_path: Path, litellm_stub: Any
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    first, merged = _client(tmp_path)
    first.complete("p")

    second = DefaultLlmClient(merged)
    second.complete("p")

    assert len(stub.calls) == 1
    assert second.usages[0].cache_hit is True


@pytest.mark.uses_local_store
def test_caching_stays_off_unless_asked_for(tmp_path: Path, litellm_stub: Any) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient({**BASE, "llm.cache_dir": str(tmp_path / "c")})

    client.complete("p")
    client.complete("p")

    assert len(stub.calls) == 2, "cache_dir alone must not enable caching"


@pytest.mark.uses_local_store
def test_an_injected_store_is_preferred_over_the_disk_cache(
    tmp_path: Path, litellm_stub: Any
) -> None:
    """A caller-supplied store wins, and is driven through get/set only."""
    from blue_data_llm_source.cache import MemoryCache

    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    store = MemoryCache()
    client = DefaultLlmClient(
        {**BASE, "llm.cache": True, "llm.cache_dir": str(tmp_path / "c"), "cache_store": store}
    )

    client.complete("p")
    client.complete("p")

    assert len(stub.calls) == 1
    assert len(store) == 1
    assert not (tmp_path / "c").exists(), "the disk cache must not have been opened"


@pytest.mark.uses_local_store
def test_requesting_the_cache_without_diskcache_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, litellm_stub: Any
) -> None:
    """The optional extra is missing: say so, rather than silently not caching."""
    import builtins

    litellm_stub(LitellmStub(chat_payload("[]")))
    real_import = builtins.__import__

    def _no_diskcache(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "diskcache":
            raise ImportError("no diskcache")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_diskcache)
    client, _ = _client(tmp_path)

    with pytest.raises(ConfigError, match="diskcache"):
        client.complete("p")


@pytest.mark.uses_local_store
def test_open_cache_backends_are_bounded_and_closable(tmp_path: Path) -> None:
    """Every distinct cache_dir opened a backend and nothing ever closed one.

    A retained client rotating cache directories — one per tenant, per day, per run —
    accumulated SQLite descriptors until the process ran out of them, at which point
    unrelated `open()` calls elsewhere in the same program began failing too. What surfaced
    was `sqlite3.OperationalError`, from outside the documented error hierarchy.
    """
    client = DefaultLlmClient({**BASE, "llm.cache": True})
    for i in range(40):
        client._cache_for(  # pyright: ignore[reportPrivateUsage]  # the map under test
            {"llm.cache": True, "llm.cache_dir": str(tmp_path / f"c{i}")}, {}
        )
    open_backends = client._caches  # pyright: ignore[reportPrivateUsage]
    assert len(open_backends) <= 8, "the map is bounded, not unbounded"

    client.close()
    assert not client._caches  # pyright: ignore[reportPrivateUsage]

    # Still usable afterwards: a later call reopens what it needs.
    cache, key = client._cache_for(  # pyright: ignore[reportPrivateUsage]
        {"llm.cache": True, "llm.cache_dir": str(tmp_path / "again")}, {}
    )
    assert cache is not None and key is not None
    client.close()
