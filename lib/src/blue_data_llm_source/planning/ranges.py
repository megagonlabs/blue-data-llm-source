"""Integer spans as partition values: reading them and checking that they partition.

A model asked to split years proposes values such as ``"1946–1952"``. Held as an ``Exact``
filter, that text is compared with each record's year and never matches one, so the leaf
is asked for a slice nothing can satisfy. Read as a :class:`~blue_data_llm_source.types.Range`
it says what the model meant.

Reading is deliberately literal. A span is two unsigned integers joined by one dash, and a
column qualifies only when the schema declares it an integer. Anything looser would turn
``"1990s"`` or ``"2-3 million"`` into a range on the strength of a guess, and a wrong range
filters out records a leaf should have kept.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from blue_data_llm_source.types import Constraint, Exact, JsonScalar, Range

ORDINAL_TYPES: Final = frozenset({"int", "integer"})
"""Declared types whose values a span can bound."""

NUMERIC_TYPES: Final = frozenset({"int", "integer", "float", "number"})
"""Declared types a partition key is checked against as a possible continuous measurement."""

SMALL_ORDINAL_SPAN: Final = 1_000
"""The most integers a numeric key's proposed values may cover and still be a small ordinal set.

A thousand is room for years across a millennium, ranks, seasons and editions, and far less
than a population, a price or an area bucketed by a model. The number is a judgement, not a
measurement; it is published so a refused key can be understood."""

_DASHES: Final = "-\u2010\u2011\u2012\u2013\u2014\u2212"
"""Hyphen-minus, hyphen, non-breaking hyphen, figure dash, en dash, em dash, minus sign."""

_SPAN: Final = re.compile(rf"^\s*(\d+)\s*[{_DASHES}]\s*(\d+)\s*$")
_POINT: Final = re.compile(r"^\s*([+-]?\d+)\s*$")


def is_ordinal(declared_type: str | None) -> bool:
    return (declared_type or "").strip().casefold() in ORDINAL_TYPES


def is_numeric(declared_type: str | None) -> bool:
    return (declared_type or "").strip().casefold() in NUMERIC_TYPES


def small_ordinal(values: Sequence[JsonScalar]) -> bool:
    """Whether *values* are integers or integer spans covering at most ``SMALL_ORDINAL_SPAN``.

    What separates a numeric key worth splitting on, such as a year or a rank, from a
    continuous measurement a model has bucketed. A bucket written as text ("under 1
    million", "2.5-3.0", "high") is not an integer or a span, and a bucket of large integers
    covers far more of the number line than a year column does.
    """
    ends: list[int] = []
    for value in values:
        span = parse_span(value)
        if span is not None:
            if span[0] > span[1]:
                return False
            ends.extend(span)
            continue
        point = read_point(value)
        if point is None:
            return False
        ends.append(point)
    return bool(ends) and max(ends) - min(ends) + 1 <= SMALL_ORDINAL_SPAN


def parse_span(value: JsonScalar) -> tuple[int, int] | None:
    """The two ends of a span written as ``"1946–1952"`` or ``"1946-1952"``, else None.

    The ends are returned as written, so a reversed span is visible to the caller rather
    than silently reordered into a different slice.
    """
    if not isinstance(value, str):
        return None
    match = _SPAN.match(value)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def read_point(value: JsonScalar) -> int | None:
    """One integer, written as a number or as digits, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        match = _POINT.match(value)
        return int(match.group(1)) if match else None
    return None


def check_disjoint(intervals: Sequence[tuple[int, int]]) -> tuple[int, int] | None:
    """The first pair of positions whose inclusive intervals overlap, or None."""
    order = sorted(range(len(intervals)), key=lambda index: intervals[index])
    for before, after in zip(order, order[1:]):
        if intervals[after][0] <= intervals[before][1]:
            return before, after
    return None


def typed_children(
    values: Sequence[JsonScalar],
) -> tuple[list[Constraint] | None, str | None]:
    """One filter per proposal value when the proposal writes spans, else ``(None, None)``.

    A proposal with no span keeps today's ``Exact`` filters, so turning the switch on
    changes nothing for a split that was never written as spans. One that writes any span
    must be wholly typed: every other value an integer, which becomes a one-point ``Exact``,
    every span with its start at or below its end, and no two values sharing an integer.
    A proposal that fails any of those is refused with the reason, not repaired, because the
    repair would be a guess about which slice the model meant.
    """
    spans = [parse_span(value) for value in values]
    if not any(span is not None for span in spans):
        return None, None
    filters: list[Constraint] = []
    intervals: list[tuple[int, int]] = []
    for value, span in zip(values, spans, strict=True):
        if span is not None:
            lo, hi = span
            if lo > hi:
                return None, f"range value {value!r} starts above where it ends"
            filters.append(Range(lo, hi))
            intervals.append((lo, hi))
            continue
        point = read_point(value)
        if point is None:
            return None, (
                f"range proposal mixes spans with {value!r}, which is neither a span nor an integer"
            )
        filters.append(Exact(point))
        intervals.append((point, point))
    overlap = check_disjoint(intervals)
    if overlap is not None:
        first, second = overlap
        return None, f"range values {values[first]!r} and {values[second]!r} overlap"
    return filters, None
