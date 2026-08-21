"""Mechanical format checks against the declared attribute types.

This is the half of data/format verification that has an oracle. Whether a claim is
true is a different question and this package does not answer it.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass
from typing import Any, cast

from blue_data_llm_source.types import Attr

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0"}

# A comma is a thousands separator only in this shape: groups of exactly three digits after
# the first, optionally signed, optionally with a dot decimal part. Stripping every comma
# unconditionally turned the decimal comma most of the world writes — "3,80" — into 380.0
# and reported outcome=ok with an empty mismatch list, so the caller received a number three
# orders of magnitude wrong, was told it was clean, and could not reconcile it against
# anything because the original string is not preserved in the result. Anything that is not
# this shape keeps its commas, fails the conversion, and is reported as a type mismatch.
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
            # A JSON integer wider than the float range. Returned unconverted so it is
            # reported as a type mismatch, which is what the contract says happens to a
            # value that will not coerce — it used to raise OverflowError out of the whole
            # fetch, losing a call the caller had already paid for.
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
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%B %d, %Y"):
            try:
                return _dt.datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
    return value


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

    A misspelled type checks nothing while the caller believes their records are typed,
    which is the risk that made an unknown type a per-value mismatch. Reported once, against
    the request rather than against the data, it stays loud without accusing every value.
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
            # A declared type this table does not know used to pass everything. That let a
            # declared `string` hold 123 and a misspelled type check nothing at all, while
            # the caller was told the records were typed.
            coercer = _COERCERS.get(declared)
            after = coercer(before) if coercer is not None else before
            if _matches(after, declared):
                fixed[name] = after
            else:
                # The value stays as it arrived. `float("1e309")` succeeds as `inf`, which
                # is then correctly reported as a mismatch — but writing it into the record
                # first put back exactly what the JSON parser refuses to read, and strict
                # consumers cannot represent it. The data model says a value that will not
                # coerce is returned as it came.
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
        # Finite: the JSON parser refuses non-finite literals, and a coercion that
        # reintroduced one put back exactly what it had refused. `math.isfinite` is asked
        # only of floats, because an int wider than the float range raises OverflowError
        # inside the conversion it does first — the check meant to keep a bad value out was
        # itself the thing that escaped the error hierarchy. An int is always finite.
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
        # The pattern says it is shaped like a date; only the calendar says it is one.
        # `2026-02-30` matched the pattern and was reported as valid.
        if not isinstance(value, str) or not _ISO_DATE.match(value):
            return False
        try:
            _dt.date.fromisoformat(value)
        except ValueError:
            return False
        return True
    # An unknown declared type cannot be checked, so no value can fail against it. Saying
    # so is not the same as passing silently — `verify` reports the name once, as a fact
    # about the request, rather than flagging every value the caller received. Reporting it
    # per value measured 21 mismatches on a batch holding one genuinely bad number, which
    # destroys the signal the decision log tells callers to monitor, and it condemned a
    # declaration the documentation invites: the declared type is sent to the model verbatim
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

    ``repr`` of a nested object carries its key order, and JSON object order is not
    semantic — two records equal in Python survived deduplication because the model emitted
    the same nested object's keys in a different order.
    """
    if isinstance(value, dict):
        entries = cast("dict[str, Any]", value)
        return "{" + ",".join(f"{k!r}:{_canonical(entries[k])}" for k in sorted(entries)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical(v) for v in cast("list[Any]", value)) + "]"
    return repr(value)


def dedupe(records: list[dict[str, Any]], key: str | None = None) -> list[dict[str, Any]]:
    """Remove exact duplicates, or duplicates on *key* when one is given."""
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for record in records:
        if key is not None and key in record:
            marker: Any = record.get(key)
        else:
            marker = tuple(sorted((k, _canonical(v)) for k, v in record.items()))
        if marker in seen:
            continue
        seen.add(marker)
        out.append(record)
    return out
