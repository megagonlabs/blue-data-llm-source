"""The worst case, before anything is asked.

A bound that cannot be computed before the first call is not a bound, so everything here
is arithmetic over configuration. No model output, no plan, no measurement.

The subtle term is ``max_nodes``. Depth and fan-out compound — three levels of ten is a
thousand leaves — so capping those two alone caps nothing useful, and a formula that leaves
the node count free is not closed however it is written. The global leaf cap closes it:
every internal node splits into at least two children, so a tree with L leaves holds at most
L-1 of them, and never fewer than the one root a split plan always has.

The leaf cap is not the tight term once it is large, though. A split has at most
``max_partitions`` children and only a node above ``max_depth`` may split, so a partitioned
tree holds at most ``p**d`` nodes at depth ``d``. Every bound below that counts nodes or
leaves takes the smaller of the two readings, so raising the leaf cap for large tables does
not also raise the planning overhead that the tree's shape already rules out.

Cost is not computed here. It is a sum over call classes with a per-class prompt and
output cap, and it needs the pricing the client owns; this module supplies the counts
that sum is over.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from blue_data_llm_source.llm.budget import check_retries
from blue_data_llm_source.planning.context import (
    PLANNING_COMBINED,
    PLANNING_LOCKED_PARALLEL,
    PLANNING_LOCKED_SEQUENTIAL,
    PLANNING_SCHEMA_FIRST,
    validate_planning_order,
)
from blue_data_llm_source.types import ConfigError

MODE_FLAT: Final = "flat"
MODE_PARTITIONED: Final = "partitioned"
MODE_ROW_BY_ROW: Final = "row_by_row"

MODES: Final = (MODE_FLAT, MODE_PARTITIONED, MODE_ROW_BY_ROW)


@dataclass(frozen=True)
class PlanLimits:
    """The caps a worst case is computed from.

    The planner reads these settings at one seam so configured limits reach the bound.
    """

    max_leaves: int = 100
    max_depth: int = 2
    max_partitions: int = 20
    target_rows_per_leaf: int = 50
    max_continuations: int = 0
    """How many times a leaf may ask again for what it did not get. Zero by default.

    Each continuation is a billed call. The same default applies at every entry point.
    """

    max_schema_candidates: int = 3
    max_strategy_candidates: int = 5
    max_retries: int = 2
    max_excluded_keys: int = 50
    """How many already-seen keys a continuation prompt may list.

    A real knob, not a detail: the exclusion list is model-produced text re-entering a
    prompt, so an uncapped one grows until it dominates the request it is attached to, and
    a prompt whose size cannot be bounded makes the cost bound unbounded too.
    """
    max_exclusion_bytes: int = 16_384
    """Maximum encoded bytes occupied by whole exclusion objects in one prompt."""

    ambiguity_threshold: float = 0.8
    empty_leaf_retry: bool = False

    def __post_init__(self) -> None:
        # Validated here rather than where the arithmetic happens, so an impossible
        # configuration is refused when it is written rather than when a plan is sized.
        for name in (
            "max_leaves",
            "max_depth",
            "max_partitions",
            "target_rows_per_leaf",
            "max_continuations",
            "max_schema_candidates",
            "max_strategy_candidates",
            "max_retries",
            "max_excluded_keys",
            "max_exclusion_bytes",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ConfigError(f"{name} must be an integer, got {value!r}")
            if value < 0:
                raise ConfigError(f"{name} must not be negative, got {value!r}")
        # The same ceiling on retries as the run's own Limits, so no attempt count computed
        # here can exceed the one the ledger will ever be asked to admit.
        check_retries(self.max_retries, "max_retries")
        # bool only: the annotation covers non-numbers for our own callers, but bool is a
        # subtype of int, and ambiguity_threshold=True would silently mean 1.0.
        if isinstance(self.ambiguity_threshold, bool):
            raise ConfigError(
                f"ambiguity_threshold must be a number, got {self.ambiguity_threshold!r}"
            )
        if not 0.0 <= float(self.ambiguity_threshold) <= 1.0:
            raise ConfigError(
                f"ambiguity_threshold must be within 0..1, got {self.ambiguity_threshold!r}"
            )
        if self.max_leaves < 1:
            raise ConfigError(f"max_leaves must be at least 1, got {self.max_leaves!r}")
        if self.target_rows_per_leaf < 1:
            raise ConfigError(
                f"target_rows_per_leaf must be at least 1, got {self.target_rows_per_leaf!r}"
            )


def _splittable_nodes(limits: PlanLimits) -> int:
    """Nodes a partitioned tree can hold above ``max_depth``, where a split may happen.

    ``sum(p**d for d in range(max_depth))``, with nothing below the root when no split can
    succeed: a proposal needs two to ``max_partitions`` distinct values, and a single-leaf
    cap refuses every expansion.
    """
    width = limits.max_partitions if limits.max_partitions >= 2 and limits.max_leaves > 1 else 0
    total, level = 0, 1
    # Summed level by level and stopped at the leaf-cap reading, which bounds the same count
    # from the other side, so a deep, wide configuration never builds a huge integer.
    ceiling = 2 * limits.max_leaves - 1
    for _ in range(limits.max_depth):
        total += level
        if total >= ceiling:
            return ceiling
        level *= width
    return total


def proposal_attempt_limit(limits: PlanLimits) -> int:
    """The maximum number of partition proposals in one request.

    The coordinator pops each frontier leaf once and proposes for it at most once, and only a
    leaf above ``max_depth`` is eligible. So proposals number at most the nodes that can
    exist above that depth. The same count is bounded from the leaf cap as well: a proposed
    node either becomes internal, of which a tree with L leaves has at most L-1, or stays a
    leaf, of which there are at most L. The limit is the smaller of the two.
    """
    return min(2 * limits.max_leaves - 1, _splittable_nodes(limits))


def leaf_limit(limits: PlanLimits, mode: str) -> int:
    """The most leaves a plan in this mode can run.

    ``flat`` is one. ``row_by_row`` runs one leaf per enumerated entity, up to the cap. A
    partitioned tree has at most ``max_partitions`` children per split and ``max_depth``
    levels of splits, so it cannot outgrow ``max_partitions ** max_depth`` whatever the cap.
    """
    _check_mode(mode)
    if mode == MODE_FLAT:
        return 1
    if mode == MODE_ROW_BY_ROW:
        return limits.max_leaves
    if limits.max_partitions < 2:
        return 1
    return max(1, min(limits.max_leaves, limits.max_partitions**limits.max_depth))


def distribution_node_attempt_limit(limits: PlanLimits) -> int:
    """Candidate-bearing nodes the opt-in distribution stage may attempt.

    The planning bound charges ``max_strategy_candidates`` calls for each node in
    :func:`max_nodes`.  An attempt therefore consumes this allowance whether its
    candidates are accepted, malformed, ambiguous, or rejected by a later plan guard.
    """
    return max_nodes(limits, MODE_PARTITIONED)


SPLIT_POLICY_ALWAYS: Final = "always"
SPLIT_POLICY_EVIDENCE: Final = "evidence"


def probe_calls(mode: str, split_policy: str = SPLIT_POLICY_ALWAYS) -> int:
    """Logical calls the root probe makes: one under ``split_policy="evidence"``, else none.

    The probe is one ask of the root leaf with no continuation and no empty-answer retry, so
    a single logical call bounds it; its retries are the transport attempts every accessor
    multiplies by. Only a partitioned plan probes.
    """
    _check_mode(mode)
    if split_policy not in (SPLIT_POLICY_ALWAYS, SPLIT_POLICY_EVIDENCE):
        raise ConfigError(f"unknown split policy {split_policy!r}")
    return 1 if mode == MODE_PARTITIONED and split_policy == SPLIT_POLICY_EVIDENCE else 0


def fact_calls(planning_order: str) -> int:
    """Model calls the planning schedule makes before any split or enumeration.

    ``planning.combined`` asks one bootstrap question. The locked schedules ask for the
    analysis and the cardinality and keep the caller's schema. ``planning.cardinality_first``
    and ``planning.schema_first`` ask all three.
    """
    order = validate_planning_order(planning_order)
    if order == PLANNING_COMBINED:
        return 1
    if order in (PLANNING_LOCKED_SEQUENTIAL, PLANNING_LOCKED_PARALLEL):
        return 2
    return 3


def planning_model_calls(
    limits: PlanLimits,
    mode: str,
    *,
    planning_order: str = PLANNING_SCHEMA_FIRST,
    distribution_search: bool = False,
    split_policy: str = SPLIT_POLICY_ALWAYS,
) -> int:
    """Logical model calls one request's planning can make, at worst, under this schedule.

    The schedule's fact calls, then the work that shapes the plan. A partitioned plan either
    proposes a split for each node it may try, or, with distribution search, asks for one
    distribution per candidate rule at each candidate-bearing node; the coordinator takes
    one path or the other, never both. A row-by-row plan asks once for the entity list.
    ``flat`` plans nothing.

    The default schedule is the one ``LlmSource`` runs when ``plan.planning_order`` is not
    set, and it is also the one with the most fact calls.

    Under ``split_policy="evidence"`` the root probe runs between the facts and the first
    proposal, and is charged here with them (:func:`probe_calls`). When it is accepted no
    proposal follows, so the charge only ever over-counts.
    """
    _check_mode(mode)
    if mode == MODE_FLAT:
        return 0
    facts = fact_calls(planning_order) + probe_calls(mode, split_policy)
    if mode == MODE_ROW_BY_ROW:
        return facts + 1
    if distribution_search:
        return facts + distribution_node_attempt_limit(limits) * limits.max_strategy_candidates
    return facts + proposal_attempt_limit(limits)


def planned_logical_calls(
    limits: PlanLimits,
    mode: str,
    *,
    planning_order: str = PLANNING_SCHEMA_FIRST,
    distribution_search: bool = False,
    split_policy: str = SPLIT_POLICY_ALWAYS,
) -> int:
    """Logical model calls one request in this mode can make, at worst, the plan included.

    :func:`planning_model_calls` for the schedule and distribution setting the request runs
    with, plus the calls its leaves then make. ``flat`` has no plan to build and is one leaf.
    """
    _check_mode(mode)
    per_leaf = 1 + limits.max_continuations + (1 if limits.empty_leaf_retry else 0)
    planning = planning_model_calls(
        limits,
        mode,
        planning_order=planning_order,
        distribution_search=distribution_search,
        split_policy=split_policy,
    )
    return planning + leaf_limit(limits, mode) * per_leaf


def planned_provider_attempts(
    limits: PlanLimits,
    mode: str,
    *,
    planning_order: str = PLANNING_SCHEMA_FIRST,
    distribution_search: bool = False,
    split_policy: str = SPLIT_POLICY_ALWAYS,
) -> int:
    """:func:`planned_logical_calls` multiplied by the configured transport attempts."""
    logical = planned_logical_calls(
        limits,
        mode,
        planning_order=planning_order,
        distribution_search=distribution_search,
        split_policy=split_policy,
    )
    return attempts(logical, limits)


def max_nodes(limits: PlanLimits, mode: str) -> int:
    """The most internal partition nodes a plan in this mode can hold.

    ``row_by_row`` has exactly one: the root, whose enumeration produces the entity set
    when the caller's constraints did not carry one. Charging that call to a root node is
    what keeps it inside this formula rather than escaping it as an unassigned extra.

    A partitioned plan records that root whatever its leaf count, so one is the floor.
    ``max_leaves - 1`` alone reads zero at ``max_leaves=1`` and publishes a ceiling under
    what the smallest legal plan builds — and a ceiling a plan can pass through is worse
    than none, because a caller sizes a budget from ``worst_case_attempts``, is admitted,
    and then needs attempts it was never allowed. Only a node above ``max_depth`` can split,
    so the count is also at most the nodes the tree can hold there.
    """
    _check_mode(mode)
    if mode == MODE_FLAT:
        return 0
    if mode == MODE_ROW_BY_ROW:
        return 1
    return max(1, min(limits.max_leaves - 1, _splittable_nodes(limits)))


def planning_calls(
    limits: PlanLimits, mode: str, *, split_policy: str = SPLIT_POLICY_ALWAYS
) -> int:
    """Logical model calls planning can make, at worst.

    One to analyze and one to estimate; a schema pool, a candidate set, and one
    enrichment per candidate; then per partition node, one distribution estimate per
    candidate column plus a selection and an enumeration; then one rewrite per leaf.

    This bound covers every planning schedule, including distribution search. For a
    schedule-specific count, use :func:`planning_model_calls`.
    """
    _check_mode(mode)
    schema = 2 + limits.max_schema_candidates
    per_node = limits.max_strategy_candidates + 2
    nodes = max_nodes(limits, mode) * per_node
    # A flat plan's single leaf asks the refined question itself, so there is nothing to
    # rewrite it into. Charging a rewrite there would inflate the bound on the one shape
    # where the caller is most likely to be watching the cost.
    rewrites = 0 if mode == MODE_FLAT else leaf_limit(limits, mode)
    return 2 + schema + nodes + rewrites + probe_calls(mode, split_policy)


def execution_calls(limits: PlanLimits, leaves: int) -> int:
    """Logical model calls execution can make, at worst, for a plan of this many leaves.

    Each leaf makes one call plus its continuations. An enabled zero-row retry adds one
    call per leaf.
    """
    # bool only. The annotation already excludes non-integers for our own callers, but
    # bool is a subtype of int, so `leaves=True` type-checks and would silently plan one
    # leaf. That is the same trap the estimate parser has to dodge.
    if isinstance(leaves, bool):
        raise ConfigError(f"leaves must be an integer, got {leaves!r}")
    if leaves < 0:
        raise ConfigError(f"leaves must not be negative, got {leaves!r}")
    per_leaf = 1 + limits.max_continuations + (1 if limits.empty_leaf_retry else 0)
    return leaves * per_leaf


def attempts(logical_calls: int, limits: PlanLimits) -> int:
    """Provider attempts for a number of logical calls.

    A retry consumes budget on every real provider call, not once per logical task, so
    this multiplication is the difference between a bound and a figure wrong by the retry
    factor. Applying it to the call count but not to the cost sum is the easy mistake: a
    class permitted three attempts then reserves three calls and one call's money.
    """
    if isinstance(logical_calls, bool):
        raise ConfigError(f"logical_calls must be an integer, got {logical_calls!r}")
    if logical_calls < 0:
        raise ConfigError(f"logical_calls must not be negative, got {logical_calls!r}")
    return logical_calls * (limits.max_retries + 1)


def request_calls(
    limits: PlanLimits,
    mode: str,
    leaves: int | None = None,
    *,
    planning_order: str = PLANNING_SCHEMA_FIRST,
    distribution_search: bool = False,
    split_policy: str = SPLIT_POLICY_ALWAYS,
) -> int:
    """Logical model calls a whole request in this mode makes, at worst.

    Includes the schedule's planning calls outside flat, then the leaf calls.
    :func:`planning_calls` instead sizes all supported planning schedules.

    ``leaves`` is the plan's real count once there is a plan. Before there is one the most
    leaves the mode can run (:func:`leaf_limit`) stands in for it, because a bound that
    waits for the analysis call has already let the request spend money it was not
    admitted for. Flat is the exception and is not sized
    against the cap: it asks the caller's question once and is one leaf by construction, so
    charging it the cap would refuse the default path a budget that comfortably fits it.

    Logical calls, not provider attempts. The retry factor belongs to the client's own
    ``limits.max_retries`` and to any call ceiling narrowing it, neither of which is visible
    from here. :meth:`LlmSource._attempts_allowed` reads the retry depth the whole budget
    chain permits and multiplies this; :meth:`LlmSource._attempts_reachable` narrows that
    by a call ceiling for the published estimate, which is a different question and
    deliberately a different accessor.
    """
    _check_mode(mode)
    if mode == MODE_FLAT:
        return execution_calls(limits, 1)
    planning = planning_model_calls(
        limits,
        mode,
        planning_order=planning_order,
        distribution_search=distribution_search,
        split_policy=split_policy,
    )
    return planning + execution_calls(
        limits, leaf_limit(limits, mode) if leaves is None else leaves
    )


def worst_case_attempts(
    limits: PlanLimits, mode: str, *, split_policy: str = SPLIT_POLICY_ALWAYS
) -> int:
    """Every attempt a whole request in this mode can make, before it starts.

    This is what a recommendation is sized against: no plan exists yet, so the leaf count
    is :func:`leaf_limit` rather than a real count. Built on :func:`planning_calls`, so it
    bounds every planning schedule and distribution setting at once.
    """
    logical = planning_calls(limits, mode, split_policy=split_policy) + execution_calls(
        limits, leaf_limit(limits, mode)
    )
    return attempts(logical, limits)


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ConfigError(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")
