# LlmSource — Library

> External dependencies: `blue-data` provides the `DataSource` base class this source inherits, and is a core dependency; it brings `pandas`, `numpy`, and `jsonpath-ng` into the install. `tenacity`, also core, provides the retry policy the client builds around provider transport. `litellm` and `diskcache`, from the `llm` extra, provide provider transport and the cache the `llm.cache` switch opens; both are imported lazily. The two cache stores in `cache/` need none of these.

---

## LlmSource

This class is the package entry point. It inherits `blue_data.source.source.DataSource` and can be instantiated directly because the base class has no abstract members. It holds connection properties, resolves a model client for each request, and delegates rendering, parsing, alignment, and verification to other modules.

A contract test checks that every shared method resolves to `LlmSource`. This protects the required base-class order; reversing it would select the empty `DataSource` implementations. See [The Source Base Class](04-detailed-architecture.md#the-source-base-class) for the affected methods.

Inheriting the base class does not make the source constructible by protocol name. `DataRegistry.connect_source` returns the registered instance or rebuilds one from the recorded class path when the live entry is gone. `DataRegistry.register_source` takes a built instance and reads only `name` and `properties`.

### `LlmSource` constructor

```python
LlmSource(
    name: str = "llm",
    properties: dict[str, Any] | None = None,
    client: Any | None = None,
)
```

`properties` holds the `connection` mapping and caller-supplied values. The package's defaults (`llm.model`, `llm.stream`, and the model's effort and allowance) are applied when call properties are resolved, beneath an injected client's configuration. They are not stored here, because a source rebuilt from `properties` treats every key as caller-supplied. A `"connection"` mapping is merged key by key, allowing a caller to set `variant` without restating host, port, user, and password. After each merge, `protocol` is reset to `llm` because it names the source family and is not caller-settable.

`client` injects a model client for every request made by this instance. Any object with `complete(prompt, properties)` and `acomplete(prompt, properties)` is accepted; its class is not checked. A client that also implements `budget_capabilities()` receives the run's budget under the `budget` property on every call and is held to the axes that method names. Every configured ceiling on an undeclared axis is refused with a `ConfigError` listing the ceiling, so callers are not led to believe an unenforced limit applies.

### Example

```python
import asyncio
from blue_data_llm_source import LlmSource

source = LlmSource(properties={"llm.model": "gpt-4o", "limits.max_cost": 0.25})

# Library surface, synchronous
result = source.fetch(
    "the member states of the European Union",
    attrs=[
        {"name": "country", "type": "string"},
        {"name": "accession_year", "type": "int"},
    ],
)
print(len(result), result.meta.model, result.meta.calls, result.meta.cost)


# Library surface, asynchronous
async def main() -> None:
    result = await source.afetch(
        "Show me 3 presidents of the United States",
        attrs=["name", "year", "party"],
        max_records=3,
    )
    for record in result.records:
        print(record)


asyncio.run(main())

# Source surface
source.execute_query(
    {
        "op": "LLM.FETCH",
        "query": "the member states of the European Union",
        "attr_names": ["country"],
    }
)
source.execute_query({"op": "LLM.MODELS"})
source.execute_query(
    {"op": "LLM.ESTIMATE", "query": "the member states of the European Union"}
)
source.execute_query_nl("the member states of the European Union", llm_fn=my_model_call)

# A ceiling on one request, which does not outlive it
source.execute_query(
    {"op": "LLM.FETCH", "query": "commercial airports in France", "attrs": ["name"]},
    optional_properties={"limits.max_rows": 20, "limits.max_cost": 0.10},
)

# What the request would cost, before running it
sized = source.estimate("commercial airports in France", attrs=["name"])

# The allowance an inspected model would use
allowance = source.max_tokens_for_model("gpt-4o")
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `name` | `str` | Source name, as the registry records it |
| `properties` | `dict[str, Any]` | Configuration, including the `connection` mapping |
| `protocol` | `str` (read-only) | Always `llm` |
| `variant` | `str` (read-only) | Implementation within the family; defaults to `openai` |

### Library surface

#### `afetch(query, attrs=None, context=None, max_records=None, properties=None, llm_fn=None, budget=None)`

Fetches records for `query`. Returns a `FetchResult`.

**Parameters**

- `query`: the natural language request. It is annotated `Any` rather than `str`, because handling an absent or wrong-typed request is part of the contract. `None`, an empty string, or whitespace is an empty ask, not a caller mistake, and returns an empty result whose `meta.outcome` is `empty_query`, without a model call. Any other non-string raises `QueryError` naming the type that arrived
- `attrs`: requested output attributes, as a list of names, a list of `Attr`, or a list of `{"name": ..., "type": ...}` mappings
- `context`: additional text, as a string or a list of strings; its documented use is to say what an attribute name means
- `max_records`: a positive integer cap on returned records; replaces the row limit for this run
- `properties`: per-call configuration merged over the instance properties for this call only. A `limits.*` key here bounds this request and is not written back, which is the primary way to cap what one request may spend
- `llm_fn`: a `(prompt: str) -> str` callable used as the model client for this run
- `budget`: a `Budget` the caller owns. Supplied, this fetch derives a child from it, so the ceilings accumulate across every call the budget is handed to and this fetch can never spend more than it has left. Omitted, one is built from the configured limits for this call alone, so each fetch is bounded on its own. This is the secondary route, for a caller that wants one total across several requests rather than a bound on each

**Algorithm**

1. Validate and normalise attributes, context, and `max_records` through `checks`.
2. Merge instance properties with `properties`, check the closed property namespaces, assemble
   `Limits`, and validate rule-backed distribution controls. Per-call rules replace source rules.
   Distribution search requires partitioned mode, explicit ordered keys, declared finite domains,
   and a root-applicable rule. Property namespaces, `Limits`, and the distribution controls are
   checked before the request text, so an empty request does not hide a misspelled limit or an
   invalid distribution rule. Mode, schedule, schema-mode, row-target, and estimate-exposure values
   are validated after it.
3. Normalise the request text. Return early when it is empty after stripping, with no records and
   `meta.outcome` set to `empty_query`. No budget or client is constructed, and no model is called.
4. Produce the run's budget, deriving it from `budget` when the caller supplied one. Remove the three
   distribution-search controls from provider properties, then resolve a client: `llm_fn` first,
   then the injected client, then a `DefaultLlmClient` holding the budget.
5. Refuse every configured ceiling whose axis the resolved client does not declare, then place the run's budget in the per-call properties under the key `budget` and a fresh list under the key `usage_sink`.
6. Outside `flat`, build immutable query-analysis, schema, and cardinality facts in the selected `plan.planning_order`. `LlmSource` defaults to `planning.schema_first`; `planning.combined` retains the one-call bootstrap contract for compatibility. Each independent stage has its own prompt and parser, so a malformed result can fall back without another model call. Cancellation and process interrupts propagate.
7. Coordinate the task tree breadth first. Ordinary `partitioned` planning attempts one proposal per
   eligible slice and accepts a split only when every child is valid, the known parent value set is
   preserved, and the whole expansion fits. With distribution search enabled, a bounded set of
   applicable declared strategies is evaluated and `PartitionSafety` must accept containment,
   pairwise disjointness, and coverage before the split is used. `row_by_row` uses an explicit key
   list or a bounded enumeration; if the complete entity plan cannot fit, it keeps one batch leaf.
8. Before an optional planning call, check that the call and one extraction attempt fit current call, cost, and deadline headroom. Locked parallel planning first checks aggregate room for every permitted attempt of both siblings and one extraction attempt. These checks do not reserve budget; each provider attempt still uses the budget's ordinary reservation, so an unrelated concurrent consumer may spend the shared headroom after the check.
9. Run the plan's parts concurrently. A flat leaf calls `execution.arun_leaf` and renders
   `prompts.build_fetch_prompt`. A non-flat leaf crosses the versioned `LeafTask` boundary through
   `execution.local.run_local_leaf`, which renders `prompts.build_leaf_fetch_prompt`. Both
   paths loop up to `plan.max_continuations` times. Every part is awaited, including one that raises,
   so the spend a caller reads is settled before control returns. Records parsed before a late
   failure are retained. A run with no answered response propagates a package error; answered work
   beside failures returns with operational gaps and the appropriate outcome.
10. Reduce the parts through `reduction`: parse, align, verify, and deduplicate the responses, merging on `plan.key` where one was declared. Verification returns the records alongside a list of the values that would not coerce, which becomes `meta.type_mismatches`; alignment supplies the observed, remapped, unmapped, and colliding keys.
11. Offer the records to the budget's row axis, truncating and recording when the ceiling is reached.
12. Fold in accounting and all stop facts, set `meta.stop_reasons` in stable precedence order and `meta.stopped_by` to its first member, classify the final result, stamp elapsed time, and return. Answered extraction followed by interrupted work is `partial`; simultaneous caps remain visible in `stop_reasons`. A distribution-search result from direct Python also carries an optional versioned planning receipt.

The five planning schedules are closed values. `planning.cardinality_first` runs analysis, cardinality, then schema. `planning.schema_first` runs analysis, schema, then cardinality. `planning.locked_sequential` and `planning.locked_parallel` use the caller's non-empty schema and ordered keys, do not ask the model for a schema, and give analysis and cardinality byte-identical independent inputs. The parallel form changes only when those two calls may overlap. `planning.combined` makes the compatible bootstrap call and projects its analysis, schema, and cardinality fields.

`DataTask` holds immutable semantic state for planning. `QueryAnalysisResult`, `SchemaDefinition`, and
`CardinalityEstimate` are the accepted facts, collected in a digestible `PlanningContext`. `LeafTask`
remains the versioned execution message created from the accepted plan. `plan.expose_estimate`
defaults to true and controls whether extraction prompts receive the soft estimate. Setting it false
keeps the estimate in the planning context and `LeafTask`, and does not change the plan or context
digest. The facade applies this setting when it builds execution settings; `coordinate_plan` does not
accept it. Planning affordability checks always price the estimate-bearing prompt, so this display
choice cannot change the accepted topology. `plan.exact_rows` is a hard target only when the plan has
one leaf. A multi-leaf plan does not apply it and adds `exact_rows_not_leaf_bound` to `meta.gaps`.

#### Final outcome classification

Classification uses the final records, raw reduced records, whether any extraction answered, whether work was interrupted, row trimming, and every stop reason. Interrupted work with an answered response is `partial`. Otherwise a non-row stop is `stopped_by_limit`; a row-only trim is `truncated` when records remain. With no stop, records are `ok`, an answered empty response is `model_returned_none`, and records removed by reduction are `filtered_to_empty`.

#### `fetch(query, attrs=None, context=None, max_records=None, properties=None, llm_fn=None, budget=None)`

Synchronous wrapper over `afetch`, with the same parameters and return type. It runs the coroutine with `asyncio.run`, and raises `ConfigError` when called from inside a running event loop, directing the caller to `afetch` instead.

#### `estimate(query, attrs=None, context=None, properties=None, llm_fn=None)`

The most one **flat** fetch of `query` could cost, without contacting the provider. It reads no `plan.*` setting, so a request with parts or continuations can cost several times this figure. Returns an `Estimate`. Synchronous, because there is no I/O to wait on.

**Parameters** are `afetch`'s first three, plus `properties` and `llm_fn`, and mean the same things. There is no `max_records`, because the row cap does not change what a call costs, and no `budget`, because a fresh one is built from the resolved limits rather than taken. `llm_fn` is accepted so the estimate is asked of whatever will actually answer: a client is resolved exactly as a fetch resolves it, and a callable cannot price itself, so a request against one is reported unestimable, not guessed at.

**Algorithm**

1. Resolve the properties in three layers (this source's defaults, then an injected client's own configuration, then whatever the caller set on the source or with the call) and read `llm.model` and `llm.max_tokens` from the result. A default fills a gap; it does not overwrite a client's deliberate choice.
2. Return a bounded estimate of zero for an empty request. It never reaches a model, so it cannot cost anything.
3. Normalise the attributes and context through `checks`, and render the prompt through `prompts.build_fetch_prompt`, the same prompt the fetch path would send for these arguments.
4. Bound that prompt through `pricing.bound_cost`. A `ConfigError` becomes an `Estimate` with `bounded` false carrying the error's text as `reason`, rather than propagating.
5. Accumulate the per-attempt bound once per permitted attempt, in the same order as the ledger, and return an `Estimate` carrying `bounded`, `max_cost`, `max_cost_per_attempt`, `max_attempts`, `model`, `prompt_bytes`, and `max_output_tokens`. Repeated addition is used because multiplication can differ from the ledger by one unit in the last place, enough to refuse the final accounted attempt at an exact ceiling.

The method renders the actual prompt rather than using an average request. It calls `bound_cost` with the same arguments as runtime reservation, so estimation and enforcement use the same value.

For an unpriceable model, `estimate` returns `bounded=False` with a reason instead of propagating the `ConfigError` from `bound_cost`. Callers can inspect why no estimate is available without treating the query as a failed operation.

### `LlmSource` source surface

These methods override those declared by `DataSource`, so an instance exposes the same query surface as other sources in the layer. A signature that drifts from upstream becomes a type error here rather than a runtime failure in an integrator's code.

#### `execute_query(query, database=None, collection=None, optional_properties=None)`

Runs a query and returns `list[dict]`. `query` is either a natural language string or a mapping carrying an `op` key.

| Op | Effect |
|----|--------|
| `LLM.FETCH` | Reads `query`, `attrs` or `attr_names`, `context`, and `max_records` from the mapping and runs a fetch |
| `LLM.MODELS` | Returns `[{"protocol": ..., "variant": ...}]` without a model call |
| `LLM.ESTIMATE` | Returns a one-record list holding an `Estimate` for the same arguments a fetch would take, without a model call |

An unrecognised op raises `QueryError` listing the supported ones. Keys recognised in `optional_properties` become arguments; anything left is passed through as per-call properties. That is how a ceiling travels with a request made through this surface: a `limits.*` key in `optional_properties` is merged over the instance properties for this call and is not written back, so the next call through the same source is unbounded again. `database` and `collection` are accepted for signature compatibility and are unused, since a model has neither.

#### `execute_query_nl(query, database=None, collection=None, schema=None, llm_fn=None, optional_properties=None)`

Runs a natural language query and returns `list[dict]`. Argument extraction matches `execute_query` without op dispatch, `limits.*` keys in `optional_properties` included, so a ceiling reaches this path the same way and binds the one call.

`llm_fn` is the parameter that means something different here than elsewhere. For other sources it translates natural language into a native dialect before execution. This source has no other dialect, so translation is the identity, and the callable, typed `(prompt: str) -> str`, is exactly the shape of a model call. It is therefore used as the model client for this run, with the configured client as the fallback when none is passed.

`schema` is accepted for signature compatibility and is currently unused.

#### `translate_nl_to_native_query(query, schema, llm_fn)`

Returns `query` unchanged, or `None` when it is empty. This is the documented override point for sources needing a different translation strategy; for this source the identity is the correct implementation.

### Orchestrator tool surface

#### `get_nl_tools(database=None, collection=None)`

Returns the base class's own tool definitions for the four tools this source can answer: `list_databases`, `list_collections`, `get_schema`, and `execute_query`. The other four are filtered out. `get_metadata`, `get_column_stats`, and `get_relations` would answer with an empty result and no explanation, which leaves the model calling them unable to tell "nothing here" from "wrong question", and `get_sample` raises a `TypeError` because the base dispatcher passes `row_sample_size` to a `fetch_entity_stats` that takes no such argument.

Filtered, not rewritten, because the base dispatcher is what reads the input. A schema written here would be a second copy of its contract, and the copy is what drifts: a key renamed or a `required` entry added upstream would leave this source advertising a call the dispatcher then rejects. Filtering also fails in the safe direction: a tool added upstream stays unadvertised until someone here has checked that it answers, and one renamed upstream drops out of the advertisement instead of being advertised and then failing when called.

#### `execute_nl_tool(name, tool_input, database=None, collection=None, optional_properties=None)`

Serves the four by delegating to the base dispatcher, and refuses any other name with a `ConfigError` listing what is available. The served set is read from `get_nl_tools`, so what is advertised and what runs cannot diverge.

Nothing on this path leaves the `LlmSourceError` hierarchy. The base dispatcher raises `ValueError` for an unknown name, `KeyError` for a required input it indexes directly, and `AttributeError` for a `tool_input` that is not a mapping, none of which a caller written against this library's documented errors can catch. A missing required key is reported by name, read off the advertised schema instead of listed locally; `database` and `collection` are excluded from that check because the dispatcher falls back to the arguments of the same name.

`execute_query` is the one advertised tool that reaches a provider. It takes a ceiling where the query surface takes one: a `limits.*` key in `optional_properties` is handed to `execute_query` by the dispatcher and binds that call alone. A misspelled ceiling key is refused on every tool rather than only on the one that spends, for the reason `execute_query` checks the key set before reading the op — a key accepted on one call and refused on the next is a ceiling a caller believes they have set.

#### `execute_query_nl_tools(...)`

Raises `ConfigError`. It drives a loop of up to ten provider calls that this source's ceilings do not govern, so serving it would put an unbounded spend path behind a method this package never chose to offer. `execute_query_nl` is the bounded natural-language route, and the refusal names it.

### Metadata surface

A registry walking a source expects a hierarchy of databases, collections, entities, and attributes. A model has none, so one of each is synthesised, named for the family, not for a vendor.

| Method | Returns |
|--------|---------|
| `fetch_metadata()` | `{"protocol": "llm", "variant": "openai"}` |
| `fetch_schema()` | `{}` |
| `fetch_databases()` | `["llm"]` |
| `fetch_database_metadata(database)` | `{}` |
| `fetch_database_schema(database)` | `{}` |
| `fetch_database_collections(database)` | `["public"]` |
| `fetch_database_collection_metadata(database, collection)` | `{}` |
| `fetch_database_collection_entities(database, collection)` | One virtual entity, `llm_entity`, with one text attribute |
| `fetch_database_collection_relations(database, collection)` | `{}` |
| `fetch_source_stats()` | `None` |
| `fetch_database_stats(database)` | `None` |
| `fetch_collection_stats(database, collection, entities=None, relations=None, sample_limit=None)` | `None` |
| `fetch_entity_stats(database, collection, entity)` | `None` |
| `fetch_property_stats(database, collection, entity, property_name, sample_limit=None)` | `None` |
| `connect()` | `{}`; no persistent connection is held, since the client is per call |
| `disconnect()` | `None` |

Every statistics method returns `None`, because a model has no row counts to report.

### Write surface

A model has nothing to write to. These methods exist so that a registry walking the full source surface reaches a defined method rather than an `AttributeError`, and each returns the empty result the surface specifies for an unsupported operation.

| Method | Returns |
|--------|---------|
| `create_database(database, properties=None)` | `{}` |
| `create_database_collection(database, collection, properties=None)` | `{}` |
| `create_database_collection_entity(database, collection, entity, properties=None)` | `{}` |
| `create_database_collection_relation(database, collection, relation, properties=None)` | `{}` |
| `import_data(source, database="default", collection="default", entity=None, mode="append", options=None)` | `{}` |

`import_data` is the one worth a note, because the name suggests a capability that is not here. Materialising model knowledge into a real store is the receiving source's `import_data` with this source as its argument, not this method.

---

## Result and Request Types

Defined in `types.py` so that every module and any platform adapter shares them without circular imports.

### `Attr`

```python
@dataclass(frozen=True)
class Attr:
    name: str
    type: str | None = None
```

One requested output attribute. A bare name shapes the output without constraining the value; a name with a type additionally drives coercion in the verifier.

### `Usage`

```python
@dataclass
class Usage:
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    latency_seconds: float = 0.0
    cache_hit: bool = False
    finish_reason: str | None = None
    cost_known: bool = True
```

Per-call provider accounting, recorded by the client. A cache hit records latency and marks itself, and carries zero tokens and zero cost. `cost` is the reserved bound, not a real figure, when the provider's accounting could not be read, which is the same number the budget was charged.

### `ExecMeta`

```python
@dataclass
class ExecMeta:
    gaps: list[str] = ...
    outcome: str = Outcome.OK
    model: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    latency_seconds: float = 0.0
    cache_hits: int = 0
    requested_attrs: list[str] = ...
    row_estimate: int | None = None
    cardinality_estimate: dict[str, object] | None = None
    primary_keys: list[str] = ...
    observed_key_count: int = 0
    marginal_key_yields: list[int] = ...
    incomplete_key_records: int = 0
    normalization_collisions: list[dict[str, Any]] = ...
    repaired_numbers: int = 0
    observed_keys: list[str] = ...
    unmapped_keys: list[str] = ...
    remapped_keys: dict[str, str] = ...
    type_mismatches: list[dict[str, Any]] = ...
    key_collisions: list[dict[str, Any]] = ...
    truncated_to: int | None = None
    stop_reasons: list[str] = ...
    stopped_by: str | None = None
    unchecked_types: list[str] = field(default_factory=...)
    accounting_known: bool = True
    asked: str | None = None
    record_origins: list[list[str]] = ...
```

What happened during a fetch, alongside the records. `observed_keys` lists every key the model produced, `remapped_keys` maps a returned key to the requested attribute it was matched to, and `unmapped_keys` lists keys with no match. `type_mismatches` lists the values that would not coerce to their declared types. `key_collisions` lists returned keys whose values were not used because another key had already answered the same attribute. `stop_reasons` is always present and retains every observed stop fact; `stopped_by` is its first member. `gaps` is always present and carries supported operational diagnostics, not a completeness result. `requested_attrs` remains the caller declaration.

`cardinality_estimate` is the accepted non-flat bootstrap estimate with `expected`, `lower`, `upper`, `row_unit`, `source`, `parse_status`, and `diagnostics`; `None` means flat mode or that planning did not produce the structure. `row_estimate` is its `expected` value for compatibility. Both are planning evidence, not measured row counts or completeness measures. `primary_keys` is the ordered set of identifying attributes used for execution and reduction. `observed_key_count` counts distinct complete keys in the final returned records, after reduction, deduplication, and the row cap. `marginal_key_yields` records the new normalized key identities from each extraction attempt in leaf order; without primary keys it counts new exact records. `incomplete_key_records` counts distinct per-leaf records missing at least one primary-key value. `normalization_collisions` reports per-leaf key spellings that compare equal after declared-type-aware normalization, with `primary_keys`, `kept`, and `collided` in each entry. `repaired_numbers` counts numbers the model wrote with thousands separators in an object value, such as `4,470`, that were read as `4470`; such a response is not valid JSON and is read only after that one repair. These observations do not establish that all keys or rows were found.

`record_origins` runs parallel to records, and `asked` carries a restated query when one ran.

`calls` and `cost` come from the run's budget rather than from summing the usage entries, because the budget claimed them under a lock and a count taken from a shared usage list can carry another concurrent request's spend. A contacted attempt that failed produced no usage entry and still cost a call, which is the other reason the two are not the same number. The consequence to know is that a client which does not claim against the run's budget reports zero calls and zero cost whatever usage it recorded, which is why a client is refused the ceilings on the axes it did not declare, and is not measured through them.

The token counts and the model name come from the usage entries the client appended to the list handed down under the `usage_sink` property, and from nothing else. The client's own `usages` list is its history across every call it has served, so reading tokens from it lets two concurrent fetches through one reused client each report the other's. A per-call list is private to the fetch that created it, which is the only version of this that survives concurrency.

### `Outcome`

```python
class Outcome:
    OK = "ok"
    EMPTY_QUERY = "empty_query"
    MODEL_RETURNED_NONE = "model_returned_none"
    FILTERED_TO_EMPTY = "filtered_to_empty"
    PARTIAL = "partial"
    STOPPED_BY_LIMIT = "stopped_by_limit"
    TRUNCATED = "truncated"

    ALL = (
        OK,
        EMPTY_QUERY,
        MODEL_RETURNED_NONE,
        FILTERED_TO_EMPTY,
        PARTIAL,
        STOPPED_BY_LIMIT,
        TRUNCATED,
    )
```

The closed vocabulary `ExecMeta.outcome` is drawn from, defined in `types.py`. An empty result is a valid answer here, not a failure, which is how the other sources in this layer treat an absent result, and `outcome` is what keeps the reason from being lost with it.

| Value | Set when |
|-------|----------|
| `ok` | Records were produced and no limit intervened |
| `empty_query` | The request was absent, empty, or whitespace, so no model was called |
| `model_returned_none` | The model returned no records at all |
| `filtered_to_empty` | The model returned records and reduction left none, which happens when no record answered any requested attribute |
| `stopped_by_limit` | A ceiling ended the run and left no usable result, whether it refused the attempt before the provider was reached, emptied the result through the row cap, or was found crossed once the work was done |
| `partial` | Extraction was answered but some work was interrupted, including a failed split leaf beside an answered sibling or a flat leaf that retained records before a late continuation failure |
| `truncated` | The row ceiling cut the result short and records remain; `truncated_to` and `stopped_by` carry the detail |

A caller deciding whether to ask a different source needs `model_returned_none` and `filtered_to_empty` kept apart, and a caller deciding whether to retry needs both kept apart from `stopped_by_limit`. Collapsing them into an empty list throws that away.

`outcome` reaches a caller through `fetch` and `afetch` only. `execute_query` and `execute_query_nl` return the record list alone, because that is what the surface they match returns, so a caller that needs the reason uses the library surface.

Final classification reads all stop reasons. Partial interruption with an answered response reports `partial`; simultaneous caps remain visible in `stop_reasons`. Without interruption, a non-row stop reports `stopped_by_limit`, while a row-only trim reports `truncated` when records remain. `filtered_to_empty` and `model_returned_none` require that no stop fired. `Outcome` is exported from the package root alongside `ExecMeta`.

### `Estimate`

```python
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

What one `flat` fetch could cost at most, returned by `LlmSource.estimate` and by the `LLM.ESTIMATE` op, computed without contacting the provider. It reads no `plan.*` setting, so a split request can cost several times this figure. Exported from the package root alongside `ExecMeta` and `FetchResult`.

`max_cost` is an upper bound, not a prediction. It prices the entire output allowance at the model's output rate and bounds the prompt by its encoded length, so the actual cost is usually lower. The calculation must not underestimate the cost because the same value is used to decide whether a request fits within a configured ceiling.

`bounded` is `False` when no bound can be established at all, and `reason` then carries what is missing, in the words `pricing` used. That is a normal answer rather than an error, because a caller asking what a request might cost is not yet asking for a ceiling, and a model this package has no price for is a fact the caller can act on.

`prompt_bytes` is the encoded length of the prompt that was rendered, and `max_output_tokens` is the output allowance the bound reserved, both reported so a caller can see what the figure was built from rather than having to trust it whole. An empty request produces `bounded=True` with a `max_cost` of zero and both of those at zero, since no prompt was rendered.

### `FetchResult`

```python
@dataclass
class FetchResult:
    records: list[dict[str, Any]] = ...
    meta: ExecMeta = ...
```

Supports `len()` and iteration over its records, so a caller that only wants rows can treat it as a sequence.

The dataclass retains exactly `records` and `meta`. A direct Python distribution-search result exposes an
optional `planning_receipt` property and `with_planning_receipt(...)`; `to_dict()` adds the receipt
only when present. Results without distribution search keep the existing two-key serialization.

### `IdentityPartitionRule` and `PlanningReceipt`

`IdentityPartitionRule` is a closed, versioned finite-domain rule under `planning`. Its wire fields
are `dimension`, `ordered_primary_keys`, `kind`, `allowed_domain`, `parent_scope`, `provenance`, and
`contract_version`. Source rules use `source_configuration`; per-call rules use `caller_declared`.

`PlanningReceipt` is exported from the package root. Its closed wire form carries
`contract_version`, `configured_rule_digests`, `planning_facts`, and `fetch_plan`. Constructor and
deserialization checks keep the counters, accepted decisions, rules, and partitioned plan mutually
consistent. The receipt is integrity evidence for this plan; it does not prove external
authenticity, answer truth, completeness, or quality superiority.

### `LlmClientProtocol`

```python
@runtime_checkable
class LlmClientProtocol(Protocol):
    def complete(
        self, prompt: str, properties: dict[str, Any] | None = None
    ) -> str: ...
    async def acomplete(
        self, prompt: str, properties: dict[str, Any] | None = None
    ) -> str: ...
```

The model boundary. Any object with these two methods can be injected. Both take the prompt first and the merged per-call properties second, and both return the model's text; a client whose signature cannot be called that way is refused when it is resolved, with a `ConfigError` naming the contract, rather than failing later with a raw `TypeError`.

### `Capability`

```python
class Capability:
    CALLS = "calls"
    ROWS = "rows"
    SECONDS = "seconds"
    COST = "cost"
    ESTIMATE = "estimate"

    ALL = frozenset({CALLS, ROWS, SECONDS, COST, ESTIMATE})
```

The axis names a client uses to say what it can be held to. Declared, not inferred: an earlier version accepted any client carrying an attribute named `budget` as evidence that ceilings would bind, which a client with `budget = None` satisfied while making unbounded calls. Attribute presence is not evidence of behaviour.

The axes are separate because what is provable differs between them. A client that drives the provider itself can prove how many calls it made, what each cost, and when to stop; a wrapper around a caller's function can prove only that it invoked the function once, because it cannot know whether that function contacts a provider, what it is billed, or how to interrupt it. Collapsing them into one flag would force such a client to claim all of them or none.

`ESTIMATE` is the odd one and is not a ceiling. It is the client saying it can price an attempt before making it, which is what `LlmSource.estimate` needs before it will return a figure: a client that does not declare it, or does not expose `bound_for_attempt`, produces `bounded` false carrying a reason instead of a number. It rides in `Capability.ALL` because a client that drives the provider itself can do this too, and a client written to the three-method protocol above, returning `ALL` as instructed, therefore gets it without having to know it exists.

### `BudgetAwareClient`

```python
@runtime_checkable
class BudgetAwareClient(Protocol):
    def budget_capabilities(self) -> frozenset[str]: ...
    def complete(
        self, prompt: str, properties: dict[str, Any] | None = None
    ) -> str: ...
    async def acomplete(
        self, prompt: str, properties: dict[str, Any] | None = None
    ) -> str: ...
```

A client that states which ceilings it can be held to, and honours them. `budget_capabilities` is a method, not an attribute, so that satisfying the protocol takes a deliberate implementation rather than a name that happens to match, and it returns the axes the client will enforce against the budget it is handed for a call. A client satisfying this protocol receives the run's budget under the `budget` property on every call and claims against it. `DefaultLlmClient` declares `Capability.ALL`. `FunctionClient` declares rows alone, so a call, cost, or time ceiling against an `llm_fn` is refused instead of settled at zero, which is what an earlier version did and what made a cost ceiling appear to hold over a callable that might be spending anything.

### `CacheStore`

```python
@runtime_checkable
class CacheStore(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any) -> None: ...
```

The optional response cache. A caller supplies a store through the `cache_store` property, and the client drives it with `get` and `set` alone, which is what this protocol declares. Driving it with containment and item access instead would work for the on-disk backend and break any store written to the documented interface, so the narrower two-method access is deliberate.

### Exceptions

All inherit from `LlmSourceError`.

| Exception | Raised when | Carries |
|-----------|-------------|---------|
| `ConfigError` | Configuration is rejected or missing, including an unknown `llm.*` override, an unknown or invalid `limits.*` key, a requested cache whose backend is not installed, a ceiling configured on an axis the resolved client does not declare, and a cost ceiling that cannot be bounded because the model has no published price or the output allowance is not a finite positive number | — |
| `QueryError` | A request or an argument is malformed, an unknown op is dispatched, or the same attribute is requested twice | — |
| `TransportError` | A provider call fails, or a provider response cannot be materialised, has no choices, or has no message content | `code`, `provider_error` |
| `ParseError` | A model response cannot be read as a list of records | `raw`, the response text |
| `BudgetExceeded` | A configured limit would be crossed by the next call | `limit`, the axis that fired |

`ParseError` carries the raw response because a caller debugging one needs it, and re-running the request is not guaranteed to reproduce the failure.

---

## Cache Stores

Two implementations of `CacheStore` ship in `cache/`. Both use the standard library only, so persistence does not depend on the optional provider extra, and neither requires a server.

### `MemoryCache`

```python
MemoryCache(max_entries: int = 512)
```

An in-process cache with least-recently-used eviction. Reading a key moves it to the most-recent end, and writing past `max_entries` evicts the oldest. It is bounded, not unbounded, because the values are provider responses, and a long-running process asking many distinct questions would otherwise grow without limit. A `max_entries` of zero or less raises `ValueError`. `clear()` and `len()` are also available.

### `FileCache`

```python
FileCache(directory: str | os.PathLike[str] = ".llm_cache")
```

Persists one JSON file per entry under `directory`, which is created if absent. A key is hashed with SHA-256 to produce the filename, because cache keys are long serialised request mappings and are not path-safe. A write goes to a temporary file in the same directory and is then moved into place, so a reader never sees a half-written entry. A corrupt, unreadable, or unserialisable entry is treated as a miss and skipped rather than raised, because a cache that can fail the request it was meant to make cheaper is worse than no cache. `clear()` is also available.

Both are passed to the client through the `cache_store` property. The separate `llm.cache` switch opens a `diskcache` cache under `llm.cache_dir`. To use the included file store, pass it as `cache_store`; the two paths remain independent until they are reconciled in code.

---

## DefaultLlmClient

Calls a provider and reports what each call cost.

### `DefaultLlmClient` constructor

```python
DefaultLlmClient(properties: dict[str, Any] | None = None, budget: Budget | None = None)
```

`budget` is supplied by `LlmSource` when it builds the client for a request. Without one, a fresh unlimited `Budget` is used. A budget arriving on a call under the `budget` property wins over both, which is how one client instance serving several concurrent fetches keeps each one's ceiling separate.

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `properties` | `dict[str, Any]` | Instance configuration, merged with per-call properties on every call |
| `budget` | `Budget` | The budget this client reserves against when a call carries none |
| `last_usage` | `Usage \| None` | Accounting for the most recent call |
| `usages` | `list[Usage]` | Accounting for every call made through this client, across every fetch it has served |

`usages` is the client's own history and is not what a fetch reads. When a call carries a `usage_sink` property, this client appends that call's `Usage` to the supplied list as well, and the fetch reads only from there, because one client instance can serve several concurrent fetches and a shared history cannot be sliced back into per-request totals.

### `DefaultLlmClient` methods

#### `DefaultLlmClient.budget_capabilities()`

Returns `Capability.ALL`. This client makes the provider call, so it can enforce every axis. Cost is included because `pricing.bound_cost` either produces a figure that cannot be exceeded or refuses, and the refusal reaches the caller as a `ConfigError` naming what is missing rather than as a ceiling that quietly does not hold.

#### `complete(prompt, properties=None)` / `acomplete(prompt, properties=None)`

Both return the model's message content as a string. The asynchronous form additionally acquires a per-event-loop semaphore bounding concurrency to `llm.max_parallel`.

**Algorithm**

1. Merge instance and per-call properties, then build the provider call arguments.
2. Select a cache and compute the key from the serialised call arguments.
3. On a hit, record usage as a cache hit and return without touching the budget.
4. On a miss, enter the retry loop.
5. For each attempt, cap the provider timeout at the time the budget has left, bound those call arguments through `pricing.bound_cost`, and reserve one attempt and that bound against the budget, which is also where the time limit is rechecked. Then call the provider.
6. On failure, resolve the reservation with `keep_unpriced` and re-raise: the provider was contacted, so the bound remains charged and is not released. A transport failure is retried, and the next attempt reserves again, so a retry sequence cannot outrun the call ceiling.
7. On success, settle the reservation against verified provider cost, which releases whatever the bound overshot by, then store the payload in the cache, record usage, and extract the message content. When the response cannot prove the charge, including a calculator zero for a call that reached the provider, [the cost bound](../lib/README.md#the-cost-bound) sets it and the result reports its accounting as unknown.

The bound is computed only when it will be enforced. `remaining_cost()` returning `None` means no cost ceiling is configured anywhere up the budget chain, and in that case the reservation is made at zero and `pricing` is never consulted, so a run with no cost ceiling is never refused because this package has never heard of its model.

Reservation is per provider attempt, not per operation. Reserving once around the whole retry loop would let a run configured for two retries make three times the calls its limit allows, which is the difference between a limit and a suggestion. The same placement is what makes the time limit hold across a slow retry sequence: each attempt rechecks the deadline before it reserves, and the timeout it hands the provider is never longer than the time the run has left. Everything that can fail locally happens before the reservation, so such a failure is uncharged. The order is admit, import, claim: the bound is checked against the budget without taking anything, so a request the ceiling refuses never reaches the network, and the claim itself is the last step before the provider is entered. Claiming before the import instead made the claim visible to a sibling sharing the budget, which was then refused on an allowance the final ledger said was free.

Everything after the provider entry point is invoked sits inside one guard, including reading a stream to its end, reassembling it, and materialising the result into a mapping. Wrapping only the call itself left a response that failed to materialise with its claim outstanding, so the retry reserved again and the headroom it had taken never came back. The rule is that a reservation resolves on every path out of the attempt.

**Accepted `llm.*` overrides**

`model`, `reasoning_effort`, `temperature`, `max_tokens`, `stream`, `timeout`, `cache`, `cache_dir`, `max_parallel`, `price_per_input_token`, `price_per_output_token`. The set is closed and an unknown name raises `ConfigError` listing the accepted ones. The last five are consumed locally and never forwarded, the two price keys among them, which is what lets a caller bound a model `pricing.PRICES` does not carry. Transport-shaped names are stripped after every merge. That strip is unreachable in practice because the closed key set refuses those names first. The strip provides a second line of defense; the closed set is the primary defense.

**Streaming**

`stream` is forwarded like any other provider setting, and a truthy value changes only how the answer arrives. The chunks are drained, handed to the provider library's assembler, and the single response that comes back goes down the path a non-streamed response takes, so nothing after this point knows the difference. It is there for provider configurations and proxies that require a stream; it cannot make a fetch faster, because a fetch parses a whole document and has nothing to give a caller until the last chunk lands.

The accounting is the part that needed a decision. Whether the provider priced the call is read from the chunks, not from the assembled response, because the assembler always attaches a usage block and fills the counts in from a local tokenizer when the stream sent none. A stream with no accounting of its own therefore takes the route every unreadable cost takes (the reservation is kept at its bound and `cost_known` is false) instead of settling a figure that was computed here and charged nowhere.

**Retry**

Transport errors are retried with exponential backoff and then re-raised. The depth comes from `Budget.effective_retries()`, as that many attempts plus one, so the configured value is a count of retries rather than of attempts and the default of 2 gives three attempts. Reading it through `effective_retries` instead of off the client's own `Limits` is what narrows it along the parent chain like every other axis: a child could otherwise spend more attempts than the budget it was derived from authorised, which was the one axis that did not consult the chain.

---

## FunctionClient

Wraps a plain `(prompt: str) -> str` callable as a client.

### `FunctionClient` constructor

```python
FunctionClient(fn, budget: Budget | None = None)
```

`LlmSource` builds one of these per fetch and hands it the run's budget. Without one, a fresh unlimited `Budget` is used.

### `FunctionClient` methods

#### `FunctionClient.budget_capabilities()`

Returns rows and nothing else. This wraps a function the caller supplied: it can prove it invoked that function once, and it cannot know whether the function contacts a provider, how many times, what it is billed, or how to stop it when a deadline passes. A call, cost, or time ceiling against an `llm_fn` is therefore refused with a `ConfigError` rather than reported as holding. Calls were declared here at first, on the reasoning that one invocation is one call, which is the same guess as reporting the cost at zero: `limits.max_calls` counts provider entries everywhere else, and a callable may answer from memory, contact one provider, or fan out to several.

#### `complete(prompt, properties=None)`

Reserves one attempt against the budget priced at nothing, calls `fn`, settles the reservation, records a `Usage` entry with model `"injected"` and the elapsed time, and returns the result as a string. A callable that raises resolves its reservation with `keep_unpriced`, so a failed call still counts as an attempt.

#### `acomplete(prompt, properties=None)`

The same, awaiting the result when the callable returns a coroutine.

Both paths record, which matters because the asynchronous one is what `afetch` uses. Recording only in the synchronous path would report zero calls for a fetch that demonstrably made one. Both also append to the `usage_sink` list when the call carries one, which is how the fetch reads its own token counts.

The claim is priced at zero because a supplied callable does its own billing, if it bills at all. The attempt is claimed anyway so that there is a reservation to resolve on every path out of the call, including a cancellation, rather than because the attempt has been measured; declaring only the rows axis is what keeps that claim from being read as a call count or a cost this client established.

---

## Budget, Limits, and Reservation

Defined in `llm/budget.py`, with the cost bound they claim in `llm/pricing.py`. `Budget`, `Limits`, `Reservation`, `BudgetAwareClient`, and `Capability` are re-exported from the package root.

### `Limits`

```python
@dataclass(frozen=True)
class Limits:
    max_rows: int | None = None
    max_seconds: float | None = None
    max_cost: float | None = None
    max_calls: int | None = None
    max_retries: int = 2
```

Configured ceilings, where `None` means unlimited on that axis. Assembled by `LlmSource` from `limits.*` properties, with a `max_records` argument replacing `max_rows` for that run. `max_retries` is a count of retries rather than of attempts, so the client makes `max_retries + 1` attempts and defaults to three.

Every field is validated on construction. A wrong type, `bool`, negative number, or non-finite number raises `ConfigError` naming the field and value. `NaN` is explicitly rejected because every spend comparison with it is false, leaving an apparently configured ceiling unenforced. A `bool` is rejected because Python would otherwise accept `True` as the integer ceiling `1`.

The set of `limits.*` property names is closed in the same way the `llm.*` set is. An unknown one raises `ConfigError` listing the accepted names, as do the near misses `limit.*` and a bare `limits`. `limits.max_cst` was accepted and ignored, so a run that looked bounded was not.

### `Budget`

```python
Budget(limits: Limits | None = None, clock=time.monotonic, parent: Budget | None = None)
```

Tracks spend against `Limits` and refuses the attempt that would cross one. The injectable clock exists so a test can drive the time axis without waiting. `parent` is set by `derive`, not by a caller.

| Method | Description |
|--------|-------------|
| `attempt(estimated_cost=0.0)` | **The lifecycle callers should use.** A context manager that admits and claims on entry and releases on exit if the block did not resolve the handle. Its cleanup is registered before the claim is taken, so there is no instant in which a claim exists and nothing holds a handle that could give it back |
| `reserve(estimated_cost=0.0)` | Checks call count, elapsed time, and committed cost plus the bound under one lock, raises `BudgetExceeded` naming the first limit that would be crossed, and otherwise claims one provider attempt and the bound together, returning a `Reservation`. The caller then owns resolving it on every path out, including an interrupt between this returning and their guard beginning; `attempt` exists because that window is easy to leave open |
| `derive(limits=None)` | A child that spends from what this budget has left; every claim and release travels up the chain |
| `check_deadline()` | Raises `BudgetExceeded` if the time limit has already passed, without claiming anything |
| `note_exhausted()` | Records a ceiling that completed work has crossed, without raising |
| `remaining_seconds()` | Time left, or `None` when no time limit is set; the client uses it to cap the provider timeout and, in `acomplete`, to bound the await |
| `remaining_calls()`, `remaining_cost()` | Headroom on those axes, or `None` when unlimited |
| `effective_retries()` | Retry depth, narrowed to the smallest along the parent chain |
| `add_rows(n)` | Records rows and returns how many fit under the row ceiling, marking the run as stopped by `max_rows` when it had to drop any |
| `rows_exhausted()` | Whether the row ceiling has been reached |
| `committed` | Settled spend plus outstanding reservations |
| `elapsed` | Seconds since the budget was constructed, or since its parent was, for a derived child |

Reservation happens under a lock so that a concurrent fan-out cannot admit two calls that each saw room for one. It is claimed once per provider attempt, including every retry, so a retry sequence spends its limit rather than escaping it, and each attempt rechecks the deadline on the way through.

Cost is claimed against an upper bound rather than an average-case estimate. If `pricing` cannot establish that bound, it refuses to return a figure. Reserving the bound before each attempt also covers two failure cases: an unreadable provider cost leaves its reservation in place, and concurrent attempts cannot all spend against the same remaining budget.

The headroom methods, `effective_retries`, and `derive` all narrow along the parent chain, so the effective ceiling on every axis is the smallest value from this budget up to the root. A child with no limits of its own is bounded entirely by its parent, which is what a fan-out leaf should be; without it a planner could hand ten leaves a full budget each. A derived child also measures elapsed time from its parent's start, so a caller's time ceiling covers the whole unit of work and does not restart with each fetch.

The child does not inherit `stopped_by`. A parent may carry the axis that refused an earlier request. If a child copied that field, a later affordable request under the same caller-owned budget would report the earlier refusal as its own and return records with `stopped_by_limit`. The child starts with the field unset and fills it from its own execution.

`note_exhausted` reports only a ceiling something has actually crossed. Spending the last allowed call refuses nothing, and reporting it would make an ordinary run under an exact limit look truncated; the same reasoning applies to spending exactly the cost ceiling, which is why that comparison is strict.

`rows_exhausted` supports callers that loop over multiple fetches, but the library does not call it in any plan mode. `check_deadline` is also public but not part of the fetch path because `reserve` performs the deadline check before every provider attempt.

### `Reservation`

One claimed provider attempt awaiting its real cost. Obtained from `Budget.attempt` or
`Budget.reserve`; there is no supported way to construct one. Its ledger identity is not
constructible for the same reason: a handle that aliased a live claim could resolve it. The
budget that issued it is readable as `issuer`, which is how a claim presented to the wrong
budget is refused rather than silently resolved somewhere else.

| Member | Description |
|--------|-------------|
| `settle(actual_cost, cache_hit=False)` | Replaces the bound with what the attempt really cost, releasing the overshoot. With `cache_hit=True` the whole claim is released, including the attempt, since nothing reached the provider |
| `keep_unpriced()` | Resolves an attempt whose real cost could not be read; the bound remains charged |
| `issuer` | The budget this claim was taken against |
| `release()` | Gives back the bound and the attempt for a call that never reached the provider |
| `estimate` | What was claimed |
| `resolved` | Whether the claim has already been resolved |

The reservation is a handle that resolves the claim exactly once; resolving it twice raises. A free-standing counter decrement cannot establish that the attempt being undone never happened, which could allow a retry to escape accounting. `release` is valid only before invoking the provider. After that point, the attempt counts regardless of its result, or failed retries would be free.

### `pricing`

A genuine upper bound on what one call can cost, or a refusal to guess, in `llm/pricing.py`.

| Member | Value | Description |
|--------|-------|-------------|
| `FRAMING_TOKENS` | `64` | Chat framing the provider adds around the prompt: role labels, separators, and per-message overhead. Generous, because it is added to a bound rather than to an estimate |
| `PRICES` | `dict[str, tuple[float, float]]` | Published input and output list prices per token, derived from the model table in `llm/models.py`, keyed by model name and matched exactly, or by stripping a trailing dated or regional suffix made only of digits and hyphens, so a dated or regional variant resolves to its family. An `openai/` prefix is removed first, and the longest matching name wins |
| `rates_for(model)` | `tuple[float, float] \| None` | The listed rates, or `None` for a model the table does not carry |
| `resolve_rates(model, properties=None)` | `tuple[float, float]` | Caller-supplied rates when either price key is present, the listed rates when neither is, and `ConfigError` when nothing usable is available. Presence decides that an override was meant, not validity, so one half alone, or a value that will not read as a finite positive number, is refused instead of falling back to a table the caller has already said is wrong for this model |
| `bound_cost(prompt, model, max_tokens, properties=None)` | `float` | `len(prompt.encode("utf-8")) + FRAMING_TOKENS` at the input rate plus the whole `max_tokens` allowance at the output rate, or `ConfigError` when that figure cannot be established. For a table model, the input rate rises by the cache-write rate's ratio from 1,024 prompt tokens where the model has one, the long-context multipliers apply above 272,000 prompt tokens, and the whole figure carries the regional uplift where the model's entry has it. Supplied rates for a table model get the same modifiers and never yield less than the table's bound; for a model outside the table they are used as given and must cover every billed tier |
| `can_bound(model, max_tokens, properties=None)` | `bool` | Whether a cost ceiling could be claimed for these arguments |

The bound rests on one property: tokens never outnumber the bytes of the encoded prompt. A byte-pair tokenizer merges byte sequences, so every token it emits consumes at least one byte, and `len(prompt.encode("utf-8"))` bounds the prompt's token count for any tokenizer, present or future. Characters do not carry that property although they read as though they do, because a tokenizer splits a character whose encoding it has no merge for: a composed emoji of seven code points can cost eleven tokens, and a 1,400-character sequence has been measured at 2,200. The byte count is loose, since English runs three to four bytes per token, and it is loose in the only safe direction.

Two values are required to calculate a bound: model rates and a finite positive output cap. A model absent from the table may cost more than any listed model, so `rates_for` returns `None` instead of selecting the highest listed rate. Without a finite positive output cap, a request could produce output until the provider stops it. If either value is missing, `bound_cost` raises `ConfigError` naming the missing value and how to provide it; the caller's cost ceiling is refused rather than based on a guess. A caller who knows the rates for an unlisted model supplies both `llm.price_per_input_token` and `llm.price_per_output_token`.

The minimum cost bound is the full `max_tokens` allowance at the model's output rate, independent of the prompt. With the default `gpt-6-luna` model and `llm.max_tokens=25000`, it is about $0.01375 with the regional uplift. A lower `limits.max_cost` refuses the call before provider contact. Reducing `llm.max_tokens` lowers the bound proportionally.

`PRICES` is public list-price data and usually goes stale in the safe direction, since providers cut prices far more often than they raise them. GPT-5.6 Sol is the known exception: its rate is a promotion through at least 2026-11-21, and the rows for it and the `gpt-5.6` alias need raising when it ends. It is a file to update rather than a mechanism to redesign.

The two price keys are available through the normal client path as well as through `pricing` itself. `DefaultLlmClient` accepts `llm.price_per_input_token` and `llm.price_per_output_token` as local `llm.*` overrides and does not forward them. `LlmSource.estimate` reads the same merged properties, allowing callers to estimate or cap an unlisted model when they supply its rates.

`LlmSource.estimate` also reports the result of `bound_cost`. Publishing this helper lets callers get the same bound that will apply to their request before making it.

---

## Module Functions

### `checks`

| Function | Returns | Description |
|----------|---------|-------------|
| `normalize_query(query)` | `str` | Strips the request and returns it, or returns empty for `None` and for a blank string; any other non-string raises. This is what the fetch path calls |
| `require_query(query)` | `str` | The stricter form, which additionally rejects an empty or whitespace request. Published for a caller that wants an empty ask to be an error, and not used by the fetch path |
| `normalize_attrs(attrs)` | `list[Attr]` | Accepts a list of names, of `Attr`, or of `{"name", "type"}` mappings; rejects anything else by index |
| `normalize_context(context)` | `str` | Accepts a string or a list of strings and joins them into one block |
| `require_positive_int(value, field)` | `int \| None` | Rejects booleans and non-positive integers; `None` passes through |

The split between the two query functions is the empty-request decision made concrete. A wrong type is a caller mistake and raises. An empty string is an empty request, and the caller gets an empty result carrying `empty_query` as its reason.

Every message names the offending value and the accepted form, because a caller reaching the library through a tool interface sees only the exception text.

### `prompts`

| Function | Returns | Description |
|----------|---------|-------------|
| `render_attributes(attrs)` | `str` | Renders the attribute block, or `(none specified)` |
| `render_constraints(constraints)` | `list[str]` | One line per filter: `- field "region" must equal one of ["North", "South"]` |
| `render_held(shown, total)` | `list[str]` | The exclusion block a continuation carries, with the total it was capped from |
| `render_held_entries(shown)` | `list[str]` | Complete encoded lines for held records |
| `rendered_held_entries_size(shown)` | `int` | UTF-8 byte size of the held-record block, including line separators |
| `build_fetch_prompt(query, attrs, *, caller_context, constraints, held, held_total, primary_keys, estimate)` | `str` | Flat extraction and continuation prompt |
| `build_leaf_fetch_prompt(...)` | `str` | Versioned non-flat leaf extraction prompt |
| `build_analyse_prompt(query, context)` | `str` | Query-analysis prompt |
| `build_schema_prompt(...)` | `str` | Independent schema prompt |
| `build_cardinality_prompt(...)` | `str` | Independent cardinality prompt |
| `build_bootstrap_prompt(...)` | `str` | Compatible combined analysis, schema, and cardinality prompt |
| `build_partition_prompt(...)` | `str` | Partition-proposal prompt |
| `build_distribution_prompt(...)` | `str` | Rule-backed distribution prompt |
| `build_entity_prompt(...)` | `str` | Row-by-row entity-enumeration prompt |

The nine templates are `FETCH`, `LEAF_FETCH`, `ANALYSE`, `SCHEMA`, `CARDINALITY`,
`BOOTSTRAP`, `PARTITION`, `DISTRIBUTION`, and `ENUMERATE_ENTITIES`. Each builder passes `Slot`
instances to `fill`, which applies one `str.format_map` over fixed named fields. Model-derived and
structured values use encoded slots. Raw slots are limited to caller-owned request, context, and
attribute text; an architecture test pins those uses.

The builders take structured parts, not assembled prompt strings. They are the only functions
that assemble provider prompts; a test refuses prompt section headings outside this module.

For the flat builder, filters pass through `render_constraints`, held records through `render_held`,
and the query is encoded because the same slot carries caller text in flat mode and an analysis
rewrite elsewhere. Encoding is not an authorization boundary: the caller remains the principal and
supplies its own context and attribute text.

### `reduction.parsing`

| Function | Returns | Description |
|----------|---------|-------------|
| `strip_fence(text)` | `str` | Removes one surrounding code fence, matched as a fence rather than by language name |
| `parse_records(text, wanted=None)` | `list[dict]` | Reads a JSON array of objects, accepting a bare object and supported wrappers; uses requested attributes to resolve an envelope with sibling metadata |
| `salvage_records(text, wanted=None)` | `list[dict]` | Recovers a complete array prefix from a truncated response, then applies `parse_records` to it |

An object whose only key is `items`, `rows`, `data`, or `results` is unwrapped. With sibling fields,
both the envelope and its list may be records. When the caller supplied `wanted`, the reading that
answers more requested attributes wins; without requested attributes, the envelope is kept because
it discards nothing. This avoids both losing metadata siblings and dropping records under a wrapper.
Nesting deeper than a hundred levels is refused because downstream walks are recursive.

An object that repeats a key is rejected at any nesting depth through an `object_pairs_hook`. Python's decoder would otherwise keep the final value and discard the earlier one without a trace. An earlier implementation renamed later occurrences with a private marker, but that marker could reach callers when no attributes were requested and also marked nested duplicates that alignment never examines. The library cannot determine which value the model intended, so it rejects the object. `NaN`, `Infinity`, and `-Infinity` are also rejected because they are not valid JSON. Numeric literals that overflow float conversion, such as `1e309`, are caught during conversion. Either case would otherwise produce a value that cannot be serialized back to JSON.

### `reduction.aligner`

```python
@dataclass
class Alignment:
    records: list[dict[str, Any]]
    observed_keys: list[str]
    remapped: dict[str, str]
    unmapped: list[str]
    collisions: list[dict[str, Any]]
    matched: list[set[str]]
```

| Function | Returns | Description |
|----------|---------|-------------|
| `align(records, attrs)` | `Alignment` | Maps returned keys onto requested attributes by exact, then case-insensitive, then normalised match, and reports what it could not place |

With no requested attributes the records pass through unchanged, `observed_keys` is populated, and `matched` carries each record's own keys.

`collisions` holds returned keys that matched an attribute already answered by another key. The first value is kept; the second is reported with `record_index`, `attribute`, `kept_key`, `ignored_key`, and `ignored_value`, and becomes `ExecMeta.key_collisions`. Keeping the first value avoids making the result depend on the order in which the model emitted the keys. The intended spelling cannot be determined, so the source reports the collision without resolving it. Parsing rejects repeated identical names before this stage. For example, `First Name` and `first_name` are distinguishable keys that can be reported, while a repeated key provides no way to distinguish its values. Two requested attributes with matching fuzzy forms are handled differently: the fuzzy index omits the ambiguous entry, so exact keys still match and keys with only a fuzzy match are reported as unmapped. Only a request that repeats the same name raises `QueryError`.

`matched` records, positionally, which requested attributes each record actually answered. Alignment fills a missing attribute with `None` and a JSON null is also `None`, so the value alone cannot tell "not answered" from "answered null"; presence is recorded where it is known, not inferred later from a value that cannot carry it.

### `reduction.verifier`

| Function | Returns | Description |
|----------|---------|-------------|
| `verify(records, attrs)` | `tuple[list[dict], list[dict]]` | Coerces values to their declared types where the coercion is unambiguous, and returns the records alongside every value that would not coerce |
| `drop_empty(records, matched, attrs)` | `list[dict]` | Removes records that answered none of the requested attributes, reading presence from `matched` rather than from the values, so a record whose only answer is an explicit null survives |
| `dedupe(records, key=None, origins=None)` | `tuple[list[dict], list[list[str]]]` | Removes exact or keyed duplicates and folds every contributing leaf into the kept record's origin list |

```python
@dataclass
class TypeMismatch:
    record_index: int
    attribute: str
    declared_type: str
    value: str
```

Each mismatch is returned as a mapping through `as_dict()` and surfaced as `ExecMeta.type_mismatches`. `value` is the repr of the value before coercion. A value that will not coerce is kept rather than dropped, because discarding a row over a formatting difference loses data. Every failure is reported so callers know the records are not all of the declared types. A request with no typed attributes skips the pass and reports no mismatches.

### `reduction.identity`

This module defines one declared-type-aware rule for record identity, shared by continuation exclusion, reduction, and identity-rule validation. Keeping the rule in one module prevents continuation and reduction from using different key spellings and asking the model again for records it already returned.

| Member | Returns | Description |
|--------|---------|-------------|
| `KeyTuple` | dataclass | A complete ordered primary key, carrying the key `names`, the `normalized` form two records are compared on, and the `display` form a prompt shows. `display` is excluded from equality and hashing, so two spellings of one value are one identity while the prompt still shows what the model returned |
| `KeyTuple.exclusion()` | `dict[str, Any]` | The named key object a continuation prompt carries |
| `key_identity(record, primary_keys, attrs)` | `KeyTuple \| None` | The record's complete identity, or `None` when any key component is absent. It aligns the record first, so identity reads the same value final shaping will |
| `normalized_value(value, declared_type)` | `Hashable` | One value in the form identities are compared on, honouring the declared type |
| `record_identity(record)` | `Hashable` | Exact whole-record identity, independent of key order. The continuation loop falls back to it for a record whose primary key is incomplete, so a part that returns the same keyless record twice adds it once |

`key_identity` and `KeyTuple` are re-exported from the package root, because an adapter or a caller merging records across two runs needs the library's own answer rather than a second one that agrees with it most of the time.

### `reduction.coverage`

This module combines records from multiple leaves and reports what the result covers. `reduce_leaves(plan, schema, results)` merges leaf records into the returned list and provides a `Coverage` object with per-leaf outcomes, gaps, and `record_origins`. These become `meta.gaps` and `meta.record_origins`; `Coverage.run_outcome` determines the run's outcome by precedence. The merged list does not show what was lost between the leaves and the caller, such as rows removed by a ceiling, leaves that never ran, or duplicate records with differing field values. The `Coverage` object reports these details alongside the rows.

The account is produced for every run, a one-leaf run included, because one leaf still executes inside a bounded loop and can stop at its cap, which is exactly the shape where rows with no account read as a complete answer. Merging here runs only against a column `planning.schema` identified, since merging on a guessed column collapses distinct records into one and returns the result as clean; where no key was identified this stage merges nothing and says so in a note. Two merge facts do become gaps here: a key that two leaves spell differently and that normalizes to one identity, and a record that lacked a key component and was kept unmerged. The key-level counts a caller reads as `meta.observed_key_count`, `meta.marginal_key_yields`, `meta.incomplete_key_records` and `meta.normalization_collisions` are not from here: the continuation loop in `execution/mapper.py` observes them a leaf at a time and the facade sums them. None of it is a completeness measure.

---

## compat.BlueDataLlmSource

```python
BlueDataLlmSource = LlmSource
```

An alias, and nothing more. This module existed when `blue-data` was an optional extra and the core matched the source surface by shape: it held a subclass of both `LlmSource` and `DataSource` that added the real inheritance for callers who installed the extra and wanted the base class in their type hierarchy.

The core now inherits `DataSource` itself, so there is nothing left for a separate class to add. It is bound as an alias rather than declared as an empty subclass because a subclass would be a second class to test and a second method resolution order to get wrong, for no behaviour `LlmSource` does not already have. The name survives so an import written against the old module keeps working; new code should use `LlmSource`.

Nothing in the core imports this module.

---

## Constants

Declared in `_constants.py`.

| Constant | Value | Role |
|----------|-------|------|
| `PROTOCOL` | `"llm"` | Source family |
| `VARIANT_OPENAI`, `DEFAULT_VARIANT` | `"openai"` | Implementation within the family |
| `OP_FETCH` | `"LLM.FETCH"` | Fetch op code |
| `OP_MODELS` | `"LLM.MODELS"` | Protocol and variant reporting op code |
| `OP_ESTIMATE` | `"LLM.ESTIMATE"` | Cost-bound op code |
| `DATABASE` | `"llm"` | Synthesised database name |
| `COLLECTION` | `"public"` | Synthesised collection name |
| `ENTITY`, `ENTITY_ATTRIBUTE` | `"llm_entity"`, `"llm_entity_attribute"` | Synthesised entity and attribute |
| `ENTITY_ATTRIBUTE_TYPE` | `"text"` | Declared type of the synthesised attribute |
| `BUDGET_PROPERTY` | `"budget"` | The property key a fetch hands its budget down under |
| `USAGE_SINK_PROPERTY` | `"usage_sink"` | The property key a fetch hands down a list for this call's `Usage` entries |

No environment variable is named here, read, or logged. The model is selected through the `llm.model` property, and the client's default is `gpt-6-luna` with medium reasoning.
