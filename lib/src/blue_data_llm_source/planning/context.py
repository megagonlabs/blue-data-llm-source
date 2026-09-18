"""Immutable facts shared by planning stages."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, cast

from blue_data_llm_source.planning.analysis import QueryAnalysisResult
from blue_data_llm_source.planning.cardinality import CardinalityEstimate
from blue_data_llm_source.planning.schema import SchemaDefinition
from blue_data_llm_source.types import ConfigError

PLANNING_CARDINALITY_FIRST: Final = "planning.cardinality_first"
PLANNING_SCHEMA_FIRST: Final = "planning.schema_first"
PLANNING_LOCKED_SEQUENTIAL: Final = "planning.locked_sequential"
PLANNING_LOCKED_PARALLEL: Final = "planning.locked_parallel"
PLANNING_COMBINED: Final = "planning.combined"
PLANNING_ORDERS: Final = (
    PLANNING_CARDINALITY_FIRST,
    PLANNING_SCHEMA_FIRST,
    PLANNING_LOCKED_SEQUENTIAL,
    PLANNING_LOCKED_PARALLEL,
    PLANNING_COMBINED,
)


def _runtime_value(value: object) -> object:
    """Hide static field types while checking direct-constructor inputs."""
    return value


def validate_planning_order(value: object) -> str:
    """Return a supported planning order or raise a configuration error."""
    if not isinstance(value, str) or value not in PLANNING_ORDERS:
        raise ConfigError(
            f"unknown plan.planning_order {value!r}; expected one of {', '.join(PLANNING_ORDERS)}"
        )
    return value


@dataclass(frozen=True)
class PlanningStage:
    """Observable identity and outcome for one model-backed planning stage."""

    name: str
    attempted: bool
    parsed: bool
    request_digest: str | None = None
    raw_digest: str | None = None
    parsed_digest: str | None = None
    provider_attempts: int = 0

    def __post_init__(self) -> None:
        name = _runtime_value(self.name)
        attempted = _runtime_value(self.attempted)
        parsed = _runtime_value(self.parsed)
        provider_attempts = _runtime_value(self.provider_attempts)
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("planning stage name must be non-empty")
        if not isinstance(attempted, bool) or not isinstance(parsed, bool):
            raise ConfigError("planning stage attempted and parsed must be booleans")
        if (
            isinstance(provider_attempts, bool)
            or not isinstance(provider_attempts, int)
            or provider_attempts < 0
        ):
            raise ConfigError("planning stage provider_attempts must be non-negative")
        if not self.attempted and self.provider_attempts:
            raise ConfigError("an unattempted planning stage cannot report provider attempts")
        if self.parsed and not self.attempted:
            raise ConfigError("an unattempted planning stage cannot report a parsed response")
        if self.attempted and self.request_digest is None:
            raise ConfigError("an attempted planning stage must identify its request")
        for name in ("request_digest", "raw_digest", "parsed_digest"):
            digest = getattr(self, name)
            if digest is not None and (
                not isinstance(digest, str)
                or len(digest) != 64
                or digest != digest.lower()
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ConfigError(f"planning stage {name} must be a lowercase SHA-256 digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "attempted": self.attempted,
            "parsed": self.parsed,
            "request_digest": self.request_digest,
            "raw_digest": self.raw_digest,
            "parsed_digest": self.parsed_digest,
            "provider_attempts": self.provider_attempts,
        }


@dataclass(frozen=True)
class PlanningContext:
    """Accepted analysis, schema, and cardinality for one plan."""

    analysis: QueryAnalysisResult
    schema: SchemaDefinition
    cardinality: CardinalityEstimate
    stages: tuple[PlanningStage, ...] = field(
        default_factory=tuple[PlanningStage, ...], compare=False
    )
    diagnostics: tuple[str, ...] = field(default_factory=tuple[str, ...], compare=False)
    schedule: str = field(default=PLANNING_SCHEMA_FIRST, compare=False)

    def __post_init__(self) -> None:
        validate_planning_order(self.schedule)
        if not all(item.strip() for item in self.diagnostics):
            raise ConfigError("planning diagnostics must contain non-empty strings")
        object.__setattr__(self, "stages", tuple(self.stages))
        object.__setattr__(
            self,
            "diagnostics",
            tuple(item.strip() for item in self.diagnostics),
        )

    def canonical_dict(self) -> dict[str, object]:
        """Semantic planning facts, excluding the schedule used to obtain them."""
        return {
            "analysis": self.analysis.to_dict(),
            "schema": self.schema.to_dict(),
            "cardinality": self.cardinality.to_dict(),
        }

    @property
    def digest(self) -> str:
        return value_digest(self.canonical_dict())


def _canonical_value(value: object) -> object:
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["number", value, 1]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigError("digest values must contain only finite numbers")
        numerator, denominator = value.as_integer_ratio()
        return ["number", numerator, denominator]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        entries = [(_canonical_value(key), _canonical_value(item)) for key, item in mapping.items()]
        entries.sort(
            key=lambda entry: json.dumps(
                entry[0], sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
        )
        return ["object", [[key, item] for key, item in entries]]
    if isinstance(value, (list, tuple)):
        sequence = cast("list[object] | tuple[object, ...]", value)
        return ["array", [_canonical_value(item) for item in sequence]]
    raise ConfigError(f"digest values must be JSON-compatible, got {type(value).__name__}")


def value_digest(value: object) -> str:
    """Stable identity for a prompt or parsed planning value."""
    encoded = json.dumps(_canonical_value(value), separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()
