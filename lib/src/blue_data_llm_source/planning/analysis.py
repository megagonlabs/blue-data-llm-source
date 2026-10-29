"""What is actually being asked, and roughly how much of it there is.

Parsing an untrusted response is a pure function over text. If a rewrite is unusable,
the caller's ask is retained and ``rewritten=False`` is reported.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, cast

from blue_data_llm_source.planning.cardinality import (
    Cardinality,
    CardinalityEstimate,
    estimate_from_reported,
    from_reported,
)
from blue_data_llm_source.planning.schema import SchemaDefinition
from blue_data_llm_source.reduction.parsing import strip_fence
from blue_data_llm_source.types import (
    ConfigError,
    Constraint,
    Exact,
    JsonScalar,
    OneOf,
    Range,
    json_scalar,
    value_identity,
)

ConstraintValue = JsonScalar | list[JsonScalar]


@dataclass(frozen=True)
class Analysis:
    """What step 1 established. Internal to planning."""

    ask: str
    """The refined ask, or the caller's own when ``rewritten`` is False."""

    constraints: dict[str, ConstraintValue] = field(default_factory=dict[str, ConstraintValue])
    """Filters the request implies. A list value is what ``row_by_row`` reads as its
    entity set, which is why the value type is not plain ``str``."""

    rewritten: bool = False
    """Whether ``ask`` came from the model. False means it is the caller's own text."""

    estimate: str = Cardinality.UNKNOWN
    """A :class:`Cardinality` member. ``UNKNOWN`` when no usable figure came back."""


@dataclass(frozen=True)
class BootstrapAnalysis:
    """The query fields accepted from the combined bootstrap call."""

    ask: str
    constraints: dict[str, Constraint] = field(default_factory=dict[str, Constraint])
    row_estimate: int | None = None
    cardinality: CardinalityEstimate = field(default_factory=CardinalityEstimate)


def _freeze_constraints(
    constraints: Mapping[str, Constraint],
) -> Mapping[str, Constraint]:
    return MappingProxyType(dict(constraints))


def _constraint_dict(value: Constraint) -> dict[str, object]:
    if isinstance(value, OneOf):
        return {"kind": "one_of", "values": list(value.values)}
    if isinstance(value, Range):
        return {"kind": "range", "lo": value.lo, "hi": value.hi}
    return {"kind": "exact", "value": value.value}


def _within(value: JsonScalar, span: Range) -> bool:
    return span.contains(value)


def _intersect_range(span: Range, other: Constraint) -> Constraint:
    if isinstance(other, Range):
        lo, hi = max(span.lo, other.lo), min(span.hi, other.hi)
        return Range(lo, hi) if lo <= hi else OneOf(())
    if isinstance(other, Exact):
        return other if _within(other.value, span) else OneOf(())
    return OneOf(tuple(value for value in other.values if _within(value, span)))


def intersect_constraints(left: Constraint, right: Constraint) -> Constraint:
    """Intersect constraints using the typed identities used by execution."""
    # A range holds numbers; a value written as text is outside it rather than read as one,
    # the same identity rule the alternatives below use.
    if isinstance(left, Range):
        return _intersect_range(left, right)
    if isinstance(right, Range):
        return _intersect_range(right, left)
    if isinstance(left, Exact):
        if isinstance(right, Exact):
            return left if value_identity(left.value) == value_identity(right.value) else OneOf(())
        allowed = {value_identity(value) for value in right.values}
        return left if value_identity(left.value) in allowed else OneOf(())
    if isinstance(right, Exact):
        allowed = {value_identity(value) for value in left.values}
        return right if value_identity(right.value) in allowed else OneOf(())
    allowed = {value_identity(value) for value in right.values}
    seen: set[object] = set()
    values: list[JsonScalar] = []
    for value in left.values:
        identity = value_identity(value)
        if identity in allowed and identity not in seen:
            seen.add(identity)
            values.append(value)
    return OneOf(tuple(values))


@dataclass(frozen=True)
class DataTask:
    """Semantic state for a root, internal, or terminal retrieval task."""

    id: str
    parent_id: str | None
    original_query: str
    query: str
    constraints: Mapping[str, Constraint] = field(default_factory=dict[str, Constraint])
    schema: SchemaDefinition | None = None
    cardinality: CardinalityEstimate = field(default_factory=CardinalityEstimate)
    depth: int = 0
    partition_key: str | None = None
    partition_value: JsonScalar | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ConfigError("data task id must be non-empty")
        if not self.original_query.strip() or not self.query.strip():
            raise ConfigError("data task queries must be non-empty")
        if self.depth < 0:
            raise ConfigError("data task depth must be non-negative")
        if self.partition_value is not None:
            object.__setattr__(
                self,
                "partition_value",
                json_scalar(self.partition_value, "data task partition_value"),
            )
        object.__setattr__(self, "constraints", _freeze_constraints(self.constraints))

    def to_dict(self) -> dict[str, object]:
        rendered_schema = self.schema.to_dict() if self.schema is not None else None
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "original_query": self.original_query,
            "query": self.query,
            "constraints": {
                name: _constraint_dict(value) for name, value in self.constraints.items()
            },
            "schema": rendered_schema,
            "cardinality": self.cardinality.to_dict(),
            "depth": self.depth,
            "partition_key": self.partition_key,
            "partition_value": self.partition_value,
        }


@dataclass(frozen=True)
class QueryAnalysisResult:
    """Accepted query analysis and the root semantic task it creates."""

    ask: str
    constraints: Mapping[str, Constraint] = field(default_factory=dict[str, Constraint])
    rewritten: bool = False
    row_estimate: int | None = None
    cardinality: CardinalityEstimate = field(default_factory=CardinalityEstimate)
    task: DataTask | None = None
    diagnostics: tuple[str, ...] = field(default_factory=tuple[str, ...])

    def __post_init__(self) -> None:
        if not self.ask.strip():
            raise ConfigError("analysis ask must be non-empty")
        constraints = _freeze_constraints(self.constraints)
        task = self.task or DataTask(
            id="0",
            parent_id=None,
            original_query=self.ask,
            query=self.ask,
            constraints=constraints,
            cardinality=self.cardinality,
        )
        if not all(item.strip() for item in self.diagnostics):
            raise ConfigError("analysis diagnostics must contain non-empty strings")
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "diagnostics", tuple(item.strip() for item in self.diagnostics))

    def to_dict(self) -> dict[str, object]:
        return {
            "ask": self.ask,
            "constraints": {
                name: _constraint_dict(value) for name, value in self.constraints.items()
            },
            "rewritten": self.rewritten,
            "task": self.task.to_dict() if self.task is not None else None,
        }


def query_analysis_from_payload(
    payload: object,
    *,
    asked: str,
    original_query: str | None = None,
    caller_constraints: Mapping[str, Constraint] | None = None,
) -> QueryAnalysisResult:
    """Parse the independent analysis object and salvage with caller facts."""
    diagnostics: list[str] = []
    data = cast("dict[str, Any]", payload) if isinstance(payload, dict) else {}
    if set(data) != {"ask", "constraints"}:
        diagnostics.append("analysis response did not match its object contract")
    raw_ask = data.get("ask")
    if isinstance(raw_ask, str) and raw_ask.strip():
        rewritten = True
        ask = raw_ask.strip()
    else:
        rewritten = False
        ask = asked
        diagnostics.append("analysis rewrite was unusable; using the original request")
    parsed_constraints = strict_constraints(data.get("constraints"))
    if parsed_constraints is None:
        parsed_constraints = {}
        diagnostics.append("analysis constraints were unusable; using caller constraints")
    merged = dict(parsed_constraints)
    for name, value in (caller_constraints or {}).items():
        merged[name] = intersect_constraints(merged[name], value) if name in merged else value
    root = DataTask(
        id="0",
        parent_id=None,
        original_query=original_query or asked,
        query=ask,
        constraints=merged,
    )
    return QueryAnalysisResult(
        ask=ask,
        constraints=merged,
        rewritten=rewritten,
        task=root,
        diagnostics=tuple(dict.fromkeys(diagnostics)),
    )


def query_analysis_from_response(
    text: str | None,
    *,
    asked: str,
    original_query: str | None = None,
    caller_constraints: Mapping[str, Constraint] | None = None,
) -> QueryAnalysisResult:
    """Read one independent analysis response without making another call."""
    return query_analysis_from_payload(
        _object_from(text),
        asked=asked,
        original_query=original_query,
        caller_constraints=caller_constraints,
    )


class QueryAnalyzer:
    """Prompt and pure parser for the query-analysis stage."""

    @staticmethod
    def parse(
        text: str | None,
        *,
        asked: str,
        original_query: str | None = None,
        caller_constraints: Mapping[str, Constraint] | None = None,
    ) -> QueryAnalysisResult:
        return query_analysis_from_response(
            text,
            asked=asked,
            original_query=original_query,
            caller_constraints=caller_constraints,
        )


def strict_constraints(reported: object) -> dict[str, Constraint] | None:
    """Parse a complete constraint mapping without dropping malformed entries."""
    if not isinstance(reported, dict):
        return None
    out: dict[str, Constraint] = {}
    for raw_name, raw_value in cast("dict[Any, Any]", reported).items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            return None
        name = raw_name.strip()
        if name in out:
            return None
        if not isinstance(raw_value, list):
            try:
                value = json_scalar(raw_value)
            except ConfigError:
                return None
            out[name] = Exact(value.strip() if isinstance(value, str) else value)
            continue
        values: list[JsonScalar] = []
        seen: set[object] = set()
        for raw in cast("list[Any]", raw_value):
            try:
                value = json_scalar(raw)
            except ConfigError:
                return None
            if isinstance(value, str):
                value = value.strip()
            identity = value_identity(value)
            if identity not in seen:
                seen.add(identity)
                values.append(value)
        out[name] = OneOf(tuple(values))
    return out


def bootstrap_analysis_from_payload(payload: dict[str, Any]) -> BootstrapAnalysis | None:
    """Read the analysis half of a combined bootstrap object."""
    if set(payload) != {"ask", "constraints", "row_estimate", "schema"}:
        return None
    ask = payload.get("ask")
    constraints = strict_constraints(payload.get("constraints"))
    reported_estimate = payload.get("row_estimate")
    if not isinstance(ask, str) or not ask.strip() or constraints is None:
        return None
    estimate = estimate_from_reported(
        reported_estimate,
        row_unit="records matching the accepted request and schema",
        source="combined_bootstrap",
    )
    return BootstrapAnalysis(
        ask=ask.strip(),
        constraints=constraints,
        row_estimate=estimate.expected,
        cardinality=estimate,
    )


def _object_from(text: str | None) -> dict[str, Any] | None:
    """The JSON object a response carries, or None if it does not carry one."""
    if not text or not text.strip():
        return None
    try:
        loaded: object = json.loads(strip_fence(text))
    except (ValueError, RecursionError):
        return None
    return cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else None


def _constraints_from(reported: object) -> dict[str, ConstraintValue]:
    """The constraints a response carries, keeping only entries this can vouch for.

    A partly unreadable map yields its readable entries rather than nothing, because a
    dropped filter and an absent one are the same to everything downstream, and one bad
    value should not discard the others. What is refused is refused per entry: a value
    this cannot represent is left out rather than coerced with ``str()``, since a
    stringified dict makes a meaningless filter that looks like a real one.
    """
    if not isinstance(reported, dict):
        return {}
    out: dict[str, ConstraintValue] = {}
    for raw_name, raw_value in cast("dict[Any, Any]", reported).items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        if isinstance(raw_value, list):
            items = cast("list[Any]", raw_value)
            # Every element must be a finite scalar. A mixed list would otherwise become
            # an entity set with holes in it, and row_by_row plans one leaf per element.
            #
            # Pass empty lists to the planner so it can report the dropped constraint.
            try:
                values = [json_scalar(item) for item in items]
            except ConfigError:
                continue
            out[raw_name] = values
        else:
            try:
                out[raw_name] = json_scalar(raw_value)
            except ConfigError:
                continue
    return out


def analysis_from_response(text: str | None, asked: str) -> Analysis:
    """Read step 1's response, falling back to ``asked`` when it cannot be used.

    ``asked`` is the caller's own query and is what ``ask`` becomes on every failure
    path, so this is total: there is no input for which planning cannot continue.
    """
    payload = _object_from(text)
    if payload is None:
        return Analysis(ask=asked, rewritten=False, estimate=Cardinality.UNKNOWN)

    reported_ask = payload.get("ask")
    usable = isinstance(reported_ask, str) and bool(reported_ask.strip())
    ask = reported_ask.strip() if usable and isinstance(reported_ask, str) else asked

    return Analysis(
        ask=ask,
        constraints=_constraints_from(payload.get("constraints")),
        rewritten=usable,
        estimate=from_reported(payload.get("row_estimate")),
    )
