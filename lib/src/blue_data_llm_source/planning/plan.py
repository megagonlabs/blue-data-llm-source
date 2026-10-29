"""The plan: what will be asked, in how many pieces, and what that leaves unsaid.

Building the plan is a pure function of what earlier steps established. Every model call
that informs it — choosing a partition key, enumerating its values, scoring how ambiguous
it is — happens before this and arrives as a :class:`PartitionProposal`. So the guards,
which are the part that decides how much a request costs, are testable without a provider.

When a guard fires, the node collapses to one leaf covering its full range. A typed gap
records the reason. All enumerated values remain in the plan; only granularity is lost.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final, cast

from blue_data_llm_source.planning.bounds import (
    MODE_FLAT,
    MODE_PARTITIONED,
    MODE_ROW_BY_ROW,
    MODES,
    PlanLimits,
    proposal_attempt_limit,
)
from blue_data_llm_source.planning.cardinality import (
    CardinalityEstimate,
    estimate_from_dict,
    estimate_from_reported,
    upper_bound,
)
from blue_data_llm_source.planning.partition_safety import (
    AppliedPartitionDecision,
    evaluate_partition_safety,
)
from blue_data_llm_source.planning.ranges import (
    SMALL_ORDINAL_SPAN,
    is_numeric,
    is_ordinal,
    small_ordinal,
    typed_children,
)
from blue_data_llm_source.planning.schema import Schema
from blue_data_llm_source.prompts import canonical_child_wording, split_filters
from blue_data_llm_source.reduction.identity import (
    KeyTuple,
    clean_key_names,
    key_identity,
    normalized_value,
)
from blue_data_llm_source.types import (
    Attr,
    ConfigError,
    Constraint,
    Exact,
    JsonScalar,
    OneOf,
    Range,
    json_scalar,
    resolve_attr,
    value_identity,
)

ROOT: Final = "0"
PLAN_SERIALIZATION_VERSION: Final = 1
RANGE_PLAN_SERIALIZATION_VERSION: Final = 2
"""The version a plan carrying any ``Range`` constraint is written under.

Its own number because a 0.2.1.0 reader of version 1 has no range kind, and a payload it must
refuse should say so by its version rather than fail on a constraint deep inside. A plan
without ranges is still written as version 1, byte for byte as before."""
PLAN_SERIALIZATION_VERSIONS: Final = (PLAN_SERIALIZATION_VERSION, RANGE_PLAN_SERIALIZATION_VERSION)

VALUES_FROM_ESTIMATOR: Final = "estimator"
VALUES_FROM_ENUMERATION: Final = "enumeration"
VALUES_FROM_CONSTRAINTS: Final = "caller_constraints"

CHILD_WORDING_PARENT: Final = "parent"
CHILD_WORDING_CANONICAL: Final = "canonical"
CHILD_WORDINGS: Final = (CHILD_WORDING_PARENT, CHILD_WORDING_CANONICAL)
"""How a child leaf's retrieval wording is chosen, the first being the default."""


@dataclass(frozen=True)
class PartitionPolicy:
    """How an accepted proposal becomes child leaves. The defaults are 0.2.1.0 behavior.

    ``child_wording="parent"`` gives every child its parent's wording, separated from its
    siblings only by the filter. ``"canonical"`` derives the wording from the root request
    plus the child's validated filters, so the question a child is asked names the slice it
    covers.

    ``range_filters`` reads a model proposal written as integer spans, on a column the schema
    declares an integer, as :class:`~blue_data_llm_source.types.Range` filters instead of
    exact text. See :mod:`blue_data_llm_source.planning.ranges` for what qualifies.

    ``partition_guardrails`` sends the ``partition-2`` proposal prompt, with rules about
    which keys and values make a usable split, and refuses a model-proposed key the schema
    declares numeric whose values are not a small ordinal set: a continuous measurement split
    into buckets gives children whose filters no record's exact value falls under.
    """

    child_wording: str = CHILD_WORDING_PARENT
    range_filters: bool = False
    partition_guardrails: bool = False

    def __post_init__(self) -> None:
        if self.child_wording not in CHILD_WORDINGS:
            raise ConfigError(
                f"unknown child wording {self.child_wording!r}; expected one of "
                f"{', '.join(CHILD_WORDINGS)}"
            )
        if not isinstance(self.range_filters, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigError("range_filters must be a boolean")
        if not isinstance(self.partition_guardrails, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigError("partition_guardrails must be a boolean")


class Gap:
    """Why rows may be missing. A code rather than free text, so it binds to a plan node."""

    LEAF_EMPTY = "leaf_empty"
    LEAF_EMPTY_QUERY = "leaf_empty_query"
    """The leaf was processed and refused before any call, because it carried no question.

    Distinct from both of its neighbors. ``LEAF_EMPTY`` claims a model was asked and had
    nothing, which is a statement about the world that nobody made; ``LEAF_NOT_RUN`` says
    execution never reached the leaf, which drops the only fact anyone has about it.
    """

    LEAF_AT_CAP = "leaf_at_cap"
    LEAF_RESPONSE_TRUNCATED = "leaf_response_truncated"
    """A response did not parse whole and its complete records were kept.

    Distinct from ``LEAF_AT_CAP``, which this project's own caps cause. There every row the
    model produced was seen; here rows were produced and billed and never arrived, and what
    will fetch them is a larger output allowance rather than a larger row target. One code
    for both would send a caller to raise the setting that is not holding the answer back.

    The note's detail says whether the provider attested that its output allowance ended the
    response, or whether nothing said why. Only the first is a cause; the second is the
    observation on its own.
    """

    LEAF_STOPPED_BY_LIMIT = "leaf_stopped_by_limit"
    LEAF_FAILED = "leaf_failed"
    LEAF_NOT_RUN = "leaf_not_run"
    ENUMERATION_SHORT = "enumeration_short"
    GUARD_COLLAPSED = "guard_collapsed"
    PROPOSAL_REJECTED = "proposal_rejected"
    PLAN_TRUNCATED = "plan_truncated"
    ESTIMATE_UNKNOWN = "estimate_unknown"
    DEDUP_KEY_ABSENT = "dedup_key_absent"
    DEDUP_CONFLICT = "dedup_conflict"
    DEDUP_NORMALIZATION_COLLISION = "dedup_normalization_collision"
    INCOMPLETE_KEY = "incomplete_key"
    CONSTRAINT_DROPPED = "constraint_dropped"

    ALL: Final = (
        LEAF_EMPTY,
        LEAF_EMPTY_QUERY,
        LEAF_AT_CAP,
        LEAF_RESPONSE_TRUNCATED,
        LEAF_STOPPED_BY_LIMIT,
        LEAF_FAILED,
        LEAF_NOT_RUN,
        ENUMERATION_SHORT,
        GUARD_COLLAPSED,
        PROPOSAL_REJECTED,
        PLAN_TRUNCATED,
        ESTIMATE_UNKNOWN,
        DEDUP_KEY_ABSENT,
        DEDUP_CONFLICT,
        DEDUP_NORMALIZATION_COLLISION,
        INCOMPLETE_KEY,
        CONSTRAINT_DROPPED,
    )


@dataclass(frozen=True)
class Note:
    """One gap, bound to the plan node or leaf it describes."""

    code: str
    target_id: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class PartitionProposal:
    """What the planning calls produced about how to split the ask.

    ``ambiguity`` is the estimator's own confidence that the key means one thing, on 0..1
    where 1 is most ambiguous. It arrives from a model and is used only to collapse, never
    to expand, so a wrong score costs granularity rather than money.
    """

    key: str
    values: Sequence[JsonScalar]
    values_from: str = VALUES_FROM_ENUMERATION
    ambiguity: float = 0.0
    from_caller: bool = False
    """True when the caller named this key rather than the planner choosing it."""

    child_estimates: tuple[int | None, ...] = ()
    """Optional row hints aligned with ``values``. Missing entries stay unknown."""

    partition_safety_status: str | None = None
    partition_rule_digest: str | None = None


@dataclass(frozen=True)
class Node:
    """An internal partition node — a task, not just partition metadata."""

    id: str
    parent_id: str | None
    query: str
    constraints: dict[str, Constraint]
    key: str
    key_in_schema: bool
    values: list[JsonScalar]
    values_from: str
    depth: int = 0
    original_query: str = ""
    estimate: int | None = None
    partition_safety_status: str | None = None
    partition_rule_digest: str | None = None


@dataclass(frozen=True)
class Leaf:
    """One bounded ask. A flat plan is a plan with one of these."""

    id: str
    parent_id: str | None
    query: str
    constraints: dict[str, Constraint]
    depth: int = 0
    original_query: str = ""
    estimate: int | None = None
    cardinality: CardinalityEstimate | None = None
    partition_safety_status: str | None = None
    partition_rule_digest: str | None = None


def _schema_declared_types(value: object) -> tuple[tuple[str, str | None], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ConfigError("plan schema declared types must be a sequence")
    entries = cast("Sequence[object]", value)
    typed: list[tuple[str, str | None]] = []
    names: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, (tuple, list)):
            raise ConfigError("plan schema declared type entries must be name/type pairs")
        entry = cast("tuple[object, ...] | list[object]", raw_entry)
        if len(entry) != 2:
            raise ConfigError("plan schema declared type entries must be name/type pairs")
        raw_name, raw_type = entry
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ConfigError("plan schema column names must be non-empty strings")
        name = raw_name.strip()
        if name in names:
            raise ConfigError("plan schema column names must be distinct")
        if raw_type is not None and not isinstance(raw_type, str):
            raise ConfigError("plan schema column types must be strings or null")
        names.add(name)
        typed.append((name, raw_type))
    return tuple(typed)


@dataclass(frozen=True)
class PlanSchemaContract:
    """Schema facts used to verify serialized partition decisions.

    These checks establish internal semantic agreement. They do not authenticate a
    wholesale coordinated replacement of the plan, rules, and schema contract; that
    requires a trusted transport or signature outside this serialization format.
    """

    ordered_primary_keys: Sequence[str] = ()
    declared_types: Sequence[tuple[str, str | None]] = ()

    def __post_init__(self) -> None:
        keys = clean_key_names(self.ordered_primary_keys)
        typed = _schema_declared_types(self.declared_types)
        object.__setattr__(self, "ordered_primary_keys", keys)
        object.__setattr__(self, "declared_types", typed)

    @classmethod
    def from_schema(cls, schema: Schema) -> PlanSchemaContract:
        return cls(
            ordered_primary_keys=schema.primary_keys,
            declared_types=tuple((attr.name, attr.type) for attr in schema.attrs),
        )

    def has_column(self, name: str) -> bool:
        return any(column == name for column, _ in self.declared_types)

    def type_for(self, name: str) -> str | None:
        return next(
            (declared_type for column, declared_type in self.declared_types if column == name),
            None,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "ordered_primary_keys": list(self.ordered_primary_keys),
            "declared_types": [
                {"name": name, "type": declared_type} for name, declared_type in self.declared_types
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> PlanSchemaContract:
        if not isinstance(value, dict):
            raise ConfigError("plan schema contract must be an object")
        data = cast("dict[str, Any]", value)
        if set(data) != {"ordered_primary_keys", "declared_types"}:
            raise ConfigError("plan schema contract fields are invalid")
        raw_keys = _required_list(
            data.get("ordered_primary_keys"), "plan schema ordered primary keys"
        )
        raw_types = _required_list(data.get("declared_types"), "plan schema declared types")
        declared_types: list[tuple[str, str | None]] = []
        for item in raw_types:
            if not isinstance(item, dict):
                raise ConfigError("plan schema declared type must be an object")
            entry = cast("dict[str, object]", item)
            if set(entry) != {"name", "type"}:
                raise ConfigError("plan schema declared type fields are invalid")
            name = entry.get("name")
            declared_type = entry.get("type")
            if not isinstance(name, str):
                raise ConfigError("plan schema column name must be a string")
            if declared_type is not None and not isinstance(declared_type, str):
                raise ConfigError("plan schema column type must be a string or null")
            declared_types.append((name, declared_type))
        return cls(
            ordered_primary_keys=tuple(
                _required_text(item, "plan schema primary key") for item in raw_keys
            ),
            declared_types=tuple(declared_types),
        )


def _constraint_identity(value: Constraint) -> tuple[str, tuple[object, ...]]:
    if isinstance(value, Exact):
        return ("exact", (value_identity(value.value),))
    if isinstance(value, Range):
        return ("range", (value.lo, value.hi))
    return ("one_of", tuple(value_identity(item) for item in value.values))


def _validate_safety_graph(nodes: list[Node], leaves: list[Leaf]) -> None:
    objects: list[Node | Leaf] = [*nodes, *leaves]
    ids = [item.id for item in objects]
    if len(set(ids)) != len(ids):
        raise ConfigError("safety-traced plan object ids must be unique")
    by_id = {node.id: node for node in nodes}
    roots = [node for node in nodes if node.parent_id is None]
    if len(roots) != 1 or roots[0].depth != 0:
        raise ConfigError("safety-traced plan must have one depth-zero root node")
    root = roots[0]
    for item in objects:
        if item is root:
            continue
        if item.parent_id is None:
            raise ConfigError("safety-traced plan object is detached from its parent")
        parent = by_id.get(item.parent_id)
        if parent is None:
            raise ConfigError("safety-traced plan object has an unknown parent")
        seen = {item.id}
        ancestor = parent
        while ancestor is not root:
            if ancestor.id in seen:
                raise ConfigError("safety-traced plan contains a parent cycle")
            seen.add(ancestor.id)
            if ancestor.parent_id is None:
                raise ConfigError("safety-traced plan subtree is detached from the root")
            next_ancestor = by_id.get(ancestor.parent_id)
            if next_ancestor is None:
                raise ConfigError("safety-traced plan subtree has an unknown ancestor")
            ancestor = next_ancestor
        if item.depth != parent.depth + 1:
            raise ConfigError("safety-traced plan child depth does not match its parent")


def _validate_decision_children(
    *,
    node: Node,
    decision: AppliedPartitionDecision,
    nodes: list[Node],
    leaves: list[Leaf],
    schema_contract: PlanSchemaContract,
) -> None:
    declared_type = schema_contract.type_for(decision.rule.dimension)
    allowed = {
        normalized_value(value, declared_type): value for value in decision.rule.allowed_domain
    }
    children: list[Node | Leaf] = [item for item in [*nodes, *leaves] if item.parent_id == node.id]
    if len(children) != len(allowed):
        raise ConfigError("safety-traced node children do not match the declared domain")

    expected_names = set(node.constraints) | {decision.rule.dimension}
    observed: set[object] = set()
    for child in children:
        if child.depth != node.depth + 1:
            raise ConfigError("safety-traced child depth does not match its partition node")
        if set(child.constraints) != expected_names:
            raise ConfigError("safety-traced child constraints do not match its partition scope")
        if any(
            _constraint_identity(child.constraints[name]) != _constraint_identity(constraint)
            for name, constraint in node.constraints.items()
            if name != decision.rule.dimension
        ):
            raise ConfigError("safety-traced child changed an inherited constraint")
        split = child.constraints.get(decision.rule.dimension)
        if not isinstance(split, Exact):
            raise ConfigError("safety-traced child must carry one exact partition value")
        identity = normalized_value(split.value, declared_type)
        if identity not in allowed or identity in observed:
            raise ConfigError("safety-traced child identity is duplicate or outside the domain")
        observed.add(identity)
    if observed != set(allowed):
        raise ConfigError("safety-traced node omits a declared child identity")


@dataclass(frozen=True)
class FetchPlan:
    mode: str
    """The mode that will run, after any guard collapse — not necessarily the one asked for."""

    leaves: list[Leaf] = field(default_factory=list[Leaf])
    nodes: list[Node] = field(default_factory=list[Node])
    notes: list[Note] = field(default_factory=list[Note])
    refused: bool = False
    """True when the plan cannot be built within its caps. Carries what it would have run."""

    attempted_proposals: int = 0
    accepted_internal_nodes: int = 0
    applied_partition_decisions: tuple[AppliedPartitionDecision, ...] = ()
    schema_contract: PlanSchemaContract = field(default_factory=PlanSchemaContract)

    def __post_init__(self) -> None:
        decision_ids = [decision.node_id for decision in self.applied_partition_decisions]
        if len(set(decision_ids)) != len(decision_ids):
            raise ConfigError("fetch plan contains duplicate applied partition decisions")
        nodes = {node.id: node for node in self.nodes}
        anchored = bool(
            self.schema_contract.ordered_primary_keys or self.schema_contract.declared_types
        )
        if anchored:
            if not self.nodes:
                raise ConfigError("nonempty plan schema contract requires partition nodes")
            if any(
                node.values_from != VALUES_FROM_ESTIMATOR
                or node.partition_safety_status is None
                or node.partition_rule_digest is None
                for node in self.nodes
            ):
                raise ConfigError(
                    "every node under a plan schema contract requires complete safety trace"
                )
            traced_node_ids = {node.id for node in self.nodes}
        else:
            traced_node_ids = {
                node.id
                for node in self.nodes
                if node.values_from == VALUES_FROM_ESTIMATOR
                or node.partition_safety_status is not None
                or node.partition_rule_digest is not None
            }
        if set(decision_ids) != traced_node_ids:
            raise ConfigError(
                "fetch plan must carry exactly one decision for each safety-traced node"
            )
        if decision_ids and not self.schema_contract.ordered_primary_keys:
            raise ConfigError("partition decisions require an authoritative plan schema contract")
        if traced_node_ids:
            _validate_safety_graph(self.nodes, self.leaves)
        for decision in self.applied_partition_decisions:
            node = nodes.get(decision.node_id)
            if node is None:
                raise ConfigError("applied partition decision does not name a plan node")
            reevaluated = evaluate_partition_safety(
                rule=decision.rule,
                dimension=node.key,
                values=node.values,
                ordered_primary_keys=self.schema_contract.ordered_primary_keys,
                parent_constraints=node.constraints,
                declared_type=self.schema_contract.type_for(node.key),
            )
            if (
                not reevaluated.accepted
                or reevaluated != decision.safety
                or tuple(decision.rule.ordered_primary_keys)
                != tuple(self.schema_contract.ordered_primary_keys)
                or not self.schema_contract.has_column(node.key)
                or node.key != decision.rule.dimension
                or tuple(value_identity(value) for value in node.values)
                != tuple(value_identity(value) for value in decision.rule.allowed_domain)
                or not decision.rule.applies_to(node.constraints)
                or node.partition_safety_status != decision.safety.status
                or node.partition_rule_digest != decision.safety.rule_digest
            ):
                raise ConfigError("applied partition decision does not match its plan node")
            _validate_decision_children(
                node=node,
                decision=decision,
                nodes=self.nodes,
                leaves=self.leaves,
                schema_contract=self.schema_contract,
            )

    @property
    def leaf_count(self) -> int:
        return len(self.leaves)

    def _has_ranges(self) -> bool:
        holders: list[Leaf | Node] = [*self.leaves, *self.nodes]
        return any(
            isinstance(constraint, Range)
            for holder in holders
            for constraint in holder.constraints.values()
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "serialization_version": (
                RANGE_PLAN_SERIALIZATION_VERSION
                if self._has_ranges()
                else PLAN_SERIALIZATION_VERSION
            ),
            "mode": self.mode,
            "leaves": [_leaf_to_dict(leaf) for leaf in self.leaves],
            "nodes": [_node_to_dict(node) for node in self.nodes],
            "notes": [_note_to_dict(note) for note in self.notes],
            "refused": self.refused,
            "attempted_proposals": self.attempted_proposals,
            "accepted_internal_nodes": self.accepted_internal_nodes,
            "applied_partition_decisions": [
                decision.to_dict() for decision in self.applied_partition_decisions
            ],
            "schema_contract": self.schema_contract.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> FetchPlan:
        if not isinstance(value, dict):
            raise ConfigError("fetch plan must be an object")
        data = cast("dict[str, Any]", value)
        expected = {
            "serialization_version",
            "mode",
            "leaves",
            "nodes",
            "notes",
            "refused",
            "attempted_proposals",
            "accepted_internal_nodes",
            "applied_partition_decisions",
            "schema_contract",
        }
        version = data.get("serialization_version")
        if (
            set(data) != expected
            or type(version) is not int
            or version not in PLAN_SERIALIZATION_VERSIONS
        ):
            raise ConfigError("fetch plan fields or serialization version are invalid")
        mode = _required_text(data.get("mode"), "fetch plan mode")
        if mode not in MODES:
            raise ConfigError(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")
        leaves = _required_list(data.get("leaves"), "fetch plan leaves")
        nodes = _required_list(data.get("nodes"), "fetch plan nodes")
        notes = _required_list(data.get("notes"), "fetch plan notes")
        decisions = _required_list(
            data.get("applied_partition_decisions"), "fetch plan applied decisions"
        )
        refused = data.get("refused")
        if not isinstance(refused, bool):
            raise ConfigError("fetch plan refused must be a boolean")
        plan = cls(
            mode=mode,
            leaves=[_leaf_from_dict(item) for item in leaves],
            nodes=[_node_from_dict(item) for item in nodes],
            notes=[_note_from_dict(item) for item in notes],
            refused=refused,
            attempted_proposals=_nonnegative_int(
                data.get("attempted_proposals"), "fetch plan attempted proposals"
            ),
            accepted_internal_nodes=_nonnegative_int(
                data.get("accepted_internal_nodes"), "fetch plan accepted nodes"
            ),
            applied_partition_decisions=tuple(
                AppliedPartitionDecision.from_dict(item) for item in decisions
            ),
            schema_contract=PlanSchemaContract.from_dict(data.get("schema_contract")),
        )
        if version == PLAN_SERIALIZATION_VERSION and plan._has_ranges():
            raise ConfigError("fetch plan serialization version 1 cannot carry range constraints")
        return plan


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a non-empty string")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    return value


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _required_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a list")
    return cast("list[object]", value)


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{name} must be a non-negative integer")
    return value


def _optional_nonnegative_int(value: object, name: str) -> int | None:
    return None if value is None else _nonnegative_int(value, name)


def _constraint_to_dict(value: Constraint) -> dict[str, object]:
    if isinstance(value, Exact):
        return {"kind": "exact", "value": value.value}
    if isinstance(value, Range):
        return {"kind": "range", "lo": value.lo, "hi": value.hi}
    return {"kind": "one_of", "values": list(value.values)}


def _constraint_from_dict(value: object) -> Constraint:
    if not isinstance(value, dict):
        raise ConfigError("fetch plan constraint must be an object")
    data = cast("dict[str, Any]", value)
    if set(data) == {"kind", "value"} and data.get("kind") == "exact":
        return Exact(json_scalar(data.get("value"), "fetch plan exact value"))
    if set(data) == {"kind", "values"} and data.get("kind") == "one_of":
        return OneOf(
            tuple(
                json_scalar(item, "fetch plan one_of value")
                for item in _required_list(data.get("values"), "fetch plan one_of values")
            )
        )
    if set(data) == {"kind", "lo", "hi"} and data.get("kind") == "range":
        return Range(data.get("lo"), data.get("hi"))  # pyright: ignore[reportArgumentType]
    raise ConfigError("fetch plan constraint fields are invalid")


def _constraints_to_dict(values: Mapping[str, Constraint]) -> dict[str, object]:
    return {name: _constraint_to_dict(value) for name, value in values.items()}


def _constraints_from_dict(value: object) -> dict[str, Constraint]:
    if not isinstance(value, dict):
        raise ConfigError("fetch plan constraints must be an object")
    data = cast("dict[object, object]", value)
    if not all(isinstance(name, str) and name for name in data):
        raise ConfigError("fetch plan constraint names must be non-empty strings")
    return {
        cast("str", name): _constraint_from_dict(constraint) for name, constraint in data.items()
    }


def _leaf_to_dict(leaf: Leaf) -> dict[str, object]:
    return {
        "id": leaf.id,
        "parent_id": leaf.parent_id,
        "query": leaf.query,
        "constraints": _constraints_to_dict(leaf.constraints),
        "depth": leaf.depth,
        "original_query": leaf.original_query,
        "estimate": leaf.estimate,
        "cardinality": leaf.cardinality.to_dict() if leaf.cardinality is not None else None,
        "partition_safety_status": leaf.partition_safety_status,
        "partition_rule_digest": leaf.partition_rule_digest,
    }


def _leaf_from_dict(value: object) -> Leaf:
    if not isinstance(value, dict):
        raise ConfigError("fetch plan leaf must be an object")
    data = cast("dict[str, Any]", value)
    expected = {
        "id",
        "parent_id",
        "query",
        "constraints",
        "depth",
        "original_query",
        "estimate",
        "cardinality",
        "partition_safety_status",
        "partition_rule_digest",
    }
    if set(data) != expected:
        raise ConfigError("fetch plan leaf fields are invalid")
    raw_cardinality = data.get("cardinality")
    return Leaf(
        id=_required_text(data.get("id"), "fetch plan leaf id"),
        parent_id=_optional_text(data.get("parent_id"), "fetch plan leaf parent id"),
        query=_string(data.get("query"), "fetch plan leaf query"),
        constraints=_constraints_from_dict(data.get("constraints")),
        depth=_nonnegative_int(data.get("depth"), "fetch plan leaf depth"),
        original_query=_string(data.get("original_query"), "fetch plan leaf original query"),
        estimate=_optional_nonnegative_int(data.get("estimate"), "fetch plan leaf estimate"),
        cardinality=(estimate_from_dict(raw_cardinality) if raw_cardinality is not None else None),
        partition_safety_status=_optional_text(
            data.get("partition_safety_status"), "fetch plan leaf safety status"
        ),
        partition_rule_digest=_optional_text(
            data.get("partition_rule_digest"), "fetch plan leaf rule digest"
        ),
    )


def _node_to_dict(node: Node) -> dict[str, object]:
    return {
        "id": node.id,
        "parent_id": node.parent_id,
        "query": node.query,
        "constraints": _constraints_to_dict(node.constraints),
        "key": node.key,
        "key_in_schema": node.key_in_schema,
        "values": list(node.values),
        "values_from": node.values_from,
        "depth": node.depth,
        "original_query": node.original_query,
        "estimate": node.estimate,
        "partition_safety_status": node.partition_safety_status,
        "partition_rule_digest": node.partition_rule_digest,
    }


def _node_from_dict(value: object) -> Node:
    if not isinstance(value, dict):
        raise ConfigError("fetch plan node must be an object")
    data = cast("dict[str, Any]", value)
    expected = {
        "id",
        "parent_id",
        "query",
        "constraints",
        "key",
        "key_in_schema",
        "values",
        "values_from",
        "depth",
        "original_query",
        "estimate",
        "partition_safety_status",
        "partition_rule_digest",
    }
    if set(data) != expected:
        raise ConfigError("fetch plan node fields are invalid")
    key_in_schema = data.get("key_in_schema")
    if not isinstance(key_in_schema, bool):
        raise ConfigError("fetch plan node key_in_schema must be a boolean")
    return Node(
        id=_required_text(data.get("id"), "fetch plan node id"),
        parent_id=_optional_text(data.get("parent_id"), "fetch plan node parent id"),
        query=_string(data.get("query"), "fetch plan node query"),
        constraints=_constraints_from_dict(data.get("constraints")),
        key=_required_text(data.get("key"), "fetch plan node key"),
        key_in_schema=key_in_schema,
        values=[
            json_scalar(item, "fetch plan node value")
            for item in _required_list(data.get("values"), "fetch plan node values")
        ],
        values_from=_required_text(data.get("values_from"), "fetch plan node values source"),
        depth=_nonnegative_int(data.get("depth"), "fetch plan node depth"),
        original_query=_string(data.get("original_query"), "fetch plan node original query"),
        estimate=_optional_nonnegative_int(data.get("estimate"), "fetch plan node estimate"),
        partition_safety_status=_optional_text(
            data.get("partition_safety_status"), "fetch plan node safety status"
        ),
        partition_rule_digest=_optional_text(
            data.get("partition_rule_digest"), "fetch plan node rule digest"
        ),
    )


def _note_to_dict(note: Note) -> dict[str, object]:
    return {"code": note.code, "target_id": note.target_id, "detail": note.detail}


def _note_from_dict(value: object) -> Note:
    if not isinstance(value, dict):
        raise ConfigError("fetch plan note must be an object")
    data = cast("dict[str, Any]", value)
    if set(data) != {"code", "target_id", "detail"}:
        raise ConfigError("fetch plan note fields are invalid")
    detail = data.get("detail")
    if not isinstance(detail, str):
        raise ConfigError("fetch plan note detail must be a string")
    return Note(
        code=_required_text(data.get("code"), "fetch plan note code"),
        target_id=_optional_text(data.get("target_id"), "fetch plan note target id"),
        detail=detail,
    )


def _flat(ask: str, constraints: dict[str, Constraint], notes: list[Note]) -> FetchPlan:
    """One leaf, and notes retargeted onto it.

    A flat plan has no partition node. Move root notes to the leaf that runs.
    """
    leaf = Leaf(id=f"{ROOT}.0", parent_id=ROOT, query=ask, constraints=dict(constraints))
    rebound = [
        Note(code=n.code, target_id=leaf.id, detail=n.detail) if n.target_id == ROOT else n
        for n in notes
    ]
    return FetchPlan(mode=MODE_FLAT, leaves=[leaf], nodes=[], notes=rebound)


def _dedup(values: Sequence[JsonScalar]) -> list[JsonScalar]:
    """Distinct values in first-seen order.

    Analysis output is model output and may name the same value twice. A duplicate value
    spends a leaf and a call to ask a question already asked, and returns the same rows
    again under a second identity.
    """
    seen: set[object] = set()
    out: list[JsonScalar] = []
    for value in values:
        identity = value_identity(value)
        if identity not in seen:
            seen.add(identity)
            out.append(value)
    return out


def _filters(
    constraints: dict[str, JsonScalar | list[JsonScalar]],
    *,
    split_on: str | None = None,
    notes: list[Note] | None = None,
) -> dict[str, Constraint]:
    """Every constraint as a tagged filter, with a list kept as its alternatives.

    A list value is what a split reads as its entity set, but it is a filter first. Keeping
    only the scalars means a request the model narrowed to one named value runs as the
    unnarrowed question whenever the split does not happen — a wider ask than the caller
    made, answered without saying so. ``split_on`` omits the key a partition is already
    splitting on, whose values its leaves carry one at a time.

    An empty list is a malformed constraint. Drop it and add a note so the resulting
    wider request is visible to the caller.
    """
    out: dict[str, Constraint] = {}
    for name, value in constraints.items():
        if name == split_on:
            continue
        if not isinstance(value, list):
            out[name] = Exact(value)
            continue
        alternatives = _dedup(value)
        if not alternatives:
            if notes is not None:
                notes.append(
                    Note(
                        code=Gap.CONSTRAINT_DROPPED,
                        target_id=ROOT,
                        detail=(
                            f"{name!r} enumerated no values, so the filter was dropped and "
                            "the request runs wider than it was narrowed to"
                        ),
                    )
                )
            continue
        # A one-element list stays a disjunction. The caller wrote alternatives and named
        # one, which is a different statement from naming a value outright: it says more
        # were expected. Collapsing it would lose the only signal that says so.
        out[name] = OneOf(tuple(alternatives))
    return out


def _covers(values: list[JsonScalar]) -> Constraint:
    """What a leaf covering *values* must match.

    ``Exact`` for a single value, because that is what the leaf covers — no alternative was
    dropped and saying "one of" would send a reader looking for one. The asymmetry with a
    caller's own one-element list is deliberate: there the list is the caller's statement
    that alternatives existed, and here the count is this plan's own arithmetic.
    """
    return Exact(values[0]) if len(values) == 1 else OneOf(tuple(values))


def _buckets(values: list[JsonScalar], count: int) -> list[list[JsonScalar]]:
    """``values`` in at most ``count`` contiguous groups of near-equal size.

    Grouping rather than a remainder. A remainder leaf carrying no constraint of its own
    asks the whole question again: it overlaps every partition that was expanded and covers
    none of the values it was standing in for, so the values past the cap are absent from
    the plan and present in the bill. Grouped, every value the enumeration named is encoded
    in exactly one leaf — nothing it listed is dropped and nothing is listed twice — and only
    the granularity is lost.

    What that does not establish is coverage of the answer. The enumeration came from a model
    and may be wrong, its values may overlap, and a model may omit records from a filter it
    was given correctly.
    """
    if count <= 0:
        return []
    size, extra = divmod(len(values), count)
    out: list[list[JsonScalar]] = []
    start = 0
    for i in range(min(count, len(values))):
        end = start + size + (1 if i < extra else 0)
        out.append(values[start:end])
        start = end
    return out


def _entity_list(
    constraints: dict[str, JsonScalar | list[JsonScalar]],
) -> tuple[str, list[JsonScalar]] | None:
    """The first list-valued constraint, which is what row_by_row splits on."""
    for name, value in constraints.items():
        if isinstance(value, list) and value:
            return name, value
    return None


def _guard_failure(
    proposal: PartitionProposal | None, estimate: str, limits: PlanLimits
) -> str | None:
    """Why this node cannot usefully be partitioned, or None if it can.

    Each of these is a case where splitting produces a worse answer than not splitting,
    and each costs a call per partition to find out the hard way.
    """
    if proposal is None:
        return "no partition key was proposed"
    if not proposal.values:
        return f"{proposal.key!r} enumerated no values"
    if len(proposal.values) == 1:
        return (
            f"{proposal.key!r} enumerated one value, so splitting adds a constraint and no coverage"
        )
    if proposal.ambiguity > limits.ambiguity_threshold:
        return (
            f"{proposal.key!r} scored {proposal.ambiguity} for ambiguity, "
            f"above the {limits.ambiguity_threshold} threshold"
        )
    rows = upper_bound(estimate)
    if rows is not None and len(proposal.values) > rows:
        return (
            f"{proposal.key!r} enumerated {len(proposal.values)} values against at most "
            f"{rows} rows, so most partitions can only come back empty"
        )
    return None


def build_plan(
    *,
    mode: str,
    ask: str,
    constraints: dict[str, JsonScalar | list[JsonScalar]],
    schema: Schema,
    estimate: str,
    limits: PlanLimits,
    proposal: PartitionProposal | None = None,
) -> FetchPlan:
    """The plan for this request, in the mode asked for where that is possible.

    Total apart from a mode this does not know: every other failure produces a plan with a
    note rather than an exception, because a caller who asked for a partitioned run and got
    a flat one needs the rows and the reason, not a traceback.
    """
    if mode not in MODES:
        raise ConfigError(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")

    notes: list[Note] = []

    from blue_data_llm_source.planning.cardinality import Cardinality

    if estimate == Cardinality.UNKNOWN:
        notes.append(
            Note(
                code=Gap.ESTIMATE_UNKNOWN,
                target_id=ROOT,
                detail="no usable row estimate; depth and quota sized as if hundreds",
            )
        )

    if mode == MODE_FLAT:
        return _flat(ask, _filters(constraints, notes=notes), notes)

    if mode == MODE_ROW_BY_ROW:
        return _row_by_row(ask, constraints, proposal, limits, notes)

    return _partitioned(ask, constraints, schema, estimate, limits, proposal, notes)


def _row_by_row(
    ask: str,
    constraints: dict[str, JsonScalar | list[JsonScalar]],
    proposal: PartitionProposal | None,
    limits: PlanLimits,
    notes: list[Note],
) -> FetchPlan:
    """One leaf per named entity.

    Entities come from a list-valued constraint when the caller supplied one, and otherwise
    from an enumeration call, which is charged to the root node so it stays inside the
    worst-case formula rather than escaping it as an unassigned extra.
    """
    found = _entity_list(constraints)
    if found is not None:
        key, values, source = found[0], found[1], VALUES_FROM_CONSTRAINTS
    elif proposal is not None and proposal.values:
        key, values, source = proposal.key, list(proposal.values), proposal.values_from
    else:
        notes.append(
            Note(
                code=Gap.GUARD_COLLAPSED,
                target_id=ROOT,
                detail="row_by_row found no entity list, so the request runs as one ask",
            )
        )
        return _flat(ask, _filters(constraints, notes=notes), notes)

    values = _dedup(values)
    scalar = _filters(constraints, split_on=key, notes=notes)

    node = Node(
        id=ROOT,
        parent_id=None,
        query=ask,
        constraints=dict(scalar),
        key=key,
        key_in_schema=False,
        values=list(values),
        values_from=source,
    )

    # Refuse rather than truncate. Every entity here was named, by the caller or by an
    # enumeration answering the caller's ask, so dropping some silently returns a partial
    # answer to a request that was explicit about its scope.
    if len(values) > limits.max_leaves:
        kept = [
            Leaf(
                id=f"{ROOT}.{i}",
                parent_id=ROOT,
                query=ask,
                constraints={**scalar, key: Exact(value)},
            )
            for i, value in enumerate(values[: limits.max_leaves])
        ]
        notes.append(
            Note(
                code=Gap.PLAN_TRUNCATED,
                target_id=ROOT,
                detail=(
                    f"{len(values)} entities exceed max_leaves={limits.max_leaves}; "
                    f"refused rather than dropping {len(values) - limits.max_leaves} of them"
                ),
            )
        )
        return FetchPlan(mode=MODE_ROW_BY_ROW, leaves=kept, nodes=[node], notes=notes, refused=True)

    leaves = [
        Leaf(
            id=f"{ROOT}.{i}",
            parent_id=ROOT,
            query=ask,
            constraints={**scalar, key: Exact(value)},
        )
        for i, value in enumerate(values)
    ]
    return FetchPlan(mode=MODE_ROW_BY_ROW, leaves=leaves, nodes=[node], notes=notes)


def _partitioned(
    ask: str,
    constraints: dict[str, JsonScalar | list[JsonScalar]],
    schema: Schema,
    estimate: str,
    limits: PlanLimits,
    proposal: PartitionProposal | None,
    notes: list[Note],
) -> FetchPlan:
    # Before the guard, not after. The one-value guard exists because splitting on a single
    # value buys no coverage, and a list naming the same value twice is that case wearing a
    # count of two.
    if proposal is not None:
        proposal = replace(proposal, values=[str(value) for value in _dedup(proposal.values)])
    reason = _guard_failure(proposal, estimate, limits)
    if reason is not None or proposal is None:
        if proposal is not None and proposal.from_caller:
            notes.append(
                Note(
                    code=Gap.PROPOSAL_REJECTED,
                    target_id=ROOT,
                    detail=f"the proposed key was not used: {reason}",
                )
            )
        notes.append(Note(code=Gap.GUARD_COLLAPSED, target_id=ROOT, detail=reason or ""))
        return _flat(ask, _filters(constraints, notes=notes), notes)

    scalar = _filters(constraints, split_on=proposal.key, notes=notes)
    values = _dedup(proposal.values)
    # Group rather than truncate. Each value past the cap shares a leaf with its neighbors
    # instead of losing one, so every value the enumeration named is encoded in exactly
    # one leaf and only the granularity is lost.
    if len(values) > limits.max_leaves:
        notes.append(
            Note(
                code=Gap.PLAN_TRUNCATED,
                target_id=ROOT,
                detail=(
                    f"{len(values)} partitions exceed max_leaves={limits.max_leaves}; "
                    f"expansion stopped and the values run grouped, several to a leaf"
                ),
            )
        )
        groups = _buckets(values, limits.max_leaves)
    else:
        groups = [[value] for value in values]

    node = Node(
        id=ROOT,
        parent_id=None,
        query=ask,
        constraints=dict(scalar),
        key=proposal.key,
        key_in_schema=any(a.name == proposal.key for a in schema.attrs),
        values=list(values),
        values_from=proposal.values_from,
    )
    leaves = [
        Leaf(
            id=f"{ROOT}.{i}",
            parent_id=ROOT,
            query=ask,
            constraints={**scalar, proposal.key: _covers(group)},
        )
        for i, group in enumerate(groups)
    ]
    return FetchPlan(mode=MODE_PARTITIONED, leaves=leaves, nodes=[node], notes=notes)


def root_plan(
    *,
    mode: str,
    original_query: str,
    retrieval_query: str,
    constraints: Mapping[str, Constraint],
    estimate: int | None,
    cardinality: CardinalityEstimate | None = None,
) -> FetchPlan:
    """Create the protected root slice used by the planning coordinator."""
    if mode not in MODES:
        raise ConfigError(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")
    return FetchPlan(
        mode=mode,
        leaves=[
            Leaf(
                id=ROOT,
                parent_id=None,
                query=retrieval_query,
                constraints=dict(constraints),
                depth=0,
                original_query=original_query,
                estimate=estimate,
                cardinality=cardinality
                or estimate_from_reported(
                    estimate,
                    row_unit="records matching the accepted request and schema",
                    source="planner",
                ),
            )
        ],
    )


def _ancestor_keys(plan: FetchPlan, leaf: Leaf) -> set[str]:
    by_id = {node.id: node for node in plan.nodes}
    keys: set[str] = set()
    parent_id = leaf.parent_id
    while parent_id is not None:
        parent = by_id.get(parent_id)
        if parent is None:
            break
        keys.add(parent.key)
        parent_id = parent.parent_id
    return keys


def _declared_type(schema: Schema, name: str) -> str | None:
    attr = resolve_attr(name, schema.attrs)
    return attr.type if attr is not None else None


def partition_keys(plan: FetchPlan, leaf: Leaf) -> tuple[str, ...]:
    """The constraint keys partitioning introduced on *leaf*, in its constraint order.

    A key is introduced when the leaf carries it and the root does not: the root holds what
    the analysis step and the caller set for the whole request, so everything it carries is
    request scope. A root key that a split narrowed stays request scope too; the narrower
    value still reaches the leaf, and the only claim withheld is that every record carries
    that field. A plan with no partition node introduces nothing.
    """
    root = next((node for node in plan.nodes if node.parent_id is None), None)
    if root is None or leaf.parent_id is None:
        return ()
    return tuple(name for name in leaf.constraints if name not in root.constraints)


def range_buckets(values: Sequence[int], count: int) -> list[Range]:
    """Integers grouped into at most *count* contiguous, pairwise disjoint spans.

    The numeric counterpart of :func:`_buckets`, and the other source a ``Range`` may come
    from. Distinct values are sorted first, so each bucket is a run of neighbors and the
    spans cannot overlap; a span covers the integers between its ends, including ones no
    value named, which is what makes it a slice rather than a list.
    """
    ordered: list[JsonScalar] = sorted({value for value in values if not isinstance(value, bool)})
    return [
        Range(cast("int", group[0]), cast("int", group[-1])) for group in _buckets(ordered, count)
    ]


def _canonical_base(plan: FetchPlan, leaf: Leaf) -> tuple[str, Mapping[str, Constraint]]:
    """The request every child's wording is derived from, and the filters it already states.

    The root's, not the parent's. Deriving from the parent's wording would restate each
    ancestor's restriction once per level, and the root request is the one the analysis step
    made canonical.
    """
    if leaf.parent_id is None:
        return leaf.query, leaf.constraints
    root = next((node for node in plan.nodes if node.parent_id is None), None)
    if root is None:
        return leaf.query, leaf.constraints
    return root.query, root.constraints


def _child_query(
    plan: FetchPlan,
    leaf: Leaf,
    constraints: Mapping[str, Constraint],
    policy: PartitionPolicy,
    schema: Schema,
) -> str:
    if policy.child_wording == CHILD_WORDING_PARENT:
        return leaf.query
    request, stated = _canonical_base(plan, leaf)
    # Column filters only, by the rule the leaf prompt uses: introduced by partitioning, not
    # carried by the root, and naming one schema column. Everything else stays in the
    # structured filters, where the leaf prompt presents it as request scope.
    introduced = [name for name in constraints if name not in stated]
    columns, _ = split_filters(constraints, schema.attrs, introduced)
    return canonical_child_wording(request, columns)


def _proposal_children(
    plan: FetchPlan,
    leaf: Leaf,
    proposal: PartitionProposal | None,
    schema: Schema,
    limits: PlanLimits,
    policy: PartitionPolicy | None = None,
) -> tuple[list[Leaf], Node | None, str | None]:
    """Validate one proposal and return its complete replacement, if usable."""
    policy = policy or PartitionPolicy()
    if proposal is None:
        return [], None, "no partition proposal was returned"
    key = proposal.key.strip()
    if not key:
        return [], None, "the partition key was empty"
    if key in _ancestor_keys(plan, leaf):
        return [], None, f"partition key {key!r} repeats an ancestor split"
    if leaf.depth >= limits.max_depth:
        return [], None, f"max_depth={limits.max_depth} keeps this slice terminal"
    if proposal.ambiguity > limits.ambiguity_threshold:
        return [], None, f"partition key {key!r} was too ambiguous"

    paired: list[tuple[JsonScalar, int | None]] = []
    pair_index: dict[object, int] = {}
    for original_index, raw_value in enumerate(proposal.values):
        value = raw_value.strip() if isinstance(raw_value, str) else raw_value
        if isinstance(value, str) and not value:
            continue
        identity = value_identity(value)
        estimate = (
            proposal.child_estimates[original_index]
            if original_index < len(proposal.child_estimates)
            else None
        )
        existing = pair_index.get(identity)
        if existing is None:
            pair_index[identity] = len(paired)
            paired.append((value, estimate))
        elif paired[existing][1] != estimate:
            paired[existing] = (value, None)

    if len(paired) > limits.max_partitions:
        return [], None, f"proposal has {len(paired)} values above max_partitions"
    if len(paired) < 2:
        return [], None, "proposal did not produce two distinct values"
    if (
        policy.partition_guardrails
        and proposal.values_from == VALUES_FROM_ENUMERATION
        and is_numeric(_declared_type(schema, key))
        and not small_ordinal([value for value, _ in paired])
    ):
        return (
            [],
            None,
            (
                f"partition key {key!r} is declared {_declared_type(schema, key)!r} and its values "
                f"are not integers or integer spans covering at most {SMALL_ORDINAL_SPAN}, so it "
                "reads as a continuous measurement"
            ),
        )

    inherited = leaf.constraints.get(key)
    if isinstance(inherited, Exact):
        return [], None, f"exact constraint on {key!r} cannot be subdivided"
    if isinstance(inherited, Range):
        return [], None, f"range constraint on {key!r} cannot be subdivided"
    if isinstance(inherited, OneOf):
        known = _dedup(list(inherited.values))
        known_set = {value_identity(value) for value in known}
        admitted = [
            (value, estimate) for value, estimate in paired if value_identity(value) in known_set
        ]
        if (
            len(admitted) != len(known)
            or {value_identity(value) for value, _ in admitted} != known_set
        ):
            return [], None, f"proposal does not cover every known value for {key!r}"
        paired = admitted
        if len(paired) < 2:
            return [], None, "known-value intersection has fewer than two children"

    if len(plan.leaves) - 1 + len(paired) > limits.max_leaves:
        return [], None, f"complete expansion would exceed max_leaves={limits.max_leaves}"

    # Spans only where a model wrote the values and nothing above constrains the key. A
    # caller-declared domain is matched value for value by the safety check, and an inherited
    # filter on the key has already been handled by the checks above.
    typed: list[Constraint] | None = None
    if (
        policy.range_filters
        and inherited is None
        and proposal.values_from == VALUES_FROM_ENUMERATION
        and is_ordinal(_declared_type(schema, key))
    ):
        typed, range_failure = typed_children([value for value, _ in paired])
        if range_failure is not None:
            return [], None, range_failure

    children: list[Leaf] = []
    for index, (value, child_estimate) in enumerate(paired):
        split = typed[index] if typed is not None else Exact(value)
        constraints: dict[str, Constraint] = {**leaf.constraints, key: split}
        children.append(
            Leaf(
                id=f"{leaf.id}.{index}",
                parent_id=leaf.id,
                query=_child_query(plan, leaf, constraints, policy, schema),
                constraints=constraints,
                depth=leaf.depth + 1,
                original_query=leaf.original_query,
                estimate=child_estimate,
                cardinality=estimate_from_reported(
                    child_estimate,
                    row_unit="records in this leaf",
                    source="partition_proposal",
                ),
                partition_safety_status=proposal.partition_safety_status,
                partition_rule_digest=proposal.partition_rule_digest,
            )
        )
    node = Node(
        id=leaf.id,
        parent_id=leaf.parent_id,
        query=leaf.query,
        constraints=dict(leaf.constraints),
        key=key,
        key_in_schema=any(attr.name == key for attr in schema.attrs),
        values=[value for value, _ in paired],
        values_from=proposal.values_from,
        depth=leaf.depth,
        original_query=leaf.original_query,
        estimate=leaf.estimate,
        partition_safety_status=proposal.partition_safety_status,
        partition_rule_digest=proposal.partition_rule_digest,
    )
    return children, node, None


def apply_partition_proposal(
    plan: FetchPlan,
    *,
    leaf_id: str,
    proposal: PartitionProposal | None,
    schema: Schema,
    limits: PlanLimits,
    applied_decision: AppliedPartitionDecision | None = None,
    schema_contract: PlanSchemaContract | None = None,
    proposal_failure: str | None = None,
    policy: PartitionPolicy | None = None,
) -> FetchPlan:
    """Apply one complete proposal while preserving the parent on rejection."""
    leaf = next((candidate for candidate in plan.leaves if candidate.id == leaf_id), None)
    if leaf is None:
        raise ConfigError(f"unknown frontier leaf {leaf_id!r}")
    children, node, reason = _proposal_children(plan, leaf, proposal, schema, limits, policy)
    if proposal is None and proposal_failure is not None:
        reason = proposal_failure
    if plan.attempted_proposals >= proposal_attempt_limit(limits):
        return replace(
            plan,
            notes=[
                *plan.notes,
                Note(
                    code=Gap.GUARD_COLLAPSED,
                    target_id=leaf.id,
                    detail="the derived proposal-attempt limit was reached",
                ),
            ],
        )
    attempts = plan.attempted_proposals + 1
    if reason is not None or node is None:
        return replace(
            plan,
            notes=[
                *plan.notes,
                Note(code=Gap.GUARD_COLLAPSED, target_id=leaf.id, detail=reason or ""),
            ],
            attempted_proposals=attempts,
        )

    at = plan.leaves.index(leaf)
    leaves = [*plan.leaves[:at], *children, *plan.leaves[at + 1 :]]
    return replace(
        plan,
        leaves=leaves,
        nodes=[*plan.nodes, node],
        attempted_proposals=attempts,
        accepted_internal_nodes=plan.accepted_internal_nodes + 1,
        applied_partition_decisions=(
            (*plan.applied_partition_decisions, applied_decision)
            if applied_decision is not None
            else plan.applied_partition_decisions
        ),
        schema_contract=schema_contract or plan.schema_contract,
    )


def _eligible(leaf: Leaf, limits: PlanLimits) -> bool:
    if leaf.depth >= limits.max_depth:
        return False
    if limits.max_partitions < 2 or limits.max_leaves == 1:
        return False
    return leaf.depth == 0 or leaf.estimate is None or leaf.estimate > limits.target_rows_per_leaf


def expand_partitioned_plan(
    *,
    original_query: str,
    retrieval_query: str,
    constraints: Mapping[str, Constraint],
    schema: Schema,
    estimate: int | None,
    limits: PlanLimits,
    proposals: Mapping[str, PartitionProposal | None],
) -> FetchPlan:
    """Expand a bounded tree breadth first from precomputed proposal answers."""
    plan = root_plan(
        mode=MODE_PARTITIONED,
        original_query=original_query,
        retrieval_query=retrieval_query,
        constraints=constraints,
        estimate=estimate,
    )
    frontier = [plan.leaves[0]]
    while frontier and plan.attempted_proposals < proposal_attempt_limit(limits):
        current = frontier.pop(0)
        live = next((leaf for leaf in plan.leaves if leaf.id == current.id), None)
        if live is None or not _eligible(live, limits):
            continue
        before = plan.accepted_internal_nodes
        plan = apply_partition_proposal(
            plan,
            leaf_id=live.id,
            proposal=proposals.get(live.id),
            schema=schema,
            limits=limits,
        )
        if plan.accepted_internal_nodes > before:
            frontier.extend(leaf for leaf in plan.leaves if leaf.parent_id == live.id)
    return plan


def build_entity_plan(
    *,
    original_query: str,
    retrieval_query: str,
    constraints: Mapping[str, Constraint],
    key: str,
    entities: Sequence[JsonScalar],
    limits: PlanLimits,
) -> FetchPlan:
    """Build one leaf per entity, or preserve the batch root when it cannot fit."""
    root = root_plan(
        mode=MODE_ROW_BY_ROW,
        original_query=original_query,
        retrieval_query=retrieval_query,
        constraints=constraints,
        estimate=None,
    )
    values = _dedup([value.strip() if isinstance(value, str) else value for value in entities])
    if len(values) < 1 or len(values) > limits.max_leaves:
        reason = (
            "entity enumeration was empty"
            if not values
            else f"{len(values)} entities exceed max_leaves={limits.max_leaves}"
        )
        return replace(
            root,
            notes=[Note(code=Gap.GUARD_COLLAPSED, target_id=ROOT, detail=reason)],
        )
    inherited = constraints.get(key)
    if isinstance(inherited, Range):
        return replace(
            root,
            notes=[
                Note(
                    code=Gap.GUARD_COLLAPSED,
                    target_id=ROOT,
                    detail=f"range constraint on {key!r} cannot be subdivided",
                )
            ],
        )
    if isinstance(inherited, Exact):
        values = [inherited.value] if inherited.value in values else []
    elif isinstance(inherited, OneOf):
        known = _dedup(list(inherited.values))
        if values != known:
            return replace(
                root,
                notes=[
                    Note(
                        code=Gap.GUARD_COLLAPSED,
                        target_id=ROOT,
                        detail=f"entity enumeration does not cover every known value for {key!r}",
                    )
                ],
            )
        values = known
    if not values:
        return replace(
            root,
            notes=[Note(code=Gap.GUARD_COLLAPSED, target_id=ROOT, detail="no entity survived")],
        )
    node = Node(
        id=ROOT,
        parent_id=None,
        query=retrieval_query,
        constraints=dict(constraints),
        key=key,
        key_in_schema=False,
        values=values,
        values_from=VALUES_FROM_ENUMERATION,
        original_query=original_query,
    )
    leaves = [
        Leaf(
            id=f"{ROOT}.{index}",
            parent_id=ROOT,
            query=retrieval_query,
            constraints={**constraints, key: Exact(value)},
            depth=1,
            original_query=original_query,
        )
        for index, value in enumerate(values)
    ]
    return replace(root, leaves=leaves, nodes=[node], accepted_internal_nodes=1)


def build_key_tuple_plan(
    *,
    original_query: str,
    retrieval_query: str,
    constraints: Mapping[str, Constraint],
    primary_keys: tuple[str, ...],
    attrs: Sequence[Attr],
    entities: list[dict[str, Any]],
    limits: PlanLimits,
) -> FetchPlan:
    """Build one leaf per complete ordered key object."""
    root = root_plan(
        mode=MODE_ROW_BY_ROW,
        original_query=original_query,
        retrieval_query=retrieval_query,
        constraints=constraints,
        estimate=None,
    )
    ordered: list[dict[str, Any]] = []
    seen: set[KeyTuple] = set()
    for entity in entities:
        identity = key_identity(entity, primary_keys, attrs)
        if identity is None or identity in seen:
            continue
        admitted: dict[str, Any] = {}
        valid = True
        for key, value in identity.exclusion().items():
            inherited = constraints.get(key)
            if inherited is None:
                admitted[key] = value
                continue
            if isinstance(inherited, Range):
                # A planner-built span; an enumerated entity is not matched against one.
                valid = False
                break
            candidates = (inherited.value,) if isinstance(inherited, Exact) else inherited.values
            matched = next(
                (
                    candidate
                    for candidate in candidates
                    if key_identity({key: candidate}, (key,), attrs)
                    == key_identity({key: value}, (key,), attrs)
                ),
                None,
            )
            if matched is None:
                valid = False
                break
            admitted[key] = matched
        if not valid:
            continue
        seen.add(identity)
        ordered.append(admitted)
    if not ordered or len(ordered) > limits.max_leaves:
        reason = (
            "entity enumeration was empty"
            if not ordered
            else f"{len(ordered)} entities exceed max_leaves={limits.max_leaves}"
        )
        return replace(
            root,
            notes=[Note(code=Gap.GUARD_COLLAPSED, target_id=ROOT, detail=reason)],
        )
    leaves = [
        Leaf(
            id=f"{ROOT}.{index}",
            parent_id=ROOT,
            query=retrieval_query,
            constraints={
                **constraints,
                **{key: Exact(entity[key]) for key in primary_keys},
            },
            depth=1,
            original_query=original_query,
        )
        for index, entity in enumerate(ordered)
    ]
    node = Node(
        id=ROOT,
        parent_id=None,
        query=retrieval_query,
        constraints=dict(constraints),
        key=" + ".join(primary_keys),
        key_in_schema=True,
        values=[str(entity) for entity in ordered],
        values_from=VALUES_FROM_ENUMERATION,
        original_query=original_query,
    )
    return replace(root, leaves=leaves, nodes=[node], accepted_internal_nodes=1)
