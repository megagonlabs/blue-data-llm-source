"""The client's call path, with litellm replaced by a stub module."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.llm.client import DefaultLlmClient
from blue_data_llm_source.types import BudgetExceeded, TransportError
from tests.doubles import LitellmStub, chat_payload

InstallStub = Callable[[LitellmStub], LitellmStub]


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
