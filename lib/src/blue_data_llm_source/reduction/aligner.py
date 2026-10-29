"""Align returned keys to the requested attributes, and report what did not fit."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from blue_data_llm_source.types import Attr, QueryError


def _unambiguous(
    index: dict[str, str], wanted: list[str], form: Callable[[str], str]
) -> dict[str, str]:
    """Drop entries of *index* that more than one requested name maps onto.

    A fuzzy index exists to rescue a key that does not match exactly. Where two requested
    names share a fuzzy form it cannot do that, so the entry is removed and the exact index
    answers instead — or the key is reported unmapped.
    """
    counts: dict[str, int] = {}
    for name in wanted:
        counts[form(name)] = counts.get(form(name), 0) + 1
    return {key: name for key, name in index.items() if counts.get(key, 0) == 1}


def normalized_key(key: str) -> str:
    """The fuzzy comparison form of *key*. Shared with parsing, which needs the same rule.

    Parsing decides whether an object is a wrapper or a record by asking which reading
    answers more of the requested attributes, and that question is only meaningful if both
    sides are compared the way alignment will compare them.
    """
    return _normalize(key)


def _normalize(key: str) -> str:
    """A key with separators, case and punctuation removed, for fuzzy matching.

    Unicode-aware so non-Latin names remain distinct. ``casefold`` supports case-insensitive
    comparison.
    """
    # NFKC makes precomposed and decomposed spellings comparable before stripping marks.
    return re.sub(
        r"[^\w]+", "", unicodedata.normalize("NFKC", key).casefold(), flags=re.UNICODE
    ).replace("_", "")


@dataclass
class Alignment:
    """Records after alignment, plus what the alignment could not place."""

    records: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    observed_keys: list[str] = field(default_factory=list[str])
    remapped: dict[str, str] = field(default_factory=dict[str, str])
    unmapped: list[str] = field(default_factory=list[str])
    # Returned keys that matched an attribute another key had already answered. Reported
    # rather than resolved: which of two spellings the model meant is not knowable here,
    # and letting the later one win makes the answer depend on JSON key order.
    collisions: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    # Which requested attributes each record actually answered, positionally. Alignment
    # fills a missing attribute with None, and a JSON null is also None, so the value
    # alone cannot tell "not answered" from "answered null". Presence is recorded here
    # instead of being inferred later from a value that cannot carry it.
    matched: list[set[str]] = field(default_factory=list[set[str]])


def align(records: list[dict[str, Any]], attrs: list[Attr]) -> Alignment:
    """Map each record's keys onto the requested attribute names.

    With no requested attributes the records pass through unchanged, since there is
    nothing to align to. Keys that cannot be matched are reported.
    """
    observed: list[str] = []
    for record in records:
        for key in record:
            if key not in observed:
                observed.append(key)

    if not attrs:
        return Alignment(
            records=list(records),
            observed_keys=observed,
            matched=[set(record) for record in records],
        )

    wanted = [a.name for a in attrs]
    if len(set(wanted)) != len(wanted):
        seen: set[str] = set()
        repeated = next(name for name in wanted if name in seen or seen.add(name))  # type: ignore[func-returns-value]
        raise QueryError(f"requested attribute {repeated!r} appears twice")

    exact = {name: name for name in wanted}
    lowered = _unambiguous({name.lower(): name for name in wanted}, wanted, str.lower)
    # Ignore colliding fuzzy forms; exact matches still work for those names.
    normalized = _unambiguous({_normalize(name): name for name in wanted}, wanted, _normalize)

    remapped: dict[str, str] = {}
    unmapped: list[str] = []
    collisions: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    matched: list[set[str]] = []

    for index, record in enumerate(records):
        aligned: dict[str, Any] = {name: None for name in wanted}
        answered: set[str] = set()
        first_key: dict[str, str] = {}
        for key, value in record.items():
            target = exact.get(key) or lowered.get(key.lower()) or normalized.get(_normalize(key))
            if target is None:
                if key not in unmapped:
                    unmapped.append(key)
                continue
            if target != key and key not in remapped:
                remapped[key] = target
            if target in answered:
                # JSON key order determines which value survives a collision.
                collisions.append(
                    {
                        "record_index": index,
                        "attribute": target,
                        "kept_key": first_key[target],
                        "ignored_key": key,
                        "ignored_value": value,
                    }
                )
                continue
            first_key[target] = key
            aligned[target] = value
            answered.add(target)
        out.append(aligned)
        matched.append(answered)

    return Alignment(
        records=out,
        observed_keys=observed,
        remapped=remapped,
        unmapped=unmapped,
        matched=matched,
        collisions=collisions,
    )
