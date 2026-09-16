from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, cast

from blue_data_llm_source.planning.bounds import MODE_PARTITIONED
from blue_data_llm_source.planning.context import validate_planning_order
from blue_data_llm_source.planning.coordinator import PlanningFacts
from blue_data_llm_source.planning.partition_safety import SAFETY_ACCEPTED
from blue_data_llm_source.planning.plan import FetchPlan
from blue_data_llm_source.types import ConfigError

PLANNING_RECEIPT_VERSION: Final = "planning-receipt-1"

_FACT_FIELDS = frozenset(
    {
        "bootstrap_attempted",
        "proposal_attempts",
        "accepted_internal_nodes",
        "entity_enumeration_attempted",
        "proposal_attempt_limit",
        "planning_order",
        "analysis_calls",
        "schema_calls",
        "cardinality_calls",
        "combined_calls",
        "context_digest",
        "distribution_search_enabled",
        "strategy_candidates_considered",
        "distribution_attempts",
        "distribution_nodes_attempted",
        "distribution_parse_failures",
        "usable_strategy_candidates",
        "selected_partition_key",
        "partition_safety_status",
        "partition_rule_digest",
        "parent_child_estimate_disagreement",
    }
)
_FACT_BOOLS = frozenset(
    {"bootstrap_attempted", "entity_enumeration_attempted", "distribution_search_enabled"}
)
_FACT_INTS = frozenset(
    {
        "proposal_attempts",
        "accepted_internal_nodes",
        "proposal_attempt_limit",
        "analysis_calls",
        "schema_calls",
        "cardinality_calls",
        "combined_calls",
        "strategy_candidates_considered",
        "distribution_attempts",
        "distribution_nodes_attempted",
        "distribution_parse_failures",
        "usable_strategy_candidates",
    }
)
_FACT_OPTIONAL_TEXT = frozenset(
    {
        "context_digest",
        "selected_partition_key",
        "partition_safety_status",
        "partition_rule_digest",
    }
)


def _facts_from_dict(value: object) -> PlanningFacts:
    if not isinstance(value, dict):
        raise ConfigError("planning receipt facts must be an object")
    data = dict(cast("dict[str, Any]", value))
    if set(data) != set(_FACT_FIELDS):
        raise ConfigError("planning receipt fact fields do not match the contract")
    for name in _FACT_BOOLS:
        if not isinstance(data[name], bool):
            raise ConfigError(f"planning receipt fact {name} must be a boolean")
    for name in _FACT_INTS:
        item = data[name]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ConfigError(f"planning receipt fact {name} must be a non-negative integer")
    for name in _FACT_OPTIONAL_TEXT:
        item = data[name]
        if item is not None and (not isinstance(item, str) or not item):
            raise ConfigError(f"planning receipt fact {name} must be a non-empty string or null")
    disagreement = data["parent_child_estimate_disagreement"]
    if disagreement is not None and not isinstance(disagreement, bool):
        raise ConfigError(
            "planning receipt fact parent_child_estimate_disagreement must be a boolean or null"
        )
    data["planning_order"] = validate_planning_order(data["planning_order"])
    status = data["partition_safety_status"]
    if status is not None and status not in {"accepted", "rejected", "unknown"}:
        raise ConfigError("planning receipt partition safety status is invalid")
    return PlanningFacts(**data)


def _digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ConfigError("planning receipt rule digests must be lowercase SHA-256 values")
    return value


@dataclass(frozen=True)
class PlanningReceipt:
    configured_rule_digests: tuple[str, ...]
    facts: PlanningFacts
    plan: FetchPlan
    contract_version: str = PLANNING_RECEIPT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != PLANNING_RECEIPT_VERSION:
            raise ConfigError("planning receipt contract version is invalid")
        digests = tuple(_digest(item) for item in self.configured_rule_digests)
        if not digests or len(set(digests)) != len(digests):
            raise ConfigError("planning receipt requires distinct configured rule digests")
        if not self.facts.distribution_search_enabled:
            raise ConfigError("planning receipt facts do not describe distribution search")
        if self.plan.mode != MODE_PARTITIONED:
            raise ConfigError("planning receipt requires a partitioned fetch plan")
        if self.facts.entity_enumeration_attempted:
            raise ConfigError("planning receipt cannot contain row-by-row enumeration facts")
        if self.facts.proposal_attempts > self.facts.proposal_attempt_limit:
            raise ConfigError("planning receipt proposal attempts exceed their limit")
        selected = self.facts.partition_rule_digest
        if selected is not None and selected not in digests:
            raise ConfigError("planning receipt selected an unconfigured rule")
        applied = {decision.rule.digest for decision in self.plan.applied_partition_decisions}
        if not applied <= set(digests):
            raise ConfigError("planning receipt plan contains an unconfigured rule")
        if self.facts.proposal_attempts != self.plan.attempted_proposals:
            raise ConfigError("planning receipt proposal count does not match its plan")
        if self.facts.accepted_internal_nodes != self.plan.accepted_internal_nodes:
            raise ConfigError("planning receipt accepted-node count does not match its plan")

        decisions = self.plan.applied_partition_decisions
        accepted = self.plan.accepted_internal_nodes
        if accepted != len(decisions):
            raise ConfigError("planning receipt accepted nodes lack applied decisions")
        if self.facts.strategy_candidates_considered < self.facts.distribution_attempts:
            raise ConfigError("planning receipt has more distribution attempts than candidates")
        if (
            self.facts.distribution_parse_failures + self.facts.usable_strategy_candidates
            > self.facts.distribution_attempts
        ):
            raise ConfigError("planning receipt distribution outcomes exceed attempts")
        if self.facts.distribution_nodes_attempted > self.facts.strategy_candidates_considered:
            raise ConfigError("planning receipt attempted more nodes than candidates")
        if self.facts.distribution_nodes_attempted < accepted:
            raise ConfigError("planning receipt accepted more nodes than distribution attempted")
        if self.facts.distribution_attempts < accepted:
            raise ConfigError("planning receipt accepted more nodes than estimates attempted")
        if self.facts.usable_strategy_candidates < accepted:
            raise ConfigError("planning receipt accepted more nodes than usable candidates")
        if decisions:
            last = decisions[-1]
            if self.facts.selected_partition_key != last.rule.dimension:
                raise ConfigError("planning receipt selected key does not match its last decision")
            if self.facts.partition_rule_digest != last.rule.digest:
                raise ConfigError("planning receipt selected rule does not match its last decision")
            if self.facts.partition_safety_status != SAFETY_ACCEPTED:
                raise ConfigError("planning receipt last applied decision is not reported accepted")
        object.__setattr__(self, "configured_rule_digests", digests)

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "configured_rule_digests": list(self.configured_rule_digests),
            "planning_facts": {name: getattr(self.facts, name) for name in sorted(_FACT_FIELDS)},
            "fetch_plan": self.plan.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> PlanningReceipt:
        if not isinstance(value, dict):
            raise ConfigError("planning receipt must be an object")
        data = cast("dict[str, Any]", value)
        expected = {
            "contract_version",
            "configured_rule_digests",
            "planning_facts",
            "fetch_plan",
        }
        if set(data) != expected:
            raise ConfigError("planning receipt fields do not match the contract")
        raw_digests = data.get("configured_rule_digests")
        if not isinstance(raw_digests, list):
            raise ConfigError("planning receipt configured rule digests must be a list")
        return cls(
            contract_version=data.get("contract_version"),  # pyright: ignore[reportArgumentType]
            configured_rule_digests=tuple(
                _digest(item) for item in cast("list[object]", raw_digests)
            ),
            facts=_facts_from_dict(data.get("planning_facts")),
            plan=FetchPlan.from_dict(data.get("fetch_plan")),
        )
