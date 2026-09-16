from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

import pytest

from blue_data_llm_source import _constants as C
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.planning.bounds import (
    MODE_PARTITIONED,
    PlanLimits,
    distribution_node_attempt_limit,
    execution_calls,
    max_nodes,
    planning_calls,
)
from blue_data_llm_source.planning.cardinality import (
    PARSE_INVALID,
    PARSE_PARSED,
    CardinalityEstimate,
)
from blue_data_llm_source.planning.coordinator import PlanningBundle, coordinate_plan
from blue_data_llm_source.planning.distribution import (
    DistributionEstimator,
    StrategyEvaluator,
)
from blue_data_llm_source.planning.partition_safety import (
    SAFETY_ACCEPTED,
    SAFETY_REJECTED,
    AppliedPartitionDecision,
    IdentityPartitionRule,
    evaluate_partition_safety,
)
from blue_data_llm_source.planning.plan import FetchPlan, PlanSchemaContract
from blue_data_llm_source.planning.schema import (
    KEY_FROM_CALLER,
    SCHEMA_PRESERVE,
    Schema,
    SchemaDefinition,
)
from blue_data_llm_source.planning.transport import SAFETY_WIRE_VERSION, LeafTask
from blue_data_llm_source.types import Attr, ConfigError, Exact, JsonScalar, OneOf, Usage


def response(key: str, values: list[tuple[object, int | None]], ambiguity: float = 0.1) -> str:
    return json.dumps(
        {
            "key": key,
            "partitions": [
                {
                    "value": value,
                    "estimate": None if estimate is None else {"expected": estimate},
                }
                for value, estimate in values
            ],
            "ambiguity": ambiguity,
            "reasoning": "bounded domain",
        }
    )


def bootstrap(attrs: list[str], keys: list[str], estimate: int = 210) -> str:
    return json.dumps(
        {
            "ask": "retrieval wording",
            "constraints": {},
            "row_estimate": estimate,
            "schema": {
                "attrs": [{"name": name, "type": "string"} for name in attrs],
                "primary_keys": keys,
            },
        }
    )


class ScriptedClient:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def bound_for_attempt(self, _prompt: str, _properties: dict[str, Any]) -> float:
        return 0.0

    async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
        self.prompts.append(prompt)
        budget = properties[C.BUDGET_PROPERTY]
        sink = properties[C.USAGE_SINK_PROPERTY]
        assert isinstance(budget, Budget)
        assert isinstance(sink, list)
        with budget.attempt(0.0) as reservation:
            reservation.settle(0.0)
            cast("list[Usage]", sink).append(Usage(model="scripted"))
            return self.responses.pop(0)


CONSOLE_SCHEMA = Schema(
    attrs=[Attr("Console", "string"), Attr("Game", "string"), Attr("Sales", "number")],
    primary_keys=("Console", "Game"),
    key_from=KEY_FROM_CALLER,
)
CONSOLE_RULE = IdentityPartitionRule(
    dimension="Console",
    ordered_primary_keys=("Console", "Game"),
    allowed_domain=("Switch", "PS4", "Xbox"),
    provenance="source_configuration",
)


def test_distribution_parser_preserves_declared_order_and_typed_unknowns() -> None:
    stats = DistributionEstimator.parse(
        response("Console", [("Xbox", 20), ("Switch", None), ("PS4", 10)]),
        rule=CONSOLE_RULE,
        parent=CardinalityEstimate(
            expected=30,
            row_unit="records",
            source="test",
            parse_status=PARSE_PARSED,
        ),
    )
    assert stats.parse_status == PARSE_PARSED
    assert [part.value for part in stats.partitions] == ["Switch", "PS4", "Xbox"]
    assert [part.estimate.expected for part in stats.partitions] == [None, 10, 20]
    assert stats.largest_partition is None
    assert stats.parent_expected_delta is None


def test_distribution_parser_rejects_huge_ambiguity_without_raising() -> None:
    raw = response(
        "Console",
        [("Switch", 1), ("PS4", 1), ("Xbox", 1)],
        ambiguity=10**399,
    )
    stats = DistributionEstimator.parse(raw, rule=CONSOLE_RULE, parent=CardinalityEstimate())
    assert stats.parse_status == PARSE_INVALID
    assert stats.diagnostics == ("distribution ambiguity was not within 0..1",)


def test_strategy_ranking_compares_huge_integer_estimates_without_float_conversion() -> None:
    huge = 10**399
    rule = IdentityPartitionRule(
        dimension="Console",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("A", "B"),
    )
    stats = DistributionEstimator.parse(
        response("Console", [("A", huge), ("B", huge - 1)]),
        rule=rule,
        parent=CardinalityEstimate(
            expected=huge * 2,
            row_unit="records",
            source="test",
            parse_status=PARSE_PARSED,
        ),
    )
    safety = evaluate_partition_safety(
        rule=rule,
        dimension=rule.dimension,
        values=rule.allowed_domain,
        ordered_primary_keys=rule.ordered_primary_keys,
        parent_constraints={},
    )
    candidate = StrategyEvaluator.candidate(
        schema=SchemaDefinition.from_schema(CONSOLE_SCHEMA),
        stats=stats,
        safety=safety,
        rule=rule,
    )
    larger_stats = DistributionEstimator.parse(
        response("Console", [("A", huge + 1), ("B", huge)]),
        rule=rule,
        parent=CardinalityEstimate(
            expected=huge * 2,
            row_unit="records",
            source="test",
            parse_status=PARSE_PARSED,
        ),
    )
    larger = StrategyEvaluator.candidate(
        schema=SchemaDefinition.from_schema(CONSOLE_SCHEMA),
        stats=larger_stats,
        safety=safety,
        rule=rule,
    )
    assert candidate.score[1] == huge
    assert candidate.score[4] == 1
    assert StrategyEvaluator.select((larger, candidate)) is candidate


@pytest.mark.parametrize(
    ("raw", "rule", "declared_type", "diagnostic"),
    [
        pytest.param(
            "",
            CONSOLE_RULE,
            None,
            "distribution response was empty",
            id="empty",
        ),
        pytest.param(
            "not json",
            CONSOLE_RULE,
            None,
            "distribution response was not valid JSON",
            id="invalid-json",
        ),
        pytest.param(
            json.dumps([]),
            CONSOLE_RULE,
            None,
            "distribution response was not an object",
            id="non-object",
        ),
        pytest.param(
            json.dumps(
                {
                    "key": "Console",
                    "partitions": [],
                    "ambiguity": 0.1,
                }
            ),
            CONSOLE_RULE,
            None,
            "distribution response fields did not match the contract",
            id="response-fields",
        ),
        pytest.param(
            response("Other", [("Switch", 1), ("PS4", 1), ("Xbox", 1)]),
            CONSOLE_RULE,
            None,
            "distribution response changed the declared key",
            id="changed-key",
        ),
        pytest.param(
            json.dumps(
                {
                    "key": "Console",
                    "partitions": {},
                    "ambiguity": 0.1,
                    "reasoning": "bounded domain",
                }
            ),
            CONSOLE_RULE,
            None,
            "distribution partitions were not a list",
            id="partitions-not-list",
        ),
        pytest.param(
            response(
                "Console",
                [("Switch", 1), ("PS4", 1), ("Xbox", 1)],
                ambiguity=-0.1,
            ),
            CONSOLE_RULE,
            None,
            "distribution ambiguity was not within 0..1",
            id="ambiguity-out-of-range",
        ),
        pytest.param(
            json.dumps(
                {
                    "key": "Console",
                    "partitions": [],
                    "ambiguity": 0.1,
                    "reasoning": None,
                }
            ),
            CONSOLE_RULE,
            None,
            "distribution reasoning was not a string",
            id="reasoning-not-string",
        ),
        pytest.param(
            json.dumps(
                {
                    "key": "Console",
                    "partitions": ["Switch"],
                    "ambiguity": 0.1,
                    "reasoning": "bounded domain",
                }
            ),
            CONSOLE_RULE,
            None,
            "distribution partition fields did not match the contract",
            id="partition-not-object",
        ),
        pytest.param(
            json.dumps(
                {
                    "key": "Console",
                    "partitions": [{"value": "Switch"}],
                    "ambiguity": 0.1,
                    "reasoning": "bounded domain",
                }
            ),
            CONSOLE_RULE,
            None,
            "distribution partition fields did not match the contract",
            id="partition-fields",
        ),
        pytest.param(
            json.dumps(
                {
                    "key": "Console",
                    "partitions": [{"value": [], "estimate": None}],
                    "ambiguity": 0.1,
                    "reasoning": "bounded domain",
                }
            ),
            CONSOLE_RULE,
            None,
            "distribution partition value was not a finite JSON scalar",
            id="partition-value",
        ),
        pytest.param(
            response("Console", [("Switch", 1), ("Switch", 1), ("Xbox", 1)]),
            CONSOLE_RULE,
            None,
            "distribution response contained duplicate values",
            id="duplicate-response-values",
        ),
        pytest.param(
            json.dumps(
                {
                    "key": "Console",
                    "partitions": [{"value": "Switch", "estimate": 1}],
                    "ambiguity": 0.1,
                    "reasoning": "bounded domain",
                }
            ),
            CONSOLE_RULE,
            None,
            "distribution child estimate was malformed",
            id="estimate-not-object",
        ),
        pytest.param(
            json.dumps(
                {
                    "key": "Console",
                    "partitions": [
                        {"value": "Switch", "estimate": {"expected": 1, "unit": "rows"}}
                    ],
                    "ambiguity": 0.1,
                    "reasoning": "bounded domain",
                }
            ),
            CONSOLE_RULE,
            None,
            "distribution child estimate was malformed",
            id="estimate-fields",
        ),
        pytest.param(
            json.dumps(
                {
                    "key": "Console",
                    "partitions": [{"value": "Switch", "estimate": {"expected": -1}}],
                    "ambiguity": 0.1,
                    "reasoning": "bounded domain",
                }
            ),
            CONSOLE_RULE,
            None,
            "distribution child estimate was invalid",
            id="estimate-invalid",
        ),
        pytest.param(
            json.dumps(
                {
                    "key": "Console",
                    "partitions": [],
                    "ambiguity": 0.1,
                    "reasoning": "bounded domain",
                }
            ),
            IdentityPartitionRule(
                dimension="Console",
                ordered_primary_keys=("Console", "Game"),
                allowed_domain=("Switch", " switch "),
            ),
            "string",
            "declared domain contained normalized duplicates",
            id="duplicate-declared-values",
        ),
        pytest.param(
            response("Console", [("Switch", 1), ("PS4", 1), ("Extra", 1)]),
            CONSOLE_RULE,
            None,
            "distribution response changed the declared domain",
            id="changed-domain",
        ),
    ],
)
def test_distribution_parser_reports_each_rejection_without_raw_values(
    raw: str,
    rule: IdentityPartitionRule,
    declared_type: str | None,
    diagnostic: str,
) -> None:
    stats = DistributionEstimator.parse(
        raw,
        rule=rule,
        parent=CardinalityEstimate(),
        declared_type=declared_type,
    )
    assert stats.parse_status == PARSE_INVALID
    assert stats.partitions == ()
    assert stats.diagnostics == (diagnostic,)


def test_safety_uses_json_scalar_identity_and_rejects_non_key_dimensions() -> None:
    duplicate = IdentityPartitionRule(
        dimension="Console",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=(1, 1.0),
    )
    rejected = evaluate_partition_safety(
        rule=duplicate,
        dimension="Console",
        values=duplicate.allowed_domain,
        ordered_primary_keys=("Console", "Game"),
        parent_constraints={},
    )
    assert rejected.status == SAFETY_REJECTED
    assert "normalized duplicates" in " ".join(rejected.reasons)

    text_duplicate = IdentityPartitionRule(
        dimension="Console",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("Nintendo Switch", "  NINTENDO   SWITCH "),
    )
    rejected = evaluate_partition_safety(
        rule=text_duplicate,
        dimension="Console",
        values=text_duplicate.allowed_domain,
        ordered_primary_keys=("Console", "Game"),
        parent_constraints={},
        declared_type="string",
    )
    assert rejected.status == SAFETY_REJECTED
    assert "normalized duplicates" in " ".join(rejected.reasons)

    non_key = IdentityPartitionRule(
        dimension="Source and year",
        ordered_primary_keys=("Country",),
        allowed_domain=("IEA-2023", "IRENA-2024"),
        provenance="source_configuration",
    )
    rejected = evaluate_partition_safety(
        rule=non_key,
        dimension=non_key.dimension,
        values=non_key.allowed_domain,
        ordered_primary_keys=("Country",),
        parent_constraints={},
    )
    assert rejected.status == SAFETY_REJECTED
    assert "not an ordered primary-key component" in " ".join(rejected.reasons)


@pytest.mark.parametrize("domain", [(), ("", "A"), (None, "A"), "AB"])
def test_identity_rule_rejects_incomplete_or_nonsequence_domains(domain: object) -> None:
    with pytest.raises(ConfigError, match="domain"):
        IdentityPartitionRule(
            dimension="Console",
            ordered_primary_keys=("Console", "Game"),
            allowed_domain=domain,  # pyright: ignore[reportArgumentType]
        )


def test_missing_rule_is_unknown_and_mismatched_key_contract_is_rejected() -> None:
    missing = evaluate_partition_safety(
        rule=None,
        dimension="Console",
        values=("A", "B"),
        ordered_primary_keys=("Console", "Game"),
        parent_constraints={},
    )
    assert missing.status == "unknown"
    mismatch = IdentityPartitionRule(
        dimension="Console",
        ordered_primary_keys=("Console",),
        allowed_domain=("A", "B"),
    )
    rejected = evaluate_partition_safety(
        rule=mismatch,
        dimension="Console",
        values=mismatch.allowed_domain,
        ordered_primary_keys=("Console", "Game"),
        parent_constraints={},
    )
    assert rejected.status == SAFETY_REJECTED
    assert "do not match the schema" in " ".join(rejected.reasons)


def test_rule_domain_must_cover_an_inherited_one_of_constraint() -> None:
    rule = IdentityPartitionRule(
        dimension="Console",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("Switch", "PS4"),
    )
    safety = evaluate_partition_safety(
        rule=rule,
        dimension="Console",
        values=rule.allowed_domain,
        ordered_primary_keys=("Console", "Game"),
        parent_constraints={"Console": OneOf(("Switch", "PS4", "Xbox"))},
        declared_type="string",
    )
    assert safety.status == SAFETY_REJECTED
    assert safety.containment is True
    assert safety.coverage is False


def test_strategy_ranking_uses_parent_consistency_before_key_position() -> None:
    first = IdentityPartitionRule(
        dimension="Console",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("A", "B", "C"),
    )
    second = IdentityPartitionRule(
        dimension="Game",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("X", "Y", "Z"),
    )
    definition = SchemaDefinition.from_schema(CONSOLE_SCHEMA)
    parent = CardinalityEstimate(
        expected=301,
        row_unit="records",
        source="test",
        parse_status=PARSE_PARSED,
    )
    first_stats = DistributionEstimator.parse(
        response("Console", [("A", 101), ("B", 101), ("C", 100)]),
        rule=first,
        parent=parent,
    )
    second_stats = DistributionEstimator.parse(
        response("Game", [("X", 101), ("Y", 100), ("Z", 100)]),
        rule=second,
        parent=parent,
    )
    first_safety = evaluate_partition_safety(
        rule=first,
        dimension="Console",
        values=first.allowed_domain,
        ordered_primary_keys=definition.primary_keys,
        parent_constraints={},
    )
    second_safety = evaluate_partition_safety(
        rule=second,
        dimension="Game",
        values=second.allowed_domain,
        ordered_primary_keys=definition.primary_keys,
        parent_constraints={},
    )
    assert first_safety.status == second_safety.status == SAFETY_ACCEPTED
    candidates = [
        StrategyEvaluator.candidate(schema=definition, stats=second_stats, safety=second_safety),
        StrategyEvaluator.candidate(schema=definition, stats=first_stats, safety=first_safety),
    ]
    selected = StrategyEvaluator.select(candidates)
    assert selected is not None
    assert selected.partition_key == "Game"
    assert first_stats.parent_expected_delta == 1
    assert second_stats.parent_expected_delta == 0
    assert first_stats.largest_partition == second_stats.largest_partition == 101
    assert first_stats.balance_spread == second_stats.balance_spread == 1
    assert candidates[0].score == (0.0, 101.0, 1.0, 0.1, 0.0, 1.0, 1.0)


def test_strategy_ranking_uses_distribution_shape_before_key_position() -> None:
    first = IdentityPartitionRule(
        dimension="Console",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("A", "B"),
    )
    second = IdentityPartitionRule(
        dimension="Game",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("X", "Y"),
    )
    definition = SchemaDefinition.from_schema(CONSOLE_SCHEMA)
    parent = CardinalityEstimate(
        expected=202,
        row_unit="records",
        source="test",
        parse_status=PARSE_PARSED,
    )
    first_stats = DistributionEstimator.parse(
        response("Console", [("A", 151), ("B", 51)]), rule=first, parent=parent
    )
    second_stats = DistributionEstimator.parse(
        response("Game", [("X", 101), ("Y", 101)]), rule=second, parent=parent
    )
    first_safety = evaluate_partition_safety(
        rule=first,
        dimension="Console",
        values=first.allowed_domain,
        ordered_primary_keys=definition.primary_keys,
        parent_constraints={},
    )
    second_safety = evaluate_partition_safety(
        rule=second,
        dimension="Game",
        values=second.allowed_domain,
        ordered_primary_keys=definition.primary_keys,
        parent_constraints={},
    )
    assert first_safety.status == second_safety.status == SAFETY_ACCEPTED
    candidates = [
        StrategyEvaluator.candidate(schema=definition, stats=first_stats, safety=first_safety),
        StrategyEvaluator.candidate(schema=definition, stats=second_stats, safety=second_safety),
    ]
    selected = StrategyEvaluator.select(candidates)
    assert selected is not None
    assert selected.partition_key == "Game"
    assert first_stats.parent_expected_delta == second_stats.parent_expected_delta == 0
    assert first_stats.largest_partition == 151
    assert second_stats.largest_partition == 101
    assert candidates[1].score == (0.0, 101.0, 0.0, 0.1, 0.0, 1.0, 1.0)


def test_strategy_ranking_uses_ambiguity_before_soft_parent_consistency() -> None:
    first = IdentityPartitionRule(
        dimension="Console",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("A", "B", "C"),
    )
    second = IdentityPartitionRule(
        dimension="Game",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("X", "Y", "Z"),
    )
    definition = SchemaDefinition.from_schema(CONSOLE_SCHEMA)
    parent = CardinalityEstimate(
        expected=301,
        row_unit="records",
        source="test",
        parse_status=PARSE_PARSED,
    )
    first_stats = DistributionEstimator.parse(
        response("Console", [("A", 101), ("B", 100), ("C", 100)], ambiguity=0.2),
        rule=first,
        parent=parent,
    )
    second_stats = DistributionEstimator.parse(
        response("Game", [("X", 101), ("Y", 101), ("Z", 100)], ambiguity=0.1),
        rule=second,
        parent=parent,
    )
    first_safety = evaluate_partition_safety(
        rule=first,
        dimension="Console",
        values=first.allowed_domain,
        ordered_primary_keys=definition.primary_keys,
        parent_constraints={},
    )
    second_safety = evaluate_partition_safety(
        rule=second,
        dimension="Game",
        values=second.allowed_domain,
        ordered_primary_keys=definition.primary_keys,
        parent_constraints={},
    )
    assert first_safety.status == second_safety.status == SAFETY_ACCEPTED
    candidates = [
        StrategyEvaluator.candidate(schema=definition, stats=first_stats, safety=first_safety),
        StrategyEvaluator.candidate(schema=definition, stats=second_stats, safety=second_safety),
    ]
    selected = StrategyEvaluator.select(candidates)
    assert selected is not None
    assert selected.partition_key == "Game"
    assert first_stats.parent_expected_delta == 0
    assert second_stats.parent_expected_delta == 1
    assert first_stats.largest_partition == second_stats.largest_partition == 101
    assert first_stats.balance_spread == second_stats.balance_spread == 1


async def coordinated(
    client: ScriptedClient,
    *,
    rules: tuple[IdentityPartitionRule, ...],
    max_depth: int = 1,
    max_candidates: int = 5,
) -> PlanningBundle:
    return await coordinate_plan(
        mode="partitioned",
        original_query="games",
        caller_context="",
        caller_constraints={},
        declared_schema=CONSOLE_SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(max_depth=max_depth, max_strategy_candidates=max_candidates),
        client=client,
        budget=Budget(Limits(max_calls=20, max_retries=0)),
        distribution_search=True,
        identity_partition_rules=rules,
    )


@pytest.mark.asyncio
async def test_safe_composite_rule_activates_and_carries_wire_trace() -> None:
    client = ScriptedClient(
        bootstrap(["Console", "Game", "Sales"], ["Console", "Game"]),
        response("Console", [("Switch", 80), ("PS4", 70), ("Xbox", 60)]),
    )
    bundle = await coordinated(client, rules=(CONSOLE_RULE,))
    assert [leaf.constraints["Console"] for leaf in bundle.plan.leaves] == [
        Exact("Switch"),
        Exact("PS4"),
        Exact("Xbox"),
    ]
    assert bundle.facts.selected_partition_key == "Console"
    assert bundle.facts.partition_safety_status == SAFETY_ACCEPTED
    assert bundle.plan.leaves[0].cardinality is not None
    assert bundle.plan.leaves[0].cardinality.source == "distribution_estimator"
    assert bundle.plan.leaves[0].cardinality.expected == 80
    assert 'Declared finite domain: ["Switch", "PS4", "Xbox"]' in client.prompts[1]
    assert '"partitions": a JSON array' in client.prompts[1]
    assert '"estimate":{"expected":10,"lower":5,"upper":20}' in client.prompts[1]
    assert "Do not return an object keyed by partition value." in client.prompts[1]
    assert 'non-null "expected"' in client.prompts[1]
    task = LeafTask.from_leaf(
        bundle.plan.leaves[0], caller_context="", schema=bundle.schema, settings={}
    )
    payload = task.to_dict()
    assert payload["wire_version"] == SAFETY_WIRE_VERSION
    assert payload["partition_safety_status"] == SAFETY_ACCEPTED
    assert LeafTask.from_dict(json.loads(json.dumps(payload))) == task
    assert bundle.plan.applied_partition_decisions == (
        AppliedPartitionDecision(
            node_id="0",
            rule=CONSOLE_RULE,
            safety=evaluate_partition_safety(
                rule=CONSOLE_RULE,
                dimension="Console",
                values=CONSOLE_RULE.allowed_domain,
                ordered_primary_keys=CONSOLE_RULE.ordered_primary_keys,
                parent_constraints={},
                declared_type="string",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_unsafe_renewable_like_rule_is_nonactivating_without_distribution_call() -> None:
    schema = Schema(
        attrs=[Attr("Country", "string"), Attr("Source and year", "string")],
        primary_keys=("Country",),
        key_from=KEY_FROM_CALLER,
    )
    rule = IdentityPartitionRule(
        dimension="Source and year",
        ordered_primary_keys=("Country",),
        allowed_domain=("IEA-2023", "IRENA-2024"),
        provenance="source_configuration",
    )
    client = ScriptedClient(bootstrap(["Country", "Source and year"], ["Country"], 66))
    bundle = await coordinate_plan(
        mode="partitioned",
        original_query="renewable",
        caller_context="",
        caller_constraints={},
        declared_schema=schema,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(),
        client=client,
        budget=Budget(Limits(max_calls=20, max_retries=0)),
        distribution_search=True,
        identity_partition_rules=(rule,),
    )
    assert len(bundle.plan.leaves) == 1
    assert bundle.facts.partition_safety_status == SAFETY_REJECTED
    assert bundle.facts.distribution_attempts == 0
    assert len(client.prompts) == 1


@pytest.mark.asyncio
async def test_malformed_distribution_is_isolated_and_candidate_calls_are_bounded() -> None:
    other = IdentityPartitionRule(
        dimension="Game",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("A", "B"),
    )
    client = ScriptedClient(
        bootstrap(["Console", "Game", "Sales"], ["Console", "Game"]),
        "not json",
    )
    bundle = await coordinated(client, rules=(CONSOLE_RULE, other), max_candidates=1)
    assert len(bundle.plan.leaves) == 1
    assert bundle.facts.strategy_candidates_considered == 1
    assert bundle.facts.distribution_attempts == 1
    assert bundle.facts.distribution_parse_failures == 1
    assert len(client.prompts) == 2


@pytest.mark.asyncio
async def test_rule_scopes_build_depth_two_hierarchy() -> None:
    nested = IdentityPartitionRule(
        dimension="Game",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("Shared", "Only"),
        parent_scope={"Console": Exact("Switch")},
        provenance="source_configuration",
    )
    client = ScriptedClient(
        bootstrap(["Console", "Game", "Sales"], ["Console", "Game"]),
        response("Console", [("Switch", 100), ("PS4", 10), ("Xbox", 10)]),
        response("Game", [("Shared", 50), ("Only", 50)]),
    )
    bundle = await coordinated(client, rules=(CONSOLE_RULE, nested), max_depth=2)
    assert [node.id for node in bundle.plan.nodes] == ["0", "0.0"]
    assert [leaf.id for leaf in bundle.plan.leaves] == ["0.0.0", "0.0.1", "0.1", "0.2"]
    assert bundle.plan.leaves[0].constraints == {
        "Console": Exact("Switch"),
        "Game": Exact("Shared"),
    }
    assert [decision.node_id for decision in bundle.plan.applied_partition_decisions] == [
        "0",
        "0.0",
    ]
    restored = FetchPlan.from_dict(json.loads(json.dumps(bundle.plan.to_dict())))
    assert restored == bundle.plan
    assert restored.to_dict() == bundle.plan.to_dict()

    original = cast("dict[str, Any]", json.loads(json.dumps(bundle.plan.to_dict())))
    downgraded_nested = deepcopy(original)
    nested_nodes = cast("list[dict[str, Any]]", downgraded_nested["nodes"])
    nested_nodes[1]["values_from"] = "enumeration"
    nested_nodes[1]["partition_safety_status"] = None
    nested_nodes[1]["partition_rule_digest"] = None
    decisions = cast("list[dict[str, Any]]", downgraded_nested["applied_partition_decisions"])
    downgraded_nested["applied_partition_decisions"] = [
        decision for decision in decisions if decision["node_id"] != "0.0"
    ]
    with pytest.raises(ConfigError, match="complete safety trace"):
        FetchPlan.from_dict(downgraded_nested)

    nested_constraint = deepcopy(original)
    nested_nodes = cast("list[dict[str, Any]]", nested_constraint["nodes"])
    nested_scope = cast("dict[str, Any]", nested_nodes[1]["constraints"])
    console = cast("dict[str, Any]", nested_scope["Console"])
    console["value"] = "PS4"
    with pytest.raises(ConfigError, match="child identity|partition scope"):
        FetchPlan.from_dict(nested_constraint)

    inherited_constraint = deepcopy(original)
    nested_leaves = cast("list[dict[str, Any]]", inherited_constraint["leaves"])
    leaf_scope = cast("dict[str, Any]", nested_leaves[0]["constraints"])
    console = cast("dict[str, Any]", leaf_scope["Console"])
    console["value"] = "PS4"
    with pytest.raises(ConfigError, match="inherited constraint"):
        FetchPlan.from_dict(inherited_constraint)

    nested_parent = deepcopy(original)
    nested_nodes = cast("list[dict[str, Any]]", nested_parent["nodes"])
    nested_nodes[1]["parent_id"] = "detached"
    with pytest.raises(ConfigError, match="unknown parent"):
        FetchPlan.from_dict(nested_parent)

    nested_depth = deepcopy(original)
    nested_nodes = cast("list[dict[str, Any]]", nested_depth["nodes"])
    nested_nodes[1]["depth"] = 3
    with pytest.raises(ConfigError, match="depth"):
        FetchPlan.from_dict(nested_depth)

    cyclic = deepcopy(original)
    nested_nodes = cast("list[dict[str, Any]]", cyclic["nodes"])
    nested_nodes[1]["parent_id"] = nested_nodes[1]["id"]
    with pytest.raises(ConfigError, match="parent cycle"):
        FetchPlan.from_dict(cyclic)


@pytest.mark.asyncio
async def test_safety_node_binds_exactly_one_immediate_child_per_domain_value() -> None:
    rule = IdentityPartitionRule(
        dimension="Console",
        ordered_primary_keys=("Console", "Game"),
        allowed_domain=("PS4", "Xbox"),
        provenance="source_configuration",
    )
    client = ScriptedClient(
        bootstrap(["Console", "Game", "Sales"], ["Console", "Game"]),
        response("Console", [("PS4", 70), ("Xbox", 60)]),
    )
    bundle = await coordinated(client, rules=(rule,))
    assert FetchPlan.from_dict(json.loads(json.dumps(bundle.plan.to_dict()))) == bundle.plan
    original = cast("dict[str, Any]", json.loads(json.dumps(bundle.plan.to_dict())))

    downgraded_root = deepcopy(original)
    nodes = cast("list[dict[str, Any]]", downgraded_root["nodes"])
    nodes[0]["values_from"] = "enumeration"
    nodes[0]["partition_safety_status"] = None
    nodes[0]["partition_rule_digest"] = None
    downgraded_root["applied_partition_decisions"] = []
    with pytest.raises(ConfigError, match="complete safety trace"):
        FetchPlan.from_dict(downgraded_root)

    duplicate_identity = deepcopy(original)
    leaves = cast("list[dict[str, Any]]", duplicate_identity["leaves"])
    first_constraints = cast("dict[str, Any]", leaves[0]["constraints"])
    first_console = cast("dict[str, Any]", first_constraints["Console"])
    assert first_console["value"] == "PS4"
    first_console["value"] = "Xbox"
    with pytest.raises(ConfigError, match="duplicate or outside"):
        FetchPlan.from_dict(duplicate_identity)

    omitted = deepcopy(original)
    omitted["leaves"] = cast("list[dict[str, Any]]", omitted["leaves"])[1:]
    with pytest.raises(ConfigError, match="children do not match"):
        FetchPlan.from_dict(omitted)

    duplicate_id = deepcopy(original)
    leaves = cast("list[dict[str, Any]]", duplicate_id["leaves"])
    leaves.append(deepcopy(leaves[0]))
    with pytest.raises(ConfigError, match="object ids must be unique"):
        FetchPlan.from_dict(duplicate_id)

    extra = deepcopy(original)
    leaves = cast("list[dict[str, Any]]", extra["leaves"])
    extra_leaf = deepcopy(leaves[0])
    extra_leaf["id"] = "0.2"
    extra_constraints = cast("dict[str, Any]", extra_leaf["constraints"])
    extra_console = cast("dict[str, Any]", extra_constraints["Console"])
    extra_console["value"] = "Other"
    leaves.append(extra_leaf)
    with pytest.raises(ConfigError, match="children do not match"):
        FetchPlan.from_dict(extra)

    wrong_parent = deepcopy(original)
    leaves = cast("list[dict[str, Any]]", wrong_parent["leaves"])
    leaves[0]["parent_id"] = "detached"
    with pytest.raises(ConfigError, match="unknown parent"):
        FetchPlan.from_dict(wrong_parent)

    wrong_depth = deepcopy(original)
    leaves = cast("list[dict[str, Any]]", wrong_depth["leaves"])
    leaves[0]["depth"] = 2
    with pytest.raises(ConfigError, match="depth"):
        FetchPlan.from_dict(wrong_depth)


@pytest.mark.asyncio
async def test_fetch_plan_reevaluates_forged_partition_rule_evidence() -> None:
    client = ScriptedClient(
        bootstrap(["Console", "Game", "Sales"], ["Console", "Game"]),
        response("Console", [("Switch", 80), ("PS4", 70), ("Xbox", 60)]),
    )
    bundle = await coordinated(client, rules=(CONSOLE_RULE,))
    original = cast("dict[str, Any]", json.loads(json.dumps(bundle.plan.to_dict())))
    mutations: tuple[tuple[str, object], ...] = (
        ("provenance", "model_generated"),
        ("contract_version", "identity-partition-rule-999"),
        ("kind", "heuristic_buckets"),
        ("ordered_primary_keys", ["Console", "Other"]),
        ("dimension", "Game"),
        ("allowed_domain", ["Switch", "PS4", "Xbox", "Other"]),
        (
            "parent_scope",
            {"Console": {"kind": "exact", "value": "Switch"}},
        ),
    )
    for field, value in mutations:
        payload = deepcopy(original)
        decisions = cast("list[dict[str, Any]]", payload["applied_partition_decisions"])
        raw_rule = cast("dict[str, Any]", decisions[0]["rule"])
        raw_rule[field] = value
        forged_rule = IdentityPartitionRule.from_dict(raw_rule)
        forged_digest = forged_rule.digest
        safety = cast("dict[str, Any]", decisions[0]["safety"])
        safety["rule_digest"] = forged_digest
        nodes = cast("list[dict[str, Any]]", payload["nodes"])
        nodes[0]["partition_rule_digest"] = forged_digest
        with pytest.raises(ConfigError, match="partition decision"):
            FetchPlan.from_dict(payload)


def test_applied_decision_constructor_rejects_untrusted_rule_contracts() -> None:
    safety = evaluate_partition_safety(
        rule=CONSOLE_RULE,
        dimension=CONSOLE_RULE.dimension,
        values=CONSOLE_RULE.allowed_domain,
        ordered_primary_keys=CONSOLE_RULE.ordered_primary_keys,
        parent_constraints={},
        declared_type="string",
    )
    mutations = (
        {"provenance": "model_generated"},
        {"contract_version": "identity-partition-rule-999"},
        {"kind": "heuristic_buckets"},
    )
    for changes in mutations:
        rule = replace(CONSOLE_RULE, **changes)
        forged_safety = replace(safety, rule_digest=rule.digest)
        with pytest.raises(ConfigError, match="applied partition decision rule"):
            AppliedPartitionDecision(node_id="0", rule=rule, safety=forged_safety)


async def typed_partition_bundle(
    *,
    declared_type: str,
    domain: tuple[JsonScalar, ...],
    returned: tuple[JsonScalar, ...],
) -> PlanningBundle:
    bootstrap_payload = json.dumps(
        {
            "ask": "typed retrieval",
            "constraints": {},
            "row_estimate": 20,
            "schema": {
                "attrs": [
                    {"name": "Key", "type": declared_type},
                    {"name": "Value", "type": "string"},
                ],
                "primary_keys": ["Key"],
            },
        }
    )
    client = ScriptedClient(
        bootstrap_payload,
        response("Key", [(value, 10) for value in returned]),
    )
    return await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="typed values",
        caller_context="",
        caller_constraints={},
        declared_schema=Schema(
            attrs=[Attr("Key", declared_type), Attr("Value", "string")],
            primary_keys=("Key",),
            key_from=KEY_FROM_CALLER,
        ),
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(max_depth=1),
        client=client,
        budget=Budget(Limits(max_calls=20, max_retries=0)),
        distribution_search=True,
        identity_partition_rules=(
            IdentityPartitionRule(
                dimension="Key",
                ordered_primary_keys=("Key",),
                allowed_domain=domain,
                provenance="source_configuration",
            ),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "declared_type,domain,returned",
    [
        ("number", (1, 2), ("1", "2")),
        ("date", ("2024-01-01", "2024-01-02"), ("2024/01/01", "2024/01/02")),
        ("string", ("Alpha", "Beta"), (" alpha ", "BETA")),
    ],
)
async def test_plan_schema_contract_preserves_typed_normalization_roundtrip(
    declared_type: str,
    domain: tuple[JsonScalar, ...],
    returned: tuple[JsonScalar, ...],
) -> None:
    bundle = await typed_partition_bundle(
        declared_type=declared_type,
        domain=domain,
        returned=returned,
    )
    assert bundle.plan.schema_contract.ordered_primary_keys == ("Key",)
    assert bundle.plan.schema_contract.type_for("Key") == declared_type
    assert FetchPlan.from_dict(json.loads(json.dumps(bundle.plan.to_dict()))) == bundle.plan


@pytest.mark.asyncio
async def test_fetch_plan_rejects_schema_contract_tampering() -> None:
    bundle = await typed_partition_bundle(
        declared_type="string",
        domain=("1", 1),
        returned=("1", 1),
    )
    original = cast("dict[str, Any]", json.loads(json.dumps(bundle.plan.to_dict())))

    wrong_keys = deepcopy(original)
    contract = cast("dict[str, Any]", wrong_keys["schema_contract"])
    contract["ordered_primary_keys"] = ["Key", "Other"]
    with pytest.raises(ConfigError, match="does not match its plan node"):
        FetchPlan.from_dict(wrong_keys)

    wrong_type = deepcopy(original)
    contract = cast("dict[str, Any]", wrong_type["schema_contract"])
    declared_types = cast("list[dict[str, Any]]", contract["declared_types"])
    declared_types[0]["type"] = "number"
    with pytest.raises(ConfigError, match="does not match its plan node"):
        FetchPlan.from_dict(wrong_type)

    missing_type = deepcopy(original)
    contract = cast("dict[str, Any]", missing_type["schema_contract"])
    contract["declared_types"] = []
    with pytest.raises(ConfigError, match="does not match its plan node"):
        FetchPlan.from_dict(missing_type)


@pytest.mark.asyncio
async def test_fetch_plan_requires_one_decision_per_safety_traced_node() -> None:
    client = ScriptedClient(
        bootstrap(["Console", "Game", "Sales"], ["Console", "Game"]),
        response("Console", [("Switch", 80), ("PS4", 70), ("Xbox", 60)]),
    )
    bundle = await coordinated(client, rules=(CONSOLE_RULE,))
    original = cast("dict[str, Any]", json.loads(json.dumps(bundle.plan.to_dict())))

    missing = deepcopy(original)
    missing["applied_partition_decisions"] = []
    with pytest.raises(ConfigError, match="exactly one decision"):
        FetchPlan.from_dict(missing)
    with pytest.raises(ConfigError, match="exactly one decision"):
        replace(bundle.plan, applied_partition_decisions=())

    duplicate = deepcopy(original)
    duplicate_decisions = cast("list[dict[str, Any]]", duplicate["applied_partition_decisions"])
    duplicate_decisions.append(deepcopy(duplicate_decisions[0]))
    with pytest.raises(ConfigError, match="duplicate applied partition decisions"):
        FetchPlan.from_dict(duplicate)

    extra = deepcopy(original)
    extra_decisions = cast("list[dict[str, Any]]", extra["applied_partition_decisions"])
    detached = deepcopy(extra_decisions[0])
    detached["node_id"] = "detached"
    extra_decisions.append(detached)
    with pytest.raises(ConfigError, match="exactly one decision"):
        FetchPlan.from_dict(extra)


def branching_rule(
    dimension: str,
    domain: tuple[str, ...],
    *,
    scope: dict[str, Exact] | None = None,
) -> IdentityPartitionRule:
    return IdentityPartitionRule(
        dimension=dimension,
        ordered_primary_keys=("K0", "K1", "K2"),
        allowed_domain=domain,
        parent_scope=scope or {},
        provenance="source_configuration",
    )


async def bounded_tree(
    responses: tuple[str, ...], rules: tuple[IdentityPartitionRule, ...]
) -> tuple[PlanningBundle, ScriptedClient, PlanLimits]:
    limits = PlanLimits(max_leaves=4, max_depth=4, max_strategy_candidates=1)
    client = ScriptedClient(
        bootstrap(["K0", "K1", "K2", "value"], ["K0", "K1", "K2"]),
        *responses,
    )
    schema = Schema(
        attrs=[Attr("K0", "string"), Attr("K1", "string"), Attr("K2", "string")],
        primary_keys=("K0", "K1", "K2"),
        key_from=KEY_FROM_CALLER,
    )
    bundle = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="bounded tree",
        caller_context="",
        caller_constraints={},
        declared_schema=schema,
        schema_mode=SCHEMA_PRESERVE,
        limits=limits,
        client=client,
        budget=Budget(Limits(max_calls=30, max_retries=0)),
        distribution_search=True,
        identity_partition_rules=rules,
    )
    return bundle, client, limits


@pytest.mark.asyncio
async def test_deep_two_way_rejection_stays_inside_published_call_bound() -> None:
    rules = (
        branching_rule("K0", ("A", "B")),
        branching_rule("K1", ("C", "D"), scope={"K0": Exact("A")}),
        branching_rule("K2", ("E", "F"), scope={"K0": Exact("B")}),
    )
    bundle, client, limits = await bounded_tree(
        (
            response("K0", [("A", 100), ("B", 100)]),
            response("K1", [("C", 60), ("D", 40)]),
            "not json",
        ),
        rules,
    )
    assert bundle.plan.accepted_internal_nodes == 2
    assert bundle.facts.distribution_nodes_attempted == max_nodes(limits, MODE_PARTITIONED)
    assert bundle.facts.distribution_attempts == 3
    assert bundle.facts.distribution_parse_failures == 1
    assert len(client.prompts) <= planning_calls(limits, MODE_PARTITIONED)
    assert len(client.prompts) <= planning_calls(limits, MODE_PARTITIONED) + execution_calls(
        limits, limits.max_leaves
    )


@pytest.mark.asyncio
async def test_leaf_cap_rejection_consumes_distribution_node_allowance() -> None:
    rules = (
        branching_rule("K0", ("A", "B", "C")),
        branching_rule("K1", ("D", "E"), scope={"K0": Exact("A")}),
        branching_rule("K1", ("F", "G"), scope={"K0": Exact("B")}),
    )
    bundle, _, limits = await bounded_tree(
        (
            response("K0", [("A", 100), ("B", 100), ("C", 100)]),
            response("K1", [("D", 60), ("E", 40)]),
            response("K1", [("F", 60), ("G", 40)]),
        ),
        rules,
    )
    assert bundle.plan.accepted_internal_nodes == 2
    assert bundle.plan.attempted_proposals == 3
    assert bundle.facts.distribution_nodes_attempted == distribution_node_attempt_limit(limits)
    assert any("max_leaves=4" in note.detail for note in bundle.plan.notes)


@pytest.mark.asyncio
async def test_d_off_prompt_and_leaf_wire_are_unchanged_when_rules_are_supplied() -> None:
    old = ScriptedClient(
        bootstrap(["Console", "Game", "Sales"], ["Console", "Game"]),
        json.dumps(
            {
                "key": "Console",
                "values": ["Switch", "PS4", "Xbox"],
                "ambiguity": 0.1,
                "child_estimates": [80, 70, 60],
            }
        ),
    )
    with_rules = ScriptedClient(*old.responses)
    baseline = await coordinate_plan(
        mode="partitioned",
        original_query="games",
        caller_context="",
        caller_constraints={},
        declared_schema=CONSOLE_SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(max_depth=1),
        client=old,
        budget=Budget(Limits(max_calls=20, max_retries=0)),
    )
    compared = await coordinate_plan(
        mode="partitioned",
        original_query="games",
        caller_context="",
        caller_constraints={},
        declared_schema=CONSOLE_SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(max_depth=1),
        client=with_rules,
        budget=Budget(Limits(max_calls=20, max_retries=0)),
        identity_partition_rules=(CONSOLE_RULE,),
    )
    assert with_rules.prompts == old.prompts
    assert compared.plan == baseline.plan
    assert compared.plan.schema_contract == PlanSchemaContract()
    with pytest.raises(ConfigError, match="requires partition nodes"):
        FetchPlan(
            mode=MODE_PARTITIONED,
            schema_contract=PlanSchemaContract(
                ordered_primary_keys=("Console",),
                declared_types=(("Console", "string"),),
            ),
        )
    baseline_task = LeafTask.from_leaf(
        baseline.plan.leaves[0], caller_context="", schema=baseline.schema, settings={}
    )
    compared_task = LeafTask.from_leaf(
        compared.plan.leaves[0], caller_context="", schema=compared.schema, settings={}
    )
    assert compared_task.to_dict() == baseline_task.to_dict()
