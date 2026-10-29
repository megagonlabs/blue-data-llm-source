"""Coerce values to declared attribute types and report mismatches."""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass
from typing import Any, cast

from blue_data_llm_source.reduction.identity import parse_date
from blue_data_llm_source.types import Attr

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0"}

# Strip commas only from grouped thousands. A decimal comma such as "3,80" must
# remain unchanged and fail conversion as a type mismatch.
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d*)?$")


def _fits_float(value: int) -> bool:
    """Whether *value* can be represented as a float at all."""
    try:
        float(value)
    except OverflowError:
        return False
    return True


def _degrouped(text: str) -> str:
    """*text* with thousands separators removed, or unchanged if they are not that."""
    return text.replace(",", "") if _THOUSANDS.match(text) else text


def _coerce_int(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = _degrouped(value.strip())
        try:
            return int(text)
        except ValueError:
            return value
    return value


def _coerce_float(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except OverflowError:
            # An integer beyond the float range remains a value-level type
            # mismatch; it must not fail the entire fetch.
            return value
    if isinstance(value, str):
        try:
            return float(_degrouped(value.strip()))
        except (ValueError, OverflowError):
            return value
    return value


def _coerce_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return value


def _coerce_date(value: Any) -> Any:
    parsed = parse_date(value)
    return value if parsed is None else parsed


def _coerce_str(value: Any) -> Any:
    """A declared string stays a string; anything else is reported rather than stringified.

    Coercing would hide the mismatch — the caller asked for text and the model answered with
    a number, which is the thing they wanted to be told about.
    """
    return value


_COERCERS = {
    "int": _coerce_int,
    "integer": _coerce_int,
    "float": _coerce_float,
    "number": _coerce_float,
    "bool": _coerce_bool,
    "boolean": _coerce_bool,
    "date": _coerce_date,
    "str": _coerce_str,
    "string": _coerce_str,
    "text": _coerce_str,
}


@dataclass
class TypeMismatch:
    """One value that did not match its declared type."""

    record_index: int
    attribute: str
    declared_type: str
    value: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_index": self.record_index,
            "attribute": self.attribute,
            "declared_type": self.declared_type,
            "value": self.value,
        }


def unchecked_types(attrs: list[Attr]) -> list[str]:
    """Declared type names nothing here knows how to check, in the order requested.

    An unknown type checks no values. Report it once against the request, rather than as a
    mismatch for every value.
    """
    seen: list[str] = []
    for attr in attrs:
        declared = (attr.type or "").strip().lower()
        if declared and declared not in _COERCERS and declared not in seen:
            seen.append(declared)
    return seen


def verify(
    records: list[dict[str, Any]], attrs: list[Attr]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Coerce values to their declared types, and report the ones that would not.

    Returns the records and a list of mismatches. A value that will not coerce is kept
    as it came back rather than dropped, because discarding a row over a formatting
    difference loses real data. But keeping it silently would mean the caller is told
    the records are typed when one of them is not, so every failure is reported.
    """
    typed = {a.name: (a.type or "").strip().lower() for a in attrs if a.type}
    if not typed:
        return records, []

    out: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        fixed = dict(record)
        for name, declared in typed.items():
            if name not in fixed or fixed[name] is None:
                continue
            before = fixed[name]
            # Unknown declared types still produce mismatches; they cannot silently
            # pass values through as if the type had been checked.
            coercer = _COERCERS.get(declared)
            after = coercer(before) if coercer is not None else before
            if _matches(after, declared):
                fixed[name] = after
            else:
                # Keep the original value when coercion fails; `inf` is not valid JSON.
                mismatches.append(
                    TypeMismatch(
                        record_index=index,
                        attribute=name,
                        declared_type=declared,
                        value=repr(before),
                    ).as_dict()
                )
        out.append(fixed)
    return out, mismatches


def _matches(value: Any, declared: str) -> bool:
    """Whether *value* now satisfies the declared type."""
    if declared in {"int", "integer"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if declared in {"float", "number"}:
        # JSON requires finite numbers. Check integers separately: converting a wide int
        # to float can raise OverflowError.
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            # Still not a float value; it did not coerce, so it is a mismatch.
            return type(value) is int and _fits_float(value)
        return isinstance(value, float) and math.isfinite(value)
    if declared in {"bool", "boolean"}:
        return isinstance(value, bool)
    if declared in {"str", "string", "text"}:
        return isinstance(value, str)
    if declared == "date":
        # The pattern checks shape; the calendar checks whether the date exists.
        if not isinstance(value, str) or not _ISO_DATE.match(value):
            return False
        try:
            _dt.date.fromisoformat(value)
        except ValueError:
            return False
        return True
    # An unknown declared type cannot be checked, so no value can fail against it. Saying
    # so is not the same as passing silently — `verify` reports the name once, as a fact
    # about the request, rather than flagging every value the caller received. Per-value
    # reporting would obscure genuine mismatches and condemn a declaration the documentation
    # invites: the declared type is sent to the model verbatim
    # ("- price: money"), so an unlisted name is an instruction, not necessarily a typo.
    return True


def drop_empty(
    records: list[dict[str, Any]], matched: list[set[str]], attrs: list[Attr]
) -> list[dict[str, Any]]:
    """Remove records that answered none of the requested attributes.

    Alignment fills every requested attribute, so a response whose keys all failed to map
    becomes a record of nothing but ``None``. Returning it says the model answered when it
    did not, and a caller counting records gets a number with no content behind it.

    Presence comes from *matched*, not from the values. A JSON null is also ``None``, so a
    record whose only answer is an explicit null is a real answer and is kept, while a
    record with no answered attribute at all is not. Deciding this from the values would
    discard the first along with the second and there would be no way to tell afterwards.

    Only reached when attributes were requested; without them there is nothing to be
    missing, and the record passes through as the model sent it.
    """
    if not attrs:
        return list(records)
    return [record for record, answered in zip(records, matched, strict=True) if answered]


def _canonical(value: Any) -> str:
    """A marker that does not depend on the order a mapping was written in.

    Nested object key order is not semantic, so canonicalize it before deduplication.
    """
    if isinstance(value, dict):
        entries = cast("dict[str, Any]", value)
        return "{" + ",".join(f"{k!r}:{_canonical(entries[k])}" for k in sorted(entries)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical(v) for v in cast("list[Any]", value)) + "]"
    return repr(value)


def dedupe(
    records: list[dict[str, Any]],
    key: str | None = None,
    origins: list[list[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    """Remove exact duplicates, or duplicates on *key* when one is given.

    *origins* travels with the records because this pass drops where the merge above it
    appends. The merge records that two parts both produced a record by extending that
    record's origin list; dropping the second copy here without folding its origin in leaves
    the survivor naming one part, which is not a smaller claim than naming both but a
    different and false one.
    """
    seen: dict[Any, int] = {}
    out: list[dict[str, Any]] = []
    kept: list[list[str]] = []
    for position, record in enumerate(records):
        if key is not None and key in record:
            marker: Any = record.get(key)
        else:
            marker = tuple(sorted((k, _canonical(v)) for k, v in record.items()))
        source = list(origins[position]) if origins is not None and position < len(origins) else []
        at = seen.get(marker)
        if at is not None:
            kept[at].extend(name for name in source if name not in kept[at])
            continue
        seen[marker] = len(out)
        out.append(record)
        kept.append(source)
    return out, kept
