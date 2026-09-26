"""Primitive task and result messages for the local executor boundary."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, cast

from blue_data_llm_source.planning.cardinality import (
    CardinalityEstimate,
    estimate_from_dict,
    estimate_from_reported,
)
from blue_data_llm_source.planning.plan import Leaf
from blue_data_llm_source.planning.ranges import is_ordinal
from blue_data_llm_source.planning.schema import (
    KEY_FROM_CALLER,
    KEY_FROM_INFERRED,
    KEY_FROM_NONE,
    Schema,
)
from blue_data_llm_source.prompts import (
    COMPLETE_LEAF_FETCH_PROMPT_VERSION,
    KEYED_LEAF_FETCH_PROMPT_VERSION,
    LEGACY_LEAF_FETCH_PROMPT_VERSION,
)
from blue_data_llm_source.reduction.identity import clean_key_names
from blue_data_llm_source.types import (
    Attr,
    ConfigError,
    Constraint,
    Exact,
    JsonScalar,
    OneOf,
    Range,
    Usage,
    json_scalar,
    resolve_attr,
)

WIRE_VERSION: Final = 2
SAFETY_WIRE_VERSION: Final = 3
REASONING_WIRE_VERSION: Final = 4
LEGACY_WIRE_VERSION: Final = 1
COMPLETE_WIRE_VERSION: Final = 5
"""The wire version of a task built for the ``keyed-leaf-fetch-2`` contract.

Its own number because each wire version admits exactly one prompt contract, and a receiver
that accepted either on one number would answer a task with a prompt its sender did not plan
against. It carries the partition-safety trace fields always (null when untraced) and
``reasoning_effort`` optionally, so it does not need a variant for each of those."""
HELD_WIRE_VERSION: Final = 6
"""The wire version of a ``keyed-leaf-fetch-1`` task seeded with keys an earlier call holds.

Under ``plan.split_policy="evidence"`` the children of a probed root are told which keys the
probe already returned. That list is a field no older version has, so a task carrying one
travels on its own number and an older receiver refuses it rather than dropping the list.
Like wire version 5 it carries the partition-safety trace always and ``reasoning_effort``
optionally. A ``keyed-leaf-fetch-2`` task carries the same fields on wire version 5."""
PROMPT_VERSION: Final = KEYED_LEAF_FETCH_PROMPT_VERSION
_PROMPT_FOR_WIRE: Final = {
    LEGACY_WIRE_VERSION: LEGACY_LEAF_FETCH_PROMPT_VERSION,
    WIRE_VERSION: KEYED_LEAF_FETCH_PROMPT_VERSION,
    SAFETY_WIRE_VERSION: KEYED_LEAF_FETCH_PROMPT_VERSION,
    REASONING_WIRE_VERSION: KEYED_LEAF_FETCH_PROMPT_VERSION,
    COMPLETE_WIRE_VERSION: COMPLETE_LEAF_FETCH_PROMPT_VERSION,
    HELD_WIRE_VERSION: KEYED_LEAF_FETCH_PROMPT_VERSION,
}
_TRACED_WIRE_VERSIONS: Final = frozenset(
    {SAFETY_WIRE_VERSION, REASONING_WIRE_VERSION, COMPLETE_WIRE_VERSION, HELD_WIRE_VERSION}
)
"""Wire versions whose tasks carry the partition-safety trace fields."""
_HELD_WIRE_VERSIONS: Final = frozenset({COMPLETE_WIRE_VERSION, HELD_WIRE_VERSION})
"""Wire versions whose tasks carry ``held_exclusions`` and ``held_total``."""
EXECUTION_SETTING_KEYS: Final = frozenset(
    {
        "empty_leaf_retry",
        "max_continuations",
        "max_excluded_keys",
        "max_exclusion_bytes",
        "exact_rows",
        "max_output_tokens",
        "max_retries",
        "model",
        "reasoning_effort",
        "temperature",
        "expose_estimate",
    }
)


def _primitive(value: object) -> bool:
    if value is None or isinstance(value, (str, int, bool)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_primitive(item) for item in cast("list[Any]", value))
    if isinstance(value, dict):
        items = cast("dict[Any, Any]", value)
        return all(isinstance(key, str) and _primitive(item) for key, item in items.items())
    return False


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in cast("dict[str, Any]", value).items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in cast("list[Any]", value))
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        mapping = cast("Mapping[str, Any]", value)
        return {key: _thaw_json(item) for key, item in mapping.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in cast("tuple[Any, ...]", value)]
    return value


def _require_object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be an object")
    return cast("dict[str, Any]", value)


def _require_string(value: object, name: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise ConfigError(f"{name} must be a {'string' if empty else 'non-empty string'}")
    return value


def _require_key_scalar(value: object, name: str) -> JsonScalar:
    return json_scalar(value, name)


def _check_versions(data: Mapping[str, Any]) -> int:
    wire_version = data.get("wire_version")
    if type(wire_version) is not int or wire_version not in _PROMPT_FOR_WIRE:
        raise ConfigError(f"unsupported wire_version {data.get('wire_version')!r}")
    prompt_version = data.get("prompt_version")
    expected_prompt = _PROMPT_FOR_WIRE[wire_version]
    if type(prompt_version) is not str or prompt_version != expected_prompt:
        raise ConfigError(f"unsupported prompt_version {data.get('prompt_version')!r}")
    return wire_version


def _check_settings(settings: Mapping[str, Any]) -> None:
    unknown = sorted(set(settings) - EXECUTION_SETTING_KEYS)
    if unknown:
        raise ConfigError(f"unknown task settings: {', '.join(unknown)}")
    nonnegative_counts = {
        "max_continuations",
        "max_excluded_keys",
        "max_exclusion_bytes",
        "max_retries",
        "exact_rows",
    }
    for name in nonnegative_counts:
        if name not in settings:
            continue
        value = settings[name]
        if name == "exact_rows" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            suffix = " or null" if name == "exact_rows" else ""
            raise ConfigError(f"task setting {name} must be a non-negative integer{suffix}")
    if "max_output_tokens" in settings and settings["max_output_tokens"] is not None:
        max_output_tokens = settings["max_output_tokens"]
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or max_output_tokens <= 0
        ):
            raise ConfigError("task setting max_output_tokens must be a positive integer or null")
    if "empty_leaf_retry" in settings and not isinstance(settings["empty_leaf_retry"], bool):
        raise ConfigError("task setting empty_leaf_retry must be a boolean")
    if "expose_estimate" in settings and not isinstance(settings["expose_estimate"], bool):
        raise ConfigError("task setting expose_estimate must be a boolean")
    if (
        "model" in settings
        and settings["model"] is not None
        and (not isinstance(settings["model"], str) or not settings["model"].strip())
    ):
        raise ConfigError("task setting model must be a non-empty string or null")
    if "temperature" in settings and settings["temperature"] is not None:
        temperature = settings["temperature"]
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(float(temperature))
        ):
            raise ConfigError("task setting temperature must be a finite number or null")
    if "reasoning_effort" in settings and settings["reasoning_effort"] is not None:
        effort = settings["reasoning_effort"]
        if not isinstance(effort, str) or effort not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise ConfigError("task setting reasoning_effort is not supported")
        if effort != "none" and settings.get("temperature") is not None:
            raise ConfigError(
                "task setting temperature requires reasoning_effort to be 'none' or null"
            )


def _partition_keys_from(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in cast("list[Any]", value)
    ):
        raise ConfigError("partition_keys must be a list of strings")
    return tuple(cast("list[str]", value))


def _held_from(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError("held_exclusions must be a list")
    return cast("list[Any]", value)


def _held_total_from(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("held_total must be an integer")
    return value


def _filter_to_dict(value: Constraint) -> dict[str, object]:
    if isinstance(value, Exact):
        return {"kind": "exact", "value": value.value}
    if isinstance(value, Range):
        return {"kind": "range", "lo": value.lo, "hi": value.hi}
    return {"kind": "one_of", "values": list(value.values)}


def _filter_from_dict(value: object, *, ranges: bool = False) -> Constraint:
    data = _require_object(value, "filter")
    kind = data.get("kind")
    if kind == "range" and ranges and set(data) == {"kind", "lo", "hi"}:
        lo, hi = data.get("lo"), data.get("hi")
        if any(isinstance(end, bool) or not isinstance(end, int) for end in (lo, hi)):
            raise ConfigError("range filter ends must be integers")
        return Range(cast("int", lo), cast("int", hi))
    if kind == "exact" and set(data) == {"kind", "value"}:
        return Exact(_require_key_scalar(data.get("value"), "exact filter value"))
    if kind == "one_of" and set(data) == {"kind", "values"}:
        values = data.get("values")
        if not isinstance(values, list):
            raise ConfigError("one_of filter values must be a list")
        raw_values = cast("list[Any]", values)
        return OneOf(tuple(_require_key_scalar(item, "one_of filter value") for item in raw_values))
    raise ConfigError(f"unknown or malformed filter kind {kind!r}")


def _schema_to_dict(schema: Schema) -> dict[str, object]:
    return {
        "attrs": [{"name": attr.name, "type": attr.type} for attr in schema.attrs],
        "primary_keys": list(schema.primary_keys),
        "key_from": schema.key_from,
    }


def _schema_from_dict(value: object, wire_version: int) -> Schema:
    data = _require_object(value, "schema")
    expected = (
        {"attrs", "key", "key_from"}
        if wire_version == LEGACY_WIRE_VERSION
        else {"attrs", "primary_keys", "key_from"}
    )
    if set(data) != expected:
        raise ConfigError(f"schema fields do not match wire version {wire_version}")
    raw_attrs = data.get("attrs")
    if not isinstance(raw_attrs, list):
        raise ConfigError("schema attrs must be a list")
    attrs: list[Attr] = []
    names: set[str] = set()
    for raw in cast("list[Any]", raw_attrs):
        item = _require_object(raw, "schema attr")
        if set(item) != {"name", "type"}:
            raise ConfigError(f"schema attr fields do not match wire version {wire_version}")
        name = _require_string(item.get("name"), "schema attr name")
        attr_type = item.get("type")
        if attr_type is not None and not isinstance(attr_type, str):
            raise ConfigError("schema attr type must be a string or null")
        if name in names:
            raise ConfigError(f"duplicate schema attribute {name!r}")
        names.add(name)
        attrs.append(Attr(name=name, type=attr_type))
    if wire_version == LEGACY_WIRE_VERSION:
        key = data.get("key")
        if key is not None and (not isinstance(key, str) or key not in names):
            raise ConfigError("schema key must name an attribute")
        keys = (key,) if isinstance(key, str) else ()
    else:
        raw_keys = data.get("primary_keys")
        if not isinstance(raw_keys, list) or not all(
            isinstance(item, str) and item in names for item in cast("list[Any]", raw_keys)
        ):
            raise ConfigError("schema primary_keys must be a list of attributes")
        keys = tuple(cast("list[str]", raw_keys))
        if len(set(keys)) != len(keys):
            raise ConfigError("schema primary_keys must be distinct")
    key_from = data.get("key_from")
    if key_from not in {KEY_FROM_CALLER, KEY_FROM_INFERRED, KEY_FROM_NONE}:
        raise ConfigError(f"unknown schema key source {key_from!r}")
    return Schema(attrs=attrs, primary_keys=keys, key_from=key_from)


def _usage_to_dict(usage: Usage) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": usage.model,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cost": usage.cost,
        "latency_seconds": usage.latency_seconds,
        "cache_hit": usage.cache_hit,
        "finish_reason": usage.finish_reason,
        "cost_known": usage.cost_known,
    }
    _usage_from_dict(payload)
    return payload


def _usage_from_dict(value: object) -> Usage:
    data = _require_object(value, "usage")
    expected = {
        "model",
        "prompt_tokens",
        "completion_tokens",
        "cost",
        "latency_seconds",
        "cache_hit",
        "finish_reason",
        "cost_known",
    }
    if set(data) != expected:
        raise ConfigError("usage fields do not match wire version 1")
    model = _require_string(data.get("model"), "usage model", empty=True)
    prompt_tokens = data.get("prompt_tokens")
    completion_tokens = data.get("completion_tokens")
    cost = data.get("cost")
    latency = data.get("latency_seconds")
    cache_hit = data.get("cache_hit")
    finish_reason = data.get("finish_reason")
    cost_known = data.get("cost_known")
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in (prompt_tokens, completion_tokens)
    ):
        raise ConfigError("usage token counts must be non-negative integers")
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(float(item))
        or item < 0
        for item in (cost, latency)
    ):
        raise ConfigError("usage cost and latency must be non-negative numbers")
    if not isinstance(cache_hit, bool) or not isinstance(cost_known, bool):
        raise ConfigError("usage flags must be booleans")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise ConfigError("usage finish_reason must be a string or null")
    return Usage(
        model=model,
        prompt_tokens=cast("int", prompt_tokens),
        completion_tokens=cast("int", completion_tokens),
        cost=float(cast("float", cost)),
        latency_seconds=float(cast("float", latency)),
        cache_hit=cache_hit,
        finish_reason=finish_reason,
        cost_known=cost_known,
    )


@dataclass(frozen=True)
class LeafTask:
    task_id: str
    parent_id: str | None
    original_query: str
    retrieval_query: str
    caller_context: str
    schema: Schema
    primary_keys: tuple[str, ...]
    estimate: CardinalityEstimate
    filters: Mapping[str, Constraint]
    settings: Mapping[str, Any]
    partition_safety_status: str | None = None
    partition_rule_digest: str | None = None
    wire_version: int = WIRE_VERSION
    prompt_version: str = PROMPT_VERSION
    partition_keys: tuple[str, ...] = ()
    """The filter names partitioning introduced, which the completeness contract may present
    as column filters. Carried on wire version 5 only; every other filter is request scope."""
    held_exclusions: tuple[Any, ...] = ()
    """Keys an earlier call of this request already returned, as the prompt's held list shows
    them, already bounded by the exclusion settings. Carried on wire versions 5 and 6."""
    held_total: int = 0
    """How many keys that earlier call returned, of which ``held_exclusions`` is the tail."""

    def __post_init__(self) -> None:
        keys = clean_key_names(self.primary_keys)
        if keys != self.schema.primary_keys:
            raise ConfigError("task primary_keys disagree with schema primary_keys")
        filters = MappingProxyType(dict(self.filters))
        ranged = [name for name, value in filters.items() if isinstance(value, Range)]
        if ranged and self.prompt_version != COMPLETE_LEAF_FETCH_PROMPT_VERSION:
            # Only the newest wire version admits the range kind. Sending one on an older
            # version would hand its receivers a filter kind their grammar does not have.
            raise ConfigError(
                "range filters travel only on wire version 5, the keyed-leaf-fetch-2 contract"
            )
        for name in ranged:
            # A range on any other column is a filter the obedience check cannot enforce and
            # a model reads as a span over text, so it is refused rather than carried.
            attr = resolve_attr(name, self.schema.attrs)
            if attr is None or not is_ordinal(attr.type):
                raise ConfigError(
                    f"range filter on {name!r} must name an attribute declared int or integer"
                )
        copied_settings = deepcopy(dict(self.settings))
        for name in ("reasoning_effort", "temperature"):
            if copied_settings.get(name) is None:
                copied_settings.pop(name, None)
        _check_settings(copied_settings)
        traced = self.partition_safety_status is not None or self.partition_rule_digest is not None
        if traced and (
            self.partition_safety_status != "accepted"
            or not isinstance(self.partition_rule_digest, str)
            or len(self.partition_rule_digest) != 64
            or any(char not in "0123456789abcdef" for char in self.partition_rule_digest)
        ):
            raise ConfigError("task partition safety trace is malformed")
        object.__setattr__(self, "filters", filters)
        split = tuple(self.partition_keys)
        if split and self.prompt_version != COMPLETE_LEAF_FETCH_PROMPT_VERSION:
            raise ConfigError("partition_keys travel only on wire version 5")
        # The isinstance check stays for callers that bypass the type checker.
        unnamed = any(
            not isinstance(name, str)  # pyright: ignore[reportUnnecessaryIsInstance]
            or name not in filters
            for name in split
        )
        if unnamed or len(set(split)) != len(split):
            raise ConfigError("partition_keys must be distinct names of the task's filters")
        object.__setattr__(self, "partition_keys", split)
        held = tuple(self.held_exclusions)
        if not _primitive(list(held)):
            raise ConfigError("held_exclusions must contain only primitive JSON values")
        total = self.held_total
        # Checked at runtime too: a caller may bypass the type checker.
        unreadable = not isinstance(total, int)  # pyright: ignore[reportUnnecessaryIsInstance]
        if isinstance(total, bool) or unreadable or total < len(held):
            raise ConfigError("held_total must be an integer counting at least held_exclusions")
        object.__setattr__(self, "held_exclusions", _freeze_json(deepcopy(list(held))))
        object.__setattr__(self, "primary_keys", keys)
        object.__setattr__(self, "settings", _freeze_json(copied_settings))
        if self.prompt_version == COMPLETE_LEAF_FETCH_PROMPT_VERSION:
            # Chosen by the contract, not derived from the payload: the payload variants the
            # older numbers distinguish are all expressible on this one.
            object.__setattr__(self, "wire_version", COMPLETE_WIRE_VERSION)
            return
        # Every other contract is normalized to the keyed one, as it was before there was a
        # choice: a legacy wire task is upgraded to the keyed prompt on receipt.
        wire_version = (
            HELD_WIRE_VERSION
            if total
            else REASONING_WIRE_VERSION
            if "reasoning_effort" in copied_settings
            else SAFETY_WIRE_VERSION
            if traced
            else WIRE_VERSION
        )
        object.__setattr__(self, "wire_version", wire_version)
        object.__setattr__(self, "prompt_version", PROMPT_VERSION)

    @classmethod
    def from_leaf(
        cls,
        leaf: Leaf,
        *,
        caller_context: str,
        schema: Schema,
        settings: Mapping[str, Any],
        prompt_version: str = PROMPT_VERSION,
        partition_keys: tuple[str, ...] = (),
        held_exclusions: tuple[Any, ...] = (),
        held_total: int = 0,
    ) -> LeafTask:
        return cls(
            task_id=leaf.id,
            parent_id=leaf.parent_id,
            original_query=leaf.original_query,
            retrieval_query=leaf.query,
            caller_context=caller_context,
            schema=schema,
            primary_keys=schema.primary_keys,
            estimate=leaf.cardinality
            or estimate_from_reported(
                leaf.estimate,
                row_unit="records in this leaf",
                source="planner",
            ),
            filters=dict(leaf.constraints),
            settings=dict(settings),
            partition_safety_status=leaf.partition_safety_status,
            partition_rule_digest=leaf.partition_rule_digest,
            prompt_version=prompt_version,
            partition_keys=partition_keys,
            held_exclusions=held_exclusions,
            held_total=held_total,
        )

    def to_dict(self) -> dict[str, object]:
        settings = cast("dict[str, Any]", _thaw_json(self.settings))
        _check_settings(settings)
        payload: dict[str, object] = {
            "wire_version": self.wire_version,
            "prompt_version": self.prompt_version,
            "task_id": self.task_id,
            "parent_id": self.parent_id,
            "original_query": self.original_query,
            "retrieval_query": self.retrieval_query,
            "caller_context": self.caller_context,
            "schema": _schema_to_dict(self.schema),
            "primary_keys": list(self.primary_keys),
            "estimate": self.estimate.to_dict(),
            "filters": {name: _filter_to_dict(value) for name, value in self.filters.items()},
            "settings": settings,
        }
        if self.wire_version == COMPLETE_WIRE_VERSION:
            payload["partition_keys"] = list(self.partition_keys)
        if self.wire_version in _HELD_WIRE_VERSIONS:
            payload["held_exclusions"] = _thaw_json(self.held_exclusions)
            payload["held_total"] = self.held_total
        if self.wire_version in _TRACED_WIRE_VERSIONS:
            payload["partition_safety_status"] = self.partition_safety_status
            payload["partition_rule_digest"] = self.partition_rule_digest
        return payload

    @classmethod
    def from_dict(cls, value: object) -> LeafTask:
        data = _require_object(value, "leaf task")
        wire_version = _check_versions(data)
        expected = {
            "wire_version",
            "prompt_version",
            "task_id",
            "parent_id",
            "original_query",
            "retrieval_query",
            "caller_context",
            "schema",
            "filters",
            "settings",
        }
        if wire_version != LEGACY_WIRE_VERSION:
            expected.update({"primary_keys", "estimate"})
        if wire_version in _TRACED_WIRE_VERSIONS:
            expected.update({"partition_safety_status", "partition_rule_digest"})
        if wire_version == COMPLETE_WIRE_VERSION:
            expected.add("partition_keys")
        if wire_version in _HELD_WIRE_VERSIONS:
            expected.update({"held_exclusions", "held_total"})
        if set(data) != expected:
            raise ConfigError(f"leaf task fields do not match wire version {wire_version}")
        parent_id = data.get("parent_id")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ConfigError("parent_id must be a string or null")
        filters = _require_object(data.get("filters"), "filters")
        settings = _require_object(data.get("settings"), "settings")
        _check_settings(settings)
        schema = _schema_from_dict(data.get("schema"), wire_version)
        if wire_version == LEGACY_WIRE_VERSION:
            keys = schema.primary_keys
            estimate = CardinalityEstimate(source="legacy_wire")
        else:
            raw_keys = data.get("primary_keys")
            if not isinstance(raw_keys, list) or not all(
                isinstance(item, str) for item in cast("list[Any]", raw_keys)
            ):
                raise ConfigError("primary_keys must be a list of strings")
            keys = tuple(cast("list[str]", raw_keys))
            if keys != schema.primary_keys:
                raise ConfigError("task primary_keys disagree with schema primary_keys")
            estimate = estimate_from_dict(data.get("estimate"))
        safety_status = data.get("partition_safety_status")
        rule_digest = data.get("partition_rule_digest")
        if wire_version == SAFETY_WIRE_VERSION and (
            not isinstance(safety_status, str) or not isinstance(rule_digest, str)
        ):
            raise ConfigError("task partition safety trace must contain strings")
        if wire_version == REASONING_WIRE_VERSION:
            if settings.get("reasoning_effort") is None:
                raise ConfigError("reasoning wire version requires reasoning_effort")
            if (safety_status is None) != (rule_digest is None):
                raise ConfigError("task partition safety trace must be complete or absent")
        elif wire_version in _HELD_WIRE_VERSIONS:
            if (safety_status is None) != (rule_digest is None):
                raise ConfigError("task partition safety trace must be complete or absent")
            if wire_version == HELD_WIRE_VERSION and data.get("held_total") == 0:
                # An empty seed belongs on the version the task would otherwise use.
                raise ConfigError("wire version 6 requires a non-empty held seed")
        elif "reasoning_effort" in settings:
            raise ConfigError("reasoning_effort requires reasoning wire version")
        return cls(
            task_id=_require_string(data.get("task_id"), "task_id"),
            parent_id=parent_id,
            original_query=_require_string(data.get("original_query"), "original_query"),
            retrieval_query=_require_string(data.get("retrieval_query"), "retrieval_query"),
            caller_context=_require_string(
                data.get("caller_context"), "caller_context", empty=True
            ),
            schema=schema,
            primary_keys=keys,
            estimate=estimate,
            filters={
                name: _filter_from_dict(item, ranges=wire_version == COMPLETE_WIRE_VERSION)
                for name, item in filters.items()
            },
            settings=settings,
            partition_safety_status=safety_status if isinstance(safety_status, str) else None,
            partition_rule_digest=rule_digest if isinstance(rule_digest, str) else None,
            prompt_version=_PROMPT_FOR_WIRE[wire_version],
            partition_keys=_partition_keys_from(data.get("partition_keys", [])),
            held_exclusions=tuple(_held_from(data.get("held_exclusions", []))),
            held_total=_held_total_from(data.get("held_total", 0)),
        )


@dataclass(frozen=True)
class ErrorData:
    kind: str
    message: str
    code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "message": self.message, "code": self.code}

    @classmethod
    def from_dict(cls, value: object) -> ErrorData:
        data = _require_object(value, "error")
        if set(data) != {"kind", "message", "code"}:
            raise ConfigError("error fields do not match wire version 1")
        code = data.get("code")
        if code is not None and not isinstance(code, str):
            raise ConfigError("error code must be a string or null")
        return cls(
            kind=_require_string(data.get("kind"), "error kind"),
            message=_require_string(data.get("message"), "error message", empty=True),
            code=code,
        )


@dataclass(frozen=True)
class LeafTaskResult:
    task_id: str
    records: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    usages: list[Usage] = field(default_factory=list[Usage])
    outcome: str = "ok"
    error: ErrorData | None = None
    wire_version: int = WIRE_VERSION
    prompt_version: str = PROMPT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", deepcopy(self.records))
        object.__setattr__(self, "usages", list(self.usages))

    def to_dict(self) -> dict[str, object]:
        if not _primitive(self.records):
            raise ConfigError("task records must contain only primitive JSON values")
        return {
            "wire_version": self.wire_version,
            "prompt_version": self.prompt_version,
            "task_id": self.task_id,
            "records": deepcopy(self.records),
            "usages": [_usage_to_dict(usage) for usage in self.usages],
            "outcome": self.outcome,
            "error": self.error.to_dict() if self.error is not None else None,
        }

    @classmethod
    def from_dict(cls, value: object) -> LeafTaskResult:
        data = _require_object(value, "leaf task result")
        wire_version = _check_versions(data)
        expected = {
            "wire_version",
            "prompt_version",
            "task_id",
            "records",
            "usages",
            "outcome",
            "error",
        }
        if set(data) != expected:
            raise ConfigError(f"leaf task result fields do not match wire version {wire_version}")
        records = data.get("records")
        usages = data.get("usages")
        if not isinstance(records, list):
            raise ConfigError("records must be a list of objects")
        raw_records = cast("list[Any]", records)
        if not all(isinstance(item, dict) for item in raw_records):
            raise ConfigError("records must be a list of objects")
        if not _primitive(raw_records):
            raise ConfigError("task records must contain only primitive JSON values")
        if not isinstance(usages, list):
            raise ConfigError("usages must be a list")
        raw_error = data.get("error")
        return cls(
            task_id=_require_string(data.get("task_id"), "task_id"),
            records=cast("list[dict[str, Any]]", raw_records),
            usages=[_usage_from_dict(item) for item in cast("list[Any]", usages)],
            outcome=_require_string(data.get("outcome"), "outcome"),
            error=ErrorData.from_dict(raw_error) if raw_error is not None else None,
            wire_version=wire_version,
            prompt_version=cast("str", data.get("prompt_version")),
        )


class LocalLeafExecutor:
    """Run a task through an injected local handler and enforce boundary identity."""

    def __init__(self, handler: Callable[[LeafTask], Awaitable[LeafTaskResult]]) -> None:
        self._handler = handler

    async def execute(self, task: LeafTask) -> LeafTaskResult:
        LeafTask.from_dict(task.to_dict())
        result = await self._handler(task)
        LeafTaskResult.from_dict(result.to_dict())
        if result.task_id != task.task_id:
            raise ConfigError(
                f"task result id {result.task_id!r} does not match task {task.task_id!r}"
            )
        check_result_contract(task, result)
        return result


def check_result_contract(task: LeafTask, result: LeafTaskResult) -> None:
    """Refuse a result answered under a different leaf contract from its task's.

    A ``keyed-leaf-fetch-2`` task must come back on wire version 5, and a result on wire
    version 5 answers only such a task. Tasks on the older wire versions keep their
    historical result envelope, which does not echo the task's own number: a task on wire
    version 3 or 4 is answered on 2, and wire version 1 remains readable.
    """
    task_complete = task.wire_version == COMPLETE_WIRE_VERSION
    result_complete = result.wire_version == COMPLETE_WIRE_VERSION
    if task_complete != result_complete or (
        task_complete and result.prompt_version != task.prompt_version
    ):
        raise ConfigError(
            f"task result contract {result.prompt_version!r} on wire version "
            f"{result.wire_version} does not match task contract {task.prompt_version!r} "
            f"on wire version {task.wire_version}"
        )
