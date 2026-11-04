"""Operation strings through the SDK registry and source entry points."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable, Iterator
from importlib import import_module, util
from typing import Any, NoReturn, cast

import pytest
from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,
)

from blue_data_llm_source import LlmSource
from blue_data_llm_source import _constants as C
from blue_data_llm_source.types import ConfigError, QueryError
from tests.doubles import ScriptedClient

RECORDS = '[{"name": "Ada", "age": 36}, {"name": "Grace", "age": 37}]'


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    attempts: list[str] = []

    def deny(*args: Any, **kwargs: Any) -> NoReturn:
        attempts.append("network")
        raise AssertionError("operation dispatch attempted network access")

    async def adeny(*args: Any, **kwargs: Any) -> NoReturn:
        deny(*args, **kwargs)

    if util.find_spec("httpx") is not None:
        httpx = import_module("httpx")
        monkeypatch.setattr(httpx.Client, "send", deny)
        monkeypatch.setattr(httpx.AsyncClient, "send", adeny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, deny)
    yield
    assert attempts == []


def registered_query(
    source: LlmSource, query: object, options: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    registry = cast("Any", DataRegistry())
    registry.register_source(source)
    return cast(
        "list[dict[str, Any]]",
        registry.execute_query("llm", query, optional_properties=options),
    )


def source_with_response() -> tuple[LlmSource, ScriptedClient]:
    client = ScriptedClient([RECORDS])
    return LlmSource(client=client, properties={"llm.model": "gpt-4o-mini"}), client


@pytest.mark.parametrize("op", C.OPS)
@pytest.mark.parametrize("padding", ["", " \t\r\n", "\u2003\xa0"])
def test_registry_dispatches_encoded_ops_like_dicts(op: str, padding: str) -> None:
    spec = {"op": op, "query": "people", "attrs": ["name"], "context": "names only"}
    direct, direct_client = source_with_response()
    encoded, encoded_client = source_with_response()

    expected = registered_query(direct, spec)
    actual = registered_query(encoded, padding + json.dumps(spec) + padding)

    assert actual == expected
    assert encoded_client.prompts == direct_client.prompts
    if op != C.OP_FETCH:
        assert encoded_client.prompts == []


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("commit", [False, True])
@pytest.mark.parametrize("op", ["LLM.DROP", None, 7])
def test_registry_refuses_invalid_tagged_ops_without_fetching(
    encoded: bool, commit: bool, op: object
) -> None:
    source, client = source_with_response()
    spec = {"op": op, "query": "people"}
    query = json.dumps(spec) if encoded else spec

    with pytest.raises(QueryError if commit else ValueError):
        registered_query(source, query, {"commit": commit})

    assert client.prompts == []


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("commit", [False, True])
def test_lowercase_ops_keep_the_dict_error_without_fetching(encoded: bool, commit: bool) -> None:
    source, client = source_with_response()
    spec = {"op": "llm.models"}

    with pytest.raises(QueryError, match="unknown op"):
        registered_query(source, json.dumps(spec) if encoded else spec, {"commit": commit})

    assert client.prompts == []


def test_duplicate_op_keys_follow_the_registry_guard() -> None:
    source, client = source_with_response()

    assert registered_query(source, '{"op":"LLM.FETCH","op":"LLM.MODELS"}') == [
        {"protocol": "llm", "variant": "openai"}
    ]
    assert client.prompts == []


@pytest.mark.parametrize(
    "query", ["people", "{not json", '[{"op":"LLM.MODELS"}]', '"people"', '{"query":"people"}']
)
def test_nonoperation_strings_keep_their_natural_language_meaning(query: str) -> None:
    source, client = source_with_response()

    assert source.execute_query(query) == json.loads(RECORDS)
    assert len(client.prompts) == 1


def test_deep_unparseable_json_keeps_direct_string_behavior() -> None:
    source, client = source_with_response()
    depth = 10_000
    query = '{"op":"LLM.MODELS","nested":' + "[" * depth + "0" + "]" * depth + "}"

    with pytest.raises(RecursionError):
        json.loads(query)
    assert source.execute_query(query) == json.loads(RECORDS)
    assert len(client.prompts) == 1


def test_object_string_without_op_keeps_the_registry_commit_requirement() -> None:
    source, client = source_with_response()
    query = '{"query":"people"}'

    with pytest.raises(ValueError, match="requires"):
        registered_query(source, query)
    assert client.prompts == []
    assert registered_query(source, query, {"commit": True}) == json.loads(RECORDS)
    assert len(client.prompts) == 1
    with pytest.raises(QueryError, match="unknown op"):
        registered_query(source, json.loads(query), {"commit": True})


def test_encoded_fetch_keeps_optional_request_precedence_and_row_ceiling() -> None:
    client = ScriptedClient([RECORDS])
    source = LlmSource(client=client, properties={"limits.max_rows": 1})
    spec = {"op": C.OP_FETCH, "query": "people", "attrs": ["name"], "max_records": 50}

    assert registered_query(source, json.dumps(spec), {"attrs": ["age"], "max_records": 2}) == [
        {"age": 36}
    ]
    assert source.properties["limits.max_rows"] == 1
    assert len(client.prompts) == 1


@pytest.mark.parametrize("ceiling", [5.0, None])
def test_encoded_fetch_cannot_raise_a_configured_cost_ceiling(ceiling: float | None) -> None:
    client = ScriptedClient([RECORDS], cost=0.1)
    source = LlmSource(client=client, properties={"limits.max_cost": 0.01})

    assert (
        registered_query(
            source, '{"op":"LLM.FETCH","query":"people"}', {"limits.max_cost": ceiling}
        )
        == []
    )
    assert client.prompts == []


@pytest.mark.parametrize("row_cap_in_options", [False, True])
def test_encoded_estimate_still_refuses_a_row_cap(row_cap_in_options: bool) -> None:
    source, client = source_with_response()
    spec: dict[str, Any] = {"op": C.OP_ESTIMATE, "query": "people"}
    options = {"max_records": 1} if row_cap_in_options else None
    if not row_cap_in_options:
        spec["max_records"] = 1

    with pytest.raises(ConfigError, match="max_records"):
        registered_query(source, json.dumps(spec), options)
    assert client.prompts == []


def test_invalid_settings_are_checked_before_encoded_op_dispatch() -> None:
    source, client = source_with_response()

    with pytest.raises(ConfigError, match="unknown"):
        registered_query(source, '{"op":"LLM.MODELS"}', {"unknown.setting": True})
    assert client.prompts == []


def test_execute_query_tool_dispatches_an_encoded_op() -> None:
    source, client = source_with_response()
    invoke = cast(
        "Callable[[str, dict[str, Any]], list[dict[str, Any]]]",
        getattr(source, "execute_nl_tool"),
    )

    assert invoke("execute_query", {"query": '{"op":"LLM.MODELS"}'}) == [
        {"protocol": "llm", "variant": "openai"}
    ]
    assert client.prompts == []


def test_natural_language_entry_point_keeps_an_op_string_as_text() -> None:
    source, client = source_with_response()

    assert source.execute_query_nl('{"op":"LLM.MODELS"}') == json.loads(RECORDS)
    assert len(client.prompts) == 1
