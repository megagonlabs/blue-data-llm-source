"""The published planning bounds, checked with the root probe of `split_policy="evidence"`.

Counted from the calls the coordinator actually made, probe included, under every schedule
and cap setting the coordinator bound tests drive, for a probe that splits and one that is accepted.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from blue_data_llm_source import _constants as C
from blue_data_llm_source.execution.local import run_local_leaf
from blue_data_llm_source.execution.mapper import LeafRun
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.planning.bounds import (
    MODE_PARTITIONED,
    PlanLimits,
    fact_calls,
    leaf_limit,
    planned_logical_calls,
    planned_provider_attempts,
    planning_calls,
    planning_model_calls,
    request_calls,
    worst_case_attempts,
)
from blue_data_llm_source.planning.context import PLANNING_COMBINED, PLANNING_ORDERS
from blue_data_llm_source.planning.coordinator import PlanningBundle, coordinate_plan
from blue_data_llm_source.planning.plan import Leaf
from blue_data_llm_source.planning.schema import SCHEMA_PRESERVE, Schema
from blue_data_llm_source.planning.transport import LeafTask
from blue_data_llm_source.types import BudgetExceeded, ConfigError
from tests.unit.planning.test_coordinator import KEYED, RULES, StageClient

EVIDENCE = "evidence"


class AnsweringClient(StageClient):
    """The matrix's client, with a one-row answer for the probe instead of unreadable text."""

    async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
        text = await super().acomplete(prompt, properties)
        if text == "not json" and prompt.startswith("Your task is to answer"):
            return json.dumps([{"K0": "a", "K1": "d", "K2": "g", "v": "x"}])
        return text


def _prober(client: StageClient, budget: Budget) -> Any:
    async def probe(leaf: Leaf, schema: Schema) -> LeafRun:
        task = LeafTask.from_leaf(
            leaf,
            caller_context="",
            schema=schema,
            settings={"max_continuations": 0, "empty_leaf_retry": False},
        )
        return await run_local_leaf(
            task, client=client, properties={C.BUDGET_PROPERTY: budget}, usage_sink=[]
        )

    return probe


async def _plan(
    order: str,
    distribution: bool,
    limits: PlanLimits,
    client: StageClient,
    threshold: int,
) -> PlanningBundle:
    budget = Budget(Limits(max_retries=0))
    return await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="",
        caller_constraints={},
        declared_schema=KEYED,
        schema_mode=SCHEMA_PRESERVE,
        limits=limits,
        client=client,
        budget=budget,
        planning_order=order,
        distribution_search=distribution,
        identity_partition_rules=RULES if distribution else (),
        split_policy=EVIDENCE,
        split_threshold_rows=threshold,
        probe=_prober(client, budget),
    )


LIMITS = [
    PlanLimits(max_depth=0, max_partitions=4, max_leaves=24),
    PlanLimits(max_depth=1, max_partitions=3, max_leaves=24, max_strategy_candidates=3),
    PlanLimits(max_depth=2, max_partitions=3, max_leaves=9, max_strategy_candidates=2),
    PlanLimits(max_depth=3, max_partitions=3, max_leaves=100, max_strategy_candidates=3),
    PlanLimits(),
]


@pytest.mark.parametrize("order", PLANNING_ORDERS)
@pytest.mark.parametrize("distribution", [False, True], ids=["proposals", "distribution"])
@pytest.mark.parametrize(
    "limits", LIMITS, ids=["depth0", "depth1", "depth2-tight-leaves", "depth3", "defaults"]
)
@pytest.mark.parametrize(
    ("client_type", "threshold"),
    [(StageClient, 120), (AnsweringClient, 0), (AnsweringClient, 10**9)],
    ids=["probe-failed-split", "probe-shortfall-split", "probe-accepted"],
)
async def test_every_probe_path_stays_inside_the_published_counts(
    order: str,
    distribution: bool,
    limits: PlanLimits,
    client_type: type[StageClient],
    threshold: int,
) -> None:
    client = client_type(width=min(limits.max_partitions, 3), entities=limits.max_leaves)
    result = await _plan(order, distribution, limits, client, threshold)

    planning = len(client.prompts)
    # A leaf the probe answered is not asked again.
    answered_by_probe = result.probe is not None and (
        not result.probe.split or len(result.plan.leaves) == 1
    )
    leaves = 0 if answered_by_probe else len(result.plan.leaves)
    made = planning + leaves
    kw: dict[str, Any] = {"planning_order": order, "distribution_search": distribution}
    assert planning <= planning_model_calls(limits, MODE_PARTITIONED, split_policy=EVIDENCE, **kw)
    assert len(result.plan.leaves) <= leaf_limit(limits, MODE_PARTITIONED)
    assert made <= planned_logical_calls(limits, MODE_PARTITIONED, split_policy=EVIDENCE, **kw)
    assert made <= planned_provider_attempts(limits, MODE_PARTITIONED, split_policy=EVIDENCE, **kw)
    assert made <= request_calls(
        limits, MODE_PARTITIONED, len(result.plan.leaves), split_policy=EVIDENCE, **kw
    )
    assert made <= request_calls(limits, MODE_PARTITIONED, split_policy=EVIDENCE, **kw)
    assert planning <= planning_calls(limits, MODE_PARTITIONED, split_policy=EVIDENCE)
    assert made <= worst_case_attempts(limits, MODE_PARTITIONED, split_policy=EVIDENCE)


@pytest.mark.parametrize("order", PLANNING_ORDERS)
async def test_an_accepted_probe_is_exactly_the_facts_and_one_call(order: str) -> None:
    client = AnsweringClient(width=3, entities=0)
    result = await _plan(order, False, PlanLimits(max_depth=2, max_partitions=3), client, 10**9)
    assert result.probe is not None and result.probe.decision == "probe_accepted"
    assert len(client.prompts) == fact_calls(order) + 1
    assert [leaf.id for leaf in result.plan.leaves] == ["0"]


async def test_a_probe_then_split_reaches_the_proposal_count_plus_one() -> None:
    """The probe, then one proposal for the root and each of its three children."""
    limits = PlanLimits(max_depth=2, max_partitions=3, max_leaves=9)
    client = StageClient(width=3, entities=0)
    result = await _plan(PLANNING_COMBINED, False, limits, client, 120)
    assert result.probe is not None and result.probe.decision == "probe_split_failed"
    assert len(client.prompts) == planning_model_calls(
        limits, MODE_PARTITIONED, planning_order=PLANNING_COMBINED, split_policy=EVIDENCE
    )
    assert len(client.prompts) == 1 + 1 + 4
    assert len(result.plan.leaves) == 9


async def test_a_budget_refusal_during_the_probe_is_raised_before_any_proposal() -> None:
    client = StageClient(width=3, entities=0)

    async def refused(_leaf: Leaf, _schema: Schema) -> LeafRun:
        error = BudgetExceeded("refused", "max_calls")
        return LeafRun(leaf_id="0", error=str(error), exception=error)

    with pytest.raises(BudgetExceeded):
        await coordinate_plan(
            mode=MODE_PARTITIONED,
            original_query="places",
            caller_context="",
            caller_constraints={},
            declared_schema=KEYED,
            schema_mode=SCHEMA_PRESERVE,
            limits=PlanLimits(),
            client=client,
            budget=Budget(Limits(max_retries=0)),
            planning_order=PLANNING_COMBINED,
            split_policy=EVIDENCE,
            probe=refused,
        )
    assert not any(prompt.startswith("Propose") for prompt in client.prompts)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"split_policy": "sometimes"}, "unknown split policy"),
        ({"split_policy": EVIDENCE, "mode": "row_by_row"}, "requires partitioned mode"),
        ({"split_threshold_rows": -1}, "non-negative integer"),
        ({"split_policy": EVIDENCE}, "needs a probe"),
    ],
)
async def test_the_coordinator_refuses_a_split_setting_it_cannot_honor(
    kwargs: dict[str, Any], message: str
) -> None:
    arguments: dict[str, Any] = {
        "mode": MODE_PARTITIONED,
        "original_query": "places",
        "caller_context": "",
        "caller_constraints": {},
        "declared_schema": KEYED,
        "schema_mode": SCHEMA_PRESERVE,
        "limits": PlanLimits(),
        "client": StageClient(width=3, entities=0),
        "budget": Budget(Limits(max_retries=0)),
        "planning_order": PLANNING_COMBINED,
        **kwargs,
    }
    with pytest.raises(ConfigError, match=message):
        await coordinate_plan(**arguments)
