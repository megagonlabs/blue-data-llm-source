"""The declared tools and their input schemas."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

from blue_data_llm_mcp import tools

SPECS = list(tools.TOOLS)


# The advertised tool names, in the order the server lists them. A rename is a contract
# change: a client's configuration, its saved transcripts and every document in the repo
# name these strings. Pinned here, in the import-boundary reload, and against the live SDK
# listing, so a rename has to be deliberate in three places.
TOOL_NAMES = [
    "llmdb_query",
    "llmdb_estimate",
    "llmdb_list_models",
    "llmdb_inspect_model",
    "llmdb_inspect_metadata",
]


def test_the_advertised_tool_names_are_exactly_these() -> None:
    assert [spec.name for spec in SPECS] == TOOL_NAMES


def test_every_tool_is_prefixed_and_takes_its_verb_from_the_cli() -> None:
    """The naming rule, enforced rather than described.

    `llmdb_` keeps these names out of a shared client namespace where `blue-data-user`
    already exposes a bare `query`. The verb after it is the CLI action that names the same
    operation, so one vocabulary covers both front doors.
    """
    verbs = {"query", "estimate", "list", "inspect"}
    for spec in SPECS:
        assert spec.name.startswith("llmdb_"), spec.name
        assert spec.name.split("_")[1] in verbs, spec.name


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
    schema = tools.QUERY_INPUT_SCHEMA
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
        "plan.leaf_prompt",
        "plan.child_wording",
        "plan.filter_enforcement",
        "plan.range_filters",
        "plan.partition_guardrails",
        "plan.split_policy",
        "plan.split_threshold_rows",
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


def test_the_declared_enums_are_the_library_closed_sets() -> None:
    """The library does not export these, so the adapter spells them and this ties the knot.

    A value added to the library and not here would be advertised as invalid and refused by
    this server before the library ever saw it.
    """
    from blue_data_llm_source.planning.bounds import MODES
    from blue_data_llm_source.planning.context import PLANNING_ORDERS
    from blue_data_llm_source.planning.plan import CHILD_WORDINGS
    from blue_data_llm_source.planning.schema import SCHEMA_MODES
    from blue_data_llm_source.planning.split_gate import SPLIT_POLICIES
    from blue_data_llm_source.prompts import LEAF_PROMPT_VERSIONS
    from blue_data_llm_source.reduction.obedience import FILTER_ENFORCEMENTS

    assert tools.PLAN_MODES == MODES
    assert tools.SCHEMA_MODES == SCHEMA_MODES
    assert tools.PLANNING_ORDERS == PLANNING_ORDERS
    assert tools.LEAF_PROMPTS == LEAF_PROMPT_VERSIONS
    assert tools.CHILD_WORDINGS == CHILD_WORDINGS
    assert tools.FILTER_ENFORCEMENTS == FILTER_ENFORCEMENTS
    assert tools.SPLIT_POLICIES == SPLIT_POLICIES
    properties = tools.QUERY_INPUT_SCHEMA["properties"]
    assert properties["plan.mode"]["enum"] == list(MODES)
    assert properties["plan.schema_mode"]["enum"] == list(SCHEMA_MODES)
    assert properties["plan.planning_order"]["enum"] == list(PLANNING_ORDERS)
    assert properties["plan.leaf_prompt"]["enum"] == list(LEAF_PROMPT_VERSIONS)
    assert properties["plan.child_wording"]["enum"] == list(CHILD_WORDINGS)
    assert properties["plan.filter_enforcement"]["enum"] == list(FILTER_ENFORCEMENTS)
    assert properties["plan.split_policy"]["enum"] == list(SPLIT_POLICIES)


def test_the_open_attribute_type_set_is_not_declared_closed() -> None:
    """An enum here would refuse a declared type the library accepts and reports.

    `unchecked_types` exists precisely so an unrecognised type name is answered rather than
    refused, and the CLI leaves `--attr` types open for the same reason.
    """
    declared = tools.ATTR_SCHEMA["oneOf"][1]["properties"]["type"]
    assert "enum" not in declared
    assert "meta.unchecked_types" in declared["description"]


def test_attr_schema_allows_a_name_or_a_name_and_type() -> None:
    forms = tools.ATTR_SCHEMA["oneOf"]
    assert forms[0]["type"] == "string"
    assert forms[1]["type"] == "object"
    assert forms[1]["required"] == ["name"]
    assert set(forms[1]["properties"]) == {"name", "type"}


@pytest.mark.parametrize(
    "schema", [tools.METADATA_INPUT_SCHEMA, tools.MODELS_INPUT_SCHEMA], ids=["metadata", "models"]
)
def test_a_no_input_schema_still_declares_the_same_shape(schema: dict[str, Any]) -> None:
    """`required: []` spelled out rather than omitted, so five schemas read one way."""
    assert schema["properties"] == {}
    assert schema["required"] == []


def test_the_only_required_argument_on_this_server_is_the_model_to_inspect() -> None:
    required = {spec.name: spec.input_schema["required"] for spec in SPECS}
    assert required == {
        "llmdb_query": [],
        "llmdb_estimate": [],
        "llmdb_list_models": [],
        "llmdb_inspect_model": ["model"],
        "llmdb_inspect_metadata": [],
    }


def test_a_required_argument_is_enforced_and_not_merely_advertised() -> None:
    """Root-level `required` went unchecked until a tool declared one."""
    with pytest.raises(tools.ToolError, match="requires 'model'"):
        tools.dispatch("llmdb_inspect_model", {})


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
    jsonschema.validate(instance, tools.QUERY_INPUT_SCHEMA)


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
        jsonschema.validate(instance, tools.QUERY_INPUT_SCHEMA)


def test_an_empty_call_validates_because_the_server_accepts_one() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate({}, tools.QUERY_INPUT_SCHEMA)


def test_the_fetch_schema_requires_nothing_because_the_server_enforces_nothing() -> None:
    """A required key the server does not enforce is a contract two readers disagree on.

    The README says a missing query succeeds with `outcome: empty_query`, and it does. The
    schema said `query` was required, so a generated client and a direct caller would have
    disagreed about the tool surface.
    """
    assert tools.QUERY_INPUT_SCHEMA["required"] == []
    assert "query" in tools.QUERY_INPUT_SCHEMA["properties"]


def test_a_call_with_no_query_succeeds_as_the_readme_says(source: Any) -> None:
    result = tools.dispatch("llmdb_query", {}, source=source)
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
        {"query": "q", "plan.mode": "flatt"},
        {"query": "q", "plan.schema_mode": "merge"},
        {"query": "q", "plan.planning_order": "schema_first"},
        # Blank and repeated names. The library strips before it looks, so these used to
        # validate against the published schema and be refused a moment later by the runtime.
        {"query": "q", "attrs": ["   "]},
        {"query": "q", "attrs": [{"name": "  "}]},
        {"query": "q", "plan.key": "   "},
        {"query": "q", "plan.partition_key": " "},
        {"query": "q", "plan.keys": ["country", "  "]},
        {"query": "q", "plan.keys": ["country", "country"]},
        {"query": "q", "attrs": ["country", "country"]},
        {"query": "q", "attrs": [{"name": "country"}, {"name": "country"}]},
    ]
    for instance in malformed:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance, tools.QUERY_INPUT_SCHEMA)
        with pytest.raises(tools.ToolError):
            tools._check_arguments("llmdb_query", instance)  # pyright: ignore[reportPrivateUsage]


def test_our_validator_accepts_everything_the_schema_accepts() -> None:
    """The other direction, which is where a ceiling would be lost.

    A checker stricter than the published schema refuses calls a client built correctly
    from that schema, and the caller's remedy is to stop setting the ceiling. That is how a
    bound request becomes an unbounded one, so the two have to agree both ways.
    """
    jsonschema = pytest.importorskip("jsonschema")
    well_formed: list[tuple[str, dict[str, Any]]] = [
        ("llmdb_query", {"query": "q", "limits.max_cost": 0.5, "max_records": 2}),
        ("llmdb_query", {"query": "q", "limits.max_seconds": 30, "limits.max_calls": 1}),
        # Zero on every axis. The library accepts it and it refuses every call, which is a
        # ceiling a caller may reasonably set while sizing something.
        ("llmdb_query", {"limits.max_cost": 0, "limits.max_seconds": 0, "limits.max_calls": 0}),
        ("llmdb_estimate", {"query": "q", "attrs": ["country"], "limits.max_calls": 1}),
        ("llmdb_estimate", {}),
        ("llmdb_query", {"query": "q", "plan.mode": "row_by_row"}),
        ("llmdb_query", {"query": "q", "plan.planning_order": "planning.combined"}),
        ("llmdb_inspect_model", {"model": "gpt-4o"}),
        ("llmdb_list_models", {}),
    ]
    for name, instance in well_formed:
        schema = next(spec.input_schema for spec in SPECS if spec.name == name)
        jsonschema.validate(instance, schema)
        tools._check_arguments(name, dict(instance))  # pyright: ignore[reportPrivateUsage]


def test_a_repeated_attribute_is_refused_in_every_shape_it_can_be_written(
    make_source: Any,
) -> None:
    """The library refuses one attribute named twice, so the schema says so too.

    `uniqueItems` reaches the two same-shape spellings. It cannot reach the mixed pair: the
    rule there is uniqueness of a property projected out of one branch of a `oneOf`, which
    JSON Schema has no keyword for, so a client validating locally still sends it. The
    server refuses it as a bad argument instead, and the model is never reached.
    """
    jsonschema = pytest.importorskip("jsonschema")
    mixed: dict[str, Any] = {"query": "q", "attrs": ["country", {"name": "country"}]}
    jsonschema.validate(mixed, tools.QUERY_INPUT_SCHEMA)

    repeated: list[dict[str, Any]] = [
        {"query": "q", "attrs": ["country", "country"]},
        {"query": "q", "attrs": [{"name": "country", "type": "str"}, {"name": "country"}]},
        mixed,
    ]
    for instance in repeated:
        source, client = make_source()
        with pytest.raises(tools.ToolError) as caught:
            tools.dispatch("llmdb_query", dict(instance), source=source)
        assert caught.value.kind == tools.INVALID_ARGUMENTS
        assert client.prompts == []

        # The handler as the dispatcher calls it, with the argument check stepped over: the
        # library has to refuse the same pair, or this schema would be stricter than the
        # runtime it advertises and would refuse a call the library accepts.
        source, client = make_source()
        with pytest.raises(tools.ToolError) as raised:
            tools.llmdb_query(dict(instance), source=source)
        assert raised.value.kind == "QueryError"
        assert client.prompts == []


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
        "pattern",
        "uniqueItems",
        "enum",
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
    assert tools.dispatch("llmdb_inspect_metadata", None, source=source)
    assert tools.dispatch("llmdb_query", None, source=source)["meta"]["outcome"] == "empty_query"


SPEC = Path(__file__).resolve().parents[4] / "design" / "09-api-interface-spec.md"
"""The interface specification, which embeds this server's tool JSON verbatim."""


def _specified_tools() -> list[dict[str, Any]]:
    """The tool objects the specification publishes, in the order it publishes them.

    A list, not a mapping. Keyed by name straight out of the document, a second block under
    an existing name replaced the first without a word, and the order the reader meets them
    in was never compared to anything.
    """
    body = SPEC.read_text(encoding="utf-8")
    blocks = cast("list[str]", re.findall(r"```json\n(.*?)\n```", body, re.DOTALL))
    objects: list[dict[str, Any]] = []
    for block in blocks:
        parsed: Any = json.loads(block)
        if isinstance(parsed, dict):
            objects.append(cast("dict[str, Any]", parsed))
    return [b for b in objects if "name" in b and "inputSchema" in b]


def test_the_interface_spec_publishes_this_server_verbatim() -> None:
    """The specification embeds machine-readable JSON, so it can be diffed rather than read.

    It had drifted from the code before this release in eight descriptions and in the order
    it emits `limits.*` against `plan.*`, which a reader has no way to detect. Property
    order is compared as well as content, because the document is read top to bottom.
    """
    published_blocks = _specified_tools()
    # Not a skip: the document's value here is that it publishes the schema in a form that
    # can be checked. If it stops doing that, somebody has to decide where the keeper goes.
    assert published_blocks, f"{SPEC.name} no longer embeds any tool JSON for this check to read"

    published_names = [cast("str", block["name"]) for block in published_blocks]
    # Counted and ordered before anything is keyed by name, which is what a mapping built
    # straight from the blocks cannot tell you: two blocks under one name collapse into the
    # later one, and a tool documented out of order reads as a different server.
    assert len(published_names) == len(set(published_names)), (
        f"{SPEC.name} publishes two blocks under one tool name"
    )
    assert len(published_blocks) == len(SPECS)
    assert published_names == [spec.name for spec in SPECS]

    declared: dict[str, dict[str, Any]] = {
        spec.name: {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.input_schema,
        }
        for spec in SPECS
    }
    specified = {cast("str", block["name"]): block for block in published_blocks}
    assert set(specified) == set(declared)
    for name, published in specified.items():
        assert published["description"] == declared[name]["description"], name
        assert published["inputSchema"] == declared[name]["inputSchema"], name
        published_schema = cast("dict[str, Any]", published["inputSchema"])
        declared_schema = cast("dict[str, Any]", declared[name]["inputSchema"])
        published_order = list(cast("dict[str, Any]", published_schema["properties"]))
        assert published_order == list(cast("dict[str, Any]", declared_schema["properties"])), (
            f"{name}: the document emits its properties in a different order from the schema"
        )
