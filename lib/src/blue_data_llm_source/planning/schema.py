"""Output columns and record identity.

Enrichment may add columns but cannot change or remove a caller-declared
attribute or type. An inferred key must name a returned attribute; otherwise
there is no key. Choosing the first column as a fallback could collapse distinct
records during cross-leaf deduplication.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, cast

from blue_data_llm_source.planning.partition_safety import IdentityPartitionRule
from blue_data_llm_source.reduction.parsing import strip_fence
from blue_data_llm_source.types import Attr

KEY_FROM_CALLER: Final = "caller"
KEY_FROM_INFERRED: Final = "inferred"
KEY_FROM_NONE: Final = "none"

SCHEMA_PRESERVE: Final = "preserve"
SCHEMA_ENRICH: Final = "enrich"
SCHEMA_MODES: Final = (SCHEMA_PRESERVE, SCHEMA_ENRICH)

_SUPPORTED_TYPES: Final = {
    "string": "string",
    "str": "string",
    "text": "string",
    "integer": "integer",
    "int": "integer",
    "number": "number",
    "float": "number",
    "boolean": "boolean",
    "bool": "boolean",
    "date": "date",
}


def _supported_type(value: object) -> str | None:
    return _SUPPORTED_TYPES.get(value.strip().lower()) if isinstance(value, str) else None


def _schema_keys(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ValueError("schema primary_keys must be a sequence of strings")
    items = cast("tuple[Any, ...] | list[Any]", value)
    keys: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("schema primary_keys must contain non-empty strings")
        keys.append(item.strip())
    return tuple(keys)


def _schema_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("schema key must be a non-empty string or null")
    return value.strip()


@dataclass(frozen=True)
class Schema:
    """What step 2 established. Internal to planning."""

    attrs: Sequence[Attr] = field(default_factory=tuple[Attr, ...])
    primary_keys: tuple[str, ...] = ()
    """Ordered fields that jointly identify one record."""

    key: str | None = None
    """One-cycle compatibility alias for a single primary key."""

    key_from: str = KEY_FROM_NONE
    """``caller``, ``inferred``, or ``none``. ``none`` means deduplication must not run."""

    def __post_init__(self) -> None:
        attrs = tuple(self.attrs)
        keys = _schema_keys(self.primary_keys)
        if self.key is not None:
            alias = _schema_key(self.key)
            if keys and keys != (alias,):
                raise ValueError("schema key and primary_keys disagree")
            keys = (alias,)
        if len(set(keys)) != len(keys):
            raise ValueError("schema primary_keys must be distinct non-empty strings")
        names = {attr.name for attr in attrs}
        if any(key not in names for key in keys):
            raise ValueError("schema primary_keys must name requested attributes")
        object.__setattr__(self, "attrs", attrs)
        object.__setattr__(self, "primary_keys", keys)
        object.__setattr__(self, "key", keys[0] if len(keys) == 1 else None)


@dataclass(frozen=True)
class SchemaDefinition:
    """Immutable planning schema with ordered identity fields."""

    attrs: Sequence[Attr] = field(default_factory=tuple[Attr, ...])
    primary_keys: tuple[str, ...] = ()
    key_from: str = KEY_FROM_NONE
    identity_partitions: Sequence[IdentityPartitionRule] = field(
        default_factory=tuple[IdentityPartitionRule, ...]
    )

    def __post_init__(self) -> None:
        projected = Schema(
            attrs=self.attrs,
            primary_keys=self.primary_keys,
            key_from=self.key_from,
        )
        object.__setattr__(self, "attrs", tuple(projected.attrs))
        object.__setattr__(self, "primary_keys", projected.primary_keys)
        object.__setattr__(self, "key_from", projected.key_from)
        object.__setattr__(self, "identity_partitions", tuple(self.identity_partitions))

    @property
    def key(self) -> str | None:
        return self.primary_keys[0] if len(self.primary_keys) == 1 else None

    def to_schema(self) -> Schema:
        return Schema(
            attrs=self.attrs,
            primary_keys=self.primary_keys,
            key_from=self.key_from,
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "attrs": [{"name": attr.name, "type": attr.type} for attr in self.attrs],
            "primary_keys": list(self.primary_keys),
            "key_from": self.key_from,
        }
        if self.identity_partitions:
            payload["identity_partitions"] = [rule.to_dict() for rule in self.identity_partitions]
        return payload

    @classmethod
    def from_schema(cls, schema: Schema) -> SchemaDefinition:
        return cls(
            attrs=schema.attrs,
            primary_keys=schema.primary_keys,
            key_from=schema.key_from,
        )


def _object_from(text: str | None) -> dict[str, Any] | None:
    if not text or not text.strip():
        return None
    try:
        loaded: object = json.loads(strip_fence(text))
    except (ValueError, RecursionError):
        return None
    return cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else None


def _attrs_from(reported: object) -> list[Attr]:
    """The attributes a response carries, keeping only well-formed entries.

    An unrecognized type becomes ``None``. The verifier reports an unchecked
    declared type separately, so it cannot be mistaken for a checked record type.
    """
    if not isinstance(reported, list):
        return []
    out: list[Attr] = []
    seen: set[str] = set()
    for entry in cast("list[Any]", reported):
        if not isinstance(entry, dict):
            continue
        raw_name = cast("dict[str, Any]", entry).get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        name = raw_name.strip()
        # A duplicated column would produce two prompt lines for one field and an
        # ambiguous key target. First occurrence wins.
        if name in seen:
            continue
        seen.add(name)
        raw_type = cast("dict[str, Any]", entry).get("type")
        declared = _supported_type(raw_type)
        out.append(Attr(name=name, type=declared))
    return out


def _merge(declared: list[Attr], inferred: list[Attr]) -> list[Attr]:
    """Declared attributes first and unchanged, then inferred ones the caller omitted."""
    out = list(declared)
    taken = {a.name for a in declared}
    out.extend(a for a in inferred if a.name not in taken)
    return out


def schema_from_response(text: str | None, declared: list[Attr]) -> Schema:
    """Read step 2's response and reconcile it with what the caller declared.

    Total: an unreadable response yields the caller's own attributes with no key, which
    is a schema planning can proceed on rather than a failure it must handle.
    """
    payload = _object_from(text)
    inferred = _attrs_from(payload.get("attrs")) if payload is not None else []
    attrs = _merge(declared, inferred)

    reported_keys = payload.get("primary_keys") if payload is not None else None
    reported_key = payload.get("key") if payload is not None else None
    candidates: tuple[str, ...] = ()
    if reported_keys is not None:
        if not isinstance(reported_keys, list) or not all(
            isinstance(item, str) and item.strip() for item in cast("list[Any]", reported_keys)
        ):
            return Schema(attrs=attrs, key=None, key_from=KEY_FROM_NONE)
        candidates = tuple(item.strip() for item in cast("list[str]", reported_keys))
        if reported_key is not None and (
            not isinstance(reported_key, str)
            or not reported_key.strip()
            or candidates != (reported_key.strip(),)
        ):
            return Schema(attrs=attrs, key=None, key_from=KEY_FROM_NONE)
    elif isinstance(reported_key, str) and reported_key.strip():
        candidates = (reported_key.strip(),)
    if (
        candidates
        and len(set(candidates)) == len(candidates)
        and all(any(a.name == candidate for a in attrs) for candidate in candidates)
    ):
        source = (
            KEY_FROM_CALLER
            if all(any(a.name == candidate for a in declared) for candidate in candidates)
            else KEY_FROM_INFERRED
        )
        return Schema(attrs=attrs, primary_keys=candidates, key_from=source)

    return Schema(attrs=attrs, key=None, key_from=KEY_FROM_NONE)


def schema_from_bootstrap(
    reported: object,
    declared: list[Attr],
    *,
    declared_key: str | None = None,
    declared_keys: tuple[str, ...] = (),
    mode: str = SCHEMA_PRESERVE,
) -> Schema | None:
    """Strictly reconcile the schema member of a combined bootstrap response."""
    if mode not in SCHEMA_MODES or not isinstance(reported, dict):
        return None
    data = cast("dict[str, Any]", reported)
    raw_attrs = data.get("attrs")
    if not isinstance(raw_attrs, list):
        return None

    declared_names = {attr.name for attr in declared}
    inferred: list[Attr] = []
    names: set[str] = set()
    for raw in cast("list[Any]", raw_attrs):
        if not isinstance(raw, dict):
            return None
        item = cast("dict[str, Any]", raw)
        if not {"name"} <= set(item) <= {"name", "type"}:
            return None
        raw_name = item.get("name")
        raw_type = item.get("type")
        if not isinstance(raw_name, str) or not raw_name.strip():
            return None
        name = raw_name.strip()
        if name in names:
            return None
        inferred_type = _supported_type(raw_type)
        # The caller's declared type wins over whatever the model echoes for that name, so
        # only an inferred attribute has to carry a type the planner supports.
        if raw_type is not None and inferred_type is None and name not in declared_names:
            return None
        names.add(name)
        inferred.append(Attr(name=name, type=inferred_type))

    if declared_key is not None and declared_keys and declared_keys != (declared_key,):
        return None
    keys = declared_keys or ((declared_key,) if declared_key is not None else ())
    if any(key not in declared_names for key in keys):
        return None

    attrs = list(declared) if declared and mode == SCHEMA_PRESERVE else _merge(declared, inferred)
    attr_names = {attr.name for attr in attrs}
    if keys:
        return Schema(attrs=attrs, primary_keys=keys, key_from=KEY_FROM_CALLER)

    raw_keys = data.get("primary_keys")
    raw_key = data.get("key")
    if raw_keys is not None:
        if not isinstance(raw_keys, list) or not all(
            isinstance(item, str) and item.strip() for item in cast("list[Any]", raw_keys)
        ):
            return None
        inferred_keys = tuple(item.strip() for item in cast("list[str]", raw_keys))
        if raw_key is not None and inferred_keys != (raw_key,):
            return None
        if (
            not inferred_keys
            or len(set(inferred_keys)) != len(inferred_keys)
            or any(key not in attr_names for key in inferred_keys)
        ):
            return None
        return Schema(attrs=attrs, primary_keys=inferred_keys, key_from=KEY_FROM_INFERRED)
    if raw_key is None:
        return Schema(attrs=attrs, key=None, key_from=KEY_FROM_NONE)
    if not isinstance(raw_key, str) or not raw_key.strip():
        return None
    key = raw_key.strip()
    if key not in attr_names:
        return None
    return Schema(attrs=attrs, key=key, key_from=KEY_FROM_INFERRED)


def deduplication_runs(schema: Schema) -> bool:
    """Whether cross-leaf deduplication may run against this schema.

    Stated as one function because it is one rule, and because the alternative — each
    caller re-deriving "is there a key" from two fields — is how the positional fallback
    gets reintroduced by someone being helpful.
    """
    return bool(schema.primary_keys) and schema.key_from != KEY_FROM_NONE


def schema_definition_from_payload(
    payload: object,
    declared: Schema,
    *,
    mode: str,
) -> SchemaDefinition | None:
    """Parse one independent schema object with caller-preserving reconciliation."""
    parsed = schema_from_bootstrap(
        payload,
        list(declared.attrs),
        declared_key=declared.key,
        declared_keys=declared.primary_keys,
        mode=mode,
    )
    return SchemaDefinition.from_schema(parsed) if parsed is not None else None


def schema_definition_from_response(
    text: str | None,
    declared: Schema,
    *,
    mode: str,
) -> SchemaDefinition | None:
    """Read one independent schema response without provider-side fallback."""
    return schema_definition_from_payload(_object_from(text), declared, mode=mode)


class SchemaGenerator:
    """Prompt-independent parser for a caller-preserving schema stage."""

    @staticmethod
    def parse(
        text: str | None,
        declared: Schema,
        *,
        mode: str,
    ) -> SchemaDefinition | None:
        return schema_definition_from_response(text, declared, mode=mode)
