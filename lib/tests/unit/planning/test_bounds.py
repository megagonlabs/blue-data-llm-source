"""The worst case, and the two ways a formula like this stops being a bound.

The first is a free variable: a term nothing caps, so the "bound" is whatever that term
happens to be at runtime. The second is a missing multiplier: counting logical calls and
forgetting that each may be retried, which understates by exactly the retry factor.
"""

from __future__ import annotations

from typing import Any

import pytest

from blue_data_llm_source.planning.bounds import (
    MODE_FLAT,
    MODE_PARTITIONED,
    MODE_ROW_BY_ROW,
    MODES,
    PlanLimits,
    attempts,
    distribution_node_attempt_limit,
    execution_calls,
    leaf_limit,
    max_nodes,
    planning_calls,
    worst_case_attempts,
)
from blue_data_llm_source.planning.cardinality import Cardinality
from blue_data_llm_source.planning.plan import FetchPlan, PartitionProposal, build_plan
from blue_data_llm_source.planning.schema import Schema
from blue_data_llm_source.types import Attr, ConfigError


class TestEveryTermIsCapped:
    """No term may depend on anything but configuration."""

    def test_nodes_are_bounded_by_the_leaf_cap(self) -> None:
        """Depth times fan-out compounds, so only the global leaf cap closes this."""
        limits = PlanLimits(max_leaves=10)
        assert max_nodes(limits, MODE_PARTITIONED) < limits.max_leaves

    def test_distribution_attempts_use_the_same_node_term_as_planning_calls(self) -> None:
        limits = PlanLimits(
            max_leaves=7,
            max_schema_candidates=2,
            max_strategy_candidates=3,
        )
        nodes = distribution_node_attempt_limit(limits)
        assert nodes == max_nodes(limits, MODE_PARTITIONED) == 6
        assert planning_calls(limits, MODE_PARTITIONED) == (
            2
            + (2 + limits.max_schema_candidates)
            + nodes * (limits.max_strategy_candidates + 2)
            + limits.max_leaves
        )

    @pytest.mark.parametrize(
        ("leaves", "expected"),
        [(2, 1), (5, 4), (21, 20), (22, 21), (50, 21), (500, 21)],
    )
    def test_the_node_bound_is_the_smaller_of_the_leaf_cap_and_the_tree_shape(
        self, leaves: int, expected: int
    ) -> None:
        """At depth 2 and twenty partitions only 1 + 20 nodes can split, however many leaves."""
        limits = PlanLimits(max_leaves=leaves)
        assert max_nodes(limits, MODE_PARTITIONED) == expected

    def test_the_smallest_legal_cap_still_admits_the_root_node(self) -> None:
        """A partitioned plan records its root whatever its leaf count.

        ``max_leaves - 1`` reads zero here, which is a published ceiling under what the
        plan below it builds — and a caller sized against that ceiling is admitted and
        then runs out of the attempts it reserved.
        """
        assert max_nodes(PlanLimits(max_leaves=1), MODE_PARTITIONED) == 1

    def test_a_flat_plan_has_no_partition_nodes(self) -> None:
        assert max_nodes(PlanLimits(), MODE_FLAT) == 0

    def test_row_by_row_charges_exactly_one_root_node(self) -> None:
        """Its enumeration must sit inside the formula rather than escape as an extra."""
        assert max_nodes(PlanLimits(max_leaves=100), MODE_ROW_BY_ROW) == 1

    @pytest.mark.parametrize("mode", MODES)
    def test_the_whole_bound_is_finite_and_positive(self, mode: str) -> None:
        assert worst_case_attempts(PlanLimits(), mode) > 0


SCHEMA = Schema(attrs=[Attr(name="name"), Attr(name="country")], key="name", key_from="inferred")


def _realizable(mode: str, limits: PlanLimits, values: int) -> FetchPlan:
    """A plan this package can actually build, for a mode and a partition width.

    ``estimate`` is deliberately the widest bucket: the explosion guard collapses a plan
    whose partition count runs past the rows it could hold, and a shape that collapses to
    flat exercises nothing about the partitioned bound.
    """
    names = [f"v{index}" for index in range(values)]
    proposal = PartitionProposal(key="country", values=names) if names else None
    return build_plan(
        mode=mode,
        ask="commercial airports",
        constraints={},
        schema=SCHEMA,
        estimate=Cardinality.UNBOUNDED,
        limits=limits,
        proposal=proposal,
    )


class TestThePublishedBoundCoversEveryPlanThatCanBeBuilt:
    """The bound is a promise about plans, so it is checked against plans.

    A term derived from a cap can be right about the cap and wrong about the smallest
    thing the cap admits, and arithmetic tested only against itself never says so. A
    published count under a realizable plan is worse than no count: a caller sizes a
    ceiling from ``worst_case_attempts``, passes admission, and then needs attempts it
    was never granted.
    """

    @pytest.mark.parametrize("values", [0, 1, 2, 3, 9, 40])
    @pytest.mark.parametrize("max_leaves", [1, 2, 3, 8, 24])
    @pytest.mark.parametrize("mode", MODES)
    def test_no_plan_holds_more_nodes_than_the_bound(
        self, mode: str, max_leaves: int, values: int
    ) -> None:
        limits = PlanLimits(max_leaves=max_leaves)
        plan = _realizable(mode, limits, values)
        # Both the mode asked for and the mode that came back. A guard can collapse the
        # first into the second, and a caller sizes its ceiling before that is known.
        assert len(plan.nodes) <= max_nodes(limits, mode)
        assert len(plan.nodes) <= max_nodes(limits, plan.mode)

    @pytest.mark.parametrize("values", [0, 1, 2, 3, 9, 40])
    @pytest.mark.parametrize("max_leaves", [1, 2, 3, 8, 24])
    @pytest.mark.parametrize("mode", MODES)
    def test_no_plan_holds_more_leaves_than_the_cap(
        self, mode: str, max_leaves: int, values: int
    ) -> None:
        """Every other term is derived from this one, so it has to hold first."""
        limits = PlanLimits(max_leaves=max_leaves)
        assert _realizable(mode, limits, values).leaf_count <= max_leaves

    def test_the_one_leaf_boundary_is_covered(self) -> None:
        """The smallest legal cap, which is where a leaf-derived term reads one too few.

        A partitioned plan at ``max_leaves=1`` still records a root node and still pays
        that node's planning calls.
        """
        limits = PlanLimits(max_leaves=1)
        plan = _realizable(MODE_PARTITIONED, limits, values=3)
        assert plan.leaf_count == 1
        assert len(plan.nodes) == 1
        assert len(plan.nodes) <= max_nodes(limits, MODE_PARTITIONED)


class TestTheRetryMultiplier:
    """The defect that made an earlier formula wrong by the retry factor."""

    def test_attempts_multiply_logical_calls(self) -> None:
        assert attempts(10, PlanLimits(max_retries=2)) == 30

    def test_no_retries_means_one_attempt_each(self) -> None:
        assert attempts(10, PlanLimits(max_retries=0)) == 10

    def test_the_whole_bound_scales_with_the_retry_cap(self) -> None:
        """If it does not, the multiplier is not actually applied to the total."""
        one = worst_case_attempts(PlanLimits(max_retries=0), MODE_PARTITIONED)
        three = worst_case_attempts(PlanLimits(max_retries=2), MODE_PARTITIONED)
        assert three == one * 3

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("retries", [0, 1, 2, 5])
    def test_the_whole_bound_is_its_parts_times_the_multiplier(
        self, mode: str, retries: int
    ) -> None:
        """States the composition outright, so dropping the multiplier fails here too.

        The scaling test above is the same defect seen from one angle, and a property
        this central should not rest on a single assertion.
        """
        limits = PlanLimits(max_retries=retries)
        logical = planning_calls(limits, mode) + execution_calls(limits, leaf_limit(limits, mode))
        assert worst_case_attempts(limits, mode) == logical * (retries + 1)


class TestEachKnobChangesTheBound:
    """One test per knob. A setting that is accepted and then ignored is invisible to a
    type checker and to a linter; only an assertion on behaviour catches it."""

    def test_max_leaves(self) -> None:
        base = worst_case_attempts(PlanLimits(max_leaves=10), MODE_PARTITIONED)
        more = worst_case_attempts(PlanLimits(max_leaves=20), MODE_PARTITIONED)
        assert more > base

    def test_max_continuations(self) -> None:
        base = execution_calls(PlanLimits(max_continuations=3), leaves=4)
        more = execution_calls(PlanLimits(max_continuations=6), leaves=4)
        assert more > base

    def test_max_schema_candidates(self) -> None:
        base = planning_calls(PlanLimits(max_schema_candidates=3), MODE_PARTITIONED)
        more = planning_calls(PlanLimits(max_schema_candidates=6), MODE_PARTITIONED)
        assert more == base + 3

    def test_max_strategy_candidates(self) -> None:
        base = planning_calls(PlanLimits(max_strategy_candidates=5), MODE_PARTITIONED)
        more = planning_calls(PlanLimits(max_strategy_candidates=6), MODE_PARTITIONED)
        assert more > base

    def test_max_retries(self) -> None:
        base = attempts(10, PlanLimits(max_retries=1))
        more = attempts(10, PlanLimits(max_retries=4))
        assert more > base

    def test_empty_leaf_retry(self) -> None:
        """Off by default: it doubles calls on the case where the model has nothing."""
        off = execution_calls(PlanLimits(empty_leaf_retry=False), leaves=4)
        on = execution_calls(PlanLimits(empty_leaf_retry=True), leaves=4)
        assert on == off + 4

    def test_mode(self) -> None:
        flat = worst_case_attempts(PlanLimits(), MODE_FLAT)
        partitioned = worst_case_attempts(PlanLimits(), MODE_PARTITIONED)
        row = worst_case_attempts(PlanLimits(), MODE_ROW_BY_ROW)
        assert flat < row < partitioned


class TestFlatIsTheCheapShape:
    def test_a_flat_plan_pays_for_no_partition_planning(self) -> None:
        limits = PlanLimits()
        assert planning_calls(limits, MODE_FLAT) < planning_calls(limits, MODE_PARTITIONED)

    def test_a_flat_plan_pays_for_no_rewrite(self) -> None:
        """Its one leaf asks the refined question itself; there is nothing to rewrite."""
        limits = PlanLimits(max_leaves=20)
        # analyse + estimate + schema pool + candidates + k enrichments
        assert planning_calls(limits, MODE_FLAT) == 2 + 2 + limits.max_schema_candidates


class TestExecution:
    def test_zero_leaves_costs_nothing(self) -> None:
        assert execution_calls(PlanLimits(), leaves=0) == 0

    def test_each_leaf_pays_its_call_plus_continuations(self) -> None:
        assert execution_calls(PlanLimits(max_continuations=3), leaves=5) == 5 * 4


class TestImpossibleConfigurationIsRefused:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_leaves": 0},
            {"max_leaves": -1},
            {"max_continuations": -1},
            {"max_retries": -1},
            {"max_schema_candidates": -1},
            {"max_strategy_candidates": -1},
        ],
    )
    def test_out_of_range(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(ConfigError):
            PlanLimits(**kwargs)

    @pytest.mark.parametrize("bad", ["3", 3.5, None, True])
    def test_a_non_integer_cap_is_refused(self, bad: object) -> None:
        """True is an int in Python, and max_leaves=True would silently mean one leaf."""
        with pytest.raises(ConfigError):
            PlanLimits(max_leaves=bad)  # pyright: ignore[reportArgumentType]

    def test_an_unknown_mode_is_refused_by_name(self) -> None:
        """An unrecognised mode must be refused, not quietly treated as some default."""
        with pytest.raises(ConfigError, match="unknown mode"):
            planning_calls(PlanLimits(), "auto")

    @pytest.mark.parametrize("bad", [-1, True])
    def test_a_bad_leaf_count_is_refused(self, bad: int) -> None:
        """True type-checks as an int and would silently plan one leaf."""
        with pytest.raises(ConfigError):
            execution_calls(PlanLimits(), bad)
