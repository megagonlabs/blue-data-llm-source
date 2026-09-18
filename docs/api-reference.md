# API Reference

Every public method signature, parameter, and return in `blue_data_llm_source`, as the code defines them.

This overlaps [`design/09-api-interface-spec.md`](../design/09-api-interface-spec.md), which is the contributor-facing specification and the source of truth for the interface contract. Where this page and that one disagree, the code decides and the design document is corrected.

Version 2.0.0. `blue_data_llm_source.__version__` carries it.

The fenced blocks on this page are signatures rather than programs: each shows a declaration, not something to paste and run. Runnable examples live in [guide.md](guide.md), [features.md](features.md), and [extending.md](extending.md).

## Package exports

Fifty names, all importable from `blue_data_llm_source` directly.

| Name | Kind | Purpose |
|---|---|---|
| `LlmSource` | class | The facade. [Below](#llmsource) |
| `FetchResult` | dataclass | Records plus metadata, returned by `fetch` and `afetch` |
| `ExecMeta` | dataclass | What happened during a fetch |
| `Estimate` | dataclass | What one flat fetch could cost |
| `Usage` | dataclass | Per-call provider accounting |
| `Attr` | dataclass | One requested output attribute |
| `Outcome` | class | The closed outcome vocabulary |
| `Capability` | class | The closed vocabulary a client declares from |
| `Exact` | dataclass | A filter satisfied by one value |
| `OneOf` | dataclass | A filter satisfied by any one of several |
| `Constraint` | type alias | `Exact | OneOf` |
| `JsonScalar` | type alias | `str | int | float | bool` |
| `Budget` | class | Spend tracked against `Limits` |
| `Limits` | dataclass | The configured ceiling on each axis |
| `Reservation` | class | One claimed provider attempt |
| `DefaultLlmClient` | class | The built-in provider client |
| `FunctionClient` | class | A `(prompt) -> str` callable wrapped as a client |
| `LlmClientProtocol` | protocol | The two-method model boundary |
| `BudgetAwareClient` | protocol | A client that declares which ceilings it honours |
| `CacheStore` | protocol | `get`/`set` cache |
| `MemoryCache` | class | Bounded in-process cache |
| `FileCache` | class | One-JSON-file-per-entry cache |
| `LlmSourceError` | exception | The root of the hierarchy |
| `ConfigError` | exception | Rejected configuration |
| `QueryError` | exception | A malformed query |
| `TransportError` | exception | A provider failure or malformed response |
| `ParseError` | exception | A response that cannot be read as records |
| `BudgetExceeded` | exception | A limit that would be crossed |
| `PROTOCOL` | `str` | `"llm"` |
| `DEFAULT_VARIANT` | `str` | `"openai"` |
| `OP_FETCH` | `str` | `"LLM.FETCH"` |
| `OP_MODELS` | `str` | `"LLM.MODELS"` |
| `OP_ESTIMATE` | `str` | `"LLM.ESTIMATE"` |
| `rates_for` | function | Published rates for a model, or `None` |
| `priceable_models` | function | The model names the price table carries |
| `can_bound` | function | Whether a cost ceiling could be claimed |
| `meta_view` | function | `ExecMeta` as a JSON-ready dict |
| `key_identity` | function | A complete ordered identity for a record |
| `KeyTuple` | dataclass | That identity |
| `PlanningContext` | dataclass | Accepted analysis, schema, and cardinality for one plan |
| `PlanningReceipt` | dataclass | A distribution-search plan bound to its rules and facts |
| `QueryAnalyzer` | class | Parser for the query-analysis stage |
| `QueryAnalysisResult` | dataclass | Its result |
| `DataTask` | dataclass | One retrieval task in a plan |
| `SchemaGenerator` | class | Parser for the schema stage |
| `SchemaDefinition` | dataclass | Its result |
| `CardinalityEstimator` | class | Parser for the cardinality stage |
| `CardinalityEstimate` | dataclass | Its result |
| `redact` | function | Masks credential-shaped text before it is written out |
| `__version__` | `str` | `"2.0.0"` |

### `redact`

```text
redact(text: str) -> str
```

Returns `text` with anything that reads as a credential replaced by `***`. Both adapters call it on the text they write *about* a call (error messages, log output, the request-side fields of a result's metadata), so a key that reaches this package by mistake does not come back out of it.

It is not applied to records. A record value that happens to look like a token is the answer to the query, and masking it would destroy data. The same holds for the metadata fields that name the keys the model returned, because they index records that are not masked. `requested_attrs` is the exception: it names record keys too, but it is the caller's own text handed back, so it is masked.

Call it on anything you build from a caller's input or a provider's exception before you display, log, or store it.

`blue_data_llm_source.compat.blue_data.BlueDataLlmSource` is an alias for `LlmSource`, kept so an import written against the earlier optional adapter keeps working. It is not a subclass. New code imports `LlmSource`.

## `LlmSource`

```text
LlmSource(name: str = "llm", properties: dict[str, Any] | None = None, client: Any | None = None)
```

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `name` | `str` | `"llm"` | The name a registry holds this source under |
| `properties` | `dict[str, Any] \| None` | `None` | Connection details and configuration. [Configuration](#configuration-properties) below |
| `client` | `Any \| None` | `None` | A model client to use for every call. Any object with `complete` and `acomplete` |

Extends `blue_data.source.source.DataSource`. Base-class order is load-bearing: `DataSource` is a plain class whose methods mostly return an empty result, so a method left unoverridden would answer nothing rather than fail.

| Attribute | Type | Meaning |
|---|---|---|
| `name` | `str` | As constructed |
| `properties` | `dict[str, Any]` | The merged property map, including a `connection` sub-dict |
| `protocol` | `str` (property) | Always `"llm"`. Not caller-settable |
| `variant` | `str` (property) | `"openai"` by default. Set through `properties["connection"]["variant"]` |

### Library surface

```text
fetch(query, attrs=None, context=None, max_records=None, properties=None,
      llm_fn=None, budget=None) -> FetchResult
async afetch(query, attrs=None, context=None, max_records=None, properties=None,
             llm_fn=None, budget=None) -> FetchResult
estimate(query, attrs=None, context=None, properties=None, llm_fn=None) -> Estimate
```

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `query` | `Any` | — | The question. A non-string raises `QueryError`; an empty or whitespace string is an empty ask and returns `outcome="empty_query"` |
| `attrs` | `Any` | `None` | A list of names, a list of `Attr`, or a list of `{"name": str, "type": str \| None}` dicts. Anything else raises `QueryError` naming the index |
| `context` | `Any` | `None` | A string or a list of strings, joined into one block. Free text clarifying what an attribute name means |
| `max_records` | `int \| None` | `None` | Row ceiling for this call, replacing `limits.max_rows`. Must be a positive non-`bool` integer |
| `properties` | `dict[str, Any] \| None` | `None` | Per-call properties, merged over the instance's for this call only and not written back |
| `llm_fn` | `Any` | `None` | A `(prompt) -> str` callable, or a coroutine function, used as the model for this call. Wrapped in a `FunctionClient` |
| `budget` | `Budget \| None` | `None` | A budget you own. The fetch derives a child from it, so ceilings accumulate across every call you hand it to |

`afetch` is the real entry point; `fetch` wraps it with `asyncio.run`. Calling `fetch` from inside a running event loop raises `ConfigError` naming `afetch`.

`estimate` takes no `max_records` and no `budget`, contacts nothing, and reads no `plan.*` setting.

Resolution order for the client: `llm_fn` for this call, then the `client` given at construction, then a `DefaultLlmClient` built from the merged properties.

Property precedence, lowest first: this source's own defaults, an injected client's `properties`, properties set on the source, properties passed with the call.

### Source surface

Inherited from `DataSource` and overridden here.

| Method | Returns | Behaviour |
|---|---|---|
| `execute_query(query, database=None, collection=None, optional_properties=None)` | `list[dict[str, Any]]` | Runs a query. `query` is a natural-language string or a dict carrying an `op` key. `database` and `collection` are accepted for signature compatibility and unused |
| `execute_query_nl(query, database=None, collection=None, schema=None, llm_fn=None, optional_properties=None)` | `list[dict[str, Any]]` | Runs a natural-language query. `llm_fn` is the model for this call, not a translator |
| `translate_nl_to_native_query(query, schema, llm_fn)` | `str \| None` | The identity, because natural language is this source's native form. Returns `None` for an empty query |

`optional_properties` carries the per-call ceiling, and `attrs`, `attr_names`, `context`, and `max_records` are lifted out of it as arguments. Everything else is merged into the property map.

**Only `llm.*`, `limits.*`, and `plan.*` names are validated.** A key outside those three namespaces is accepted and unused. `execute_query` has no `llm_fn` parameter, so a key of that name placed in `optional_properties` is ignored and the request falls through to the provider-backed client. Inject at construction instead.

#### Op codes

| Op | Reads from the dict | Returns |
|---|---|---|
| `LLM.FETCH` | `query`, `attrs` or `attr_names`, `context`, `max_records` | The records |
| `LLM.MODELS` | nothing | `[{"protocol": "llm", "variant": "openai"}]`, without a model call |
| `LLM.ESTIMATE` | `query`, `attrs` or `attr_names`, `context` | The `Estimate` as a one-record list, without contacting anything |

An unrecognised op raises `QueryError` listing the three.

### Orchestrator tool surface

| Method | Returns | Behaviour |
|---|---|---|
| `get_nl_tools(database=None, collection=None)` | `list[dict[str, Any]]` | The base class's own tool definitions, filtered to the four this source can answer |
| `execute_nl_tool(name, tool_input, database=None, collection=None, optional_properties=None)` | `Any` | Runs one advertised tool, or raises `ConfigError` listing the four |
| `execute_query_nl_tools(query, database=None, collection=None, llm_fn=None, optional_properties=None, max_iterations=10, extra_tools=None, extra_tool_fn=None)` | — | Always raises `ConfigError` |

| Tool | Served | Behaviour |
|---|---|---|
| `list_databases` | yes | `["llm"]` |
| `list_collections` | yes | `["public"]` |
| `get_schema` | yes | `{"entities": {"llm_entity": {"properties": {"llm_entity_attribute": {"type": "text"}}}}, "relations": {}}` |
| `execute_query` | yes | The records for `tool_input["query"]`. The one tool that reaches a provider |
| `get_metadata` | no | Would return `{}` |
| `get_column_stats` | no | Would return `None` |
| `get_relations` | no | Would return `{}` |
| `get_sample` | no | Raises `TypeError` from the base dispatcher |

A tool input missing a key the advertised schema requires raises `QueryError` naming what was missing and what was supplied. `database` and `collection` may be supplied either in `tool_input` or as arguments. Nothing on this path leaves the `LlmSourceError` hierarchy.

`execute_query_nl_tools` drives a loop of up to ten provider calls that this source's ceilings do not govern, so it is refused rather than served.

### Metadata surface

| Method | Returns |
|---|---|
| `fetch_metadata()` | `{"protocol": "llm", "variant": "openai"}` |
| `fetch_databases()` | `["llm"]` |
| `fetch_database_collections(database)` | `["public"]` |
| `fetch_database_collection_entities(database, collection)` | The one virtual entity with its one text attribute |
| `fetch_schema()` | `{}` |
| `fetch_database_metadata(database)` | `{}` |
| `fetch_database_schema(database)` | `{}` |
| `fetch_database_collection_metadata(database, collection)` | `{}` |
| `fetch_database_collection_relations(database, collection)` | `{}` |
| `fetch_source_stats()` | `None` |
| `fetch_database_stats(database)` | `None` |
| `fetch_collection_stats(database, collection, entities=None, relations=None, sample_limit=None)` | `None` |
| `fetch_entity_stats(database, collection, entity)` | `None` |
| `fetch_property_stats(database, collection, entity, property_name, sample_limit=None)` | `None` |

The one-database, one-collection, one-entity hierarchy is synthesised so a registry sync has something to walk; a model has none of its own.

### Write surface

A model has nothing to write to. These exist so a registry walking the full surface reaches a defined method rather than an `AttributeError`, and each returns `{}`.

| Method |
|---|
| `create_database(database, properties=None)` |
| `create_database_collection(database, collection, properties=None)` |
| `create_database_collection_entity(database, collection, entity, properties=None)` |
| `create_database_collection_relation(database, collection, relation, properties=None)` |
| `import_data(source, database="default", collection="default", entity=None, mode="append", options=None)` |

Materialising model knowledge into a real store is the receiving source's `import_data` with this source as its argument, not this method.

### Connection

| Method | Returns | Behaviour |
|---|---|---|
| `connect()` | `{}` | No persistent connection is held; the client is per call |
| `disconnect()` | `None` | Does nothing |

## Configuration properties

Set on the instance, carried by an injected client, or passed per call. Per-call wins.

### Connection properties

| Key | Type | Default | Meaning |
|---|---|---|---|
| `connection.protocol` | `str` | `"llm"` | The source family. Written on every update; not caller-settable |
| `connection.variant` | `str` | `"openai"` | The implementation within the family |
| `connection.host`, `connection.port`, `connection.user`, `connection.password` | — | empty | Present for connection-shape compatibility. Unused by this source |

Set these through `properties={"connection": {"variant": "..."}}`; the sub-dict is merged rather than replaced.

### `llm.*` properties

Ten accepted names. An `llm.*` key outside the set raises `ConfigError` listing all ten.

| Key | Type | Default | Forwarded to provider | Meaning |
|---|---|---|---|---|
| `llm.model` | `str` | `"gpt-4o"` | yes | Model name |
| `llm.temperature` | `float` | `0` | yes | Sampling temperature |
| `llm.max_tokens` | `int` | `4096` | yes | Output allowance |
| `llm.stream` | `bool` | `False` | yes | Read the chunks to the end and reassemble. Does not make a fetch faster; a stream carrying no usage cannot be priced |
| `llm.timeout` | `float` | provider default | yes | Per-call timeout, capped at the time the budget has left. Checked as a finite positive number |
| `llm.cache` | `bool` | off | no | Opens a `diskcache` response cache. Requires the `llm` extra |
| `llm.cache_dir` | `str` | `".llm_cache"` | no | Directory for that cache. At most eight backends stay open per client |
| `llm.max_parallel` | `int` | `8` | no | Concurrency bound per event loop. Must be a whole number of at least 1 |
| `llm.price_per_input_token` | `float` | none | no | Input rate for a model the price table does not carry |
| `llm.price_per_output_token` | `float` | none | no | Output rate for the same model |

`llm.rate_limit` is refused by name with a `ConfigError`, because client-side call spacing has to reconcile with per-attempt budget admission and this library does not do it. Cap concurrency with `llm.max_parallel`, or throttle above this library.

Supplying one of the two price keys commits you to both, and a value that is not a finite positive number raises rather than falling back to the table.

### `limits.*` properties

Five accepted names, and an unknown one raises `ConfigError` listing them, as do the near misses `limit.*` and a bare `limits`. Every value is validated as the ceiling is built: a wrong type, a `bool`, a negative number, or a non-finite number raises `ConfigError` naming the field.

| Key | Type | Default | Requires the client to declare | Effect |
|---|---|---|---|---|
| `limits.max_rows` | `int \| None` | unlimited | nothing | Truncates the result and sets `meta.truncated_to`. Enforced by this source, so it binds any client |
| `limits.max_seconds` | `float \| None` | unlimited | `Capability.SECONDS` | Refuses an attempt once elapsed time reaches it and cancels a wait in flight |
| `limits.max_cost` | `float \| None` | unlimited | `Capability.COST` | Refuses an attempt whose cost bound would cross it |
| `limits.max_calls` | `int \| None` | unlimited | `Capability.CALLS` | Refuses an attempt past that many, counting every retry |
| `limits.max_retries` | `int` | `2` | nothing | Retries per call; the client makes this many attempts plus one, narrowed to the smallest depth along the budget chain |

A configured ceiling on an axis the resolved client does not declare raises `ConfigError` at the first fetch. The axes weighed are every one carrying a ceiling anywhere in the budget chain, not only this request's own.

`limits.max_cost` has one further requirement: a bound must be establishable. It is refused when the model has no published price and none was supplied, and when `llm.max_tokens` is not a finite positive number.

### `plan.*` properties

Eighteen accepted names. An unknown `plan.*` key raises `ConfigError`.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `plan.mode` | `str` | `"flat"` | `flat`, `partitioned`, or `row_by_row`. An unknown value raises |
| `plan.key` | `str` | none | The attribute identifying a record, so parts returning the same record merge. Must be one of the attributes requested |
| `plan.keys` | sequence of `str` | empty | Ordered fields that jointly identify one record. Required explicitly for distribution search |
| `plan.exact_rows` | `int` | none | Hard target when the plan has one leaf. A multi-leaf plan does not apply it and adds `exact_rows_not_leaf_bound` to `meta.gaps` |
| `plan.partition_key` | `str` | none | Preferred root split dimension. Descendants may choose another and cannot repeat an ancestor's |
| `plan.max_depth` | `int` | `2` | Deepest partition level. `0` disables subdivision while retaining the planning stages |
| `plan.max_partitions` | `int` | `4` | Most children one proposal may create. Below `2` leaves the node unsplit |
| `plan.max_leaves` | `int` | `24` | Most terminal slices. An expansion that cannot fit is rejected whole |
| `plan.target_rows_per_leaf` | `int` | `50` | Row-count hint above which a descendant may split again. Not a completeness target |
| `plan.max_continuations` | `int` | `0` | How many times a part may ask again for records it did not get. Bounds asks per part, not provider attempts |
| `plan.max_excluded_keys` | `int` | `50` | Count cap for whole-key exclusions in a continuation prompt. Direct Python only |
| `plan.max_exclusion_bytes` | `int` | `16384` | Encoded byte cap for those exclusions |
| `plan.schema_mode` | `str` | `"preserve"` | `preserve` keeps declared attributes; `enrich` lets the planning stages append inferred ones. `flat` plus `enrich` is rejected, because flat makes no planning call |
| `plan.planning_order` | `str` | `"planning.schema_first"` | One of five schedules. [Below](#planning-schedules) |
| `plan.expose_estimate` | `bool` | `True` | Whether extraction prompts include the soft estimate. Planning facts and task transport retain it either way |
| `plan.distribution_search` | `bool` | `False` | Opt-in for rule-backed distribution selection. Requires `partitioned`. Direct Python only |
| `plan.identity_partitions` | `list[dict]` | empty | Versioned finite-domain rule mappings. Direct Python only |
| `plan.max_strategy_candidates` | `int` | `5` | Distribution candidate bound. At least `1` when distribution search is enabled. Direct Python only |

The command-line interface and the MCP server expose the fourteen standard controls. They do not expose distribution search, its rules, its candidate bound, or the receipt; those four reach only a direct Python caller.

#### Planning schedules

| Value | Stages | Model calls before extraction |
|---|---|---|
| `planning.schema_first` | analysis, schema, cardinality | at most 3 |
| `planning.cardinality_first` | analysis, cardinality, schema | at most 3 |
| `planning.locked_sequential` | analysis and cardinality, from the same inputs | at most 2 |
| `planning.locked_parallel` | the same two, checked for aggregate headroom first | at most 2 |
| `planning.combined` | one bootstrap call | at most 1 |

Both locked schedules require a non-empty caller schema and ordered `plan.keys`. `flat` makes no planning call at all.

#### Distribution-search rules

Each entry of `plan.identity_partitions` is a dict with exactly these seven keys, read by `IdentityPartitionRule.from_dict`.

| Key | Type | Meaning |
|---|---|---|
| `dimension` | `str` | The partition key. Must be one of `plan.keys` |
| `ordered_primary_keys` | `list[str]` | Must equal `plan.keys` exactly |
| `kind` | `str` | `"finite_domain"` |
| `allowed_domain` | `list[JsonScalar]` | Non-empty, with no duplicates after normalisation |
| `parent_scope` | `dict[str, dict]` | Constraints under which this rule applies. `{}` for a root rule |
| `provenance` | `str` | `"source_configuration"` for a rule set on the source, `"caller_declared"` for one passed with the call |
| `contract_version` | `str` | `"identity-partition-rule-1"` |

Per-call rules replace the source list rather than adding to it. Rules may not come from an injected client's own properties. Every rule is validated before the provider is contacted, and all three distribution-search settings are removed from the property map handed to the client.

### Reserved properties

| Key | Type | Meaning |
|---|---|---|
| `cache_store` | `CacheStore` | A cache with `get` and `set`, taking precedence over `llm.cache` |
| `budget` | `Budget` | Written by the source on every call. A value you set is overwritten |
| `usage_sink` | `list[Usage]` | Written by the source on every call. The fetch reads this call's token counts and model name from it alone |

## Result types

### `FetchResult`

| Field | Type | Default |
|---|---|---|
| `records` | `list[dict[str, Any]]` | `[]` |
| `meta` | `ExecMeta` | a fresh `ExecMeta` |

| Member | Returns | Meaning |
|---|---|---|
| `len(result)` | `int` | Number of records |
| `iter(result)` | iterator | Over the records |
| `is_empty` | `bool` (property) | Whether `records` is empty |
| `planning_receipt` | `PlanningReceipt \| None` (property) | Present only for a direct-Python distribution-search result |
| `with_planning_receipt(receipt)` | `FetchResult` | Attaches a receipt and returns self |
| `to_dict()` | `dict[str, object]` | `{"records": ..., "meta": ...}`, plus `"planning_receipt"` only when one exists |

The dataclass keeps its two fields. A receipt rides alongside them rather than becoming a third.

### `ExecMeta`

Every field, in declaration order.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `gaps` | `list[str]` | `[]` | Observed failures, discarded output, or interrupted work. Empty means no supported diagnostic fired, not that nothing is missing |
| `outcome` | `str` | `"ok"` | One of the `Outcome` members |
| `model` | `str` | `""` | The model name the usage entries reported |
| `calls` | `int` | `0` | Provider attempts claimed against the budget, retries included |
| `prompt_tokens` | `int` | `0` | Summed from this call's usage entries |
| `completion_tokens` | `int` | `0` | The same |
| `cost` | `float` | `0.0` | Settled spend read from the budget |
| `latency_seconds` | `float` | `0.0` | Wall-clock time for the fetch |
| `cache_hits` | `int` | `0` | Usage entries marked as cache hits |
| `requested_attrs` | `list[str]` | `[]` | The attribute names asked for |
| `row_estimate` | `int \| None` | `None` | Accepted non-flat planning hint for the root slice. `None` in flat mode or when none was accepted. Not a row count and not a completeness claim |
| `cardinality_estimate` | `dict[str, object] \| None` | `None` | The structured form of the same evidence; `row_estimate` is its point-value alias |
| `primary_keys` | `list[str]` | `[]` | The ordered identity fields the run used |
| `observed_key_count` | `int` | `0` | Distinct complete keys seen |
| `marginal_key_yields` | `list[int]` | `[]` | New keys added by each successive ask |
| `incomplete_key_records` | `int` | `0` | Records with a key component missing |
| `normalization_collisions` | `list[dict[str, Any]]` | `[]` | Distinct raw key values that normalised together |
| `observed_keys` | `list[str]` | `[]` | One entry per key seen. Grows with the answer, so `meta_view` omits it |
| `unmapped_keys` | `list[str]` | `[]` | Returned keys that matched no requested attribute |
| `remapped_keys` | `dict[str, str]` | `{}` | Returned key to the attribute it was matched to under a different spelling |
| `asked` | `str \| None` | `None` | The request as it was actually sent, when a planning stage rewrote it. `None` when nothing did |
| `record_origins` | `list[list[str]]` | `[]` | Which parts of a split request produced each record, parallel to `records` |
| `type_mismatches` | `list[dict[str, Any]]` | `[]` | Each with `record_index`, `attribute`, `declared_type`, `value` |
| `key_collisions` | `list[dict[str, Any]]` | `[]` | Two returned keys reaching one attribute. The first wins; the second is reported with the value it carried |
| `unchecked_types` | `list[str]` | `[]` | Declared type names this library cannot check, reported once against the request |
| `truncated_to` | `int \| None` | `None` | Where the row ceiling cut the result |
| `stop_reasons` | `list[str]` | `[]` | Ordered distinct facts that limited extraction or discarded final output |
| `stopped_by` | `str \| None` | `None` | The highest-precedence member of `stop_reasons` |
| `accounting_known` | `bool` | `True` | Whether `calls`, `cost`, and the token counts describe provider work. `False` for an `llm_fn` answer |

Stop-reason precedence, highest first: `max_seconds`, `max_cost`, `max_calls`, `provider_output_limit`, `response_incomplete`, `max_rows`, `plan.max_continuations`. Anything else follows in the order it was observed.

| Stop reason | Meaning |
|---|---|
| `max_seconds`, `max_cost`, `max_calls`, `max_rows` | That ceiling fired |
| `provider_output_limit` | The provider reported that its output allowance ended the response |
| `response_incomplete` | A response did not parse whole and its complete records were kept. No cause is attached |
| `plan.max_continuations` | A leaf reached its continuation cap after an answer that was still adding records. Completeness was not established |

### `Outcome`

| Member | Value | Means |
|---|---|---|
| `Outcome.OK` | `ok` | Records, no stop |
| `Outcome.EMPTY_QUERY` | `empty_query` | The request was empty, so nothing was asked |
| `Outcome.MODEL_RETURNED_NONE` | `model_returned_none` | An answered response held no records |
| `Outcome.FILTERED_TO_EMPTY` | `filtered_to_empty` | Records came back and reduction removed all of them |
| `Outcome.PARTIAL` | `partial` | An extraction answered and other attempted work was interrupted |
| `Outcome.STOPPED_BY_LIMIT` | `stopped_by_limit` | A non-row ceiling stopped the run, or a row ceiling left nothing |
| `Outcome.TRUNCATED` | `truncated` | The row ceiling trimmed the result and records remain |

`Outcome.ALL` is the tuple of all seven. `partial` outranks a simultaneous cap, because every cap still appears in `stop_reasons`.

### `Estimate`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `bounded` | `bool` | `False` | Whether a bound could be established |
| `max_cost` | `float \| None` | `None` | The most the whole flat fetch could cost: `max_cost_per_attempt` accumulated over `max_attempts` |
| `reason` | `str \| None` | `None` | Why no bound could be given |
| `model` | `str` | `""` | The model actually configured, not a fallback |
| `prompt_bytes` | `int` | `0` | The rendered prompt's length in UTF-8 bytes |
| `max_output_tokens` | `int` | `0` | The output allowance the bound reserved |
| `max_attempts` | `int` | `1` | Provider attempts the fetch permits |
| `max_cost_per_attempt` | `float \| None` | `None` | The bound one attempt reserves |

Size a ceiling against `max_cost`. An empty request returns `bounded=True` with a `max_cost` of zero, since it never reaches a model.

### `Usage`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `model` | `str` | `""` | The model this call used |
| `prompt_tokens` | `int` | `0` | As the provider reported |
| `completion_tokens` | `int` | `0` | The same |
| `cost` | `float` | `0.0` | What the provider charged, when it could be read |
| `latency_seconds` | `float` | `0.0` | Including a cache hit's own latency |
| `cache_hit` | `bool` | `False` | Whether this answer came from a cache |
| `finish_reason` | `str \| None` | `None` | Why the provider stopped, as it said it. `None` when it said nothing |
| `cost_known` | `bool` | `True` | Whether `cost` is what the provider charged |

### `Attr`

Frozen. `Attr(name: str, type: str | None = None)`.

| Declared type | Accepted input | Result |
|---|---|---|
| `int`, `integer` | integer, whole float, digit string with optional thousands separators | `int` |
| `float`, `number` | number, numeric string with optional thousands separators | `float` |
| `bool`, `boolean` | boolean, or `true`/`yes`/`y`/`1` and `false`/`no`/`n`/`0` in any case | `bool` |
| `date` | `YYYY-MM-DD`, `YYYY/MM/DD`, `DD-MM-YYYY`, or `Month D, YYYY` | ISO date string |
| anything else | — | Untouched, and the name is reported in `meta.unchecked_types` |

A value that will not coerce is returned as it arrived and reported in `meta.type_mismatches`. An attribute with no declared type is not touched.

Only a genuine thousands grouping is stripped: three-digit groups, optionally signed, optionally with a dot decimal part.

### `Exact`, `OneOf`, `Constraint`, `JsonScalar`

```text
Exact(value: JsonScalar)
OneOf(values: tuple[JsonScalar, ...])
Constraint = Exact | OneOf
JsonScalar = str | int | float | bool
```

Both are frozen and validate on construction: a value must be a finite JSON scalar and a string must not be empty, or `ConfigError` is raised. `OneOf` holds a tuple so the whole constraint is hashable and cannot be extended after the plan that sized itself against it was built.

## Protocols

All three are `runtime_checkable`, so `isinstance` works. Nothing in the package checks against a concrete client class.

### `LlmClientProtocol`

```text
def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str: ...
async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str: ...
```

Both take the prompt first and the merged per-call properties second, and both return the model's text. The shape is checked when the client is resolved: a missing method, or one whose signature cannot be called as `complete(prompt, properties)`, raises `ConfigError` naming the contract.

### `BudgetAwareClient`

`LlmClientProtocol` plus:

```text
def budget_capabilities(self) -> frozenset[str]: ...
```

A method, not an attribute, so satisfying it takes a deliberate implementation rather than a name that happens to match.

### `Capability`

| Member | Value | Declaring it commits the client to |
|---|---|---|
| `Capability.CALLS` | `calls` | Honouring `limits.max_calls` |
| `Capability.ROWS` | `rows` | Nothing the source does not already do itself |
| `Capability.SECONDS` | `seconds` | Honouring `limits.max_seconds` |
| `Capability.COST` | `cost` | Honouring `limits.max_cost` |
| `Capability.ESTIMATE` | `estimate` | Implementing `bound_for_attempt`, **and** making at most `budget.effective_retries() + 1` provider attempts per call |
| `Capability.ALL` | frozenset of all five | All of the above |

`ESTIMATE` is not a ceiling, and `ALL` includes it. A client that cannot promise the attempt allowance omits it; `estimate` then reports `bounded=False` with a reason.

### `CacheStore`

```text
def get(self, key: str) -> Any | None: ...
def set(self, key: str, value: Any) -> None: ...
```

Driven with those two methods alone. The key is the serialised provider call arguments, which covers the model, the prompt, and every forwarded parameter.

## Clients

### `DefaultLlmClient`

```text
DefaultLlmClient(properties: dict[str, Any] | None = None, budget: Budget | None = None)
```

Reaches a provider through `litellm`, imported lazily inside the call. A missing extra raises `ConfigError` naming it.

| Attribute | Type | Meaning |
|---|---|---|
| `properties` | `dict[str, Any]` | The client's own configuration, merged under the caller's |
| `budget` | `Budget` | The fallback budget when a call carries none |
| `last_usage` | `Usage \| None` | The most recent call's accounting |
| `usages` | `list[Usage]` | This client's whole history |

| Method | Returns | Meaning |
|---|---|---|
| `complete(prompt, properties=None)` | `str` | The model's text |
| `acomplete(prompt, properties=None)` | `str` | The same, and the form every fetch reaches |
| `budget_capabilities()` | `frozenset[str]` | `Capability.ALL` |
| `bound_for_attempt(prompt, properties)` | `float` | The most one attempt at `prompt` could cost. Raises `ConfigError` when no bound can be established |
| `close()` | `None` | Releases every cache backend this client opened. Safe to call more than once; the client stays usable |

Also a context manager: `with DefaultLlmClient() as client:` closes it on exit. A long-lived service that rotates `llm.cache_dir` needs this, because nothing else releases those file descriptors; at most eight backends stay open and the coldest is closed beyond that.

### `FunctionClient`

```text
FunctionClient(fn: Callable[[str], Any], budget: Budget | None = None)
```

Wraps a plain `(prompt) -> str` callable, awaiting the result when the callable returns a coroutine. This is what `llm_fn` becomes.

| Method | Returns | Meaning |
|---|---|---|
| `complete(prompt, properties=None)` | `str` | `str(fn(prompt))`, or `""` when it returns `None` |
| `acomplete(prompt, properties=None)` | `str` | The same, awaiting a coroutine result |
| `budget_capabilities()` | `frozenset[str]` | `frozenset({Capability.ROWS})` |

Rows alone, because it can prove it invoked the callable once and nothing more. `limits.max_calls`, `limits.max_cost`, and `limits.max_seconds` are refused against it, and a result it produced sets `meta.accounting_known` to `False`.

It claims one attempt at no cost per call, so there is a reservation to resolve on every path out (including a cancellation) rather than because the attempt was measured. Attributes `budget`, `last_usage`, and `usages` match `DefaultLlmClient`'s.

## Budget

### `Limits`

Frozen. Every field is validated on construction; a violation raises `ConfigError` naming the field and the value.

```text
Limits(max_rows: int | None = None, max_seconds: float | None = None,
       max_cost: float | None = None, max_calls: int | None = None,
       max_retries: int = 2)
```

`max_rows`, `max_calls`, and `max_retries` must be whole numbers and not `bool`. `max_seconds` and `max_cost` must be real and finite. None may be negative. A `NaN` ceiling is refused because it compares false against every spend and would read as configured while enforcing nothing.

### `Budget`

```text
Budget(limits: Limits | None = None,
       clock: Callable[[], float] = time.monotonic,
       parent: Budget | None = None)
```

| Attribute | Type | Meaning |
|---|---|---|
| `limits` | `Limits` | As constructed |
| `calls` | `int` | Attempts claimed |
| `cost` | `float` | Settled spend |
| `rows` | `int` | Rows admitted |
| `reserved` | `float` | Outstanding claims |
| `stopped_by` | `str \| None` | Which ceiling refused something |

| Method | Returns | Meaning |
|---|---|---|
| `derive(limits=None)` | `Budget` | A child spending from what this budget has left. Its ceilings apply on top, never instead |
| `elapsed` | `float` (property) | Seconds since this budget's clock started |
| `committed` | `float` (property) | Settled spend plus outstanding reservations |
| `remaining_cost()` | `float \| None` | Headroom one admission will actually accept, narrowed along the whole chain |
| `remaining_calls()` | `int \| None` | The same on the call axis |
| `remaining_seconds()` | `float \| None` | The same on the time axis |
| `effective_retries()` | `int` | Retry depth, narrowed by every parent |
| `configured_axes()` | `frozenset[str]` | Every axis carrying a ceiling anywhere in this chain |
| `reserve(estimated_cost=0.0)` | `Reservation` | Claims one attempt and that bound, or raises `BudgetExceeded` naming the first ceiling that would be crossed |
| `attempt(estimated_cost=0.0)` | context manager yielding `Reservation` | `reserve` with cleanup installed before the claim is taken. Prefer this |
| `check_admissible(estimated_cost=0.0)` | `None` | Raises if an attempt would be refused, without claiming anything |
| `check_deadline()` | `None` | Raises `BudgetExceeded` when the time ceiling has been reached |
| `add_rows(n)` | `int` | How many of `n` fit under the row ceiling, marking the run stopped when it had to drop any |
| `rows_exhausted()` | `bool` | Whether the row ceiling is spent |
| `note_exhausted()` | `str \| None` | Records a ceiling that completed work crossed, without raising |
| `binding_deadline()` | `tuple[float \| None, bool]` | The `max_seconds` value of the ceiling in this chain with the least time left, and whether that ceiling is this budget's own |
| `holds(reservation)` | `bool` | Whether that claim is still outstanding here |
| `settle_reservation(reservation, actual)` | `None` | The method form of `Reservation.settle` |
| `release_reservation(reservation, refund_call)` | `None` | The method form of `Reservation.release` |
| `checked_amount(name, value)` | `float` (static) | A usable amount, or a refusal naming the field |

Enforcement order inside `reserve`: every parent outermost first, then call count, then elapsed time, then settled cost plus outstanding reservations plus this attempt's bound. All of it happens under one lock shared by the whole family.

A child does not inherit its parent's `stopped_by`, and it measures elapsed time from the parent's start.

### `Reservation`

Constructed by `Budget`, not directly.

| Member | Returns | Meaning |
|---|---|---|
| `estimate` | `float` (property) | What this claim reserved |
| `issuer` | `Budget` (property) | The budget that issued it |
| `resolved` | `bool` (property) | Whether it has been resolved |
| `settle(actual_cost, cache_hit=False)` | `None` | Replaces the bound with the real cost, releasing the overshoot. With `cache_hit=True`, releases the attempt as well |
| `keep_unpriced()` | `None` | Leaves the bound standing as the charge, for a call that reached the provider and cannot be priced |
| `release()` | `None` | Gives the bound and the attempt back, for an attempt that never reached the provider |

A reservation resolves exactly once and raises if resolved twice. `release` is correct only before the provider entry point is invoked. A claim left outstanding is headroom the budget never grants again.

## Cache stores

### `MemoryCache`

```text
MemoryCache(max_entries: int = 512)
```

Bounded, with least-recently-used eviction and a lock. `max_entries` at or below zero raises `ValueError`.

| Method | Returns |
|---|---|
| `get(key)` | The value, or `None` |
| `set(key, value)` | `None` |
| `clear()` | `None` |
| `len(cache)` | Current entry count |

### `FileCache`

```text
FileCache(directory: str | os.PathLike[str] = ".llm_cache")
```

One JSON file per entry, named `llmcache-<sha256 of key>.json`. The directory is created if absent. Writes go to a temporary file and are moved into place, so a reader never sees a half-written entry.

| Method | Returns |
|---|---|
| `get(key)` | The value, or `None`. A corrupt or unreadable entry is a miss, not an error |
| `set(key, value)` | `None`. An unserialisable or unwritable value is skipped rather than raised |
| `clear()` | `None`. Removes only files this cache wrote; anything else in the directory is left alone |

## Pricing helpers

| Function | Signature | Returns |
|---|---|---|
| `rates_for` | `rates_for(model: str) -> tuple[float, float] \| None` | Published input and output price per token for a listed model or a dated variant of one, `None` otherwise |
| `priceable_models` | `priceable_models() -> tuple[str, ...]` | The model names the table carries, sorted |
| `can_bound` | `can_bound(model: str, max_tokens: object, properties: dict[str, object] \| None = None) -> bool` | Whether a cost ceiling could be claimed for these arguments |

A model name resolves only when it equals a listed name, or extends one with a hyphen followed by digits and hyphens alone. A longer name with letters after it is a different model, not a variant.

The price table itself is not exported. It is a mutable snapshot of time-sensitive list prices with no effective date and no freshness check, and publishing it would be a contract this package cannot keep across a price change. Supply `llm.price_per_input_token` and `llm.price_per_output_token` to bound a model it does not carry.

The bound is the prompt's length in UTF-8 bytes plus a 64-token framing allowance at the input rate, plus the whole `llm.max_tokens` allowance at the output rate. Bytes rather than characters, because a byte-pair tokenizer consumes at least one byte per token and can emit more tokens than the prompt has characters.

## `meta_view`

```text
meta_view(meta: ExecMeta, *, verbose: bool = False) -> dict[str, Any]
```

The one serialization of `ExecMeta` every adapter publishes, so a run cannot describe itself two ways depending on which door the caller came through.

| Tier | Keys |
|---|---|
| Always present | `outcome`, `model`, `calls`, `prompt_tokens`, `completion_tokens`, `cost` (rounded to 6 places), `elapsed_seconds` (rounded to 3), `row_estimate`, `cardinality_estimate`, `primary_keys`, `observed_key_count`, `marginal_key_yields`, `incomplete_key_records`, `gaps`, `stop_reasons`, `requested_attrs` |
| Present when populated | `normalization_collisions`, `unchecked_types`, `record_origins`, `asked`, `stopped_by`, `truncated_to`, `cache_hits`, and `accounting_known` in its false form only |
| Verbose only | `unmapped_keys`, `remapped_keys`, `type_mismatches`, `key_collisions` |

`observed_keys` is in no tier: it grows with the answer, and `observed_key_count` already carries the fact a consumer acts on.

## Identity

```text
key_identity(record: Mapping[str, Any], primary_keys: Sequence[str],
             attrs: Sequence[Attr]) -> KeyTuple | None
```

Returns a complete ordered identity, or `None` when any component is absent or blank. The record is aligned to `attrs` first, so identity uses the same canonical field names as the final records.

| `KeyTuple` member | Type | Meaning |
|---|---|---|
| `names` | `tuple[str, ...]` | The ordered key names |
| `normalized` | `tuple[Hashable, ...]` | The comparison form. Part of equality and the hash |
| `display` | `tuple[Any, ...]` | The values as they arrived. Excluded from equality and the hash |
| `exclusion()` | `dict[str, Any]` | The named key object a continuation prompt shows |

Normalisation follows the declared type: `int`/`integer`, `float`/`number`, `bool`/`boolean`, `date`, and `str`/`string`/`text` each get their own rule, and a string type additionally folds case, width, and surrounding whitespace. An attribute with no declared type compares by exact JSON-typed value, so `"CDG"` and `" cdg "` are two identities unless the attribute is declared a string.

## Planning types

Exported so a caller can read a plan, reuse a stage parser, or check a receipt. The stage parsers are pure: each takes the model's text and returns a value, and none of them calls a model.

| Type | Constructed by | Members |
|---|---|---|
| `QueryAnalyzer` | — | `parse(text, *, asked, original_query=None, caller_constraints=None) -> QueryAnalysisResult` (static) |
| `QueryAnalysisResult` | the analysis stage | Fields `ask`, `constraints`, `rewritten`, `row_estimate`, `cardinality`, `task`, `diagnostics`; `to_dict()` |
| `DataTask` | a `QueryAnalysisResult` | Fields `id`, `parent_id`, `original_query`, `query`, `constraints`, `schema`, `cardinality`, `depth`, `partition_key`, `partition_value`; `to_dict()` |
| `SchemaGenerator` | — | `parse(text, declared, *, mode) -> SchemaDefinition \| None` (static) |
| `SchemaDefinition` | the schema stage | Fields `attrs`, `primary_keys`, `key_from`, `identity_partitions`; `key` property, `to_schema()`, `to_dict()`, `from_schema(schema)` |
| `CardinalityEstimator` | — | `parse(text, *, row_unit, source="cardinality_stage") -> CardinalityEstimate` (static) |
| `CardinalityEstimate` | the cardinality stage | Fields `expected`, `lower`, `upper`, `row_unit`, `source`, `parse_status`, `diagnostics`; `to_dict()` |
| `PlanningContext` | the coordinator | Fields `analysis`, `schema`, `cardinality`, `stages`, `diagnostics`, `schedule`; `canonical_dict()`, `digest` property |
| `PlanningReceipt` | a distribution-search fetch | Fields `configured_rule_digests`, `facts`, `plan`, `contract_version`; `to_dict()`, `from_dict(value)` |

`SchemaGenerator.parse` takes a `Schema` as its `declared` argument. That type is reachable as `blue_data_llm_source.planning.schema.Schema` but is not exported from the package root.

`PlanningReceipt` is likewise constructed by the library rather than by a caller: its `facts` and `plan` types are not exported from the package root. Read a receipt with `to_dict()`, and rebuild one from that dict with `from_dict`.

`CardinalityEstimate.parse_status` is `parsed`, `unknown`, or `invalid`, and the three are mutually exclusive about values and diagnostics: a parsed estimate carries values and no diagnostics, an unknown one carries neither, an invalid one carries diagnostics and no values.

`PlanningReceipt.to_dict()` returns `contract_version`, `configured_rule_digests`, `planning_facts`, and `fetch_plan`. Its checks are internal: that the facts match the plan, that every applied rule was configured, and that the counts agree. It does not verify factual truth, completeness, or answer quality.

## Exceptions

```
LlmSourceError
├── ConfigError
├── QueryError
├── TransportError
├── ParseError
└── BudgetExceeded
```

| Exception | Extra attributes | Raised for |
|---|---|---|
| `LlmSourceError` | — | The root. Anything unexpected is wrapped in one with the original as `__cause__` |
| `ConfigError` | — | An unknown `llm.*`, `limits.*`, or `plan.*` key; an invalid ceiling value; a ceiling the resolved client cannot hold; a cost bound that cannot be established; a missing optional extra; a client that cannot be called as the contract requires; `fetch` called inside a running event loop; an unserved tool name; `execute_query_nl_tools` |
| `QueryError` | — | A query that is not a string; a malformed attribute list; the same attribute requested twice; a non-positive `max_records`; an unknown op; a tool input missing a required key |
| `TransportError` | `message`, `code`, `provider_error` | A provider failure or a malformed provider response, including a missing or wrong credential, which this library never sees as configuration |
| `ParseError` | `message`, `raw` | A response that cannot be read as records. `raw` carries the response text, because re-running is not guaranteed to reproduce the failure |
| `BudgetExceeded` | `message`, `limit` | A ceiling that would be crossed. `limit` names the axis |

Nothing escapes `LlmSourceError` from `fetch`, `afetch`, or `estimate`. `KeyboardInterrupt` and cancellation are deliberately not caught.

`BudgetExceeded` raised inside a fetch does not reach the caller: a refusal before any extraction answered returns no records with `outcome="stopped_by_limit"`, and a refusal after one answered retains those records with `outcome="partial"`. It stays public for callers driving a `Budget` directly.

## Related

- [guide.md](guide.md): the same surface, task by task.
- [extending.md](extending.md): the protocols above, from the implementer's side.
- [`design/09-api-interface-spec.md`](../design/09-api-interface-spec.md): the interface specification this page is kept in agreement with.
- [`lib/COMPATIBILITY.md`](../lib/COMPATIBILITY.md): what differs from the data-source specification, and from what a caller being ported was written against.
