"""FunctionClient: a plain callable used as the model."""

from __future__ import annotations

from blue_data_llm_source.llm.client import FunctionClient


def test_complete_returns_what_the_callable_returned() -> None:
    client = FunctionClient(lambda prompt: '[{"name": "Ada"}]')
    assert client.complete("who?") == '[{"name": "Ada"}]'


def test_the_callable_receives_the_prompt() -> None:
    seen: list[str] = []

    def model(prompt: str) -> str:
        seen.append(prompt)
        return "[]"

    FunctionClient(model).complete("who is the president?")

    assert seen == ["who is the president?"]


def test_a_non_string_return_value_is_stringified() -> None:
    assert FunctionClient(lambda prompt: 42).complete("who?") == "42"


def test_a_none_return_value_becomes_empty_text() -> None:
    assert FunctionClient(lambda prompt: None).complete("who?") == ""


def test_complete_records_one_usage_naming_the_injected_model() -> None:
    client = FunctionClient(lambda prompt: "[]")
    client.complete("who?")

    assert len(client.usages) == 1
    assert client.usages[0].model == "injected"
    assert client.last_usage is client.usages[0]


async def test_acomplete_returns_what_the_callable_returned() -> None:
    client = FunctionClient(lambda prompt: '[{"name": "Ada"}]')
    assert await client.acomplete("who?") == '[{"name": "Ada"}]'


async def test_acomplete_awaits_a_coroutine_result() -> None:
    async def model(prompt: str) -> str:
        return '[{"name": "Ada"}]'

    client = FunctionClient(model)
    assert await client.acomplete("who?") == '[{"name": "Ada"}]'


async def test_acomplete_stringifies_a_none_result() -> None:
    assert await FunctionClient(lambda prompt: None).acomplete("who?") == ""


async def test_acomplete_records_one_usage() -> None:
    client = FunctionClient(lambda prompt: "[]")
    await client.acomplete("who?")
    assert len(client.usages) == 1
