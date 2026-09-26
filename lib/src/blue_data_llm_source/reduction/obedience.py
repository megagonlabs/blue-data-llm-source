"""Whether a record contradicts the filter its leaf was asked under.

A child leaf is asked for one slice, and a model answering it can return rows from a
neighbouring slice. Those rows are not necessarily wrong records, but they are not the ones
that leaf was asked for, and a run that keeps many of them has leaves that ignored their
filters. This module counts them and, when the caller asks, removes them at the leaf.

The comparison rule is deliberately narrow, because a false contradiction is worse than a
missed one: under ``plan.filter_enforcement="drop"`` it deletes a good row. A filter is
checked against a record only when all of these hold:

- the filter names a declared attribute whose type the key normalizer recognises
  (``int``/``integer``, ``float``/``number``, ``bool``/``boolean``, ``date``,
  ``str``/``string``/``text``);
- the record carries a value for that attribute, read through the same alignment identity
  uses, that is a non-blank scalar;
- the declared type can read both the record's value and the filter's value, through
  :func:`~blue_data_llm_source.reduction.identity.typed_identity`, the rule that already
  decides whether two keys are the same key.

Then an ``Exact`` filter is contradicted when the two typed values differ, a ``OneOf``
filter when the record's typed value is none of the alternatives the type can read, and a
``Range`` filter when the record's value reads as a number outside the span, both ends
included (a value that does not read as a number is unchecked). Strings
therefore compare after Unicode compatibility folding, case folding and whitespace
collapsing, and numbers compare by value, so ``"south"`` obeys ``"South"`` and ``"1950"``
obeys ``1950``. Anything outside the rule is unchecked rather than contradicted. A record is
counted once however many of its leaf's filters it contradicts.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from typing import Any, cast

from blue_data_llm_source.reduction.aligner import align
from blue_data_llm_source.reduction.identity import typed_identity
from blue_data_llm_source.types import Attr, Constraint, OneOf, Range, resolve_attr

FILTER_FLAG = "flag"
FILTER_DROP = "drop"
FILTER_ENFORCEMENTS = (FILTER_FLAG, FILTER_DROP)
"""What happens to a contradicting record: kept and counted, or removed at its leaf."""


def _record_value(value: object) -> bool:
    if value is None or isinstance(value, (list, dict)):
        return False
    return not (isinstance(value, str) and not value.strip())


def _contradicts(value: object, constraint: Constraint, declared: str | None) -> bool:
    observed = typed_identity(value, declared)
    if observed is None:
        return False
    if isinstance(constraint, Range):
        # Typed identities are `(declared type, value)` pairs; only a number can be in a span.
        read = cast("tuple[str, object]", observed)[1]
        if isinstance(read, bool) or not isinstance(read, (int, float)):
            return False
        # A non-integer in an integer column is outside every integer span.
        return not constraint.contains(read)
    alternatives = constraint.values if isinstance(constraint, OneOf) else (constraint.value,)
    allowed: set[Hashable] = set()
    for alternative in alternatives:
        typed = typed_identity(alternative, declared)
        if typed is not None:
            allowed.add(typed)
    # A filter the declared type cannot read says nothing checkable about this column.
    return bool(allowed) and observed not in allowed


def contradictions(
    records: Sequence[Mapping[str, Any]],
    constraints: Mapping[str, Constraint],
    attrs: Sequence[Attr],
) -> list[bool]:
    """One flag per record: whether it contradicts a checkable filter of its leaf.

    *constraints* are the leaf's column filters only, which the caller selects with
    :func:`~blue_data_llm_source.prompts.split_filters`; request scope is never checked.
    """
    checkable: dict[str, tuple[Constraint, str]] = {}
    for name, constraint in constraints.items():
        attr = resolve_attr(name, attrs)
        if attr is not None and attr.type and attr.name not in checkable:
            checkable[attr.name] = (constraint, attr.type)
    if not checkable or not records:
        return [False] * len(records)
    aligned = align([dict(record) for record in records], list(attrs)).records
    flags: list[bool] = []
    for record in aligned:
        flags.append(
            any(
                _record_value(record.get(name))
                and _contradicts(record.get(name), constraint, declared)
                for name, (constraint, declared) in checkable.items()
            )
        )
    return flags
