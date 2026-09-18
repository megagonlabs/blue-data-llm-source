"""Tool definitions and the translation between tool arguments and the library.

Nothing here imports ``mcp``. The server module owns that import, so the schemas and the
translation can be exercised with only the library installed, and so a change to either
one is tested without a transport.

There is no logic in this module beyond moving values across the boundary. Argument
validation belongs to the library, whose messages are written to be read by a model.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from blue_data_llm_source import (
    Estimate,
    LlmSource,
    LlmSourceError,
    can_bound,
    meta_view,
    priceable_models,
    rates_for,
    redact,
)
from blue_data_llm_source import __version__ as library_version

QUERY = "llmdb_query"
ESTIMATE = "llmdb_estimate"
LIST_MODELS = "llmdb_list_models"
INSPECT_MODEL = "llmdb_inspect_model"
INSPECT_METADATA = "llmdb_inspect_metadata"

# Error kinds this module raises itself, as opposed to a library exception's class name.
# They are separate from `ConfigError` on purpose: a refused argument is fixed by changing
# the call, a refused library setting by changing the deployment, and one prefix for both
# told a caller nothing about which. The CLI draws the same line, as USAGE against CONFIG.
INVALID_ARGUMENTS = "InvalidArguments"
UNKNOWN_TOOL = "UnknownTool"

NON_BLANK = r"\S"
"""The declared pattern for a field the library refuses when it holds only whitespace.

`minLength` would not express it: the library strips before it looks, so `"   "` passed the
advertised schema and was then refused by the runtime, which is the one direction in which
"advertised equals enforced" can quietly stop being true.
"""


class ToolError(Exception):
    """A failed tool call, in the library's own wording with credential shapes masked.

    Masked at construction rather than at the transport. ``dispatch`` is reached directly by
    anything embedding these tools without a server, and a message that only the server
    makes safe is only safe through the server.
    """

    def __init__(self, message: str, kind: str = "ToolError") -> None:
        masked = redact(message)
        super().__init__(masked)
        self.message = masked
        self.kind = kind


@dataclass(frozen=True)
class ToolSpec:
    """One tool as the protocol describes it."""

    name: str
    description: str
    input_schema: dict[str, Any]


ATTR_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "string", "pattern": NON_BLANK, "description": "Attribute name."},
        {
            "type": "object",
            "description": "Attribute name with a declared value type.",
            "properties": {
                "name": {"type": "string", "pattern": NON_BLANK, "description": "Attribute name."},
                "type": {
                    # No `enum`: the library checks a value against a declared type it
                    # recognises and accepts any other name unchecked, reporting it once in
                    # meta.unchecked_types. Closing the set here would refuse what the
                    # library accepts, and the CLI does not close it either.
                    "type": "string",
                    "description": (
                        "Declared type, checked against the returned values. One of int, "
                        "integer, float, number, bool, boolean, date, str, string, text. "
                        "Any other name is accepted, checks nothing, and is reported once "
                        "in meta.unchecked_types."
                    ),
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
        # The library refuses one attribute named twice, before it builds a budget or
        # contacts anything. Declaring it here is what lets a client generating arguments
        # against this schema find that out without a round trip.
        "uniqueItems": True,
    },
    "context": {
        "type": "string",
        "description": "Extra text that clarifies the query or an attribute name.",
    },
}

# The library's own property names, spelled exactly as a source and the CLI's profile file
# spell them, so a ceiling means one thing everywhere. The CLI's *flags* are hyphenated
# shorthands for these keys -- `--limit-cost` for `limits.max_cost` -- because a dot is not
# usable in a long option; the README carries the mapping. A renamed key here would be read
# by nothing: the library validates the keys it is given, and a tool argument the handlers
# do not recognise never reaches it, so the run would go unbounded in silence.
LIMIT_PROPERTIES: dict[str, Any] = {
    "limits.max_cost": {
        "type": "number",
        "description": (
            "Refuse a call that would take this run's spend past this many dollars. The "
            "output allowance is reserved in full, so llmdb_estimate reports the floor a "
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

# The plan namespace, spelled exactly as the library and the profile file spell it, for the
# reason the ceilings are: a key renamed at this boundary is read by nothing, and the run
# proceeds under a default the caller did not choose and cannot see. The CLI's `--plan-*`
# flags map to these keys; the README carries that table too.

# The three closed sets the library refuses by name, written out here as schema literals: a
# client reads the accepted values out of the advertised schema, so they are part of what
# this server publishes rather than a lookup it performs. A test pins all three against the
# library's own constants, so a change there fails here instead of reaching a caller as a
# schema that no longer matches what the runtime accepts.
PLAN_MODES = ("flat", "partitioned", "row_by_row")
SCHEMA_MODES = ("preserve", "enrich")
PLANNING_ORDERS = (
    "planning.cardinality_first",
    "planning.schema_first",
    "planning.locked_sequential",
    "planning.locked_parallel",
    "planning.combined",
)
PLAN_PROPERTIES: dict[str, Any] = {
    "plan.mode": {
        "type": "string",
        "enum": list(PLAN_MODES),
        "description": (
            "How the ask is decomposed: `flat`, `partitioned`, or `row_by_row`. `flat` "
            "asks once and is the default; non-flat modes add bounded planning before "
            "leaf extraction. No mode is selected automatically, and an unknown one is refused."
        ),
    },
    "plan.max_continuations": {
        "type": "integer",
        "description": (
            "How many times a part may ask again for records it did not get. Zero by "
            "default, so each part asks once. It bounds asks per part, not provider attempts "
            "or planning calls; an ask may be retried up to limits.max_retries times."
        ),
        "minimum": 0,
    },
    "plan.max_leaves": {
        "type": "integer",
        "description": (
            "The most parts a plan may hold; the term that bounds its worst cost. In "
            "partitioned mode, values past it run grouped several to a part rather than "
            "being dropped. In row_by_row, an entity list past it falls back to one unsplit ask."
        ),
        "minimum": 1,
    },
    "plan.partition_key": {
        "type": "string",
        "pattern": NON_BLANK,
        "description": (
            "Preferred dimension for the root partition proposal. Descendants may choose "
            "other dimensions, but may not repeat an ancestor split dimension."
        ),
    },
    "plan.max_depth": {
        "type": "integer",
        "description": "Deepest partition level. Zero disables subdivision. Default: 2.",
        "minimum": 0,
    },
    "plan.max_partitions": {
        "type": "integer",
        "description": (
            "Most children proposed for one partition node. A value below two leaves the "
            "node unsplit. Default: 4."
        ),
        "minimum": 0,
    },
    "plan.target_rows_per_leaf": {
        "type": "integer",
        "description": (
            "Uncertain row-count hint above which a descendant may split again. It is not "
            "a completeness target. Default: 50."
        ),
        "minimum": 1,
    },
    "plan.schema_mode": {
        "type": "string",
        "enum": list(SCHEMA_MODES),
        "description": (
            "`preserve` keeps declared attributes and is the default. `enrich` lets "
            "non-flat planning append inferred attributes while preserving declarations."
        ),
    },
    "plan.planning_order": {
        "type": "string",
        "enum": list(PLANNING_ORDERS),
        "description": (
            "Planning fact schedule: planning.cardinality_first, planning.schema_first, "
            "planning.locked_sequential, planning.locked_parallel, or planning.combined. "
            "The locked schedules require a non-empty caller schema and ordered plan.keys."
        ),
    },
    "plan.expose_estimate": {
        "type": "boolean",
        "description": "Whether extraction prompts include the planner's soft row estimate.",
    },
    "plan.key": {
        "type": "string",
        "pattern": NON_BLANK,
        "description": (
            "The attribute identifying a record, used to merge duplicates returned by "
            "different parts. Must be one of the requested attributes. Without it records "
            "from different parts are not merged, though exact whole-record duplicates are "
            "still removed."
        ),
    },
    "plan.keys": {
        "type": "array",
        "description": "Ordered fields that jointly identify one record.",
        "items": {"type": "string", "pattern": NON_BLANK},
        "uniqueItems": True,
    },
    "plan.exact_rows": {
        "type": "integer",
        "description": (
            "Hard row target for a single-leaf plan. A multi-leaf plan does not apply it "
            "and reports exact_rows_not_leaf_bound."
        ),
        "minimum": 0,
    },
    "plan.max_exclusion_bytes": {
        "type": "integer",
        "description": "Most encoded bytes of whole key exclusions in one continuation.",
        "minimum": 0,
    },
}

PLAN_KEYS: tuple[str, ...] = tuple(PLAN_PROPERTIES)

QUERY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **REQUEST_PROPERTIES,
        "max_records": {
            "type": "integer",
            "description": "Stop after this many records.",
            "minimum": 1,
        },
        **LIMIT_PROPERTIES,
        **PLAN_PROPERTIES,
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

MODELS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "No inputs.",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

MODEL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "model": {
            "type": "string",
            "description": (
                "Model name to look up, e.g. gpt-4o. Exact names and dated variants only; "
                "a longer name is treated as a different model rather than as a variant of "
                "the one it starts with."
            ),
        }
    },
    # The one required argument on this server, and genuinely required: there is no default
    # model to inspect, and answering for the configured one would answer a question the
    # caller did not ask.
    "required": ["model"],
    "additionalProperties": False,
}

METADATA_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "No inputs.",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name=QUERY,
        description=(
            "Fetch structured records from a language model's own knowledge. Returns a "
            "list of records and a meta object describing the call, including any "
            "reason rows may be missing."
        ),
        input_schema=QUERY_INPUT_SCHEMA,
    ),
    ToolSpec(
        name=ESTIMATE,
        description=(
            "Bound what one flat llmdb_query of the same request could cost, without calling the "
            "model. Returns an upper bound on the whole fetch, retries included. Reads no "
            "plan.* setting, so a split request can cost several times this figure."
        ),
        input_schema=ESTIMATE_INPUT_SCHEMA,
    ),
    ToolSpec(
        name=LIST_MODELS,
        description=(
            "List the pricing bases this package carries a published price for. A dated "
            "variant such as gpt-4o-2024-08-06 is priced against gpt-4o and is not listed "
            "separately, so this is not every name a ceiling can be claimed against. A "
            "ceiling needs a listed name, a dated variant of one, or rates the deployment "
            "supplied. llmdb_inspect_model settles a single name. Contacts no provider and "
            "costs nothing."
        ),
        input_schema=MODELS_INPUT_SCHEMA,
    ),
    ToolSpec(
        name=INSPECT_MODEL,
        description=(
            "Report one model's published input and output rates, and whether a "
            "limits.max_cost ceiling can be bounded against it on this server. A model the "
            "price table does not carry is a successful answer with priceable false, not an "
            "error. Contacts no provider and costs nothing."
        ),
        input_schema=MODEL_INPUT_SCHEMA,
    ),
    ToolSpec(
        name=INSPECT_METADATA,
        description="Report the protocol, the variant, and the library version.",
        input_schema=METADATA_INPUT_SCHEMA,
    ),
)


def build_source() -> LlmSource:
    """Construct the source a tool call runs against."""
    return LlmSource()


def limit_properties(args: dict[str, Any]) -> dict[str, Any] | None:
    """The ceilings and plan settings in *args*, as per-call properties the library reads.

    None rather than an empty dict when the caller set none, so a call without a ceiling
    reaches the library exactly as it did before there was one to set. The library merges
    these over the source's own properties for this call and does not write them back, so
    one agent's ceiling cannot leak into the next call over the same server.
    """
    properties = {key: args[key] for key in LIMIT_KEYS + PLAN_KEYS if key in args}
    return properties or None


async def llmdb_query_async(
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
    return {"records": list(result.records), "meta": meta_view(result.meta, verbose=True)}


def llmdb_query(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """Synchronous form, for callers outside an event loop."""
    args = arguments or {}
    target = source if source is not None else build_source()
    query: Any = args.get("query")  # same boundary as llmdb_query_async
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
    return {"records": list(result.records), "meta": meta_view(result.meta, verbose=True)}


def estimate_summary(estimate: Estimate) -> dict[str, Any]:
    """Shape Estimate for a tool result: every field, whether or not it is set.

    The two costs are published unrounded, unlike the spend in ``meta_view``. That one is
    a measurement and rounding it loses nothing a caller can act on; these are upper bounds,
    and rounding a bound to six places can round it *down*. An agent that then set
    ``limits.max_cost`` to the figure it was given would have the last attempt that figure
    accounted for refused.

    ``reason`` is a library error message published as a field rather than raised, so it
    reaches a caller without passing the boundary that masks the raised ones. It is masked
    here instead.
    """
    return {
        "bounded": estimate.bounded,
        "max_cost": estimate.max_cost,
        "max_cost_per_attempt": estimate.max_cost_per_attempt,
        "max_attempts": estimate.max_attempts,
        "reason": redact(estimate.reason) if estimate.reason else estimate.reason,
        "model": estimate.model,
        "prompt_bytes": estimate.prompt_bytes,
        "max_output_tokens": estimate.max_output_tokens,
    }


def llmdb_estimate(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """Bound what one fetch of this request could cost, contacting no provider.

    Synchronous on both dispatch paths, and safe there in a way ``fetch`` is not: this
    awaits nothing and reaches no transport, so there is no loop for it to be called from
    inside of.
    """
    args = arguments or {}
    target = source if source is not None else build_source()
    query: Any = args.get("query")  # same boundary as llmdb_query
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


def llmdb_list_models(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """The model names a cost ceiling can be bounded against, sorted.

    Names only, which is what the library publishes. The price table itself is a mutable
    snapshot of list prices and is not a contract this package can keep across a price
    change; a name is.
    """
    return {"models": list(priceable_models())}


def llmdb_inspect_model(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """One model's published rates, and whether this server could bound a ceiling on it.

    A model the table does not carry answers with ``priceable`` false rather than raising,
    the same way ``llmdb_estimate`` answers with ``bounded`` false: asking what something
    costs is reasonable even where the answer cannot be given.
    """
    args = arguments or {}
    name = str(args["model"])
    target = source if source is not None else build_source()
    rates = rates_for(name)
    # Read off the source rather than assumed, because the question is about this
    # deployment: a cost bound needs a finite output allowance as well as rates, and a
    # source configured without one can price nothing however well known the model is.
    properties: dict[str, Any] = dict(target.properties)
    allowance = properties.get("llm.max_tokens")
    return {
        # The one field here that is the caller's own text on its way back out. The lookups
        # above read what arrived; what is published is masked.
        "model": redact(name),
        # The price table alone, which is what the word means. A deployment that supplied
        # its own rates makes a ceiling bindable without making the model priced here, and
        # the rates it supplied are its configuration rather than this tool's answer.
        "priceable": rates is not None,
        "price_per_input_token": rates[0] if rates else None,
        "price_per_output_token": rates[1] if rates else None,
        "max_output_tokens": allowance,
        # Under the properties a fetch would actually run with, so this agrees with what
        # llmdb_estimate reports for the same deployment. Without them it answers the price
        # table's question instead, and reports no ceiling where the deployment supplied
        # the rates for one.
        "cost_ceiling_supported": can_bound(name, allowance, properties),
    }


def llmdb_inspect_metadata(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """Report the protocol, the variant, and the library version."""
    target = source if source is not None else build_source()
    return {
        "protocol": target.protocol,
        "variant": target.variant,
        "version": library_version,
    }


Handler = Callable[[dict[str, Any] | None, Any | None], dict[str, Any]]
AsyncHandler = Callable[[dict[str, Any] | None, Any | None], Awaitable[dict[str, Any]]]

_HANDLERS: dict[str, Handler] = {
    QUERY: llmdb_query,
    ESTIMATE: llmdb_estimate,
    LIST_MODELS: llmdb_list_models,
    INSPECT_MODEL: llmdb_inspect_model,
    INSPECT_METADATA: llmdb_inspect_metadata,
}


def _awaitable(handler: Handler) -> AsyncHandler:
    """An offline handler as a coroutine, for the async table."""

    async def run(
        arguments: dict[str, Any] | None = None, source: Any | None = None
    ) -> dict[str, Any]:
        return handler(arguments, source)

    return run


# Derived from the synchronous table rather than written out beside it, so the two cannot
# drift into offering different tools on the two dispatch paths. Only the query has a real
# async path; every other tool awaits nothing and reaches no transport.
_ASYNC_HANDLERS: dict[str, AsyncHandler] = {
    name: llmdb_query_async if name == QUERY else _awaitable(handler)
    for name, handler in _HANDLERS.items()
}


# Named here as well as in the library because a tool call never reaches the library's property
# validation: the handlers read known fields and would otherwise drop an unrecognised key.
_DEFERRED_ARGS = {
    "llm.rate_limit": (
        "client-side call spacing is not implemented here. Use 'llm.max_parallel' to cap "
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
            kind=INVALID_ARGUMENTS,
        )
    # Deferred keys first, at any depth. Left to the shape check below, a deferred key
    # nested in an attribute came back as "does not match any accepted shape", which is
    # true and useless.
    _refuse_deferred(args)
    _refuse_extras(sorted(set(args) - set(schema.get("properties", {}))), name, schema)
    # Required keys at the root, which only nested objects were checked for. Until this
    # server had a tool with a required argument the omission was invisible; declaring one
    # and not enforcing it would advertise a rule the handler discovers as a KeyError.
    for missing in cast("list[str]", schema.get("required", [])):
        if missing not in args:
            raise ToolError(f"tool {name!r} requires {missing!r}", kind=INVALID_ARGUMENTS)
    properties = cast("dict[str, Any]", schema.get("properties", {}))
    for key, value in args.items():
        # Narrowed in place: a JSON number with no fractional part satisfies the advertised
        # "integer" schema, so a client that validated against that schema and sent 2.0 was
        # refused by this checker for a value its own contract accepted. Rewriting it here
        # means the library downstream still receives an int.
        args[key] = _check_value(f"{key!r}", value, cast("dict[str, Any]", properties[key]))
    _check_attr_names(args)


def _check_attr_names(args: dict[str, Any]) -> None:
    """Refuse one attribute named twice across the two shapes `attrs` accepts.

    `uniqueItems` compares whole entries, so `"country"` and `{"name": "country"}` satisfy
    it and still name one attribute. JSON Schema cannot say otherwise: the rule is
    uniqueness of a property projected out of one branch of a union, and there is no
    keyword for that. So it is said here, and the pair is refused as the bad argument it is
    rather than reaching the library and coming back wearing a runtime kind.
    """
    seen: set[str] = set()
    for entry in cast("list[Any]", args.get("attrs") or []):
        declared = entry if isinstance(entry, str) else cast("dict[str, Any]", entry).get("name")
        if not isinstance(declared, str):
            continue
        if declared in seen:
            # Not quoted back. The repeated name is in the caller's own array, and what
            # they cannot work out is that the two shapes are the same attribute.
            raise ToolError(
                "'attrs' must not name the same attribute twice, in either shape",
                kind=INVALID_ARGUMENTS,
            )
        seen.add(declared)


def _refuse_deferred(node: object) -> None:
    """Name a withdrawn setting wherever it appears, before anything else looks at shape."""
    if isinstance(node, dict):
        for key, value in cast("dict[str, Any]", node).items():
            if key in _DEFERRED_ARGS:
                raise ToolError(
                    f"{key!r} is not supported: {_DEFERRED_ARGS[key]}", kind=INVALID_ARGUMENTS
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
    so this covers every construct the shipped schemas actually use — `type`, `minimum`,
    `enum`, `items`, `properties`, `required`, `additionalProperties`, `oneOf`/`anyOf` — and
    the schema tests assert that no other construct, and no other declared type, appears.
    """
    branches = spec.get("oneOf") or spec.get("anyOf")
    if branches is not None:
        for option in cast("list[dict[str, Any]]", branches):
            try:
                return _check_value(label, value, option)
            except ToolError:
                continue
        raise ToolError(f"{label} does not match any accepted shape", kind=INVALID_ARGUMENTS)

    # Read once, before any narrowing: taken later the checker sees the accumulated
    # union from the isinstance chain rather than what actually arrived.
    kind = _kind(value)
    declared = spec.get("type")
    if declared == "string":
        if not isinstance(value, str):
            raise ToolError(f"{label} must be a string, got {kind}", kind=INVALID_ARGUMENTS)
        _check_pattern(label, value, spec)
    if declared == "boolean" and not isinstance(value, bool):
        raise ToolError(f"{label} must be a boolean, got {kind}", kind=INVALID_ARGUMENTS)
    if declared == "integer":
        # bool is an int in Python and is never what a count meant.
        if isinstance(value, bool):
            raise ToolError(f"{label} must be an integer, got {kind}", kind=INVALID_ARGUMENTS)
        # JSON has one number type, so 2 and 2.0 are the same value on the wire and both
        # satisfy an "integer" schema. Refusing the second contradicted the schema this
        # server advertises, and a client generating arguments from that schema had no way
        # to tell which spelling would be accepted.
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int):
            raise ToolError(f"{label} must be an integer, got {kind}", kind=INVALID_ARGUMENTS)
        _check_minimum(label, value, spec)
    if declared == "number":
        # bool is an int is a number in Python, and a flag is never a ceiling. The schema
        # validators a client generates arguments against exclude it too, so accepting it
        # here would make this server accept what its own published contract refuses.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolError(f"{label} must be a number, got {kind}", kind=INVALID_ARGUMENTS)
        _check_minimum(label, value, spec)
    if declared == "array":
        if not isinstance(value, list):
            raise ToolError(f"{label} must be an array, got {kind}", kind=INVALID_ARGUMENTS)
        item_spec = cast("dict[str, Any]", spec.get("items", {}))
        for index, item in enumerate(cast("list[Any]", value)):
            cast("list[Any]", value)[index] = _check_value(f"{label}[{index}]", item, item_spec)
        _check_unique(label, cast("list[Any]", value), spec)
    if declared == "object":
        if not isinstance(value, dict):
            raise ToolError(f"{label} must be an object, got {kind}", kind=INVALID_ARGUMENTS)
        entry: dict[str, Any] = cast("dict[str, Any]", value)
        declared_keys = cast("dict[str, Any]", spec.get("properties", {}))
        for missing in cast("list[str]", spec.get("required", [])):
            if missing not in entry:
                raise ToolError(f"{label} is missing {missing!r}", kind=INVALID_ARGUMENTS)
        for key in entry:
            if key not in declared_keys:
                # Quoted, where a value that missed an enum is not: a misspelled key is the
                # thing the caller has to find, and the accepted list alone leaves them
                # comparing it against their own call to see which one is wrong.
                raise ToolError(f"{label} has an unknown key {key!r}", kind=INVALID_ARGUMENTS)
            entry[key] = _check_value(
                f"{label}.{key}", entry[key], cast("dict[str, Any]", declared_keys[key])
            )
    allowed = cast("list[Any] | None", spec.get("enum"))
    if allowed is not None and value not in allowed:
        options = ", ".join(repr(option) for option in allowed)
        # The accepted set, without the value that missed it. What the caller cannot work
        # out is which values this server takes; their own string read back tells them
        # nothing they did not just send, and is one more copy of it in the transcript.
        raise ToolError(f"{label} must be one of {options}", kind=INVALID_ARGUMENTS)
    # The value as it should be used, which is the same object unless a branch narrowed it.
    return cast("Any", value)


def _check_minimum(label: str, value: float, spec: dict[str, Any]) -> None:
    """Enforce a declared lower bound, for whichever numeric type declared it."""
    minimum = spec.get("minimum")
    if minimum is not None and value < cast("float", minimum):
        raise ToolError(f"{label} must be at least {minimum}, got {value}", kind=INVALID_ARGUMENTS)


def _check_pattern(label: str, value: str, spec: dict[str, Any]) -> None:
    """Enforce a declared string pattern, in the terms the pattern was declared for.

    The one in use asks for a character that is not whitespace, and a caller handed the
    expression back has to work out that this is what it meant.
    """
    pattern = cast("str | None", spec.get("pattern"))
    if pattern is None or re.search(pattern, value) is not None:
        return
    if pattern == NON_BLANK:
        raise ToolError(f"{label} must not be blank", kind=INVALID_ARGUMENTS)
    raise ToolError(f"{label} must match {pattern}", kind=INVALID_ARGUMENTS)


def _check_unique(label: str, value: list[Any], spec: dict[str, Any]) -> None:
    """Enforce ``uniqueItems``. Which entry repeats is in the caller's own array."""
    if not spec.get("uniqueItems"):
        return
    seen: list[Any] = []
    for item in value:
        # Compared by equality rather than through a set: an entry is only required to be
        # JSON, and JSON objects are not hashable.
        if item in seen:
            raise ToolError(f"{label} must not repeat a value", kind=INVALID_ARGUMENTS)
        seen.append(item)


def _refuse_extras(extra: list[str], name: str, schema: dict[str, Any], within: str = "") -> None:
    for key in extra:
        if key in _DEFERRED_ARGS:
            raise ToolError(
                f"{key!r} is not supported: {_DEFERRED_ARGS[key]}", kind=INVALID_ARGUMENTS
            )
    if extra:
        accepted = sorted(_ATTR_KEYS) if within else sorted(schema.get("properties", {}))
        where = f"in {within} for tool {name!r}" if within else f"for tool {name!r}"
        raise ToolError(
            f"unknown argument {', '.join(repr(k) for k in extra)} {where}. Accepted: {accepted}",
            kind=INVALID_ARGUMENTS,
        )


def dispatch(
    name: str, arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """Route a tool call by name, or raise ToolError naming the tools that exist."""
    handler = _HANDLERS.get(name)
    if handler is None:
        available = ", ".join(spec.name for spec in TOOLS)
        raise ToolError(f"unknown tool {name!r}. Available tools: {available}", kind=UNKNOWN_TOOL)
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
        raise ToolError(f"unknown tool {name!r}. Available tools: {available}", kind=UNKNOWN_TOOL)
    _check_arguments(name, arguments)
    return await handler(arguments, source)
