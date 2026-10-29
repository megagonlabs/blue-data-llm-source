"""Transport failures: what the caller sees, and what the budget is left holding."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.llm.client import DefaultLlmClient
from blue_data_llm_source.types import BudgetExceeded, ConfigError, TransportError
from tests.doubles import LitellmStub, chat_payload

InstallStub = Callable[[LitellmStub], LitellmStub]

# tenacity retries a TransportError twice more before giving up.
ATTEMPTS = 3


def test_a_response_with_no_choices_list_raises_bad_response(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(LitellmStub({"id": "stub", "object": "error"}))

    with pytest.raises(TransportError) as caught:
        DefaultLlmClient().complete("who?")

    assert caught.value.code == "bad_response"
    assert "no choices list" in caught.value.message


def test_a_non_dict_response_raises_bad_response(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub("a plain string is not a completion"))

    with pytest.raises(TransportError) as caught:
        DefaultLlmClient().complete("who?")

    assert caught.value.code == "bad_response"


def test_a_provider_exception_is_wrapped_as_a_transport_error(
    litellm_stub: InstallStub,
) -> None:
    boom = RuntimeError("rate limited by the provider")
    litellm_stub(LitellmStub(error=boom))

    with pytest.raises(TransportError) as caught:
        DefaultLlmClient().complete("who?")

    assert caught.value.code == "provider_error"
    assert caught.value.provider_error is boom
    assert "rate limited by the provider" in caught.value.message


async def test_a_provider_exception_on_the_async_path_is_wrapped(
    litellm_stub: InstallStub,
) -> None:
    boom = RuntimeError("rate limited by the provider")
    litellm_stub(LitellmStub(error=boom))

    with pytest.raises(TransportError) as caught:
        await DefaultLlmClient().acomplete("who?")

    assert caught.value.code == "provider_error"
    assert caught.value.provider_error is boom


def test_failed_retries_cannot_outrun_the_call_ceiling(litellm_stub: InstallStub) -> None:
    """A ceiling of one means one provider entry, however many retries are configured.

    A provider that was contacted and errored consumed the attempt. Returning it would
    make every failed retry free, which is how a run with two retries reaches the
    provider three times under a ceiling of one.
    """
    boom = RuntimeError("private provider error")
    stub = litellm_stub(LitellmStub(error=boom))
    budget = Budget(Limits(max_calls=1, max_cost=1.0, max_retries=2))
    client = DefaultLlmClient({"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}, budget=budget)
    reservation = client.bound_for_attempt("who?", {})

    with pytest.raises(TransportError) as caught:
        client.complete("who?")

    assert len(stub.calls) == 1, "the ceiling must bound provider entries, not successes"
    assert caught.value.code == "provider_error"
    assert caught.value.provider_error is boom
    assert "private provider error" not in str(caught.value)
    assert "max_calls" in str(caught.value)
    assert budget.calls == 1
    assert budget.cost == pytest.approx(reservation)


async def test_failed_async_retries_cannot_outrun_the_call_ceiling(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub(error=RuntimeError("boom")))
    budget = Budget(Limits(max_calls=1, max_retries=2))
    client = DefaultLlmClient({"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}, budget=budget)

    with pytest.raises(TransportError) as caught:
        await client.acomplete("who?")

    assert "boom" not in str(caught.value)
    assert len(stub.calls) == 1
    assert budget.calls == 1


@pytest.mark.parametrize(
    ("limits", "axis"),
    [(Limits(max_calls=0), "max_calls"), (Limits(max_seconds=0), "max_seconds")],
)
def test_an_initial_call_refusal_remains_a_limit(
    litellm_stub: InstallStub, limits: Limits, axis: str
) -> None:
    stub = litellm_stub(LitellmStub(error=RuntimeError("boom")))
    budget = Budget(limits)

    with pytest.raises(BudgetExceeded) as caught:
        DefaultLlmClient(budget=budget).complete("who?")

    assert caught.value.limit == axis
    assert stub.calls == []


def test_prior_success_does_not_supply_a_failure_to_the_next_call(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    budget = Budget(Limits(max_calls=1))
    client = DefaultLlmClient(budget=budget)
    client.complete("first")

    with pytest.raises(BudgetExceeded) as caught:
        client.complete("second")

    assert caught.value.limit == "max_calls"
    assert len(stub.calls) == 1


@pytest.mark.parametrize(
    ("property_name", "bad_value"),
    [
        ("llm.stream", "false"),
        ("llm.stream", 1),
        ("llm.cache", "false"),
        ("llm.cache", 1),
        ("llm.cache_dir", 123),
    ],
)
def test_invalid_adapter_flag_type_is_refused_before_provider_contact(
    litellm_stub: InstallStub, property_name: str, bad_value: object
) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient({property_name: bad_value})

    with pytest.raises(ConfigError, match=property_name):
        client.complete("who?")

    assert stub.calls == []


def test_every_failed_attempt_is_charged_when_no_ceiling_stops_it(
    litellm_stub: InstallStub,
) -> None:
    """Without a call ceiling the retries run, and each one still counts."""
    stub = litellm_stub(LitellmStub(error=RuntimeError("boom")))
    budget = Budget(Limits(max_retries=2))
    client = DefaultLlmClient({"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}, budget=budget)

    with pytest.raises(TransportError):
        client.complete("who?")

    assert len(stub.calls) == 3
    assert budget.calls == 3


def test_a_failure_before_the_provider_is_reached_costs_nothing(
    litellm_stub: InstallStub,
) -> None:
    """The reservation happens after everything that can fail locally."""
    litellm_stub(LitellmStub(error=RuntimeError("boom")))
    budget = Budget(Limits(max_calls=1))
    client = DefaultLlmClient({"llm.top_p": 0.4}, budget=budget)

    with pytest.raises(ConfigError):
        client.complete("who?")

    assert budget.calls == 0


def test_a_transport_error_is_retried_before_it_is_raised(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub(error=RuntimeError("boom")))

    with pytest.raises(TransportError):
        DefaultLlmClient().complete("who?")

    assert len(stub.calls) == ATTEMPTS


def test_a_call_that_succeeds_is_not_retried(litellm_stub: InstallStub) -> None:
    stub = litellm_stub(LitellmStub(chat_payload("[]")))
    DefaultLlmClient().complete("who?")
    assert len(stub.calls) == 1


def test_max_retries_is_honored_by_the_client(litellm_stub: InstallStub) -> None:
    stub = litellm_stub(LitellmStub(error=RuntimeError("boom")))
    budget = Budget(Limits(max_retries=0))
    client = DefaultLlmClient({"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}, budget=budget)

    with pytest.raises(TransportError):
        client.complete("who?")

    assert len(stub.calls) == 1


def test_a_missing_provider_extra_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing optional extra is a configuration problem, not a transport failure.

    Missing provider and cache extras both produce configuration errors.
    """
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *args: object, **kwargs: object) -> object:
        if name == "litellm":
            raise ImportError("No module named 'litellm'")
        return real_import(name, *args, **kwargs)  # pyright: ignore[reportCallIssue, reportArgumentType]

    monkeypatch.setattr(builtins, "__import__", refuse)

    with pytest.raises(ConfigError, match=r"blue-data-llm-source\[llm\]"):
        DefaultLlmClient().complete("who?")


async def test_a_missing_provider_extra_names_the_extra_on_the_async_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *args: object, **kwargs: object) -> object:
        if name == "litellm":
            raise ImportError("No module named 'litellm'")
        return real_import(name, *args, **kwargs)  # pyright: ignore[reportCallIssue, reportArgumentType]

    monkeypatch.setattr(builtins, "__import__", refuse)

    with pytest.raises(ConfigError, match=r"blue-data-llm-source\[llm\]"):
        await DefaultLlmClient().acomplete("who?")


def test_a_malformed_response_leaves_no_reservation_outstanding(
    litellm_stub: InstallStub,
) -> None:
    """Everything after the provider is contacted resolves its claim.

    Guarding only the call left a response that failed to materialize holding its
    reservation, so each retry claimed again and the headroom never came back — which
    poisons a caller-owned budget for every later request that shares it.
    """
    stub = litellm_stub(LitellmStub({"no_choices_here": True}))
    budget = Budget(Limits(max_cost=1.0, max_retries=2))
    client = DefaultLlmClient({"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}, budget=budget)

    with pytest.raises(TransportError) as caught:
        client.complete("who?")

    assert caught.value.code == "bad_response"
    assert len(stub.calls) == 3
    assert budget.reserved == 0.0, "every claim must resolve, including on a bad response"
    assert budget.cost > 0.0, "three contacted attempts are not free"


async def test_a_malformed_async_response_leaves_no_reservation_outstanding(
    litellm_stub: InstallStub,
) -> None:
    stub = litellm_stub(LitellmStub({"no_choices_here": True}))
    budget = Budget(Limits(max_cost=1.0, max_retries=1))
    client = DefaultLlmClient({"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}, budget=budget)

    with pytest.raises(TransportError):
        await client.acomplete("who?")

    assert len(stub.calls) == 2
    assert budget.reserved == 0.0


def test_a_non_finite_cost_is_unreadable_rather_than_free(litellm_stub: InstallStub) -> None:
    """NaN arrives as a value, not an exception, and compares false against every ceiling."""

    def _nan_cost(completion_response: Any = None, **kwargs: Any) -> float:
        return float("nan")

    stub = LitellmStub(chat_payload("[]"))
    stub.completion_cost = _nan_cost  # pyright: ignore[reportAttributeAccessIssue]
    litellm_stub(stub)
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient({"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}, budget=budget)

    client.complete("who?")

    assert budget.cost > 0.0, "an unreadable cost is charged at its bound, not at zero"
    assert budget.reserved == 0.0
