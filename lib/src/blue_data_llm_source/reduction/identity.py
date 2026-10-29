"""One declared-type-aware identity rule for execution and reduction."""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import unicodedata
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from blue_data_llm_source.reduction.aligner import align
from blue_data_llm_source.types import Attr, ConfigError, value_identity

_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d*)?$")
_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0"}


@dataclass(frozen=True)
class KeyTuple:
    """A complete ordered primary key with display and comparison forms."""

    names: tuple[str, ...]
    normalized: tuple[Hashable, ...]
    display: tuple[Any, ...] = field(compare=False, hash=False)

    def exclusion(self) -> dict[str, Any]:
        """Return the complete named key object shown in a continuation prompt."""
        return dict(zip(self.names, self.display, strict=True))


def _text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if _THOUSANDS.match(text):
            text = text.replace(",", "")
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if _THOUSANDS.match(text):
            text = text.replace(",", "")
        try:
            parsed = float(text)
        except (ValueError, OverflowError):
            return None
        if not math.isfinite(parsed):
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _boolean(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = _text(value)
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return None


def parse_date(value: object) -> str | None:
    """Return an ISO date only when the supported format is unambiguous."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    formats = ["%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y"]
    # Year-last numeric dates need a day above 12, or a day equal to the month, so that a
    # month-first reading cannot name a different date.
    day_first = re.fullmatch(r"([0-9]{1,2})-([0-9]{1,2})-([0-9]{4})", text)
    if day_first is not None and (int(day_first[1]) > 12 or int(day_first[1]) == int(day_first[2])):
        formats.append("%d-%m-%Y")
    for fmt in formats:
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def typed_identity(value: object, declared_type: str | None) -> Hashable | None:
    """*value* read under its declared type, or None when the type or the value cannot be.

    None covers two cases a caller comparing values has to treat alike: a column with no
    declared type, or one this module does not recognize, and a value the declared type
    cannot read, such as ``"unknown"`` in an integer column. Neither is evidence about what
    the value means, so neither may be compared as if it were.
    """
    declared = (declared_type or "").strip().casefold()
    normalized: object | None
    if declared in {"int", "integer"}:
        normalized = _integer(value)
    elif declared in {"float", "number"}:
        normalized = _number(value)
    elif declared in {"bool", "boolean"}:
        normalized = _boolean(value)
    elif declared == "date":
        normalized = parse_date(value)
    elif declared in {"str", "string", "text"}:
        normalized = _text(value) if isinstance(value, str) else None
    else:
        normalized = None
    if normalized is None:
        return None
    return (declared, normalized)


def normalized_value(value: object, declared_type: str | None) -> Hashable:
    """Normalize one key component according to its declared output type."""
    typed = typed_identity(value, declared_type)
    return value_identity(value) if typed is None else typed


def _key_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("primary_keys must contain non-empty strings")
    return value.strip()


def clean_key_names(primary_keys: Sequence[object]) -> tuple[str, ...]:
    """Validate and strip an ordered key declaration."""
    raw: object = primary_keys
    if isinstance(raw, (str, bytes)):
        raise ConfigError("primary_keys must be a sequence of names")
    names: list[str] = []
    for value in raw:
        names.append(_key_name(value))
    if len(set(names)) != len(names):
        raise ConfigError("primary_keys must be distinct")
    return tuple(names)


def _incomplete(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def key_identity(
    record: Mapping[str, Any], primary_keys: Sequence[str], attrs: Sequence[Attr]
) -> KeyTuple | None:
    """Return a complete ordered identity, or ``None`` when any component is absent."""
    names = clean_key_names(primary_keys)
    if not names:
        return None
    # Identity consumes the same canonical field names and first-answer collision rule as
    # final shaping. Looking up an exact spelling first made identity depend on a value the
    # aligner later ignored when an alias appeared earlier in the object.
    aligned = align([dict(record)], list(attrs)).records[0]
    declared = {attr.name: attr.type for attr in attrs}
    display: list[Any] = []
    normalized: list[Hashable] = []
    for name in names:
        value = aligned.get(name)
        if _incomplete(value):
            return None
        display.append(value)
        normalized.append(normalized_value(value, declared.get(name)))
    return KeyTuple(names=names, normalized=tuple(normalized), display=tuple(display))


def record_identity(record: Mapping[str, Any]) -> Hashable:
    """Exact JSON-record identity, independent of object key order."""
    return (
        "record",
        json.dumps(record, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":")),
    )
