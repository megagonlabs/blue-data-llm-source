"""The declared tools and their input schemas."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from blue_data_llm_mcp import tools

SPECS = list(tools.TOOLS)


def test_exactly_three_tools_matching_the_three_library_ops() -> None:
    assert [spec.name for spec in SPECS] == ["llm_fetch", "llm_estimate", "llm_info"]


def test_every_tool_is_described() -> None:
    for spec in SPECS:
        assert spec.description.strip()
        assert spec.name.strip()


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_input_schema_is_a_closed_object(spec: tools.ToolSpec) -> None:
    schema = spec.input_schema
    assert schema["type"] == "object"
    assert isinstance(schema["properties"], dict)
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_required_names_only_declared_properties(spec: tools.ToolSpec) -> None:
    required = spec.input_schema.get("required", [])
    assert set(required) <= set(spec.input_schema["properties"])


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_every_property_has_a_type_and_a_description(spec: tools.ToolSpec) -> None:
    for name, prop in spec.input_schema["properties"].items():
        assert "type" in prop or "oneOf" in prop, name
        assert prop.get("description", "").strip(), name


def test_plan_descriptions_do_not_understate_nonflat_calls() -> None:
    mode = tools.PLAN_PROPERTIES["plan.mode"]["description"]
    continuations = tools.PLAN_PROPERTIES["plan.max_continuations"]["description"]

    assert "bounded planning before leaf extraction" in mode
    assert "not provider attempts or planning calls" in continuations
    assert "the call that planned it" not in continuations


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_input_schema_survives_json(spec: tools.ToolSpec) -> None:
    assert json.loads(json.dumps(spec.input_schema)) == spec.input_schema


def test_fetch_schema_declares_the_fields_the_server_accepts() -> None:
    schema = tools.FETCH_INPUT_SCHEMA
    # Nothing is required: the server treats a missing query as `empty_query` and says so
    # in its README, so declaring it required described a rule nobody enforced.
    assert schema["required"] == []
    assert set(schema["properties"]) == {
        "query",
        "attrs",
        "context",
        "max_records",
        "plan.mode",
        "plan.max_continuations",
        "plan.max_leaves",
        "plan.key",
        "plan.keys",
        "plan.exact_rows",
        "plan.max_exclusion_bytes",
        "plan.partition_key",
        "plan.max_depth",
        "plan.max_partitions",
        "plan.target_rows_per_leaf",
        "plan.schema_mode",
        "plan.planning_order",
        "plan.expose_estimate",
        "limits.max_cost",
        "limits.max_seconds",
        "limits.max_calls",
    }
    assert schema["properties"]["max_records"]["type"] == "integer"
    assert schema["properties"]["attrs"]["type"] == "array"
    assert schema["properties"]["attrs"]["items"] == tools.ATTR_SCHEMA


def test_estimate_schema_declares_the_request_and_the_ceilings() -> None:
    """The same request as a fetch, priced rather than run, and no row cap.

    `estimate` takes no `max_records`, so advertising one would offer an input that cannot
    change the answer — and a row cap bounds the result, not the spend, which is the thing
    a caller reaches for this tool to find out about.
    """
    schema = tools.ESTIMATE_INPUT_SCHEMA
    assert schema["required"] == []
    assert set(schema["properties"]) == {
        "query",
        "attrs",
        "context",
        "limits.max_cost",
        "limits.max_seconds",
        "limits.max_calls",
    }
    assert "max_records" not in schema["properties"]


def test_the_ceilings_are_named_the_way_the_library_names_them() -> None:
    """The keys are property names the library reads, not an interface vocabulary.

    Renaming them here would not be a translation; nothing downstream would recognise the
    result. A tool argument the handlers do not pass on never reaches the library's own key
    validation, so a misspelling arrives as an unbounded run rather than as a refusal.
    """
    from dataclasses import fields

    from blue_data_llm_source import Limits

    assert set(tools.LIMIT_KEYS) <= {f"limits.{field.name}" for field in fields(Limits)}
    assert set(tools.LIMIT_KEYS) == {"limits.max_cost", "limits.max_seconds", "limits.max_calls"}
    assert tools.LIMIT_PROPERTIES["limits.max_cost"]["type"] == "number"
    assert tools.LIMIT_PROPERTIES["limits.max_seconds"]["type"] == "number"
    assert tools.LIMIT_PROPERTIES["limits.max_calls"]["type"] == "integer"


def test_attr_schema_allows_a_name_or_a_name_and_type() -> None:
    forms = tools.ATTR_SCHEMA["oneOf"]
    assert forms[0]["type"] == "string"
    assert forms[1]["type"] == "object"
    assert forms[1]["required"] == ["name"]
    assert set(forms[1]["properties"]) == {"name", "type"}


def test_info_schema_takes_no_input() -> None:
    assert tools.INFO_INPUT_SCHEMA["properties"] == {}
    assert "required" not in tools.INFO_INPUT_SCHEMA


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_input_schema_is_valid_json_schema(spec: tools.ToolSpec) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(spec.input_schema)


@pytest.mark.parametrize(
    "instance",
    [
        {"query": "the member states of the European Union"},
        {"query": "q", "attrs": ["country"]},
        {"query": "q", "attrs": [{"name": "country"}, {"name": "year", "type": "int"}]},
        {"query": "q", "context": "founding members only"},
        {"query": "q", "max_records": 5},
        {"query": "q", "limits.max_cost": 0.5},
        # An integer where a number is declared. JSON has one number type, and a client
        # generating arguments from this schema is entitled to send either spelling.
        {"query": "q", "limits.max_seconds": 30},
        {"query": "q", "limits.max_calls": 2},
        {"query": "q", "limits.max_cost": 0.5, "limits.max_seconds": 30, "limits.max_calls": 2},
    ],
)
def test_fetch_schema_accepts_a_well_formed_call(instance: dict[str, Any]) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(instance, tools.FETCH_INPUT_SCHEMA)


@pytest.mark.parametrize(
    "instance",
    [
        {"query": 7},
        {"query": "q", "attrs": "country"},
        {"query": "q", "attrs": [{"type": "int"}]},
        {"query": "q", "max_records": 0},
        {"query": "q", "model": "gpt-4o"},
    ],
)
def test_fetch_schema_rejects_a_malformed_call(instance: dict[str, Any]) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance, tools.FETCH_INPUT_SCHEMA)


def test_an_empty_call_validates_because_the_server_accepts_one() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate({}, tools.FETCH_INPUT_SCHEMA)


def test_the_fetch_schema_requires_nothing_because_the_server_enforces_nothing() -> None:
    """A required key the server does not enforce is a contract two readers disagree on.

    The README says a missing query succeeds with `outcome: empty_query`, and it does. The
    schema said `query` was required, so a generated client and a direct caller would have
    disagreed about the tool surface.
    """
    assert tools.FETCH_INPUT_SCHEMA["required"] == []
    assert "query" in tools.FETCH_INPUT_SCHEMA["properties"]


def test_a_call_with_no_query_succeeds_as_the_readme_says(source: Any) -> None:
    result = tools.dispatch("llm_fetch", {}, source=source)
    assert result["meta"]["outcome"] == "empty_query"


def test_our_validator_rejects_everything_the_schema_rejects() -> None:
    """The SDK's prevalidator is off, so this check has to do the whole job.

    Every instance the shipped schema calls malformed must also be refused here. Otherwise
    turning off the prevalidator to recover a useful message would quietly widen what the
    server accepts.
    """
    jsonschema = pytest.importorskip("jsonschema")
    malformed = [
        {"query": 7},
        {"query": "q", "attrs": "country"},
        {"query": "q", "attrs": [{"type": "int"}]},
        {"query": "q", "max_records": 0},
        {"query": "q", "model": "gpt-4o"},
        {"query": "q", "attrs": [{"name": "a", "extra": 1}]},
        {"query": "q", "max_records": True},
        {"query": "q", "context": 5},
        {"query": "q", "limits.max_cost": "0.50"},
        {"query": "q", "limits.max_cost": -1},
        {"query": "q", "limits.max_seconds": -0.5},
        # A flag is never a ceiling, int subclass or not, and the schema validators a client
        # generates arguments against refuse it too.
        {"query": "q", "limits.max_cost": True},
        {"query": "q", "limits.max_calls": 1.5},
        {"query": "q", "limits.max_calls": -1},
        {"query": "q", "limits.max_rows": 5},
    ]
    for instance in malformed:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance, tools.FETCH_INPUT_SCHEMA)
        with pytest.raises(tools.ToolError):
            tools._check_arguments("llm_fetch", instance)  # pyright: ignore[reportPrivateUsage]


def test_our_validator_accepts_everything_the_schema_accepts() -> None:
    """The other direction, which is where a ceiling would be lost.

    A checker stricter than the published schema refuses calls a client built correctly
    from that schema, and the caller's remedy is to stop setting the ceiling. That is how a
    bound request becomes an unbounded one, so the two have to agree both ways.
    """
    jsonschema = pytest.importorskip("jsonschema")
    well_formed: list[tuple[str, dict[str, Any]]] = [
        ("llm_fetch", {"query": "q", "limits.max_cost": 0.5, "max_records": 2}),
        ("llm_fetch", {"query": "q", "limits.max_seconds": 30, "limits.max_calls": 1}),
        # Zero on every axis. The library accepts it and it refuses every call, which is a
        # ceiling a caller may reasonably set while sizing something.
        ("llm_fetch", {"limits.max_cost": 0, "limits.max_seconds": 0, "limits.max_calls": 0}),
        ("llm_estimate", {"query": "q", "attrs": ["country"], "limits.max_calls": 1}),
        ("llm_estimate", {}),
    ]
    for name, instance in well_formed:
        schema = next(spec.input_schema for spec in SPECS if spec.name == name)
        jsonschema.validate(instance, schema)
        tools._check_arguments(name, dict(instance))  # pyright: ignore[reportPrivateUsage]


def test_the_schemas_use_only_constructs_the_validator_covers() -> None:
    """A guard on the guard: a new keyword in a schema would not be enforced silently."""
    covered = {
        "type",
        "description",
        "properties",
        "items",
        "required",
        "additionalProperties",
        "minimum",
        "anyOf",
        "oneOf",
    }

    def walk(node: object) -> None:
        if isinstance(node, dict):
            entry = cast("dict[str, Any]", node)
            unknown = set(entry) - covered
            assert not unknown, f"schema uses {sorted(unknown)}, which _check_value ignores"
            for key, value in entry.items():
                if key in {"properties"}:
                    for sub in cast("dict[str, Any]", value).values():
                        walk(sub)
                elif key in {"items", "anyOf", "oneOf"}:
                    walk(value)
        elif isinstance(node, list):
            for item in cast("list[Any]", node):
                walk(item)

    for spec in SPECS:
        walk(spec.input_schema)


def test_the_schemas_declare_only_types_the_validator_enforces() -> None:
    """The same guard, on the other half of a property declaration.

    The keyword guard above would not have noticed `"type": "number"`: the keyword is
    covered, and `_check_value` had no branch for that type, so every value of a
    number-typed field would have passed straight through unchecked. A ceiling is exactly
    the field where that matters.
    """
    enforced = {"string", "integer", "number", "boolean", "array", "object"}
    declared: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            entry = cast("dict[str, Any]", node)
            for key, value in entry.items():
                if key == "type" and isinstance(value, str):
                    declared.add(value)
                elif key == "properties":
                    for sub in cast("dict[str, Any]", value).values():
                        walk(sub)
                elif key in {"items", "anyOf", "oneOf"}:
                    walk(value)
        elif isinstance(node, list):
            for item in cast("list[Any]", node):
                walk(item)

    for spec in SPECS:
        walk(spec.input_schema)

    # The attribute objects declare `"type": "string"` for a field *named* type, which is
    # data rather than a schema keyword; it is in the enforced set either way.
    assert declared <= enforced, f"schema declares {sorted(declared - enforced)}, unenforced"
    assert "number" in declared, "the ceilings are gone, or are no longer numbers"


@pytest.mark.parametrize("root", [[], "", 0, False, 7, "text", ["a"]])
def test_a_non_object_argument_root_is_refused_on_both_dispatchers(root: Any) -> None:
    """The schemas declare an object root, and `arguments or {}` quietly accepted otherwise.

    Falsy non-mappings folded into an empty object, so a caller violating the annotated
    root type got a successful empty-query or info result rather than the validation the
    schema advertises. `None` remains the explicit no-arguments sentinel.
    """
    for tool in [spec.name for spec in SPECS]:
        with pytest.raises(tools.ToolError, match="must be an object"):
            tools.dispatch(tool, root)  # pyright: ignore[reportArgumentType]
        with pytest.raises(tools.ToolError, match="must be an object"):
            asyncio.run(tools.adispatch(tool, root))  # pyright: ignore[reportArgumentType]


def test_no_arguments_at_all_is_still_accepted(source: Any) -> None:
    """None is how a client says it sent nothing, and both tools accept that."""
    assert tools.dispatch("llm_info", None, source=source)
    assert tools.dispatch("llm_fetch", None, source=source)["meta"]["outcome"] == "empty_query"
