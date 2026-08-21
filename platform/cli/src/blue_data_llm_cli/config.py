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

_EXIT_CODES: tuple[tuple[type[LlmSourceError], int], ...] = (
    (QueryError, EXIT_QUERY),
    (ConfigError, EXIT_CONFIG),
    (TransportError, EXIT_TRANSPORT),
    (ParseError, EXIT_PARSE),
    (BudgetExceeded, EXIT_BUDGET),
)


def exit_code_for(error: LlmSourceError) -> int:
    """Return the documented exit code for *error*."""
    for kind, code in _EXIT_CODES:
        if isinstance(error, kind):
            return code
    return EXIT_ERROR


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
    model: str | None, limits: Limits, max_tokens: int | None = None
) -> dict[str, Any]:
    """Build the per-call properties dict from the model and limit options."""
    properties = limit_properties(limits)
    if model:
        properties["llm.model"] = model
    if max_tokens is not None:
        properties["llm.max_tokens"] = max_tokens
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
    }
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
    if verbose:
        if meta.unmapped_keys:
            summary["unmapped_keys"] = list(meta.unmapped_keys)
        if meta.remapped_keys:
            summary["remapped_keys"] = dict(meta.remapped_keys)
        if meta.stopped_by:
            summary["stopped_by"] = meta.stopped_by
        if meta.type_mismatches:
            summary["type_mismatches"] = list(meta.type_mismatches)
        if meta.key_collisions:
            summary["key_collisions"] = list(meta.key_collisions)
    return summary
