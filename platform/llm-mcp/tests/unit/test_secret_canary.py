"""MCP tool redaction tests for errors, arguments, and result fields."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from typing import Any, cast

import pytest
from blue_data_llm_source import ConfigError, Estimate, LlmSource, LlmSourceError, TransportError
from blue_data_llm_source.redaction import MASK

from blue_data_llm_mcp import tools

STAMPED = "sk-canary-NOTAKEY"


def test_an_echoed_model_name_is_masked(source: LlmSource) -> None:
    answer = tools.dispatch("llmdb_inspect_model", {"model": STAMPED}, source=source)

    assert STAMPED not in json.dumps(answer)
    assert answer["model"] == "***"
    assert answer["priceable"] is False


def test_a_value_outside_a_declared_set_is_not_read_back() -> None:
    with pytest.raises(tools.ToolError) as raised:
        tools.dispatch("llmdb_query", {"query": "who?", "plan.mode": STAMPED})

    message = raised.value.message
    assert STAMPED not in message
    assert "flat" in message and "partitioned" in message and "row_by_row" in message


def test_a_blank_value_is_refused_without_reading_it_back() -> None:
    """The `attrs` oneOf schema names accepted shapes for a blank value."""
    with pytest.raises(tools.ToolError, match="must not be blank"):
        tools.dispatch("llmdb_query", {"query": "who?", "plan.key": "   "})

    with pytest.raises(tools.ToolError, match="non-blank string or an object") as caught:
        tools.dispatch("llmdb_query", {"query": "who?", "attrs": ["   "]})
    assert caught.value.kind == tools.INVALID_ARGUMENTS
    assert "   " not in caught.value.message

    with pytest.raises(tools.ToolError, match="must not be blank"):
        tools.dispatch("llmdb_query", {"query": "who?", "plan.keys": ["country", "  "]})


def test_a_repeated_key_is_refused_without_reading_it_back() -> None:
    with pytest.raises(tools.ToolError) as raised:
        tools.dispatch("llmdb_query", {"query": "who?", "plan.keys": [STAMPED, STAMPED]})

    assert STAMPED not in raised.value.message
    assert "must not repeat" in raised.value.message


@pytest.mark.parametrize(
    "call",
    [
        (STAMPED, {}),
        ("llmdb_query", {"query": "who?", STAMPED: 1}),
        ("llmdb_estimate", {"query": "who?", STAMPED: 1}),
    ],
    ids=["tool name", "query argument", "estimate argument"],
)
def test_a_name_quoted_back_to_the_caller_is_masked(call: tuple[str, dict[str, Any]]) -> None:
    name, arguments = call
    with pytest.raises(tools.ToolError) as raised:
        tools.dispatch(name, arguments)

    assert STAMPED not in raised.value.message
    assert "***" in raised.value.message


def test_a_provider_exception_does_not_reach_the_caller_intact(
    make_source: Callable[..., tuple[LlmSource, Any]],
) -> None:
    source, _ = make_source(TransportError(f"AuthenticationError: invalid api key {STAMPED}"))

    with pytest.raises(tools.ToolError) as raised:
        tools.dispatch("llmdb_query", {"query": "who?"}, source=source)

    assert STAMPED not in raised.value.message
    assert STAMPED not in str(raised.value)


@pytest.mark.parametrize("error_type", [TransportError, LlmSourceError])
def test_valid_operator_setting_echo_is_not_in_direct_tool_errors(
    error_type: type[LlmSourceError],
    make_source: Callable[..., tuple[LlmSource, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = "27183"
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", value)
    assert tools.deployment_properties()["llm.max_tokens"] == int(value)
    source, _ = make_source(error_type(f"provider echoed max_tokens={value}"))

    for call in (
        lambda: tools.dispatch("llmdb_query", {"query": "who?"}, source=source),
        lambda: asyncio.run(tools.adispatch("llmdb_query", {"query": "who?"}, source=source)),
    ):
        with pytest.raises(tools.ToolError) as raised:
            call()
        assert value not in raised.value.message
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None


def test_a_declared_type_the_library_cannot_check_is_masked_in_meta(
    make_source: Callable[..., tuple[LlmSource, Any]],
) -> None:
    source, _ = make_source(json.dumps([{"country": "France"}]))
    answer = tools.dispatch(
        "llmdb_query",
        {"query": "who?", "attrs": [{"name": "country", "type": STAMPED}]},
        source=source,
    )

    assert answer["meta"]["unchecked_types"] == ["***"]
    assert STAMPED not in json.dumps(answer)


def test_an_estimate_reason_uses_fixed_guidance(
    make_source: Callable[..., tuple[LlmSource, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Estimate reasons bypass the exception reporter."""
    source, client = make_source()

    def refuse(prompt: str, properties: dict[str, Any]) -> float:
        raise ConfigError(f"no published prices for {STAMPED!r}")

    monkeypatch.setattr(client, "bound_for_attempt", refuse)
    answer = tools.dispatch("llmdb_estimate", {"query": "who?"}, source=source)

    assert answer["bounded"] is False
    assert STAMPED not in json.dumps(answer)
    assert answer["reason"] == "Model pricing is unavailable; set finite input and output rates."


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (
            "llm.price_per_input_token and llm.price_per_output_token are invalid: 27183",
            "Set finite positive input and output rates.",
        ),
        (
            "limits.max_cost needs a finite positive llm.max_tokens: 27183",
            "A finite positive output allowance is required to bound cost.",
        ),
        (
            "unclassified cost error: 27183",
            "A cost bound is unavailable; check pricing, output allowance, or client cost support.",
        ),
    ],
)
def test_estimate_reason_distinguishes_safe_causes_without_echoing_values(
    reason: str, expected: str
) -> None:
    summary = tools.estimate_summary(Estimate(reason=reason))
    assert summary["reason"] == expected
    assert "27183" not in json.dumps(summary)


def test_escaped_private_model_name_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    private_model = r"private\canary"
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", private_model)
    text = f"pricing unavailable for {private_model!r}; {json.dumps(private_model)}"

    assert tools.conceal(text) == "pricing unavailable for ***; ***"

    estimate = tools.dispatch("llmdb_estimate", {"query": "list two names"})
    assert estimate["bounded"] is False
    assert estimate["model"] == MASK
    assert private_model not in json.dumps(estimate)
    assert repr(private_model) not in json.dumps(estimate)


def test_a_record_value_shaped_like_a_key_is_returned_intact(
    make_source: Callable[..., tuple[LlmSource, Any]],
) -> None:
    source, _ = make_source(json.dumps([{"country": "France", "code": STAMPED}]))
    answer = tools.dispatch("llmdb_query", {"query": "who?"}, source=source)

    assert answer["records"] == [{"country": "France", "code": STAMPED}]


def test_discarded_record_text_is_masked_in_query_metadata(
    make_source: Callable[..., tuple[LlmSource, Any]],
) -> None:
    source, _ = make_source(json.dumps([{"id": "safe", "ID": STAMPED}]))
    answer = tools.dispatch("llmdb_query", {"query": "one", "attrs": ["id"]}, source=source)

    assert answer["records"] == [{"id": "safe"}]
    assert STAMPED not in json.dumps(answer)
    assert answer["meta"]["key_collisions"][0]["ignored_value"] == MASK


# Probe every tool and classify each cleartext result path.

REQUEST: dict[str, Any] = {
    "query": STAMPED,
    "context": STAMPED,
    "attrs": [{"name": STAMPED, "type": STAMPED}],
}

PROBES: dict[str, dict[str, Any]] = {
    tools.QUERY: dict(REQUEST),
    tools.ESTIMATE: dict(REQUEST),
    tools.LIST_MODELS: {},
    tools.INSPECT_MODEL: {"model": STAMPED},
    tools.INSPECT_METADATA: {},
}

ANSWER = json.dumps([{STAMPED: STAMPED, "capital": STAMPED}])


def published_strings(node: object, path: str = "") -> Iterator[tuple[str, str]]:
    """Yield strings with stable field paths, including dictionary keys."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, list):
        for item in cast("list[object]", node):
            yield from published_strings(item, f"{path}[]")
    elif isinstance(node, dict):
        for key, value in cast("dict[str, object]", node).items():
            yield f"{path}{{}}", key
            step = "{}" if STAMPED in key else key
            yield from published_strings(value, f"{path}.{step}" if path else step)


UNMASKED: dict[str, str] = {
    "llmdb_query{}": "the result's own field names, written as literals in `tools`.",
    "llmdb_query.records[]{}": "a key the model returned. The records are the answer.",
    "llmdb_query.records[].{}": "a value the model returned. The records are the answer.",
    "llmdb_query.meta{}": "the view's own field names, written as literals in the library.",
    "llmdb_query.meta.outcome": "a library outcome constant.",
    "llmdb_query.meta.record_origins[][]": "plan-generated leaf ids, parallel to the records.",
    "llmdb_query.meta.unmapped_keys[]": "names a key the model returned, which is not masked.",
    "llmdb_estimate{}": "the result's own field names, written as literals in `tools`.",
    "llmdb_estimate.reason": "fixed guidance written in `tools`.",
    "llmdb_list_models{}": "the result's own field name.",
    "llmdb_list_models.models[]": "this package's own price-table names. Nothing arrives here.",
    "llmdb_inspect_model{}": "the result's own field names, written as literals in `tools`.",
    "llmdb_inspect_metadata{}": "the result's own field names, written as literals in `tools`.",
    "llmdb_inspect_metadata.protocol": "a library constant naming the source family.",
    "llmdb_inspect_metadata.variant": "a library constant naming the provider variant.",
    "llmdb_inspect_metadata.version": "the library's own version string.",
}


def test_every_string_a_tool_publishes_is_masked_or_has_a_written_reason(
    make_source: Callable[..., tuple[LlmSource, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert set(PROBES) == {spec.name for spec in tools.TOOLS}, "missing tool probe"

    open_paths: set[str] = set()
    for name, arguments in PROBES.items():
        source, client = make_source(ANSWER, model=STAMPED)
        source.properties["llm.model"] = STAMPED

        def refuse(prompt: str, properties: dict[str, Any]) -> float:
            # Expose the estimate error as a result field.
            raise ConfigError(f"no published prices for {STAMPED!r}")

        monkeypatch.setattr(client, "bound_for_attempt", refuse)
        answer = tools.dispatch(name, dict(arguments), source=source)
        open_paths |= {path for path, value in published_strings(answer, name) if MASK not in value}

    # Equality detects both new unmasked fields and stale exceptions.
    assert open_paths == set(UNMASKED)
