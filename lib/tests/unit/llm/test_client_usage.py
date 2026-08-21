"""Usage accounting is total.

A malformed usage block must not break a call that already succeeded.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.llm.client import DefaultLlmClient
from tests.doubles import LitellmStub, chat_payload

InstallStub = Callable[[LitellmStub], LitellmStub]


def test_junk_token_counts_are_recorded_as_zero(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload("[]", usage={"prompt_tokens": "junk"})))
    client = DefaultLlmClient()

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.prompt_tokens == 0
    assert client.last_usage.completion_tokens == 0


def test_a_response_with_no_usage_key_records_zero_tokens(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient()

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.prompt_tokens == 0
    assert client.last_usage.completion_tokens == 0


def test_a_usage_block_of_the_wrong_shape_records_zero_tokens(
    litellm_stub: InstallStub,
) -> None:
    payload = chat_payload("[]")
    payload["usage"] = ["prompt_tokens", 11]
    litellm_stub(LitellmStub(payload))
    client = DefaultLlmClient()

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.prompt_tokens == 0


def test_none_token_counts_are_recorded_as_zero(litellm_stub: InstallStub) -> None:
    litellm_stub(
        LitellmStub(chat_payload("[]", usage={"prompt_tokens": None, "completion_tokens": None}))
    )
    client = DefaultLlmClient()

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.prompt_tokens == 0


def test_numeric_string_token_counts_are_read_as_numbers(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(
        LitellmStub(chat_payload("[]", usage={"prompt_tokens": "11", "completion_tokens": 7}))
    )
    client = DefaultLlmClient()

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.prompt_tokens == 11
    assert client.last_usage.completion_tokens == 7


def test_reported_cost_is_recorded_on_the_usage(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload("[]"), cost=0.5))
    client = DefaultLlmClient()

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.cost == pytest.approx(0.5)


def test_an_uncostable_response_is_charged_at_its_reservation(litellm_stub: InstallStub) -> None:
    """None is not zero, wherever a ceiling depends on the difference.

    A call whose price cannot be read still happened and was still billed by the provider.
    Recording it as free is how a ceiling comes to admit every remaining attempt while the
    running total stays where it started, so the bound reserved for it stands instead.
    """
    stub = LitellmStub(chat_payload("[]"))
    stub.completion_cost = _raise_cost
    litellm_stub(stub)
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient({"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}, budget=budget)

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.cost > 0.0
    assert budget.cost == client.last_usage.cost


def test_an_uncostable_response_costs_nothing_when_no_ceiling_depends_on_it(
    litellm_stub: InstallStub,
) -> None:
    """No bound is computed when there is no ceiling to enforce.

    Otherwise a run with no cost limit would be refused for using a model this package has
    never priced, which is a ceiling getting in the way of a caller who set none.
    """
    stub = LitellmStub(chat_payload("[]"))
    stub.completion_cost = _raise_cost
    litellm_stub(stub)
    client = DefaultLlmClient()

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.cost == 0.0


def test_every_call_appends_one_usage_record(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient()

    client.complete("who?")
    client.complete("who else?")

    assert len(client.usages) == 2
    assert client.usages[-1] is client.last_usage


def test_latency_is_recorded_for_every_call(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload("[]")))
    client = DefaultLlmClient()

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.latency_seconds >= 0.0


def _raise_cost(*args: object, **kwargs: object) -> float:
    raise RuntimeError("this model has no price sheet")
