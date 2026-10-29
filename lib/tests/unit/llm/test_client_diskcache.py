"""The client's on-disk cache branch.

Every test here carries ``uses_local_store``: diskcache is backed by a local sqlite file,
which the offline guard blocks by default. Remote drivers stay blocked throughout.

The tests exercise the client's ``get`` and ``set`` calls on a disk-backed store.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, cast

import pytest

from blue_data_llm_source.llm.client import DefaultLlmClient
from blue_data_llm_source.types import ConfigError
from tests.doubles import LitellmStub, chat_payload

# A priced model and an output cap, because a cost ceiling needs a bound it can claim.
BASE = {"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}


@pytest.fixture(autouse=True)
def diskcache_for_store_tests(request: pytest.FixtureRequest) -> None:
    case = cast("pytest.Function", getattr(request, "node"))
    if case.name != "test_requesting_the_cache_without_diskcache_fails_loudly":
        pytest.importorskip("diskcache")


def _client(tmp_path: Path, **props: Any) -> tuple[DefaultLlmClient, dict[str, Any]]:
    merged = {**BASE, "llm.cache": True, "llm.cache_dir": str(tmp_path / "c"), **props}
    return DefaultLlmClient(merged), merged


@pytest.mark.uses_local_store
def test_default_disk_cache_directory_is_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, litellm_stub: Any
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]")))
    monkeypatch.chdir(tmp_path)
    client = DefaultLlmClient({**BASE, "llm.cache": True})

    client.complete("p")

    directory = tmp_path / ".llm_cache"
    assert stat.S_IMODE(directory.stat().st_mode) & 0o077 == 0
    assert (directory / "cache.db").exists()


@pytest.mark.uses_local_store
def test_permissive_disk_cache_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, litellm_stub: Any
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".llm_cache"
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)
    client = DefaultLlmClient({**BASE, "llm.cache": True})

    with pytest.raises(ConfigError, match="private directory"):
        client.complete("p")

    assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    assert not (directory / "cache.db").exists()
    assert stub.calls == []


@pytest.mark.uses_local_store
def test_expanded_disk_cache_directory_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, litellm_stub: Any
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)
    monkeypatch.setenv("LLM_TEST_CACHE_DIR", str(directory))
    client = DefaultLlmClient({**BASE, "llm.cache": True, "llm.cache_dir": "$LLM_TEST_CACHE_DIR"})

    with pytest.raises(ConfigError, match="private directory"):
        client.complete("p")

    assert not (directory / "cache.db").exists()
    assert not (tmp_path / "$LLM_TEST_CACHE_DIR").exists()
    assert stub.calls == []


@pytest.mark.uses_local_store
def test_private_expanded_disk_cache_directory_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, litellm_stub: Any
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    directory = tmp_path / "private"
    monkeypatch.setenv("LLM_TEST_CACHE_DIR", str(directory))
    client = DefaultLlmClient({**BASE, "llm.cache": True, "llm.cache_dir": "$LLM_TEST_CACHE_DIR"})

    client.complete("p")
    client.complete("p")

    assert (directory / "cache.db").exists()
    assert len(stub.calls) == 1


@pytest.mark.uses_local_store
def test_cache_directory_error_does_not_quote_expanded_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, litellm_stub: Any
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    blocker = tmp_path / "private_marker"
    blocker.write_text("file, not a directory")
    monkeypatch.setenv("LLM_TEST_CACHE_DIR", str(blocker / "cache"))
    client = DefaultLlmClient({**BASE, "llm.cache": True, "llm.cache_dir": "$LLM_TEST_CACHE_DIR"})

    with pytest.raises(ConfigError, match="llm.cache_dir cannot be opened") as caught:
        client.complete("p")

    assert "private_marker" not in str(caught.value)
    assert stub.calls == []


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
    """The client bounds open cache backends and closes them on shutdown."""
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
