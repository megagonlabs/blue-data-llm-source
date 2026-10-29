"""MCP tool schemas, argument validation, and library calls.

The server owns the ``mcp`` import, allowing these tools to run without a transport.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from blue_data_llm_source import (
    BudgetExceeded,
    ConfigError,
    Estimate,
    Limits,
    LlmSource,
    LlmSourceError,
    ParseError,
    QueryError,
    TransportError,
    can_bound,
    meta_view,
    priceable_models,
    published_defaults,
    rates_for,
    redact,
)
from blue_data_llm_source import __version__ as library_version
from blue_data_llm_source.llm.client import check_adapter_keys
from blue_data_llm_source.redaction import MASK

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

`minLength` permits whitespace-only input, which the library strips and refuses.
"""


class ToolError(Exception):
    """A failed tool call with text safe for an MCP caller."""

    def __init__(self, message: str, kind: str = "ToolError") -> None:
        masked = conceal(redact(message))
        super().__init__(masked)
        self.message = masked
        self.kind = kind


def _library_error(error: LlmSourceError) -> ToolError:
    """Keep known failure kinds without publishing library exception text."""
    if type(error) is QueryError:
        if str(error) == "query must not be empty or whitespace":
            return ToolError("query must not be empty or whitespace", kind="QueryError")
        if re.match(r"^attrs\[\d+\] must have a non-empty string 'name', got ", str(error)):
            return ToolError("attrs item must have a non-empty string 'name'.", kind="QueryError")
        return ToolError("Query or attributes are invalid.", kind="QueryError")
    if type(error) is ConfigError:
        message = str(error)
        if message.startswith("plan.leaf_prompt=") and "requires a non-flat plan.mode" in message:
            return ToolError(
                "plan.leaf_prompt needs plan.mode partitioned or row_by_row.",
                kind=INVALID_ARGUMENTS,
            )
        if message.startswith("plan.filter_enforcement='drop' requires a non-flat plan.mode"):
            return ToolError(
                "plan.filter_enforcement drop needs plan.mode partitioned or row_by_row.",
                kind=INVALID_ARGUMENTS,
            )
        if message.startswith("plan.split_policy=") and (
            "requires plan.mode='partitioned'" in message
        ):
            return ToolError(
                "plan.split_policy evidence needs plan.mode partitioned.",
                kind=INVALID_ARGUMENTS,
            )
        if message.startswith("plan.keys ") and message.endswith(
            " are not among the requested attributes"
        ):
            return ToolError("plan.keys must name requested attrs.", kind=INVALID_ARGUMENTS)
        if message.startswith("planning.locked_sequential requires a non-empty caller schema"):
            return ToolError(
                "plan.planning_order planning.locked_sequential needs attrs and ordered plan.keys.",
                kind=INVALID_ARGUMENTS,
            )
        if str(error).startswith("limits.max_cost must be "):
            return ToolError(
                "limits.max_cost must be a finite non-negative number.", kind="ConfigError"
            )
        if str(error).startswith("unknown adapter override "):
            return ToolError(
                "unknown adapter override; check supported settings.", kind="ConfigError"
            )
        return ToolError("Configuration or request settings are invalid.", kind="ConfigError")
    if type(error) is ParseError:
        if str(error).startswith("model response is not valid JSON:"):
            return ToolError("Model response is not valid JSON.", kind="ParseError")
        return ToolError("Model response could not be parsed as records.", kind="ParseError")
    if type(error) is TransportError:
        category = _transport_category(error)
        detail = f" ({category})" if category else ""
        return ToolError(f"Provider request failed{detail}.", kind="TransportError")
    if type(error) is BudgetExceeded:
        return ToolError("Configured limit reached.", kind="BudgetExceeded")
    return ToolError("Library request failed.", kind="LlmSourceError")


def _transport_category(error: TransportError) -> str | None:
    """Classify structured provider errors without publishing provider text."""
    if error.code in {"authentication_error", "invalid_api_key"}:
        return "authentication"
    if error.code in {"rate_limit_error", "rate_limit"}:
        return "rate_limit"
    if error.code in {"model_not_found", "unknown_model"}:
        return "model_not_found"
    if error.code in {"connection_error", "timeout"}:
        return "network"
    provider = error.provider_error
    if provider is None or not type(provider).__module__.startswith("litellm."):
        return None
    kind = type(provider).__name__
    if kind in {"AuthenticationError", "PermissionDeniedError"}:
        return "authentication"
    if kind == "RateLimitError":
        return "rate_limit"
    if kind in {"APIConnectionError", "Timeout"}:
        return "network"
    if kind == "ModelNotFoundError":
        return "model_not_found"
    try:
        status = getattr(provider, "status_code", None)
    except Exception:
        return None
    if type(status) is int:
        if status in {401, 403}:
            return "authentication"
        if status == 429:
            return "rate_limit"
    return None


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
                    # recognizes and accepts any other name unchecked, reporting it once in
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
# do not recognize never reaches it, so the run would go unbounded in silence.
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

# The seven closed sets the library refuses by name, written out here as schema literals: a
# client reads the accepted values out of the advertised schema, so they are part of what
# this server publishes rather than a lookup it performs. A test pins all seven against the
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
LEAF_PROMPTS = ("keyed-leaf-fetch-1", "keyed-leaf-fetch-2")
CHILD_WORDINGS = ("parent", "canonical")
FILTER_ENFORCEMENTS = ("flag", "drop")
SPLIT_POLICIES = ("always", "evidence")
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
            "being dropped. In row_by_row, an entity list past it falls back to one unsplit "
            "ask. Default: 100."
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
            "node unsplit. Default: 20."
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
    "plan.leaf_prompt": {
        "type": "string",
        "enum": list(LEAF_PROMPTS),
        "description": (
            "Leaf prompt contract for non-flat modes. `keyed-leaf-fetch-1` is the default; "
            "`keyed-leaf-fetch-2` asks for every matching record, states the expected count "
            "as a sentence, and allows an empty answer only when nothing matches. Refused in "
            "flat mode."
        ),
    },
    "plan.child_wording": {
        "type": "string",
        "enum": list(CHILD_WORDINGS),
        "description": (
            "How a child part is worded in partitioned mode: `parent` (default) copies the "
            "parent's wording; `canonical` derives it from the request and the child's filters."
        ),
    },
    "plan.filter_enforcement": {
        "type": "string",
        "enum": list(FILTER_ENFORCEMENTS),
        "description": (
            "Records that contradict their part's filter are kept (`flag`, default) or removed "
            "at the part before merging (`drop`). Both count them in "
            "meta.filter_contradictions. `drop` is refused in flat mode."
        ),
    },
    "plan.range_filters": {
        "type": "boolean",
        "description": (
            "Read partition values written as integer spans, on an integer column, as range "
            "filters. Requires partitioned mode and plan.leaf_prompt `keyed-leaf-fetch-2`."
        ),
    },
    "plan.partition_guardrails": {
        "type": "boolean",
        "description": (
            "Send partition proposals with key and value rules and refuse a numeric key "
            "bucketed like a measurement. Requires partitioned mode."
        ),
    },
    "plan.split_policy": {
        "type": "string",
        "enum": list(SPLIT_POLICIES),
        "description": (
            "`always` (default) splits the root of a partitioned plan whatever it holds. "
            "`evidence` asks the root once first and splits only when that answer was cut off "
            "or failed, or holds fewer than half the keys a large estimate expects; "
            "otherwise that answer is the result. Requires partitioned mode."
        ),
    },
    "plan.split_threshold_rows": {
        "type": "integer",
        "description": (
            "Under plan.split_policy `evidence`, the estimate above which a short root answer "
            "splits. Default: 120."
        ),
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
    # Missing or empty queries are valid inputs that produce `empty_query`.
    "required": [],
    "additionalProperties": False,
}

# `estimate` prices a flat fetch without a row cap. Accepting `max_records` here would
# advertise an input that cannot change the estimate.
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
            "limits.max_cost ceiling can be bounded against it on this server. "
            "published_defaults gives the model's published request contract (reasoning "
            "efforts, default effort, when temperature is accepted, output-token parameter), "
            "its cached-input and cache-write rates and its long-context threshold. These are "
            "the model's defaults, which this deployment's settings override. A model the price "
            "table does not carry is a successful answer with priceable false and null "
            "published_defaults, not an error. Contacts no provider and costs nothing."
        ),
        input_schema=MODEL_INPUT_SCHEMA,
    ),
    ToolSpec(
        name=INSPECT_METADATA,
        description="Report the protocol, the variant, and the library version.",
        input_schema=METADATA_INPUT_SCHEMA,
    ),
)


# The request settings a deployment gives this server, read from the process environment
# under the names the CLI profile uses, so one deployment's settings mean the same thing on
# both surfaces. Per-call arguments cannot set these: a model or a price is the operator's
# choice, and a cost ceiling is claimed against it.
_ENVIRONMENT_SETTINGS: tuple[tuple[str, str, str], ...] = (
    ("BLUE_DATA_LLM_MODEL", "llm.model", "str"),
    ("BLUE_DATA_LLM_SOURCE_REASONING_EFFORT", "llm.reasoning_effort", "str"),
    ("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "llm.max_tokens", "int"),
    ("BLUE_DATA_LLM_SOURCE_TEMPERATURE", "llm.temperature", "float"),
    ("BLUE_DATA_LLM_SOURCE_TIMEOUT", "llm.timeout", "float"),
    ("BLUE_DATA_LLM_SOURCE_MAX_PARALLEL", "llm.max_parallel", "int"),
    ("BLUE_DATA_LLM_SOURCE_CACHE", "llm.cache", "bool"),
    ("BLUE_DATA_LLM_SOURCE_CACHE_DIR", "llm.cache_dir", "directory"),
    ("BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN", "llm.price_per_input_token", "float"),
    ("BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN", "llm.price_per_output_token", "float"),
)

# Operator ceilings, under the CLI profile's names. Unlike the settings above they are not
# the source's properties: they are merged with each call's own ceilings, the lower winning,
# so an agent can tighten a ceiling for one call but never raise or remove it.
_OPERATOR_LIMITS: tuple[tuple[str, str, str], ...] = (
    ("BLUE_DATA_LLM_SOURCE_LIMIT_COST", "limits.max_cost", "float"),
    ("BLUE_DATA_LLM_SOURCE_LIMIT_CALLS", "limits.max_calls", "int"),
    ("BLUE_DATA_LLM_SOURCE_LIMIT_SECONDS", "limits.max_seconds", "float"),
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

# What each setting must be, in words that never repeat the value. A deployment setting is
# the operator's, it can hold anything, and an error returned to an MCP caller lands in a
# transcript the operator does not control. Masking credential shapes is not enough there:
# a private value need not look like a key.
_RULES: dict[str, str] = {
    "llm.model": "a model name the configured effort and temperature suit",
    "llm.reasoning_effort": "a reasoning effort the configured model accepts",
    "llm.max_tokens": "a whole number of at least 1",
    "llm.temperature": "a finite number the configured model and effort accept",
    "llm.timeout": "a finite positive number of seconds",
    "llm.max_parallel": "a whole number of at least 1",
    "llm.cache": "true or false",
    "llm.cache_dir": "a directory path",
    "llm.price_per_input_token": "a finite positive number, set together with the output price",
    "llm.price_per_output_token": "a finite positive number, set together with the input price",
    "limits.max_cost": "a finite number of dollars, zero or more",
    "limits.max_calls": "a whole number of calls, zero or more",
    "limits.max_seconds": "a finite number of seconds, zero or more",
}
_VARIABLES = {key: variable for variable, key, _ in (*_ENVIRONMENT_SETTINGS, *_OPERATOR_LIMITS)}
# Checked together, because the library refuses them as a combination rather than alone.
_CONTRACT_KEYS = ("llm.model", "llm.reasoning_effort", "llm.temperature")


def _refused(keys: Sequence[str]) -> ToolError:
    """An error naming the deployment variables behind *keys* and what each must be."""
    parts = [f"{_VARIABLES[key]} must be {_RULES[key]}" for key in keys]
    return ToolError(
        "; ".join(parts) + ".",
        kind=ConfigError.__name__,
    )


def _environment_value(key: str, text: str, kind: str) -> Any:
    """*text* as its setting's type, or a ToolError naming the variable but not the value."""
    if kind == "int":
        try:
            return int(text)
        except ValueError:
            raise _refused([key]) from None
    if kind == "float":
        try:
            number = float(text)
        except ValueError:
            raise _refused([key]) from None
        if key.startswith("llm.price_per_") and not (math.isfinite(number) and number > 0):
            raise _refused([key])
        return number
    if kind == "bool":
        if text.lower() in _TRUE:
            return True
        if text.lower() in _FALSE:
            return False
        raise _refused([key])
    if kind == "directory":
        return str(Path(text).expanduser())
    return text


def deployment_properties(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The ``llm.*`` settings the process environment configures, typed and checked.

    Read on every call rather than once at startup, so a malformed value is reported as the
    failed call it causes instead of stopping the server, and a variable that is unset or
    blank supplies nothing, leaving the library's default in place.

    Checked here with the library's own rules rather than left for the call to refuse,
    because the library's refusal quotes the value it refused, and here that value is the
    operator's. The error names the variable and what it must be instead.
    """
    source = os.environ if environ is None else environ
    properties: dict[str, Any] = {}
    for variable, key, kind in _ENVIRONMENT_SETTINGS:
        text = source.get(variable, "").strip()
        if text:
            properties[key] = _environment_value(key, text, kind)
    try:
        check_adapter_keys(properties)
    except LlmSourceError as error:
        named = [key for key in properties if f"'{key}'" in str(error)]
        if not named or any(key in _CONTRACT_KEYS for key in named):
            named = [*named, *(key for key in _CONTRACT_KEYS if key in properties)]
        raise _refused(list(dict.fromkeys(named)) or list(properties)) from None
    return properties


def operator_limits(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The ceilings the operator set for every call, typed and checked.

    Read on every call, like the settings, so a malformed value fails the call it reaches
    rather than the server, with an error naming the variable and not its value.
    """
    source = os.environ if environ is None else environ
    ceilings: dict[str, Any] = {}
    for variable, key, kind in _OPERATOR_LIMITS:
        if variable not in source:
            continue
        text = source[variable].strip()
        if not text:
            raise _refused([key])
        ceilings[key] = _environment_value(key, text, kind)
    try:
        Limits(
            max_cost=ceilings.get("limits.max_cost"),
            max_calls=ceilings.get("limits.max_calls"),
            max_seconds=ceilings.get("limits.max_seconds"),
        )
    except LlmSourceError as error:
        named = [key for key in ceilings if key.split(".", 1)[1] in str(error)]
        raise _refused(named or list(ceilings)) from None
    return ceilings


def _concealed(environ: Mapping[str, str] | None = None) -> list[str]:
    """Configured text that must not come back to a caller, longest first.

    A model name the package publishes is public, so a configured ``gpt-6-sol`` is reported
    as itself. Any other configured model name, and the cache directory, are the operator's
    and are masked wherever they would appear, in an error or in a result.
    """
    source = os.environ if environ is None else environ
    values: set[str] = set()
    model = source.get("BLUE_DATA_LLM_MODEL", "").strip()
    if model and published_defaults(model) is None:
        values.add(model)
    cache_dir = source.get("BLUE_DATA_LLM_SOURCE_CACHE_DIR", "").strip()
    if cache_dir:
        values.update({cache_dir, str(Path(cache_dir).expanduser())})
    return sorted(values, key=len, reverse=True)


def conceal(text: str) -> str:
    """*text* with every configured private value replaced by the mask."""
    for value in _concealed():
        text = text.replace(repr(value), MASK)
        text = text.replace(json.dumps(value, ensure_ascii=False), MASK)
        text = text.replace(value, MASK)
    return text


def build_source(environ: Mapping[str, str] | None = None) -> LlmSource:
    """Construct the source a tool call runs against, from the deployment's settings.

    Each call gets a fresh source, so what one call merges over these settings, a ceiling
    or a plan setting, is never seen by the next.
    """
    return LlmSource(properties=deployment_properties(environ) or None)


def limit_properties(args: dict[str, Any]) -> dict[str, Any] | None:
    """The ceilings and plan settings in *args*, as per-call properties the library reads.

    Return None when no settings were supplied. Per-call settings do not persist to the
    next call.

    The operator's ceilings from the environment are merged in here, each call's own value
    kept only where it is lower, so no call can raise or remove the operator's bound.
    """
    properties = {key: args[key] for key in LIMIT_KEYS + PLAN_KEYS if key in args}
    # The operator's ceiling binds every call. A call's own ceiling can only tighten it.
    for key, ceiling in operator_limits().items():
        asked = properties.get(key)
        properties[key] = ceiling if asked is None else min(asked, ceiling)
    return properties or None


async def llmdb_query_async(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """Run one fetch and return its records with a compact meta dict.

    Async because the MCP handler that calls it is async. Inside a running loop the
    library's synchronous ``fetch`` blocks the handler's thread while it runs on a worker
    thread, which stalls the server's loop and hangs any client that needs that loop.
    Awaiting ``afetch`` keeps the fetch on the loop.
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
        failure = _library_error(error)
    else:
        return _query_result(result)
    raise failure


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
        failure = _library_error(error)
    else:
        return _query_result(result)
    raise failure


def _query_result(result: Any) -> dict[str, Any]:
    """Records and meta for a tool result, with a privately configured model name masked.

    ``meta.model`` names the model that answered, and a provider commonly answers with the
    name it was asked for, which here is the operator's setting.
    """
    meta = meta_view(result.meta, verbose=True)
    meta["model"] = conceal(str(meta["model"]))
    return {"records": list(result.records), "meta": meta}


def _estimate_reason(reason: str | None) -> str | None:
    """Report why a bound is missing without returning library exception text."""
    if not reason:
        return reason
    if reason.startswith("this client does not report what an attempt will cost"):
        return "This client does not report a cost bound."
    if reason.startswith("no published prices for "):
        return "Model pricing is unavailable; set finite input and output rates."
    if reason.startswith("llm.price_per_input_token and llm.price_per_output_token"):
        return "Set finite positive input and output rates."
    if reason.startswith("limits.max_cost needs a finite positive llm.max_tokens"):
        return "A finite positive output allowance is required to bound cost."
    return "A cost bound is unavailable; check pricing, output allowance, or client cost support."


def estimate_summary(estimate: Estimate) -> dict[str, Any]:
    """Shape Estimate for a tool result: every field, whether or not it is set.

    The two costs are published unrounded, unlike the spend in ``meta_view``. That one is
    a measurement and rounding it loses nothing a caller can act on; these are upper bounds,
    and rounding a bound to six places can round it *down*. An agent that then set
    ``limits.max_cost`` to the figure it was given would have the last attempt that figure
    accounted for refused.

    ``reason`` may contain a library error, so it uses fixed guidance.
    """
    return {
        "bounded": estimate.bounded,
        "max_cost": estimate.max_cost,
        "max_cost_per_attempt": estimate.max_cost_per_attempt,
        "max_attempts": estimate.max_attempts,
        "reason": _estimate_reason(estimate.reason),
        "model": conceal(estimate.model),
        "prompt_bytes": estimate.prompt_bytes,
        "max_output_tokens": estimate.max_output_tokens,
        "attempts_within_ceiling": estimate.attempts_within_ceiling,
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
        failure = _library_error(error)
    else:
        summary = estimate_summary(estimate)
        if query is None or (isinstance(query, str) and not query.strip()):
            summary["reason"] = "empty_query"
        return summary
    raise failure


def llmdb_list_models(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """The model names a cost ceiling can be bounded against, sorted.

    Names only, which is what the library publishes. The price table itself is a mutable
    snapshot of list prices and is not a contract this package can keep across a price
    change; a name is.
    """
    return {"models": list(priceable_models())}


def _model_allowance(target: Any, name: str) -> Any:
    try:
        allowance = target.max_tokens_for_model(name)
    except LlmSourceError as error:
        failure = _library_error(error)
    else:
        return allowance
    raise failure


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
    if not name.strip():
        raise ToolError("model must be a non-blank string.", kind=INVALID_ARGUMENTS)
    target = source if source is not None else build_source()
    rates = rates_for(name)
    # Read off the source rather than assumed, because the question is about this
    # deployment: a cost bound needs a finite output allowance as well as rates, and a
    # source configured without one can price nothing however well known the model is.
    properties: dict[str, Any] = dict(target.properties)
    allowance = _model_allowance(target, name)
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
        # What the table records for the model. The deployment's own settings override it.
        # Null for a model the table does not carry.
        "published_defaults": published_defaults(name),
    }


def llmdb_inspect_metadata(
    arguments: dict[str, Any] | None = None, source: Any | None = None
) -> dict[str, Any]:
    """Report the protocol, the variant, and the library version.

    None of these depends on the deployment's settings, so a plain source answers, and a
    malformed setting that fails every other call still leaves this one able to report the
    version.
    """
    target = source if source is not None else LlmSource()
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
# validation: the handlers read known fields and would otherwise drop an unrecognized key.
_DEFERRED_ARGS = {
    "llm.rate_limit": (
        "client-side call spacing is not implemented here. Use 'llm.max_parallel' to cap "
        "concurrency, or throttle above this server where the whole request sequence is visible"
    ),
}

_SCHEMAS = {spec.name: spec.input_schema for spec in TOOLS}


def _check_arguments(name: str, arguments: object) -> None:
    """Enforce the advertised schema, which nothing else enforces for us.

    Each schema declares ``additionalProperties: false``. Check unknown keys here before
    handlers can discard them. The SDK prevalidator is disabled so unsupported settings
    receive the specific guidance below.
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
        if label.startswith("'attrs'["):
            raise ToolError(
                f"{label} must be a non-blank string or an object with a non-blank "
                "string 'name' and optional string 'type'",
                kind=INVALID_ARGUMENTS,
            )
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
        if isinstance(value, float) and not math.isfinite(value):
            raise ToolError(f"{label} must be a finite integer", kind=INVALID_ARGUMENTS)
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
        if isinstance(value, float) and not math.isfinite(value):
            raise ToolError(f"{label} must be a finite number", kind=INVALID_ARGUMENTS)
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
    synchronous dispatcher here would call ``fetch`` inside a running loop, which blocks
    the server's loop until the fetch finishes on a worker thread.
    """
    handler = _ASYNC_HANDLERS.get(name)
    if handler is None:
        available = ", ".join(spec.name for spec in TOOLS)
        raise ToolError(f"unknown tool {name!r}. Available tools: {available}", kind=UNKNOWN_TOOL)
    _check_arguments(name, arguments)
    return await handler(arguments, source)
