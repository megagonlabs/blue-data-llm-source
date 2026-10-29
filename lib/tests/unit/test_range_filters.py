"""Typed range filters: parsed, validated, serialized, rendered, and checked.

`plan.range_filters` reads a model proposal written as integer spans, such as "1946–1952",
on a column the schema declares an integer, as `Range` filters. With the switch off the
same proposal becomes exact text filters, as in 0.2.1.0.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from blue_data_llm_source import prompts
from blue_data_llm_source.planning import plan as plan_module
from blue_data_llm_source.planning.analysis import intersect_constraints
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.plan import (
    PLAN_SERIALIZATION_VERSION,
    RANGE_PLAN_SERIALIZATION_VERSION,
    VALUES_FROM_ESTIMATOR,
    FetchPlan,
    Gap,
    Leaf,
    PartitionPolicy,
    PartitionProposal,
    apply_partition_proposal,
    build_entity_plan,
    range_buckets,
    root_plan,
)
from blue_data_llm_source.planning.ranges import parse_span, read_point, typed_children
from blue_data_llm_source.planning.schedules import constraint_payload
from blue_data_llm_source.planning.schema import KEY_FROM_CALLER, Schema
from blue_data_llm_source.planning.transport import COMPLETE_WIRE_VERSION, LeafTask
from blue_data_llm_source.reduction.obedience import contradictions
from blue_data_llm_source.types import Attr, ConfigError, Constraint, Exact, OneOf, Range
from tests.unit.scenario_doubles import kind, run_scenario

RANGES_ON: dict[str, Any] = {"plan.leaf_prompt": "keyed-leaf-fetch-2", "plan.range_filters": True}
RANGES = PartitionPolicy(range_filters=True)

# --------------------------------------------------------------------------- the type ---


def test_a_range_holds_both_ends() -> None:
    span = Range(1946, 1952)
    assert span.contains(1946) and span.contains(1952) and span.contains(1950.0)
    assert not span.contains(1945) and not span.contains(1953)
    assert Range(7, 7).contains(7)


@pytest.mark.parametrize("value", [2.5, True, "2", None, float("nan")])
def test_a_range_holds_only_integer_values(value: object) -> None:
    assert not Range(1, 3).contains(value)
    assert Range(1, 3).contains(2) and Range(1, 3).contains(2.0)


def test_intersection_with_a_range_keeps_only_integer_values() -> None:
    assert intersect_constraints(Range(1, 3), Exact(2.5)) == OneOf(())
    assert intersect_constraints(OneOf((2.5, 2, 3.0)), Range(1, 3)) == OneOf((2, 3.0))


@pytest.mark.parametrize(
    ("lo", "hi", "message"),
    [
        (1952, 1946, "above"),
        (True, 3, "integer"),
        (1.5, 3, "integer"),
        ("1946", 1952, "integer"),
    ],
)
def test_a_range_refuses_ends_it_cannot_order(lo: object, hi: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Range(lo, hi)  # pyright: ignore[reportArgumentType]


# -------------------------------------------------------------------------- parsing ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1946–1952", (1946, 1952)),
        ("1946-1952", (1946, 1952)),
        (" 1946 — 1952 ", (1946, 1952)),
        ("1946−1952", (1946, 1952)),
        ("1952-1946", (1952, 1946)),
        ("1990s", None),
        ("1-2 million", None),
        ("1946 to 1952", None),
        ("-5-3", None),
        ("1946", None),
        (1946, None),
        ("1946-1952-1960", None),
    ],
)
def test_only_two_integers_joined_by_one_dash_are_a_span(
    value: str | int, expected: tuple[int, int] | None
) -> None:
    assert parse_span(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1950, 1950), ("1950", 1950), (" -3 ", -3), (1950.0, 1950), (True, None), ("1950s", None)],
)
def test_a_point_is_one_integer(value: Any, expected: int | None) -> None:
    assert read_point(value) == expected


def test_a_proposal_without_a_span_is_left_as_it_was() -> None:
    assert typed_children(["1946", "1950", "North"]) == (None, None)


def test_a_proposal_with_spans_is_typed_whole() -> None:
    filters, reason = typed_children(["1946–1952", "1953", "1954-1960"])
    assert reason is None
    assert filters == [Range(1946, 1952), Exact(1953), Range(1954, 1960)]


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        (["1952-1946", "1953-1960"], "starts above where it ends"),
        (["1946-1952", "later"], "neither a span nor an integer"),
        (["1946-1952", "1950-1960"], "overlap"),
        (["1946-1952", "1952-1960"], "overlap"),
        (["1946-1952", "1950"], "overlap"),
        (["1946-1952", "1946–1952"], "overlap"),
    ],
)
def test_a_span_proposal_that_does_not_partition_is_refused(values: list[str], reason: str) -> None:
    filters, failure = typed_children(values)
    assert filters is None
    assert failure is not None and reason in failure


def test_numeric_bucketing_yields_contiguous_disjoint_spans() -> None:
    assert range_buckets([1990, 1950, 1970, 1960, 1980, 1950], 2) == [
        Range(1950, 1970),
        Range(1980, 1990),
    ]
    assert range_buckets([3, 1, 2], 5) == [Range(1, 1), Range(2, 2), Range(3, 3)]
    assert range_buckets([], 3) == []


# ----------------------------------------------------------------- planner children ---


def _schema(year_type: str | None) -> Schema:
    return Schema(
        attrs=[Attr("name", "string"), Attr("year", year_type)],
        key="name",
        key_from=KEY_FROM_CALLER,
    )


def _split(
    values: list[str],
    *,
    year_type: str | None = "integer",
    policy: PartitionPolicy | None = RANGES,
    constraints: dict[str, Constraint] | None = None,
    **proposal: Any,
) -> FetchPlan:
    plan = root_plan(
        mode="partitioned",
        original_query="films",
        retrieval_query="films",
        constraints=constraints or {},
        estimate=500,
    )
    return apply_partition_proposal(
        plan,
        leaf_id="0",
        proposal=PartitionProposal(key="year", values=values, **proposal),
        schema=_schema(year_type),
        limits=PlanLimits(),
        policy=policy,
    )


def _year_filters(plan: FetchPlan) -> list[Constraint]:
    return [leaf.constraints["year"] for leaf in plan.leaves]


def test_span_values_on_an_integer_column_become_range_children() -> None:
    plan = _split(["1946–1952", "1953-1960"])
    assert _year_filters(plan) == [Range(1946, 1952), Range(1953, 1960)]
    # The node keeps the values as proposed; only the children's filters are typed.
    assert plan.nodes[0].values == ["1946–1952", "1953-1960"]


@pytest.mark.parametrize("year_type", ["string", None, "number", "date"])
def test_a_column_not_declared_an_integer_keeps_exact_text(year_type: str | None) -> None:
    plan = _split(["1946–1952", "1953-1960"], year_type=year_type)
    assert _year_filters(plan) == [Exact("1946–1952"), Exact("1953-1960")]


def test_with_the_switch_off_the_split_is_what_it_was() -> None:
    plan = _split(["1946–1952", "1953-1960"], policy=None)
    assert _year_filters(plan) == [Exact("1946–1952"), Exact("1953-1960")]


def test_a_caller_declared_domain_is_not_reread_as_spans() -> None:
    # Checked below the plan, because an estimator-sourced split also needs the safety
    # decision a real distribution search would attach.
    plan = root_plan(
        mode="partitioned",
        original_query="films",
        retrieval_query="films",
        constraints={},
        estimate=500,
    )
    children, _, reason = plan_module._proposal_children(  # pyright: ignore[reportPrivateUsage]
        plan,
        plan.leaves[0],
        PartitionProposal(
            key="year", values=["1946–1952", "1953-1960"], values_from=VALUES_FROM_ESTIMATOR
        ),
        _schema("integer"),
        PlanLimits(),
        RANGES,
    )
    assert reason is None
    assert [leaf.constraints["year"] for leaf in children] == [
        Exact("1946–1952"),
        Exact("1953-1960"),
    ]


def test_an_inherited_filter_on_the_key_takes_the_existing_path() -> None:
    plan = _split(
        ["1946–1952", "1953-1960"],
        constraints={"year": OneOf(("1946–1952", "1953-1960"))},
    )
    assert _year_filters(plan) == [Exact("1946–1952"), Exact("1953-1960")]


@pytest.mark.parametrize("values", [["1990", "2020"], ["2001", "2002"]])
def test_an_inherited_range_cannot_be_replaced_by_enumerated_children(
    values: list[str],
) -> None:
    parent = Range(2000, 2010)
    plan = _split(values, constraints={"year": parent})
    assert [leaf.id for leaf in plan.leaves] == ["0"]
    assert plan.leaves[0].constraints["year"] == parent
    assert plan.notes[-1].code == Gap.GUARD_COLLAPSED
    assert "range constraint" in plan.notes[-1].detail


@pytest.mark.parametrize("entities", [[1990, 2020], [2001, 2002]])
def test_an_entity_plan_keeps_an_inherited_range(entities: list[int]) -> None:
    parent = Range(2000, 2010)
    plan = build_entity_plan(
        original_query="films",
        retrieval_query="films",
        constraints={"year": parent},
        key="year",
        entities=entities,
        limits=PlanLimits(),
    )
    assert [leaf.id for leaf in plan.leaves] == ["0"]
    assert plan.leaves[0].constraints["year"] == parent
    assert plan.notes[-1].code == Gap.GUARD_COLLAPSED
    assert "range constraint" in plan.notes[-1].detail


def test_a_refused_span_proposal_keeps_the_parent_and_says_why() -> None:
    plan = _split(["1946-1952", "1950-1960"])
    assert [leaf.id for leaf in plan.leaves] == ["0"]
    assert plan.notes[-1].code == Gap.GUARD_COLLAPSED
    assert "overlap" in plan.notes[-1].detail


def test_canonical_wording_states_a_range_as_a_span() -> None:
    plan = _split(["1946–1952", "1953-1960"], policy=PartitionPolicy("canonical", True))
    assert [leaf.query for leaf in plan.leaves] == [
        'films, restricted to the slice the column filters define ("year" from 1946 to 1952); '
        "the column filters are authoritative",
        'films, restricted to the slice the column filters define ("year" from 1953 to 1960); '
        "the column filters are authoritative",
    ]


# -------------------------------------------------------------------- serialization ---


def _task(filters: dict[str, Constraint], prompt_version: str) -> LeafTask:
    return LeafTask.from_leaf(
        Leaf(id="0.0", parent_id="0", query="films", constraints=filters, original_query="films"),
        caller_context="",
        schema=_schema("integer"),
        settings={},
        prompt_version=prompt_version,
    )


def test_a_range_travels_on_the_completeness_wire() -> None:
    task = _task({"year": Range(1946, 1952)}, prompts.COMPLETE_LEAF_FETCH_PROMPT_VERSION)
    payload = json.loads(json.dumps(task.to_dict()))
    assert payload["wire_version"] == COMPLETE_WIRE_VERSION
    assert payload["filters"] == {"year": {"kind": "range", "lo": 1946, "hi": 1952}}
    assert LeafTask.from_dict(payload) == task


def test_a_range_cannot_be_put_on_an_older_wire() -> None:
    with pytest.raises(ConfigError, match="wire version 5"):
        _task({"year": Range(1946, 1952)}, prompts.KEYED_LEAF_FETCH_PROMPT_VERSION)


def test_an_older_wire_payload_carrying_a_range_is_refused() -> None:
    payload = _task({"year": Exact(1950)}, prompts.KEYED_LEAF_FETCH_PROMPT_VERSION).to_dict()
    payload["filters"] = {"year": {"kind": "range", "lo": 1946, "hi": 1952}}
    with pytest.raises(ConfigError, match="filter kind"):
        LeafTask.from_dict(payload)


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "range", "lo": True, "hi": 3},
        {"kind": "range", "lo": 1.0, "hi": 3},
        {"kind": "range", "lo": 5, "hi": 3},
        {"kind": "range", "lo": 1},
    ],
)
def test_a_malformed_range_on_the_wire_is_refused(bad: dict[str, Any]) -> None:
    payload = _task({"year": Exact(1950)}, prompts.COMPLETE_LEAF_FETCH_PROMPT_VERSION).to_dict()
    payload["filters"] = {"year": bad}
    with pytest.raises(ConfigError):
        LeafTask.from_dict(payload)


def test_a_plan_with_ranges_round_trips() -> None:
    plan = _split(["1946–1952", "1953"])
    restored = FetchPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert _year_filters(restored) == [Range(1946, 1952), Exact(1953)]


# ------------------------------------------------------------------------ rendering ---


def test_a_range_is_rendered_with_both_ends_named_in_every_prompt_form() -> None:
    constraints: dict[str, Constraint] = {"year": Range(1946, 1952)}
    leaf_prompt = prompts.build_leaf_fetch_prompt(
        original_query="films",
        retrieval_query="films",
        attrs=[Attr("year", "integer")],
        caller_context="",
        constraints=constraints,
        prompt_version=prompts.COMPLETE_LEAF_FETCH_PROMPT_VERSION,
        partition_keys=("year",),
    )
    assert 'Column filters: {"year": {"kind": "range", "max": 1952, "min": 1946}}' in leaf_prompt
    assert constraint_payload(constraints) == {"year": {"kind": "range", "min": 1946, "max": 1952}}
    assert prompts.render_constraints(constraints) == [
        '- field "year" must be from 1946 to 1952, both included'
    ]


# ------------------------------------------------------------------------ obedience ---


@pytest.mark.parametrize(
    ("value", "contradicts"),
    [
        (1946, False),
        (1952, False),
        ("1950", False),
        (1945, True),
        ("1,953", True),
        ("unknown", False),
        (None, False),
    ],
)
def test_a_range_is_obeyed_inclusively(value: object, contradicts: bool) -> None:
    flags = contradictions(
        [{"year": value}], {"year": Range(1946, 1952)}, [Attr("year", "integer")]
    )
    assert flags == [contradicts]


def test_a_range_on_an_untyped_column_is_not_checked() -> None:
    assert contradictions([{"year": 1800}], {"year": Range(1946, 1952)}, [Attr("year")]) == [False]


# ------------------------------------------------------------------ constraint algebra ---


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (Range(1, 10), Range(5, 20), Range(5, 10)),
        (Range(1, 4), Range(5, 20), OneOf(())),
        (Range(1, 10), Exact(3), Exact(3)),
        (Exact("3"), Range(1, 10), OneOf(())),
        (OneOf((2, 12, "4")), Range(1, 10), OneOf((2,))),
    ],
)
def test_a_range_intersects_with_every_constraint_kind(
    left: Constraint, right: Constraint, expected: Constraint
) -> None:
    assert intersect_constraints(left, right) == expected


# --------------------------------------------------------------------------- facade ---


def test_range_children_reach_the_leaf_prompts_and_are_checked() -> None:
    client, result = run_scenario("partitioned", RANGES_ON)
    filters = [
        line
        for prompt in client.prompts
        if kind(prompt) == "fetch"
        for line in prompt.splitlines()
        if line.startswith("Column filters:")
    ]
    assert filters == [
        'Column filters: {"region": {"kind": "exact", "value": "North"}, '
        '"year": {"kind": "range", "max": 1952, "min": 1946}}',
        'Column filters: {"region": {"kind": "exact", "value": "South"}, '
        '"year": {"kind": "range", "max": 1952, "min": 1946}}',
        'Column filters: {"year": {"kind": "range", "max": 1960, "min": 1953}}',
    ]
    # Bravo is in the wrong region; Delta (1970) and Foxtrot (1940) are outside their span.
    assert result.meta.filter_contradictions == 3


def test_range_children_with_drop_keep_only_records_inside_their_slice() -> None:
    _, result = run_scenario("partitioned", {**RANGES_ON, "plan.filter_enforcement": "drop"})
    assert [record["name"] for record in result.records] == ["Alpha", "Charlie", "Echo"]


def test_range_filters_need_the_contract_that_carries_them() -> None:
    with pytest.raises(ConfigError, match="keyed-leaf-fetch-2"):
        run_scenario("partitioned", {"plan.range_filters": True})


@pytest.mark.parametrize("mode", ["row_by_row", "flat"])
def test_range_filters_are_refused_outside_partitioned_mode(mode: str) -> None:
    # Flat is refused one switch earlier, for the contract the ranges need.
    with pytest.raises(ConfigError, match="requires .*plan.mode"):
        run_scenario(mode, RANGES_ON)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_range_filters_must_be_a_boolean(value: object) -> None:
    with pytest.raises(ConfigError, match="plan.range_filters must be a boolean"):
        run_scenario("partitioned", {**RANGES_ON, "plan.range_filters": value})


@pytest.mark.parametrize("declared", ["string", None, "number", "date"])
def test_a_transported_range_must_name_an_integer_attribute(declared: str | None) -> None:
    schema = Schema(
        attrs=[Attr("name", "string"), Attr("year", declared)], key="name", key_from=KEY_FROM_CALLER
    )
    with pytest.raises(ConfigError, match="declared int or integer"):
        LeafTask.from_leaf(
            Leaf(id="0.0", parent_id="0", query="films", constraints={"year": Range(1, 2)}),
            caller_context="",
            schema=schema,
            settings={},
            prompt_version=prompts.COMPLETE_LEAF_FETCH_PROMPT_VERSION,
        )


def test_a_range_on_an_undeclared_attribute_is_refused_on_receipt() -> None:
    payload = _task({"year": Range(1, 2)}, prompts.COMPLETE_LEAF_FETCH_PROMPT_VERSION).to_dict()
    payload["filters"] = {"title": {"kind": "range", "lo": 1, "hi": 2}}
    with pytest.raises(ConfigError, match="declared int or integer"):
        LeafTask.from_dict(payload)


def test_a_plan_with_ranges_is_written_under_its_own_version() -> None:
    ranged = _split(["1946–1952", "1953"]).to_dict()
    plain = _split(["1946", "1953"]).to_dict()
    assert ranged["serialization_version"] == RANGE_PLAN_SERIALIZATION_VERSION == 2
    assert plain["serialization_version"] == PLAN_SERIALIZATION_VERSION == 1
    assert FetchPlan.from_dict(json.loads(json.dumps(plain))).to_dict() == plain


def test_a_version_one_plan_cannot_carry_a_range() -> None:
    payload = _split(["1946–1952", "1953"]).to_dict()
    payload["serialization_version"] = 1
    with pytest.raises(ConfigError, match="version 1 cannot carry range"):
        FetchPlan.from_dict(payload)


@pytest.mark.parametrize("version", [0, 3, "2", True])
def test_an_unknown_plan_version_is_refused(version: object) -> None:
    payload = _split(["1946", "1953"]).to_dict()
    payload["serialization_version"] = version
    with pytest.raises(ConfigError, match="serialization version"):
        FetchPlan.from_dict(payload)
