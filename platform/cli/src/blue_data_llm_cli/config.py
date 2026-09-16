"""Option translation: command line strings in, library arguments out.

Every mapping between what the user typed and what the library accepts lives here, so
``commands.py`` is argument declaration and printing and nothing else.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from blue_data_llm_source import (
    Attr,
    BudgetExceeded,
    ConfigError,
    ExecMeta,
    Limits,
    LlmSource,
    LlmSourceError,
    ParseError,
    QueryError,
    TransportError,
)

# Exit codes. 2 belongs to Click, which uses it for usage errors, so the library errors
# start at 3. A caller can branch on the cause without parsing the message.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_QUERY = 3
EXIT_CONFIG = 4
EXIT_TRANSPORT = 5
EXIT_PARSE = 6
EXIT_BUDGET = 7
EXIT_PARTIAL = 8
"""Extraction returned an answered result after some work was interrupted.

Its own code rather than 7, which names a budget ceiling, or 0, which would make an
interrupted result look complete. A split leaf can fail beside an answered sibling, and a
flat leaf can retain records parsed before a late continuation failure."""

_EXIT_CODES: tuple[tuple[type[LlmSourceError], int], ...] = (
    (QueryError, EXIT_QUERY),
    (ConfigError, EXIT_CONFIG),
    (TransportError, EXIT_TRANSPORT),
    (ParseError, EXIT_PARSE),
    (BudgetExceeded, EXIT_BUDGET),
)

_EFFORT_CONTINUATIONS = {"low": 0, "medium": 1, "high": 2}


def exit_code_for(error: LlmSourceError) -> int:
    """Return the documented exit code for *error*."""
    for kind, code in _EXIT_CODES:
        if isinstance(error, kind):
            return code
    return EXIT_ERROR


def continuations_for_effort(effort: str) -> int:
    """Return the continuation allowance for a CLI effort level."""
    try:
        return _EFFORT_CONTINUATIONS[effort]
    except KeyError as error:
        choices = ", ".join(_EFFORT_CONTINUATIONS)
        raise ValueError(f"unknown effort {effort!r}; expected one of {choices}") from error


def parse_attr(value: str) -> Attr:
    """Parse one ``--attr`` value, in either ``NAME`` or ``NAME:TYPE`` form."""
    name, _, declared = value.partition(":")
    name = name.strip()
    declared = declared.strip()
    if not name:
        raise ValueError(f"{value!r} has no attribute name; use NAME or NAME:TYPE")
    return Attr(name=name, type=declared or None)


def build_attrs(values: Sequence[str]) -> list[Attr]:
    """Parse every ``--attr`` value in the order they were given."""
    return [parse_attr(value) for value in values]


def build_limits(
    limit_cost: float | None = None,
    limit_seconds: float | None = None,
    limit_calls: int | None = None,
) -> Limits:
    """Collect the ``--limit-*`` options into the library's Limits."""
    return Limits(max_cost=limit_cost, max_seconds=limit_seconds, max_calls=limit_calls)


def limit_properties(limits: Limits) -> dict[str, Any]:
    """Render Limits as the ``limits.*`` property keys the source reads."""
    out: dict[str, Any] = {}
    if limits.max_rows is not None:
        out["limits.max_rows"] = limits.max_rows
    if limits.max_seconds is not None:
        out["limits.max_seconds"] = limits.max_seconds
    if limits.max_cost is not None:
        out["limits.max_cost"] = limits.max_cost
    if limits.max_calls is not None:
        out["limits.max_calls"] = limits.max_calls
    return out


def build_properties(
    model: str | None,
    limits: Limits,
    max_tokens: int | None = None,
    plan_mode: str | None = None,
    plan_continuations: int | None = None,
    plan_key: str | None = None,
    plan_leaves: int | None = None,
    plan_partition_key: str | None = None,
    plan_depth: int | None = None,
    plan_partitions: int | None = None,
    plan_target_rows: int | None = None,
    plan_schema_mode: str | None = None,
    plan_keys: Sequence[str] = (),
    plan_exact_rows: int | None = None,
    plan_exclusion_bytes: int | None = None,
    plan_planning_order: str | None = None,
    plan_expose_estimate: bool | None = None,
) -> dict[str, Any]:
    """Build the per-call properties dict from the model, limit and plan options.

    The plan keys are passed through under the names the library reads rather than being
    translated, so a value refused here is refused with the library's own message naming the
    setting the caller wrote."""
    properties = limit_properties(limits)
    if model:
        properties["llm.model"] = model
    if max_tokens is not None:
        properties["llm.max_tokens"] = max_tokens
    if plan_mode is not None:
        properties["plan.mode"] = plan_mode
    if plan_continuations is not None:
        properties["plan.max_continuations"] = plan_continuations
    if plan_key is not None:
        properties["plan.key"] = plan_key
    if plan_keys:
        properties["plan.keys"] = list(plan_keys)
    if plan_exact_rows is not None:
        properties["plan.exact_rows"] = plan_exact_rows
    if plan_exclusion_bytes is not None:
        properties["plan.max_exclusion_bytes"] = plan_exclusion_bytes
    if plan_leaves is not None:
        properties["plan.max_leaves"] = plan_leaves
    if plan_partition_key is not None:
        properties["plan.partition_key"] = plan_partition_key
    if plan_depth is not None:
        properties["plan.max_depth"] = plan_depth
    if plan_partitions is not None:
        properties["plan.max_partitions"] = plan_partitions
    if plan_target_rows is not None:
        properties["plan.target_rows_per_leaf"] = plan_target_rows
    if plan_schema_mode is not None:
        properties["plan.schema_mode"] = plan_schema_mode
    if plan_planning_order is not None:
        properties["plan.planning_order"] = plan_planning_order
    if plan_expose_estimate is not None:
        properties["plan.expose_estimate"] = plan_expose_estimate
    return properties


def build_source() -> LlmSource:
    """Construct the source a command runs against.

    Tests replace this to inject a client, which is the only seam they need.
    """
    return LlmSource()


def meta_summary(meta: ExecMeta, verbose: bool = False) -> dict[str, Any]:
    """Shape ExecMeta for machine-readable output."""
    summary: dict[str, Any] = {
        "outcome": meta.outcome,
        "model": meta.model,
        "calls": meta.calls,
        "prompt_tokens": meta.prompt_tokens,
        "completion_tokens": meta.completion_tokens,
        "cost": round(meta.cost, 6),
        "elapsed_seconds": round(meta.latency_seconds, 3),
        "row_estimate": meta.row_estimate,
        "cardinality_estimate": meta.cardinality_estimate,
        "primary_keys": list(meta.primary_keys),
        "observed_key_count": meta.observed_key_count,
        "marginal_key_yields": list(meta.marginal_key_yields),
        "incomplete_key_records": meta.incomplete_key_records,
    }
    if meta.normalization_collisions:
        summary["normalization_collisions"] = list(meta.normalization_collisions)
    if not meta.accounting_known:
        # Only when false, so an ordinary result is unchanged. The calls and cost above are
        # this library's bookkeeping, not provider work, and a reader must not take the zero
        # for a measurement.
        summary["accounting_known"] = False
    # Not gated on --verbose: an uncheckable declared type is a fact about the request rather
    # than about the answer, and the whole reason the library reports it once is so a
    # misspelled type cannot check nothing in silence. Both adapters discarded it, which put
    # that silence back one layer further out.
    if meta.unchecked_types:
        summary["unchecked_types"] = list(meta.unchecked_types)
    # Ungated for the same reason, and unconditional besides. A gap says rows may be missing
    # from this answer, which a caller cannot infer from the rows that arrived — a short
    # answer and a complete one are the same list. Omitting the key when the list is empty
    # makes a program branch on whether the field is there rather than on what it says, and
    # the interface specification promises it is always present. The empty list is a real
    # answer: nothing was observed to be missing, which is not the same as complete.
    summary["gaps"] = list(meta.gaps)
    summary["stop_reasons"] = list(meta.stop_reasons)
    summary["requested_attrs"] = list(meta.requested_attrs)
    if meta.record_origins:
        summary["record_origins"] = [list(entry) for entry in meta.record_origins]
    # Only when a model changed the question. A caller reading a partial or short answer
    # needs to know which question it is short of, and that is not always the one they typed.
    if meta.asked is not None:
        summary["asked"] = meta.asked
    if meta.stopped_by:
        summary["stopped_by"] = meta.stopped_by
    if verbose:
        if meta.unmapped_keys:
            summary["unmapped_keys"] = list(meta.unmapped_keys)
        if meta.remapped_keys:
            summary["remapped_keys"] = dict(meta.remapped_keys)
        if meta.type_mismatches:
            summary["type_mismatches"] = list(meta.type_mismatches)
        if meta.key_collisions:
            summary["key_collisions"] = list(meta.key_collisions)
    return summary
