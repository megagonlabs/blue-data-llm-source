"""The model table: the request each model is sent, what it costs, and when a charge is known.

Request shape is read from ``_call_kwargs``, as in the config tests, because the question is
what the provider would have been handed. Charging is read from a call against the stub.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

import blue_data_llm_source as pkg
from blue_data_llm_source.llm import models, pricing
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.llm.client import (
    DefaultLlmClient,
    check_adapter_keys,
    default_max_tokens_for,
    default_reasoning_effort_for,
)
from blue_data_llm_source.types import ConfigError
from tests.doubles import LitellmStub, MemoryCache, chat_payload, stream_chunks

InstallStub = Callable[[LitellmStub], LitellmStub]

M = 1_000_000
UPLIFT = 1.10
USAGE = {"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42}


def _kwargs(**properties: Any) -> dict[str, Any]:
    return DefaultLlmClient()._call_kwargs("who?", {f"llm.{k}": v for k, v in properties.items()})


def _complete(client: DefaultLlmClient) -> str:
    return client.complete("who?")


def _acomplete(client: DefaultLlmClient) -> str:
    return asyncio.run(client.acomplete("who?"))


# ------------------------------------------------------------------ requests --
@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            "gpt-4o",
            {"temperature": 0, "max_tokens": 512, "custom_llm_provider": "openai"},
        ),
        (
            "gpt-4o-mini-2024-07-18",
            {"temperature": 0, "max_tokens": 512, "custom_llm_provider": "openai"},
        ),
        (
            "gpt-4.1",
            {"temperature": 0, "max_tokens": 512, "custom_llm_provider": "openai"},
        ),
        (
            "gpt-5",
            {
                "reasoning_effort": "medium",
                "max_completion_tokens": 512,
                "custom_llm_provider": "openai",
            },
        ),
        (
            "gpt-5.4",
            {
                "reasoning_effort": "none",
                "max_completion_tokens": 512,
                "custom_llm_provider": "openai",
            },
        ),
        (
            "gpt-5.4-mini-2026-03-17",
            {
                "reasoning_effort": "none",
                "max_completion_tokens": 512,
                "custom_llm_provider": "openai",
            },
        ),
        (
            "gpt-5.6",
            {
                "reasoning_effort": "medium",
                "max_completion_tokens": 512,
                "custom_llm_provider": "openai",
            },
        ),
        (
            "gpt-6-sol",
            {
                "reasoning_effort": "medium",
                "max_completion_tokens": 512,
                "custom_llm_provider": "openai",
            },
        ),
        (
            "gpt-6-astra",
            {
                "reasoning_effort": "medium",
                "max_completion_tokens": 512,
                "custom_llm_provider": "openai",
            },
        ),
        (
            "gpt-6-luna-2026-09-22",
            {
                "reasoning_effort": "medium",
                "max_completion_tokens": 512,
                "custom_llm_provider": "openai",
            },
        ),
        (
            "o3",
            {
                "reasoning_effort": "medium",
                "max_completion_tokens": 512,
                "custom_llm_provider": "openai",
            },
        ),
        # Not in the table, but in OpenAI's naming: routed to OpenAI, sent nothing else.
        ("gpt-7-foo", {"max_tokens": 512, "custom_llm_provider": "openai"}),
        ("o9-preview", {"max_tokens": 512, "custom_llm_provider": "openai"}),
        # A price-only row keeps no contract and no provider.
        ("mistral-large", {"max_tokens": 512}),
        ("gpt-4", {"max_tokens": 512}),
        # Another family's bare name is left to the provider library.
        ("llama-9", {"max_tokens": 512}),
        # A provider prefix is the caller's routing. `openai/` still reads the contract.
        (
            "openai/gpt-6-sol",
            {"reasoning_effort": "medium", "max_completion_tokens": 512},
        ),
        ("azure/gpt-6-sol", {"max_tokens": 512}),
    ],
)
def test_each_family_is_sent_its_own_contract(model: str, expected: dict[str, Any]) -> None:
    kwargs = _kwargs(model=model, max_tokens=512)
    assert kwargs.pop("messages")
    assert kwargs.pop("model") == model
    assert kwargs == expected


@pytest.mark.parametrize("model", ["gpt-7-foo", "llama-9", "azure/gpt-6-sol"])
def test_an_unknown_name_passes_the_callers_own_values_through(model: str) -> None:
    kwargs = _kwargs(model=model, max_tokens=64, temperature=0.3, reasoning_effort="none")
    assert kwargs["temperature"] == 0.3
    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["max_tokens"] == 64


@pytest.mark.parametrize(
    ("model", "allowance", "effort"),
    [
        ("gpt-4o", 4_096, None),
        ("gpt-4o-2024-05-13", 4_096, None),
        ("gpt-4.1-nano", 4_096, None),
        ("gpt-6-luna", 25_000, "medium"),
        ("openai/gpt-6-sol", 25_000, "medium"),
        ("gpt-6-astra", 25_000, "medium"),
        ("gpt-5.4-nano", 25_000, "none"),
        ("gpt-5-mini", 25_000, "medium"),
        ("o4-mini", 25_000, "medium"),
        ("gpt-7-foo", 4_096, None),
        ("gpt-4-turbo", 4_096, None),
    ],
)
def test_defaults_come_from_the_table(model: str, allowance: int, effort: str | None) -> None:
    assert default_max_tokens_for(model) == allowance
    assert default_reasoning_effort_for(model) == effort


def test_temperature_is_allowed_at_effort_none_where_the_contract_says_so() -> None:
    kwargs = _kwargs(model="gpt-6-sol", reasoning_effort="none", temperature=0.2)
    assert kwargs["temperature"] == 0.2
    # GPT-5.4 defaults to effort none, so a temperature needs no effort alongside it.
    assert _kwargs(model="gpt-5.4", temperature=0.2)["temperature"] == 0.2


def test_minimal_effort_is_accepted_where_the_model_offers_it() -> None:
    assert _kwargs(model="gpt-5", reasoning_effort="minimal")["reasoning_effort"] == "minimal"
    assert "minimal" in pkg.REASONING_EFFORTS


@pytest.mark.parametrize(
    ("properties", "message"),
    [
        ({"model": "gpt-6-sol", "temperature": 0.2}, "temperature.*reasoning_effort"),
        ({"model": "gpt-5.6", "temperature": 0.2}, "temperature.*reasoning_effort"),
        (
            {"model": "gpt-6-sol", "reasoning_effort": "high", "temperature": 0.2},
            "temperature.*reasoning_effort",
        ),
        ({"model": "gpt-6-astra", "temperature": 0.2}, "gpt-6-astra.*temperature"),
        ({"model": "gpt-6-astra", "reasoning_effort": "none"}, "reasoning effort 'none'"),
        ({"model": "gpt-5", "temperature": 0.0}, "gpt-5.*temperature"),
        ({"model": "gpt-5", "reasoning_effort": "none"}, "reasoning effort 'none'"),
        ({"model": "gpt-5.5", "reasoning_effort": "max"}, "reasoning effort 'max'"),
        ({"model": "o3", "reasoning_effort": "xhigh"}, "reasoning effort 'xhigh'"),
        ({"model": "o1", "temperature": 1.0}, "o1.*temperature"),
        ({"model": "gpt-4.1", "reasoning_effort": "low"}, "gpt-4.1.*reasoning_effort"),
        ({"model": "openai/gpt-6-astra", "temperature": 0.2}, "temperature"),
    ],
)
def test_a_request_the_contract_rules_out_is_refused_by_name(
    properties: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        _kwargs(**properties)


@pytest.mark.parametrize(
    "properties",
    [
        {"llm.model": "gpt-6-astra", "llm.temperature": 0.2},
        {"llm.model": "gpt-5", "llm.reasoning_effort": "xhigh"},
        {"llm.model": "gpt-6-sol", "llm.temperature": 0.2},
    ],
)
def test_the_source_boundary_applies_the_same_contract(properties: dict[str, Any]) -> None:
    """An injected client never reaches `_call_kwargs`, so the key check holds the rule too."""
    with pytest.raises(ConfigError):
        check_adapter_keys(properties)


@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
@pytest.mark.parametrize(
    "properties",
    [
        {"llm.model": "gpt-6-sol", "llm.temperature": 0.2},
        {"llm.model": "gpt-6-astra", "llm.reasoning_effort": "none"},
        {"llm.model": "o3", "llm.temperature": 0.0},
    ],
)
def test_a_contract_refusal_costs_nothing(
    litellm_stub: InstallStub,
    invoke: Callable[[DefaultLlmClient], str],
    properties: dict[str, Any],
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]", usage=USAGE), cost=0.01))
    budget = Budget(Limits(max_cost=1.0, max_calls=5))
    client = DefaultLlmClient({**properties, "llm.max_tokens": 512}, budget=budget)

    with pytest.raises(ConfigError):
        invoke(client)

    assert stub.calls == []
    assert budget.calls == 0
    assert budget.cost == 0
    assert client.usages == []


# ------------------------------------------------------------------- routing --
@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
def test_an_unroutable_model_is_refused_before_anything_is_claimed(
    litellm_stub: InstallStub, invoke: Callable[[DefaultLlmClient], str]
) -> None:
    stub = LitellmStub(chat_payload("[]", usage=USAGE), cost=0.01)
    stub.unroutable = {"mistral-large"}
    litellm_stub(stub)
    budget = Budget(Limits(max_cost=1.0, max_calls=1))
    client = DefaultLlmClient(
        {
            "llm.model": "mistral-large",
            "llm.max_tokens": 512,
            "llm.price_per_input_token": 1e-6,
            "llm.price_per_output_token": 1e-6,
        },
        budget,
    )

    with pytest.raises(ConfigError, match="'mistral-large'.*'provider/model'") as caught:
        invoke(client)
    # Another family's name is never pointed at OpenAI.
    assert "openai/" not in str(caught.value)

    assert stub.calls == []
    assert budget.calls == 0
    assert budget.cost == 0
    assert client.usages == []
    # The allowance it did not spend is still there for the next call.
    stub.unroutable.clear()
    invoke(client)
    assert budget.calls == 1


def test_a_pinned_name_routes_where_the_bare_name_alone_would_not(
    litellm_stub: InstallStub,
) -> None:
    stub = LitellmStub(chat_payload("[]"))
    stub.unroutable = {"gpt-6-sol", "gpt-7-foo"}
    litellm_stub(stub)

    for model in ("gpt-6-sol", "gpt-7-foo"):
        DefaultLlmClient({"llm.model": model, "llm.max_tokens": 512}).complete("who?")

    assert [call["custom_llm_provider"] for call in stub.calls] == ["openai", "openai"]
    assert stub.resolved == [("gpt-6-sol", "openai"), ("gpt-7-foo", "openai")]


def test_only_the_missing_provider_failure_is_converted(litellm_stub: InstallStub) -> None:
    """Any other resolution failure is left to the attempt, which runs and reports what it meets."""
    stub = LitellmStub(chat_payload("[]"))

    def broken(model: str, custom_llm_provider: str | None = None, **_: Any) -> Any:
        raise ValueError("unrelated resolution failure")

    stub.get_llm_provider = broken  # type: ignore[method-assign]
    litellm_stub(stub)

    DefaultLlmClient({"llm.model": "gpt-4o", "llm.max_tokens": 512}).complete("who?")
    assert len(stub.calls) == 1


@pytest.mark.parametrize("model", ["gpt-6-sol", "gpt-5", "o3", "openai/gpt-5.4"])
def test_every_reasoning_entry_checks_the_installed_litellm(
    litellm_stub: InstallStub, model: str
) -> None:
    stub = LitellmStub(chat_payload("[]"))
    stub.get_supported_openai_params = lambda **_kwargs: ["max_completion_tokens"]  # type: ignore[method-assign]
    litellm_stub(stub)
    with pytest.raises(ConfigError, match="reasoning_effort"):
        DefaultLlmClient({"llm.model": model, "llm.max_tokens": 512}).complete("who?")
    assert stub.calls == []


# ------------------------------------------------------------------- prices --
@pytest.mark.parametrize(
    ("model", "rates"),
    [
        ("gpt-6-astra", (10.00, 50.00)),
        ("gpt-6-sol", (2.00, 10.00)),
        ("openai/gpt-6-sol", (2.00, 10.00)),
        ("gpt-6-luna", (0.10, 0.50)),
        ("gpt-5.6", (4.00, 20.00)),
        ("gpt-5.6-sol", (4.00, 20.00)),
        ("gpt-5.6-terra", (2.00, 12.00)),
        ("gpt-5.6-luna", (0.20, 1.20)),
        ("gpt-5.5", (5.00, 30.00)),
        ("gpt-5.5-2026-04-23", (5.00, 30.00)),
        ("gpt-5.4", (2.50, 15.00)),
        ("gpt-5.4-mini", (0.75, 4.50)),
        ("gpt-5.4-nano", (0.20, 1.25)),
        ("gpt-5", (1.25, 10.00)),
        ("gpt-5-mini", (0.25, 2.00)),
        ("gpt-5-nano", (0.05, 0.40)),
        ("gpt-4.1", (2.00, 8.00)),
        ("gpt-4.1-mini", (0.40, 1.60)),
        ("gpt-4.1-nano", (0.10, 0.40)),
        ("gpt-4o", (2.50, 10.00)),
        ("gpt-4o-2024-08-06", (2.50, 10.00)),
        ("gpt-4o-2024-05-13", (5.00, 15.00)),
        ("gpt-4o-mini", (0.15, 0.60)),
        ("o3", (2.00, 8.00)),
        ("o3-mini", (1.10, 4.40)),
        ("o4-mini", (1.10, 4.40)),
        ("o1", (15.00, 60.00)),
        ("gpt-3.5-turbo", (0.50, 1.50)),
    ],
)
def test_published_rates(model: str, rates: tuple[float, float]) -> None:
    assert pricing.rates_for(model) == pytest.approx((rates[0] / M, rates[1] / M))


@pytest.mark.parametrize("model", ["gpt-6-sol-mini", "gpt-5.4-pro", "azure/gpt-4o", "gpt-7-foo"])
def test_a_name_the_table_does_not_carry_has_no_rates(model: str) -> None:
    assert pricing.rates_for(model) is None


def test_the_older_gpt_4o_snapshot_is_not_bounded_at_the_current_rate() -> None:
    old = pricing.bound_cost("x" * 100, "gpt-4o-2024-05-13", 100)
    assert old == pytest.approx((100 + pricing.FRAMING_TOKENS) * 5.00 / M + 100 * 15.00 / M)
    # And a dated suffix on it keeps its own row rather than the shorter family name.
    assert pricing.rates_for("gpt-4o-2024-05-13-01") == pricing.rates_for("gpt-4o-2024-05-13")


@pytest.mark.parametrize(
    ("model", "write", "output"),
    [
        ("gpt-6-astra", 12.50, 50.00),
        ("gpt-6-sol", 2.50, 10.00),
        ("gpt-5.6-terra", 2.50, 12.00),
        ("openai/gpt-5.6-luna", 0.25, 1.20),
    ],
)
def test_a_cacheable_prompt_is_bounded_at_the_cache_write_rate(
    model: str, write: float, output: float
) -> None:
    prompt = "x" * 2_000
    tokens = len(prompt) + pricing.FRAMING_TOKENS
    assert pricing.bound_cost(prompt, model, 100) == pytest.approx(
        (tokens * write * 2 / M + 100 * output * 1.5 / M) * UPLIFT
    )


@pytest.mark.parametrize(
    ("model", "input_rate", "output"),
    [
        ("gpt-5.5", 5.00, 30.00),
        ("gpt-5.4", 2.50, 15.00),
    ],
)
def test_a_model_with_no_cache_write_charge_is_bounded_at_its_input_rate(
    model: str, input_rate: float, output: float
) -> None:
    prompt = "x" * 2_000
    tokens = len(prompt) + pricing.FRAMING_TOKENS
    assert pricing.bound_cost(prompt, model, 100) == pytest.approx(
        (tokens * input_rate * 2 / M + 100 * output * 1.5 / M) * UPLIFT
    )


@pytest.mark.parametrize(
    ("model", "input_rate", "output", "tiered", "uplift"),
    [
        ("gpt-6-sol", 2.50, 10.00, True, UPLIFT),
        ("gpt-6-astra", 12.50, 50.00, True, UPLIFT),
        ("gpt-5.6", 5.00, 20.00, True, UPLIFT),
        ("gpt-5.5", 5.00, 30.00, True, UPLIFT),
        ("gpt-5.4", 2.50, 15.00, True, UPLIFT),
        # No tier: their input is capped below the threshold, and 4.1 lists none.
        ("gpt-5.4-mini", 0.75, 4.50, False, UPLIFT),
        ("gpt-5", 1.25, 10.00, False, 1.0),
        ("gpt-4.1", 2.00, 8.00, False, 1.0),
    ],
)
def test_the_long_context_tier_applies_above_its_threshold(
    model: str, input_rate: float, output: float, tiered: bool, uplift: float
) -> None:
    above = "x" * (models.LONG_CONTEXT_THRESHOLD - pricing.FRAMING_TOKENS + 1)
    tokens = len(above) + pricing.FRAMING_TOKENS
    factor_in, factor_out = (2.0, 1.5) if tiered else (1.0, 1.0)
    assert pricing.bound_cost(above, model, 100) == pytest.approx(
        (tokens * input_rate * factor_in / M + 100 * output * factor_out / M) * uplift
    )


def _supplied(input_usd: float, output_usd: float) -> dict[str, object]:
    return {
        "llm.price_per_input_token": input_usd / M,
        "llm.price_per_output_token": output_usd / M,
    }


@pytest.mark.parametrize("prompt_bytes", [100, 1_000, 2_000])
def test_supplied_base_rates_never_lower_a_known_models_bound(prompt_bytes: int) -> None:
    """The published base rates alone leave out Sol's cache-write rate and regional uplift."""
    prompt = "x" * prompt_bytes
    table = pricing.bound_cost(prompt, "gpt-6-sol", 100)
    assert pricing.bound_cost(prompt, "gpt-6-sol", 100, _supplied(2.00, 10.00)) == pytest.approx(
        table
    )
    assert pricing.bound_cost(prompt, "gpt-6-sol", 100, _supplied(0.01, 0.01)) == pytest.approx(
        table
    )


def test_supplied_rates_for_a_known_model_carry_its_billing_modifiers() -> None:
    """Rates above the table's raise the bound, with the write ratio, tier and uplift applied."""
    prompt = "x" * 2_000
    tokens = len(prompt) + pricing.FRAMING_TOKENS
    # Sol's cache write is 1.25 times its input rate.
    expected = (tokens * 4.00 * 1.25 * 2 / M + 100 * 20.00 * 1.5 / M) * UPLIFT
    assert pricing.bound_cost(prompt, "gpt-6-sol", 100, _supplied(4.00, 20.00)) == pytest.approx(
        expected
    )
    above = "x" * (models.LONG_CONTEXT_THRESHOLD - pricing.FRAMING_TOKENS + 1)
    tokens = len(above) + pricing.FRAMING_TOKENS
    tiered = (tokens * 4.00 * 1.25 * 2 / M + 100 * 20.00 * 1.5 / M) * UPLIFT
    assert pricing.bound_cost(above, "gpt-6-sol", 100, _supplied(4.00, 20.00)) == pytest.approx(
        tiered
    )
    # An unflagged model without a write charge takes the supplied rates as they are.
    small = "x" * 100
    tokens = len(small) + pricing.FRAMING_TOKENS
    assert pricing.bound_cost(small, "gpt-4o", 100, _supplied(5.00, 20.00)) == pytest.approx(
        tokens * 5.00 / M + 100 * 20.00 / M
    )


def test_supplied_rates_for_an_unknown_model_are_used_as_given() -> None:
    prompt = "x" * 2_000
    tokens = len(prompt) + pricing.FRAMING_TOKENS
    assert pricing.bound_cost(prompt, "house-model", 100, _supplied(1.00, 2.00)) == pytest.approx(
        tokens * 1.00 / M + 100 * 2.00 / M
    )


def test_every_listed_model_is_priceable_and_boundable() -> None:
    for name in pricing.priceable_models():
        assert pricing.rates_for(name) is not None
        assert pricing.can_bound(name, 512)


@pytest.mark.parametrize("name", sorted(models.MODELS))
def test_every_rate_prints_as_the_published_decimal(name: str) -> None:
    """`model inspect` prints these, and 2.0000000000000002e-07 is not a price anyone lists."""
    entry = models.MODELS[name]
    for rate in (entry.input, entry.output, entry.cached_input, entry.cache_write):
        if rate is None:
            continue
        mantissa = repr(rate).split("e")[0].replace(".", "").lstrip("0").rstrip("0")
        assert len(mantissa) <= 4, f"{name}: {rate!r}"


# --------------------------------------------------------------- settlement --
@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
def test_a_zero_price_for_a_call_that_used_tokens_is_not_a_reading(
    litellm_stub: InstallStub, invoke: Callable[[DefaultLlmClient], str]
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]", usage=USAGE), cost=0.0))
    budget = Budget()
    client = DefaultLlmClient({"llm.model": "gpt-4o", "llm.max_tokens": 512}, budget=budget)

    invoke(client)

    assert client.last_usage is not None
    assert client.last_usage.cost_known is False
    assert client.last_usage.prompt_tokens == 12


@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
def test_a_zero_price_under_a_ceiling_keeps_the_reservation(
    litellm_stub: InstallStub, invoke: Callable[[DefaultLlmClient], str]
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]", usage=USAGE), cost=0.0))
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient({"llm.model": "gpt-4o", "llm.max_tokens": 512}, budget=budget)

    invoke(client)

    bound = client.bound_for_attempt("who?", {})
    assert budget.cost == pytest.approx(bound)
    assert bound > 0
    assert client.last_usage is not None
    assert client.last_usage.cost_known is False


@pytest.mark.parametrize(
    "usage",
    [None, {"prompt_tokens": 0}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}],
    ids=["no-usage", "zero-prompt", "all-zero"],
)
def test_a_zero_price_is_never_a_measured_cost_for_a_contacted_call(
    litellm_stub: InstallStub, usage: dict[str, Any] | None
) -> None:
    """Every request sends a prompt, so a zero is not an account of the call."""
    litellm_stub(LitellmStub(chat_payload("[]", usage=usage), cost=0.0))
    client = DefaultLlmClient({"llm.model": "gpt-4o", "llm.max_tokens": 512})
    client.complete("who?")
    assert client.last_usage is not None
    assert client.last_usage.cost_known is False


def test_a_cache_hit_still_costs_a_measured_zero(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload("[]", usage=USAGE), cost=0.001))
    cache = MemoryCache()
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient(
        {"cache_store": cache, "llm.model": "gpt-4o-mini", "llm.max_tokens": 512}, budget=budget
    )
    client.complete("who?")
    client.complete("who?")
    assert client.last_usage is not None
    assert client.last_usage.cache_hit is True
    assert client.last_usage.cost == 0.0
    assert client.last_usage.cost_known is True
    assert budget.cost == pytest.approx(0.001)


@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
@pytest.mark.parametrize(
    ("model", "properties"),
    [
        ("gpt-6-sol", {}),
        ("gpt-6-astra", {}),
        ("gpt-5.6", {}),
        ("gpt-5.5", {}),
        ("gpt-5.4", {}),
        ("openai/gpt-6-sol", {}),
        # The regional uplift is not in the calculator's figure either.
        ("gpt-5.4-mini", {}),
        ("gpt-5.4-nano", {}),
        ("gpt-5", {}),
        ("gpt-4.1", {}),
        ("gpt-4o", {}),
        ("gpt-4o-2024-05-13", {}),
        ("o3", {}),
        ("openai/gpt-4o", {}),
        ("gpt-4o", {"llm.price_per_input_token": 1e-5, "llm.price_per_output_token": 1e-5}),
        (
            "gpt-7-foo",
            {"llm.price_per_input_token": 1e-5, "llm.price_per_output_token": 1e-5},
        ),
    ],
)
def test_a_ceiling_keeps_the_reservation_when_the_response_cannot_prove_the_charge(
    litellm_stub: InstallStub,
    invoke: Callable[[DefaultLlmClient], str],
    model: str,
    properties: dict[str, Any],
) -> None:
    reported = 1e-9
    litellm_stub(LitellmStub(chat_payload("[]", usage=USAGE), cost=reported))
    budget = Budget(Limits(max_cost=100.0))
    client = DefaultLlmClient(
        {"llm.model": model, "llm.max_tokens": 512, **properties}, budget=budget
    )

    invoke(client)

    assert client.last_usage is not None
    assert budget.cost == pytest.approx(client.bound_for_attempt("who?", {}))
    assert client.last_usage.cost_known is False


def test_without_a_ceiling_a_retained_model_settles_at_the_reported_figure(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]", usage=USAGE), cost=0.003))
    budget = Budget()
    client = DefaultLlmClient({"llm.model": "gpt-6-sol", "llm.max_tokens": 512}, budget=budget)
    client.complete("who?")
    assert budget.cost == pytest.approx(0.003)
    assert client.last_usage is not None
    assert client.last_usage.cost_known is True


def test_a_reported_figure_above_the_reservation_wins(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload("[]", usage=USAGE), cost=0.9))
    budget = Budget(Limits(max_cost=100.0))
    client = DefaultLlmClient({"llm.model": "gpt-6-astra", "llm.max_tokens": 512}, budget=budget)
    client.complete("who?")
    assert budget.cost == pytest.approx(0.9)


# ------------------------------------------------------ usage-bounded charge --
def _served(
    model: str,
    *,
    prompt: Any = 1_000,
    cached: Any = 0,
    completion: Any = 200,
    tier: Any = "default",
    details: bool = True,
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": 1_200,
    }
    if details:
        usage["prompt_tokens_details"] = {"cached_tokens": cached, "cache_write_tokens": 0}
    payload = chat_payload("[]", model=model, usage=usage)
    if tier is not None:
        payload["service_tier"] = tier
    return payload


def _charged(
    model: str,
    invoke: Callable[[DefaultLlmClient], str] = _complete,
    *,
    max_tokens: int = 25_000,
    **properties: Any,
) -> tuple[float, float, bool]:
    """(charged to the budget, the attempt's reservation, whether the charge is known)."""
    budget = Budget(Limits(max_cost=100.0))
    client = DefaultLlmClient(
        {"llm.model": model, "llm.max_tokens": max_tokens, **properties}, budget=budget
    )
    invoke(client)
    assert client.last_usage is not None
    return budget.cost, client.bound_for_attempt("who?", {}), client.last_usage.cost_known


def _bound(
    uncached_rate: float,
    cached_rate: float,
    output: float,
    *,
    prompt: int,
    cached: int,
    completion: int,
    tiered: bool = True,
    uplift: float = UPLIFT,
) -> float:
    factor_in, factor_out = (2.0, 1.5) if tiered else (1.0, 1.0)
    return (
        uplift
        * (
            (prompt - cached) * uncached_rate * factor_in
            + cached * cached_rate * factor_in
            + completion * output * factor_out
        )
        / M
    )


@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
@pytest.mark.parametrize(
    ("model", "answered", "rates"),
    [
        # (uncached rate, cached rate, output): uncached is the cache-write rate where one exists.
        ("gpt-6-sol", "gpt-6-sol", (2.50, 0.20, 10.00)),
        ("gpt-6-astra", "gpt-6-astra-2026-09-01", (12.50, 1.00, 50.00)),
        ("gpt-6-luna", "gpt-6-luna-2026-09-22", (0.125, 0.01, 0.50)),
        ("gpt-5.5", "gpt-5.5-2026-04-23", (5.00, 0.50, 30.00)),
        ("gpt-5.4", "gpt-5.4-2026-03-05", (2.50, 0.25, 15.00)),
        ("openai/gpt-6-sol", "gpt-6-sol", (2.50, 0.20, 10.00)),
        # The alias answers as the model it routes to, which carries the same row.
        ("gpt-5.6", "gpt-5.6-sol", (5.00, 0.40, 20.00)),
    ],
)
@pytest.mark.parametrize("cached", [0, 600])
def test_a_retained_call_keeps_its_reservation_above_the_usage_bound(
    litellm_stub: InstallStub,
    invoke: Callable[[DefaultLlmClient], str],
    model: str,
    answered: str,
    rates: tuple[float, float, float],
    cached: int,
) -> None:
    litellm_stub(LitellmStub(_served(answered, cached=cached), cost=1e-9))

    charged, reservation, known = _charged(model, invoke)

    expected = _bound(*rates, prompt=1_000, cached=cached, completion=200)
    assert expected < reservation
    assert charged == pytest.approx(reservation)
    assert known is False


def test_the_exact_figure_for_one_call() -> None:
    """Written out once by hand, so the helper above is not the only statement of the rule."""
    # gpt-6-sol, 1,000 prompt tokens of which 600 cached, 200 completion tokens.
    expected = 1.10 * (400 * 2.50 * 2 + 600 * 0.20 * 2 + 200 * 10.00 * 1.5) / M
    assert expected == pytest.approx(0.005764)
    assert _bound(2.50, 0.20, 10.00, prompt=1_000, cached=600, completion=200) == pytest.approx(
        expected
    )


# Responses whose charge cannot be bounded from usage, one guard failing in each.
UNBOUNDABLE = [
    pytest.param("gpt-6-sol", _served("gpt-6-sol", details=False), {}, id="no-details"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol", cached=None), {}, id="no-cached-count"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol", cached=-1), {}, id="negative-cached"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol", cached=True), {}, id="bool-cached"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol", cached=10.0), {}, id="float-cached"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol", cached=1_001), {}, id="cached-above-prompt"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol", prompt="1000"), {}, id="text-prompt-count"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol", completion=-5), {}, id="negative-completion"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol", tier="priority"), {}, id="priority-tier"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol", tier="auto"), {}, id="auto-tier"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol", tier=None), {}, id="missing-tier"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol", tier="scale"), {}, id="unknown-tier"),
    pytest.param("gpt-6-sol", _served("gpt-6-luna"), {}, id="answered-by-another-model"),
    pytest.param("gpt-6-sol", _served("gpt-6-sol-mini"), {}, id="answered-by-a-longer-name"),
    pytest.param("gpt-6-sol", chat_payload("[]", usage=USAGE), {}, id="answered-by-no-known-model"),
    pytest.param(
        "gpt-6-sol",
        _served("gpt-6-sol"),
        {"llm.price_per_input_token": 1e-5, "llm.price_per_output_token": 1e-5},
        id="caller-rates",
    ),
    pytest.param(
        "gpt-7-foo",
        _served("gpt-7-foo"),
        {"llm.price_per_input_token": 1e-5, "llm.price_per_output_token": 1e-5},
        id="unknown-model",
    ),
]


@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
@pytest.mark.parametrize(("model", "payload", "properties"), UNBOUNDABLE)
def test_a_call_whose_charge_cannot_be_bounded_from_usage_keeps_its_reservation(
    litellm_stub: InstallStub,
    invoke: Callable[[DefaultLlmClient], str],
    model: str,
    payload: dict[str, Any],
    properties: dict[str, Any],
) -> None:
    litellm_stub(LitellmStub(payload, cost=1e-9))

    charged, reservation, known = _charged(model, invoke, **properties)

    assert charged == pytest.approx(reservation)
    assert known is False


def test_a_usage_bound_above_the_reservation_wins(litellm_stub: InstallStub) -> None:
    """The reservation was too small, so the ceiling was crossed; the ledger says so."""
    litellm_stub(LitellmStub(_served("gpt-6-sol", completion=5_000), cost=1e-9))

    charged, reservation, known = _charged("gpt-6-sol", max_tokens=16)

    expected = _bound(2.50, 0.20, 10.00, prompt=1_000, cached=0, completion=5_000)
    assert expected > reservation
    assert charged == pytest.approx(expected)
    assert known is False


@pytest.mark.parametrize("model", sorted(models.MODELS))
@pytest.mark.parametrize("prompt_size", [603, 1_023, 1_024, 272_001])
@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_reservation_bounds_conservative_settlement_for_supported_usage(
    litellm_stub: InstallStub,
    model: str,
    prompt_size: int,
    async_mode: bool,
) -> None:
    """A valid capped reply cannot raise the ledger charge above its reservation."""
    prompt = "x" * prompt_size
    output_cap = 32
    payload = _served(model, prompt=prompt_size, completion=output_cap)
    litellm_stub(LitellmStub(payload, cost=1e-9))
    budget = Budget(Limits(max_cost=100.0, max_retries=0))
    client = DefaultLlmClient({"llm.model": model, "llm.max_tokens": output_cap}, budget=budget)
    estimate = client.bound_for_attempt(prompt, {})

    if async_mode:
        asyncio.run(client.acomplete(prompt))
    else:
        client.complete(prompt)

    assert budget.calls == 1
    assert budget.cost <= estimate


def test_a_calculator_figure_above_the_reservation_wins(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(_served("gpt-6-sol"), cost=0.9))
    charged, _, known = _charged("gpt-6-sol")
    assert charged == pytest.approx(0.9)
    assert known is False


def test_a_flex_tier_is_bounded_at_standard_rates(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(_served("gpt-6-sol", tier="flex"), cost=1e-9))
    charged, reservation, _ = _charged("gpt-6-sol")
    assert charged == pytest.approx(reservation)


@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
@pytest.mark.parametrize(
    ("model", "rates"),
    [
        ("gpt-5.4-mini", (0.75, 0.075, 4.50)),
        ("gpt-5.4-nano", (0.20, 0.02, 1.25)),
    ],
)
@pytest.mark.parametrize("reported", [1e-9, 0.0], ids=["calculator-figure", "unreadable"])
def test_a_regional_model_without_other_modifiers_settles_with_the_uplift(
    litellm_stub: InstallStub,
    invoke: Callable[[DefaultLlmClient], str],
    model: str,
    rates: tuple[float, float, float],
    reported: float,
) -> None:
    """The calculator prices at standard rates, 10% under a regional charge."""
    litellm_stub(LitellmStub(_served(model, cached=600), cost=reported))

    charged, reservation, known = _charged(model, invoke)

    expected = _bound(*rates, prompt=1_000, cached=600, completion=200, tiered=False)
    assert expected < reservation
    assert charged == pytest.approx(reservation)
    assert known is False


@pytest.mark.parametrize("model", ["gpt-5.4-mini", "gpt-5.4-nano"])
def test_a_regional_model_keeps_its_reservation_when_usage_cannot_bound_it(
    litellm_stub: InstallStub, model: str
) -> None:
    litellm_stub(LitellmStub(_served(model, tier="priority"), cost=1e-9))
    charged, reservation, known = _charged(model)
    assert charged == pytest.approx(reservation)
    assert known is False


def test_a_simple_model_keeps_the_reservation_above_the_calculator_figure(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(LitellmStub(_served("gpt-5-mini"), cost=0.000_7))
    charged, reservation, known = _charged("gpt-5-mini")
    assert charged == pytest.approx(reservation)
    assert reservation > 0.000_7
    assert known is False


@pytest.mark.parametrize(
    ("model", "input_rate", "output", "uplift"),
    [
        ("gpt-5.4-mini", 0.75, 4.50, UPLIFT),
        ("gpt-5.4-nano", 0.20, 1.25, UPLIFT),
        ("gpt-5.5", 5.00, 30.00, UPLIFT),
        ("gpt-5-mini", 0.25, 2.00, 1.0),
        ("gpt-4.1", 2.00, 8.00, 1.0),
        ("o3", 2.00, 8.00, 1.0),
        ("gpt-4-turbo", 10.00, 30.00, 1.0),
    ],
)
def test_the_admission_bound_carries_the_regional_uplift_where_it_applies(
    model: str, input_rate: float, output: float, uplift: float
) -> None:
    tokens = 1 + pricing.FRAMING_TOKENS
    entry = models.entry_for(model)
    tier = entry.long_context if entry is not None else None
    input_factor = tier.input_multiplier if tier is not None else 1.0
    output_factor = tier.output_multiplier if tier is not None else 1.0
    assert pricing.bound_cost("x", model, 100) == pytest.approx(
        (tokens * input_rate * input_factor / M + 100 * output * output_factor / M) * uplift
    )


# ------------------------------------------ usage bound for an unreadable cost --
# (uncached rate, cached rate, output, tiered, uplift) for the two models below.
_UNPRICED = [
    pytest.param("gpt-6-sol", (2.50, 0.20, 10.00, True, UPLIFT), id="gpt-6-sol"),
    pytest.param("gpt-4o-mini", (0.15, 0.075, 0.60, False, 1.0), id="gpt-4o-mini"),
]


@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
@pytest.mark.parametrize(("model", "rates"), _UNPRICED)
@pytest.mark.parametrize("ceiling", [True, False], ids=["ceiling", "no-ceiling"])
def test_a_call_the_calculator_cannot_price_keeps_the_reservation_under_a_ceiling(
    litellm_stub: InstallStub,
    invoke: Callable[[DefaultLlmClient], str],
    model: str,
    rates: tuple[float, float, float, bool, float],
    ceiling: bool,
) -> None:
    """A calculator zero is unpriced; a ceiling retains its reservation."""
    uncached, cached_rate, output, tiered, uplift = rates
    litellm_stub(LitellmStub(_served(model, cached=600), cost=0.0))
    budget = Budget(Limits(max_cost=100.0)) if ceiling else Budget()
    client = DefaultLlmClient({"llm.model": model, "llm.max_tokens": 25_000}, budget=budget)

    invoke(client)

    expected = _bound(
        uncached,
        cached_rate,
        output,
        prompt=1_000,
        cached=600,
        completion=200,
        tiered=tiered,
        uplift=uplift,
    )
    charged = max(expected, client.bound_for_attempt("who?", {})) if ceiling else expected
    assert budget.cost == pytest.approx(charged)
    assert client.last_usage is not None
    assert client.last_usage.cost == pytest.approx(charged)
    assert client.last_usage.cost_known is False


def test_a_calculator_that_raises_is_bounded_the_same_way(litellm_stub: InstallStub) -> None:
    stub = LitellmStub(_served("gpt-6-sol"))

    def unreadable(completion_response: Any = None, **_: Any) -> float:
        raise ValueError("model not in the price map")

    stub.completion_cost = unreadable  # type: ignore[method-assign]
    litellm_stub(stub)

    charged, reservation, known = _charged("gpt-6-sol")

    assert charged == pytest.approx(reservation)
    assert known is False


def test_an_unreadable_usage_bound_above_the_reservation_wins(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(_served("gpt-6-sol", completion=5_000), cost=0.0))

    charged, reservation, _ = _charged("gpt-6-sol", max_tokens=16)

    expected = _bound(2.50, 0.20, 10.00, prompt=1_000, cached=0, completion=5_000)
    assert expected > reservation
    assert charged == pytest.approx(expected)


@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
@pytest.mark.parametrize(("model", "payload", "properties"), UNBOUNDABLE)
@pytest.mark.parametrize("ceiling", [True, False], ids=["ceiling", "no-ceiling"])
def test_an_unreadable_cost_with_no_usage_bound_keeps_the_reservation(
    litellm_stub: InstallStub,
    invoke: Callable[[DefaultLlmClient], str],
    model: str,
    payload: dict[str, Any],
    properties: dict[str, Any],
    ceiling: bool,
) -> None:
    """The reservation stands under a ceiling; without one nothing is claimed or charged."""
    stub = LitellmStub(payload)

    def unreadable(completion_response: Any = None, **_: Any) -> float:
        raise ValueError("model not in the price map")

    stub.completion_cost = unreadable  # type: ignore[method-assign]
    litellm_stub(stub)
    budget = Budget(Limits(max_cost=100.0)) if ceiling else Budget()
    client = DefaultLlmClient(
        {"llm.model": model, "llm.max_tokens": 25_000, **properties}, budget=budget
    )

    invoke(client)

    expected = client.bound_for_attempt("who?", {}) if ceiling else 0.0
    assert budget.cost == pytest.approx(expected)
    assert client.last_usage is not None
    assert client.last_usage.cost == pytest.approx(expected)
    assert client.last_usage.cost_known is False


def test_a_stream_that_carried_no_usage_keeps_its_reservation(litellm_stub: InstallStub) -> None:
    """The assembler's locally counted usage is dropped, so there is nothing to bound from."""
    chunks = stream_chunks("[]", model="gpt-6-sol")
    chunks[-1]["service_tier"] = "default"
    litellm_stub(LitellmStub(chat_payload("[]"), chunks=chunks, cost=0.0))

    streamed: dict[str, Any] = {"llm.stream": True}
    charged, reservation, known = _charged("gpt-6-sol", **streamed)

    assert charged == pytest.approx(reservation)
    assert known is False


# ------------------------------------------------- counts that are not an account --
def _unreadable_cost(stub: LitellmStub) -> LitellmStub:
    def unreadable(completion_response: Any = None, **_: Any) -> float:
        raise ValueError("model not in the price map")

    stub.completion_cost = unreadable  # type: ignore[method-assign]
    return stub


@pytest.mark.parametrize(
    "invoke",
    [_complete, _acomplete],
    ids=["sync", "async"],
)
@pytest.mark.parametrize("model", ["gpt-6-sol", "gpt-4o-mini"])
@pytest.mark.parametrize("calculator", ["zero", "unreadable", "positive"])
@pytest.mark.parametrize(
    "counts",
    [
        {"prompt": 0, "cached": 0, "completion": 0},
        {"prompt": 0, "cached": 0, "completion": 200},
        {"prompt": 1_000, "cached": 0, "completion": 0},
    ],
    ids=["all-zero", "zero-prompt", "zero-completion-with-text"],
)
def test_counts_that_cannot_describe_the_call_keep_the_reservation(
    litellm_stub: InstallStub,
    invoke: Callable[[DefaultLlmClient], str],
    model: str,
    calculator: str,
    counts: dict[str, int],
) -> None:
    """All-zero counts beside a text answer priced a billed call at nothing, repeatably."""
    stub = LitellmStub(
        _served(
            model,
            prompt=counts["prompt"],
            cached=counts["cached"],
            completion=counts["completion"],
        ),
        cost=1e-9 if calculator == "positive" else 0.0,
    )
    if calculator == "unreadable":
        _unreadable_cost(stub)
    litellm_stub(stub)

    charged, reservation, known = _charged(model, invoke)

    assert charged == pytest.approx(reservation)
    assert known is False


def test_counts_that_cannot_describe_the_call_are_unknown_without_a_ceiling(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(LitellmStub(_served("gpt-6-sol", prompt=0, cached=0, completion=0), cost=0.0))
    budget = Budget()
    client = DefaultLlmClient({"llm.model": "gpt-6-sol", "llm.max_tokens": 512}, budget=budget)
    client.complete("who?")
    assert client.last_usage is not None
    assert client.last_usage.cost_known is False
    assert budget.cost == 0.0


@pytest.mark.parametrize("calculator", ["zero", "unreadable"])
def test_the_smallest_real_counts_do_not_lower_the_reservation(
    litellm_stub: InstallStub, calculator: str
) -> None:
    stub = LitellmStub(_served("gpt-6-sol", prompt=1, cached=0, completion=1), cost=0.0)
    if calculator == "unreadable":
        _unreadable_cost(stub)
    litellm_stub(stub)

    charged, reservation, known = _charged("gpt-6-sol")

    expected = _bound(2.50, 0.20, 10.00, prompt=1, cached=0, completion=1)
    assert 0 < expected < reservation
    assert charged == pytest.approx(reservation)
    assert known is False


def test_an_empty_answer_may_report_no_output_tokens(litellm_stub: InstallStub) -> None:
    payload = _served("gpt-6-sol", prompt=1_000, cached=0, completion=0)
    payload["choices"][0]["message"]["content"] = ""
    litellm_stub(LitellmStub(payload, cost=0.0))

    charged, reservation, _ = _charged("gpt-6-sol")

    assert charged == pytest.approx(reservation)


# ------------------------------------------------------- published defaults --
def test_published_defaults_describe_the_table_entry() -> None:
    assert pkg.published_defaults("openai/gpt-6-sol") == {
        "reasoning_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        "default_reasoning_effort": "medium",
        "temperature": "effort_none_only",
        "output_token_parameter": "max_completion_tokens",
        "cached_input_price_per_token": pytest.approx(0.20 / M),
        "cache_write_price_per_token": pytest.approx(2.50 / M),
        "long_context_threshold_tokens": 272_000,
    }
    assert pkg.published_defaults("gpt-4o") == {
        "reasoning_efforts": [],
        "default_reasoning_effort": None,
        "temperature": "always",
        "output_token_parameter": "max_tokens",
        "cached_input_price_per_token": pytest.approx(1.25 / M),
        "cache_write_price_per_token": None,
        "long_context_threshold_tokens": None,
    }
    defaults = pkg.published_defaults("gpt-5-nano-2025-08-07")
    assert defaults is not None
    assert defaults["reasoning_efforts"] == ["minimal", "low", "medium", "high"]
    assert defaults["temperature"] == "never"


def test_a_price_only_row_publishes_no_contract_and_an_unknown_model_publishes_nothing() -> None:
    defaults = pkg.published_defaults("gpt-4")
    assert defaults is not None
    assert defaults["reasoning_efforts"] is None
    assert defaults["output_token_parameter"] is None
    assert pkg.published_defaults("gpt-7-foo") is None


@pytest.mark.parametrize("model", ["claude-3-5-haiku", "claude-3-5-sonnet", "claude-3-opus"])
def test_models_this_variant_cannot_call_are_not_listed_as_boundable(model: str) -> None:
    """The ``openai`` variant cannot reach them, so a ceiling bounded on them bounds nothing."""
    assert model not in pricing.priceable_models()
    assert pricing.rates_for(model) is None


def test_only_an_openai_family_name_is_pointed_at_openai() -> None:
    """The hint for an unroutable name suggests ``openai/`` only where OpenAI serves it."""
    assert models.openai_family("gpt-9-new")
    assert models.openai_family("openai/o7-mini")
    assert not models.openai_family("mistral-large")
    assert not models.openai_family("claude-3-5-haiku")
