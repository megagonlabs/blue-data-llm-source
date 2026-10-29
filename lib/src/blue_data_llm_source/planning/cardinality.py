"""Represent estimated row counts as buckets for partition planning.

An unreadable estimate is ``UNKNOWN``, distinct from a count of zero. Treating it as
zero would suppress partitioning and continuations for requests of unknown size.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final, cast

from blue_data_llm_source.reduction.parsing import strip_fence
from blue_data_llm_source.types import ConfigError

PARSE_PARSED: Final = "parsed"
PARSE_UNKNOWN: Final = "unknown"
PARSE_INVALID: Final = "invalid"
PARSE_STATUSES: Final = (PARSE_PARSED, PARSE_UNKNOWN, PARSE_INVALID)


def _nonempty_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"cardinality {name} must be a non-empty string")
    return value.strip()


def _diagnostics(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ConfigError("cardinality diagnostics must contain strings")
    items = cast("tuple[Any, ...] | list[Any]", value)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise ConfigError("cardinality diagnostics must contain strings")
    return tuple(item.strip() for item in cast("tuple[str, ...] | list[str]", items))


@dataclass(frozen=True)
class CardinalityEstimate:
    """Uncertain row-count evidence bound to the unit it counts."""

    expected: int | None = None
    lower: int | None = None
    upper: int | None = None
    row_unit: str = "records"
    source: str = "unknown"
    parse_status: str = PARSE_UNKNOWN
    diagnostics: tuple[str, ...] = field(default_factory=tuple[str, ...])

    def __post_init__(self) -> None:
        for name in ("expected", "lower", "upper"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ConfigError(f"cardinality {name} must be a non-negative integer or null")
        row_unit = _nonempty_text(self.row_unit, "row_unit")
        source = _nonempty_text(self.source, "source")
        diagnostics = _diagnostics(self.diagnostics)
        object.__setattr__(self, "row_unit", row_unit)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "diagnostics", diagnostics)
        if self.parse_status not in PARSE_STATUSES:
            raise ConfigError(f"unknown cardinality parse_status {self.parse_status!r}")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ConfigError("cardinality lower must not exceed upper")
        if self.expected is not None and self.lower is not None and self.expected < self.lower:
            raise ConfigError("cardinality expected must not be below lower")
        if self.expected is not None and self.upper is not None and self.expected > self.upper:
            raise ConfigError("cardinality expected must not exceed upper")
        has_value = any(value is not None for value in (self.expected, self.lower, self.upper))
        if self.parse_status == PARSE_PARSED and (not has_value or diagnostics):
            raise ConfigError("parsed cardinality must contain values and no diagnostics")
        if self.parse_status == PARSE_UNKNOWN and (has_value or diagnostics):
            raise ConfigError("unknown cardinality must contain neither values nor diagnostics")
        if self.parse_status == PARSE_INVALID and (has_value or not diagnostics):
            raise ConfigError("invalid cardinality must contain diagnostics and no values")

    def to_dict(self) -> dict[str, object]:
        return {
            "expected": self.expected,
            "lower": self.lower,
            "upper": self.upper,
            "row_unit": self.row_unit,
            "source": self.source,
            "parse_status": self.parse_status,
            "diagnostics": list(self.diagnostics),
        }


def estimate_from_reported(
    reported: object, *, row_unit: str = "records", source: str = "model"
) -> CardinalityEstimate:
    """Parse a structured estimate without mining a number from prose."""
    if reported is None:
        return CardinalityEstimate(row_unit=row_unit, source=source)
    if isinstance(reported, bool):
        return CardinalityEstimate(
            row_unit=row_unit,
            source=source,
            parse_status=PARSE_INVALID,
            diagnostics=("estimate was a boolean",),
        )
    if isinstance(reported, int):
        if reported >= 0:
            return CardinalityEstimate(
                expected=reported,
                row_unit=row_unit,
                source=source,
                parse_status=PARSE_PARSED,
            )
        return CardinalityEstimate(
            row_unit=row_unit,
            source=source,
            parse_status=PARSE_INVALID,
            diagnostics=("estimate was negative",),
        )
    if not isinstance(reported, dict):
        return CardinalityEstimate(
            row_unit=row_unit,
            source=source,
            parse_status=PARSE_INVALID,
            diagnostics=("estimate was not a structured object",),
        )
    data = cast("dict[str, Any]", reported)
    allowed = {"expected", "lower", "upper", "row_unit"}
    if not set(data) <= allowed:
        return CardinalityEstimate(
            row_unit=row_unit,
            source=source,
            parse_status=PARSE_INVALID,
            diagnostics=("estimate contained unknown fields",),
        )
    unit = data.get("row_unit", row_unit)
    if not isinstance(unit, str) or not unit.strip():
        return CardinalityEstimate(
            row_unit=row_unit,
            source=source,
            parse_status=PARSE_INVALID,
            diagnostics=("row_unit was not a non-empty string",),
        )
    values: dict[str, int | None] = {}
    for name in ("expected", "lower", "upper"):
        value = data.get(name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            return CardinalityEstimate(
                row_unit=unit,
                source=source,
                parse_status=PARSE_INVALID,
                diagnostics=(f"{name} was not a non-negative integer or null",),
            )
        values[name] = value
    try:
        return CardinalityEstimate(
            expected=values["expected"],
            lower=values["lower"],
            upper=values["upper"],
            row_unit=unit.strip(),
            source=source,
            parse_status=(
                PARSE_PARSED
                if any(value is not None for value in values.values())
                else PARSE_UNKNOWN
            ),
        )
    except ConfigError as error:
        return CardinalityEstimate(
            row_unit=unit.strip(),
            source=source,
            parse_status=PARSE_INVALID,
            diagnostics=(str(error),),
        )


def cardinality_from_payload(
    payload: object,
    *,
    row_unit: str,
    source: str = "cardinality_stage",
) -> CardinalityEstimate:
    """Parse the cardinality stage's structured object."""
    if not isinstance(payload, dict):
        return CardinalityEstimate(
            row_unit=row_unit,
            source=source,
            parse_status=PARSE_INVALID,
            diagnostics=("cardinality response was not a JSON object",),
        )
    return estimate_from_reported(cast("dict[str, Any]", payload), row_unit=row_unit, source=source)


def cardinality_from_response(
    text: str | None,
    *,
    row_unit: str,
    source: str = "cardinality_stage",
) -> CardinalityEstimate:
    """Read one cardinality response without extracting numbers from prose."""
    payload: object = None
    if text and text.strip():
        try:
            payload = json.loads(strip_fence(text))
        except (ValueError, RecursionError):
            payload = None
    return cardinality_from_payload(payload, row_unit=row_unit, source=source)


class CardinalityEstimator:
    """Pure structured parser for row-count evidence."""

    @staticmethod
    def parse(
        text: str | None,
        *,
        row_unit: str,
        source: str = "cardinality_stage",
    ) -> CardinalityEstimate:
        return cardinality_from_response(text, row_unit=row_unit, source=source)


def estimate_from_dict(value: object) -> CardinalityEstimate:
    """Strictly read the complete estimate form carried on wire version 2."""
    if not isinstance(value, dict):
        raise ConfigError("cardinality estimate must be an object")
    data = cast("dict[str, Any]", value)
    expected_fields = {
        "expected",
        "lower",
        "upper",
        "row_unit",
        "source",
        "parse_status",
        "diagnostics",
    }
    if set(data) != expected_fields:
        raise ConfigError("cardinality estimate fields do not match wire version 2")
    diagnostics = data.get("diagnostics")
    if not isinstance(diagnostics, list) or not all(
        isinstance(item, str) for item in cast("list[Any]", diagnostics)
    ):
        raise ConfigError("cardinality diagnostics must be a list of strings")
    row_unit = data.get("row_unit")
    source = data.get("source")
    parse_status = data.get("parse_status")
    if not isinstance(row_unit, str):
        raise ConfigError("cardinality row_unit must be a string")
    if not isinstance(source, str):
        raise ConfigError("cardinality source must be a string")
    if not isinstance(parse_status, str):
        raise ConfigError("cardinality parse_status must be a string")
    return CardinalityEstimate(
        expected=data.get("expected"),
        lower=data.get("lower"),
        upper=data.get("upper"),
        row_unit=row_unit,
        source=source,
        parse_status=parse_status,
        diagnostics=tuple(cast("list[str]", diagnostics)),
    )


# Upper bounds, inclusive. Chosen to be coarse: the estimate drives depth and quota, and
# a boundary that pretends to more resolution than an uncalibrated estimator has would be
# decoration. Exposed as a mapping rather than buried in a comparison chain so a test can
# assert against the same numbers the code uses.
_TENS_MAX: Final = 50
_HUNDREDS_MAX: Final = 500
_THOUSANDS_MAX: Final = 5_000


class Cardinality:
    """A bucketed row-count estimate.

    ``UNKNOWN`` means no usable figure came back — no parsable number, or the model
    declining to give one. A decline is an absence of information, not an assertion that
    the answer is enormous, so it does not belong in ``UNBOUNDED``, which claims the
    opposite. Putting a decline in both would let one response select two buckets.
    """

    TENS = "tens"
    HUNDREDS = "hundreds"
    THOUSANDS = "thousands"
    UNBOUNDED = "unbounded"
    UNKNOWN = "unknown"

    ALL: Final = (TENS, HUNDREDS, THOUSANDS, UNBOUNDED, UNKNOWN)


def bucket_of(count: int) -> str:
    """The bucket a known, non-negative count falls in."""
    if count < 0:
        raise ValueError(f"a row count cannot be negative, got {count!r}")
    if count <= _TENS_MAX:
        return Cardinality.TENS
    if count <= _HUNDREDS_MAX:
        return Cardinality.HUNDREDS
    if count <= _THOUSANDS_MAX:
        return Cardinality.THOUSANDS
    return Cardinality.UNBOUNDED


def from_reported(reported: object) -> str:
    """The bucket for whatever the estimation call actually returned.

    Deliberately total: every input has an answer and none of them raises, because the
    caller is a planning step that must proceed either way. Anything this cannot read is
    ``UNKNOWN``, which is a real answer rather than a failure — the point being that it
    is *distinguishable* from a small count.

    A bool is rejected before the int branch. ``isinstance(True, int)`` is true in Python,
    so a provider returning ``true`` for "can you estimate this" would otherwise be read
    as an estimate of one row, which is both wrong and in the cheap direction.
    """
    if isinstance(reported, bool):
        return Cardinality.UNKNOWN
    if isinstance(reported, int):
        return bucket_of(reported) if reported >= 0 else Cardinality.UNKNOWN
    if isinstance(reported, float):
        # A finite non-negative float is a count written loosely; NaN and the infinities
        # are not counts and must not survive int() as an arbitrary value.
        if reported != reported or reported in (float("inf"), float("-inf")):
            return Cardinality.UNKNOWN
        return bucket_of(int(reported)) if reported >= 0 else Cardinality.UNKNOWN
    if isinstance(reported, str):
        text = reported.strip().replace(",", "")
        # Accept only a bare number; a number embedded in prose is ambiguous.
        if text.isdigit():
            return bucket_of(int(text))
        return Cardinality.UNKNOWN
    return Cardinality.UNKNOWN


def upper_bound(estimate: str) -> int | None:
    """The most rows this bucket allows, or None when the bucket names no ceiling.

    Used by the partition-explosion guard, which compares how many partition values were
    enumerated against how many rows the answer is thought to hold. More partitions than
    rows means most leaves can only come back empty, and the fan-out costs a call each.
    """
    if estimate not in Cardinality.ALL:
        raise ValueError(f"not a cardinality: {estimate!r}")
    sized = for_sizing(estimate)
    return {
        Cardinality.TENS: _TENS_MAX,
        Cardinality.HUNDREDS: _HUNDREDS_MAX,
        Cardinality.THOUSANDS: _THOUSANDS_MAX,
    }.get(sized)


def for_sizing(estimate: str) -> str:
    """The bucket used to size depth and quota, given what the estimate said.

    ``UNKNOWN`` is sized as ``HUNDREDS`` so an absent estimate does not reduce the
    planned row count. The committed budget still bounds the request.
    """
    if estimate not in Cardinality.ALL:
        raise ValueError(f"not a cardinality: {estimate!r}")
    return Cardinality.HUNDREDS if estimate == Cardinality.UNKNOWN else estimate
