"""A sentinel put into every place a string can enter a tool, looked for in every result.

A tool argument is whatever a client sent, and an agent that pastes a credential into one
has already made its mistake. What this server must not do is hand it back, where it lands
in a transcript that outlives the call. The entries are a tool name, an argument value, a
declared attribute type, an unknown key and a provider exception; the exits are the error
text and the JSON payload.

Nothing here contacts a provider: the tools are called as functions over the fake client,
which is what the server does to them.

Every literal below is a sentinel, not a credential.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any, cast

import pytest
from blue_data_llm_source import ConfigError, LlmSource, TransportError
from blue_data_llm_source.redaction import MASK

from blue_data_llm_mcp import tools

STAMPED = "sk-canary-NOTAKEY"
"""A key literal in the shape a provider stamps one with."""


def test_an_echoed_model_name_is_masked(source: LlmSource) -> None:
    """The tool answers about a model the caller named, and names it back."""
    answer = tools.dispatch("llmdb_inspect_model", {"model": STAMPED}, source=source)

    assert STAMPED not in json.dumps(answer)
    assert answer["model"] == "***"
    assert answer["priceable"] is False


def test_a_value_outside_a_declared_set_is_not_read_back() -> None:
    """The accepted values are the part the caller cannot work out; their own string is not."""
    with pytest.raises(tools.ToolError) as raised:
        tools.dispatch("llmdb_query", {"query": "who?", "plan.mode": STAMPED})

    message = raised.value.message
    assert STAMPED not in message
    assert "flat" in message and "partitioned" in message and "row_by_row" in message


def test_a_blank_value_is_refused_without_reading_it_back() -> None:
    """The schema declares the pattern the library enforces, so both refuse the same call.

    `attrs` answers with the shape message rather than the pattern one, because it is a
    `oneOf` and a branch's own refusal is what the checker discards to try the next branch.
    """
    with pytest.raises(tools.ToolError, match="must not be blank"):
        tools.dispatch("llmdb_query", {"query": "who?", "plan.key": "   "})

    with pytest.raises(tools.ToolError, match="does not match any accepted shape"):
        tools.dispatch("llmdb_query", {"query": "who?", "attrs": ["   "]})

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
    """A misspelled name is quoted on purpose, because it is what the caller has to find.

    Quoting it is the deliberate half of the decision. Masking it is the other half.
    """
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


def test_a_declared_type_the_library_cannot_check_is_masked_in_meta(
    make_source: Callable[..., tuple[LlmSource, Any]],
) -> None:
    """`meta.unchecked_types` reports the caller's own text, so it leaves masked."""
    source, _ = make_source(json.dumps([{"country": "France"}]))
    answer = tools.dispatch(
        "llmdb_query",
        {"query": "who?", "attrs": [{"name": "country", "type": STAMPED}]},
        source=source,
    )

    assert answer["meta"]["unchecked_types"] == ["***"]
    assert STAMPED not in json.dumps(answer)


def test_an_estimate_reason_is_masked(
    make_source: Callable[..., tuple[LlmSource, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`reason` is a library message published as a field rather than raised.

    Nothing else about the call fails, so the error boundary never sees it.
    """
    source, client = make_source()

    def refuse(prompt: str, properties: dict[str, Any]) -> float:
        raise ConfigError(f"no published prices for {STAMPED!r}")

    monkeypatch.setattr(client, "bound_for_attempt", refuse)
    answer = tools.dispatch("llmdb_estimate", {"query": "who?"}, source=source)

    assert answer["bounded"] is False
    assert STAMPED not in json.dumps(answer)
    assert "***" in str(answer["reason"])


def test_a_record_value_shaped_like_a_key_is_returned_intact(
    make_source: Callable[..., tuple[LlmSource, Any]],
) -> None:
    """The other side of the boundary. Records are model output, which is the answer."""
    source, _ = make_source(json.dumps([{"country": "France", "code": STAMPED}]))
    answer = tools.dispatch("llmdb_query", {"query": "who?"}, source=source)

    assert answer["records"] == [{"country": "France", "code": STAMPED}]


# ------------------------------------------------------- the whole result set --
# The tests above name the fields, and a list of fields is only ever right about the surface
# it was written against. So this enumerates the surface instead: every tool is called with
# the sentinel in every place a string can enter it, and every string in what comes back has
# to be masked or to be named below with a reason. A tool added to `TOOLS` needs a probe here
# before this can run at all.

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
"""Arguments carrying the sentinel wherever each tool's schema accepts a string."""

ANSWER = json.dumps([{STAMPED: STAMPED, "capital": STAMPED}])
"""One record under the key the caller asked for, and one under a key it did not."""


def published_strings(node: object, path: str = "") -> Iterator[tuple[str, str]]:
    """Every string in *node*, each with a path naming its field rather than its value.

    A list index collapses to `[]`. A dict key is itself published, so it is yielded at
    `{}` under its container; a key holding data rather than a field name spells its own
    step `{}` too, which keeps the path stable whatever arrived under it.
    """
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
    "llmdb_list_models{}": "the result's own field name.",
    "llmdb_list_models.models[]": "this package's own price-table names. Nothing arrives here.",
    "llmdb_inspect_model{}": "the result's own field names, written as literals in `tools`.",
    "llmdb_inspect_metadata{}": "the result's own field names, written as literals in `tools`.",
    "llmdb_inspect_metadata.protocol": "a library constant naming the source family.",
    "llmdb_inspect_metadata.variant": "a library constant naming the provider variant.",
    "llmdb_inspect_metadata.version": "the library's own version string.",
}
"""Every string these tools publish without masking, and why each one has to stay.

Field names and library constants are text this package wrote, and there is nothing in them
to mask. The rest is, or indexes, the records: this source returns model output as data, and
a diagnostic naming a key masked here would describe a record that still carries that key.

The `meta` paths are the ones one fetch reaches. The library's own canary saturates
`ExecMeta` from its annotations and holds the whole of that view.
"""


def test_every_string_a_tool_publishes_is_masked_or_has_a_written_reason(
    make_source: Callable[..., tuple[LlmSource, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The keeper is the boundary itself, not the fields that were on it when it was written."""
    assert set(PROBES) == {spec.name for spec in tools.TOOLS}, (
        "every tool needs a probe here, or it is not on this boundary check at all"
    )

    open_paths: set[str] = set()
    for name, arguments in PROBES.items():
        source, client = make_source(ANSWER, model=STAMPED)
        source.properties["llm.model"] = STAMPED

        def refuse(prompt: str, properties: dict[str, Any]) -> float:
            # So `estimate` answers with a `reason` instead of a bound, which is the one
            # library message this surface returns as a field rather than raising.
            raise ConfigError(f"no published prices for {STAMPED!r}")

        monkeypatch.setattr(client, "bound_for_attempt", refuse)
        answer = tools.dispatch(name, dict(arguments), source=source)
        open_paths |= {path for path, value in published_strings(answer, name) if MASK not in value}

    # Equality both ways: an unlisted path is a field published in the clear that nobody
    # decided about, and a listed path that no longer appears is a reason for a rule that
    # is no longer there.
    assert open_paths == set(UNMASKED)
