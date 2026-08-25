"""The tool functions, called the way the server calls them."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from blue_data_llm_source import (
    BudgetExceeded,
    ConfigError,
    TransportError,
)
from blue_data_llm_source import __version__ as library_version

from blue_data_llm_mcp import tools

QUERY = "the member states of the European Union"

MESSY_RECORDS = json.dumps(
    [
        {"Country": "France", "capital": "Paris"},
        {"Country": "Germany", "capital": "Berlin"},
    ]
)


def test_llm_fetch_returns_records_and_meta(source: Any) -> None:
    payload = tools.llm_fetch({"query": QUERY}, source=source)
    assert set(payload) == {"records", "meta"}
    assert payload["records"][0]["country"] == "France"
    assert payload["meta"]["calls"] == 1
    assert payload["meta"]["model"] == "fake-model"
    assert payload["meta"]["prompt_tokens"] == 12
    assert payload["meta"]["completion_tokens"] == 34
    assert payload["meta"]["cost"] == 0.0012
    assert "elapsed_seconds" in payload["meta"]


def test_llm_fetch_accepts_attribute_names(source: Any) -> None:
    payload = tools.llm_fetch({"query": QUERY, "attrs": ["country"]}, source=source)
    assert payload["records"] == [{"country": "France"}, {"country": "Germany"}]


def test_llm_fetch_accepts_attribute_objects(source: Any) -> None:
    payload = tools.llm_fetch(
        {
            "query": QUERY,
            "attrs": [{"name": "country"}, {"name": "accession_year", "type": "int"}],
        },
        source=source,
    )
    assert payload["records"][0] == {"country": "France", "accession_year": 1958}


def test_llm_fetch_passes_context_to_the_model(make_source: Any) -> None:
    source, client = make_source()
    tools.llm_fetch({"query": QUERY, "context": "founding members only"}, source=source)
    assert "founding members only" in client.prompts[0]


def test_llm_fetch_caps_records(source: Any) -> None:
    payload = tools.llm_fetch({"query": QUERY, "max_records": 1}, source=source)
    assert len(payload["records"]) == 1
    assert payload["meta"]["truncated_to"] == 1
    assert payload["meta"]["stopped_by"] == "max_rows"


def test_llm_fetch_reports_keys_it_could_not_place(make_source: Any) -> None:
    source, _ = make_source(MESSY_RECORDS)
    payload = tools.llm_fetch({"query": QUERY, "attrs": ["country"]}, source=source)
    assert payload["meta"]["unmapped_keys"] == ["capital"]
    assert payload["meta"]["remapped_keys"] == {"Country": "country"}


def test_meta_stays_out_of_the_way_when_there_is_nothing_to_report(source: Any) -> None:
    payload = tools.llm_fetch({"query": QUERY}, source=source)
    for key in ("unmapped_keys", "remapped_keys", "truncated_to", "stopped_by"):
        assert key not in payload["meta"]


def test_llm_fetch_result_is_json_serialisable(source: Any) -> None:
    payload = tools.llm_fetch({"query": QUERY}, source=source)
    assert json.loads(json.dumps(payload)) == payload


def test_a_budget_stop_is_an_empty_result_not_an_error(make_source: Any) -> None:
    source, _ = make_source(BudgetExceeded("cost limit reached (0.5)", limit="max_cost"))
    payload = tools.llm_fetch({"query": QUERY}, source=source)
    assert payload["records"] == []
    assert payload["meta"]["stopped_by"] == "max_cost"


def test_a_missing_query_returns_an_empty_result_with_its_reason(source: Any) -> None:
    """An absent query is an empty ask, and the tool says why rather than erroring.

    An agent reading the result learns that it supplied nothing, which is more useful
    than a raised error it has to interpret.
    """
    payload = tools.llm_fetch({}, source=source)
    assert payload["records"] == []
    assert payload["meta"]["outcome"] == "empty_query"


def test_no_arguments_at_all_returns_an_empty_result_with_its_reason(source: Any) -> None:
    payload = tools.llm_fetch(None, source=source)
    assert payload["records"] == []
    assert payload["meta"]["outcome"] == "empty_query"


def test_malformed_attrs_is_a_tool_error(source: Any) -> None:
    with pytest.raises(tools.ToolError) as caught:
        tools.llm_fetch({"query": QUERY, "attrs": [{"type": "int"}]}, source=source)
    assert caught.value.kind == "QueryError"
    assert "non-empty string 'name'" in caught.value.message


def test_an_unreadable_answer_is_a_parse_error(make_source: Any) -> None:
    source, _ = make_source("I am afraid I cannot answer that.")
    with pytest.raises(tools.ToolError) as caught:
        tools.llm_fetch({"query": QUERY}, source=source)
    assert caught.value.kind == "ParseError"
    assert "not valid JSON" in caught.value.message


def test_a_provider_failure_is_a_transport_error(make_source: Any) -> None:
    source, _ = make_source(TransportError("connection reset by peer", code="provider_error"))
    with pytest.raises(tools.ToolError) as caught:
        tools.llm_fetch({"query": QUERY}, source=source)
    assert caught.value.kind == "TransportError"
    assert caught.value.message == "connection reset by peer"


def test_a_rejected_setting_is_a_config_error(make_source: Any) -> None:
    source, _ = make_source(ConfigError("unknown adapter override 'llm.nope'"))
    with pytest.raises(tools.ToolError) as caught:
        tools.llm_fetch({"query": QUERY}, source=source)
    assert caught.value.kind == "ConfigError"
    assert "unknown adapter override" in caught.value.message


def test_llm_info_reports_the_source_identity(source: Any) -> None:
    assert tools.llm_info(source=source) == {
        "protocol": "llm",
        "variant": "openai",
        "version": library_version,
    }


def test_llm_info_needs_no_source_of_its_own() -> None:
    payload = tools.llm_info()
    assert payload["protocol"] == "llm"
    assert payload["variant"] == "openai"


def test_build_source_makes_a_usable_source() -> None:
    built = tools.build_source()
    assert built.protocol == "llm"
    assert built.variant == "openai"


def test_dispatch_routes_fetch(source: Any) -> None:
    payload = tools.dispatch("llm_fetch", {"query": QUERY}, source=source)
    assert payload["records"][0]["country"] == "France"


def test_dispatch_routes_info(source: Any) -> None:
    assert tools.dispatch("llm_info", None, source=source)["protocol"] == "llm"


def test_dispatch_rejects_an_unknown_tool(source: Any) -> None:
    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch("llm_plan", {"query": QUERY}, source=source)
    assert caught.value.kind == "UnknownTool"
    assert "llm_fetch" in caught.value.message
    assert "llm_info" in caught.value.message


def test_dispatch_covers_every_declared_tool(source: Any) -> None:
    for spec in tools.TOOLS:
        # Each tool gets only what its own schema declares. `llm_info` takes no inputs and
        # says `additionalProperties: false`, so handing it a query is a refusal now.
        args = {"query": QUERY} if "query" in spec.input_schema["properties"] else {}
        assert tools.dispatch(spec.name, args, source=source)


def test_the_withdrawn_rate_key_is_refused_before_the_source_is_touched(
    make_source: Any,
) -> None:
    """A tool call never reaches the library's property validation, so it is checked here.

    The handlers read the fields they recognise and drop the rest, so a caller setting a
    ceiling this build does not support was told nothing and got an unbounded run — the
    exact outcome withdrawing the knob was meant to prevent.
    """
    source, client = make_source()
    args = {"query": QUERY, "llm.rate_limit": 10}

    with pytest.raises(tools.ToolError) as sync_error:
        tools.dispatch("llm_fetch", args, source=source)
    with pytest.raises(tools.ToolError) as async_error:
        asyncio.run(tools.adispatch("llm_fetch", args, source=source))

    for caught in (sync_error, async_error):
        message = str(caught.value)
        assert "llm.rate_limit" in message
        assert "v2.0" in message
        assert "max_parallel" in message
    assert client.usages == [], "the source was reached despite an unsupported argument"


def test_an_unknown_tool_argument_is_refused_rather_than_dropped(source: Any) -> None:
    """The advertised schema forbids extras; nothing was enforcing it."""
    with pytest.raises(tools.ToolError, match="unknown argument"):
        tools.dispatch("llm_fetch", {"query": QUERY, "temperature": 0.5}, source=source)


def test_meta_carries_key_collisions_when_the_model_answers_twice(
    make_source: Any,
) -> None:
    """A discarded answer must reach the agent, like a type mismatch does."""
    source, _ = make_source('[{"First Name": "Ada", "first_name": "Grace"}]')
    result = asyncio.run(tools.allm_fetch({"query": "who?", "attrs": ["first name"]}, source))

    assert result["records"] == [{"first name": "Ada"}]
    assert len(result["meta"]["key_collisions"]) == 1
    assert result["meta"]["key_collisions"][0]["ignored_value"] == "Grace"


def test_meta_omits_key_collisions_when_there_are_none(
    make_source: Any,
) -> None:
    source, _ = make_source('[{"first name": "Ada"}]')
    result = asyncio.run(tools.allm_fetch({"query": "who?", "attrs": ["first name"]}, source))

    assert "key_collisions" not in result["meta"]


def test_a_nested_extra_in_an_attribute_is_refused_on_both_paths(make_source: Any) -> None:
    """The attribute branch closes itself, and checking only the top level missed it.

    A misspelled key inside an attribute object was accepted and then dropped by
    normalisation, so a caller who asked for a declared type silently did not get one, and
    a nested deferred key arrived as silence rather than as the refusal it gets at the top.
    """
    source, client = make_source()
    misspelled = {"query": QUERY, "attrs": [{"name": "capital", "typ": "text"}]}
    nested_deferred = {"query": QUERY, "attrs": [{"name": "capital", "llm.rate_limit": 10}]}

    for args in (misspelled, nested_deferred):
        with pytest.raises(tools.ToolError):
            tools.dispatch("llm_fetch", args, source=source)
        with pytest.raises(tools.ToolError):
            asyncio.run(tools.adispatch("llm_fetch", args, source=source))

    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch("llm_fetch", nested_deferred, source=source)
    assert "v2.0" in str(caught.value)
    assert "max_parallel" in str(caught.value)
    assert client.usages == [], "the source was reached despite an invalid attribute"


def test_a_well_formed_attribute_object_still_passes(source: Any) -> None:
    """The declared shape must keep working; this check refuses extras, not attributes."""
    result = tools.dispatch(
        "llm_fetch", {"query": QUERY, "attrs": [{"name": "capital", "type": "text"}]}, source=source
    )
    # What matters is that the declared shape reached the source at all. Which records come
    # back is the fixture's business, not this check's.
    assert "records" in result
    assert "meta" in result


def test_an_integral_float_satisfies_an_integer_field(source: Any) -> None:
    """JSON has one number type, so 2 and 2.0 are the same value on the wire.

    The advertised inputSchema declares `max_records` as an integer, and a client that
    generated its arguments from that schema and sent 2.0 was refused by this server for a
    value its own published contract accepts. Narrowed rather than merely permitted, so the
    library downstream still receives an int.
    """
    payload = tools.dispatch("llm_fetch", {"query": QUERY, "max_records": 2.0}, source=source)
    assert len(payload["records"]) <= 2

    # A fractional value is still a mistake and is still named as one.
    with pytest.raises(tools.ToolError, match="must be an integer"):
        tools.dispatch("llm_fetch", {"query": QUERY, "max_records": 2.5}, source=source)
    # And a bool is never what a count meant, int subclass or not.
    with pytest.raises(tools.ToolError, match="must be an integer"):
        tools.dispatch("llm_fetch", {"query": QUERY, "max_records": True}, source=source)


def test_an_uncheckable_declared_type_reaches_the_mcp_caller() -> None:
    """Same field, same reasoning, and this adapter discarded it too."""
    from blue_data_llm_source import ExecMeta

    meta = ExecMeta(outcome="ok", unchecked_types=["money"])
    assert tools.meta_summary(meta)["unchecked_types"] == ["money"]
    assert "unchecked_types" not in tools.meta_summary(ExecMeta(outcome="ok"))
