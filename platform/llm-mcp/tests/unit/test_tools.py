"""The tool functions, called the way the server calls them."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
from blue_data_llm_source import (
    BudgetExceeded,
    ConfigError,
    ExecMeta,
    FetchResult,
    TransportError,
    meta_view,
)
from blue_data_llm_source import __version__ as library_version

from blue_data_llm_mcp import tools

QUERY = "the member states of the European Union"
README = Path(__file__).resolve().parents[2] / "README.md"

MESSY_RECORDS = json.dumps(
    [
        {"Country": "France", "capital": "Paris"},
        {"Country": "Germany", "capital": "Berlin"},
    ]
)


def test_llmdb_query_returns_records_and_meta(source: Any) -> None:
    payload = tools.llmdb_query({"query": QUERY}, source=source)
    assert set(payload) == {"records", "meta"}
    assert payload["records"][0]["country"] == "France"
    assert payload["meta"]["calls"] == 1
    assert payload["meta"]["model"] == "fake-model"
    assert payload["meta"]["prompt_tokens"] == 12
    assert payload["meta"]["completion_tokens"] == 34
    assert payload["meta"]["cost"] == 0.0012
    assert "elapsed_seconds" in payload["meta"]


def test_the_readme_result_sample_matches_the_default_serializer_shape() -> None:
    body = README.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", body, re.DOTALL)
    sample = next(json.loads(block) for block in blocks if '"records"' in block)
    documented = sample["meta"]
    actual = meta_view(ExecMeta(), verbose=True)

    assert set(documented) == set(actual)
    for field, value in actual.items():
        assert type(documented[field]) is type(value), field


def test_llmdb_query_accepts_attribute_names(source: Any) -> None:
    payload = tools.llmdb_query({"query": QUERY, "attrs": ["country"]}, source=source)
    assert payload["records"] == [{"country": "France"}, {"country": "Germany"}]


def test_llmdb_query_accepts_attribute_objects(source: Any) -> None:
    payload = tools.llmdb_query(
        {
            "query": QUERY,
            "attrs": [{"name": "country"}, {"name": "accession_year", "type": "int"}],
        },
        source=source,
    )
    assert payload["records"][0] == {"country": "France", "accession_year": 1958}


def test_llmdb_query_passes_context_to_the_model(make_source: Any) -> None:
    source, client = make_source()
    tools.llmdb_query({"query": QUERY, "context": "founding members only"}, source=source)
    assert "founding members only" in client.prompts[0]


def test_llmdb_query_caps_records(source: Any) -> None:
    payload = tools.llmdb_query({"query": QUERY, "max_records": 1}, source=source)
    assert len(payload["records"]) == 1
    assert payload["meta"]["truncated_to"] == 1
    assert payload["meta"]["stopped_by"] == "max_rows"


def test_llmdb_query_reports_keys_it_could_not_place(make_source: Any) -> None:
    source, _ = make_source(MESSY_RECORDS)
    payload = tools.llmdb_query({"query": QUERY, "attrs": ["country"]}, source=source)
    assert payload["meta"]["unmapped_keys"] == ["capital"]
    assert payload["meta"]["remapped_keys"] == {"Country": "country"}


def test_meta_stays_out_of_the_way_when_there_is_nothing_to_report(source: Any) -> None:
    payload = tools.llmdb_query({"query": QUERY}, source=source)
    for key in ("unmapped_keys", "remapped_keys", "truncated_to", "stopped_by"):
        assert key not in payload["meta"]


def test_llmdb_query_result_is_json_serialisable(source: Any) -> None:
    payload = tools.llmdb_query({"query": QUERY}, source=source)
    assert json.loads(json.dumps(payload)) == payload


def test_a_budget_stop_is_an_empty_result_not_an_error(make_source: Any) -> None:
    source, _ = make_source(BudgetExceeded("cost limit reached (0.5)", limit="max_cost"))
    payload = tools.llmdb_query({"query": QUERY}, source=source)
    assert payload["records"] == []
    assert payload["meta"]["stopped_by"] == "max_cost"


def test_a_missing_query_returns_an_empty_result_with_its_reason(source: Any) -> None:
    """An absent query is an empty ask, and the tool says why rather than erroring.

    An agent reading the result learns that it supplied nothing, which is more useful
    than a raised error it has to interpret.
    """
    payload = tools.llmdb_query({}, source=source)
    assert payload["records"] == []
    assert payload["meta"]["outcome"] == "empty_query"


def test_no_arguments_at_all_returns_an_empty_result_with_its_reason(source: Any) -> None:
    payload = tools.llmdb_query(None, source=source)
    assert payload["records"] == []
    assert payload["meta"]["outcome"] == "empty_query"


def test_malformed_attrs_is_a_tool_error(source: Any) -> None:
    with pytest.raises(tools.ToolError) as caught:
        tools.llmdb_query({"query": QUERY, "attrs": [{"type": "int"}]}, source=source)
    assert caught.value.kind == "QueryError"
    assert "non-empty string 'name'" in caught.value.message


def test_an_unreadable_answer_is_a_parse_error(make_source: Any) -> None:
    source, _ = make_source("I am afraid I cannot answer that.")
    with pytest.raises(tools.ToolError) as caught:
        tools.llmdb_query({"query": QUERY}, source=source)
    assert caught.value.kind == "ParseError"
    assert "not valid JSON" in caught.value.message


def test_a_provider_failure_is_a_transport_error(make_source: Any) -> None:
    source, _ = make_source(TransportError("connection reset by peer", code="provider_error"))
    with pytest.raises(tools.ToolError) as caught:
        tools.llmdb_query({"query": QUERY}, source=source)
    assert caught.value.kind == "TransportError"
    assert caught.value.message == "connection reset by peer"


def test_a_rejected_setting_is_a_config_error(make_source: Any) -> None:
    source, _ = make_source(ConfigError("unknown adapter override 'llm.nope'"))
    with pytest.raises(tools.ToolError) as caught:
        tools.llmdb_query({"query": QUERY}, source=source)
    assert caught.value.kind == "ConfigError"
    assert "unknown adapter override" in caught.value.message


def test_llmdb_inspect_metadata_reports_the_source_identity(source: Any) -> None:
    assert tools.llmdb_inspect_metadata(source=source) == {
        "protocol": "llm",
        "variant": "openai",
        "version": library_version,
    }


def test_llmdb_inspect_metadata_needs_no_source_of_its_own() -> None:
    payload = tools.llmdb_inspect_metadata()
    assert payload["protocol"] == "llm"
    assert payload["variant"] == "openai"


def test_build_source_makes_a_usable_source() -> None:
    built = tools.build_source()
    assert built.protocol == "llm"
    assert built.variant == "openai"


def test_dispatch_routes_fetch(source: Any) -> None:
    payload = tools.dispatch("llmdb_query", {"query": QUERY}, source=source)
    assert payload["records"][0]["country"] == "France"


def test_dispatch_routes_info(source: Any) -> None:
    assert tools.dispatch("llmdb_inspect_metadata", None, source=source)["protocol"] == "llm"


def test_dispatch_rejects_an_unknown_tool(source: Any) -> None:
    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch("llm_plan", {"query": QUERY}, source=source)
    assert caught.value.kind == "UnknownTool"
    assert "llmdb_query" in caught.value.message
    assert "llmdb_inspect_metadata" in caught.value.message


def test_dispatch_covers_every_declared_tool(source: Any) -> None:
    # Each tool gets only what its own schema declares, required keys included. A tool that
    # takes no inputs says `additionalProperties: false`, so handing it a query is a refusal.
    samples: dict[str, Any] = {"query": QUERY, "model": "gpt-4o"}
    for spec in tools.TOOLS:
        declared = spec.input_schema["properties"]
        args = {key: value for key, value in samples.items() if key in declared}
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
        tools.dispatch("llmdb_query", args, source=source)
    with pytest.raises(tools.ToolError) as async_error:
        asyncio.run(tools.adispatch("llmdb_query", args, source=source))

    for caught in (sync_error, async_error):
        message = str(caught.value)
        assert "llm.rate_limit" in message
        assert "v2.0" not in message
        assert "max_parallel" in message
        assert "max_parallel" in message
    assert client.usages == [], "the source was reached despite an unsupported argument"


def test_an_unknown_tool_argument_is_refused_rather_than_dropped(source: Any) -> None:
    """The advertised schema forbids extras; nothing was enforcing it."""
    with pytest.raises(tools.ToolError, match="unknown argument"):
        tools.dispatch("llmdb_query", {"query": QUERY, "temperature": 0.5}, source=source)


def test_meta_carries_key_collisions_when_the_model_answers_twice(
    make_source: Any,
) -> None:
    """A discarded answer must reach the agent, like a type mismatch does."""
    source, _ = make_source('[{"First Name": "Ada", "first_name": "Grace"}]')
    result = asyncio.run(
        tools.llmdb_query_async({"query": "who?", "attrs": ["first name"]}, source)
    )

    assert result["records"] == [{"first name": "Ada"}]
    assert len(result["meta"]["key_collisions"]) == 1
    assert result["meta"]["key_collisions"][0]["ignored_value"] == "Grace"


def test_meta_omits_key_collisions_when_there_are_none(
    make_source: Any,
) -> None:
    source, _ = make_source('[{"first name": "Ada"}]')
    result = asyncio.run(
        tools.llmdb_query_async({"query": "who?", "attrs": ["first name"]}, source)
    )

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
            tools.dispatch("llmdb_query", args, source=source)
        with pytest.raises(tools.ToolError):
            asyncio.run(tools.adispatch("llmdb_query", args, source=source))

    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch("llmdb_query", nested_deferred, source=source)
    assert "v2.0" not in str(caught.value)
    assert "max_parallel" in str(caught.value)
    assert "max_parallel" in str(caught.value)
    assert client.usages == [], "the source was reached despite an invalid attribute"


def test_a_well_formed_attribute_object_still_passes(source: Any) -> None:
    """The declared shape must keep working; this check refuses extras, not attributes."""
    result = tools.dispatch(
        "llmdb_query",
        {"query": QUERY, "attrs": [{"name": "capital", "type": "text"}]},
        source=source,
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
    payload = tools.dispatch("llmdb_query", {"query": QUERY, "max_records": 2.0}, source=source)
    assert len(payload["records"]) <= 2

    # A fractional value is still a mistake and is still named as one.
    with pytest.raises(tools.ToolError, match="must be an integer"):
        tools.dispatch("llmdb_query", {"query": QUERY, "max_records": 2.5}, source=source)
    # And a bool is never what a count meant, int subclass or not.
    with pytest.raises(tools.ToolError, match="must be an integer"):
        tools.dispatch("llmdb_query", {"query": QUERY, "max_records": True}, source=source)


def test_an_uncheckable_declared_type_reaches_the_mcp_caller() -> None:
    """Same field, same reasoning, and this adapter discarded it too."""
    from blue_data_llm_source import ExecMeta

    meta = ExecMeta(outcome="ok", unchecked_types=["money"])
    assert meta_view(meta, verbose=True)["unchecked_types"] == ["money"]
    assert "unchecked_types" not in meta_view(ExecMeta(outcome="ok"), verbose=True)


class TestThePlanNamespaceCrossesTheBoundary:
    """A ceiling or a plan setting renamed at this boundary is read by nothing: the library
    validates the keys it is given, and an argument the handler drops never reaches it, so
    the run proceeds under a default the caller chose against and cannot see."""

    def test_plan_keys_are_advertised(self) -> None:
        advertised = set(tools.QUERY_INPUT_SCHEMA["properties"])
        assert {"plan.mode", "plan.max_continuations", "plan.max_leaves", "plan.key"} <= advertised

    def test_plan_keys_reach_the_source_under_their_own_names(self) -> None:
        seen: dict[str, Any] = {}

        class Recorder:
            async def afetch(self, query: Any, **kwargs: Any) -> Any:
                seen.update(kwargs.get("properties") or {})
                return FetchResult(records=[], meta=ExecMeta())

        asyncio.run(
            tools.llmdb_query_async(
                {"query": "x", "plan.mode": "flat", "plan.max_continuations": 2},
                source=Recorder(),
            )
        )
        assert seen == {"plan.mode": "flat", "plan.max_continuations": 2}

    def test_the_closed_sets_are_advertised_and_enforced(self) -> None:
        """A schema that advertises a closed set the server does not check is a lie to a
        generated client; one that checks a set it does not advertise refuses calls the
        client had no way to build. The enum and the check ship together."""
        assert tools.QUERY_INPUT_SCHEMA["properties"]["plan.mode"]["enum"] == [
            "flat",
            "partitioned",
            "row_by_row",
        ]
        with pytest.raises(tools.ToolError, match="must be one of"):
            tools.dispatch("llmdb_query", {"query": "x", "plan.mode": "recursive"})

    def test_estimate_exposure_requires_a_boolean(self) -> None:
        with pytest.raises(tools.ToolError, match="must be a boolean"):
            tools.dispatch(
                "llmdb_query",
                {"query": "x", "plan.expose_estimate": "false"},
                source=object(),
            )


class TestGapsAreReportedWhenThereAreSome:
    def test_a_gap_reaches_the_tool_result(self) -> None:
        summary = meta_view(ExecMeta(gaps=["leaf_empty: leaf 0.0 returned nothing"]), verbose=True)
        assert summary["gaps"] == ["leaf_empty: leaf 0.0 returned nothing"]

    def test_a_clean_run_carries_an_empty_gaps_key(self) -> None:
        """Present even when empty. A model reading this branches on the contents, not on
        whether the key arrived, and an empty list says nothing was observed to be missing
        -- which is a weaker claim than complete and the only one this package may make."""
        assert meta_view(ExecMeta(), verbose=True)["gaps"] == []


class TestTheErrorRule:
    """One rule, applied to every tool.

    A call that produced no result is an MCP-level error. A call that ran and produced
    something the caller can read is a success, and whatever went wrong with it is a field
    in the payload -- `meta.outcome`, `bounded`, `priceable`.
    """

    def test_every_refusal_this_module_raises_itself_is_named_as_one(self) -> None:
        refusals: list[tuple[str, dict[str, Any]]] = [
            ("llmdb_query", {"query": 7}),
            ("llmdb_query", {"query": "x", "nope": 1}),
            ("llmdb_query", {"query": "x", "max_records": 0}),
            ("llmdb_query", {"query": "x", "plan.mode": "recursive"}),
            ("llmdb_query", {"query": "x", "attrs": [{"type": "int"}]}),
            ("llmdb_query", {"query": "x", "llm.rate_limit": 10}),
            ("llmdb_estimate", {"query": "x", "max_records": 5}),
            ("llmdb_inspect_model", {}),
            ("llmdb_list_models", {"model": "gpt-4o"}),
        ]
        for name, args in refusals:
            with pytest.raises(tools.ToolError) as caught:
                tools.dispatch(name, args)
            assert caught.value.kind == tools.INVALID_ARGUMENTS, (name, args)

    def test_an_unknown_tool_has_its_own_kind_and_names_the_ones_that_exist(self) -> None:
        """Including the names this release removed, which is how a stale client is told."""
        with pytest.raises(tools.ToolError) as caught:
            tools.dispatch("llm_fetch", {})
        assert caught.value.kind == tools.UNKNOWN_TOOL
        assert "llmdb_query" in caught.value.message

    def test_a_library_failure_keeps_the_exception_class_as_its_kind(
        self, make_source: Any
    ) -> None:
        source, _ = make_source(ConfigError("no model configured (set llm.model)"))
        with pytest.raises(tools.ToolError) as caught:
            tools.llmdb_query({"query": QUERY}, source=source)
        assert caught.value.kind == "ConfigError"

    def test_the_two_vocabularies_do_not_overlap(self) -> None:
        """A caller reading the prefix has to be able to tell whose problem it is.

        Both of these used to be `ConfigError`: a refused argument, which the caller fixes,
        and a refused library setting, which the deployment fixes.
        """
        import blue_data_llm_source as library

        exceptions = {
            name
            for name in dir(library)
            if isinstance(getattr(library, name), type)
            and issubclass(getattr(library, name), Exception)
        }
        assert {tools.INVALID_ARGUMENTS, tools.UNKNOWN_TOOL}.isdisjoint(exceptions)

    def test_every_result_is_an_object_and_only_the_query_carries_meta(self, source: Any) -> None:
        samples: dict[str, Any] = {"query": QUERY, "model": "gpt-4o"}
        for spec in tools.TOOLS:
            args = {k: v for k, v in samples.items() if k in spec.input_schema["properties"]}
            payload = tools.dispatch(spec.name, args, source=source)
            assert isinstance(payload, dict), spec.name
            assert json.dumps(payload, default=str)
            assert ("meta" in payload) is (spec.name == tools.QUERY), spec.name


def test_no_tool_hands_back_a_configured_credential(make_source: Any) -> None:
    """`LlmSource` carries connection `user` and `password`; no tool may publish either.

    Nothing reads `properties` today except the model inspection, which takes one key from
    it. This is the guard that keeps a later field from arriving with the rest of the map.
    """
    placeholder = "REDACT-ME-SENTINEL"
    source, _ = make_source()
    source.properties["connection"]["password"] = placeholder
    source.properties["connection"]["user"] = "operator"

    samples: dict[str, Any] = {"query": QUERY, "model": "gpt-4o"}
    for spec in tools.TOOLS:
        args = {k: v for k, v in samples.items() if k in spec.input_schema["properties"]}
        rendered = json.dumps(tools.dispatch(spec.name, args, source=source), default=str)
        assert placeholder not in rendered, spec.name
        assert "password" not in rendered, spec.name


def test_no_tool_accepts_a_credential_as_an_argument() -> None:
    """Every advertised argument name, against the spellings a credential arrives under.

    Bare `key` is not on the list and cannot be: `plan.key`, `plan.keys` and
    `plan.partition_key` are record-identity attributes and have nothing to do with a
    secret.
    """
    shapes = (
        "api_key",
        "apikey",
        "secret",
        "password",
        "passwd",
        "credential",
        "auth_token",
        "access_token",
        "bearer",
    )
    for spec in tools.TOOLS:
        for argument in spec.input_schema["properties"]:
            assert not any(shape in argument.lower() for shape in shapes), argument


def _readme_json_objects() -> list[dict[str, Any]]:
    """Every JSON object the README publishes, so a sample can be compared to a live one."""
    body = README.read_text(encoding="utf-8")
    blocks = cast("list[str]", re.findall(r"```json\n(.*?)\n```", body, re.DOTALL))
    objects: list[dict[str, Any]] = []
    for block in blocks:
        parsed: Any = json.loads(block)
        if isinstance(parsed, dict):
            objects.append(cast("dict[str, Any]", parsed))
    return objects


def test_every_readme_result_sample_is_a_live_tool_result(source: Any) -> None:
    """The README claims to match the live server, so the samples are taken from it.

    Only the fetch sample was pinned before, so the other result shapes could drift by a
    field without anything noticing. Values vary with the model and the run; the field set
    is the contract, and that is what this compares.
    """
    blocks = _readme_json_objects()
    live: dict[str, dict[str, Any]] = {
        "models": tools.dispatch("llmdb_list_models", {}, source=source),
        "priceable": tools.dispatch("llmdb_inspect_model", {"model": "gpt-4o"}, source=source),
        "protocol": tools.dispatch("llmdb_inspect_metadata", {}, source=source),
        "bounded": tools.dispatch("llmdb_estimate", {"query": QUERY}, source=source),
    }
    for marker, actual in live.items():
        documented = next(block for block in blocks if marker in block)
        assert set(documented) == set(actual), marker
        for field, value in actual.items():
            if value is None or documented[field] is None:
                continue
            assert type(documented[field]) is type(value), f"{marker}.{field}"


def test_the_readme_model_listing_is_the_live_one() -> None:
    """Names, not just shape: a model added to the table and not to the README is drift."""
    documented = next(block for block in _readme_json_objects() if "models" in block)
    assert documented["models"] == tools.dispatch("llmdb_list_models", {})["models"]


def test_the_readme_names_every_tool_and_no_removed_one_as_current() -> None:
    body = README.read_text(encoding="utf-8")
    for spec in tools.TOOLS:
        assert f"`{spec.name}`" in body, spec.name
    # The old names appear once, in the sentence that says they were removed.
    for retired in ("llm_fetch", "llm_estimate", "llm_info"):
        assert body.count(retired) == 1, retired
