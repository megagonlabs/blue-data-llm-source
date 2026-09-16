"""Whether a whole plan fits, asked before any of it is run.

A budget admits one attempt at a time. That is the granularity money is spent at and the
wrong granularity to plan at: a forty-leaf fan-out under a ceiling with room for eight is
not a cheaper version of that plan. Eight leaves are billed, the result is missing four
fifths of its rows, and the caller learns the plan never fitted only after the money is
gone. Sizing the whole thing first is the last moment at which the answer is still free.

Nothing here claims anything, for the same reason the one-attempt check claims nothing: a
claim held while a plan is being weighed takes headroom away from siblings on the same
budget, and they are then refused against an allowance the final ledger says was never
spent. The price of not claiming is that this answer is a snapshot. A sibling can take the
room between the check and the run, so a plan admitted here can still stop partway. What
an admission rules out is a plan that does not fit even with the budget to itself, and
that weaker statement is the one that can be made without blocking every other caller.
``Budget.reserve`` remains the only authority on any particular attempt.
"""

from __future__ import annotations

from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.types import BudgetExceeded, ConfigError


def check_plan(budget: Budget, *, calls: int, cost: float) -> None:
    """Refuse a plan of this many calls and this much money, before any of it is committed.

    ``Budget.check_admissible`` weighs one prospective attempt, and its call test asks
    whether the count has already reached the ceiling. Neither question is the planner's.
    Under a ceiling of ten with two spent, a forty-leaf fan-out passes the one-attempt
    check forty times over and stops at leaf eight.

    Every ceiling in the family binds. The room is the tightest of this budget's and every
    parent's, so a child's own ceiling applies on top of its parent's rather than instead
    of it: a leaf sized against its own limits alone would plan a fan-out on an allowance
    it was never given. An axis carrying no ceiling anywhere in the chain refuses nothing.

    Raises :class:`BudgetExceeded` naming the axis and how far short the budget is. Takes
    nothing and reserves nothing, so an admission is a snapshot rather than a promise; see
    the module docstring. Time and rows are not weighed here, because a plan can state how
    many calls it will make and what they may cost and cannot state how long they will run.

    A plan asking for nothing fits any budget, including a spent one. Reaching a ceiling
    crosses nothing, and the one-attempt check refuses at an exhausted call ceiling because
    it is being asked about an attempt.
    """
    wanted_calls = _checked_calls(calls)
    # Both figures are read before either decides anything. A plan whose cost cannot be
    # read is a caller's bug, and reporting it as "does not fit" sends the planner shrinking
    # a fan-out until nonsense becomes affordable, which it never does.
    wanted_cost = Budget.checked_amount("the plan cost", cost)

    # Asked through the budget's own headroom rather than by walking the chain here. That
    # walk is exactly where a child's ceiling comes to be applied instead of its parent's,
    # and a second copy of the narrowing rule living in the planning layer is a copy that
    # can drift from the one the spending path enforces — which would leave a plan admitted
    # here and refused at its first leaf.
    room_calls = budget.remaining_calls()
    if room_calls is not None and wanted_calls > room_calls:
        raise BudgetExceeded(
            f"this plan needs {wanted_calls} calls and {room_calls} are left "
            f"({wanted_calls - room_calls} short)",
            limit="max_calls",
        )
    room_cost = budget.remaining_cost()
    if room_cost is not None and wanted_cost > room_cost:
        raise BudgetExceeded(
            f"this plan is estimated at {wanted_cost:.6f} and {room_cost:.6f} is left "
            f"({wanted_cost - room_cost:.6f} short)",
            limit="max_cost",
        )


def plan_fits(budget: Budget, *, calls: int, cost: float) -> bool:
    """The question :func:`check_plan` raises on, answered instead.

    For a caller choosing rather than proceeding — sizing a fan-out down until it fits, or
    picking between candidate plans. A plan that does not fit is an ordinary answer there,
    and routing it through exception handling once per candidate makes the ordinary case
    read like the failure.

    A figure that cannot be read still raises. "Does not fit" and "I cannot read what you
    asked" are different answers, and returning False for the second buries a caller's bug
    inside a budget decision.
    """
    try:
        check_plan(budget, calls=calls, cost=cost)
    except BudgetExceeded:
        return False
    return True


def _checked_calls(value: object) -> int:
    """A usable call count, or a refusal naming the field.

    ``bool`` is a subtype of ``int``, so ``calls=True`` type-checks and would weigh a whole
    fan-out as a single call: the plan is admitted against one call's headroom and then
    makes however many it really wanted, which is the overrun this check exists to prevent.

    ``object`` rather than ``int``, because an annotation is a statement about the callers
    a type checker can see and not a guarantee about what arrives.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"the plan call count must be a whole number, got {value!r}")
    if value < 0:
        raise ConfigError(f"the plan call count must not be negative, got {value!r}")
    return value
