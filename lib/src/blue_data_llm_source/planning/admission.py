"""Whether a whole plan fits, asked before any of it is run.

A budget admits attempts individually. This module checks whether an entire plan fits
before execution. The check reserves nothing, so admission is a snapshot: concurrent
spend can still stop the plan later. ``Budget.reserve`` decides each attempt.
"""

from __future__ import annotations

from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.types import BudgetExceeded, ConfigError


def check_plan(budget: Budget, *, calls: int, cost: float) -> None:
    """Refuse a plan of this many calls and this much money, before any of it is committed.

    The available room is the tightest of this budget's and its parents' ceilings.
    An axis without a ceiling anywhere in the chain refuses nothing.

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

    Useful when sizing a fan-out or choosing between plans. Invalid input still raises;
    False means a valid plan exceeds the available budget.
    """
    try:
        check_plan(budget, calls=calls, cost=cost)
    except BudgetExceeded:
        return False
    return True


def _checked_calls(value: object) -> int:
    """A usable call count, or a refusal naming the field.

    Reject booleans explicitly because ``bool`` is a subtype of ``int``. The ``object``
    annotation reflects that callers may supply unchecked values.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"the plan call count must be a whole number, got {value!r}")
    if value < 0:
        raise ConfigError(f"the plan call count must not be negative, got {value!r}")
    return value
