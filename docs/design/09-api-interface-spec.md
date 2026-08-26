# LlmSource — API / Interface Spec

Three external interfaces exist over the core library: the Python library itself, an MCP server, and a CLI, each built as its own package, none of them published yet. A REST API is **not implemented and not scheduled**. No business logic belongs in any interface layer; each is a translation layer over the Python library, and each passes the library's own error messages through unreworded. Authentication and authorisation are left to the deployment layer, and no interface reads or forwards a provider credential.

---

## 1. Python Library

The canonical interface. Every other interface delegates to it. `LlmSource` inherits `blue_data.source.source.DataSource` and remains directly instantiable, since that base class has no abstract members. One method, `afetch`, is a coroutine and must be awaited; everything else, `estimate` included, is synchronous.

The base order matters and is not cosmetic; `LlmSource`'s own implementations come first in the method resolution order, and a test asserts it for every method on the shared surface. See [The Source Base Class](04-detailed-architecture.md#the-source-base-class) for why. Inheriting the base class does not make the source constructible by protocol name: `DataRegistry.connect_source` returns the registered instance and rebuilds one from the recorded class path when the live entry is gone, so the integration route is `register_source` with a built instance and the registration is durable.

### Constructor

```python
class LlmSource:
    def __init__(
        self,
        name: str = "llm",
        properties: dict[str, Any] | None = None,
        client: Any | None = None,
    ) -> None: ...
```

`properties` is merged over the defaults, with the `connection` mapping merged key by key. `client` injects a model client used by every request from this instance; any object with `complete(prompt, properties)` and `acomplete(prompt, properties)` is accepted. A client that additionally implements `budget_capabilities() -> frozenset[str]` satisfies `types.BudgetAwareClient`, receives the run's budget under the `budget` property and a per-call `Usage` list under the `usage_sink` property on every call, and is expected to claim against the budget. Each configured ceiling on an axis the client did not declare is refused with a `ConfigError` naming that ceiling and the axes the client did declare.

### `LlmSource`

```python
class LlmSource:
    name: str
    properties: dict[str, Any]

    @property
    def protocol(self) -> str: ...            # always "llm"
    @property
    def variant(self) -> str: ...             # "openai" unless configured otherwise

    # Library surface
    async def afetch(self, query: Any, attrs: Any = None, context: Any = None, max_records: int | None = None, properties: dict[str, Any] | None = None, llm_fn: Any = None, budget: Budget | None = None) -> FetchResult: ...
    def fetch(self, query: Any, attrs: Any = None, context: Any = None, max_records: int | None = None, properties: dict[str, Any] | None = None, llm_fn: Any = None, budget: Budget | None = None) -> FetchResult: ...
    def estimate(self, query: Any, attrs: Any = None, context: Any = None, properties: dict[str, Any] | None = None, llm_fn: Any = None) -> Estimate: ...

    # Source surface
    def execute_query(self, query: Any, database: str | None = None, collection: str | None = None, optional_properties: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...
    def execute_query_nl(self, query: str, database: str | None = None, collection: str | None = None, schema: dict[str, Any] | None = None, llm_fn: Any = None, optional_properties: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...
    def translate_nl_to_native_query(self, query: str, schema: dict[str, Any] | None, llm_fn: Any) -> str | None: ...

    # Orchestrator tool surface, covered in section 1a
    def get_nl_tools(self, database: str | None = None, collection: str | None = None) -> list[dict[str, Any]]: ...
    def execute_nl_tool(self, name: str, tool_input: dict[str, Any], database: str | None = None, collection: str | None = None, optional_properties: dict[str, Any] | None = None) -> Any: ...
    def execute_query_nl_tools(self, query: str, database: str | None = None, collection: str | None = None, llm_fn: Any = None, optional_properties: dict[str, Any] | None = None, max_iterations: int = 10, extra_tools: list[dict[str, Any]] | None = None, extra_tool_fn: Any = None) -> list[dict[str, Any]]: ...

    # Metadata surface
    def fetch_metadata(self) -> dict[str, Any]: ...
    def fetch_schema(self) -> dict[str, Any]: ...
    def fetch_databases(self) -> list[str]: ...
    def fetch_database_metadata(self, database: str) -> dict[str, Any]: ...
    def fetch_database_schema(self, database: str) -> dict[str, Any]: ...
    def fetch_database_collections(self, database: str) -> list[str]: ...
    def fetch_database_collection_metadata(self, database: str, collection: str) -> dict[str, Any]: ...
    def fetch_database_collection_entities(self, database: str, collection: str) -> dict[str, Any]: ...
    def fetch_database_collection_relations(self, database: str, collection: str) -> dict[str, Any]: ...
    def fetch_source_stats(self) -> dict[str, Any] | None: ...
    def fetch_database_stats(self, database: str) -> dict[str, Any] | None: ...
    def fetch_collection_stats(self, database: str, collection: str, entities: Any = None, relations: Any = None, sample_limit: int | None = None) -> dict[str, Any] | None: ...
    def fetch_entity_stats(self, database: str, collection: str, entity: str) -> dict[str, Any] | None: ...
    def fetch_property_stats(self, database: str, collection: str, entity: str, property_name: str, sample_limit: int | None = None) -> dict[str, Any] | None: ...

    # Write surface
    def create_database(self, database: str, properties: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def create_database_collection(self, database: str, collection: str, properties: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def create_database_collection_entity(self, database: str, collection: str, entity: str, properties: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def create_database_collection_relation(self, database: str, collection: str, relation: str, properties: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def import_data(self, source: Any, database: str = "default", collection: str = "default", entity: str | None = None, mode: str = "append", options: dict[str, Any] | None = None) -> dict[str, Any]: ...

    # Connection
    def connect(self) -> dict[str, Any]: ...
    def disconnect(self) -> None: ...
```

Every statistics method returns `None`, because a model has no row counts. Every write method returns `{}`, because a model has nothing to write to; they exist so that a registry walking the full source surface reaches a defined method rather than an `AttributeError`.

`fetch` raises `RuntimeError` when called from inside a running event loop, directing the caller to `afetch`. Both annotate `query` as `Any` rather than `str`, because handling an absent or wrong-typed request is part of the contract: an absent one is answered with an empty result, a wrong-typed one with a readable `QueryError`. Annotating `str` would describe a narrower contract than the one that ships.

### Request arguments

| Argument | Type | Accepted forms |
|----------|------|----------------|
| `query` | `Any`, normalised to `str` | A natural language request. `None`, an empty string, or whitespace is an empty ask and returns an empty result with `outcome` set to `empty_query`; any other non-string raises `QueryError` |
| `attrs` | list | Names (`["country", "accession_year"]`), `Attr` instances, or mappings (`[{"name": "country", "type": "string"}]`) |
| `context` | `str` or list of `str` | Additional text; its documented use is to say what an attribute name means |
| `max_records` | `int` | A positive integer; replaces the row limit for this run |
| `properties` | mapping | Per-call configuration, merged over the instance properties for this call and not written back. A `limits.*` key here bounds this request alone, which is the primary way to cap what one request may spend |
| `llm_fn` | callable | A `(prompt: str) -> str` callable used as the model client for this run |
| `budget` | `Budget` | A budget the caller owns. Supplied, this fetch derives a child from it and the ceilings accumulate across every call the budget is handed to; omitted, one is built from the configured limits for this call alone. The secondary route, for one total across several requests rather than a bound on each |

### Op codes for `execute_query`

When `execute_query` receives a mapping, its `op` key selects the operation.

| Op | Reads from the mapping | Returns |
|----|------------------------|---------|
| `LLM.FETCH` | `query`, `attrs` or `attr_names`, `context`, `max_records` | The record list |
| `LLM.MODELS` | — | `[{"protocol": "llm", "variant": "openai"}]` |
| `LLM.ESTIMATE` | `query`, `attrs`, `context` | `[{"bounded": true, "max_cost": 0.12924, "reason": null, "model": "gpt-4o", "prompt_bytes": 784, "max_output_tokens": 4096, "max_attempts": 3, "max_cost_per_attempt": 0.04308}]` |

An unrecognised op raises `QueryError` listing the supported ones. A plain string is treated as the request itself and runs a fetch. `LLM.ESTIMATE` reaches `estimate` with `optional_properties` as its properties and contacts nothing; the one-record list is the shape this surface returns, and a caller wanting the `Estimate` object itself calls the method.

`optional_properties` is also where a ceiling goes on this surface. Keys the op recognises become arguments, and everything left is merged over the instance properties as per-call configuration, so a `limits.*` key there bounds this call and the next call through the same source is unbounded again.

### Configuration properties

Properties may be set on the instance or per call, with per-call values winning.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `connection.variant` | `str` | `openai` | Implementation within the `llm` family |
| `connection.host`, `connection.port`, `connection.user`, `connection.password` | — | empty | Present for connection-shape compatibility; unused by this source |
| `llm.model` | `str` | `gpt-4o` | Model name, forwarded to the provider |
| `llm.temperature` | `float` | `0` | Forwarded to the provider |
| `llm.max_tokens` | `int` | `4096` | Forwarded to the provider |
| `llm.stream` | `bool` | `False` | Forwarded to the provider. `True` is accepted for compatibility with provider configurations and proxies that require a stream; it does not make a fetch faster, and a stream the provider sends no usage with cannot be priced, so it reports `accounting_known=False`. See [streaming](../../lib/README.md#streaming) |
| `llm.timeout` | `float` | provider default | Per-call timeout, forwarded to the provider. When `limits.max_seconds` is set, the value actually sent is the smaller of this and the wall-clock time the run has left |
| `llm.cache` | `bool` | off | Enables the `diskcache` response cache; requires the `llm` extra |
| `llm.cache_dir` | `str` | `.llm_cache` | Directory for that cache |
| `llm.max_parallel` | `int` | `8` | Concurrency bound per event loop |
| `llm.rate_limit` | — | — | **Not supported.** Refused with `ConfigError`; deferred to v2.0. Use `llm.max_parallel`, or throttle above the library |
| `cache_store` | `CacheStore` | none | A cache with `get` and `set`, taking precedence over `llm.cache`. `MemoryCache` and `FileCache` ship for this slot, and any object with the two methods is accepted |
| `limits.max_retries` | `int` | `2` | Retries on a transport failure; the client makes this many plus one attempt, narrowed to the smallest depth along the budget chain, and reserves budget for each |
| `limits.max_rows` | `int` | unlimited | Row ceiling; overridden by a `max_records` argument. Enforced by the source itself, so it binds any client |
| `limits.max_seconds` | `float` | unlimited | Time ceiling on admission and cooperative waits, not on work that never yields; requires a client declaring `Capability.SECONDS`. See [what it bounds](../../lib/README.md#what-the-time-ceiling-actually-bounds) |
| `limits.max_cost` | `float` | unlimited | API cost ceiling, claimed against a computed upper bound before each attempt; requires a client declaring `Capability.COST` |
| `limits.max_calls` | `int` | unlimited | Provider call ceiling, counted per attempt including retries; requires a client declaring `Capability.CALLS` |
| `budget` | `Budget` | set per call | Reserved. The source writes the run's budget here on every call, so a caller setting it is overwritten |
| `usage_sink` | `list[Usage]` | set per call | Reserved. The source writes a fresh list here on every call and reads this call's token counts and model name from it alone |

An `llm.*` key outside the accepted set raises `ConfigError` listing the accepted ones, and so does a `limits.*` key outside the set above, along with the near misses `limit.*` and a bare `limits`. Every `limits.*` value is validated as the ceiling is built: a wrong type, a `bool`, a negative number, or a non-finite number raises `ConfigError` naming the field, because a `NaN` ceiling would read as configured and enforce nothing.

The three ceilings marked as requiring a capability are refused with a `ConfigError` when the resolved client does not declare that axis, because reporting a ceiling that is not holding anything is worse than declining to hold it. `DefaultLlmClient` declares all four axes. `FunctionClient`, which wraps an `llm_fn`, declares rows alone, so a call, cost, or time ceiling on a run that supplies `llm_fn` is refused rather than settled at zero or counted against an invocation that may be any number of provider calls.

`limits.max_cost` has one further requirement, since a ceiling needs a number to claim. The bound is the prompt's length in UTF-8 bytes plus a framing allowance at the model's published input rate, plus the whole `llm.max_tokens` allowance at its output rate, and it is refused with a `ConfigError` when the model has no published price and none was supplied, or when `llm.max_tokens` is not a finite positive number. The floor of that bound is the output allowance alone, which for the default `gpt-4o` and 4096 tokens is about $0.041, so a smaller ceiling refuses every call before one is made. `estimate` reports the same bound for a request in hand, which is how a caller sizes a ceiling against the request it is going to bound rather than against an example.

### Result types

```python
@dataclass(frozen=True)
class Attr:
    name: str
    type: str | None = None

@dataclass
class Usage:
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    latency_seconds: float = 0.0
    cache_hit: bool = False
    cost_known: bool = True

@dataclass
class ExecMeta:
    outcome: str = Outcome.OK
    model: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    latency_seconds: float = 0.0
    cache_hits: int = 0
    requested_attrs: list[str] = ...
    observed_keys: list[str] = ...
    unmapped_keys: list[str] = ...
    remapped_keys: dict[str, str] = ...
    type_mismatches: list[dict[str, Any]] = ...
    key_collisions: list[dict[str, Any]] = ...
    unchecked_types: list[str] = ...
    truncated_to: int | None = None
    stopped_by: str | None = None
    accounting_known: bool = True

@dataclass
class FetchResult:
    records: list[dict[str, Any]] = ...
    meta: ExecMeta = ...

@dataclass
class Estimate:
    bounded: bool = False
    max_cost: float | None = None
    reason: str | None = None
    model: str = ""
    prompt_bytes: int = 0
    max_output_tokens: int = 0
    max_attempts: int = 1
    max_cost_per_attempt: float | None = None
```

`afetch` and `fetch` return a `FetchResult`, which supports `len()` and iteration over its records. `execute_query` and `execute_query_nl` return the record list alone, because the surface they match returns records.

`estimate` returns an `Estimate`, and `LLM.ESTIMATE` returns the same fields as a one-record list. `max_cost` is an upper bound rather than a prediction, computed by the same code that bounds a reservation, so an estimate and the ceiling that enforces it are one figure. `bounded` is `False` when no bound can be established, and `reason` then carries what is missing; that is a normal answer rather than an error, because a caller asking what a request might cost is entitled to hear that this package has no price for the model. `prompt_bytes` and `max_output_tokens` report what the bound was built from, and `model` is the one that will actually answer — an injected client's own configuration outranks this source's defaults. `max_cost` is the whole fetch: `max_cost_per_attempt` accumulated `max_attempts` times, the same way the ledger accumulates it, so a ceiling set to exactly `max_cost` admits every attempt the figure accounted for. Sizing against the per-attempt figure instead means one transient failure consumes the ceiling and the retry is refused. An empty request estimates at zero, since it never reaches a model. `Estimate` is exported from the package root.

### Outcome

An empty result is a valid answer from this source rather than a failure. `ExecMeta.outcome` carries the reason, from a closed vocabulary declared as `types.Outcome`.

| Value | Set when |
|-------|----------|
| `ok` | Records were produced and no limit intervened |
| `empty_query` | The request was absent, empty, or whitespace, so no model was called |
| `model_returned_none` | The model returned no records at all |
| `filtered_to_empty` | The model returned records and reduction left none, which happens when no record answered any requested attribute |
| `stopped_by_limit` | A ceiling ended the run and left no usable result, whether it refused the attempt before the provider was reached, emptied the result through the row cap, or was found crossed once the work was done |
| `truncated` | The row ceiling cut the result short and records remain |

The field rides on `FetchResult`, so it is available through `fetch` and `afetch` and not through `execute_query` or `execute_query_nl`, which return `list[dict]` by the contract of the surface they match. A caller on the source surface that needs the reason for an empty list uses the library surface instead.

`Outcome` is defined in `blue_data_llm_source.types` and re-exported from the package root. The precedence is one ordered rule in `source.decide_outcome`, and a `stopped_by` on any axis outranks what the run produced: the row cap yields `truncated` when records remain and `stopped_by_limit` when none do, and every other axis yields `stopped_by_limit`. `filtered_to_empty` and `model_returned_none` therefore describe runs where no ceiling fired at all.

### Type mismatches

`ExecMeta.type_mismatches` lists every value that would not coerce to the type its attribute declared. The record is still returned and the value is still present, uncoerced, so the caller keeps the data and learns which field is not what it asked for.

| Key | Type | Description |
|-----|------|-------------|
| `record_index` | `int` | Position of the record in the returned list |
| `attribute` | `str` | The requested attribute whose value did not fit |
| `declared_type` | `str` | The declared type, lowercased |
| `value` | `str` | The repr of the value as it arrived, before coercion |

### Key collisions

`ExecMeta.key_collisions` lists the returned keys whose values were not used because another key had already answered the same requested attribute. The first key to arrive keeps the attribute, because letting the later one win would make the surviving value depend on the order the model emitted its keys in, and which spelling it meant is not knowable here. The ignored value is reported rather than discarded, so a caller that can decide still has both.

| Key | Type | Description |
|-----|------|-------------|
| `record_index` | `int` | Position of the record in the returned list |
| `attribute` | `str` | The requested attribute both keys reached |
| `kept_key` | `str` | The returned key whose value is in the record |
| `ignored_key` | `str` | The returned key whose value was not used |
| `ignored_value` | any | The value that key carried |

Two *requested* attributes whose fuzzy forms coincide are a different case and are not refused: the fuzzy index drops the entry it cannot separate, so exact keys still match and a key that matches only fuzzily is reported unmapped rather than assigned by guess. Only the same name requested twice raises `QueryError`.

### Exceptions

All inherit from `LlmSourceError`, defined in `types.py`.

| Exception | Raised when |
|-----------|-------------|
| `ConfigError` | An unknown `llm.*` override, an unknown or invalid `limits.*` key, a missing model, a requested cache backend that is not installed, a ceiling configured on an axis the resolved client does not declare, or a cost ceiling that cannot be bounded because the model has no published price or the output allowance is not a finite positive number |
| `QueryError` | A malformed request or argument, an unknown op, or the same attribute is requested twice, including two identical spellings |
| `TransportError` | A provider call fails, or a response cannot be materialised, has no choices, or has no message content |
| `ParseError` | A model response cannot be read as a list of records |
| `BudgetExceeded` | A configured limit would be crossed by the next call |

`BudgetExceeded` raised during the fetch path is caught and converted into an empty result whose `meta.stopped_by` names the axis and whose `meta.outcome` is `stopped_by_limit`. An error already in the hierarchy reaches the caller unchanged. Anything else escaping `fetch`, `afetch` or `estimate` is wrapped in `LlmSourceError` with the original as `__cause__`, which is what makes `except LlmSourceError` sufficient. A `BaseException` is deliberately not caught.

---

## 1a. Orchestrator tools — four of the eight

`DataSource` offers `get_nl_tools`, `execute_nl_tool` and `execute_query_nl_tools`, a tool-calling interface for an orchestrating model. It is built around the relational shape the rest of the data layer has: databases, collections, columns, row samples, foreign keys. A source answering from a model's own knowledge has only part of that, so four of the eight tools are advertised and four are not.

| Tool | Served | Behaviour |
|------|--------|-----------|
| `list_databases` | yes | `["llm"]`, the one virtual database |
| `list_collections` | yes | `["public"]`, the one virtual collection |
| `get_schema` | yes | `{"entities": {...}, "relations": {}}` for the virtual entity |
| `execute_query` | yes | `execute_query(tool_input["query"], ...)`, the one tool that reaches a provider |
| `get_metadata` | no | Would return `{}` |
| `get_column_stats` | no | Would return `None` |
| `get_relations` | no | Would return `{}` |
| `get_sample` | no | Raises `TypeError`: the base dispatcher passes `row_sample_size` to `fetch_entity_stats`, which takes no such argument |

A tool that answers with an empty result and no explanation is worse than one that is absent, because the model calling it has no way to tell "nothing here" from "wrong question". The four unserved ones are therefore filtered out of the advertisement rather than answered.

`get_nl_tools` returns the base class's own definitions for the four, filtered by name. They are not rewritten here: the base dispatcher is what reads the input, so a schema written locally would be a second copy of its contract, and a key renamed or a `required` entry added upstream would leave this source advertising a call the dispatcher then rejects. `database` and `collection` stay in the schemas as upstream declares them, and the dispatcher reads each from the tool input with a fallback to the argument of the same name.

`execute_nl_tool` serves those four by delegating to the base dispatcher, and refuses any other name with a `ConfigError` listing the ones that are available. Nothing on this path leaves the `LlmSourceError` hierarchy: an unknown name, a `name` that is not a string, a `tool_input` that is not a mapping, and a missing required key all arrive as `ConfigError`, `QueryError`, or `LlmSourceError`, which the base's own `ValueError`, `KeyError`, and `AttributeError` do not.

Ceilings reach the `execute_query` tool the way they reach the query surface. A `limits.*` key in `optional_properties` is handed to `execute_query` by the dispatcher and binds that call and no other; a misspelled one is refused on every tool rather than only on the one that spends.

`execute_query_nl_tools` is not served and raises `ConfigError`. It drives a loop of up to ten provider calls that this source's ceilings do not govern, so serving it would place an unbounded spend path behind a method this package never chose to offer. `execute_query_nl` is the natural-language surface here, and its spend and time ceilings are enforced.

## 2. MCP Server

Package `blue-data-llm-mcp`, entry point `blue-data-llm-mcp`, speaking MCP over stdio. It exposes the source to LLM agents as three tools. Beyond moving values across the boundary it holds one piece of logic of its own: it enforces the schema it advertises.

Argument validation is split by kind. The server checks shape against the published JSON schema, before the library sees the call: that the arguments are an object, that no key outside the advertised set appears at any depth, that a declared object carries its required keys, that each value matches its declared JSON type, and that a numeric value clears its declared `minimum`. It does this itself rather than leaving it to the SDK, whose prevalidator is switched off for this handler — running first, it answered a withdrawn key such as `llm.rate_limit` with a generic schema error instead of the message naming what to use instead, so the server's own checker has to do the whole job. The library keeps domain and configuration validation: whether a query is empty, whether an `llm.*` override is a name it accepts, whether a ceiling is a finite positive number the resolved client can be held to, whether a model can be priced, and whether the model's answer parses.

That split shows in the messages, which matters here in particular: an agent supplies arguments programmatically and sees only the exception text as its tool result. A library rejection is passed through unchanged, so it reaches the agent naming the offending value and the accepted form in the library's own words. A shape rejection is the server's own message and names the argument, what arrived, and what the schema declares.

The low-level server is used rather than the schema-deriving one, because the tool schemas are written by hand. Deriving a schema from a handler signature would put the contract in a Python signature instead of in the JSON a client reads.

### Tool: `llm_fetch`

```json
{
  "name": "llm_fetch",
  "description": "Fetch structured records from a language model's own knowledge. Returns a list of records and a meta object describing the call.",
  "inputSchema": {
    "type": "object",
    "required": [],
    "additionalProperties": false,
    "properties": {
      "query": {
        "type": "string",
        "description": "What to ask the model for, in plain language."
      },
      "attrs": {
        "type": "array",
        "description": "Attributes every record must carry. Omit to let the model choose keys.",
        "items": {
          "oneOf": [
            { "type": "string", "description": "Attribute name." },
            {
              "type": "object",
              "description": "Attribute name with a declared value type.",
              "required": ["name"],
              "additionalProperties": false,
              "properties": {
                "name": { "type": "string", "description": "Attribute name." },
                "type": { "type": "string", "description": "Declared type: int, float, bool, date, or text." }
              }
            }
          ]
        }
      },
      "context": {
        "type": "string",
        "description": "Extra text that clarifies the query or an attribute name."
      },
      "max_records": {
        "type": "integer",
        "minimum": 1,
        "description": "Stop after this many records."
      },
      "limits.max_cost": {
        "type": "number",
        "minimum": 0,
        "description": "Refuse a call that would take this run's spend past this many dollars. The output allowance is reserved in full, so llm_estimate reports the floor a ceiling has to clear."
      },
      "limits.max_seconds": {
        "type": "number",
        "minimum": 0,
        "description": "Refuse a call once the run has been going this long."
      },
      "limits.max_calls": {
        "type": "integer",
        "minimum": 0,
        "description": "Refuse more than this many provider calls, retries included."
      }
    }
  }
}
```

The three ceilings are the library's own property names, unrenamed. They are passed through as the call's properties, which is the same route `optional_properties` takes on the query surface and `--limit-*` takes on the command line: merged over the source's properties for this call and not written back, so one agent's ceiling cannot bind the next call over the same server. A key this layer did not recognise would never reach the library's own limit validation, so a translated name would have been dropped in silence rather than refused — which is why there is no translation.

**Returns:** JSON text content, `{"records": [...], "meta": {...}}`. The meta object always carries `outcome`, `model`, `calls`, `prompt_tokens`, `completion_tokens`, `cost`, and `elapsed_seconds`. Two keys are sparse: `accounting_known` appears only when it is false, so its absence means the accounting is known, and `unchecked_types` appears only when the request declared a type this library cannot check. Both are present whether or not verbose output was asked for, because each is a warning rather than detail. It additionally carries `type_mismatches`, `unmapped_keys`, `remapped_keys`, `key_collisions`, `truncated_to`, and `stopped_by` when there is something to report.

An empty result is not an error. A budget stop returns an empty record list with `outcome` of `stopped_by_limit` and `stopped_by` naming the limit. An empty or whitespace `query` returns an empty record list with `outcome` of `empty_query`, and no model is called. An agent can therefore tell a ceiling, an empty ask, and a model that knows nothing apart from each other and from a failure, without parsing a message.

A ceiling reaches the ledger the same way one configured on a source does: claimed before the provider is contacted, so a call that would cross one is refused rather than accounted for afterwards. `max_records` is not one of them and is not counted as one — it caps the rows in the result, not the spend, which is why it reports `truncated` rather than `stopped_by_limit`.

The input schema is the whole of what this tool exposes, and the ceilings in it are per call. The server constructs its source with no properties, so a call that names no ceiling has none: nothing is configured out of band on the agent's behalf, by the client that launched the server or by anything else. A deployment that cannot leave the ceiling to the agent embeds the library instead, or wraps this server in a process that constructs the source itself.

**On error:** a failed tool call whose text is the error kind followed by the library's message, for example `QueryError: attrs[0] is an empty attribute name`.

---

### Tool: `llm_estimate`

```json
{
  "name": "llm_estimate",
  "description": "Bound what one llm_fetch of the same request could cost, without calling the model. Returns an upper bound on the whole fetch, retries included.",
  "inputSchema": {
    "type": "object",
    "required": [],
    "additionalProperties": false,
    "properties": {
      "query": {
        "type": "string",
        "description": "What to ask the model for, in plain language."
      },
      "attrs": {
        "type": "array",
        "description": "Attributes every record must carry. Omit to let the model choose keys.",
        "items": {
          "oneOf": [
            { "type": "string", "description": "Attribute name." },
            {
              "type": "object",
              "description": "Attribute name with a declared value type.",
              "required": ["name"],
              "additionalProperties": false,
              "properties": {
                "name": { "type": "string", "description": "Attribute name." },
                "type": { "type": "string", "description": "Declared type: int, float, bool, date, or text." }
              }
            }
          ]
        }
      },
      "context": {
        "type": "string",
        "description": "Extra text that clarifies the query or an attribute name."
      },
      "limits.max_cost": {
        "type": "number",
        "minimum": 0,
        "description": "Refuse a call that would take this run's spend past this many dollars. The output allowance is reserved in full, so llm_estimate reports the floor a ceiling has to clear."
      },
      "limits.max_seconds": {
        "type": "number",
        "minimum": 0,
        "description": "Refuse a call once the run has been going this long."
      },
      "limits.max_calls": {
        "type": "integer",
        "minimum": 0,
        "description": "Refuse more than this many provider calls, retries included."
      }
    }
  }
}
```

The request half of the schema is the same object `llm_fetch` declares, shared between the two so they cannot drift into pricing one request and running another. The ceilings are there because a call ceiling narrows the attempts a fetch may make and so narrows what a fetch can cost. `max_records` is absent: `estimate` takes no row cap, and advertising an input that cannot change the answer is how a caller comes to believe it asked for something it did not get.

**Returns:** JSON text content carrying the `Estimate` fields, every one of them, set or not: `bounded`, `max_cost`, `max_cost_per_attempt`, `max_attempts`, `reason`, `model`, `prompt_bytes`, `max_output_tokens`.

```json
{
  "bounded": true,
  "max_cost": 0.12924,
  "max_cost_per_attempt": 0.04308,
  "max_attempts": 3,
  "reason": null,
  "model": "gpt-4o",
  "prompt_bytes": 784,
  "max_output_tokens": 4096
}
```

Neither cost is rounded, unlike the spend in a fetch's `meta`. That one is a measurement of what was already paid; these are upper bounds, and rounding a bound to six places can round it down. An agent that set `limits.max_cost` to a figure this tool understated would have the last attempt that figure accounted for refused.

`bounded` is `false` with a `reason` when no bound can be established, which is a successful call rather than an error, for the reason §1 gives for the method. Nothing on this path contacts the provider, so an agent can size a request without paying for the question.

---

### Tool: `llm_info`

```json
{
  "name": "llm_info",
  "description": "Report the protocol, the variant, and the library version.",
  "inputSchema": {
    "type": "object",
    "description": "No inputs.",
    "properties": {},
    "additionalProperties": false
  }
}
```

**Returns:** JSON text content, `{"protocol": "llm", "variant": "openai", "version": "1.1.1"}`.

Requesting a tool that does not exist returns `UnknownTool` and names the ones that do. Nothing in either tool exposes a credential, a provider endpoint, or the contents of a configured property beyond protocol, variant, and version.

---

## 3. CLI

Package `blue-data-llm-cli`, entry point `llm`. Built with Click for argument parsing and Rich for output, wrapping the library's synchronous `fetch`. Rich markup is disabled on output, because a record value containing square brackets is data rather than a style tag.

### Global options

```
llm [--verbose] COMMAND
```

| Option | Description |
|--------|-------------|
| `--verbose` | Adds the keys the model returned that no requested attribute matched, the keys it renamed on the way in, the run's outcome, the limit that stopped it, and one line per value that would not coerce to its declared type |

`--verbose` is on the group rather than the command, so it precedes the subcommand. The outcome line always appears under `--verbose`, since every run has one. The others appear only when there is something to report.

### `llm fetch`

```
llm fetch QUERY [--attr NAME[:TYPE]]... [--context TEXT] [--max-records N]
                [--model MODEL] [--limit-cost AMOUNT] [--limit-seconds N] [--limit-calls N]
                [--format table|json]
```

| Argument / Option | Description |
|-------------------|-------------|
| `QUERY` | Natural language request (required) |
| `--attr NAME[:TYPE]` | Requested output attribute; repeat for several. A bare name shapes the output; a `NAME:TYPE` pair also drives coercion. Types understood: `int`, `float`, `bool`, `date`, and `str`/`string`/`text`, which is checked rather than converted. Any other name is accepted, checks nothing, and is reported in `unchecked_types` |
| `--context TEXT` | Extra text that clarifies the query, usually what an attribute name means |
| `--max-records N` | Stop after this many records |
| `--model MODEL` | Model to call |
| `--max-tokens N` | Output allowance for the call. The lever on the cost floor, since a cost ceiling's floor is this allowance priced at the model's output rate |
| `--rate-limit N` | Refused at the front door by Click as a bad parameter, exit 2, with a message naming the deferral. Deliberately a usage error rather than a `ConfigError`: the flag is rejected before a source is built, so nothing is configured to be wrong |
| `--limit-cost`, `--limit-seconds`, `--limit-calls` | Per-run ceilings on the cost, time, and call axes, claimed before the call rather than reported after it. `--limit-cost` must exceed the output allowance at the model's output rate, which for the default model and 4096 output tokens is about $0.041, or every call is refused |
| `--format` | `table` (default, via Rich) or `json` |

**Example**

```
$ llm fetch "the member states of the European Union" --attr country --attr accession_year:int

┏━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ country ┃ accession_year ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ France  │ 1958           │
│ Germany │ 1958           │
└─────────┴────────────────┘
2 records | model gpt-4o | 1 call | 46 tokens | $0.0012 | 0.31s
```

Under `--format json` the same run prints one object with `records` and `meta`, for piping. That `meta` always carries `outcome`, `model`, `calls`, `prompt_tokens`, `completion_tokens`, `cost`, and `elapsed_seconds`. Two keys are sparse and appear without `--verbose`, because each is a warning rather than detail: `accounting_known` only when it is false, so its absence means the accounting is known, and `unchecked_types` only when the request declared a type this library cannot check. `--verbose` adds `unmapped_keys`, `remapped_keys`, `stopped_by`, `type_mismatches`, and `key_collisions` when there is something to report.

### `llm info`

```
llm info
```

Prints the protocol, the variant, and the version of the library underneath.

There is no `llm estimate`. The library's `estimate` has no command over this interface, so a person sizing a request before running it reaches the library directly.

### Exit codes

Every library error is caught and printed to stderr as one line. No traceback reaches the terminal. Click keeps code 2 for usage errors, so the library codes start at 3, which lets a caller branch on the cause without parsing the message.

| Code | Cause |
|------|-------|
| 0 | Success |
| 1 | An `LlmSourceError` outside the five classified kinds |
| 2 | Usage error: unknown option, bad value, missing argument (Click's own code) |
| 3 | `QueryError` |
| 4 | `ConfigError` |
| 5 | `TransportError` |
| 6 | `ParseError` |
| 7 | A ceiling ended the run: `meta.outcome` is `stopped_by_limit` and `meta.stopped_by` names the axis. A row cap is not a failure and stays 0, since it leaves a usable result |

An empty query is not one of these. It exits 0 and prints `(no records)`, with `outcome: empty_query` under `--verbose`, because the library treats an empty ask as an empty answer rather than an error.

An absent provider credential is not code 4. The library never reads a credential, so a missing one is not a setting it can find missing; the provider SDK reports it as a call that failed, which becomes a `TransportError` and exits 5. Code 4 covers the configuration this package does check, such as an unknown override, an invalid ceiling, or a ceiling the resolved client cannot hold.

Under `--format json` the output is not clean JSON when the default client is in use. The provider library writes its own banners to standard output on some paths, and they land in the same stream as the JSON object, so a pipe into a JSON reader can fail on text this package did not write. Redirecting standard error does not help, because the banners are on standard output.

