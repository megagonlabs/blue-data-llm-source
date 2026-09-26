"""Partition guardrails: the `partition-2` proposal prompt and the metric-key reject.

`plan.partition_guardrails` sends a proposal prompt with generic rules about which keys and
values make a usable split, and refuses a model-proposed key the schema declares numeric
whose values are not a small ordinal set. Off, the proposal prompt and the checks are 2.1.0's.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from blue_data_llm_source import prompts
from blue_data_llm_source.planning import plan as plan_module
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.plan import (
    VALUES_FROM_ESTIMATOR,
    FetchPlan,
    Gap,
    PartitionPolicy,
    PartitionProposal,
    apply_partition_proposal,
    root_plan,
)
from blue_data_llm_source.planning.ranges import SMALL_ORDINAL_SPAN, small_ordinal
from blue_data_llm_source.planning.schema import KEY_FROM_CALLER, Schema
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import Attr, ConfigError
from tests.unit.scenario_doubles import ScriptedClient, kind, run_scenario

GUARDRAILS = {"plan.partition_guardrails": True}
GUARDED = PartitionPolicy(partition_guardrails=True)


def _proposals(sent: list[str]) -> list[str]:
    return [prompt for prompt in sent if kind(prompt) == "proposal"]


# --------------------------------------------------------------------------- prompt ---


def test_the_guarded_prompt_states_each_rule_and_its_contract() -> None:
    client, _ = run_scenario("partitioned", GUARDRAILS)
    [root, child] = _proposals(client.prompts)
    for prompt in (root, child):
        assert 'Prompt contract: "partition-2"' in prompt
        assert "Do not choose an identifier" in prompt
        assert "continuous measurement" in prompt
        assert "Do not use placeholders" in prompt
        assert "Do not use catch-all values" in prompt
    # Calibrated against each slice's own estimate: 120 at the root, 60 for the first child.
    assert "expected to hold about 120 records. Propose about 3 values" in root
    assert "expected to hold about 60 records. Propose about 2 values" in child


def test_off_the_proposal_prompt_is_the_releases() -> None:
    baseline, _ = run_scenario("partitioned")
    client, _ = run_scenario("partitioned", {"plan.partition_guardrails": False})
    assert _proposals(client.prompts) == _proposals(baseline.prompts)
    assert not any("Prompt contract" in prompt for prompt in _proposals(client.prompts))


def test_the_guarded_prompt_changes_only_proposals() -> None:
    baseline, before = run_scenario("partitioned")
    client, result = run_scenario("partitioned", GUARDRAILS)
    assert [p for p in client.prompts if kind(p) != "proposal"] == [
        p for p in baseline.prompts if kind(p) != "proposal"
    ]
    assert result.records == before.records


@pytest.mark.parametrize(
    ("expected", "target", "most", "count"),
    [(120, 50, 20, 3), (30, 50, 20, 2), (5_000, 50, 20, 20), (0, 50, 20, 2), (101, 50, 3, 3)],
)
def test_the_suggested_value_count_follows_the_estimate(
    expected: int, target: int, most: int, count: int
) -> None:
    sentence = prompts.render_value_count(expected, target, most)
    assert f"about {expected} records. Propose about {count} values" in sentence
    assert "never more values than there are records" in sentence


def test_an_unknown_estimate_asks_for_as_few_values_as_fit_the_target() -> None:
    sentence = prompts.render_value_count(None, 40, 20)
    assert sentence.startswith("Use as few values as keep each one to roughly 40 records")


def test_an_unknown_partition_contract_is_refused_by_the_builder() -> None:
    with pytest.raises(ConfigError, match="unknown partition prompt contract"):
        prompts.build_partition_prompt(
            original_query="q",
            retrieval_query="q",
            caller_context="",
            schema={},
            constraints={},
            ancestor_keys=[],
            preferred_key=None,
            max_partitions=5,
            prompt_version="partition-9",
        )


# ------------------------------------------------------------------- small ordinal ---


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["1946–1952", "1953-1960"], True),
        (["1", "2", "3"], True),
        ([1990, 2000, 2010], True),
        (["1000-1999", "2000-2025"], False),
        (["under 1 million", "over 1 million"], False),
        (["0-1000000", "1000001-5000000"], False),
        (["2.5", "3.5"], False),
        (["high", "low"], False),
        (["1960-1950", "1970"], False),
        ([], False),
    ],
)
def test_a_small_ordinal_set_is_integers_or_spans_over_a_short_stretch(
    values: list[Any], expected: bool
) -> None:
    assert small_ordinal(values) is expected


def test_the_published_stretch_is_a_thousand_integers() -> None:
    assert SMALL_ORDINAL_SPAN == 1_000
    assert small_ordinal(["1000-1999"]) and not small_ordinal(["1000-2000"])


# ----------------------------------------------------------------- deterministic reject ---


def _split(
    key: str,
    declared: str | None,
    values: list[str],
    policy: PartitionPolicy | None = GUARDED,
) -> FetchPlan:
    plan = root_plan(
        mode="partitioned",
        original_query="cities",
        retrieval_query="cities",
        constraints={},
        estimate=500,
    )
    return apply_partition_proposal(
        plan,
        leaf_id="0",
        proposal=PartitionProposal(key=key, values=values),
        schema=Schema(
            attrs=[Attr("name", "string"), Attr(key, declared)],
            key="name",
            key_from=KEY_FROM_CALLER,
        ),
        limits=PlanLimits(),
        policy=policy,
    )


@pytest.mark.parametrize(
    ("declared", "values"),
    [
        ("integer", ["under 1 million", "1-5 million", "over 5 million"]),
        ("int", ["0-1000000", "1000001-9000000"]),
        ("number", ["low", "medium", "high"]),
        ("float", ["2.5", "3.5"]),
    ],
)
def test_a_numeric_key_with_bucketed_values_is_refused_with_the_reason(
    declared: str, values: list[str]
) -> None:
    plan = _split("population", declared, values)
    assert [leaf.id for leaf in plan.leaves] == ["0"]
    assert plan.notes[-1].code == Gap.GUARD_COLLAPSED
    assert "reads as a continuous measurement" in plan.notes[-1].detail
    assert repr(declared) in plan.notes[-1].detail


@pytest.mark.parametrize(
    ("declared", "values"),
    [
        ("integer", ["1946–1952", "1953-1960"]),
        ("number", ["1", "2", "3"]),
        ("string", ["under 1 million", "over 1 million"]),
        (None, ["under 1 million", "over 1 million"]),
    ],
)
def test_an_ordinal_or_non_numeric_key_is_accepted(declared: str | None, values: list[str]) -> None:
    plan = _split("population", declared, values)
    assert len(plan.leaves) == len(values)


def test_with_the_switch_off_a_bucketed_metric_is_split_as_before() -> None:
    plan = _split("population", "integer", ["under 1 million", "over 1 million"], policy=None)
    assert len(plan.leaves) == 2


def test_a_caller_declared_domain_is_not_second_guessed() -> None:
    plan = root_plan(
        mode="partitioned",
        original_query="cities",
        retrieval_query="cities",
        constraints={},
        estimate=500,
    )
    children, _, reason = plan_module._proposal_children(  # pyright: ignore[reportPrivateUsage]
        plan,
        plan.leaves[0],
        PartitionProposal(
            key="population", values=["low", "high"], values_from=VALUES_FROM_ESTIMATOR
        ),
        Schema(
            attrs=[Attr("name", "string"), Attr("population", "integer")],
            key="name",
            key_from=KEY_FROM_CALLER,
        ),
        PlanLimits(),
        GUARDED,
    )
    assert reason is None and len(children) == 2


def _metric_answer(prompt: str) -> str:
    step = kind(prompt)
    if step == "bootstrap":
        return json.dumps(
            {
                "ask": "cities",
                "constraints": {},
                "row_estimate": 300,
                "schema": {
                    "attrs": [
                        {"name": "name", "type": "string"},
                        {"name": "population", "type": "integer"},
                    ],
                    "key": "name",
                },
            }
        )
    if step == "proposal":
        return json.dumps(
            {
                "key": "population",
                "values": ["under 1 million", "over 1 million"],
                "ambiguity": 0,
                "child_estimates": [150, 150],
            }
        )
    return json.dumps([{"name": "Lyon", "population": 500000}])


def test_the_reject_reason_reaches_the_published_gaps_and_spends_no_extra_call() -> None:
    def fetch(guardrails: bool) -> tuple[ScriptedClient, Any]:
        client = ScriptedClient(_metric_answer)
        result = LlmSource(
            properties={"plan.planning_order": "planning.combined"}, client=client
        ).fetch(
            "cities",
            attrs=[{"name": "name", "type": "string"}, {"name": "population", "type": "integer"}],
            properties={"plan.mode": "partitioned", "plan.partition_guardrails": guardrails},
        )
        return client, result

    unguarded, _ = fetch(False)
    client, result = fetch(True)
    assert [kind(prompt) for prompt in client.prompts] == ["bootstrap", "proposal", "fetch"]
    assert len(client.prompts) < len(unguarded.prompts)
    assert any("reads as a continuous measurement" in gap for gap in result.meta.gaps)


# --------------------------------------------------------------------------- facade ---


@pytest.mark.parametrize("mode", ["row_by_row", "flat"])
def test_guardrails_are_refused_outside_partitioned_mode(mode: str) -> None:
    with pytest.raises(ConfigError, match="plan.partition_guardrails requires"):
        run_scenario(mode, GUARDRAILS)


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_guardrails_must_be_a_boolean(value: object) -> None:
    with pytest.raises(ConfigError, match="plan.partition_guardrails must be a boolean"):
        run_scenario("partitioned", {"plan.partition_guardrails": value})
