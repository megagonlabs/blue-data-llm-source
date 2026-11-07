"""Identity proposals preserve scope independently of soft row estimates."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast

import pytest

from blue_data_llm_source import Attr, ConfigError, Exact, LlmSource, OneOf, Outcome
from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.cardinality import estimate_from_reported
from blue_data_llm_source.planning.partition_safety import (
    AppliedPartitionDecision,
    IdentityPartitionRule,
    evaluate_partition_safety,
)
from blue_data_llm_source.planning.plan import (
    FetchPlan,
    Gap,
    PartitionProposal,
    PlanSchemaContract,
    apply_partition_proposal,
    root_plan,
)
from blue_data_llm_source.planning.schema import Schema
from blue_data_llm_source.types import Capability, Range, Usage

STATES = tuple(f"State {index:02d}" for index in range(50))
SCHEMA = Schema(attrs=[Attr("state"), Attr("system")], primary_keys=("state", "system"))
STATE_ONLY = Schema(attrs=[Attr("state"), Attr("region")], primary_keys=("state",))
LIMITS = PlanLimits(max_depth=3, max_partitions=50)


@pytest.mark.parametrize("key", ["state", "State", "STATE"])
@pytest.mark.parametrize("primary_keys", [("state",), ("state", "system")])
def test_ambiguous_identity_spelling_is_collapsed(key: str, primary_keys: tuple[str, ...]) -> None:
    schema = Schema(attrs=[Attr("state"), Attr("State"), Attr("system")], primary_keys=primary_keys)
    plan = root_plan(
        mode="partitioned",
        original_query="states",
        retrieval_query="states",
        constraints={},
        estimate=50,
    )
    result = apply_partition_proposal(
        plan,
        leaf_id="0",
        proposal=PartitionProposal(key=key, values=("State 00", "State 01")),
        schema=schema,
        limits=LIMITS,
    )
    assert result.leaves == plan.leaves
    assert result.accepted_internal_nodes == 0
    assert result.attempted_proposals == 1
    assert result.notes[-1].code == Gap.GUARD_COLLAPSED


class StatesModel:
    def __init__(self, children: object = (1, 1), *, category: bool = False) -> None:
        self.children = children
        self.category = category
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("Your task is to prepare a data request"):
            return json.dumps({"ask": "the US states", "constraints": {}})
        if prompt.startswith("Resolve the output schema"):
            return json.dumps({"attrs": [{"name": "state"}], "primary_keys": ["state"]})
        if prompt.startswith("Estimate the number of records"):
            return json.dumps({"expected": 50, "lower": 50, "upper": 50})
        if prompt.startswith("Propose one useful partition"):
            proposal: dict[str, object] = {
                "key": "region" if self.category else "state",
                "values": ["East", "West"] if self.category else STATES[:2],
                "ambiguity": 0,
            }
            if self.children is not None:
                proposal["child_estimates"] = self.children
            return json.dumps(proposal)
        if self.category:
            for region, values in (("East", STATES[:25]), ("West", STATES[25:])):
                if f'"{region}"' in prompt:
                    return json.dumps([{"state": state} for state in values])
        for state in STATES[:2]:
            if f'"{state}"' in prompt:
                return json.dumps([{"state": state}])
        return json.dumps([{"state": state} for state in STATES])


def test_us_states_request_returns_all_50_in_partitioned_mode() -> None:
    # Partitioned mode asks the whole question first; the answer holds every state, so no
    # split is proposed at all.
    model = StatesModel((1, 1))
    result = LlmSource().fetch(
        "the US states",
        attrs=["state"],
        llm_fn=model,
        properties={"plan.mode": "partitioned", "plan.key": "state"},
    )
    assert {row["state"] for row in result.records} == set(STATES)
    assert result.meta.split_decision == "probe_accepted"
    assert not any(p.startswith("Propose one useful partition") for p in model.prompts)


@pytest.mark.parametrize("children", [None, (None, None), (1, 1), (25, 25)])
def test_an_identity_split_collapses_whatever_its_estimates_say(children: object) -> None:
    model = StatesModel(children)
    result = LlmSource().fetch(
        "the US states",
        attrs=["state"],
        llm_fn=model,
        properties={
            "plan.mode": "partitioned",
            "plan.split_policy": "always",
            "plan.max_continuations": 0,
            "plan.key": "state",
            "plan.partition_key": "state",
            "plan.max_depth": 1,
        },
    )
    assert {row["state"] for row in result.records} == set(STATES)
    assert result.meta.outcome == Outcome.OK
    assert result.meta.calls == len(model.prompts) == 5
    assert any("split on 'state' lists individual records" in gap for gap in result.meta.gaps)
    assert not any("coverage_short:" in gap for gap in result.meta.gaps)


@pytest.mark.parametrize("children", [(1, 1), None])
def test_nonidentity_categories_still_retrieve_50(children: object) -> None:
    model = StatesModel(children, category=True)
    result = LlmSource().fetch(
        "the US states",
        attrs=["state"],
        llm_fn=model,
        properties={
            "plan.mode": "partitioned",
            "plan.split_policy": "always",
            "plan.max_continuations": 0,
            "plan.key": "state",
            "plan.max_depth": 1,
        },
    )
    assert {row["state"] for row in result.records} == set(STATES)
    assert result.meta.calls == 6
    warnings = [gap for gap in result.meta.gaps if "coverage_short:" in gap]
    assert len(warnings) == (0 if children is None else 1)
    assert all(
        "split_coverage_short:" in gap and "estimate-based warning" in gap for gap in warnings
    )


def initial_plan(
    *, constraints: dict[str, Any] | None = None, cardinality: object = 50
) -> FetchPlan:
    return root_plan(
        mode="partitioned",
        original_query="states",
        retrieval_query="states",
        constraints=constraints or {},
        estimate=None,
        cardinality=estimate_from_reported(cardinality),
    )


YEARS = ("2019", "2020", "2021", "2022", "2023")
COUNTRY_YEAR = Schema(attrs=[Attr("country"), Attr("year")], primary_keys=("country", "year"))


def year_split(estimates: tuple[int | None, ...], **plan: Any) -> FetchPlan:
    return apply_partition_proposal(
        initial_plan(**plan),
        leaf_id="0",
        proposal=PartitionProposal(key="year", values=YEARS, child_estimates=estimates),
        schema=COUNTRY_YEAR,
        limits=LIMITS,
    )


@pytest.mark.parametrize(
    ("estimates", "warned"),
    [((40,) * 5, False), ((40, 40, None, None, None), False), ((10,) * 5, True)],
    ids=["covering", "missing", "short"],
)
def test_a_composite_key_part_splits_like_a_category(
    estimates: tuple[int | None, ...], warned: bool
) -> None:
    candidate = year_split(estimates, cardinality={"expected": 200, "lower": 200})
    assert [leaf.constraints["year"] for leaf in candidate.leaves] == [Exact(y) for y in YEARS]
    assert any(note.code == Gap.SPLIT_COVERAGE_SHORT for note in candidate.notes) == warned


def test_the_last_open_key_part_of_a_slice_is_treated_as_an_identity() -> None:
    # Within France, a year names one record, so even complete estimates are no evidence.
    candidate = year_split(
        (1,) * 5,
        constraints={"country": Exact("France")},
        cardinality={"expected": 5, "lower": 5},
    )
    assert candidate.nodes == []
    assert "lists individual records" in candidate.notes[-1].detail


@pytest.mark.parametrize("constraint", [Exact(STATES[0]), OneOf(STATES[:3])])
def test_case_variant_of_scoped_field_is_collapsed(constraint: object) -> None:
    plan = initial_plan(constraints={"state": constraint})
    candidate = apply_partition_proposal(
        plan,
        leaf_id="0",
        proposal=PartitionProposal(key="State", values=STATES[:2]),
        schema=SCHEMA,
        limits=LIMITS,
    )
    assert candidate.leaves == plan.leaves
    assert candidate.nodes == []
    assert (
        "exact constraint" in candidate.notes[-1].detail
        or "every known value" in candidate.notes[-1].detail
    )


@pytest.mark.parametrize("children", [(0, 0), (None, None)])
def test_complete_inherited_domain_survives_estimate_disagreement(
    children: tuple[int | None, ...],
) -> None:
    plan = initial_plan(
        constraints={"state": OneOf(STATES[:2])}, cardinality={"expected": 50, "lower": 50}
    )
    candidate = apply_partition_proposal(
        plan,
        leaf_id="0",
        proposal=PartitionProposal(
            key="State",
            values=(*STATES[:2], STATES[0], STATES[2]),
            child_estimates=(*children, children[0], 100),
        ),
        schema=SCHEMA,
        limits=LIMITS,
    )
    assert [leaf.constraints["state"] for leaf in candidate.leaves] == [
        Exact(s) for s in STATES[:2]
    ]
    assert all(set(leaf.constraints) == {"state"} for leaf in candidate.leaves)
    assert candidate.nodes[0].key == "state"
    assert len(candidate.notes) == (1 if children[0] is not None else 0)
    assert all(
        note.code == Gap.SPLIT_COVERAGE_SHORT and "total 0" in note.detail
        for note in candidate.notes
    )
    assert FetchPlan.from_dict(candidate.to_dict()) == candidate


@pytest.mark.parametrize("hint", ["metadata", "caller", "estimates", "duplicates"])
def test_proposal_hints_do_not_widen_an_identity_split(hint: str) -> None:
    proposal = PartitionProposal(key="state", values=STATES[:2])
    if hint == "metadata":
        proposal = replace(
            proposal,
            values_from="estimator",
            partition_safety_status="accepted",
            partition_rule_digest="claimed",
        )
    elif hint == "caller":
        proposal = replace(proposal, from_caller=True)
    elif hint == "estimates":
        proposal = replace(proposal, child_estimates=(25, 25))
    else:
        proposal = replace(proposal, values=(*STATES[:2], STATES[0]), child_estimates=(1, 1, 100))
    candidate = apply_partition_proposal(
        initial_plan(), leaf_id="0", proposal=proposal, schema=STATE_ONLY, limits=LIMITS
    )
    assert len(candidate.leaves) == 1
    assert candidate.nodes == []


def test_expected_only_descendant_preserves_parent_sibling_and_topology() -> None:
    plan = apply_partition_proposal(
        initial_plan(),
        leaf_id="0",
        proposal=PartitionProposal(key="region", values=("East", "West"), child_estimates=(25, 25)),
        schema=STATE_ONLY,
        limits=LIMITS,
    )
    assert plan.leaves[0].cardinality is not None
    assert plan.leaves[0].cardinality.lower is None
    candidate = apply_partition_proposal(
        plan,
        leaf_id="0.0",
        proposal=PartitionProposal(key="state", values=STATES[:2]),
        schema=STATE_ONLY,
        limits=LIMITS,
    )
    assert candidate.leaves == plan.leaves
    assert candidate.nodes == plan.nodes
    assert candidate.attempted_proposals == 2
    assert candidate.accepted_internal_nodes == 1
    assert candidate.notes[-1].target_id == "0.0"
    assert FetchPlan.from_dict(candidate.to_dict()) == candidate


def test_apparently_complete_model_identity_list_is_not_domain_proof() -> None:
    plan = initial_plan(cardinality={"expected": 50, "lower": 50})
    candidate = apply_partition_proposal(
        plan,
        leaf_id="0",
        proposal=PartitionProposal(key="state", values=STATES, child_estimates=(1,) * 50),
        schema=Schema(attrs=[Attr("state")], primary_keys=("state",)),
        limits=LIMITS,
    )
    assert candidate.leaves == plan.leaves
    assert candidate.nodes == []


@pytest.mark.parametrize("children", [(0, 0), (None, None)])
@pytest.mark.parametrize("mutation", [None, "node", "scope", "schema", "values"])
def test_declared_decision_must_bind_actual_node_scope_schema_and_values(
    mutation: str | None,
    children: tuple[int | None, ...],
) -> None:
    rule = IdentityPartitionRule(
        dimension="state",
        ordered_primary_keys=SCHEMA.primary_keys,
        allowed_domain=STATES[:2],
        parent_scope={"region": Exact("East")},
    )
    safety = evaluate_partition_safety(
        rule=rule,
        dimension="state",
        values=STATES[:2],
        ordered_primary_keys=SCHEMA.primary_keys,
        parent_constraints={"region": Exact("East")},
    )
    decision = AppliedPartitionDecision(node_id="0", rule=rule, safety=safety)
    plan = initial_plan(
        constraints={"region": Exact("East")}, cardinality={"expected": 50, "lower": 50}
    )
    contract = PlanSchemaContract.from_schema(SCHEMA)
    values = STATES[:2]
    if mutation == "node":
        decision = replace(decision, node_id="0.1")
    elif mutation == "scope":
        plan = initial_plan(constraints={"region": Exact("West")})
    elif mutation == "schema":
        contract = replace(contract, ordered_primary_keys=("state",))
    elif mutation == "values":
        values = STATES[1:3]
    proposal = PartitionProposal(
        key="state",
        values=values,
        child_estimates=children,
        values_from="estimator",
        partition_safety_status=safety.status,
        partition_rule_digest=safety.rule_digest,
    )
    if mutation is not None:
        with pytest.raises(ConfigError):
            apply_partition_proposal(
                plan,
                leaf_id="0",
                proposal=proposal,
                schema=SCHEMA,
                limits=LIMITS,
                applied_decision=decision,
                schema_contract=contract,
            )
    else:
        candidate = apply_partition_proposal(
            plan,
            leaf_id="0",
            proposal=proposal,
            schema=SCHEMA,
            limits=LIMITS,
            applied_decision=decision,
            schema_contract=contract,
        )
        assert len(candidate.leaves) == 2
        assert any(note.code == Gap.SPLIT_COVERAGE_SHORT for note in candidate.notes) == (
            children[0] is not None
        )
        assert FetchPlan.from_dict(candidate.to_dict()) == candidate


class PricedStatesClient:
    def __init__(self) -> None:
        self.model = StatesModel()

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def bound_for_attempt(self, _prompt: str, _properties: dict[str, Any]) -> float:
        return 0.01

    def complete(self, prompt: str, properties: dict[str, Any]) -> str:
        return self.respond(prompt, properties)

    async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
        return self.respond(prompt, properties)

    def respond(self, prompt: str, properties: dict[str, Any]) -> str:
        budget = properties["budget"]
        assert isinstance(budget, Budget)
        with budget.attempt(0.01) as reservation:
            answer = self.model(prompt)
            reservation.settle(0.01)
        cast("list[Usage]", properties["usage_sink"]).append(Usage(model="scripted", cost=0.01))
        return answer


@pytest.mark.parametrize(
    "limits,calls",
    [
        ({"limits.max_calls": 1}, 1),
        ({"limits.max_calls": 4}, 4),
        ({"limits.max_calls": 5}, 5),
        ({"limits.max_cost": 0.051}, 5),
        ({"limits.max_calls": 5, "limits.max_rows": 1}, 5),
    ],
)
def test_recovery_charges_exact_attempts_and_respects_caps(
    limits: dict[str, object], calls: int
) -> None:
    client = PricedStatesClient()
    result = LlmSource(client=client).fetch(
        "the US states",
        attrs=["state"],
        properties={
            "plan.mode": "partitioned",
            "plan.split_policy": "always",
            "plan.max_continuations": 0,
            "plan.key": "state",
            "plan.max_depth": 1,
            "limits.max_retries": 0,
            **limits,
        },
    )
    capped = "limits.max_rows" in limits
    assert len(result.records) == (1 if capped else 50)
    assert result.meta.outcome == (Outcome.TRUNCATED if capped else Outcome.OK)
    assert result.meta.calls == len(client.model.prompts) == calls
    assert result.meta.cost == pytest.approx(calls * 0.01)
    assert not any("coverage_short:" in gap for gap in result.meta.gaps)
    assert sum(p.startswith("Propose one useful partition") for p in client.model.prompts) == (
        calls == 5
    )


@pytest.mark.parametrize(
    ("constraints", "key", "values"),
    [
        ({"country": OneOf(("France",))}, "year", YEARS),
        ({"year": Range(2020, 2020)}, "country", ("France", "Spain")),
    ],
    ids=["single-value-list", "single-value-range"],
)
def test_a_single_value_filter_closes_its_key_field(
    constraints: dict[str, Any], key: str, values: tuple[str, ...]
) -> None:
    candidate = apply_partition_proposal(
        initial_plan(constraints=constraints, cardinality={"expected": 50, "lower": 50}),
        leaf_id="0",
        proposal=PartitionProposal(key=key, values=values, child_estimates=(25,) * len(values)),
        schema=COUNTRY_YEAR,
        limits=LIMITS,
    )
    assert candidate.nodes == []
    assert "lists individual records" in candidate.notes[-1].detail


def test_an_identity_split_under_a_differently_spelled_scope_still_collapses() -> None:
    candidate = apply_partition_proposal(
        initial_plan(
            constraints={"State": OneOf(STATES[:10])}, cardinality={"expected": 10, "lower": 10}
        ),
        leaf_id="0",
        proposal=PartitionProposal(key="state", values=STATES[:2], child_estimates=(5, 5)),
        schema=Schema(attrs=[Attr("state")], primary_keys=("state",)),
        limits=LIMITS,
    )
    assert candidate.nodes == []
