"""Bounded distribution evidence and deterministic strategy selection."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, cast

from blue_data_llm_source.planning.cardinality import (
    PARSE_INVALID,
    PARSE_PARSED,
    CardinalityEstimate,
    estimate_from_reported,
)
from blue_data_llm_source.planning.partition_safety import (
    SAFETY_ACCEPTED,
    IdentityPartitionRule,
    PartitionSafety,
)
from blue_data_llm_source.planning.schema import SchemaDefinition
from blue_data_llm_source.reduction.identity import normalized_value
from blue_data_llm_source.reduction.parsing import strip_fence
from blue_data_llm_source.types import ConfigError, JsonScalar, json_scalar

CATEGORY_FORM: Final = "category"
ScoreValue = int | float


@dataclass(frozen=True)
class DistributionValue:
    value: JsonScalar
    estimate: CardinalityEstimate


@dataclass(frozen=True)
class DistributionStats:
    key_name: str
    partitions: tuple[DistributionValue, ...] = ()
    ambiguity_score: float = 1.0
    reasoning: str = ""
    form: str = CATEGORY_FORM
    parse_status: str = PARSE_INVALID
    diagnostics: tuple[str, ...] = ()
    parent_expected_delta: int | None = None

    @property
    def num_partitions(self) -> int:
        return len(self.partitions)

    @property
    def largest_partition(self) -> int | None:
        expected = [part.estimate.expected for part in self.partitions]
        if not expected or any(value is None for value in expected):
            return None
        return max(cast("list[int]", expected))

    @property
    def balance_spread(self) -> int | None:
        expected = [part.estimate.expected for part in self.partitions]
        if not expected or any(value is None for value in expected):
            return None
        known = cast("list[int]", expected)
        return max(known) - min(known)


@dataclass(frozen=True)
class StrategyCandidate:
    schema: SchemaDefinition
    partition_key: str
    stats: DistributionStats
    safety: PartitionSafety
    schema_order: int
    rule: IdentityPartitionRule | None = None
    score: tuple[ScoreValue, ...] = field(default_factory=tuple[ScoreValue, ...])
    reasoning: str = ""


class DistributionEstimator:
    """Pure parser for one finite, rule-owned distribution response."""

    @staticmethod
    def parse(
        text: str | None,
        *,
        rule: IdentityPartitionRule,
        parent: CardinalityEstimate,
        declared_type: str | None = None,
    ) -> DistributionStats:
        def invalid(reason: str) -> DistributionStats:
            return DistributionStats(
                key_name=rule.dimension,
                diagnostics=(reason,),
                parse_status=PARSE_INVALID,
            )

        if not text or not text.strip():
            return invalid("distribution response was empty")
        try:
            payload: object = json.loads(strip_fence(text))
        except (ValueError, RecursionError):
            return invalid("distribution response was not valid JSON")
        if not isinstance(payload, dict):
            return invalid("distribution response was not an object")
        data = cast("dict[str, Any]", payload)
        if set(data) != {"key", "partitions", "ambiguity", "reasoning"}:
            return invalid("distribution response fields did not match the contract")
        if data.get("key") != rule.dimension:
            return invalid("distribution response changed the declared key")
        raw_parts = data.get("partitions")
        if not isinstance(raw_parts, list):
            return invalid("distribution partitions were not a list")
        ambiguity = data.get("ambiguity")
        ambiguity_is_finite = not isinstance(ambiguity, float) or math.isfinite(ambiguity)
        if (
            isinstance(ambiguity, bool)
            or not isinstance(ambiguity, (int, float))
            or not ambiguity_is_finite
            or not 0 <= ambiguity <= 1
        ):
            return invalid("distribution ambiguity was not within 0..1")
        reasoning = data.get("reasoning")
        if not isinstance(reasoning, str):
            return invalid("distribution reasoning was not a string")

        by_identity: dict[object, CardinalityEstimate] = {}
        for raw_part in cast("list[Any]", raw_parts):
            if not isinstance(raw_part, dict):
                return invalid("distribution partition fields did not match the contract")
            item = cast("dict[str, Any]", raw_part)
            if set(item) != {"value", "estimate"}:
                return invalid("distribution partition fields did not match the contract")
            try:
                value = json_scalar(item.get("value"), "distribution partition value")
            except ConfigError:
                return invalid("distribution partition value was not a finite JSON scalar")
            identity = normalized_value(value, declared_type)
            if identity in by_identity:
                return invalid("distribution response contained duplicate values")
            estimate_payload = item.get("estimate")
            if estimate_payload is not None:
                if not isinstance(estimate_payload, dict):
                    return invalid("distribution child estimate was malformed")
                estimate_data = cast("dict[str, Any]", estimate_payload)
                if not set(estimate_data) <= {"expected", "lower", "upper"}:
                    return invalid("distribution child estimate was malformed")
                estimate_payload = estimate_data
            estimate = estimate_from_reported(
                estimate_payload,
                row_unit="records in this partition",
                source="distribution_estimator",
            )
            if estimate.parse_status == PARSE_INVALID:
                return invalid("distribution child estimate was invalid")
            by_identity[identity] = estimate

        declared_ids = [normalized_value(value, declared_type) for value in rule.allowed_domain]
        if len(set(declared_ids)) != len(declared_ids):
            return invalid("declared domain contained normalized duplicates")
        if set(by_identity) != set(declared_ids):
            return invalid("distribution response changed the declared domain")
        partitions = tuple(
            DistributionValue(value=value, estimate=by_identity[identity])
            for value, identity in zip(rule.allowed_domain, declared_ids, strict=True)
        )
        expected = [part.estimate.expected for part in partitions]
        delta = (
            sum(cast("list[int]", expected)) - parent.expected
            if parent.expected is not None and all(value is not None for value in expected)
            else None
        )
        return DistributionStats(
            key_name=rule.dimension,
            partitions=partitions,
            ambiguity_score=float(ambiguity),
            reasoning=reasoning.strip(),
            parse_status=PARSE_PARSED,
            parent_expected_delta=delta,
        )


class StrategyEvaluator:
    """Create and rank safe candidates without another model decision."""

    @staticmethod
    def candidate(
        *,
        schema: SchemaDefinition,
        stats: DistributionStats,
        safety: PartitionSafety,
        rule: IdentityPartitionRule | None = None,
    ) -> StrategyCandidate:
        key_position = (
            schema.primary_keys.index(stats.key_name)
            if stats.key_name in schema.primary_keys
            else len(schema.primary_keys)
        )
        schema_order = next(
            (index for index, attr in enumerate(schema.attrs) if attr.name == stats.key_name),
            len(schema.attrs),
        )
        score = (
            0 if safety.status == SAFETY_ACCEPTED else 1,
            stats.largest_partition if stats.largest_partition is not None else math.inf,
            stats.balance_spread if stats.balance_spread is not None else math.inf,
            stats.ambiguity_score,
            abs(stats.parent_expected_delta)
            if stats.parent_expected_delta is not None
            else math.inf,
            key_position,
            schema_order,
        )
        return StrategyCandidate(
            schema=schema,
            partition_key=stats.key_name,
            stats=stats,
            safety=safety,
            schema_order=schema_order,
            rule=rule,
            score=score,
            reasoning=stats.reasoning,
        )

    @staticmethod
    def select(candidates: Sequence[StrategyCandidate]) -> StrategyCandidate | None:
        usable = [
            candidate
            for candidate in candidates
            if candidate.safety.accepted and candidate.stats.parse_status == PARSE_PARSED
        ]
        return min(usable, key=lambda candidate: candidate.score) if usable else None
