"""The worst case, before anything is asked.

A bound that cannot be computed before the first call is not a bound, so everything here
is arithmetic over configuration. No model output, no plan, no measurement.

The subtle term is ``max_nodes``. Depth and fan-out compound — three levels of ten is a
thousand leaves — so capping those two caps nothing useful, and a formula that leaves the
node count free is not closed however it is written. The global leaf cap closes it: every
internal node splits into at least two children, so a tree with L leaves holds at most
L-1 of them, and never fewer than the one root a split plan always has. Bounding leaves
therefore bounds nodes without a second knob to keep consistent.

Cost is not computed here. It is a sum over call classes with a per-class prompt and
output cap, and it needs the pricing the client owns; this module supplies the counts
that sum is over.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from blue_data_llm_source.types import ConfigError

MODE_FLAT: Final = "flat"
MODE_PARTITIONED: Final = "partitioned"
MODE_ROW_BY_ROW: Final = "row_by_row"

MODES: Final = (MODE_FLAT, MODE_PARTITIONED, MODE_ROW_BY_ROW)


@dataclass(frozen=True)
class PlanLimits:
    """The caps a worst case is computed from.

    Every field is a real parameter consumed at one seam. A limit routed through a
    keyword-argument bag it can never appear in is silently discarded — the caller sets it,
    the code reads a literal default, and nothing reports the difference. A settings object
    read in one place is the structural answer to that.
    """

    max_leaves: int = 24
    max_depth: int = 2
    max_partitions: int = 4
    target_rows_per_leaf: int = 50
    max_continuations: int = 0
    """How many times a leaf may ask again for what it did not get. Zero by default.

    Off rather than on, because every continuation is another billed call and the caller
    who would pay for it is the one who did not configure anything. Routing ``fetch``
    through a planner whose loop defaulted to three would have quadrupled the worst case of
    every request already in use, with nothing in the caller's code changed and nothing in
    the result saying why the bill moved.

    One default, here, rather than a conservative one at the facade and a generous one in
    the library. A knob whose value depends on which door the caller came through is the
    shape of every "accepted then ignored" defect this class exists to prevent.
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


def proposal_attempt_limit(limits: PlanLimits) -> int:
    """The maximum number of partition proposals in one request."""
    return 2 * limits.max_leaves - 1


def distribution_node_attempt_limit(limits: PlanLimits) -> int:
    """Candidate-bearing nodes the opt-in distribution stage may attempt.

    The planning bound charges ``max_strategy_candidates`` calls for each node in
    :func:`max_nodes`.  An attempt therefore consumes this allowance whether its
    candidates are accepted, malformed, ambiguous, or rejected by a later plan guard.
    """
    return max_nodes(limits, MODE_PARTITIONED)


def initial_v2_logical_calls(limits: PlanLimits, mode: str) -> int:
    """The initial-v2 logical-call ceiling for one request."""
    _check_mode(mode)
    per_leaf = 1 + limits.max_continuations + (1 if limits.empty_leaf_retry else 0)
    if mode == MODE_FLAT:
        return per_leaf
    if mode == MODE_ROW_BY_ROW:
        return 2 + limits.max_leaves * per_leaf
    return 1 + proposal_attempt_limit(limits) + limits.max_leaves * per_leaf


def initial_v2_provider_attempts(limits: PlanLimits, mode: str) -> int:
    """The logical ceiling multiplied by the configured transport attempts."""
    return attempts(initial_v2_logical_calls(limits, mode), limits)


def max_nodes(limits: PlanLimits, mode: str) -> int:
    """The most internal partition nodes a plan in this mode can hold.

    ``row_by_row`` has exactly one: the root, whose enumeration produces the entity set
    when the caller's constraints did not carry one. Charging that call to a root node is
    what keeps it inside this formula rather than escaping it as an unassigned extra.

    A partitioned plan records that root whatever its leaf count, so one is the floor.
    ``max_leaves - 1`` alone reads zero at ``max_leaves=1`` and publishes a ceiling under
    what the smallest legal plan builds — and a ceiling a plan can pass through is worse
    than none, because a caller sizes a budget from ``worst_case_attempts``, is admitted,
    and then needs attempts it was never allowed.
    """
    _check_mode(mode)
    if mode == MODE_FLAT:
        return 0
    if mode == MODE_ROW_BY_ROW:
        return 1
    return max(1, limits.max_leaves - 1)


def planning_calls(limits: PlanLimits, mode: str) -> int:
    """Logical model calls planning can make, at worst.

    One to analyse and one to estimate; a schema pool, a candidate set, and one
    enrichment per candidate; then per partition node, one distribution estimate per
    candidate column plus a selection and an enumeration; then one rewrite per leaf.
    """
    _check_mode(mode)
    schema = 2 + limits.max_schema_candidates
    per_node = limits.max_strategy_candidates + 2
    nodes = max_nodes(limits, mode) * per_node
    # A flat plan's single leaf asks the refined question itself, so there is nothing to
    # rewrite it into. Charging a rewrite there would inflate the bound on the one shape
    # where the caller is most likely to be watching the cost.
    rewrites = 0 if mode == MODE_FLAT else limits.max_leaves
    return 2 + schema + nodes + rewrites


def execution_calls(limits: PlanLimits, leaves: int) -> int:
    """Logical model calls execution can make, at worst, for a plan of this many leaves.

    Each leaf is one call plus its continuations. The zero-row retry is counted once per
    leaf when enabled, because the design leaves it off by default precisely so that it
    is a decision rather than a surprise: it doubles the call count on the case where the
    model has already said it has nothing.
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


def request_calls(limits: PlanLimits, mode: str, leaves: int | None = None) -> int:
    """Logical model calls a whole request in this mode makes, at worst.

    What a call ceiling has to be weighed against before anything is asked. This is the
    whole of what the shipped pipeline does — one analysis call outside flat, then the
    leaves — rather than the full-design figure :func:`planning_calls` sizes a
    recommendation against.

    ``leaves`` is the plan's real count once there is a plan. Before there is one the cap
    stands in for it, because a bound that waits for the analysis call has already let the
    request spend money it was not admitted for. Flat is the exception and is not sized
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
    return 1 + execution_calls(limits, limits.max_leaves if leaves is None else leaves)


def worst_case_attempts(limits: PlanLimits, mode: str) -> int:
    """Every attempt a whole request in this mode can make, before it starts.

    This is what a recommendation is sized against: no plan exists yet, so the leaf count
    is the cap rather than a real count.
    """
    logical = planning_calls(limits, mode) + execution_calls(limits, limits.max_leaves)
    return attempts(logical, limits)


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ConfigError(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")
