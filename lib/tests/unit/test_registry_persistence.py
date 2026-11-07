"""Registry persistence keeps settings and omits process-local cache handles."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from blue_data.registry.data_registry import DataRegistry  # pyright: ignore[reportMissingTypeStubs]
from blue_data.source.json.file_json_source import (
    FileJSONSource,  # pyright: ignore[reportMissingTypeStubs]
)
from blue_data.source.json.memory_json_source import (
    MemoryJSONSource,  # pyright: ignore[reportMissingTypeStubs]
)
from blue_data.source.source import DataSource  # pyright: ignore[reportMissingTypeStubs]

from blue_data_llm_source import BudgetExceeded, LlmSource, MemoryCache
from tests.doubles import LitellmStub, chat_payload

SETTINGS: dict[str, Any] = {
    "llm.model": "gpt-4o-mini",
    "llm.max_tokens": 256,
    "llm.price_per_input_token": 1e-6,
    "llm.price_per_output_token": 2e-6,
    "limits.max_cost": 0.02,
    "llm.cache": True,
}


def registry_store(kind: str, tmp_path: Path) -> Any:
    if kind == "memory":
        return MemoryJSONSource(properties={})
    return FileJSONSource(properties={"connection": {"path": str(tmp_path / "registry.json")}})


@pytest.mark.parametrize("kind", ["memory", "file"])
def test_registered_cache_stays_live_and_rebuilt_settings_are_durable(
    kind: str,
    tmp_path: Path,
    litellm_stub: Callable[[LitellmStub], LitellmStub],
) -> None:
    cache = MemoryCache()
    cache.set("sentinel", {"retained": True})
    settings = {**SETTINGS, "llm.cache_dir": str(tmp_path / "cache"), "cache_store": cache}
    source = LlmSource("cached", properties=settings)
    source_map = source.properties
    settings_before = dict(settings)
    store = registry_store(kind, tmp_path)
    registry = cast("Any", DataRegistry(store=store))
    registry.register_source(source)
    stored = registry.get_source_record("cached")["properties"]
    assert "cache_store" not in stored
    for key, value in settings.items():
        if key != "cache_store":
            assert stored[key] == value
    inherited = cast("frozenset[str]", getattr(DataSource, "_transient_properties"))
    assert inherited <= LlmSource._transient_properties  # pyright: ignore[reportPrivateUsage]
    assert "cache_store" in LlmSource._transient_properties  # pyright: ignore[reportPrivateUsage]
    assert registry.connect_source("cached") is source
    assert source.properties["cache_store"] is cache
    assert source.properties is source_map
    assert settings == settings_before and settings["cache_store"] is cache
    assert cache.get("sentinel") == {"retained": True}

    stub = litellm_stub(LitellmStub(chat_payload('[{"name":"Ada"}]'), cost=0.001))
    assert source.fetch("people").meta.calls == 1
    live_hit = registry.connect_source("cached").fetch("people")
    assert live_hit.records == [{"name": "Ada"}]
    assert live_hit.meta.calls == 0 and live_hit.meta.cache_hits == 1
    assert len(stub.calls) == 1

    new_store = registry_store(kind, tmp_path) if kind == "file" else store
    fresh = cast("Any", DataRegistry(store=new_store))
    rebuilt = fresh.connect_source("cached")
    assert type(rebuilt) is LlmSource
    assert rebuilt is not source
    assert "cache_store" not in rebuilt.properties
    assert rebuilt.estimate("people").max_output_tokens == 256
    for key, value in settings.items():
        if key != "cache_store":
            assert rebuilt.properties[key] == value
    reattached = MemoryCache()
    call_settings = {"cache_store": reattached}
    assert rebuilt.fetch("reattached", properties=call_settings).meta.calls == 1
    hit = rebuilt.fetch("reattached", properties=call_settings)
    assert call_settings == {"cache_store": reattached}
    assert hit.meta.cache_hits == 1 and hit.meta.calls == 0
    assert len(stub.calls) == 2
    assert "cache_store" not in rebuilt.properties
    if kind == "file":
        payload = json.loads((tmp_path / "registry.json").read_text())
        assert "cache_store" not in json.dumps(payload)


@pytest.mark.parametrize("kind", ["memory", "file"])
@pytest.mark.parametrize("limit", ["max_cost", "max_calls", "max_seconds"])
@pytest.mark.parametrize("encoded", [False, True])
def test_reconstructed_registry_refusal_is_typed_without_provider_contact(
    kind: str,
    limit: str,
    encoded: bool,
    tmp_path: Path,
    litellm_stub: Callable[[LitellmStub], LitellmStub],
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    store = registry_store(kind, tmp_path)
    registry = cast("Any", DataRegistry(store=store))
    source = LlmSource("bounded", properties={**SETTINGS, "llm.cache": False, f"limits.{limit}": 0})
    registry.register_source(source)
    fresh = cast(
        "Any",
        DataRegistry(
            store=registry_store(kind, tmp_path) if kind == "file" else store,
        ),
    )
    rebuilt = fresh.connect_source("bounded")
    assert type(rebuilt) is LlmSource
    assert rebuilt.properties[f"limits.{limit}"] == 0
    query: Any = {"op": "LLM.FETCH", "query": "people"}
    if encoded:
        query = json.dumps(query)
    for target in (registry, fresh):
        with pytest.raises(BudgetExceeded) as caught:
            target.execute_query("bounded", query)
        assert caught.value.limit == limit
    with pytest.raises(BudgetExceeded) as caught:
        rebuilt.execute_query_nl("people")
    assert caught.value.limit == limit
    assert stub.calls == []


@pytest.mark.parametrize("kind", ["memory", "file"])
@pytest.mark.parametrize("encoded", [False, True])
def test_rebuilt_registry_reports_cost_refusal_after_planning(
    kind: str,
    encoded: bool,
    tmp_path: Path,
    litellm_stub: Callable[[LitellmStub], LitellmStub],
) -> None:
    request = json.dumps(
        {
            "ask": "people",
            "constraints": {},
            "row_estimate": 1,
            "schema": {"attrs": [{"name": "name"}], "key": "name"},
        }
    )
    stub = litellm_stub(LitellmStub(chat_payload(request), cost=0.02))
    settings = {
        **SETTINGS,
        "plan.mode": "partitioned",
        "plan.key": "name",
        "plan.planning_order": "planning.combined",
        "llm.cache": False,
        "limits.max_retries": 0,
        "plan.max_depth": 0,
    }
    source = LlmSource("planning", properties=settings)
    store = registry_store(kind, tmp_path)
    registry = cast("Any", DataRegistry(store=store))
    registry.register_source(source)
    fresh = cast(
        "Any", DataRegistry(store=registry_store(kind, tmp_path) if kind == "file" else store)
    )
    rebuilt = fresh.connect_source("planning")
    assert type(rebuilt) is LlmSource and rebuilt is not source
    direct = rebuilt.fetch("people", attrs=["name"])
    assert direct.records == [] and direct.meta.calls == 1
    assert direct.meta.cost == 0.02 and direct.meta.stopped_by == "max_cost"
    query: Any = {"op": "LLM.FETCH", "query": "people", "attrs": ["name"]}
    if encoded:
        query = json.dumps(query)
    for target in (registry, fresh):
        with pytest.raises(BudgetExceeded) as caught:
            target.execute_query("planning", query)
        assert caught.value.limit == "max_cost"
        assert "prevented an extraction answer" in caught.value.message
    assert len(stub.calls) == 3
    assert all(
        "Prepare a structured data request" in call["messages"][-1]["content"]
        for call in stub.calls
    )
