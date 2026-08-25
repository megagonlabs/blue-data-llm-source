"""Tool definitions and the translation between tool arguments and the library.

Nothing here imports ``mcp``. The server module owns that import, so the schemas and the
translation can be exercised with only the library installed, and so a change to either
one is tested without a transport.

There is no logic in this module beyond moving values across the boundary. Argument
validation belongs to the library, whose messages are written to be read by a model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from blue_data_llm_source import Estimate, ExecMeta, LlmSource, LlmSourceError
from blue_data_llm_source import __version__ as library_version

FETCH = "llm_fetch"
ESTIMATE = "llm_estimate"
INFO = "llm_info"


class ToolError(Exception):
    """A failed tool call. The library's message is carried through unchanged."""

    def __init__(self, message: str, kind: str = "ToolError") -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind


@dataclass(frozen=True)
class ToolSpec:
    """One tool as the protocol describes it."""

    name: str
    description: str
    input_schema: dict[str, Any]


ATTR_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "string", "description": "Attribute name."},
        {
            "type": "object",
            "description": "Attribute name with a declared value type.",
            "properties": {
                "name": {"type": "string", "description": "Attribute name."},
                "type": {
                    "type": "string",
                    "description": "Declared type: int, float, bool, date, or text.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    ]
}

# The request itself: what a fetch asks for, and what an estimate prices. Shared by both
# schemas so the two tools cannot drift into pricing one request and running another.
REQUEST_PROPERTIES: dict[str, Any] = {
    "query": {
        "type": "string",
        "description": "What to ask the model for, in plain language.",
    },
    "attrs": {
        "type": "array",
        "description": "Attributes every record must carry. Omit to let the model choose keys.",
        "items": ATTR_SCHEMA,
    },
    "context": {
        "type": "string",
        "description": "Extra text that clarifies the query or an attribute name.",
    },
}

# The library's own property names, spelled exactly as they are on a source or on the
# command line, so a ceiling means one thing everywhere. A renamed key here would be read
# by nothing: the library validates the keys it is given, and a tool argument the handlers
# do not recognise never reaches it, so the run would go unbounded in silence.
LIMIT_PROPERTIES: dict[str, Any] = {
    "limits.max_cost": {
        "type": "number",
        "description": (
            "Refuse a call that would take this run's spend past this many dollars. The "
            "output allowance is reserved in full, so llm_estimate reports the floor a "
            "ceiling has to clear."
        ),
        "minimum": 0,
    },
    "limits.max_seconds": {
        "type": "number",
        "description": "Refuse a call once the run has been going this long.",
        "minimum": 0,
    },
    "limits.max_calls": {
        "type": "integer",
        "description": "Refuse more than this many provider calls, retries included.",
        "minimum": 0,
    },
}

LIMIT_KEYS: tuple[str, ...] = tuple(LIMIT_PROPERTIES)

FETCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **REQUEST_PROPERTIES,
        "max_records": {
            "type": "integer",
            "description": "Stop after this many records.",
            "minimum": 1,
        },
        **LIMIT_PROPERTIES,
    },
    # No required key. The source treats a missing or empty query as a normal outcome and
    # reports it as `empty_query`, which the README documents; declaring it required told a
    # generated client something the server does not enforce and would not want to.
    "required": [],
    "additionalProperties": False,
}

# No `max_records` here, and deliberately: `estimate` takes no row cap, so accepting one
# would advertise an input that changes nothing about the answer. A row cap does not bound
# spend anyway — the model is asked once and the surplus records are dropped on arrival.
ESTIMATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {**REQUEST_PROPERTIES, **LIMIT_PROPERTIES},
    "required": [],
    "additionalProperties": False,
}

INFO_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "No inputs.",
    "properties": {},
    "additionalProperties": False,
}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name=FETCH,
        description=(
            "Fetch structured records from a language model's own knowledge. Returns a "
            "list of records and a meta object describing the call."
        ),
        input_schema=FETCH_INPUT_SCHEMA,
    ),
    ToolSpec(
        name=ESTIMATE,
        description=(
            "Bound what one llm_fetch of the same request could cost, without calling the "
            "model. Returns an upper bound on the whole fetch, retries included."
        ),
        input_schema=ESTIMATE_INPUT_SCHEMA,
    ),
    ToolSpec(
        name=INFO,
        description="Report the protocol, the variant, and the library version.",
        input_schema=INFO_INPUT_SCHEMA,
    ),
)


def build_source() -> LlmSource:
    """Construct the source a tool call runs against."""
    return LlmSource()


def limit_properties(args: dict[str, Any]) -> dict[str, Any] | None:
    """The ceilings in *args*, as the per-call properties the library reads.

    None rather than an empty dict when the caller set none, so a call without a ceiling
    reaches the library exactly as it did before there was one to set. The library merges
    these over the source's own properties for this call and does not write them back, so
    one agent's ceiling cannot leak into the next call over the same server.
    """
    properties = {key: args[key] for key in LIMIT_KEYS if key in args}
    return properties or None


def meta_summary(meta: ExecMeta) -> dict[str, Any]:
    """Shape ExecMeta for a tool result: always the accounting, the rest when set."""
    summary: dict[str, Any] = {
        "outcome": meta.outcome,
        "model": meta.model,
        "calls": meta.calls,
        "prompt_tokens": meta.prompt_tokens,
        "completion_tokens": meta.completion_tokens,
        "cost": round(meta.cost, 6),
        "elapsed_seconds": round(meta.latency_seconds, 3),
    }
    if meta.type_mismatches:
        summary["type_mismatches"] = list(meta.type_mismatches)
    if meta.key_collisions:
        summary["key_collisions"] = list(meta.key_collisions)
    if meta.unmapped_keys:
        summary["unmapped_keys"] = list(meta.unmapped_keys)
    # An uncheckable declared type is a fact about the request, and reporting it once is the
    # whole point of the field; discarding it here restored the silence it was added to end.
    if meta.unchecked_types:
        summary["unchecked_types"] = list(meta.unchecked_types)
    if meta.remapped_keys:
        summary["remapped_keys"] = dict(meta.remapped_keys)
    if meta.truncated_to is not None:
        summary["truncated_to"] = meta.truncated_to
    if meta.stopped_by:
        summary["stopped_by"] = meta.stopped_by
    if not meta.accounting_known:
        # Only when it is false, so an ordinary result is unchanged. A model reading this
        # needs to know the calls and cost above are this library's bookkeeping rather than
        # provider work, or it will treat an unmeasured zero as a measured one.
        summary["accounting_known"] = False
    return summary


async def allm_fetch(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """Run one fetch and return its records with a compact meta dict.

    Async because the MCP handler that calls it is async. The library's synchronous
    ``fetch`` refuses to run inside an event loop, so a sync dispatcher reached from an
    async handler cannot work at all.
    """
    args = arguments or {}
    target = source if source is not None else build_source()
    # A tool argument is whatever the client sent, including nothing at all, so that is how
    # it is typed here. The library reads a missing query as an empty ask and reports it in
    # the result, and names a wrong type in a message the model can act on.
    query: Any = args.get("query")
    try:
        result = await target.afetch(
            query,
            attrs=args.get("attrs"),
            context=args.get("context"),
            max_records=args.get("max_records"),
            properties=limit_properties(args),
        )
    except LlmSourceError as error:
        raise ToolError(str(error), kind=type(error).__name__) from error
    return {"records": list(result.records), "meta": meta_summary(result.meta)}


def llm_fetch(arguments: dict[str, Any] | None = None, source: Any | None = None) -> dict[str, Any]:
    """Synchronous form, for callers outside an event loop."""
    args = arguments or {}
    target = source if source is not None else build_source()
    query: Any = args.get("query")  # same boundary as allm_fetch
    try:
        result = target.fetch(
            query,
            attrs=args.get("attrs"),
            context=args.get("context"),
            max_records=args.get("max_records"),
            properties=limit_properties(args),
        )
    except LlmSourceError as error:
        raise ToolError(str(error), kind=type(error).__name__) from error
    return {"records": list(result.records), "meta": meta_summary(result.meta)}


def estimate_summary(estimate: Estimate) -> dict[str, Any]:
    """Shape Estimate for a tool result: every field, whether or not it is set.

    The two costs are published unrounded, unlike the spend in ``meta_summary``. That one is
    a measurement and rounding it loses nothing a caller can act on; these are upper bounds,
    and rounding a bound to six places can round it *down*. An agent that then set
    ``limits.max_cost`` to the figure it was given would have the last attempt that figure
    accounted for refused.
    """
    return {
        "bounded": estimate.bounded,
        "max_cost": estimate.max_cost,
        "max_cost_per_attempt": estimate.max_cost_per_attempt,
        "max_attempts": estimate.max_attempts,
        "reason": estimate.reason,
        "model": estimate.model,
        "prompt_bytes": estimate.prompt_bytes,
        "max_output_tokens": estimate.max_output_tokens,
    }


def llm_estimate(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """Bound what one fetch of this request could cost, contacting no provider.

    Synchronous on both dispatch paths, and safe there in a way ``fetch`` is not: this
    awaits nothing and reaches no transport, so there is no loop for it to be called from
    inside of.
    """
    args = arguments or {}
    target = source if source is not None else build_source()
    query: Any = args.get("query")  # same boundary as llm_fetch
    try:
        estimate = target.estimate(
            query,
            attrs=args.get("attrs"),
            context=args.get("context"),
            properties=limit_properties(args),
        )
    except LlmSourceError as error:
        raise ToolError(str(error), kind=type(error).__name__) from error
    return estimate_summary(estimate)


def llm_info(arguments: dict[str, Any] | None = None, source: Any | None = None) -> dict[str, Any]:
    """Report the protocol, the variant, and the library version."""
    target = source if source is not None else build_source()
    return {
        "protocol": target.protocol,
        "variant": target.variant,
        "version": library_version,
    }


_HANDLERS: dict[str, Callable[[dict[str, Any] | None, Any | None], dict[str, Any]]] = {
    FETCH: llm_fetch,
    ESTIMATE: llm_estimate,
    INFO: llm_info,
}


async def _allm_info(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    return llm_info(arguments, source)


async def _allm_estimate(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    return llm_estimate(arguments, source)


# Keyed by the same constants as the synchronous table, so the two cannot drift into
# offering different tools on the two dispatch paths.
_ASYNC_HANDLERS = {FETCH: allm_fetch, ESTIMATE: _allm_estimate, INFO: _allm_info}


# Withdrawn before release. Named here as well as in the library because a tool call never
# reaches the library's property validation: the handlers read the fields they know and drop
# the rest, so an unrecognised key arrived as silence rather than as a refusal.
_DEFERRED_ARGS = {
    "llm.rate_limit": (
        "client-side call spacing is deferred to v2.0. Use 'llm.max_parallel' to cap "
        "concurrency, or throttle above this server where the whole request sequence is visible"
    ),
}

_SCHEMAS = {spec.name: spec.input_schema for spec in TOOLS}


def _check_arguments(name: str, arguments: object) -> None:
    """Enforce the advertised schema, which nothing else enforces for us.

    Every tool schema declares ``additionalProperties: false``, and nothing was checking it:
    the handler took the fields it recognised and discarded whatever else arrived. A caller
    setting a ceiling this build does not support was told nothing and got an unbounded run.

    The SDK's own prevalidator is switched off for this handler, because it ran first and
    answered a deferred key with a generic schema error instead of the message naming what
    to use instead. So this has to do the whole job, and the schema tests assert it does.
    """
    schema = _SCHEMAS.get(name)
    if schema is None:
        return
    # `object`, not `dict | None`: this is a boundary a programmatic caller reaches, and an
    # annotation is not a guarantee about what arrives. `arguments or {}` folded [], "", 0
    # and False into an empty object, so a caller violating the annotated root type got a
    # successful empty-query or info result instead of the validation the schema advertises.
    # None stays the no-arguments sentinel.
    if arguments is None:
        args: dict[str, Any] = {}
    elif isinstance(arguments, dict):
        args = cast("dict[str, Any]", arguments)
    else:
        raise ToolError(
            f"arguments for tool {name!r} must be an object, got {_kind(arguments)}",
            kind="ConfigError",
        )
    # Deferred keys first, at any depth. Left to the shape check below, a deferred key
    # nested in an attribute came back as "does not match any accepted shape", which is
    # true and useless.
    _refuse_deferred(args)
    _refuse_extras(sorted(set(args) - set(schema.get("properties", {}))), name, schema)
    properties = cast("dict[str, Any]", schema.get("properties", {}))
    for key, value in args.items():
        # Narrowed in place: a JSON number with no fractional part satisfies the advertised
        # "integer" schema, so a client that validated against that schema and sent 2.0 was
        # refused by this checker for a value its own contract accepted. Rewriting it here
        # means the library downstream still receives an int.
        args[key] = _check_value(f"{key!r}", value, cast("dict[str, Any]", properties[key]))


def _refuse_deferred(node: object) -> None:
    """Name a withdrawn setting wherever it appears, before anything else looks at shape."""
    if isinstance(node, dict):
        for key, value in cast("dict[str, Any]", node).items():
            if key in _DEFERRED_ARGS:
                raise ToolError(
                    f"{key!r} is not supported: {_DEFERRED_ARGS[key]}", kind="ConfigError"
                )
            _refuse_deferred(value)
    elif isinstance(node, list):
        for item in cast("list[Any]", node):
            _refuse_deferred(item)


_ATTR_KEYS = frozenset({"name", "type"})


def _kind(value: object) -> str:
    """The name of what arrived, for a message that says what was wrong with it."""
    return type(value).__name__


def _check_value(label: str, value: Any, spec: dict[str, Any]) -> Any:
    """Enforce the declared type and bounds of one advertised field.

    The SDK validates ``inputSchema`` before the decorated handler runs, which meant a
    deferred key was answered with a generic schema error instead of the message naming what
    to use instead. Turning that prevalidation off is only safe if this does the whole job,
    so this covers every construct the shipped schemas actually use — and the schema tests
    assert that no other construct, and no other declared type, appears.
    """
    branches = spec.get("oneOf") or spec.get("anyOf")
    if branches is not None:
        for option in cast("list[dict[str, Any]]", branches):
            try:
                return _check_value(label, value, option)
            except ToolError:
                continue
        raise ToolError(f"{label} does not match any accepted shape", kind="ConfigError")

    # Read once, before any narrowing: taken later the checker sees the accumulated
    # union from the isinstance chain rather than what actually arrived.
    kind = _kind(value)
    declared = spec.get("type")
    if declared == "string" and not isinstance(value, str):
        raise ToolError(f"{label} must be a string, got {kind}", kind="ConfigError")
    if declared == "integer":
        # bool is an int in Python and is never what a count meant.
        if isinstance(value, bool):
            raise ToolError(f"{label} must be an integer, got {kind}", kind="ConfigError")
        # JSON has one number type, so 2 and 2.0 are the same value on the wire and both
        # satisfy an "integer" schema. Refusing the second contradicted the schema this
        # server advertises, and a client generating arguments from that schema had no way
        # to tell which spelling would be accepted.
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int):
            raise ToolError(f"{label} must be an integer, got {kind}", kind="ConfigError")
        _check_minimum(label, value, spec)
    if declared == "number":
        # bool is an int is a number in Python, and a flag is never a ceiling. The schema
        # validators a client generates arguments against exclude it too, so accepting it
        # here would make this server accept what its own published contract refuses.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolError(f"{label} must be a number, got {kind}", kind="ConfigError")
        _check_minimum(label, value, spec)
    if declared == "array":
        if not isinstance(value, list):
            raise ToolError(f"{label} must be an array, got {kind}", kind="ConfigError")
        item_spec = cast("dict[str, Any]", spec.get("items", {}))
        for index, item in enumerate(cast("list[Any]", value)):
            cast("list[Any]", value)[index] = _check_value(f"{label}[{index}]", item, item_spec)
    if declared == "object":
        if not isinstance(value, dict):
            raise ToolError(f"{label} must be an object, got {kind}", kind="ConfigError")
        entry: dict[str, Any] = cast("dict[str, Any]", value)
        declared_keys = cast("dict[str, Any]", spec.get("properties", {}))
        for missing in cast("list[str]", spec.get("required", [])):
            if missing not in entry:
                raise ToolError(f"{label} is missing {missing!r}", kind="ConfigError")
        for key in entry:
            if key not in declared_keys:
                raise ToolError(f"{label} has an unknown key {key!r}", kind="ConfigError")
            entry[key] = _check_value(
                f"{label}.{key}", entry[key], cast("dict[str, Any]", declared_keys[key])
            )
    # The value as it should be used, which is the same object unless a branch narrowed it.
    return cast("Any", value)


def _check_minimum(label: str, value: float, spec: dict[str, Any]) -> None:
    """Enforce a declared lower bound, for whichever numeric type declared it."""
    minimum = spec.get("minimum")
    if minimum is not None and value < cast("float", minimum):
        raise ToolError(f"{label} must be at least {minimum}, got {value}", kind="ConfigError")


def _refuse_extras(extra: list[str], name: str, schema: dict[str, Any], within: str = "") -> None:
    for key in extra:
        if key in _DEFERRED_ARGS:
            raise ToolError(f"{key!r} is not supported: {_DEFERRED_ARGS[key]}", kind="ConfigError")
    if extra:
        accepted = sorted(_ATTR_KEYS) if within else sorted(schema.get("properties", {}))
        where = f"in {within} for tool {name!r}" if within else f"for tool {name!r}"
        raise ToolError(
            f"unknown argument {', '.join(repr(k) for k in extra)} {where}. Accepted: {accepted}",
            kind="ConfigError",
        )


def dispatch(
    name: str, arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """Route a tool call by name, or raise ToolError naming the tools that exist."""
    handler = _HANDLERS.get(name)
    if handler is None:
        available = ", ".join(spec.name for spec in TOOLS)
        raise ToolError(f"unknown tool {name!r}. Available tools: {available}", kind="UnknownTool")
    _check_arguments(name, arguments)
    return handler(arguments, source)


async def adispatch(
    name: str, arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """Async route, used by the MCP handler.

    The server's ``call_tool`` is async, so it must reach the library's async path. A
    synchronous dispatcher here would call ``fetch`` inside a running loop, which the
    library refuses by design, and every fetch over MCP would fail.
    """
    handler = _ASYNC_HANDLERS.get(name)
    if handler is None:
        available = ", ".join(spec.name for spec in TOOLS)
        raise ToolError(f"unknown tool {name!r}. Available tools: {available}", kind="UnknownTool")
    _check_arguments(name, arguments)
    return await handler(arguments, source)
