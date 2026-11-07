"""Request scope and column filters in the completeness contract.

The analysis step records what a request is about as constraints, and many name no schema
column: a country, a venue, a start and an end year. Some share a column's name without its
meaning. `keyed-leaf-fetch-2` therefore presents a constraint as a column filter every record
must satisfy only when partitioning introduced it and it names one schema column; everything
the root carries is request scope, whatever its key.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest

from blue_data_llm_source import prompts
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.plan import (
    PartitionPolicy,
    PartitionProposal,
    apply_partition_proposal,
    partition_keys,
    root_plan,
)
from blue_data_llm_source.planning.schema import KEY_FROM_CALLER, Schema
from blue_data_llm_source.planning.transport import LeafTask
from blue_data_llm_source.reduction.obedience import contradictions
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import (
    Attr,
    ConfigError,
    Constraint,
    Exact,
    FetchResult,
    OneOf,
    resolve_attr,
)
from tests.unit.scenario_doubles import ScriptedClient, kind

ATTRS = [
    Attr("title", "string"),
    Attr("composer", "string"),
    Attr("year", "integer"),
    Attr("country", "string"),
]
SCOPE: dict[str, Constraint] = {
    "country": Exact("Italy"),
    "start_year": Exact(1946),
    "end_year": Exact(2012),
}

LEAF_FETCH_TEMPLATE_SHA256 = "4b3677e5ef22f55f373aa9a0f9507f2a4bdfafeb06f93cb95d39ee2506b7f62d"
"""The `keyed-leaf-fetch-1` template, unchanged since 0.2.1.0."""

COMPLETE = prompts.COMPLETE_LEAF_FETCH_PROMPT_VERSION


def _prompt(
    constraints: dict[str, Constraint], contract: str = COMPLETE, split: tuple[str, ...] = ()
) -> str:
    return prompts.build_leaf_fetch_prompt(
        original_query="operas",
        retrieval_query="operas",
        attrs=ATTRS,
        caller_context="",
        constraints=constraints,
        prompt_version=contract,
        partition_keys=split,
    )


def _section(prompt: str, label: str) -> dict[str, Any]:
    line = next(line for line in prompt.splitlines() if line.startswith(label))
    return json.loads(line.split(": ", 1)[1])


# ---------------------------------------------------------------------- the rendering ---


def test_root_constraints_render_as_request_scope_even_on_a_column_name() -> None:
    prompt = _prompt(SCOPE)
    # `country` names a column, but the root carries it, so it is scope.
    assert set(_section(prompt, "Request scope:")) == {"country", "start_year", "end_year"}
    assert _section(prompt, "Column filters:") == {}
    assert "do not treat a start and end pair as a" in prompt
    assert "only when no record matches the column filters within the" in prompt


def test_a_partition_introduced_column_filter_renders_as_a_column_filter() -> None:
    prompt = _prompt({"composer": Exact("Giuseppe Verdi")}, split=("composer",))
    assert _section(prompt, "Column filters:") == {
        "composer": {"kind": "exact", "value": "Giuseppe Verdi"}
    }
    assert _section(prompt, "Request scope:") == {}


def test_a_leaf_with_both_renders_each_in_its_place() -> None:
    constraints = {**SCOPE, "composer": Exact("Giuseppe Verdi"), "decade": Exact("1950s")}
    prompt = _prompt(constraints, split=("composer", "decade"))
    # `decade` was introduced by a split but names no column: a child-specific slice of scope.
    assert set(_section(prompt, "Request scope:")) == {
        "country",
        "start_year",
        "end_year",
        "decade",
    }
    assert _section(prompt, "Column filters:") == {
        "composer": {"kind": "exact", "value": "Giuseppe Verdi"}
    }


def test_a_column_is_matched_without_regard_to_case_and_named_as_declared() -> None:
    prompt = _prompt({"Year": OneOf((1950, 1951))}, split=("Year",))
    assert _section(prompt, "Column filters:") == {
        "year": {"kind": "one_of", "values": [1950, 1951]}
    }


def test_a_name_two_columns_share_up_to_case_stays_scope() -> None:
    attrs = [Attr("year", "integer"), Attr("Year", "string")]
    assert resolve_attr("year", attrs) is None and resolve_attr("YEAR", attrs) is None
    prompt = prompts.build_leaf_fetch_prompt(
        original_query="q",
        retrieval_query="q",
        attrs=attrs,
        caller_context="",
        constraints={"year": Exact(1950)},
        prompt_version=COMPLETE,
        partition_keys=("year",),
    )
    assert _section(prompt, "Column filters:") == {}
    assert _section(prompt, "Request scope:") == {"year": {"kind": "exact", "value": 1950}}


def test_the_old_contract_is_unchanged() -> None:
    assert hashlib.sha256(prompts.LEAF_FETCH.encode()).hexdigest() == LEAF_FETCH_TEMPLATE_SHA256
    constraints = {**SCOPE, "composer": Exact("Giuseppe Verdi")}
    prompt = _prompt(constraints, prompts.KEYED_LEAF_FETCH_PROMPT_VERSION, ("composer",))
    assert "Request scope" not in prompt and "Column filters" not in prompt
    assert set(_section(prompt, "Filters:")) == {"country", "start_year", "end_year", "composer"}


# ------------------------------------------------------------------- the plan's origin ---


def _schema() -> Schema:
    return Schema(attrs=list(ATTRS), key="title", key_from=KEY_FROM_CALLER)


def test_partition_keys_are_what_the_root_does_not_carry() -> None:
    plan = root_plan(
        mode="partitioned",
        original_query="operas",
        retrieval_query="operas",
        constraints={"country": OneOf(("Italy", "France"))},
        estimate=200,
    )
    assert partition_keys(plan, plan.leaves[0]) == ()
    plan = apply_partition_proposal(
        plan,
        leaf_id="0",
        proposal=PartitionProposal(key="Composer", values=["Verdi", "Puccini"]),
        schema=_schema(),
        limits=PlanLimits(),
    )
    assert [partition_keys(plan, leaf) for leaf in plan.leaves] == [("composer",), ("composer",)]


def test_canonical_wording_names_only_partition_introduced_columns() -> None:
    plan = root_plan(
        mode="partitioned",
        original_query="operas",
        retrieval_query="operas staged in Italy",
        constraints={"country": Exact("Italy")},
        estimate=200,
    )
    plan = apply_partition_proposal(
        plan,
        leaf_id="0",
        proposal=PartitionProposal(key="Composer", values=["Verdi", "Puccini"]),
        schema=_schema(),
        limits=PlanLimits(),
        policy=PartitionPolicy(child_wording="canonical"),
    )
    assert plan.leaves[0].query == (
        'operas staged in Italy, restricted to the slice the column filters define ("composer" '
        '= "Verdi"); the column filters are authoritative'
    )


def test_partition_keys_travel_only_on_the_completeness_wire() -> None:
    plan = root_plan(
        mode="partitioned",
        original_query="o",
        retrieval_query="o",
        constraints={},
        estimate=None,
    )
    leaf = plan.leaves[0]
    task = LeafTask.from_leaf(
        leaf, caller_context="", schema=_schema(), settings={}, prompt_version=COMPLETE
    )
    assert task.to_dict()["partition_keys"] == []
    with pytest.raises(ConfigError, match="wire version 5"):
        LeafTask.from_leaf(
            replace_constraints(leaf, {"composer": Exact("Verdi")}),
            caller_context="",
            schema=_schema(),
            settings={},
            partition_keys=("composer",),
        )
    with pytest.raises(ConfigError, match="names of the task's filters"):
        LeafTask.from_leaf(
            leaf,
            caller_context="",
            schema=_schema(),
            settings={},
            prompt_version=COMPLETE,
            partition_keys=("composer",),
        )


def replace_constraints(leaf: Any, constraints: dict[str, Constraint]) -> Any:
    from dataclasses import replace

    return replace(leaf, constraints=constraints)


# --------------------------------------------------------------------- the facade ---


def _answer(split: bool) -> Callable[[str], str]:
    def answer(prompt: str) -> str:
        step = kind(prompt)
        if step == "bootstrap":
            return json.dumps(
                {
                    "ask": "operas premiered in Italy",
                    # `country` names a column but here means where the opera premiered.
                    "constraints": {"country": "Italy", "start_year": 1946, "end_year": 2012},
                    "row_estimate": 200,
                    "schema": {
                        "attrs": [
                            {"name": "title", "type": "string"},
                            {"name": "composer", "type": "string"},
                            {"name": "country", "type": "string"},
                        ],
                        "key": "title",
                    },
                }
            )
        if step == "proposal":
            values = ["Giuseppe Verdi", "Giacomo Puccini"] if split else []
            return json.dumps({"key": "Composer", "values": values, "ambiguity": 0})
        if '"Giacomo Puccini"' in prompt:
            return json.dumps(
                [{"title": "Tosca", "composer": "Giacomo Puccini", "country": "Italy"}]
            )
        # One row whose country differs from the root's, and one row off its slice.
        return json.dumps(
            [
                {"title": "Otello", "composer": "Giuseppe Verdi", "country": "Italy"},
                {"title": "Jérusalem", "composer": "Giuseppe Verdi", "country": "France"},
                {"title": "Turandot", "composer": "Giacomo Puccini", "country": "Italy"},
            ]
        )

    return answer


def _fetch(split: bool) -> tuple[ScriptedClient, FetchResult]:
    client = ScriptedClient(_answer(split))
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "operas",
        attrs=[
            {"name": "title", "type": "string"},
            {"name": "composer", "type": "string"},
            {"name": "country", "type": "string"},
        ],
        properties={
            "plan.mode": "partitioned",
            "plan.split_policy": "always",
            "plan.max_continuations": 0,
            "plan.leaf_prompt": COMPLETE,
            "plan.filter_enforcement": "drop",
        },
    )
    return client, result


def test_a_root_constraint_named_like_a_column_is_never_counted_or_dropped() -> None:
    client, result = _fetch(split=False)
    [fetch] = [prompt for prompt in client.prompts if kind(prompt) == "fetch"]
    assert set(_section(fetch, "Request scope:")) == {"country", "start_year", "end_year"}
    assert _section(fetch, "Column filters:") == {}
    # `Jérusalem` has country France against the root's Italy, and is kept.
    assert {record["title"] for record in result.records} == {"Otello", "Jérusalem", "Turandot"}
    assert result.meta.filter_contradictions == 0


def test_a_split_on_a_column_is_a_column_filter_and_is_checked() -> None:
    client, result = _fetch(split=True)
    fetches = [prompt for prompt in client.prompts if kind(prompt) == "fetch"]
    assert [_section(prompt, "Column filters:") for prompt in fetches] == [
        {"composer": {"kind": "exact", "value": "Giuseppe Verdi"}},
        {"composer": {"kind": "exact", "value": "Giacomo Puccini"}},
    ]
    assert all(
        set(_section(prompt, "Request scope:")) == {"country", "start_year", "end_year"}
        for prompt in fetches
    )
    # Turandot came back from the Verdi leaf: counted and dropped there, kept from its own.
    assert result.meta.filter_contradictions == 1
    assert {record["title"] for record in result.records} == {"Otello", "Jérusalem", "Tosca"}


def test_obedience_resolves_case_and_ignores_what_it_is_not_given() -> None:
    record = {"title": "Otello", "composer": "Giuseppe Verdi", "year": 1887}
    assert contradictions([record], {"Year": Exact(1887)}, ATTRS) == [False]
    assert contradictions([record], {"YEAR": Exact(1900)}, ATTRS) == [True]
    assert contradictions([record], {}, ATTRS) == [False]
