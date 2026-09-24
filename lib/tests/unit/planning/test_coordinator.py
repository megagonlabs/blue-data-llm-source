"""Bootstrap, hierarchy and planning admission."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

import pytest

from blue_data_llm_source import _constants as C
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.planning.bounds import (
    MODE_FLAT,
    MODE_PARTITIONED,
    MODE_ROW_BY_ROW,
    PlanLimits,
    fact_calls,
    leaf_limit,
    max_nodes,
    planned_logical_calls,
    planned_provider_attempts,
    planning_calls,
    planning_model_calls,
    proposal_attempt_limit,
    request_calls,
    worst_case_attempts,
)
from blue_data_llm_source.planning.context import (
    PLANNING_CARDINALITY_FIRST,
    PLANNING_COMBINED,
    PLANNING_LOCKED_PARALLEL,
    PLANNING_LOCKED_SEQUENTIAL,
    PLANNING_ORDERS,
    PLANNING_SCHEMA_FIRST,
)
from blue_data_llm_source.planning.coordinator import (
    PlanningBundle,
    coordinate_plan,
    intersect_constraints,
    partition_proposal_from_response,
)
from blue_data_llm_source.planning.partition_safety import IdentityPartitionRule
from blue_data_llm_source.planning.plan import (
    FetchPlan,
    Leaf,
    PartitionProposal,
    expand_partitioned_plan,
)
from blue_data_llm_source.planning.schema import (
    KEY_FROM_CALLER,
    SCHEMA_ENRICH,
    SCHEMA_PRESERVE,
    Schema,
)
from blue_data_llm_source.planning.transport import LeafTask
from blue_data_llm_source.types import (
    Attr,
    BudgetExceeded,
    ConfigError,
    Constraint,
    Exact,
    OneOf,
    Usage,
)


def encoded(**fields: object) -> str:
    return json.dumps(fields)


def bootstrap(
    *,
    constraints: dict[str, object] | None = None,
    attrs: list[dict[str, object]] | None = None,
    key: str | None = "name",
) -> str:
    return encoded(
        ask="retrieval wording",
        constraints=constraints or {},
        row_estimate=200,
        schema={
            "attrs": attrs
            or [
                {"name": "name", "type": "string"},
                {"name": "continent", "type": "string"},
                {"name": "country", "type": "string"},
            ],
            "key": key,
        },
    )


def proposal(
    key: str,
    values: list[str],
    estimates: object = None,
) -> str:
    fields: dict[str, object] = {"key": key, "values": values, "ambiguity": 0.1}
    if estimates is not None:
        fields["child_estimates"] = estimates
    return encoded(**fields)


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ("not json", "partition response was not one JSON object"),
        (encoded(key="region"), "partition response fields did not match the contract"),
        (
            encoded(key=7, values=["A", "B"], ambiguity=0.1),
            "partition key was not a bounded non-empty string",
        ),
        (
            encoded(key="region", values={"A": 1}, ambiguity=0.1),
            "partition values were not a list",
        ),
        (
            encoded(key="region", values=["A", "B", "C"], ambiguity=0.1),
            "partition response exceeded max_partitions",
        ),
        (
            encoded(key="region", values=["A", ""], ambiguity=0.1),
            "partition values contained an invalid string",
        ),
        (
            encoded(key="region", values=["A", "B"], ambiguity=True),
            "partition ambiguity was not within 0..1",
        ),
        (
            encoded(
                key="region",
                values=["A", "B"],
                ambiguity=0.1,
                child_estimates={"A": 1, "B": 2},
            ),
            "partition child_estimates was not a list",
        ),
    ],
)
def test_partition_parser_reports_a_bounded_failure_class(response: str, reason: str) -> None:
    parsed, diagnostic = partition_proposal_from_response(response, PlanLimits(max_partitions=2))
    assert parsed is None
    assert diagnostic == reason


class TestConstraintIntersectionIdentity:
    @pytest.mark.parametrize(
        "left,right,expected",
        [
            (Exact(1), Exact(1.0), Exact(1)),
            (Exact(True), Exact(1), OneOf(())),
            (Exact(1), OneOf((True, 1.0)), Exact(1)),
            (Exact(True), OneOf((1, 2)), OneOf(())),
            (OneOf((1, 2)), Exact(1.0), Exact(1.0)),
            (OneOf((True, 2)), Exact(1), OneOf(())),
        ],
    )
    def test_exact_intersections_use_public_value_identity(
        self, left: Constraint, right: Constraint, expected: Constraint
    ) -> None:
        assert intersect_constraints(left, right) == expected

    def test_disjunction_intersection_keeps_left_order_and_deduplicates_identity(self) -> None:
        result = intersect_constraints(
            OneOf((True, 1, 1.0, 2, True, 3)),
            OneOf((1.0, True, 3.0)),
        )
        assert result == OneOf((True, 1, 3))

    def test_intersection_result_round_trips_without_changing_scalar_identity(self) -> None:
        result = intersect_constraints(OneOf((True, 1, 1.0)), OneOf((1.0, True)))
        schema = Schema(attrs=[Attr("value")])
        task = LeafTask.from_leaf(
            Leaf(
                id="0",
                parent_id=None,
                query="values",
                original_query="values",
                constraints={"value": result},
            ),
            caller_context="",
            schema=schema,
            settings={},
        )
        restored = LeafTask.from_dict(json.loads(json.dumps(task.to_dict())))
        assert restored.filters["value"] == OneOf((True, 1))


class ScriptedClient:
    def __init__(self, *responses: str | Exception, price: float = 0.0) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.price = price

    def bound_for_attempt(self, _prompt: str, _properties: dict[str, Any]) -> float:
        return self.price

    async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
        self.prompts.append(prompt)
        budget = properties[C.BUDGET_PROPERTY]
        sink = properties[C.USAGE_SINK_PROPERTY]
        assert isinstance(budget, Budget)
        assert isinstance(sink, list)
        typed_sink = cast("list[Usage]", sink)
        with budget.attempt(self.price) as reservation:
            response = self.responses.pop(0)
            reservation.settle(self.price)
            if isinstance(response, Exception):
                raise response
            typed_sink.append(Usage(model="scripted", cost=self.price))
            return response


SCHEMA = Schema(
    attrs=[Attr("name", "string"), Attr("continent", "string"), Attr("country", "string")],
    key="name",
    key_from=KEY_FROM_CALLER,
)


def tree(*, limits: PlanLimits, proposals: Mapping[str, PartitionProposal | None]) -> FetchPlan:
    return expand_partitioned_plan(
        original_query="places",
        retrieval_query="all places",
        constraints={},
        schema=SCHEMA,
        estimate=None,
        limits=limits,
        proposals=proposals,
    )


class TestPureBreadthFirstExpansion:
    def test_derived_call_bounds_match_the_design_formulas(self) -> None:
        limits = PlanLimits(max_leaves=4, max_continuations=1, max_retries=2)
        order = PLANNING_COMBINED
        assert planned_logical_calls(limits, MODE_FLAT) == 2
        assert planned_logical_calls(limits, MODE_PARTITIONED, planning_order=order) == 1 + 7 + 8
        assert planned_logical_calls(limits, MODE_ROW_BY_ROW, planning_order=order) == 2 + 4 * 2
        assert planned_provider_attempts(limits, MODE_PARTITIONED, planning_order=order) == 16 * 3
        # The source's default schedule asks three fact questions rather than one.
        assert planned_logical_calls(limits, MODE_PARTITIONED) == 3 + 7 + 4 * 2

    def test_depth_two_builds_a_real_hierarchy(self) -> None:
        proposals = {
            "0": PartitionProposal("continent", ["Asia", "Europe"], child_estimates=(100, 10)),
            "0.0": PartitionProposal("country", ["China", "Japan"], child_estimates=(20, 20)),
        }
        depth_one = tree(limits=PlanLimits(max_depth=1), proposals=proposals)
        depth_two = tree(limits=PlanLimits(max_depth=2), proposals=proposals)

        assert [node.id for node in depth_one.nodes] == ["0"]
        assert [node.id for node in depth_two.nodes] == ["0", "0.0"]
        assert [leaf.id for leaf in depth_two.leaves] == ["0.0.0", "0.0.1", "0.1"]
        assert depth_two.leaves[0].constraints == {
            "continent": Exact("Asia"),
            "country": Exact("China"),
        }

    def test_leaf_cap_keeps_the_whole_parent(self) -> None:
        plan = tree(
            limits=PlanLimits(max_leaves=2),
            proposals={"0": PartitionProposal("continent", ["Asia", "Europe", "Africa"])},
        )
        assert [leaf.id for leaf in plan.leaves] == ["0"]
        assert plan.leaves[0].constraints == {}

    def test_repeated_ancestor_key_is_rejected(self) -> None:
        plan = tree(
            limits=PlanLimits(max_depth=2),
            proposals={
                "0": PartitionProposal("country", ["France", "Germany"]),
                "0.0": PartitionProposal("country", ["North", "South"]),
            },
        )
        assert [node.id for node in plan.nodes] == ["0"]
        assert any("repeats an ancestor" in note.detail for note in plan.notes)

    def test_known_one_of_requires_complete_coverage(self) -> None:
        plan = expand_partitioned_plan(
            original_query="cities",
            retrieval_query="cities",
            constraints={
                "country": OneOf(("France", "Germany", "Italy")),
                "year": Exact("2024"),
            },
            schema=SCHEMA,
            estimate=None,
            limits=PlanLimits(),
            proposals={"0": PartitionProposal("country", ["France", "Italy"])},
        )
        assert len(plan.leaves) == 1
        assert plan.leaves[0].constraints["country"] == OneOf(("France", "Germany", "Italy"))
        assert plan.leaves[0].constraints["year"] == Exact("2024")

    def test_normalization_keeps_each_estimate_with_its_original_value(self) -> None:
        duplicate = tree(
            limits=PlanLimits(max_depth=1),
            proposals={
                "0": PartitionProposal(
                    "continent",
                    [" Asia ", "Asia", "", "Europe"],
                    child_estimates=(1, 2, 999, 100),
                )
            },
        )
        assert [(leaf.constraints["continent"], leaf.estimate) for leaf in duplicate.leaves] == [
            (Exact("Asia"), None),
            (Exact("Europe"), 100),
        ]

        equal_then_missing = tree(
            limits=PlanLimits(max_depth=1),
            proposals={
                "0": PartitionProposal(
                    "continent",
                    ["Asia", " Asia ", "Europe", "Europe"],
                    child_estimates=(1, 1, 100),
                )
            },
        )
        assert [leaf.estimate for leaf in equal_then_missing.leaves] == [1, None]

        known = expand_partitioned_plan(
            original_query="cities",
            retrieval_query="cities",
            constraints={"region": OneOf(("Asia", "Europe"))},
            schema=SCHEMA,
            estimate=None,
            limits=PlanLimits(max_depth=1),
            proposals={
                "0": PartitionProposal(
                    "region",
                    ["Other", "Asia", "Europe"],
                    child_estimates=(999, 10, 100),
                )
            },
        )
        assert [(leaf.constraints["region"], leaf.estimate) for leaf in known.leaves] == [
            (Exact("Asia"), 10),
            (Exact("Europe"), 100),
        ]

    def test_descendant_normalization_preserves_estimates_after_known_set_filtering(self) -> None:
        plan = expand_partitioned_plan(
            original_query="cities",
            retrieval_query="cities",
            constraints={"country": OneOf(("Japan", "Korea"))},
            schema=SCHEMA,
            estimate=None,
            limits=PlanLimits(max_depth=2),
            proposals={
                "0": PartitionProposal("continent", ["Asia", "Europe"], child_estimates=(100, 1)),
                "0.0": PartitionProposal(
                    "country",
                    ["Other", " Japan ", "Japan", "Korea"],
                    child_estimates=(999, 10, 10, 20),
                ),
            },
        )
        assert [node.id for node in plan.nodes] == ["0", "0.0"]
        assert [(leaf.id, leaf.estimate) for leaf in plan.leaves] == [
            ("0.0.0", 10),
            ("0.0.1", 20),
            ("0.1", 1),
        ]

    @pytest.mark.parametrize(
        "constraint",
        [Exact("France"), OneOf(("France", "Germany", "Italy"))],
    )
    def test_exact_or_incomplete_known_filter_keeps_parent(self, constraint: Constraint) -> None:
        plan = expand_partitioned_plan(
            original_query="cities",
            retrieval_query="cities",
            constraints={"country": constraint},
            schema=SCHEMA,
            estimate=None,
            limits=PlanLimits(),
            proposals={"0": PartitionProposal("country", ["France", "Italy"])},
        )
        assert plan.leaves[0].constraints["country"] == constraint

    def test_malformed_width_and_no_op_proposals_keep_parent(self) -> None:
        too_wide = tree(
            limits=PlanLimits(max_partitions=2),
            proposals={"0": PartitionProposal("country", ["A", "B", "C"])},
        )
        one_value = tree(
            limits=PlanLimits(),
            proposals={"0": PartitionProposal("country", ["A", "A"])},
        )
        assert [leaf.id for leaf in too_wide.leaves] == ["0"]
        assert [leaf.id for leaf in one_value.leaves] == ["0"]

    @pytest.mark.asyncio
    async def test_optional_and_bad_child_counts_are_unknown(self) -> None:
        client = ScriptedClient(
            bootstrap(),
            proposal("continent", ["Asia", "Europe"], ["bad", -1]),
            proposal("country", ["China", "Japan"], [10, 10]),
            proposal("country", ["France", "Germany"], [10, 10]),
        )
        result = await async_case(client, PlanLimits(max_depth=2))
        assert len(result.plan.nodes) == 3

    @pytest.mark.asyncio
    async def test_present_non_array_child_estimates_reject_the_proposal(self) -> None:
        client = ScriptedClient(
            bootstrap(),
            proposal("continent", ["Asia", "Europe"], {"Asia": 100, "Europe": 10}),
        )
        result = await async_case(client, PlanLimits(max_depth=1))
        assert result.plan.nodes == []
        assert [leaf.id for leaf in result.plan.leaves] == ["0"]
        assert result.facts.proposal_attempts == 1
        assert result.plan.notes[-1].detail == "partition child_estimates was not a list"


async def async_case(client: ScriptedClient, limits: PlanLimits):
    return await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="current data",
        caller_constraints={},
        declared_schema=SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=limits,
        client=client,
        budget=Budget(Limits(max_calls=20, max_retries=0)),
    )


@pytest.mark.asyncio
async def test_coordinator_builds_breadth_first_depth_two_tree() -> None:
    client = ScriptedClient(
        bootstrap(),
        proposal("continent", ["Asia", "Europe"], [100, 10]),
        proposal("country", ["China", "Japan"], [10, 10]),
    )
    result = await async_case(client, PlanLimits(max_depth=2))
    assert [node.id for node in result.plan.nodes] == ["0", "0.0"]
    assert result.facts.proposal_attempts == 2
    assert "Original request:" in client.prompts[1]
    assert "Retrieval wording:" in client.prompts[1]
    assert '"child_estimates": a JSON array' in client.prompts[1]
    assert '"child_estimates":[1]' in client.prompts[1]
    assert "Do not return an object keyed by value." in client.prompts[1]


@pytest.mark.asyncio
async def test_bad_bootstrap_returns_protected_leaf_without_more_planning() -> None:
    client = ScriptedClient("not json", proposal("country", ["FR", "DE"], [1, 1]))
    result = await async_case(client, PlanLimits())
    assert result.plan.leaves[0].query == "places"
    assert len(client.prompts) == 1


@pytest.mark.asyncio
async def test_malformed_filters_use_caller_filters() -> None:
    malformed = ScriptedClient(bootstrap(constraints={"country": ["France", {"code": 7}]}))
    bad = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="current data",
        caller_constraints={"country": Exact("France")},
        declared_schema=SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(max_depth=0),
        client=malformed,
        budget=Budget(Limits(max_calls=20, max_retries=0)),
    )
    assert bad.plan.leaves[0].query == "places"
    assert bad.plan.leaves[0].constraints == {"country": Exact("France")}
    assert len(malformed.prompts) == 1


@pytest.mark.asyncio
async def test_empty_filters_never_widen_silently() -> None:
    empty = ScriptedClient(bootstrap(constraints={"country": []}))
    unsatisfiable = await async_case(empty, PlanLimits())
    assert unsatisfiable.plan.leaves[0].constraints["country"] == OneOf(())
    assert len(empty.prompts) == 1


@pytest.mark.asyncio
async def test_insufficient_budget_skips_planning_and_leaves_one_call() -> None:
    budget = Budget(Limits(max_calls=1, max_retries=0))
    client = ScriptedClient(bootstrap())
    result = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="",
        caller_constraints={},
        declared_schema=SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(),
        client=client,
        budget=budget,
    )
    assert client.prompts == []
    assert budget.remaining_calls() == 1
    assert result.plan.leaves[0].id == "0"


@pytest.mark.asyncio
async def test_generated_prompt_growth_rejects_bootstrap_and_keeps_parent() -> None:
    class GrowthClient(ScriptedClient):
        def bound_for_attempt(self, prompt: str, _properties: dict[str, Any]) -> float:
            return 0.2 if "x" * 200 in prompt else 0.05

    grown = encoded(
        ask="x" * 200,
        constraints={},
        row_estimate=10,
        schema={"attrs": [{"name": "name", "type": "string"}], "key": "name"},
    )
    client = GrowthClient(grown, price=0.05)
    result = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="",
        caller_constraints={},
        declared_schema=Schema(attrs=[Attr("name", "string")]),
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(),
        client=client,
        budget=Budget(Limits(max_cost=0.2, max_calls=4, max_retries=0)),
    )
    assert result.plan.leaves[0].query == "places"
    assert any("unaffordable" in item for item in result.diagnostics)


@pytest.mark.asyncio
async def test_failed_proposal_is_charged_and_parent_survives() -> None:
    budget = Budget(Limits(max_calls=6, max_retries=0))
    client = ScriptedClient(bootstrap(), RuntimeError("proposal failed"))
    result = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="",
        caller_constraints={},
        declared_schema=SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(),
        client=client,
        budget=budget,
    )
    assert budget.calls == 2
    assert result.plan.leaves[0].id == "0"
    assert result.facts.proposal_attempts == 1


@pytest.mark.asyncio
async def test_budget_exception_from_client_propagates() -> None:
    client = ScriptedClient(BudgetExceeded("deadline", "max_seconds"))
    with pytest.raises(BudgetExceeded, match="deadline"):
        await async_case(client, PlanLimits())


@pytest.mark.asyncio
async def test_spent_usage_remains_visible_when_a_later_budget_denial_escapes() -> None:
    denied = BudgetExceeded("retry denied", "max_calls")

    class SpentThenDenied(ScriptedClient):
        async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
            self.prompts.append(prompt)
            budget = properties[C.BUDGET_PROPERTY]
            sink = properties[C.USAGE_SINK_PROPERTY]
            assert isinstance(budget, Budget)
            assert isinstance(sink, list)
            typed_sink = cast("list[Usage]", sink)
            with budget.attempt(0.0) as reservation:
                reservation.settle(0.0)
            typed_sink.append(Usage(model="first planning attempt"))
            raise denied

    caller_sink = [Usage(model="earlier work")]
    client = SpentThenDenied()
    budget = Budget(Limits(max_calls=3, max_retries=0))
    with pytest.raises(BudgetExceeded) as caught:
        await coordinate_plan(
            mode=MODE_PARTITIONED,
            original_query="places",
            caller_context="",
            caller_constraints={},
            declared_schema=SCHEMA,
            schema_mode=SCHEMA_PRESERVE,
            limits=PlanLimits(),
            client=client,
            budget=budget,
            properties={C.USAGE_SINK_PROPERTY: caller_sink},
        )
    assert caught.value is denied
    assert budget.calls == 1
    assert [usage.model for usage in caller_sink] == ["earlier work", "first planning attempt"]


@pytest.mark.asyncio
async def test_normal_bundle_slices_a_shared_usage_sink_without_duplication() -> None:
    caller_sink = [Usage(model="earlier work")]
    client = ScriptedClient(bootstrap())
    result = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="",
        caller_constraints={},
        declared_schema=SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(max_depth=0),
        client=client,
        budget=Budget(Limits(max_calls=3, max_retries=0)),
        properties={C.USAGE_SINK_PROPERTY: caller_sink},
    )
    assert [usage.model for usage in caller_sink] == ["earlier work", "scripted"]
    assert [usage.model for usage in result.usages] == ["scripted"]


@pytest.mark.asyncio
async def test_schema_preserve_and_enrich_are_explicit() -> None:
    declared = Schema(attrs=[Attr("name", "string")], key="name", key_from=KEY_FROM_CALLER)
    answer = bootstrap(attrs=[{"name": "country", "type": "string"}], key=None)
    preserve = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="",
        caller_constraints={},
        declared_schema=declared,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(max_depth=0),
        client=ScriptedClient(answer),
        budget=Budget(Limits(max_calls=3, max_retries=0)),
    )
    enrich = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="",
        caller_constraints={},
        declared_schema=declared,
        schema_mode=SCHEMA_ENRICH,
        limits=PlanLimits(max_depth=0),
        client=ScriptedClient(answer),
        budget=Budget(Limits(max_calls=3, max_retries=0)),
    )
    assert [attr.name for attr in preserve.schema.attrs] == ["name"]
    assert [attr.name for attr in enrich.schema.attrs] == ["name", "country"]


@pytest.mark.asyncio
async def test_partition_key_hint_is_not_silently_ignored() -> None:
    client = ScriptedClient(bootstrap(), proposal("continent", ["Asia", "Europe"], [10, 10]))
    result = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="",
        caller_constraints={},
        declared_schema=SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(),
        client=client,
        budget=Budget(Limits(max_calls=5, max_retries=0)),
        partition_key="country",
    )
    assert result.plan.leaves[0].id == "0"
    assert any("did not match requested" in item for item in result.diagnostics)


@pytest.mark.asyncio
async def test_partition_key_hint_applies_only_to_root() -> None:
    client = ScriptedClient(
        bootstrap(constraints={"kind": "city"}),
        proposal("country", ["France", "Germany"], [100, 10]),
        proposal("year", ["2023", "2024"], [10, 10]),
    )

    result = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="cities by country and year",
        caller_context="",
        caller_constraints={},
        declared_schema=SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(max_depth=2),
        client=client,
        budget=Budget(Limits(max_calls=10, max_retries=0)),
        partition_key="country",
    )

    assert [(node.id, node.key) for node in result.plan.nodes] == [
        ("0", "country"),
        ("0.0", "year"),
    ]
    assert [leaf.id for leaf in result.plan.leaves] == ["0.0.0", "0.0.1", "0.1"]
    assert result.plan.leaves[0].constraints == {
        "kind": Exact("city"),
        "country": Exact("France"),
        "year": Exact("2023"),
    }
    assert result.plan.leaves[1].constraints == {
        "kind": Exact("city"),
        "country": Exact("France"),
        "year": Exact("2024"),
    }
    assert result.plan.leaves[2].constraints == {
        "kind": Exact("city"),
        "country": Exact("Germany"),
    }


@pytest.mark.asyncio
async def test_row_by_row_uses_explicit_key_list_or_one_enumeration() -> None:
    explicit = await coordinate_plan(
        mode=MODE_ROW_BY_ROW,
        original_query="cities",
        caller_context="",
        caller_constraints={},
        declared_schema=SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(),
        client=ScriptedClient(bootstrap(constraints={"name": ["A", "B"]})),
        budget=Budget(Limits(max_calls=5, max_retries=0)),
    )
    enumerated_client = ScriptedClient(bootstrap(), encoded(key="name", values=["A", "B", "C"]))
    enumerated = await coordinate_plan(
        mode=MODE_ROW_BY_ROW,
        original_query="cities",
        caller_context="",
        caller_constraints={},
        declared_schema=SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=PlanLimits(),
        client=enumerated_client,
        budget=Budget(Limits(max_calls=6, max_retries=0)),
    )
    assert len(explicit.plan.leaves) == 2
    assert explicit.facts.entity_enumeration_attempted is False
    assert len(enumerated.plan.leaves) == 3
    assert enumerated.facts.entity_enumeration_attempted is True


# ---------------------------------------------- the bounds hold for the coordinator --
class ProposingClient:
    """Answers the bootstrap, then every partition proposal, choosing each proposal's width.

    ``widths`` cycles: a width is how many distinct values the next proposal offers, and a
    width of zero answers with malformed JSON. Every child is estimated far above the row
    target, so every child stays eligible to split again and the tree grows as far as the
    configuration lets it.
    """

    def __init__(self, widths: tuple[int, ...]) -> None:
        self.widths = widths
        self.prompts: list[str] = []

    def bound_for_attempt(self, _prompt: str, _properties: dict[str, Any]) -> float:
        return 0.0

    async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
        budget = properties[C.BUDGET_PROPERTY]
        assert isinstance(budget, Budget)
        with budget.attempt(0.0) as reservation:
            reservation.settle(0.0)
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return bootstrap()
        index = len(self.prompts) - 2
        width = self.widths[index % len(self.widths)]
        if width == 0:
            return "not json"
        values = [f"v{index}-{i}" for i in range(width)]
        return proposal(f"key{index}", values, [10_000] * width)


@pytest.mark.parametrize(
    ("depth", "partitions", "leaves"),
    [
        (0, 4, 24),
        (1, 2, 1),
        (1, 4, 3),
        (2, 1, 24),
        (2, 2, 3),
        (2, 3, 5),
        (2, 4, 24),
        (2, 20, 5),
        (2, 20, 100),
        (2, 20, 200),
        (3, 2, 8),
        (3, 3, 10),
        (3, 2, 100),
        (3, 5, 40),
    ],
)
@pytest.mark.parametrize(
    "widths",
    [(99,), (2,), (3, 0, 2), (0,)],
    ids=["always-max", "always-two", "mixed", "never-parses"],
)
async def test_the_coordinator_stays_inside_every_derived_bound(
    depth: int, partitions: int, leaves: int, widths: tuple[int, ...]
) -> None:
    """Proposals, split nodes and leaves, counted from what actually happened.

    A width of 99 is clipped to ``max_partitions`` so the widest legal split is always
    offered; one past it would be refused and prove nothing about the bound.
    """
    limits = PlanLimits(max_depth=depth, max_partitions=partitions, max_leaves=leaves)
    clipped = tuple(min(width, max(partitions, 2)) if width else 0 for width in widths)
    client = ProposingClient(clipped)

    result = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="",
        caller_constraints={},
        declared_schema=SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=limits,
        client=client,
        budget=Budget(Limits(max_retries=0)),
    )

    proposals_made = len(client.prompts) - 1
    assert proposals_made <= proposal_attempt_limit(limits)
    assert result.plan.attempted_proposals <= proposal_attempt_limit(limits)
    assert result.plan.accepted_internal_nodes <= max_nodes(limits, MODE_PARTITIONED)
    assert len(result.plan.nodes) <= max_nodes(limits, MODE_PARTITIONED)
    assert len(result.plan.leaves) <= leaf_limit(limits, MODE_PARTITIONED)
    assert 1 + proposals_made + len(result.plan.leaves) <= planned_logical_calls(
        limits, MODE_PARTITIONED
    )


async def test_the_proposal_bound_is_reached_when_every_split_is_as_wide_as_allowed() -> None:
    """One proposal per node above the leaf level: 1 + 3 + 9 at depth three and width three.

    The bound is not padded. Each node is proposed for once, so doubling the node count, as
    the leaf-cap formula would, charges proposals the coordinator never makes.
    """
    limits = PlanLimits(max_depth=3, max_partitions=3, max_leaves=27)
    client = ProposingClient((3,))

    result = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="",
        caller_constraints={},
        declared_schema=SCHEMA,
        schema_mode=SCHEMA_PRESERVE,
        limits=limits,
        client=client,
        budget=Budget(Limits(max_retries=0)),
    )

    assert len(client.prompts) - 1 == proposal_attempt_limit(limits) == 13
    assert len(result.plan.leaves) == leaf_limit(limits, MODE_PARTITIONED) == 27
    assert result.plan.accepted_internal_nodes == max_nodes(limits, MODE_PARTITIONED) == 13


def test_the_default_caps_bound_planning_by_the_tree_shape() -> None:
    """Twenty partitions at depth two: 21 proposals and 100 leaves, not 199 and 100."""
    limits = PlanLimits()
    assert (limits.max_partitions, limits.max_depth, limits.max_leaves) == (20, 2, 100)
    assert proposal_attempt_limit(limits) == 21
    assert max_nodes(limits, MODE_PARTITIONED) == 21
    assert leaf_limit(limits, MODE_PARTITIONED) == 100
    assert planned_logical_calls(limits, MODE_PARTITIONED) == 3 + 21 + 100
    assert planned_logical_calls(limits, MODE_PARTITIONED, planning_order=PLANNING_COMBINED) == (
        1 + 21 + 100
    )


# ------------------------------- every schedule, with and without distribution --
KEYED = Schema(
    attrs=[Attr("K0", "string"), Attr("K1", "string"), Attr("K2", "string"), Attr("v", "string")],
    primary_keys=("K0", "K1", "K2"),
    key_from=KEY_FROM_CALLER,
)
DOMAINS = {"K0": ("a", "b", "c"), "K1": ("d", "e", "f"), "K2": ("g", "h", "i")}
RULES = tuple(
    IdentityPartitionRule(
        dimension=dimension,
        ordered_primary_keys=("K0", "K1", "K2"),
        allowed_domain=domain,
        parent_scope={},
        provenance="source_configuration",
    )
    for dimension, domain in DOMAINS.items()
)


class StageClient:
    """Answers every planning stage by what the prompt asks for.

    The fact stages other than the combined bootstrap are answered with malformed text: the
    call is made and counted, the coordinator falls back to the caller's facts, and planning
    goes on to the splits, which is where the counts grow. Every split is as wide as the
    configuration allows and every child is estimated far above the row target.
    """

    def __init__(self, width: int, entities: int) -> None:
        self.width = width
        self.entities = entities
        self.prompts: list[str] = []

    def bound_for_attempt(self, _prompt: str, _properties: dict[str, Any]) -> float:
        return 0.0

    async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
        budget = properties[C.BUDGET_PROPERTY]
        assert isinstance(budget, Budget)
        with budget.attempt(0.0) as reservation:
            reservation.settle(0.0)
        self.prompts.append(prompt)
        index = len(self.prompts)
        if prompt.startswith("Prepare a structured data request"):
            return json.dumps(
                {
                    "ask": "retrieval wording",
                    "constraints": {},
                    "row_estimate": 10_000,
                    "schema": {
                        "attrs": [{"name": attr.name, "type": "string"} for attr in KEYED.attrs],
                        "primary_keys": list(KEYED.primary_keys),
                    },
                }
            )
        if prompt.startswith("Propose one useful partition"):
            values = [f"v{index}-{i}" for i in range(self.width)]
            return proposal(f"key{index}", values, [10_000] * self.width)
        if prompt.startswith("Estimate the distribution of records"):
            line = prompt.split("Declared partition key: ", 1)[1].split("\n", 1)[0]
            key = str(json.loads(line))
            partitions = [
                {"value": value, "estimate": {"expected": 10_000}} for value in DOMAINS[key]
            ]
            return json.dumps(
                {
                    "key": key,
                    "partitions": partitions,
                    "ambiguity": 0.1,
                    "reasoning": "a declared domain",
                }
            )
        if prompt.startswith("Enumerate every entity"):
            values = [{"K0": f"e{i}", "K1": "x", "K2": "y"} for i in range(self.entities)]
            return json.dumps({"keys": ["K0", "K1", "K2"], "values": values})
        return "not json"


async def _plan_with(
    mode: str, order: str, distribution: bool, limits: PlanLimits, client: StageClient
) -> PlanningBundle:
    return await coordinate_plan(
        mode=mode,
        original_query="places",
        caller_context="",
        caller_constraints={},
        declared_schema=KEYED,
        schema_mode=SCHEMA_PRESERVE,
        limits=limits,
        client=client,
        budget=Budget(Limits(max_retries=0)),
        planning_order=order,
        distribution_search=distribution,
        identity_partition_rules=RULES if distribution else (),
    )


@pytest.mark.parametrize("order", PLANNING_ORDERS)
@pytest.mark.parametrize(
    ("mode", "distribution"),
    [
        (MODE_FLAT, False),
        (MODE_PARTITIONED, False),
        (MODE_PARTITIONED, True),
        (MODE_ROW_BY_ROW, False),
    ],
    ids=["flat", "partitioned", "distribution", "row_by_row"],
)
@pytest.mark.parametrize(
    "limits",
    [
        PlanLimits(max_depth=0, max_partitions=4, max_leaves=24),
        PlanLimits(max_depth=1, max_partitions=3, max_leaves=24, max_strategy_candidates=3),
        PlanLimits(max_depth=2, max_partitions=3, max_leaves=9, max_strategy_candidates=2),
        PlanLimits(max_depth=3, max_partitions=3, max_leaves=100, max_strategy_candidates=3),
        PlanLimits(),
    ],
    ids=["depth0", "depth1", "depth2-tight-leaves", "depth3", "defaults"],
)
async def test_every_schedule_stays_inside_the_published_counts(
    order: str, mode: str, distribution: bool, limits: PlanLimits
) -> None:
    """Counted from the calls the coordinator actually made, under every schedule."""
    client = StageClient(width=min(limits.max_partitions, 3), entities=limits.max_leaves)
    result = await _plan_with(mode, order, distribution, limits, client)

    planning = len(client.prompts)
    leaves = len(result.plan.leaves)
    assert planning <= planning_model_calls(
        limits, mode, planning_order=order, distribution_search=distribution
    )
    assert leaves <= leaf_limit(limits, mode)
    assert planning + leaves <= planned_logical_calls(
        limits, mode, planning_order=order, distribution_search=distribution
    )
    assert planning + leaves <= request_calls(
        limits, mode, leaves, planning_order=order, distribution_search=distribution
    )
    assert planning + leaves <= request_calls(
        limits, mode, planning_order=order, distribution_search=distribution
    )
    # The design figure takes no schedule because it covers them all.
    assert planning <= planning_calls(limits, mode)
    assert planning + leaves <= worst_case_attempts(limits, mode)
    # The source's default schedule bounds every other one.
    assert planning <= planning_model_calls(limits, mode, distribution_search=distribution)


@pytest.mark.parametrize(
    ("order", "facts"),
    [
        (PLANNING_COMBINED, 1),
        (PLANNING_LOCKED_SEQUENTIAL, 2),
        (PLANNING_LOCKED_PARALLEL, 2),
        (PLANNING_CARDINALITY_FIRST, 3),
        (PLANNING_SCHEMA_FIRST, 3),
    ],
)
async def test_each_schedule_reaches_its_published_count(order: str, facts: int) -> None:
    """Fact calls, then one proposal for the root and each of its three children."""
    limits = PlanLimits(max_depth=2, max_partitions=3, max_leaves=9)
    client = StageClient(width=3, entities=0)
    result = await _plan_with(MODE_PARTITIONED, order, False, limits, client)

    assert fact_calls(order) == facts
    assert len(client.prompts) == planning_model_calls(
        limits, MODE_PARTITIONED, planning_order=order
    )
    assert len(client.prompts) == facts + 4
    assert len(result.plan.leaves) == leaf_limit(limits, MODE_PARTITIONED) == 9


async def test_distribution_search_reaches_its_published_count() -> None:
    """Every rule is tried at every node that can split: 3 at the root, 2 at each child."""
    limits = PlanLimits(max_depth=2, max_partitions=3, max_leaves=9, max_strategy_candidates=3)
    client = StageClient(width=3, entities=0)
    result = await _plan_with(MODE_PARTITIONED, PLANNING_COMBINED, True, limits, client)

    made = len(client.prompts)
    assert made == 1 + 3 + 3 * 2
    assert made <= planning_model_calls(
        limits, MODE_PARTITIONED, planning_order=PLANNING_COMBINED, distribution_search=True
    )
    assert len(result.plan.leaves) == 9


def test_an_unknown_schedule_is_refused_by_name() -> None:
    with pytest.raises(ConfigError, match="planning_order"):
        planned_logical_calls(PlanLimits(), MODE_PARTITIONED, planning_order="planning.auto")
