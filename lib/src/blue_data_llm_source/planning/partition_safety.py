"""Machine-checkable rules for activating a finite identity partition."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, cast

from blue_data_llm_source.reduction.identity import clean_key_names, normalized_value
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

RULE_VERSION: Final = "identity-partition-rule-1"
FINITE_DOMAIN: Final = "finite_domain"
CALLER_DECLARED: Final = "caller_declared"
SOURCE_CONFIGURATION: Final = "source_configuration"
TRUSTED_PROVENANCE: Final = (CALLER_DECLARED, SOURCE_CONFIGURATION)

SAFETY_ACCEPTED: Final = "accepted"
SAFETY_REJECTED: Final = "rejected"
SAFETY_UNKNOWN: Final = "unknown"


def _constraint_payload(value: Constraint) -> dict[str, object]:
    if isinstance(value, Exact):
        return {"kind": "exact", "value": value.value}
    if isinstance(value, Range):
        return {"kind": "range", "lo": value.lo, "hi": value.hi}
    return {"kind": "one_of", "values": list(value.values)}


def _constraint_from_payload(value: object) -> Constraint:
    if not isinstance(value, dict):
        raise ConfigError("identity partition scope constraint must be an object")
    data = cast("dict[str, Any]", value)
    kind = data.get("kind")
    if kind == "exact" and set(data) == {"kind", "value"}:
        return Exact(json_scalar(data.get("value"), "identity partition exact value"))
    if kind == "one_of" and set(data) == {"kind", "values"}:
        values = data.get("values")
        if not isinstance(values, list):
            raise ConfigError("identity partition one_of values must be a list")
        return OneOf(
            tuple(
                json_scalar(item, "identity partition one_of value")
                for item in cast("list[object]", values)
            )
        )
    raise ConfigError("identity partition scope constraint fields are invalid")


def _constraint_identity(value: Constraint) -> tuple[str, tuple[object, ...]]:
    if isinstance(value, Exact):
        return ("exact", (value_identity(value.value),))
    if isinstance(value, Range):
        return ("range", (value.lo, value.hi))
    return ("one_of", tuple(value_identity(item) for item in value.values))


def _dimension(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("identity partition dimension must be a non-empty string")
    return value.strip()


def _domain(value: object) -> tuple[JsonScalar, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ConfigError("identity partition domain must be a sequence of scalar values")
    values = cast("Sequence[object]", value)
    domain = tuple(json_scalar(item, "identity partition domain value") for item in values)
    if not domain:
        raise ConfigError("identity partition domain must not be empty")
    return domain


def _scope(value: object) -> Mapping[str, Constraint]:
    if not isinstance(value, Mapping):
        raise ConfigError("identity partition parent scope must be a mapping")
    scope: dict[str, Constraint] = {}
    for raw_name, raw_constraint in cast("Mapping[object, object]", value).items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ConfigError("identity partition parent scope names must be non-empty strings")
        if not isinstance(raw_constraint, (Exact, OneOf)):
            raise ConfigError("identity partition parent scope values must be constraints")
        scope[raw_name.strip()] = raw_constraint
    return MappingProxyType(scope)


def _metadata(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"identity partition {name} must be a non-empty string")
    return value.strip()


def _optional_metadata(value: object, name: str) -> str | None:
    return None if value is None else _metadata(value, name)


def _reason_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ConfigError("partition safety reasons must contain non-empty strings")
    values = cast("tuple[object, ...] | list[object]", value)
    if not all(isinstance(reason, str) and reason.strip() for reason in values):
        raise ConfigError("partition safety reasons must contain non-empty strings")
    return tuple(cast("str", reason).strip() for reason in values)


@dataclass(frozen=True)
class IdentityPartitionRule:
    """A caller-owned finite domain that a model may estimate but not alter."""

    dimension: str
    ordered_primary_keys: Sequence[str]
    allowed_domain: Sequence[JsonScalar]
    parent_scope: Mapping[str, Constraint] = field(default_factory=dict[str, Constraint])
    provenance: str = CALLER_DECLARED
    kind: str = FINITE_DOMAIN
    contract_version: str = RULE_VERSION

    def __post_init__(self) -> None:
        dimension = _dimension(self.dimension)
        try:
            keys = clean_key_names(self.ordered_primary_keys)
        except ConfigError as exc:
            raise ConfigError("identity partition primary keys are invalid") from exc
        if not keys:
            raise ConfigError("identity partition primary keys must not be empty")
        domain = _domain(self.allowed_domain)
        scope = _scope(self.parent_scope)
        object.__setattr__(self, "dimension", dimension)
        object.__setattr__(self, "ordered_primary_keys", keys)
        object.__setattr__(self, "allowed_domain", domain)
        object.__setattr__(self, "parent_scope", scope)
        object.__setattr__(self, "provenance", _metadata(self.provenance, "provenance"))
        object.__setattr__(self, "kind", _metadata(self.kind, "kind"))
        object.__setattr__(
            self,
            "contract_version",
            _metadata(self.contract_version, "contract version"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "ordered_primary_keys": list(self.ordered_primary_keys),
            "kind": self.kind,
            "allowed_domain": list(self.allowed_domain),
            "parent_scope": {
                name: _constraint_payload(value) for name, value in self.parent_scope.items()
            },
            "provenance": self.provenance,
            "contract_version": self.contract_version,
        }

    @classmethod
    def from_dict(cls, value: object) -> IdentityPartitionRule:
        if not isinstance(value, dict):
            raise ConfigError("identity partition rule must be an object")
        data = cast("dict[str, Any]", value)
        expected = {
            "dimension",
            "ordered_primary_keys",
            "kind",
            "allowed_domain",
            "parent_scope",
            "provenance",
            "contract_version",
        }
        if set(data) != expected:
            raise ConfigError("identity partition rule fields do not match the contract")
        raw_scope = data.get("parent_scope")
        if not isinstance(raw_scope, dict):
            raise ConfigError("identity partition parent scope must be an object")
        typed_scope = cast("dict[object, object]", raw_scope)
        scope = {
            name: _constraint_from_payload(constraint)
            for name, constraint in typed_scope.items()
            if isinstance(name, str)
        }
        if len(scope) != len(typed_scope):
            raise ConfigError("identity partition parent scope names must be strings")
        return cls(
            dimension=data.get("dimension"),  # pyright: ignore[reportArgumentType]
            ordered_primary_keys=data.get("ordered_primary_keys"),  # pyright: ignore[reportArgumentType]
            allowed_domain=data.get("allowed_domain"),  # pyright: ignore[reportArgumentType]
            parent_scope=scope,
            provenance=data.get("provenance"),  # pyright: ignore[reportArgumentType]
            kind=data.get("kind"),  # pyright: ignore[reportArgumentType]
            contract_version=data.get("contract_version"),  # pyright: ignore[reportArgumentType]
        )

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def applies_to(self, constraints: Mapping[str, Constraint]) -> bool:
        return len(constraints) >= len(self.parent_scope) and all(
            name in constraints
            and _constraint_identity(constraints[name]) == _constraint_identity(expected)
            for name, expected in self.parent_scope.items()
        )


@dataclass(frozen=True)
class PartitionSafety:
    status: str
    containment: bool | None
    disjointness: bool | None
    coverage: bool | None
    rule_digest: str | None
    evidence_code: str
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in (SAFETY_ACCEPTED, SAFETY_REJECTED, SAFETY_UNKNOWN):
            raise ConfigError("partition safety status is invalid")
        for name in ("containment", "disjointness", "coverage"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ConfigError(f"partition safety {name} must be a boolean or null")
        rule_digest = _optional_metadata(self.rule_digest, "safety rule digest")
        evidence_code = _metadata(self.evidence_code, "safety evidence code")
        reasons = _reason_tuple(self.reasons)
        object.__setattr__(self, "rule_digest", rule_digest)
        object.__setattr__(self, "evidence_code", evidence_code)
        object.__setattr__(self, "reasons", reasons)

    @property
    def accepted(self) -> bool:
        return self.status == SAFETY_ACCEPTED

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "containment": self.containment,
            "disjointness": self.disjointness,
            "coverage": self.coverage,
            "rule_digest": self.rule_digest,
            "evidence_code": self.evidence_code,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, value: object) -> PartitionSafety:
        if not isinstance(value, dict):
            raise ConfigError("partition safety must be an object")
        data = cast("dict[str, Any]", value)
        expected = {
            "status",
            "containment",
            "disjointness",
            "coverage",
            "rule_digest",
            "evidence_code",
            "reasons",
        }
        if set(data) != expected:
            raise ConfigError("partition safety fields do not match the contract")
        reasons = data.get("reasons")
        if not isinstance(reasons, list):
            raise ConfigError("partition safety reasons must be a list")
        return cls(
            status=data.get("status"),  # pyright: ignore[reportArgumentType]
            containment=data.get("containment"),  # pyright: ignore[reportArgumentType]
            disjointness=data.get("disjointness"),  # pyright: ignore[reportArgumentType]
            coverage=data.get("coverage"),  # pyright: ignore[reportArgumentType]
            rule_digest=data.get("rule_digest"),  # pyright: ignore[reportArgumentType]
            evidence_code=data.get("evidence_code"),  # pyright: ignore[reportArgumentType]
            reasons=tuple(cast("list[object]", reasons)),  # pyright: ignore[reportArgumentType]
        )


@dataclass(frozen=True)
class AppliedPartitionDecision:
    """The complete rule and safety evidence for one accepted plan node."""

    node_id: str
    rule: IdentityPartitionRule
    safety: PartitionSafety

    def __post_init__(self) -> None:
        node_id = _metadata(self.node_id, "decision node id")
        if self.rule.contract_version != RULE_VERSION:
            raise ConfigError("applied partition decision rule version is unsupported")
        if self.rule.kind != FINITE_DOMAIN:
            raise ConfigError("applied partition decision rule is not a finite domain")
        if self.rule.provenance not in TRUSTED_PROVENANCE:
            raise ConfigError("applied partition decision rule provenance is not trusted")
        if not self.safety.accepted:
            raise ConfigError("applied partition decision must carry accepted safety evidence")
        if self.safety.rule_digest != self.rule.digest:
            raise ConfigError("applied partition decision rule digest does not match its rule")
        if (
            self.safety.containment is not True
            or self.safety.disjointness is not True
            or self.safety.coverage is not True
            or self.safety.evidence_code != "declared_finite_identity_domain"
            or self.safety.reasons
        ):
            raise ConfigError("applied partition decision safety evidence is inconsistent")
        object.__setattr__(self, "node_id", node_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "rule": self.rule.to_dict(),
            "safety": self.safety.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> AppliedPartitionDecision:
        if not isinstance(value, dict):
            raise ConfigError("applied partition decision must be an object")
        data = cast("dict[str, Any]", value)
        if set(data) != {"node_id", "rule", "safety"}:
            raise ConfigError("applied partition decision fields do not match the contract")
        return cls(
            node_id=data.get("node_id"),  # pyright: ignore[reportArgumentType]
            rule=IdentityPartitionRule.from_dict(data.get("rule")),
            safety=PartitionSafety.from_dict(data.get("safety")),
        )


def evaluate_partition_safety(
    *,
    rule: IdentityPartitionRule | None,
    dimension: str,
    values: Sequence[JsonScalar],
    ordered_primary_keys: Sequence[str],
    parent_constraints: Mapping[str, Constraint],
    declared_type: str | None = None,
) -> PartitionSafety:
    """Check a proposed exact-value split against one immutable declared rule."""
    if rule is None:
        return PartitionSafety(
            status=SAFETY_UNKNOWN,
            containment=None,
            disjointness=None,
            coverage=None,
            rule_digest=None,
            evidence_code="identity_rule_missing",
            reasons=("no identity partition rule matched the parent scope",),
        )

    reasons: list[str] = []
    keys = tuple(ordered_primary_keys)
    if rule.contract_version != RULE_VERSION:
        reasons.append("identity rule version is unsupported")
    if rule.kind != FINITE_DOMAIN:
        reasons.append("identity rule is not a finite domain")
    if rule.provenance not in TRUSTED_PROVENANCE:
        reasons.append("identity rule provenance is not trusted")
    if tuple(rule.ordered_primary_keys) != keys:
        reasons.append("identity rule primary keys do not match the schema")
    if dimension != rule.dimension:
        reasons.append("proposal dimension does not match the identity rule")
    if dimension not in keys:
        reasons.append("partition dimension is not an ordered primary-key component")
    if not rule.applies_to(parent_constraints):
        reasons.append("identity rule parent scope does not match the task")

    allowed_ids = [normalized_value(value, declared_type) for value in rule.allowed_domain]
    proposed_ids = [normalized_value(value, declared_type) for value in values]
    allowed_complete = bool(allowed_ids)
    proposed_complete = bool(proposed_ids)
    if not allowed_complete:
        reasons.append("identity rule domain is empty")
    elif len(allowed_ids) < 2:
        reasons.append("identity rule domain has fewer than two values")
    if len(set(allowed_ids)) != len(allowed_ids):
        reasons.append("identity rule domain contains normalized duplicates")
    if not proposed_complete:
        reasons.append("proposed partition domain is empty")
    if len(set(proposed_ids)) != len(proposed_ids):
        reasons.append("proposed partition domain contains normalized duplicates")

    disjointness = proposed_complete and len(set(proposed_ids)) == len(proposed_ids)
    coverage = allowed_complete and disjointness and set(proposed_ids) == set(allowed_ids)
    if allowed_complete and disjointness and not coverage:
        reasons.append("proposed partition domain does not match the declared domain")

    inherited = parent_constraints.get(dimension)
    # A range parent is refused like an exact one. A finite declared domain is not checked
    # against a span here, and accepting it unchecked would claim containment nobody showed.
    if isinstance(inherited, (Exact, Range)):
        containment = False
    elif isinstance(inherited, OneOf):
        inherited_ids = {normalized_value(value, declared_type) for value in inherited.values}
        containment = bool(proposed_ids) and set(proposed_ids) <= inherited_ids
        if set(proposed_ids) != inherited_ids:
            coverage = False
            reasons.append("proposed children do not cover the inherited parent domain")
    else:
        containment = True
    if not containment:
        reasons.append("proposed children are not contained in the parent constraint")

    accepted = not reasons and containment and disjointness and coverage
    return PartitionSafety(
        status=SAFETY_ACCEPTED if accepted else SAFETY_REJECTED,
        containment=containment,
        disjointness=disjointness,
        coverage=coverage,
        rule_digest=rule.digest,
        evidence_code=("declared_finite_identity_domain" if accepted else "identity_rule_rejected"),
        reasons=tuple(reasons),
    )
