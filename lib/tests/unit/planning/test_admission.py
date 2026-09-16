"""Plan admission: the questions a one-attempt check cannot answer.

Two failures are in view. A fan-out sized against one attempt's headroom runs until a
ceiling stops it partway, having spent everything up to that point and returning a partial
answer. And a fan-out sized against a child budget alone ignores the parent that actually
holds the allowance, so it is admitted here and refused at its first leaf.
"""

from __future__ import annotations

import math

import pytest

from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.planning.admission import check_plan, plan_fits
from blue_data_llm_source.types import BudgetExceeded, ConfigError


class TestThePlanIsWeighedWhole:
    """The property the one-attempt check cannot supply."""

    def test_a_fan_out_larger_than_the_call_ceiling_is_refused(self) -> None:
        """The one-attempt check admits this forty times over and stops at leaf eight."""
        budget = Budget(Limits(max_calls=10))
        budget.reserve().settle(0.0)
        budget.reserve().settle(0.0)

        with pytest.raises(BudgetExceeded) as caught:
            check_plan(budget, calls=40, cost=0.0)

        assert caught.value.limit == "max_calls"

    def test_a_fan_out_that_fits_exactly_is_admitted(self) -> None:
        """Reaching a ceiling crosses nothing; a plan for the last calls left must run."""
        budget = Budget(Limits(max_calls=10))
        budget.reserve().settle(0.0)
        budget.reserve().settle(0.0)

        check_plan(budget, calls=8, cost=0.0)

    def test_one_call_more_than_is_left_is_refused(self) -> None:
        budget = Budget(Limits(max_calls=10))
        budget.reserve().settle(0.0)
        budget.reserve().settle(0.0)

        assert not plan_fits(budget, calls=9, cost=0.0)

    def test_the_whole_cost_is_weighed_against_what_is_already_committed(self) -> None:
        budget = Budget(Limits(max_cost=1.0))
        budget.reserve(0.40).settle(0.40)

        assert plan_fits(budget, calls=1, cost=0.60)
        assert not plan_fits(budget, calls=1, cost=0.70)

    def test_an_outstanding_reservation_counts_against_a_plan(self) -> None:
        """A claim that has not settled yet is money the plan cannot also have.

        Weighing settled spend alone would admit a plan alongside every attempt still in
        flight, which is the overshoot reserving exists to prevent.
        """
        budget = Budget(Limits(max_cost=1.0))
        outstanding = budget.reserve(0.80)

        assert not plan_fits(budget, calls=1, cost=0.50)

        outstanding.settle(0.10)
        assert plan_fits(budget, calls=1, cost=0.50)

    def test_a_one_call_plan_agrees_with_the_one_attempt_check(self) -> None:
        """The two must not disagree about a single call, or the planner cannot use both."""
        budget = Budget(Limits(max_calls=1))
        budget.check_admissible(0.0)
        check_plan(budget, calls=1, cost=0.0)

        budget.reserve().settle(0.0)

        with pytest.raises(BudgetExceeded):
            budget.check_admissible(0.0)
        with pytest.raises(BudgetExceeded):
            check_plan(budget, calls=1, cost=0.0)

    def test_a_plan_that_asks_for_nothing_fits_a_spent_budget(self) -> None:
        """Zero calls and no money cannot cross a ceiling, however little is left.

        The one-attempt check refuses here, correctly: it is being asked about an attempt,
        and there is no room for one.
        """
        budget = Budget(Limits(max_calls=1, max_cost=0.10))
        budget.reserve(0.10).settle(0.10)

        check_plan(budget, calls=0, cost=0.0)
        with pytest.raises(BudgetExceeded):
            budget.check_admissible(0.0)


class TestEveryCeilingInTheFamilyBinds:
    """A child's ceiling applies on top of its parent's, never instead of it."""

    def test_a_parents_call_ceiling_refuses_a_childs_plan(self) -> None:
        parent = Budget(Limits(max_calls=3))
        leaf = parent.derive()

        with pytest.raises(BudgetExceeded) as caught:
            check_plan(leaf, calls=5, cost=0.0)

        assert caught.value.limit == "max_calls"

    def test_a_parents_cost_ceiling_refuses_a_childs_plan(self) -> None:
        parent = Budget(Limits(max_cost=1.0))
        leaf = parent.derive()

        with pytest.raises(BudgetExceeded) as caught:
            check_plan(leaf, calls=1, cost=2.0)

        assert caught.value.limit == "max_cost"

    def test_a_childs_own_ceiling_narrows_a_plan_its_parent_would_admit(self) -> None:
        parent = Budget(Limits(max_calls=10))
        leaf = parent.derive(Limits(max_calls=2))

        assert plan_fits(parent, calls=5, cost=0.0)
        assert not plan_fits(leaf, calls=5, cost=0.0)

    def test_a_child_ceiling_wider_than_its_parents_does_not_widen_the_plan(self) -> None:
        """A derived budget spends what the parent has left, whatever it was handed."""
        parent = Budget(Limits(max_calls=3, max_cost=1.0))
        leaf = parent.derive(Limits(max_calls=100, max_cost=100.0))

        assert not plan_fits(leaf, calls=4, cost=0.0)
        assert not plan_fits(leaf, calls=1, cost=1.5)
        assert plan_fits(leaf, calls=3, cost=1.0)

    def test_a_siblings_spend_narrows_the_next_leafs_plan(self) -> None:
        """Leaves share the parent's ledger, so what one spent the next cannot plan on."""
        parent = Budget(Limits(max_cost=1.0))
        parent.derive().reserve(0.80).settle(0.80)

        assert not plan_fits(parent.derive(), calls=1, cost=0.50)

    def test_a_grandparents_ceiling_still_binds(self) -> None:
        """The walk has to reach the whole chain, not just one level up."""
        root = Budget(Limits(max_calls=2, max_cost=0.50))
        middle = root.derive()
        leaf = middle.derive()

        assert not plan_fits(leaf, calls=3, cost=0.0)
        assert not plan_fits(leaf, calls=1, cost=0.60)
        assert plan_fits(leaf, calls=2, cost=0.50)


class TestAnAxisWithNoCeilingRefusesNothing:
    def test_an_unlimited_budget_admits_any_plan(self) -> None:
        assert plan_fits(Budget(), calls=1_000_000, cost=1_000_000.0)

    def test_a_cost_ceiling_alone_does_not_bound_the_call_count(self) -> None:
        budget = Budget(Limits(max_cost=1.0))
        assert plan_fits(budget, calls=10_000, cost=0.50)

    def test_a_call_ceiling_alone_does_not_bound_the_cost(self) -> None:
        budget = Budget(Limits(max_calls=100))
        assert plan_fits(budget, calls=10, cost=1_000_000_000.0)

    def test_a_ceiling_set_only_on_a_parent_still_binds(self) -> None:
        """A child with no limits of its own is bounded entirely by its parent."""
        leaf = Budget(Limits(max_cost=5.0)).derive()
        assert not plan_fits(leaf, calls=1, cost=6.0)


class TestNothingIsClaimed:
    """Holding a claim across this check would refuse the siblings it shares a budget with."""

    def test_an_admitted_plan_consumes_no_call_and_no_money(self) -> None:
        budget = Budget(Limits(max_calls=4, max_cost=1.0))

        for _ in range(3):
            check_plan(budget, calls=4, cost=1.0)

        assert budget.calls == 0
        assert budget.committed == 0.0
        # The whole allowance is still there to be spent.
        for _ in range(4):
            budget.reserve(0.25)

    def test_a_refused_plan_leaves_the_budget_spendable(self) -> None:
        parent = Budget(Limits(max_calls=2, max_cost=1.0))

        assert not plan_fits(parent.derive(), calls=50, cost=50.0)

        assert parent.calls == 0
        assert parent.committed == 0.0
        parent.derive().reserve(1.0)

    def test_a_refused_plan_does_not_report_the_run_as_stopped(self) -> None:
        """Nothing ran, so nothing was stopped.

        ``stopped_by`` is read back as the reason a fetch ended. Marking it while a planner
        is still weighing candidates would report a limit stop on a run that went on to
        finish inside its ceilings, and the shrunk plan that actually ran would carry the
        rejected one's verdict.
        """
        budget = Budget(Limits(max_calls=1, max_cost=0.10))

        assert not plan_fits(budget, calls=9, cost=9.0)

        assert budget.stopped_by is None


class TestReportedHeadroomIsPlannable:
    """Headroom a plan the size of it cannot claim is not headroom."""

    @pytest.mark.parametrize("ceiling", [0.1, 0.3, 1.0, 7.77])
    @pytest.mark.parametrize("spent", [0.0, 0.03, 0.1])
    def test_a_plan_the_size_of_the_reported_room_fits(self, ceiling: float, spent: float) -> None:
        budget = Budget(Limits(max_cost=ceiling, max_calls=4))
        if spent:
            budget.reserve(spent).settle(spent)
        room_cost = budget.remaining_cost()
        room_calls = budget.remaining_calls()
        assert room_cost is not None and room_calls is not None

        assert plan_fits(budget, calls=room_calls, cost=room_cost)

    @pytest.mark.parametrize("ceiling", [0.1, 0.3, 1.0, 7.77])
    def test_a_hair_more_than_the_reported_room_does_not_fit(self, ceiling: float) -> None:
        budget = Budget(Limits(max_cost=ceiling))
        room_cost = budget.remaining_cost()
        assert room_cost is not None

        assert not plan_fits(budget, calls=0, cost=math.nextafter(room_cost, math.inf))

    def test_one_more_call_than_the_reported_room_does_not_fit(self) -> None:
        budget = Budget(Limits(max_calls=6))
        budget.reserve().settle(0.0)
        room_calls = budget.remaining_calls()
        assert room_calls == 5

        assert plan_fits(budget, calls=room_calls, cost=0.0)
        assert not plan_fits(budget, calls=room_calls + 1, cost=0.0)


class TestTheRefusalSaysWhatIsWrong:
    def test_the_call_refusal_names_the_axis_and_the_shortfall(self) -> None:
        budget = Budget(Limits(max_calls=10))

        with pytest.raises(BudgetExceeded) as caught:
            check_plan(budget, calls=40, cost=0.0)

        assert caught.value.limit == "max_calls"
        assert "30" in str(caught.value), "the caller has to be told how far short it is"

    def test_the_cost_refusal_names_the_axis_and_the_shortfall(self) -> None:
        budget = Budget(Limits(max_cost=1.0))

        with pytest.raises(BudgetExceeded) as caught:
            check_plan(budget, calls=1, cost=2.5)

        assert caught.value.limit == "max_cost"
        assert "1.500000" in str(caught.value)

    def test_the_call_axis_is_named_when_both_would_refuse(self) -> None:
        """One deterministic answer, in the order the spending path checks the axes."""
        budget = Budget(Limits(max_calls=1, max_cost=0.10))

        with pytest.raises(BudgetExceeded) as caught:
            check_plan(budget, calls=50, cost=50.0)

        assert caught.value.limit == "max_calls"


class TestAFigureThatCannotBeReadIsRefused:
    @pytest.mark.parametrize(
        "bad", [float("nan"), float("inf"), float("-inf"), -1.0, "free", True, None]
    )
    def test_an_unusable_cost_is_refused_as_configuration(self, bad: object) -> None:
        """A non-finite figure compares false against every ceiling and would fit anything."""
        budget = Budget(Limits(max_cost=0.10))
        with pytest.raises(ConfigError):
            check_plan(budget, calls=1, cost=bad)  # pyright: ignore[reportArgumentType]

    @pytest.mark.parametrize("bad", [-1, 1.5, "40", None])
    def test_an_unusable_call_count_is_refused_as_configuration(self, bad: object) -> None:
        budget = Budget(Limits(max_calls=10))
        with pytest.raises(ConfigError):
            check_plan(budget, calls=bad, cost=0.0)  # pyright: ignore[reportArgumentType]

    def test_a_true_call_count_is_not_a_plan_of_one_call(self) -> None:
        """bool is a subtype of int, so this type-checks and needs a guard of its own.

        Read as one call, a fan-out is admitted against one call's headroom and then makes
        however many it meant. No checker or linter sees it.
        """
        budget = Budget(Limits(max_calls=10))
        with pytest.raises(ConfigError):
            check_plan(budget, calls=True, cost=0.0)

    def test_both_figures_are_read_before_either_decides(self) -> None:
        """An unreadable cost is a caller's bug even when the call count is hopeless.

        Reported as a refusal, it would send the planner shrinking a fan-out until the
        nonsense became affordable.
        """
        budget = Budget(Limits(max_calls=1, max_cost=0.10))
        with pytest.raises(ConfigError):
            check_plan(budget, calls=999, cost=float("nan"))


class TestPlanFitsAnswersRatherThanRaises:
    def test_it_reports_a_plan_that_fits(self) -> None:
        assert plan_fits(Budget(Limits(max_calls=4, max_cost=1.0)), calls=4, cost=1.0)

    def test_it_reports_a_plan_that_does_not(self) -> None:
        assert not plan_fits(Budget(Limits(max_calls=4)), calls=5, cost=0.0)

    def test_a_figure_that_cannot_be_read_still_raises(self) -> None:
        """False would bury a caller's bug as a budget decision."""
        budget = Budget(Limits(max_calls=10))
        with pytest.raises(ConfigError):
            plan_fits(budget, calls=-1, cost=0.0)

    def test_shrinking_a_plan_until_it_fits_needs_no_exception_handling(self) -> None:
        """The caller this exists for: sizing a fan-out down to what is left."""
        budget = Budget(Limits(max_calls=20, max_cost=2.0))
        budget.reserve(0.50).settle(0.50)

        leaves = 12
        while leaves and not plan_fits(budget, calls=leaves, cost=0.25 * leaves):
            leaves -= 1

        assert leaves == 6, "1.50 of room at 0.25 a leaf"
        assert budget.calls == 1, "weighing every candidate spent nothing"
