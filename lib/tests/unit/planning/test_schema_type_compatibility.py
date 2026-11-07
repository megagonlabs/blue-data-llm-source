"""Supported planner types preserve caller declarations and enable keyed retrieval."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from blue_data_llm_source import LlmSource
from blue_data_llm_source.planning.schema import (
    KEY_FROM_CALLER,
    KEY_FROM_INFERRED,
    Schema,
    SchemaGenerator,
    schema_from_bootstrap,
    schema_from_response,
)
from blue_data_llm_source.prompts import build_schema_prompt
from blue_data_llm_source.types import Attr, Capability

TYPES = [
    ("int", "integer", "1969", 1969),
    ("integer", "integer", "1969", 1969),
    ("float", "number", "1.5", 1.5),
    ("number", "number", "1.5", 1.5),
    ("bool", "boolean", "yes", True),
    ("boolean", "boolean", "yes", True),
    ("str", "string", "value", "value"),
    ("string", "string", "value", "value"),
    ("text", "string", "value", "value"),
    ("date", "date", "19-01-2026", "2026-01-19"),
]


@pytest.mark.parametrize("kind,canonical,raw,expected", TYPES)
@pytest.mark.parametrize("mode", ["preserve", "enrich"])
@pytest.mark.parametrize("echo", ["alias", "canonical", "conflict"])
def test_parsers_preserve_declarations_and_normalize_inference(
    kind: str,
    canonical: str,
    raw: Any,
    expected: Any,
    mode: str,
    echo: str,
) -> None:
    declared = [Attr("name", "text"), Attr("value", kind), Attr("custom", "money")]
    payload = {
        "attrs": [
            {"name": "name", "type": "string"},
            {
                "name": "value",
                "type": kind
                if echo == "alias"
                else (canonical if echo == "canonical" else "boolean"),
            },
            {"name": "extra", "type": f" {kind.upper()} "},
        ],
        "primary_keys": ["name"],
    }
    saved = copy.deepcopy(payload)
    for parsed in (
        schema_from_bootstrap(payload, declared, mode=mode),
        SchemaGenerator.parse(json.dumps(payload), Schema(attrs=declared), mode=mode),
    ):
        assert parsed is not None
        assert parsed.attrs[:3] == tuple(declared)
        assert all(parsed.attrs[i] is declared[i] for i in range(3))
        assert parsed.primary_keys == ("name",)
        assert parsed.key_from == KEY_FROM_INFERRED
        assert parsed.attrs[3:] == (() if mode == "preserve" else (Attr("extra", canonical),))
    legacy = schema_from_response(json.dumps(payload), declared)
    assert legacy.attrs == (*declared, Attr("extra", canonical))
    assert legacy.primary_keys == ("name",)
    assert payload == saved
    assert declared == [Attr("name", "text"), Attr("value", kind), Attr("custom", "money")]


@pytest.mark.parametrize("kind,canonical,raw,expected", TYPES)
@pytest.mark.parametrize("mode", ["preserve", "enrich"])
def test_fully_inferred_schema_and_explicit_key(
    kind: str,
    canonical: str,
    raw: Any,
    expected: Any,
    mode: str,
) -> None:
    payload = {"attrs": [{"name": "value", "type": f" {kind.upper()} "}], "key": "value"}
    for parsed in (
        schema_from_bootstrap(payload, [], mode=mode),
        SchemaGenerator.parse(json.dumps(payload), Schema(), mode=mode),
        schema_from_response(json.dumps(payload), []),
    ):
        assert parsed is not None
        assert parsed.attrs == (Attr("value", canonical),)
        assert parsed.primary_keys == ("value",)
    declared = [Attr("value", kind), Attr("custom", "money")]
    explicit = schema_from_bootstrap(
        payload,
        declared,
        declared_keys=("custom",),
        mode=mode,
    )
    assert explicit is not None
    assert explicit.primary_keys == ("custom",)
    assert explicit.key_from == KEY_FROM_CALLER
    assert explicit.attrs == tuple(declared)


@pytest.mark.parametrize("item", [{"name": "x"}, {"name": "x", "type": None}])
def test_untyped_inference(item: dict[str, Any]) -> None:
    payload = {"attrs": [item], "key": "x"}
    parsed = schema_from_bootstrap(payload, [])
    assert parsed is not None
    assert parsed.attrs == (Attr("x"),)
    assert SchemaGenerator.parse(json.dumps(payload), Schema(), mode="preserve") is not None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"attrs": "bad"},
        {"attrs": [1]},
        {"attrs": [{"name": "x", "type": "money"}], "key": "x"},
        {"attrs": [{"name": "x", "type": 1}]},
        {"attrs": [{"name": "x", "type": ""}]},
        {"attrs": [{"name": "x", "type": "int", "extra": True}]},
        {"attrs": [{"name": "x", "type": "int"}, {"name": "x", "type": "int"}]},
        {"attrs": [{"name": "x", "type": "int"}], "key": "missing"},
        {"attrs": [{"name": "x", "type": "int"}], "primary_keys": ["x", "x"]},
        {"attrs": [{"name": "x", "type": "int"}], "primary_keys": ["x"], "key": "y"},
    ],
)
def test_strict_rejection_is_unchanged(payload: Any) -> None:
    assert schema_from_bootstrap(payload, []) is None
    assert SchemaGenerator.parse(json.dumps(payload), Schema(), mode="enrich") is None


@pytest.mark.parametrize("kind", ["money", "decimal"])
def test_custom_declared_type_is_kept_when_echoed(kind: str) -> None:
    payload = {"attrs": [{"name": "x", "type": kind}], "key": "x"}
    declared = [Attr("x", kind)]
    for mode in ("preserve", "enrich"):
        for parsed in (
            schema_from_bootstrap(payload, declared, mode=mode),
            SchemaGenerator.parse(json.dumps(payload), Schema(attrs=declared), mode=mode),
        ):
            assert parsed is not None
            assert parsed.attrs == (Attr("x", kind),)
            assert parsed.primary_keys == ("x",)


def test_custom_inferred_type_fallback_is_unchanged() -> None:
    payload = {"attrs": [{"name": "x", "type": "money"}], "key": "x"}
    assert schema_from_bootstrap(payload, []) is None
    # A declared name does not cover an inferred one beside it.
    beside = {"attrs": [{"name": "x", "type": "money"}, {"name": "y", "type": "money"}]}
    assert schema_from_bootstrap(beside, [Attr("x", "money")], mode="enrich") is None
    assert schema_from_response(json.dumps(payload), []).attrs == (Attr("x"),)


def test_schema_prompt_separates_inference_from_caller_types() -> None:
    prompt = build_schema_prompt(
        original_query="events",
        retrieval_query="events",
        constraints={},
        caller_context="",
        declared_schema={"attrs": [{"name": "day", "type": "date"}]},
        schema_mode="enrich",
    )
    assert "Inferred types may be string, integer, number, boolean, date, or null." in prompt
    assert "including aliases int, float, bool, str, and text" in prompt
    assert '"type": "date"' in prompt


class PlanningClient:
    def __init__(self, kind: str, raw: Any, *, date_key: bool = False) -> None:
        self.kind = kind
        self.raw = raw
        self.date_key = date_key
        self.prompts: list[str] = []

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def complete(self, prompt: str, properties: Any = None) -> str:
        raise AssertionError("unexpected sync path")

    async def acomplete(self, prompt: str, properties: Any = None) -> str:
        self.prompts.append(prompt)
        with properties["budget"].attempt() as attempt:
            attempt.settle(0.0)
        key = "day" if self.date_key else "name"
        attrs = [{"name": key, "type": "date" if self.date_key else "string"}]
        attrs += [{"name": "value", "type": self.kind}, {"name": "extra", "type": "string"}]
        schema = {"attrs": attrs, "primary_keys": [key]}
        estimate = {"expected": 20, "lower": 10, "upper": 40, "row_unit": "event"}
        if prompt.startswith("Prepare a structured data request"):
            return json.dumps(
                {
                    "ask": "events",
                    "constraints": {},
                    "row_estimate": estimate,
                    "schema": schema,
                }
            )
        if prompt.startswith("Your task is to prepare a data request"):
            return json.dumps({"ask": "events", "constraints": {}})
        if prompt.startswith("Resolve the output schema"):
            return json.dumps(schema)
        if prompt.startswith("Estimate the number of records"):
            return json.dumps(estimate)
        if prompt.startswith("Propose one useful partition"):
            return json.dumps(
                {
                    "key": "decade",
                    "values": ["1960s", "1970s"],
                    "ambiguity": 0,
                    "child_estimates": [10, 10],
                }
            )
        if prompt.startswith("Enumerate every entity"):
            return json.dumps({"keys": [key], "values": [{key: "Event A"}, {key: "Event B"}]})
        if self.date_key:
            return json.dumps(
                [
                    {key: value, "value": self.raw, "extra": "detail"}
                    for value in (
                        "07-03-2026",
                        "2026-03-07",
                        "2026-07-03",
                        "19-01-2026",
                        "2026/01/19",
                        "2026-02-30",
                    )
                ]
            )
        if '"1960s"' in prompt:
            return json.dumps([{"name": "Event A", "value": self.raw, "extra": None}])
        return json.dumps(
            [
                {"name": "EVENT A", "value": None, "extra": "detail"},
                {"name": "Event B", "value": self.raw, "extra": "other"},
            ]
        )


@pytest.mark.parametrize("kind,canonical,raw,expected", TYPES)
@pytest.mark.parametrize("schedule", ["planning.schema_first", "planning.combined"])
@pytest.mark.parametrize("mode", ["preserve", "enrich"])
@pytest.mark.parametrize("strategy", ["partitioned", "row_by_row"])
def test_public_fetch_type_matrix(
    kind: str,
    canonical: str,
    raw: Any,
    expected: Any,
    schedule: str,
    mode: str,
    strategy: str,
) -> None:
    client = PlanningClient(kind, raw)
    result = LlmSource(client=client).fetch(
        "events",
        attrs=[{"name": "name", "type": "text"}, {"name": "value", "type": kind}],
        properties={
            "plan.mode": strategy,
            "plan.planning_order": schedule,
            "plan.schema_mode": mode,
            "plan.max_depth": 1,
            "plan.max_continuations": 1,
            # The matrix exercises a split, which the evidence policy would skip.
            **({"plan.split_policy": "always"} if strategy == "partitioned" else {}),
        },
    )
    assert result.meta.primary_keys == ["name"]
    assert len(result.records) == 2
    assert result.records[1]["value"] == expected
    assert result.meta.type_mismatches == []
    assert ("extra" in result.records[0]) == (mode == "enrich")
    if strategy == "partitioned":
        assert result.records[0]["value"] == expected
        if mode == "enrich":
            assert result.records[0]["extra"] == "detail"
    assert any(p.startswith("Enumerate every entity") for p in client.prompts) == (
        strategy == "row_by_row"
    )
    assert any("Event A" in p for p in client.prompts)
    assert any(json.dumps({"name": "value", "type": kind}) in p for p in client.prompts)
    assert result.meta.observed_key_count == 2
    assert result.meta.outcome == "ok"
    assert 0 in result.meta.marginal_key_yields
    assert sum("Return only records missing from that set." in p for p in client.prompts) == 2
    expected_calls = 8 if schedule.endswith("schema_first") else 6
    assert result.meta.calls == len(client.prompts) == expected_calls


@pytest.mark.parametrize("schedule", ["planning.schema_first", "planning.combined"])
def test_inferred_date_key_keeps_date_semantics(schedule: str) -> None:
    client = PlanningClient("int", "1", date_key=True)
    result = LlmSource(client=client).fetch(
        "events",
        properties={
            "plan.mode": "partitioned",
            "plan.split_policy": "always",
            "plan.max_continuations": 0,
            "plan.max_depth": 0,
            "plan.planning_order": schedule,
        },
    )
    assert result.meta.primary_keys == ["day"]
    assert [row["day"] for row in result.records] == [
        "07-03-2026",
        "2026-03-07",
        "2026-07-03",
        "2026-01-19",
        "2026-02-30",
    ]
    assert [m["record_index"] for m in result.meta.type_mismatches] == [0, 4]


@pytest.mark.parametrize("kind", ["money", "decimal"])
@pytest.mark.parametrize("schedule", ["planning.schema_first", "planning.combined"])
@pytest.mark.parametrize("mode", ["preserve", "enrich"])
@pytest.mark.parametrize("strategy", ["partitioned", "row_by_row"])
def test_public_fetch_keeps_the_planner_schema_for_a_custom_declared_type(
    kind: str, schedule: str, mode: str, strategy: str
) -> None:
    client = PlanningClient(kind, "12.50")
    result = LlmSource(client=client).fetch(
        "events",
        attrs=[{"name": "name", "type": "text"}, {"name": "value", "type": kind}],
        properties={
            "plan.mode": strategy,
            "plan.planning_order": schedule,
            "plan.schema_mode": mode,
            "plan.max_depth": 1,
            "plan.max_continuations": 1,
        },
    )
    assert not any("schema" in gap and "unusable" in gap for gap in result.meta.gaps)
    assert result.meta.primary_keys == ["name"]
    # Deduplicated on the key: "EVENT A" and "Event A" are one record.
    assert sorted(row["name"].lower() for row in result.records) == ["event a", "event b"]
    assert result.records[1]["value"] == "12.50"
    assert result.meta.unchecked_types == [kind]
    assert ("extra" in result.records[0]) == (mode == "enrich")
    assert result.meta.outcome == "ok"


@pytest.mark.parametrize("schedule", ["planning.schema_first", "planning.combined"])
def test_public_fetch_still_rejects_a_custom_inferred_type(schedule: str) -> None:
    class Client(PlanningClient):
        async def acomplete(self, prompt: str, properties: Any = None) -> str:
            answer = await super().acomplete(prompt, properties)
            return answer.replace(
                '{"name": "extra", "type": "string"}', '{"name": "extra", "type": "money"}'
            )

    result = LlmSource(client=Client("int", "1969")).fetch(
        "events",
        attrs=[{"name": "name", "type": "text"}, {"name": "value", "type": "int"}],
        properties={
            "plan.mode": "partitioned",
            "plan.split_policy": "always",
            "plan.planning_order": schedule,
            "plan.schema_mode": "enrich",
            "plan.max_depth": 1,
            "plan.max_continuations": 1,
        },
    )
    assert any("schema" in gap and "unusable" in gap for gap in result.meta.gaps)
    assert result.meta.primary_keys == []
