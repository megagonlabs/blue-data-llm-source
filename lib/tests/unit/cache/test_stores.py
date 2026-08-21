"""The shipped cache stores, against the published protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from blue_data_llm_source.cache import FileCache, MemoryCache
from blue_data_llm_source.types import CacheStore


def _stores(tmp_path: Path) -> list[CacheStore]:
    return [MemoryCache(), FileCache(tmp_path / "cache")]


@pytest.fixture(params=["memory", "file"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> CacheStore:
    if request.param == "memory":
        return MemoryCache()
    return FileCache(tmp_path / "cache")


def test_both_stores_satisfy_the_published_protocol(tmp_path: Path) -> None:
    for candidate in _stores(tmp_path):
        assert isinstance(candidate, CacheStore)


def test_a_miss_is_none(store: CacheStore) -> None:
    assert store.get("absent") is None


def test_a_value_survives_a_round_trip(store: CacheStore) -> None:
    payload = {"choices": [{"message": {"content": "[]"}}]}
    store.set("k", payload)
    assert store.get("k") == payload


def test_setting_the_same_key_twice_keeps_the_second(store: CacheStore) -> None:
    store.set("k", {"n": 1})
    store.set("k", {"n": 2})
    assert store.get("k") == {"n": 2}


def test_keys_do_not_collide(store: CacheStore) -> None:
    store.set("a", {"n": 1})
    store.set("b", {"n": 2})
    assert store.get("a") == {"n": 1}
    assert store.get("b") == {"n": 2}


def test_a_long_unsafe_key_is_usable(store: CacheStore) -> None:
    key = json.dumps({"messages": [{"content": "a/b\\c" * 200}]})
    store.set(key, {"ok": True})
    assert store.get(key) == {"ok": True}


def test_memory_cache_evicts_the_least_recently_used() -> None:
    cache = MemoryCache(max_entries=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # 'a' is now the most recent, so 'b' is next out
    cache.set("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert len(cache) == 2


def test_memory_cache_rejects_a_useless_bound() -> None:
    with pytest.raises(ValueError):
        MemoryCache(max_entries=0)


def test_file_cache_persists_across_instances(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    FileCache(directory).set("k", {"n": 1})
    assert FileCache(directory).get("k") == {"n": 1}


def test_file_cache_treats_a_corrupt_entry_as_a_miss(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cache = FileCache(directory)
    cache.set("k", {"n": 1})
    entry = next(directory.glob("*.json"))
    entry.write_text("{ this is not json", encoding="utf-8")
    # A cache that can fail the request it was meant to make cheaper is worse than none.
    assert cache.get("k") is None


def test_file_cache_skips_an_unserialisable_value_rather_than_raising(tmp_path: Path) -> None:
    cache = FileCache(tmp_path / "cache")
    cache.set("k", object())
    assert cache.get("k") is None


def test_file_cache_leaves_no_temporary_files(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cache = FileCache(directory)
    cache.set("k", {"n": 1})
    cache.set("k2", object())
    assert list(directory.glob("*.tmp")) == []


def test_clear_empties_the_store(store: CacheStore) -> None:
    # clear() is on both shipped stores but deliberately not on CacheStore: the client
    # never calls it, and widening the published protocol to suit a test would oblige
    # every third-party store to implement it.
    assert isinstance(store, MemoryCache | FileCache)
    store.set("k", {"n": 1})
    store.clear()
    assert store.get("k") is None


def test_a_shipped_store_works_as_the_client_cache(tmp_path: Path) -> None:
    """The stores are usable where the client expects one, with get/set only."""
    from blue_data_llm_source.llm.client import DefaultLlmClient

    client = DefaultLlmClient()
    cache = MemoryCache()
    kwargs: dict[str, Any] = {"model": "m", "messages": []}
    # Resolution is what is under test, and the client exposes it only privately.
    resolved, key = client._cache_for({"cache_store": cache}, kwargs)  # pyright: ignore[reportPrivateUsage]
    assert resolved is cache
    assert key is not None
    store: CacheStore = cache
    store.set(key, {"choices": [{"message": {"content": "ok"}}]})
    assert store.get(key) is not None


def test_clear_leaves_json_this_cache_did_not_write(tmp_path: Path) -> None:
    """A caller may point this at a directory that already holds other JSON.

    `clear` deleted every `*.json` in it — a config file, another component's state — which
    is a data loss the caller cannot recover and did not ask for.
    """
    bystander = tmp_path / "settings.json"
    bystander.write_text('{"keep": true}', encoding="utf-8")
    also = tmp_path / "0123456789abcdef.json"  # json, hex-ish, still not ours
    also.write_text("{}", encoding="utf-8")

    cache = FileCache(tmp_path)
    cache.set("a key", {"cached": 1})
    assert cache.get("a key") == {"cached": 1}

    cache.clear()

    assert cache.get("a key") is None, "the cache did not clear its own entry"
    assert bystander.read_text(encoding="utf-8") == '{"keep": true}'
    assert also.exists()
