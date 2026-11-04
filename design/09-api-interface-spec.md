# LlmSource — API / Interface Spec

The core library has three interfaces: the Python API, an MCP server, and a CLI. Each is a separate package installed from source. Interface packages translate arguments and results, but contain no retrieval, planning, or budgeting logic. The MCP server maps most library failures to fixed messages while retaining known error kinds. The provider SDK handles authentication. The CLI can store a provider credential in a profile and export it to the process environment for the SDK. MCP tools do not accept credentials as arguments. The library reads environment values held under credential-like names only to mask diagnostic text.

---

## 1. Python Library

The Python API is the canonical interface, and every other interface delegates to it. `LlmSource` inherits `blue_data.source.source.DataSource` and can be instantiated directly because that base class has no abstract members. `afetch` is a coroutine and must be awaited; all other methods, including `estimate`, are synchronous.

The base-class order affects behavior: `LlmSource`'s implementations must come first in the method resolution order. A test checks this for every method on the shared surface. See [The Source Base Class](04-detailed-architecture.md#the-source-base-class) for details. Inheritance does not make the source constructible by protocol name. `DataRegistry.connect_source` returns the registered instance or rebuilds it from the recorded class path after the live entry is gone. The supported integration route is to build an instance and pass it to `register_source`; the registration is durable.

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

`properties` is merged over the defaults, with the `connection` mapping merged key by key. `client` injects the model client used by every request from this instance; any object with `complete(prompt, properties)` and `acomplete(prompt, properties)` is accepted. A client that also implements `budget_capabilities() -> frozenset[str]` satisfies `types.BudgetAwareClient`. It receives the run's budget under the `budget` property and a per-call `Usage` list under `usage_sink`, and is expected to claim against the budget. Each configured ceiling on an undeclared axis is refused with a `ConfigError` naming that ceiling and the axes the client declared.

### `LlmSource`

```python
class LlmSource:
    name: str
    properties: dict[str, Any]

    @property
    def protocol(self) -> str: ...  # always "llm"
    @property
    def variant(self) -> str: ...  # "openai" unless configured otherwise

    # Library surface
    async def afetch(
        self,
        query: Any,
        attrs: Any = None,
        context: Any = None,
        max_records: int | None = None,
        properties: dict[str, Any] | None = None,
        llm_fn: Any = None,
        budget: Budget | None = None,
    ) -> FetchResult: ...
    def fetch(
        self,
        query: Any,
        attrs: Any = None,
        context: Any = None,
        max_records: int | None = None,
        properties: dict[str, Any] | None = None,
        llm_fn: Any = None,
        budget: Budget | None = None,
    ) -> FetchResult: ...
    def estimate(
        self,
        query: Any,
        attrs: Any = None,
        context: Any = None,
        properties: dict[str, Any] | None = None,
        llm_fn: Any = None,
    ) -> Estimate: ...
    def max_tokens_for_model(self, model: str) -> object: ...

    # Source surface
    def execute_query(
        self,
        query: Any,
        database: str | None = None,
        collection: str | None = None,
        optional_properties: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...
    def execute_query_nl(
        self,
        query: str,
        database: str | None = None,
        collection: str | None = None,
        schema: dict[str, Any] | None = None,
        llm_fn: Any = None,
        optional_properties: dict[str, Any] | None = None,
        read_only: bool = True,
    ) -> list[dict[str, Any]]: ...
    def translate_nl_to_native_query(
        self, query: str, schema: dict[str, Any] | None, llm_fn: Any
    ) -> str | None: ...

    # Orchestrator tool surface, covered in section 1a
    def get_nl_tools(
        self, database: str | None = None, collection: str | None = None
    ) -> list[dict[str, Any]]: ...
    def execute_nl_tool(
        self,
        name: str,
        tool_input: dict[str, Any],
        database: str | None = None,
        collection: str | None = None,
        optional_properties: dict[str, Any] | None = None,
        read_only: bool = True,
    ) -> Any: ...
    def execute_query_nl_tools(
        self,
        query: str,
        database: str | None = None,
        collection: str | None = None,
        llm_fn: Any = None,
        optional_properties: dict[str, Any] | None = None,
        max_iterations: int = 10,
        extra_tools: list[dict[str, Any]] | None = None,
        extra_tool_fn: Any = None,
        read_only: bool = True,
        max_sample_size: int | None = None,
    ) -> list[dict[str, Any]]: ...

    # Metadata surface
    def fetch_metadata(self) -> dict[str, Any]: ...
    def fetch_schema(self) -> dict[str, Any]: ...
    def fetch_databases(self) -> list[str]: ...
    def fetch_database_metadata(self, database: str) -> dict[str, Any]: ...
    def fetch_database_schema(self, database: str) -> dict[str, Any]: ...
    def fetch_database_collections(self, database: str) -> list[str]: ...
    def fetch_database_collection_metadata(
        self, database: str, collection: str
    ) -> dict[str, Any]: ...
    def fetch_database_collection_entities(
        self, database: str, collection: str
    ) -> dict[str, Any]: ...
    def fetch_database_collection_relations(
        self, database: str, collection: str
    ) -> dict[str, Any]: ...
    def fetch_source_stats(self) -> dict[str, Any] | None: ...
    def fetch_database_stats(self, database: str) -> dict[str, Any] | None: ...
    def fetch_collection_stats(
        self,
        database: str,
        collection: str,
        entities: Any = None,
        relations: Any = None,
        sample_limit: int | None = None,
    ) -> dict[str, Any] | None: ...
    def fetch_entity_stats(
        self, database: str, collection: str, entity: str, *, row_sample_size: int = 10
    ) -> dict[str, Any] | None: ...
    def fetch_property_stats(
        self,
        database: str,
        collection: str,
        entity: str,
        property_name: str,
        sample_limit: int | None = None,
    ) -> dict[str, Any] | None: ...

    # Write surface
    def create_database(
        self, database: str, properties: dict[str, Any] | None = None, overwrite: bool = False
    ) -> dict[str, Any]: ...
    def create_database_collection(
        self, database: str, collection: str, properties: dict[str, Any] | None = None, overwrite: bool = False
    ) -> dict[str, Any]: ...
    def create_database_collection_entity(
        self,
        database: str,
        collection: str,
        entity: str,
        properties: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]: ...
    def create_database_collection_relation(
        self,
        database: str,
        collection: str,
        relation: str,
        properties: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]: ...
    def import_data(
        self,
        source: Any,
        database: str = "default",
        collection: str = "default",
        entity: str | None = None,
        mode: str = "append",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    # Connection
    def connect(self) -> dict[str, Any]: ...
    def disconnect(self) -> None: ...
```

Every statistics method returns `None`, because a model has no row counts. Every write method returns `{}`, because a model has nothing to write to; they exist so that a registry walking the full source surface reaches a defined method rather than an `AttributeError`.

Inside a running event loop, synchronous query methods run on an owned worker and block the caller's thread. Prefer `afetch` for concurrent async work and for clients tied to the caller's loop. Cancellation remains cooperative; a client that ignores it cannot be forcibly stopped. Both `fetch` and `afetch` annotate `query` as `Any` rather than `str`: `None` returns an empty result, while another non-string raises a readable `QueryError`.

### Request arguments

| Argument | Type | Accepted forms |
|----------|------|----------------|
| `query` | `Any`, normalized to `str` | A natural language request. `None`, an empty string, or whitespace is an empty ask and returns an empty result with `outcome` set to `empty_query`; any other non-string raises `QueryError` |
| `attrs` | list | Names (`["country", "accession_year"]`), `Attr` instances, or mappings (`[{"name": "country", "type": "string"}]`) |
| `context` | `str` or list of `str` | Additional text; its documented use is to say what an attribute name means |
| `max_records` | `int` | A positive integer; can only tighten the effective row limit for this run |
| `properties` | mapping | Per-call configuration, merged over the instance properties for this call and not written back. A `limits.*` value can only tighten the effective ceiling; `None` leaves it unchanged; `limits.max_retries=None` is invalid |
| `llm_fn` | callable | A `(prompt: str) -> str` callable used as the model client for this run |
| `budget` | `Budget` | A budget the caller owns. Supplied, this fetch derives a child from it and the ceilings accumulate across every call the budget is handed to; omitted, one is built from the configured limits for this call alone. The secondary route, for one total across several requests rather than a bound on each |

### Op codes for `execute_query`

An `op` key selects the operation when `execute_query` receives a dict or a JSON-encoded operation object. Other strings remain natural-language queries.

| Op | Reads from the mapping | Returns |
|----|------------------------|---------|
| `LLM.FETCH` | `query`, `attrs` or `attr_names`, `context`, `max_records` | The record list |
| `LLM.MODELS` | — | `[{"protocol": "llm", "variant": "openai"}]` |
| `LLM.ESTIMATE` | `query`, `attrs`, `context` | `[{"bounded": true, "max_cost": 0.06257625, "reason": null, "model": "gpt-6-luna", "prompt_bytes": 786, "max_output_tokens": 25000, "max_attempts": 3, "max_cost_per_attempt": 0.020858750000000002, "attempts_within_ceiling": null}]` |

An unrecognized op raises `QueryError` listing the supported operations, whether supplied in a dict or a JSON-encoded operation object. Other strings, including malformed JSON, are treated as the request and run a fetch. `LLM.ESTIMATE` calls `estimate` with `optional_properties` as its properties and contacts no provider. This surface returns a one-record list; callers that need the `Estimate` object can call the method directly.

On this surface, `optional_properties` also carries ceilings. Recognized keys become arguments; the remaining keys are merged over the instance properties as per-call configuration. A `limits.*` value can only tighten the effective ceiling, and `None` leaves it unchanged; `limits.max_retries=None` is invalid. The per-call configuration is not written back.

### Configuration properties

General properties may be set on the instance or per call, with per-call values winning. A `limits.*` value can only tighten the effective ceiling; `None` leaves it unchanged; `limits.max_retries=None` is invalid.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `connection.variant` | `str` | `openai` | Implementation within the `llm` family |
| `connection.host`, `connection.port`, `connection.user`, `connection.password` | — | empty | Present for connection-shape compatibility; unused by this source |
| `llm.model` | `str` | `gpt-6-luna` | Model name, forwarded to the provider as written. An `openai/` prefix reads the same model-table entry, and a bare OpenAI name is sent with the provider named as `openai` |
| `llm.reasoning_effort` | `str` | the model's default | One of `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, within the set the model's table entry accepts; refused before any reservation otherwise. GPT-6 Luna's default is `medium` |
| `llm.temperature` | `float` | `0` for GPT-4.1 and GPT-4o, otherwise none | Forwarded where the model accepts it: always for GPT-4.1 and GPT-4o, only at effort `none` for the GPT-6 Luna and Sol, GPT-5.6, 5.5 and 5.4 models, never for GPT-6 Astra, GPT-5 and the o-series |
| `llm.max_tokens` | `int` | `25000` for a reasoning model, else `4096` | Forwarded through the model's output-token parameter: `max_completion_tokens` for a reasoning model, `max_tokens` for GPT-4.1, GPT-4o and a model the table does not carry |
| `llm.stream` | `bool` | `False` | Forwarded to the provider. `True` is accepted for compatibility with provider configurations and proxies that require a stream; it does not make a fetch faster, and a stream the provider sends no usage with cannot be priced, so it reports `accounting_known=False`. See [streaming](../lib/README.md#streaming) |
| `llm.timeout` | `float` | provider default | Per-call timeout, forwarded to the provider. When `limits.max_seconds` is set, the value actually sent is the smaller of this and the wall-clock time the run has left |
| `llm.cache` | `bool` | off | Enables the `diskcache` response cache; requires the `llm` extra |
| `llm.cache_dir` | `str` | `.llm_cache` | Directory for that cache |
| `llm.max_parallel` | `int` | `8` | Concurrency bound per event loop |
| `llm.rate_limit` | — | — | **Not supported.** Refused by name with `ConfigError`, because client-side call spacing has to reconcile with per-attempt budget admission and this library does not do it. Use `llm.max_parallel`, or throttle above the library |
| `cache_store` | `CacheStore` | none | A cache with `get` and `set`, taking precedence over `llm.cache`. `MemoryCache` and `FileCache` ship for this slot, and any object with the two methods is accepted |
| `limits.max_retries` | `int` | `2` | Retries on a transport failure, at most 9,999; the client makes this many plus one attempt, narrowed to the smallest depth along the budget chain, and reserves budget for each |
| `limits.max_rows` | `int` | unlimited | Row ceiling; a `max_records` argument can only tighten it. Enforced by the source itself, so it binds any client |
| `limits.max_seconds` | `float` | unlimited | Time ceiling on admission and cooperative waits, not on work that never yields; requires a client declaring `Capability.SECONDS`. See [what it bounds](../lib/README.md#what-the-time-ceiling-actually-bounds) |
| `limits.max_cost` | `float` | unlimited | API cost ceiling, claimed against a computed upper bound before each attempt; requires a client declaring `Capability.COST` |
| `limits.max_calls` | `int` | unlimited | Provider call ceiling, counted per attempt including retries; requires a client declaring `Capability.CALLS` |
| `budget` | `Budget` | set per call | Reserved. The source writes the run's budget here on every call, so a caller setting it is overwritten |
| `usage_sink` | `list[Usage]` | set per call | Reserved. The source writes a fresh list here on every call and reads this call's token counts and model name from it alone |
| `plan.mode` | `str` | `flat` | `flat`, `partitioned`, or `row_by_row` |
| `plan.max_continuations` | `int` | `0` | Additional extraction asks permitted per leaf |
| `plan.max_leaves` | `int` | `100` | Maximum number of terminal plan slices |
| `plan.partition_key` | `str` | none | Preferred root partition dimension |
| `plan.max_depth` | `int` | `2` | Maximum partition depth; zero disables subdivision |
| `plan.max_partitions` | `int` | `20` | Maximum children in one accepted partition proposal |
| `plan.target_rows_per_leaf` | `int` | `50` | Soft estimate threshold used to decide whether a descendant is eligible to split |
| `plan.schema_mode` | `str` | `preserve` | `preserve` or `enrich` |
| `plan.planning_order` | `str` | `planning.schema_first` | One of the five planning schedules described below |
| `plan.expose_estimate` | `bool` | `True` | Whether extraction prompts include the soft estimate; planning facts and task transport retain it either way |
| `plan.key` | `str` | none | Compatibility alias for a single primary key |
| `plan.keys` | sequence of `str` | empty | Ordered fields that jointly identify one record |
| `plan.exact_rows` | `int` | none | Hard target when the plan has one leaf; a multi-leaf plan does not apply it and reports `exact_rows_not_leaf_bound` |
| `plan.max_exclusion_bytes` | `int` | `16384` | Encoded byte cap for whole-key exclusions in a continuation prompt |
| `plan.max_excluded_keys` | `int` | `50` | Direct-Python-only count cap for whole-key exclusions; CLI and MCP use this default |
| `plan.leaf_prompt` | `str` | `keyed-leaf-fetch-1` | Leaf prompt contract in non-flat modes; `keyed-leaf-fetch-2` asks for every matching record and separates column filters from request scope |
| `plan.child_wording` | `str` | `parent` | `canonical` derives each partitioned child's wording from the root request and its validated filters |
| `plan.filter_enforcement` | `str` | `flag` | `flag` keeps records contradicting a leaf filter; `drop` removes them before reduction. Both count contradictions |
| `plan.range_filters` | `bool` | `False` | Reads integer spans as range filters in partitioned mode; requires `plan.leaf_prompt=keyed-leaf-fetch-2` |
| `plan.partition_guardrails` | `bool` | `False` | Adds partition proposal rules and rejects unsuitable numeric key buckets in partitioned mode |
| `plan.split_policy` | `str` | `always` | `evidence` probes the root once before deciding whether to split in partitioned mode |
| `plan.split_threshold_rows` | `int` | `120` | Under `evidence`, the estimate above which a probe with fewer than half the expected keys triggers a split |
| `plan.distribution_search` | `bool` | `False` | Direct-Python-only opt-in for rule-backed distribution selection; requires `plan.mode='partitioned'` |
| `plan.identity_partitions` | list | empty | Direct-Python-only closed rule mappings from source configuration or a per-call declaration |
| `plan.max_strategy_candidates` | `int` | `5` | Direct-Python-only distribution candidate bound; at least `1` when distribution search is enabled |

An `llm.*` key outside the accepted set raises `ConfigError` listing the accepted ones, and so does a `limits.*` key outside the set above, along with the near misses `limit.*` and a bare `limits`. Every `limits.*` value is validated as the ceiling is built: a wrong type, a `bool`, a negative number, or a non-finite number raises `ConfigError` naming the field, because a `NaN` ceiling would read as configured and enforce nothing.

The three ceilings marked as requiring a capability are refused with a `ConfigError` when the resolved client does not declare that axis, because reporting a ceiling that is not holding anything is worse than declining to hold it. `DefaultLlmClient` declares `Capability.ALL`: all four ceiling axes, and `estimate` besides. `FunctionClient`, which wraps an `llm_fn`, declares rows alone, so a call, cost, or time ceiling on a run that supplies `llm_fn` is refused rather than settled at zero or counted against an invocation that may be any number of provider calls.

`plan.planning_order` accepts exactly `planning.cardinality_first`, `planning.schema_first`, `planning.locked_sequential`, `planning.locked_parallel`, and `planning.combined`. Cardinality first runs analysis, cardinality, and schema. Schema first runs analysis, schema, and cardinality and is the `LlmSource` default. Both locked schedules require a non-empty caller schema and ordered `plan.keys`; they run analysis and cardinality from the same independent inputs and skip schema generation. The parallel form checks aggregate budget headroom before starting either sibling, but the check is not a reservation. Combined retains the compatible one-call bootstrap. A direct `coordinate_plan` call that omits this argument keeps combined behavior.

Distribution search requires explicit ordered `plan.keys` and at least one root-applicable finite
identity rule. Every rule's `ordered_primary_keys` must exactly match the keys, and its `dimension`
must be one of them. Source rules use provenance `source_configuration`; per-call rules use
`caller_declared` and replace the source list. Unknown fields, unsupported versions or kinds,
malformed scopes, empty or duplicate domains, key mismatches, and unsupported provenance fail before
provider contact. All three distribution-search properties are removed from the provider property map.

`limits.max_cost` requires a calculable bound. The bound uses the prompt's UTF-8 byte length plus a framing allowance at the model's published input rate, and the full `llm.max_tokens` allowance at its output rate. For a model in the package's table, it includes the cache-write price and long-context multipliers wherever the entry supplies them, even for a short request, and includes any regional uplift. If the model has no published or caller-supplied price, or `llm.max_tokens` is not finite and positive, the source raises `ConfigError`. The output allowance alone sets the bound's floor. For the default `gpt-6-luna` and 25,000 tokens, that floor is about $0.020625, so a smaller ceiling refuses every call before provider contact. `estimate` reports the same bound for a request, allowing a caller to size a ceiling against that request.

### Result types

```python
@dataclass(frozen=True)
class Attr:
    name: str
    type: str | None = None


JsonScalar = str | int | float | bool


@dataclass(frozen=True)
class Exact:
    value: JsonScalar


@dataclass(frozen=True)
class OneOf:
    values: tuple[JsonScalar, ...]


@dataclass(frozen=True)
class Range:
    lo: int
    hi: int

Constraint = Exact | OneOf | Range


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
    gaps: list[str] = ...
    asked: str | None = None
    record_origins: list[list[str]] = ...
    requested_attrs: list[str] = ...
    row_estimate: int | None = None
    cardinality_estimate: dict[str, object] | None = None
    primary_keys: list[str] = ...
    observed_key_count: int = 0
    marginal_key_yields: list[int] = ...
    incomplete_key_records: int = 0
    normalization_collisions: list[dict[str, Any]] = ...
    repaired_numbers: int = 0
    split_decision: str | None = None
    filter_contradictions: int = 0
    observed_keys: list[str] = ...
    unmapped_keys: list[str] = ...
    remapped_keys: dict[str, str] = ...
    type_mismatches: list[dict[str, Any]] = ...
    key_collisions: list[dict[str, Any]] = ...
    unchecked_types: list[str] = ...
    truncated_to: int | None = None
    stop_reasons: list[str] = ...
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
    attempts_within_ceiling: int | None = None
```

`FetchResult` keeps exactly those two dataclass fields. Direct Python callers can read its optional
`planning_receipt` property or attach one with `with_planning_receipt`. `to_dict()` adds the receipt
only when present; results without distribution search retain the two-key serialization.

An `IdentityPartitionRule` serializes exactly `dimension`, `ordered_primary_keys`, `kind`,
`allowed_domain`, `parent_scope`, `provenance`, and `contract_version`. Its kind is
`finite_domain`, its version is `identity-partition-rule-1`, and its domain contains nonempty finite
JSON scalar values. A `PlanningReceipt` serializes exactly `contract_version`,
`configured_rule_digests`, `planning_facts`, and `fetch_plan`; its version is
`planning-receipt-1`. The receipt checks agreement among those structures, not authenticity,
factual truth, completeness, or quality.

`cardinality_estimate` carries the accepted non-flat bootstrap estimate as `expected`, `lower`, `upper`, `row_unit`, `source`, `parse_status`, and `diagnostics`; `row_estimate` is its `expected` compatibility value. `primary_keys` lists the ordered identifying attributes used by execution and reduction. `observed_key_count` counts distinct complete keys in the final returned records, after reduction, deduplication, and row capping. `marginal_key_yields` gives the new normalized key identities from each extraction attempt in leaf order, or new exact records when no primary key exists. `incomplete_key_records` counts distinct per-leaf records missing at least one primary-key value. Each entry in `normalization_collisions` contains `primary_keys`, `kept`, and `collided` for two per-leaf key spellings that normalize to the same identity. `repaired_numbers` counts numbers written with thousands separators in an object value, such as `4,470`, that were read as `4470` after strict parsing failed. `filter_contradictions` counts records whose checkable filter column contradicts the filter of the leaf that returned them, once per record and before reduction; `plan.filter_enforcement` decides whether they are kept (`flag`) or removed at the leaf (`drop`), and zero means none was observed, not that every leaf obeyed. `split_decision` says what the root probe decided under `plan.split_policy="evidence"`: `probe_accepted`, `probe_split_length`, `probe_split_incomplete`, `probe_split_shortfall` or `probe_split_failed`; it is `None` under the default policy or when the root could not split and was not probed. The estimate and observed counts do not state that the result is complete.

`Exact` and `OneOf` are how a plan says what one part filters on. A part narrowed to a single value carries `Exact`; a part covering several alternatives carries `OneOf`. Values use the finite JSON scalar types: string, integer, finite number, and boolean. Null, non-finite numbers, and empty or whitespace-only strings are rejected. They are tagged, not held as an untagged scalar or list, because an absent-minded `join` can ask a different question: joined on `", "`, the alternatives `['A, B', 'C']` and `['A', 'B', 'C']` render identically, so a request about one region whose name contains a comma and a request about two regions become the same prompt, and the result says nothing about which was answered. With the tag, that mistake stops type-checking.

`Range` is a third kind that only the planner builds, and only under `plan.range_filters`: an inclusive integer span read from a model proposal value such as `"1946–1952"` on a column declared an integer, or from numeric bucketing. The spans of one proposal must be pairwise disjoint and each must start at or below its end, or the proposal is refused. A range travels only on leaf-task wire version 5.

A caller's own one-element list stays a `OneOf`. Writing a list of one says alternatives were expected, which is a different statement from naming a value outright, and collapsing it would discard the only signal that says so. A part the planner derived from a single value carries `Exact`, because there the count is the plan's own arithmetic and no alternative was dropped.

The claim a split makes about its filters is limited to this: **each distinct enumerated alternative is encoded in exactly one part's disjunction.** The claim does not establish that the enumeration is exhaustive, that alternatives are disjoint (values can denote overlapping categories and the enumeration itself comes from a model), or that the model will honor the filter it was given.

`afetch` and `fetch` return a `FetchResult`, which supports `len()` and iteration over its records. `execute_query` and `execute_query_nl` return the record list alone, because the surface they match returns records.

`estimate` returns an `Estimate`; `LLM.ESTIMATE` returns the same fields in a one-record list. `max_cost` is an upper bound calculated by the same code that bounds a reservation. The estimate and enforcing ceiling therefore use the same figure. When no bound can be established, `bounded` is `False` and `reason` explains what is missing. This is a normal result, not an error. `prompt_bytes` and `max_output_tokens` show the inputs to the bound, and `model` names the model that will answer. An injected client's configuration takes precedence over this source's defaults. `max_cost` covers the whole fetch: it is `max_cost_per_attempt` accumulated over `max_attempts`, as in the ledger. A ceiling equal to `max_cost` admits every attempt included in the estimate. A per-attempt ceiling may be exhausted by one transient failure, causing a retry to be refused. An empty request estimates at zero because it never reaches a model. `Estimate` is exported from the package root.

### Outcome

An empty result is a valid answer from this source, not a failure. `ExecMeta.outcome` carries the reason, from a closed vocabulary declared as `types.Outcome`.

| Value | Set when |
|-------|----------|
| `ok` | Records were produced and no limit intervened |
| `empty_query` | The request was absent, empty, or whitespace, so no model was called |
| `model_returned_none` | The model returned no records at all |
| `filtered_to_empty` | The model returned records and reduction left none, which happens when no record answered any requested attribute |
| `stopped_by_limit` | A non-row stop fired without an interrupted partial answer; records may remain when a limit is detected after extraction. A row cap that leaves no records also takes this outcome |
| `partial` | Extraction was answered but some work was interrupted, including a failed split leaf or a late continuation failure after records were parsed |
| `truncated` | The row ceiling cut the result short and records remain |

The field rides on `FetchResult`, so it is available through `fetch` and `afetch` and not through `execute_query` or `execute_query_nl`, which return `list[dict]` by the contract of the surface they match. A caller on the source surface that needs the reason for an empty list uses the library surface instead.

`Outcome` is defined in `blue_data_llm_source.types` and re-exported from the package root. Partial interruption with an answered response reports `partial`, with simultaneous limits retained in `stop_reasons`. Otherwise a non-row stop reports `stopped_by_limit`; a row-only trim reports `truncated` when records remain. `filtered_to_empty` and `model_returned_none` describe runs where no stop fired.

`partial` takes precedence when some extraction succeeded but other work was interrupted. Limit information remains in `stopped_by`, and row trimming remains in `truncated_to`. A run that loses a part and also reaches a ceiling therefore reports `outcome=partial` while retaining the ceiling in `stopped_by`.

`stopped_by` may be `None` on a `partial` run. It names the axis a caller can raise, and a provider call that raised is not one; the failure is in `meta.gaps`.

### Type mismatches

`ExecMeta.type_mismatches` lists every value that would not coerce to the type its attribute declared. The record is still returned and the value is still present, uncoerced, so the caller keeps the data and learns which field is not what it asked for.

| Key | Type | Description |
|-----|------|-------------|
| `record_index` | `int` | Position of the record in the returned list |
| `attribute` | `str` | The requested attribute whose value did not fit |
| `declared_type` | `str` | The declared type, lowercased |
| `value` | `str` | The repr of the value as it arrived, before coercion |

### Key collisions

`ExecMeta.key_collisions` lists returned keys whose values were unused because another key had already answered the same requested attribute. The first encountered key keeps the attribute, so JSON key order determines the surviving value. The intended spelling cannot be determined here. The source reports the ignored value, so a caller can still decide between both values.

| Key | Type | Description |
|-----|------|-------------|
| `record_index` | `int` | Position of the record in the returned list |
| `attribute` | `str` | The requested attribute both keys reached |
| `kept_key` | `str` | The returned key whose value is in the record |
| `ignored_key` | `str` | The returned key whose value was not used |
| `ignored_value` | any | The value that key carried |

When two *requested* attributes have matching fuzzy forms, the fuzzy index drops the entry it cannot distinguish. Exact keys still match, while keys that match only fuzzily are reported as unmapped rather than assigned by guess. A request that repeats the same name raises `QueryError`.

### Exceptions

All inherit from `LlmSourceError`, defined in `types.py`.

| Exception | Raised when |
|-----------|-------------|
| `ConfigError` | An unknown `llm.*` override, an unknown or invalid `limits.*` key, a missing model, a requested cache backend that is not installed, a ceiling configured on an axis the resolved client does not declare, or a cost ceiling that cannot be bounded because the model has no published price or the output allowance is not a finite positive number |
| `QueryError` | A malformed request or argument, an unknown op, or the same attribute is requested twice, including two identical spellings |
| `TransportError` | A provider call fails, or a response cannot be materialized, has no choices, or has no message content |
| `ParseError` | A model response cannot be read as a list of records |
| `BudgetExceeded` | A configured limit would be crossed by the next call |

`BudgetExceeded` is converted into a result whose stop metadata names the axis; records parsed before a later refusal remain available. A known package error from a run with no answered response reaches the caller with its original message. Other ordinary exceptions escaping the public boundary are wrapped in `LlmSourceError` with the original as `__cause__`. A `BaseException` is not caught.

### Supported helpers

Seven module-level functions are exported from the package root alongside the types, for adapters that need them without reaching into a private module.

| Name | Signature | Returns |
|------|-----------|---------|
| `rates_for` | `rates_for(model: str) -> tuple[float, float] \| None` | Published input and output price per token for a known model or a dated variant of one, `None` otherwise |
| `can_bound` | `can_bound(model: str, max_tokens: object, properties: dict \| None = None) -> bool` | Whether a cost ceiling could be claimed for these arguments |
| `priceable_models` | `priceable_models() -> tuple[str, ...]` | The model names the published table carries, sorted |
| `published_defaults` | `published_defaults(model: str) -> dict[str, object] \| None` | The model's table entry: `reasoning_efforts`, `default_reasoning_effort`, `temperature`, `output_token_parameter`, `cached_input_price_per_token`, `cache_write_price_per_token`, `long_context_threshold_tokens`. `None` for a model the table does not carry. A caller's settings override these defaults for a call |
| `meta_view` | `meta_view(meta: ExecMeta, *, verbose: bool = False) -> dict[str, Any]` | `ExecMeta` as a JSON-ready dict, under one field policy shared by every adapter |
| `key_identity` | `key_identity(record: Mapping[str, Any], primary_keys: Sequence[str], attrs: Sequence[Attr]) -> KeyTuple \| None` | The record's complete ordered identity, or `None` when a key component is absent. `KeyTuple` is exported with it |
| `redact` | `redact(text: str) -> str` | *text* with anything that could be a credential replaced by `***`. One rule for every surface that hands a caller text it did not write: an error message, a log line, a traceback, a request-side field of a run report. Not applied to records, which are the answer |

The price table itself stays internal. It is a mutable snapshot of time-sensitive list prices, and publishing it would be a contract this package cannot keep across a price change; what is published is behavior over it. Supply `llm.price_per_input_token` and `llm.price_per_output_token` to bound a model the table does not carry.

`meta_view` publishes three field tiers:

- Always present: `outcome`, `model`, `calls`, `prompt_tokens`, `completion_tokens`, `cost`, `elapsed_seconds` (the `latency_seconds` field under the name the CLI and MCP JSON use), `row_estimate`, `cardinality_estimate`, `primary_keys`, `observed_key_count`, `marginal_key_yields`, `incomplete_key_records`, `filter_contradictions`, `gaps`, `stop_reasons`, and `requested_attrs`.
- Present when populated: `normalization_collisions`, `repaired_numbers`, `split_decision`, `unchecked_types`, `record_origins`, `asked`, `stopped_by`, `truncated_to`, `cache_hits`, and `accounting_known` when false.
- Verbose only: `unmapped_keys`, `remapped_keys`, `type_mismatches`, and `key_collisions`.

`observed_keys` is omitted because it grows with the answer and `observed_key_count` carries the usable count. The MCP server requests the verbose view; the CLI exposes it through `--verbose`.

`meta_view` masks credential-shaped text in every string in result metadata, including diagnostic keys and values that refer to discarded records. Returned records remain unchanged. Direct Python `ExecMeta` values remain raw; callers publishing them need to mask their own output.

---

## 1a. Orchestrator tools: five of the eight

`DataSource` offers `get_nl_tools`, `execute_nl_tool` and `execute_query_nl_tools`, a tool-calling interface for an orchestrating model. It is built around the relational shape the rest of the data layer has: databases, collections, columns, row samples, foreign keys. A source answering from a model's own knowledge has only part of that, so five of the eight tools are advertised and three are not.

| Tool | Served | Behavior |
|------|--------|-----------|
| `list_databases` | yes | `["llm"]`, the one virtual database |
| `list_collections` | yes | `["public"]`, the one virtual collection |
| `get_schema` | yes | `{"entities": {...}, "relations": {}}` for the virtual entity |
| `execute_query` | yes | `execute_query(tool_input["query"], ...)`, the one tool that reaches a provider |
| `get_metadata` | no | Would return `{}` |
| `get_column_stats` | no | Would return `None` |
| `get_relations` | no | Would return `{}` |
| `get_sample` | yes | Returns `None`; shared `sample_size` forwards to keyword-only `fetch_entity_stats(..., row_sample_size=10)`, without a provider call |

A tool that answers with an empty result and no explanation is worse than one that is absent, because the model calling it has no way to tell "nothing here" from "wrong question". The three unserved ones are therefore filtered out of the advertisement instead of answered.

`get_nl_tools` returns the base class's own definitions for the five, filtered by name. They are not rewritten here: the base dispatcher is what reads the input, so a schema written locally would be a second copy of its contract, and a key renamed or a `required` entry added upstream would leave this source advertising a call the dispatcher then rejects. `database` and `collection` stay in the schemas as upstream declares them, and the dispatcher reads each from the tool input with a fallback to the argument of the same name.

`execute_nl_tool` serves those five by delegating to the base dispatcher, and refuses any other name with a `ConfigError` listing the ones that are available. Nothing on this path leaves the `LlmSourceError` hierarchy: an unknown name, a `name` that is not a string, a `tool_input` that is not a mapping, and a missing required key all arrive as `ConfigError`, `QueryError`, or `LlmSourceError`, which the base's own `ValueError`, `KeyError`, and `AttributeError` do not.

Ceilings reach the `execute_query` tool the way they reach the query surface. A `limits.*` key in `optional_properties` is handed to `execute_query` by the dispatcher and binds that call and no other; a misspelled one is refused on every tool rather than only on the one that spends.

`execute_query_nl_tools` is not served and raises `ConfigError`. It drives a loop of up to ten provider calls that this source's ceilings do not govern, so serving it would place an unbounded spend path behind a method this package never chose to offer. `execute_query_nl` is the natural-language surface here, and its spend and time ceilings are enforced.

## 2. MCP Server

Package `blue-data-llm-mcp`, entry point `blue-data-llm-mcp`, speaking MCP over stdio. It exposes the source to LLM agents as five tools: `llmdb_query`, `llmdb_estimate`, `llmdb_list_models`, `llmdb_inspect_model` and `llmdb_inspect_metadata`. Every name carries the server's own prefix, because a client holds one flat tool namespace and two Blue Data servers in it would otherwise be offering the same name for different things. Beyond moving values across the boundary the server holds one piece of logic of its own: it enforces the schema it advertises.

The server validates argument shape against the published JSON schema before calling the library. It checks object shape, allowed keys, required fields, JSON types, non-blank strings, unique arrays, and numeric minima. The SDK prevalidator is disabled for this handler so withdrawn settings can receive the package's migration message rather than a generic schema error. The library retains domain and configuration validation, including empty queries, accepted overrides, enforceable ceilings, model pricing, and response parsing.

Library failures retain a known error kind and use fixed messages. Schema rejections name the argument and its declared constraint. Values outside a declared set are not repeated; unknown keys are quoted so callers can locate misspellings. Credential-shaped text is masked in every returned message.

The server uses the low-level API because tool schemas are maintained explicitly as the JSON contract presented to clients.

### Tool: `llmdb_query`

```json
{
  "name": "llmdb_query",
  "description": "Fetch structured records from a language model's own knowledge. Returns a list of records and a meta object describing the call, including any reason rows may be missing.",
  "inputSchema": {
    "type": "object",
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
            {
              "type": "string",
              "pattern": "\\S",
              "description": "Attribute name."
            },
            {
              "type": "object",
              "description": "Attribute name with a declared value type.",
              "properties": {
                "name": {
                  "type": "string",
                  "pattern": "\\S",
                  "description": "Attribute name."
                },
                "type": {
                  "type": "string",
                  "description": "Declared type, checked against the returned values. One of int, integer, float, number, bool, boolean, date, str, string, text. Any other name is accepted, checks nothing, and is reported once in meta.unchecked_types."
                }
              },
              "required": [
                "name"
              ],
              "additionalProperties": false
            }
          ]
        },
        "uniqueItems": true
      },
      "context": {
        "type": "string",
        "description": "Extra text that clarifies the query or an attribute name."
      },
      "max_records": {
        "type": "integer",
        "description": "Stop after this many records.",
        "minimum": 1
      },
      "limits.max_cost": {
        "type": "number",
        "description": "Refuse a call that would take this run's spend past this many dollars. The output allowance is reserved in full, so llmdb_estimate reports the floor a ceiling has to clear.",
        "minimum": 0
      },
      "limits.max_seconds": {
        "type": "number",
        "description": "Refuse a call once the run has been going this long.",
        "minimum": 0
      },
      "limits.max_calls": {
        "type": "integer",
        "description": "Refuse more than this many provider calls, retries included.",
        "minimum": 0
      },
      "plan.mode": {
        "type": "string",
        "enum": [
          "flat",
          "partitioned",
          "row_by_row"
        ],
        "description": "How the ask is decomposed: `flat`, `partitioned`, or `row_by_row`. `flat` asks once and is the default; non-flat modes add bounded planning before leaf extraction. No mode is selected automatically, and an unknown one is refused."
      },
      "plan.max_continuations": {
        "type": "integer",
        "description": "How many times a part may ask again for records it did not get. Zero by default, so each part asks once. It bounds asks per part, not provider attempts or planning calls; an ask may be retried up to limits.max_retries times.",
        "minimum": 0
      },
      "plan.max_leaves": {
        "type": "integer",
        "description": "The most parts a plan may hold; the term that bounds its worst cost. In partitioned mode, values past it run grouped several to a part rather than being dropped. In row_by_row, an entity list past it falls back to one unsplit ask. Default: 100.",
        "minimum": 1
      },
      "plan.partition_key": {
        "type": "string",
        "pattern": "\\S",
        "description": "Preferred dimension for the root partition proposal. Descendants may choose other dimensions, but may not repeat an ancestor split dimension."
      },
      "plan.max_depth": {
        "type": "integer",
        "description": "Deepest partition level. Zero disables subdivision. Default: 2.",
        "minimum": 0
      },
      "plan.max_partitions": {
        "type": "integer",
        "description": "Most children proposed for one partition node. A value below two leaves the node unsplit. Default: 20.",
        "minimum": 0
      },
      "plan.target_rows_per_leaf": {
        "type": "integer",
        "description": "Uncertain row-count hint above which a descendant may split again. It is not a completeness target. Default: 50.",
        "minimum": 1
      },
      "plan.schema_mode": {
        "type": "string",
        "enum": [
          "preserve",
          "enrich"
        ],
        "description": "`preserve` keeps declared attributes and is the default. `enrich` lets non-flat planning append inferred attributes while preserving declarations."
      },
      "plan.planning_order": {
        "type": "string",
        "enum": [
          "planning.cardinality_first",
          "planning.schema_first",
          "planning.locked_sequential",
          "planning.locked_parallel",
          "planning.combined"
        ],
        "description": "Planning fact schedule: planning.cardinality_first, planning.schema_first, planning.locked_sequential, planning.locked_parallel, or planning.combined. The locked schedules require a non-empty caller schema and ordered plan.keys."
      },
      "plan.expose_estimate": {
        "type": "boolean",
        "description": "Whether extraction prompts include the planner's soft row estimate."
      },
      "plan.key": {
        "type": "string",
        "pattern": "\\S",
        "description": "The attribute identifying a record, used to merge duplicates returned by different parts. Must be one of the requested attributes. Without it records from different parts are not merged, though exact whole-record duplicates are still removed."
      },
      "plan.keys": {
        "type": "array",
        "description": "Ordered fields that jointly identify one record.",
        "items": {
          "type": "string",
          "pattern": "\\S"
        },
        "uniqueItems": true
      },
      "plan.exact_rows": {
        "type": "integer",
        "description": "Hard row target for a single-leaf plan. A multi-leaf plan does not apply it and reports exact_rows_not_leaf_bound.",
        "minimum": 0
      },
      "plan.max_exclusion_bytes": {
        "type": "integer",
        "description": "Most encoded bytes of whole key exclusions in one continuation.",
        "minimum": 0
      },
      "plan.leaf_prompt": {
        "type": "string",
        "enum": [
          "keyed-leaf-fetch-1",
          "keyed-leaf-fetch-2"
        ],
        "description": "Leaf prompt contract for non-flat modes. `keyed-leaf-fetch-1` is the default; `keyed-leaf-fetch-2` asks for every matching record, states the expected count as a sentence, and allows an empty answer only when nothing matches. Refused in flat mode."
      },
      "plan.child_wording": {
        "type": "string",
        "enum": [
          "parent",
          "canonical"
        ],
        "description": "How a child part is worded in partitioned mode: `parent` (default) copies the parent's wording; `canonical` derives it from the request and the child's filters."
      },
      "plan.filter_enforcement": {
        "type": "string",
        "enum": [
          "flag",
          "drop"
        ],
        "description": "Records that contradict their part's filter are kept (`flag`, default) or removed at the part before merging (`drop`). Both count them in meta.filter_contradictions. `drop` is refused in flat mode."
      },
      "plan.range_filters": {
        "type": "boolean",
        "description": "Read partition values written as integer spans, on an integer column, as range filters. Requires partitioned mode and plan.leaf_prompt `keyed-leaf-fetch-2`."
      },
      "plan.partition_guardrails": {
        "type": "boolean",
        "description": "Send partition proposals with key and value rules and refuse a numeric key bucketed like a measurement. Requires partitioned mode."
      },
      "plan.split_policy": {
        "type": "string",
        "enum": [
          "always",
          "evidence"
        ],
        "description": "`always` (default) splits the root of a partitioned plan whatever it holds. `evidence` asks the root once first and splits only when that answer was cut off or failed, or holds fewer than half the keys a large estimate expects; otherwise that answer is the result. Requires partitioned mode."
      },
      "plan.split_threshold_rows": {
        "type": "integer",
        "description": "Under plan.split_policy `evidence`, the estimate above which a short root answer splits. Default: 120.",
        "minimum": 0
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

The twenty-one plan settings and the three ceilings are the library's own property names, unrenamed. They are passed through as the call's properties, which is the same route `optional_properties` takes on the query surface and `--limit-*` takes on the command line. General properties merge over the source's for that call and are not written back; `limits.*` can only tighten effective ceilings, and `None` leaves them unchanged; `limits.max_retries=None` is invalid. A key this layer did not recognize would never reach the library's own limit validation, so a translated name would have been dropped in silence rather than refused.

**Returns:** JSON text content, `{"records": [...], "meta": {...}}`. The meta object always carries `outcome`, accounting fields, `gaps`, `stop_reasons`, `requested_attrs`, `row_estimate`, `cardinality_estimate`, `primary_keys`, `observed_key_count`, `marginal_key_yields`, `incomplete_key_records`, and `filter_contradictions`. `normalization_collisions` appears when non-empty, `repaired_numbers` when non-zero, and `split_decision` when a root probe ran. The cardinality estimate is accepted non-flat bootstrap evidence or JSON `null`; neither it nor the observed-key fields is a completeness measure. `gaps` contains operational diagnostics and an empty list means only that none fired. `stop_reasons` is the ordered list of simultaneous extraction limits; `stopped_by`, when present, is its first member. `accounting_known` appears only when false, and format diagnostics, `truncated_to`, `record_origins`, and `asked` appear when applicable.

An empty result is not an error. A budget refusal before any extraction answer returns no records
with `outcome` of `stopped_by_limit`; a later refusal after an extraction answered retains those
records with `outcome` of `partial`. The stop fields name the limit in both cases. An empty or
whitespace `query` returns no records with `outcome` of `empty_query`, and no model is called. An
agent can distinguish those states from a model that knows nothing and from a failure without
parsing a message.

A ceiling reaches the ledger the same way one configured on a source does: claimed before the provider is contacted, so a call that would cross one is refused, not accounted for afterwards. `max_records` is not one of them and is not counted as one. It caps the rows in the result, not the spend, which is why it reports `truncated` rather than `stopped_by_limit`.

The input schema is the whole of what this tool exposes, and the ceilings in it are per call. The server builds a fresh source for each call from the `llm.*` settings in its environment (`BLUE_DATA_LLM_MODEL` for `llm.model`, and `BLUE_DATA_LLM_SOURCE_REASONING_EFFORT`, `_MAX_TOKENS`, `_TEMPERATURE`, `_TIMEOUT`, `_MAX_PARALLEL`, `_CACHE`, `_CACHE_DIR`, `_PRICE_PER_INPUT_TOKEN` and `_PRICE_PER_OUTPUT_TOKEN` for the matching keys), read on every call. An unset or blank variable leaves the library default; a malformed or refused value fails that call with a `ConfigError` naming the variable, never its value. The operator's ceilings come from `BLUE_DATA_LLM_SOURCE_LIMIT_COST`, `_LIMIT_CALLS` and `_LIMIT_SECONDS`, also read on every call, and are merged with the call's own `limits.*` arguments with the lower value winning, so a call can tighten an operator ceiling but never raise or remove it. Without an operator ceiling, a call that names none has none. No `plan.*` setting is read from the environment; a call's `plan.*` arguments apply to that call only.

**On error:** a failed tool call whose text is an error kind followed by a safe message, for example `QueryError: Query or attributes are invalid.`

---

### Tool: `llmdb_estimate`

```json
{
  "name": "llmdb_estimate",
  "description": "Bound what one flat llmdb_query of the same request could cost, without calling the model. Returns an upper bound on the whole fetch, retries included. Reads no plan.* setting, so a split request can cost several times this figure.",
  "inputSchema": {
    "type": "object",
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
            {
              "type": "string",
              "pattern": "\\S",
              "description": "Attribute name."
            },
            {
              "type": "object",
              "description": "Attribute name with a declared value type.",
              "properties": {
                "name": {
                  "type": "string",
                  "pattern": "\\S",
                  "description": "Attribute name."
                },
                "type": {
                  "type": "string",
                  "description": "Declared type, checked against the returned values. One of int, integer, float, number, bool, boolean, date, str, string, text. Any other name is accepted, checks nothing, and is reported once in meta.unchecked_types."
                }
              },
              "required": [
                "name"
              ],
              "additionalProperties": false
            }
          ]
        },
        "uniqueItems": true
      },
      "context": {
        "type": "string",
        "description": "Extra text that clarifies the query or an attribute name."
      },
      "limits.max_cost": {
        "type": "number",
        "description": "Refuse a call that would take this run's spend past this many dollars. The output allowance is reserved in full, so llmdb_estimate reports the floor a ceiling has to clear.",
        "minimum": 0
      },
      "limits.max_seconds": {
        "type": "number",
        "description": "Refuse a call once the run has been going this long.",
        "minimum": 0
      },
      "limits.max_calls": {
        "type": "integer",
        "description": "Refuse more than this many provider calls, retries included.",
        "minimum": 0
      }
    },
    "required": [],
    "additionalProperties": false
  }
}
```

The request half of the schema is the same object `llmdb_query` declares, shared between the two so they cannot drift into pricing one request and running another. The ceilings are there because a call ceiling narrows the attempts a fetch may make and so narrows what a fetch can cost. `max_records` is absent: `estimate` takes no row cap, and advertising an input that cannot change the answer is how a caller comes to believe it asked for something it did not get.

**Returns:** JSON text content carrying the `Estimate` fields, every one of them, set or not: `bounded`, `max_cost`, `max_cost_per_attempt`, `max_attempts`, `reason`, `model`, `prompt_bytes`, `max_output_tokens`, `attempts_within_ceiling`. The last is `null` when the call sets no cost ceiling.

```json
{
  "bounded": true,
  "max_cost": 0.06258862500000001,
  "max_cost_per_attempt": 0.020862875000000003,
  "max_attempts": 3,
  "reason": null,
  "model": "gpt-6-luna",
  "prompt_bytes": 801,
  "max_output_tokens": 25000,
  "attempts_within_ceiling": null
}
```

Neither cost is rounded, unlike the spend in a fetch's `meta`. That one is the ledger charge for completed work and may retain a conservative reservation; these are upper bounds, and rounding a bound to six places can round it down. An agent that set `limits.max_cost` to a figure this tool understated would have the last attempt that figure accounted for refused.

`bounded` is `false` with a `reason` when no bound can be established, which is a successful call rather than an error, for the reason §1 gives for the method. Nothing on this path contacts the provider, so an agent can size a request without paying for the question.

---

### Tool: `llmdb_list_models`

```json
{
  "name": "llmdb_list_models",
  "description": "List the pricing bases this package carries a published price for. A dated variant such as gpt-4o-2024-08-06 is priced against gpt-4o and is not listed separately, so this is not every name a ceiling can be claimed against. A ceiling needs a listed name, a dated variant of one, or rates the deployment supplied. llmdb_inspect_model settles a single name. Contacts no provider and costs nothing.",
  "inputSchema": {
    "type": "object",
    "description": "No inputs.",
    "properties": {},
    "required": [],
    "additionalProperties": false
  }
}
```

**Returns:** JSON text content, `{"models": [...]}`, the names only. They are pricing bases, not the whole set of boundable names: `rates_for` resolves a dated variant against the base it extends, so `gpt-4o-2024-08-06` is bindable and is not listed. The rates behind the names are a static snapshot of published list prices with no effective date, so the table is not published as a contract; a deployment that maintains its own rates supplies them through `BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN` and `BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN` instead.

---

### Tool: `llmdb_inspect_model`

```json
{
  "name": "llmdb_inspect_model",
  "description": "Report one model's published input and output rates, and whether a limits.max_cost ceiling can be bounded against it on this server. published_defaults gives the model's published request contract (reasoning efforts, default effort, when temperature is accepted, output-token parameter), its cached-input and cache-write rates and its long-context threshold. These are the model's defaults, which this deployment's settings override. A model the price table does not carry is a successful answer with priceable false and null published_defaults, not an error. Contacts no provider and costs nothing.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "model": {
        "type": "string",
        "description": "Model name to look up, e.g. gpt-4o. Exact names and dated variants only; a longer name is treated as a different model rather than as a variant of the one it starts with."
      }
    },
    "required": [
      "model"
    ],
    "additionalProperties": false
  }
}
```

**Returns:** JSON text content carrying `model`, `priceable`, `price_per_input_token`, `price_per_output_token`, `max_output_tokens`, `cost_ceiling_supported`, and `published_defaults`.

```json
{
  "model": "gpt-4o",
  "priceable": true,
  "price_per_input_token": 2.5e-06,
  "price_per_output_token": 1e-05,
  "max_output_tokens": 4096,
  "cost_ceiling_supported": true,
  "published_defaults": {
    "reasoning_efforts": [],
    "default_reasoning_effort": null,
    "temperature": "always",
    "output_token_parameter": "max_tokens",
    "cached_input_price_per_token": 1.25e-06,
    "cache_write_price_per_token": null,
    "long_context_threshold_tokens": null
  }
}
```

These tools let clients check pricing support before a query is refused under `limits.max_cost`. `cost_ceiling_supported` requires both usable rates and a finite output allowance, so it cannot be inferred from `priceable` alone. It is evaluated with the source properties used for a fetch. Caller-supplied rates can therefore make `cost_ceiling_supported` true while `priceable` remains false, because `priceable` describes only the packaged table. An unsupported model returns `priceable: false` and `published_defaults: null` as a successful inspection result. `published_defaults` is the model's entry in the package's table. A deployment setting such as `BLUE_DATA_LLM_SOURCE_REASONING_EFFORT` overrides the matching default.

---

### Tool: `llmdb_inspect_metadata`

```json
{
  "name": "llmdb_inspect_metadata",
  "description": "Report the protocol, the variant, and the library version.",
  "inputSchema": {
    "type": "object",
    "description": "No inputs.",
    "properties": {},
    "required": [],
    "additionalProperties": false
  }
}
```

**Returns:** JSON text content, `{"protocol": "llm", "variant": "openai", "version": "1.0.0"}`.

The MCP schema exposes the same 21 standard planning controls as the CLI. It intentionally omits the
three distribution-search properties, and `llmdb_query` does not return the direct-Python planning
receipt.

Requesting an unknown tool returns `UnknownTool` and lists the available tools. No tool exposes a provider endpoint or credential. Inspection results report the protocol, variant, library version, output allowance, the model's published defaults, and whether a cost ceiling can be bounded; caller-supplied rates are used for that decision but are not returned. `llmdb_inspect_metadata` reads no deployment setting, so it answers even while a malformed one fails the other tools that read them.

Messages, model names, requested attributes, rewritten queries, unchecked types, and unbounded-estimate reasons pass through `redact`. A test examines every returned string and requires it to be redacted or explicitly exempted.

---

## 3. CLI

Package `blue-data-llm-cli`, entry point `blue-llmdb`. Built with Click for argument parsing and Rich for table output, wrapping the library's synchronous `fetch` and `estimate`. Rich markup is disabled on output, because a record value containing square brackets is data, not a style tag.

Twelve commands over four components, in the Blue family's `<cli> <component> <action> <parameters>` shape.

```
blue-llmdb [--version] [-h|--help] [--profile NAME]
├── profile
│   ├── add NAME
│   ├── remove NAME [--confirm]
│   ├── list [--format]
│   ├── config [PARAM [VALUE]] [--profile NAME]
│   ├── inspect [NAME] [--format]
│   └── select NAME
├── data
│   ├── query QUERY [query options] [--format]
│   └── estimate QUERY [estimate options] [--format]
├── model
│   ├── list [--format]
│   ├── inspect NAME [--format]
│   └── select NAME
└── metadata
    └── inspect [--format]
```

| Command | What backs it |
|---------|---------------|
| `profile *` | CLI-local state under `~/.blue-llmdb`. No library call |
| `data query` | `LlmSource.fetch` |
| `data estimate` | `LlmSource.estimate` |
| `model list` | `priceable_models()` |
| `model inspect` | `rates_for`, `can_bound`, `published_defaults` |
| `model select` | CLI-local, plus `rates_for` for the unpriced warning |
| `metadata inspect` | `LlmSource.fetch_metadata`, `__version__` |

No `database`, `schema` or `cache` component is built. `fetch_databases()` returns one synthetic name and the create/import methods return `{}`, so a `database` component would be a two-row constant over four operations that do nothing; the source has no stored schema to expose; and cache management would need cache-introspection APIs the library does not have. `data search` is absent because searching and querying are one operation for this source, and `data import`/`data export` are absent because materializing belongs to the receiving source and export is `--format` plus shell redirection.

### Global options

```
blue-llmdb [--version] [-h|--help] [--profile NAME] COMMAND
```

| Option | Description |
|--------|-------------|
| `--version` | Prints `blue-llmdb <version>`, read from the installed distribution |
| `--profile NAME` | Configuration profile. Bound to `envvar="BLUE_DATA_PROFILE"`, and falls back to the selection in `~/.blue-llmdb/.selected.profile`. A named profile that does not exist is refused as `ERROR: PROFILE` rather than run as an empty one, because a misspelled name would silently drop the ceilings the profile holds |

`--verbose`, `--log` and `--log-limit` are per-command, not global, on the two `data` commands.

### Configuration

Precedence is **command option > environment variable > profile > built-in default**, stated in `blue-llmdb --help` and in each affected command's help. The resolver owns the bottom three tiers and exports them into the environment before any command body runs, which is where the provider SDK reads its own configuration from; a command option is applied on top and never reaches the resolver. `profile inspect` therefore reports `environment`, `profile:<name>`, `default` or `unset`, and can never report `flag`. The chain covers the settings the spec declares a parameter for, which is what `profile inspect` lists. The twenty-one `--plan-*` controls are outside it by decision, not by omission: a plan setting describes one ask, not a deployment, and a partition key or a row target left in a profile would apply itself to the next, unrelated query. `--effort` is the exception among the planning-adjacent options, because a continuation allowance is an appetite for spending rather than a shape.

Four family parameters — `OPENAI_API_KEY` (secret), `BLUE_DATA_LLM_MODEL`, `BLUE_DATA_CLI_FORMAT`, `BLUE_DATA_CLI_LOG_LIMIT` — plus fifteen `BLUE_DATA_LLM_SOURCE_*` parameters, one per request setting the CLI exposes. Only the first two are walked by `blue-llmdb profile config` with no argument, and none of them gates a command: `profile`, `model`, `metadata` and `data estimate` contact no provider and run against an empty profile.

`BLUE_DATA_LLM_SOURCE_HOME` moves the CLI home. It is an environment setting, not a profile parameter, because it is the directory the profiles themselves are read from.

Profiles live at `~/.blue-llmdb/.profiles` as INI, one section per profile, at file mode 0600. The CLI creates the profile directory at 0700. An existing directory is refused if group or other users can write to it; an existing 0755 directory is accepted with a warning and left unchanged. Writes use a temporary file in the same directory, fsync, and `os.replace`, so an interrupted write cannot leave a partial profile file. A path inside the home that is a symlink out of it is refused. `profile remove` deletes only the named section. If that profile was selected, the command selects an existing one (`default` if available, otherwise the first remaining profile) and reports the choice. The last profile cannot be removed because the selection must name an existing profile.

A secret is never accepted as a value on the command line. The prompt hides input. A `$NAME` reference is resolved from the environment at use and keeps the secret out of the profile. A directly entered secret is stored as `enc:` plus reversible base64 of its UTF-8 value. The `0600` file mode restricts local access on POSIX systems; anyone who can read the profile or a copied backup can decode the secret. `MASK` is `***`, and profile commands do not print a secret value.

### `blue-llmdb data query`

```
blue-llmdb data query QUERY [--attr NAME[:TYPE]]... [--context TEXT] [--max-records N]
                [--model MODEL] [--max-tokens N] [--temperature X] [--timeout SECONDS]
                [--reasoning-effort NAME]
                [--max-parallel N] [--cache|--no-cache] [--cache-dir PATH]
                [--price-in X] [--price-out X]
                [--limit-cost AMOUNT] [--limit-seconds N] [--limit-calls N] [--max-retries N]
                [--effort low|medium|high] [--variant NAME]
                [--plan-mode MODE] [--plan-continuations N] [--plan-key NAME]
                [--plan-keys NAME]... [--plan-exact-rows N] [--plan-exclusion-bytes N]
                [--plan-leaves N] [--plan-partition-key NAME]
                [--plan-depth N] [--plan-partitions N] [--plan-target-rows N]
                [--plan-schema-mode preserve|enrich]
                [--plan-planning-order ORDER]
                [--plan-expose-estimate|--no-plan-expose-estimate]
                [--plan-leaf-prompt CONTRACT] [--plan-child-wording parent|canonical]
                [--plan-filter-enforcement flag|drop]
                [--plan-range-filters|--no-plan-range-filters]
                [--plan-partition-guardrails|--no-plan-partition-guardrails]
                [--plan-split-policy always|evidence] [--plan-split-threshold-rows N]
                [--format table|json|nl|csv] [-v|--verbose] [-l|--log] [--log-limit N]
```

| Argument / Option | Property or argument | Description |
|-------------------|----------------------|-------------|
| `QUERY` | `fetch(query=)` | Natural language request (required) |
| `--attr NAME[:TYPE]` | `fetch(attrs=)` | Requested output attribute; repeat for several. Types understood: `int`, `float`, `bool`, `date`, and `str`/`string`/`text`, which is checked but not converted. Any other name is accepted, checks nothing, and is reported in `unchecked_types` |
| `--context TEXT` | `fetch(context=)` | Extra text that clarifies the query |
| `--max-records N` | `fetch(max_records=)` | The row ceiling. `fetch` clamps it by the effective `Limits.max_rows`, so this is the only flag for that knob and there is no `--limit-rows` |
| `--model MODEL` | `llm.model` | Model to call |
| `--max-tokens N` | `llm.max_tokens` | Output allowance for the call, and the lever on the cost floor |
| `--temperature X` | `llm.temperature` | Sampling temperature |
| `--reasoning-effort NAME` | `llm.reasoning_effort` | Model reasoning effort: `none`, `minimal`, `low`, `medium`, `high`, `xhigh` or `max`, within the set the model accepts |
| `--timeout SECONDS` | `llm.timeout` | Seconds to wait for one call |
| `--max-parallel N` | `llm.max_parallel` | Calls in flight at once |
| `--cache` / `--no-cache` | `llm.cache` | Answer a repeated call from the on-disk cache |
| `--cache-dir PATH` | `llm.cache_dir` | Where cached answers are kept |
| `--price-in X`, `--price-out X` | `llm.price_per_input_token`, `llm.price_per_output_token` | Rates for a model the price table does not carry |
| `--limit-cost`, `--limit-seconds`, `--limit-calls` | `limits.max_cost`, `limits.max_seconds`, `limits.max_calls` | Per-run ceilings, claimed before the call rather than reported after it |
| `--max-retries N` | `limits.max_retries` | Retries allowed per ask |
| `--effort LEVEL` | `plan.max_continuations` = 0/1/2 | A CLI convenience over the plan setting, not library policy. An explicit `--plan-continuations` wins. Flat `medium`/`high` requires a requested `--plan-key` or `--plan-keys` and fails before source construction when none is supplied |
| `--variant NAME` | `connection.variant` | Source variant within the `llm` protocol |
| the 21 `--plan-*` options | `plan.*` | One option per planning control, passed through under the library's own key; see the planning controls above |
| `--rate-limit` | — | Refused as `ERROR: USAGE`, exit 2, carrying the library's own reason. Declared so the refusal can name the remedy rather than producing Click's generic "no such option" |
| `--format` | — | `table`, `json`, `nl` or `csv`. Default from `BLUE_DATA_CLI_FORMAT`, else `table` |
| `-v`, `--verbose` | — | Adds key alignment, type mismatch and collision details. Gaps and stop reasons are visible without it |
| `-l`, `--log`, `--log-limit N` | — | Each step of the run on standard error, truncated, plus a traceback on failure |

`llm.stream` has no option: streaming has no meaning for a command whose result is a parsed record set. Of the eleven accepted `llm.*` keys, ten are reachable.

**Example**

```
$ blue-llmdb data query "the member states of the European Union" --attr country --attr accession_year:int

┏━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ country ┃ accession_year ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ France  │ 1958           │
│ Germany │ 1958           │
└─────────┴────────────────┘
2 records | model gpt-6-luna | 1 call | 46 tokens | row estimate unknown | $0.0000 | 0.31s
settings: llm.cache_dir=~/.blue-llmdb/cache (default), llm.model=gpt-6-luna (default), plan.max_continuations=0 (default)
```

Under `--format json` the same run prints one object with **three** keys: `records`, `meta`, and `settings`. The MCP tool result has `records` and `meta`.

`meta` is `meta_view(result.meta, verbose=...)` and follows the three-tier policy in §1. It always carries `outcome`, the accounting fields, `gaps`, `stop_reasons`, `requested_attrs`, `row_estimate`, `cardinality_estimate`, `primary_keys`, `observed_key_count`, `marginal_key_yields`, `incomplete_key_records`, and `filter_contradictions`. `normalization_collisions`, `repaired_numbers`, `split_decision`, `unchecked_types`, `record_origins`, `asked`, `stopped_by`, `truncated_to` and `cache_hits` appear when populated; `accounting_known` appears only when false. `--verbose` adds `unmapped_keys`, `remapped_keys`, `type_mismatches` and `key_collisions`. The table footer prints the point row estimate or `unknown`, and `stopped_by` is not gated on `--verbose`.

`settings` is the CLI's own envelope, with a fixed two-map schema:

```json
{"settings": {"applied": {"<property>": "<value>"}, "source": {"<property>": "flag|environment|profile:<name>|default"}}}
```

Its scope is exactly the values this invocation resolved and passed to the library, and the tier each came from. It is **not** a report of the library's effective configuration: that resolution is three layers deep and private, and reporting it would be claiming knowledge the CLI does not have. `<property>` is the library's own dotted key, so the same spelling appears in the CLI, in the profile file and in the MCP schema. A property the invocation did not set appears in neither map. Under `--format table` the same pairs print as a `settings:` footer; under `--format nl` only the ceilings are named.

### `blue-llmdb data estimate`

```
blue-llmdb data estimate QUERY [--attr NAME[:TYPE]]... [--context TEXT]
                [--model MODEL] [--max-tokens N] [--temperature X] [--timeout SECONDS]
                [--reasoning-effort NAME]
                [--price-in X] [--price-out X]
                [--limit-cost AMOUNT] [--limit-seconds N] [--limit-calls N] [--max-retries N]
                [--variant NAME] [--format table|json|nl|csv] [-l|--log]
```

`LlmSource.estimate` prices one flat fetch and reads no `plan.*` setting, but it does resolve the full `Limits` and derives its attempt count from them, so the whole `--limit-*` family and `--max-retries` are accepted and move the published number. `--max-records`, `--effort` and every `--plan-*` option are refused as `ERROR: USAGE`, naming the flag and pointing at `data query`. Refusing is stricter than the library, which would accept and ignore them, and a setting accepted and ignored is what the library's closed key set exists to prevent.

Any option that appears on both `data` commands has the same name, type, default and meaning on both.

Under `--format json` the result is the nine `Estimate` fields plus the same `settings` envelope.

### `blue-llmdb model`

`model list` prints the names in the published price table, from `priceable_models()`. `model inspect NAME` prints its published rates, the output allowance the bound is judged against, and whether a cost ceiling could be claimed, from `rates_for` and `can_bound`, and the model's table entry from `published_defaults`, one `published_defaults.*` row each in the table and CSV formats; an unpriced model reports `null` rates and `null` defaults instead of dropping the keys. The `nl` sentence names the floor a cost ceiling has to clear, with the regional uplift included where the model carries it. `model select NAME` stores the name as the profile's `BLUE_DATA_LLM_MODEL`, so `--model` still overrides it for one request, and warns when the model has no published rate, naming `--price-in`/`--price-out` and the two profile parameters.

### `blue-llmdb metadata inspect`

Prints the protocol, the variant, and the version of the library underneath, from `fetch_metadata` and `__version__`.

### Output formats

`--format table|json|nl|csv` is on every command that returns a result, with the default taken from `BLUE_DATA_CLI_FORMAT`. One renderer module serves all four formats for all commands.

- `table`: a Rich table, bold header, `markup=False`, `highlight=False`.
- `json`: one object on standard output and nothing else. The command's top-level envelope is stable; metadata keys that do not apply are omitted as specified by `meta_view`.
- `nl`: a short plain-sentence paragraph.
- `csv`: RFC 4180, header row from the same columns as `table`; for a non-tabular result the columns are `key,value`.

Table, CSV, and natural-language output strip C0/C1 controls from displayed text, including table and CSV headers. JSON strips controls from string values; dictionary keys retain their spelling and JSON encoding escapes any controls in them. In `csv` a cell whose first character is `=`, `+`, `-`, `@`, tab or carriage return is additionally prefixed with a single quote, because a spreadsheet reads those as formulas.

Informational output, progress and logs go to standard error; the result goes to standard output, so every format is safe to pipe. Progress is a Rich `console.status` spinner on standard error, shown only when standard error is a terminal and never under `--format json` or `--format csv`, checked explicitly rather than left to Rich's own detection.

### Exit codes

Errors reach the terminal as one line on standard error, `ERROR: <CODE> <description>`, with no traceback unless `--log` is set. The description says what failed and, where the CLI can know it, what to do instead.

| Code | Exit | Raised for |
|------|------|------------|
| — | 0 | Success, including the `empty_query` and `truncated` outcomes |
| `INTERNAL` | 1 | Any other `LlmSourceError` |
| `USAGE` | 2 | Unknown option, missing argument, bad value, an option on the wrong command, a refused deferred setting |
| `QUERY` | 3 | `QueryError` |
| `CONFIG` | 4 | `ConfigError`: a library setting the source rejected |
| `PROFILE` | 4 | A profile operation this CLI rejected: unknown profile, unknown configuration parameter, a cast failure |
| `TRANSPORT` | 5 | `TransportError`, including a missing credential |
| `PARSE` | 6 | `ParseError` |
| `BUDGET` | 7 | `BudgetExceeded`, and the `stopped_by_limit` outcome |
| `PARTIAL` | 8 | The `partial` outcome |

`CONFIG` and `PROFILE` share exit 4 and are told apart by the code token. No command here is a batch command, so there is no partial-success rule beyond `PARTIAL`, which is about one request rather than several.

An absent provider credential is not code 4. The provider SDK handles authentication. A failed call may raise `TransportError` (exit 5), or a configured call ceiling may stop retries first (exit 7). The library reads credential environment values for diagnostic masking, not authentication. A credential-related transport message names `blue-llmdb profile config OPENAI_API_KEY`.

**JSON-mode errors.** On a format-bearing command, an explicit `--format json` or a valid `BLUE_DATA_CLI_FORMAT=json` default also writes a Click parse failure to standard output as `{"error": {"code": "USAGE", "message": ...}}`. It exits 2 and retains the `ERROR: USAGE` line on standard error. The last explicit `--format` flag wins over the default; a flag after `--` is an argument. A profile default is available after the root callback exports it. Root-option parse failures can use an already exported environment default, but not a profile-only default; an explicit `--format json` on the selected command covers that boundary. Commands without `--format` use standard error only. Errors after parsing use the same JSON envelope when JSON is already selected. The root callback passes startup errors (an unknown `--profile`, an unreadable profiles file, or a stored value that will not cast) to the command body. Before a profile can be read, the error publisher uses an explicit format flag or an already exported environment default. An invalid `BLUE_DATA_CLI_FORMAT` value without an explicit format flag reports only on standard error because the format lookup itself failed.

During a fetch, the CLI discards interpreter-level writes through `sys.stdout`. This keeps provider diagnostics out of the JSON result and standard error streams. Handled errors remain on standard error. Direct writes to file descriptor 1 and writes made after fetch returns are outside the redirect.
