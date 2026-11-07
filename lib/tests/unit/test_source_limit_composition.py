"""Ceilings compose before every query route reaches the async driver."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest
from blue_data.registry.data_registry import DataRegistry  # pyright: ignore[reportMissingTypeStubs]
from blue_data.source.json.memory_json_source import (
    MemoryJSONSource,  # pyright: ignore[reportMissingTypeStubs]
)

from blue_data_llm_source import Budget, BudgetExceeded, ConfigError, Limits, LlmSource, QueryError
from tests.doubles import ScriptedClient

ROWS = '[{"item":"alpha"},{"item":"beta"},{"item":"gamma"}]'
SURFACES = ["fetch", "afetch", "native", "nl", "dict", "tool", "registry", "registry_nl"]


def invoke(source: LlmSource, route: str, options: dict[str, Any]) -> list[dict[str, Any]]:
    opts = dict(options)
    cap = opts.pop("max_records", None)
    if route == "fetch":
        return source.fetch("items", max_records=cap, properties=opts).records
    if route == "afetch":
        return asyncio.run(source.afetch("items", max_records=cap, properties=opts)).records
    if cap is not None:
        opts["max_records"] = cap
    if route == "native":
        return source.execute_query("items", optional_properties=opts)
    if route == "nl":
        return source.execute_query_nl("items", optional_properties=opts)
    if route == "dict":
        return source.execute_query({"op": "LLM.FETCH", "query": "items"}, optional_properties=opts)
    if route == "tool":
        return source.execute_nl_tool("execute_query", {"query": "items"}, optional_properties=opts)
    registry: Any = DataRegistry(store=MemoryJSONSource())
    registry.register_source(source)
    method = registry.execute_query_nl if route == "registry_nl" else registry.execute_query
    return method(source=source.name, query="items", optional_properties=opts)


@pytest.mark.parametrize("route", SURFACES)
@pytest.mark.parametrize("axis", ["max_cost", "max_calls", "max_seconds", "max_rows"])
@pytest.mark.parametrize("call", [{}, {"value": None}, {"value": 5}, {"value": 0}])
def test_configured_zero_survives_call_overrides(
    route: str, axis: str, call: dict[str, Any]
) -> None:
    client = ScriptedClient([ROWS], cost=0.1)
    source = LlmSource(properties={f"limits.{axis}": 0}, client=client)
    options = {f"limits.{axis}": call["value"]} if call else {}
    before = deepcopy((source.properties, options))
    if route not in ("fetch", "afetch") and axis != "max_rows":
        with pytest.raises(BudgetExceeded) as caught:
            invoke(source, route, options)
        assert caught.value.limit == axis
    else:
        assert invoke(source, route, options) == []
    assert (source.properties, options) == before
    assert len(client.prompts) == (1 if axis == "max_rows" else 0)


@pytest.mark.parametrize("route", SURFACES)
@pytest.mark.parametrize("cap", [0, 1, 2])
def test_max_records_cannot_replace_a_row_ceiling(route: str, cap: int) -> None:
    source = LlmSource(properties={"limits.max_rows": cap}, client=ScriptedClient([ROWS]))
    assert len(invoke(source, route, {"max_records": 3})) == cap


@pytest.mark.parametrize("route", SURFACES)
@pytest.mark.parametrize(
    "axis,bad",
    [
        ("max_cost", -1),
        ("max_cost", True),
        ("max_cost", "1"),
        ("max_cost", float("nan")),
        ("max_seconds", float("inf")),
        ("max_seconds", 10**400),
        ("max_calls", True),
        ("max_calls", 1.5),
        ("max_rows", -1),
        ("max_rows", "1"),
        ("max_retries", None),
        ("max_retries", 10000),
        ("max_retries", False),
    ],
)
@pytest.mark.parametrize("invalid_layer", ["source", "call", "client"])
def test_invalid_declarations_cannot_be_hidden(
    route: str, axis: str, bad: Any, invalid_layer: str
) -> None:
    key = f"limits.{axis}"
    client = ScriptedClient([ROWS])
    source_props = {key: bad if invalid_layer == "source" else 0}
    call_props = {key: bad if invalid_layer == "call" else 0}
    if invalid_layer == "client":
        client.properties = {key: bad}  # type: ignore[attr-defined]
    source = LlmSource(properties=source_props, client=client)
    with pytest.raises(ConfigError, match=key):
        invoke(source, route, call_props)
    assert not client.prompts


@pytest.mark.parametrize("route", SURFACES)
@pytest.mark.parametrize("cap", [0, -1, True, 1.5, "2", 10**400])
def test_max_records_remains_a_positive_integer(route: str, cap: Any) -> None:
    source = LlmSource(properties={"limits.max_rows": 1}, client=ScriptedClient([ROWS]))
    if type(cap) is int and cap > 0:
        assert len(invoke(source, route, {"max_records": cap})) == 1
    else:
        with pytest.raises(QueryError, match="max_records"):
            invoke(source, route, {"max_records": cap})


@pytest.mark.parametrize("axis", ["max_cost", "max_seconds", "max_rows", "max_calls"])
@pytest.mark.parametrize("call", [None, 0, 1, 2, 3])
def test_each_axis_uses_the_smaller_non_none_value(axis: str, call: int | None) -> None:
    source = LlmSource(properties={f"limits.{axis}": 2})
    assert getattr(source._limits({f"limits.{axis}": call}), axis) == (  # pyright: ignore[reportPrivateUsage]
        2 if call is None else min(2, call)
    )


def test_client_source_precedence_retry_default_and_nonmutation() -> None:
    client = ScriptedClient([ROWS, ROWS])
    client.properties = {"limits.max_rows": 1, "limits.max_retries": 4}  # type: ignore[attr-defined]
    source = LlmSource(properties={"limits.max_rows": 3, "limits.max_retries": 3}, client=client)
    before = deepcopy(source.properties)
    assert source._limits({"limits.max_retries": 9}).max_retries == 3  # pyright: ignore[reportPrivateUsage]
    assert LlmSource()._limits({"limits.max_retries": 9}).max_retries == 9  # pyright: ignore[reportPrivateUsage]
    assert len(source.fetch("items", properties={"limits.max_rows": 1}).records) == 1
    assert len(source.fetch("items").records) == 3
    assert source.properties == before


def test_parent_budget_and_request_field_precedence() -> None:
    source = LlmSource(properties={"limits.max_rows": 2}, client=ScriptedClient([ROWS, ROWS]))
    assert (
        len(
            source.execute_query(
                {"op": "LLM.FETCH", "query": "items", "max_records": 1},
                optional_properties={"max_records": 3},
            )
        )
        == 2
    )
    parent = Budget(Limits(max_calls=1))
    assert len(source.fetch("items", budget=parent).records) == 2
    assert source.fetch("items", budget=parent).records == []
    assert parent.calls == 1


@pytest.mark.parametrize("query", ["", "items"])
def test_empty_queries_do_not_hide_invalid_limits(query: str) -> None:
    source = LlmSource(properties={"limits.max_cost": float("nan")})
    with pytest.raises(ConfigError, match="limits.max_cost"):
        source.fetch(query, properties={"limits.max_cost": 0})
    with pytest.raises(ConfigError, match="limits.max_cost"):
        source.estimate(query, properties={"limits.max_cost": 0})


@pytest.mark.parametrize("route", SURFACES)
@pytest.mark.parametrize("configured,call,attempts", [(0, 5, 1), (1, 5, 2), (None, 3, 4)])
def test_retry_caps_bind_real_transient_failures(
    monkeypatch: pytest.MonkeyPatch, route: str, configured: int | None, call: int, attempts: int
) -> None:
    from blue_data_llm_source import LlmSourceError
    from blue_data_llm_source.llm.client import DefaultLlmClient
    from tests.doubles import LitellmStub, chat_payload, install_litellm

    client = DefaultLlmClient()

    def no_wait(*_args: Any) -> float:
        return 0.0

    monkeypatch.setattr("blue_data_llm_source.llm.client._WaitWithin.__call__", no_wait)
    props = {} if configured is None else {"limits.max_retries": configured}
    source = LlmSource(properties=props, client=client)
    stub = install_litellm(
        monkeypatch,
        LitellmStub(
            chat_payload(ROWS),
            fail_times=3,
        ),
    )
    if attempts < 4:
        with pytest.raises(LlmSourceError):
            invoke(source, route, {"limits.max_retries": call})
    else:
        assert len(invoke(source, route, {"limits.max_retries": call})) == 3
    assert len(stub.calls) == attempts


@pytest.mark.parametrize("mode", ["partitioned", "row_by_row"])
def test_non_flat_calls_keep_the_source_call_ceiling(mode: str) -> None:
    client = ScriptedClient([ROWS])
    source = LlmSource(properties={"limits.max_calls": 0}, client=client)
    result = source.fetch(
        "items",
        attrs=["item"],
        properties={
            "plan.mode": mode,
            "plan.key": "item",
            "limits.max_calls": 5,
        },
    )
    assert result.records == []
    assert result.meta.stopped_by == "max_calls"
    assert not client.prompts


@pytest.mark.parametrize("route", SURFACES)
def test_unknown_properties_still_fail_on_every_route(route: str) -> None:
    client = ScriptedClient([ROWS])
    with pytest.raises(ConfigError, match="unknown property"):
        invoke(LlmSource(client=client), route, {"commmit": False})
    assert not client.prompts


# Rates far below any real model's, and source-owned rates far above a small listed model's.
TINY_RATES = {"llm.price_per_input_token": 1e-15, "llm.price_per_output_token": 1e-15}
OWNED_RATES = {"llm.price_per_input_token": 1e-5, "llm.price_per_output_token": 1e-5}
OWNED_MODEL = {"llm.model": "acme-mini", **OWNED_RATES}
CEILING = {"limits.max_cost": 0.05}
PER_CALL_RATES = "cannot be set per call when limits.max_cost is configured"
UNPRICED_CALL_MODEL = "no published prices for model 'acme-large', so the limits.max_cost"


@pytest.mark.parametrize("route", SURFACES)
@pytest.mark.parametrize(
    "client_props,source_props,call,refusal",
    [
        # An unlisted model bounded at rates the caller supplied.
        (None, CEILING, {"llm.model": "acme-large", **TINY_RATES}, PER_CALL_RATES),
        # The source's rates for its own unlisted model, carried to another unlisted model.
        (None, {**CEILING, **OWNED_MODEL}, {"llm.model": "acme-large"}, UNPRICED_CALL_MODEL),
        # A listed model with supplied rates, and one rate cleared per call.
        (None, CEILING, {"llm.model": "gpt-6-astra", **TINY_RATES}, PER_CALL_RATES),
        (None, {**CEILING, **OWNED_MODEL}, {"llm.price_per_input_token": None}, PER_CALL_RATES),
        # An injected client's rates for its own model, or its own ceiling.
        (OWNED_MODEL, CEILING, {"llm.model": "acme-large"}, UNPRICED_CALL_MODEL),
        ({**CEILING, **OWNED_MODEL}, None, {"llm.model": "acme-large"}, UNPRICED_CALL_MODEL),
        (CEILING, None, TINY_RATES, PER_CALL_RATES),
    ],
)
def test_a_configured_cost_ceiling_is_not_bounded_against_call_rates(
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    client_props: dict[str, Any] | None,
    source_props: dict[str, Any] | None,
    call: dict[str, Any],
    refusal: str,
) -> None:
    from blue_data_llm_source.llm.client import DefaultLlmClient
    from tests.doubles import LitellmStub, chat_payload, install_litellm

    stub = install_litellm(monkeypatch, LitellmStub(chat_payload(ROWS)))
    client = DefaultLlmClient(properties=client_props) if client_props is not None else None
    source = LlmSource(properties=source_props, client=client)
    with pytest.raises(ConfigError, match=refusal) as refused:
        invoke(source, route, {**call, "llm.max_tokens": 100_000})
    assert "per call" not in str(refused.value).split(". ", 1)[1]
    assert stub.calls == []


@pytest.mark.parametrize("route", SURFACES)
@pytest.mark.parametrize(
    "source_props,call",
    [
        # The source's own model and rates, with a tighter allowance.
        ({**CEILING, **OWNED_MODEL}, {}),
        ({**CEILING, **OWNED_MODEL}, {"llm.model": "acme-mini"}),
        # No configured ceiling: the caller's own ceiling over its own rates.
        ({}, {"llm.model": "acme-large", "limits.max_cost": 1.0, **TINY_RATES}),
        (OWNED_MODEL, {"llm.model": "acme-large", "limits.max_cost": 1.0}),
        # A listed model is bounded at its published prices, not the source's rates.
        ({**CEILING, **OWNED_MODEL}, {"llm.model": "gpt-4.1-nano"}),
    ],
)
def test_calls_the_configured_ceiling_can_bound_still_run(
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    source_props: dict[str, Any],
    call: dict[str, Any],
) -> None:
    from tests.doubles import LitellmStub, chat_payload, install_litellm

    stub = install_litellm(monkeypatch, LitellmStub(chat_payload(ROWS)))
    source = LlmSource(properties=source_props)
    assert len(invoke(source, route, {**call, "llm.max_tokens": 100})) == 3
    assert len(stub.calls) == 1


def test_estimates_follow_the_same_rates() -> None:
    owned = LlmSource(properties={**CEILING, **OWNED_MODEL})
    plain = LlmSource(properties=CEILING)
    listed = {"llm.model": "gpt-4.1-nano", "llm.max_tokens": 100}
    assert owned.estimate("items", properties=listed) == plain.estimate("items", properties=listed)
    with pytest.raises(ConfigError, match=PER_CALL_RATES):
        owned.estimate("items", properties=TINY_RATES)
    unpriced = owned.estimate("items", properties={"llm.model": "acme-large"})
    assert not unpriced.bounded
    assert unpriced.reason and "no published prices for model 'acme-large'" in unpriced.reason
