# LlmSource — High Level Architecture

## Diagram

```
                          External Interfaces

  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
  │  Python Lib   │  │  MCP Server   │  │      CLI      │
  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘
          └──────────────────┴──────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                               LlmSource                                  │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                          Request Intake                            │  │
│  │        checks (validation) · prompts (rendering) · op dispatch     │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                       Planning and Execution                       │  │
│  │ facts · schedules · task tree · partition safety · leaf mapping   │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌──────────────────────────────┐    ┌────────────────────────────────┐  │
│  │        Model Boundary        │    │           Reduction            │  │
│  │                              │    │                                │  │
│  │  ┌────────────────────────┐  │    │  ┌──────────────────────────┐  │  │
│  │  │   LlmClientProtocol    │  │    │  │         parsing          │  │  │
│  │  └────────────────────────┘  │    │  └──────────────────────────┘  │  │
│  │  ┌────────────────────────┐  │    │  ┌──────────────────────────┐  │  │
│  │  │   DefaultLlmClient     │  │    │  │         aligner          │  │  │
│  │  │   FunctionClient       │  │    │  └──────────────────────────┘  │  │
│  │  └────────────────────────┘  │    │  ┌──────────────────────────┐  │  │
│  │  ┌────────────────────────┐  │    │  │         verifier         │  │  │
│  │  │    Budget / Limits     │  │    │  └──────────────────────────┘  │  │
│  │  │    Reservation         │  │    └────────────────────────────────┘  │
│  │  │    pricing             │  │                                        │
│  │  └────────────────────────┘  │                                        │
│  │  ┌────────────────────────┐  │                                        │
│  │  │  cache: Memory · File  │  │                                        │
│  │  └────────────────────────┘  │                                        │
│  └──────────────────────────────┘                                        │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │            types (contracts, exceptions) · _constants              │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                     ┌─────────────▼──────────────┐
                     │      Model provider        │
                     │   reached through litellm  │
                     │   from the `llm` extra,    │
                     │      imported lazily       │
                     └────────────────────────────┘
```

## Components

### External Interfaces

Interface adapters delegate to the core library and contain only interface-specific translation and presentation logic. Each adapter is a separately versioned package that depends on the library; the library does not depend on either adapter package.

**Python Library.** Native Python API (`LlmSource` and its return types), distributed as `blue-data-llm-source` and installed from source. Every other interface wraps this API.

**MCP Server.** `blue-data-llm-mcp` exposes five tools over stdio: `llmdb_query`, `llmdb_estimate`, `llmdb_list_models`, `llmdb_inspect_model`, and `llmdb_inspect_metadata`. A model-driven client can query the source, estimate cost, inspect model pricing support, and read server metadata. The `llmdb_` prefix avoids collisions with tools from other data-source servers. Tool definitions and argument translation are isolated from the protocol SDK; a separate module owns the transport.

**CLI.** `blue-data-llm-cli` provides the `blue-llmdb` command with twelve commands across `profile`, `data`, `model`, and `metadata`. Click handles argument parsing, and Rich handles terminal output. Profiles live under `~/.blue-llmdb`; settings resolve in the order command option, environment variable, profile, then built-in default. Translation, rendering, and profile storage are separate modules, leaving the command module responsible for option declarations and output.

### LlmSource

`LlmSource` is the main public class. It inherits `blue_data.source.source.DataSource`, initializes connection properties, accepts an optional client, and delegates specialized work to other modules.

Base-class order affects behavior. `LlmSource` must resolve its own implementations before the default methods on `DataSource`, several of which return empty results. A test checks every method on the shared surface. [The Source Base Class](04-detailed-architecture.md#the-source-base-class) documents the details.

- Presents three surfaces over the same core. The library surface is `afetch` (async) and `fetch` (a synchronous wrapper over it). The sizing surface is `estimate`, which contacts nothing. The source surface is `execute_query` and `execute_query_nl`, inherited from `DataSource` and overridden here, plus `translate_nl_to_native_query`, which is the identity because natural language is this source's native form.
- Holds the connection properties that report `protocol` and `variant`. Protocol names the source family and is fixed at `llm`; variant names the implementation within it and defaults to `openai`.
- Resolves which client answers a given request: a supplied `llm_fn` wins, then a client injected at construction, then the default client built from properties.
- Assembles the run's `Limits` from the instance properties and the call's properties. A ceiling supplied through `properties` or `optional_properties` applies only to that request. It creates a fresh budget unless the caller supplied one, in which case it derives a child that cannot spend more than the parent has left. The budget is passed to the client as a per-call property rather than stored on the client, since one client can serve concurrent fetches with different ceilings.
- Answers `estimate` without contacting a provider. It resolves the client that would answer, since only that client can price an attempt and it must declare `Capability.ESTIMATE` to provide a bound. It then builds the prompt the fetch path would send and bounds it through `pricing`. The figure describes the request and covers a whole `flat` fetch: `max_cost` is `max_cost_per_attempt` accumulated over `max_attempts`, so a ceiling set to exactly `max_cost` admits every attempt included in the estimate. The method reads no `plan.*` setting, so it does not price a split request.
- Refuses each configured ceiling whose axis the resolved client does not declare, and folds the run's accounting into the returned `ExecMeta`: entries and spend from the budget, which claimed them under a lock, and token counts and the model name from the per-call usage list it handed the client for this call alone.
- Decides the outcome through one published ordered rule, `decide_outcome`, so a ceiling that fired is never contradicted by a result that reads as complete.
- Answers the metadata calls a registry walks, over a synthesized one-database, one-collection, one-entity hierarchy, since a model has none of its own.

### Request Intake

Request-intake modules validate caller arguments and render provider prompts.

**checks.** Argument validation. Rejects a request that is not a string and passes an empty one through as an empty ask, normalizes attributes from either a list of names or a list of `{name, type}` mappings, normalizes context from a string or a list of strings, and requires positive integers where a count is expected. Messages name the offending value and the accepted form, because an agent calling through a tool interface sees only the exception text.

**prompts.** Every prompt the package sends lives in one module: eleven templates and nine
builders cover flat extraction, planned extraction, fact collection, partitioning, distribution,
and entity enumeration. `Slot` marks each inserted value as encoded, raw, or already-rendered lines;
`fill` then applies one `str.format_map` over a fixed set of named fields.

**Op dispatch.** `execute_query` accepts natural language, an operation dict, or a JSON-encoded object carrying an `op` key. Other strings remain natural-language queries. `LLM.FETCH` runs a fetch; `LLM.MODELS` reports protocol and variant; `LLM.ESTIMATE` bounds what the same fetch could cost, without contacting anything, and returns it as a one-record list because that is what this surface returns. An unrecognized op raises an error that lists the supported ones. Both surfaces also take `optional_properties`. General per-call properties merge over the instance's for that call and are not written back; `limits.*` can only tighten an effective ceiling, and `None` leaves it unchanged; `limits.max_retries=None` is invalid.

### Planning and Execution

Non-flat requests build immutable analysis, schema, and cardinality facts under one of five closed
schedules, then coordinate a bounded task tree. Partitioned planning is breadth first; row-by-row
planning uses an explicit or bounded entity list. `execution` converts accepted leaves into
versioned `LeafTask` messages, runs them concurrently under the shared budget, and retains answered
records when later work is interrupted.

Direct Python callers can opt into rule-backed distribution search over declared finite identity
domains. `PartitionSafety` must establish containment, pairwise disjointness, and coverage before a
split is accepted. The plan records each applied decision, and an optional planning receipt binds
configured rule digests, planning facts, and plan serialization. This checks internal agreement,
not factual truth or completeness. CLI and MCP expose the 21 standard controls and omit rule-backed
distribution search and the receipt.

### Model Boundary

The model boundary is the only layer that contacts a provider, allowing the rest of the package to be tested offline.

**LlmClientProtocol.** A structural protocol with two methods, `complete` and `acomplete`. Any object carrying them can be injected. Nothing in the package performs an `isinstance` check against a concrete client class.

**Capability and BudgetAwareClient.** `Capability` names the axes a client can enforce (calls, rows, time, and cost) plus `estimate`, which indicates that the client can price an attempt before making it. `BudgetAwareClient` adds `budget_capabilities()`. Capabilities are declared per axis, and the source refuses a ceiling that the selected client cannot enforce. The check includes limits inherited from a caller-owned parent budget.

**DefaultLlmClient.** Calls a provider through a lazily imported `litellm` dependency. It validates a closed set of `llm.*` overrides, removes local control keys before provider contact, applies retry and concurrency limits, supports an optional cache, validates responses, and records per-call usage.

**FunctionClient.** Wraps a `(prompt: str) -> str` callable and implements the `llm_fn` path. It declares only the rows capability because it cannot observe the callable's provider calls, cost, or deadline behavior. The source therefore refuses call, cost, and time ceilings for this client.

**Budget, Limits, and Reservation.** `Limits` holds the optional ceiling on each axis, and `Budget` tracks consumption against those ceilings. Calls, elapsed time, and cost are reserved under a shared lock before provider contact; rows are counted after parsing. `Budget.reserve` returns a `Reservation`. The default client keeps at least its bound after provider contact, raises the charge for a higher readable figure, and releases it if contact did not occur. Each retry reserves separately and rechecks the deadline. Cache hits consume no provider call, tokens, or cost, but their rows and elapsed time still count.

`Budget.derive` creates a child that spends from the parent's remaining allowance. The effective limit on each axis, including retry depth, is the smaller of the child limit and the parent's remaining headroom. The child does not inherit the parent's `stopped_by` value.

**pricing.** Computes an upper cost bound from the prompt's UTF-8 byte length, a framing allowance, the model's input rate, and the full `max_tokens` allowance at its output rate. It refuses to produce a bound when rates or a finite positive output allowance are unavailable. `LlmSource.estimate` and runtime reservation use this same calculation.

**cache.** Two stores implementing the `CacheStore` protocol, `MemoryCache` in process with least-recently-used eviction and `FileCache` on disk with one JSON file per entry. Both use the standard library only, so a caller gets a working cache without the provider extra and without a cache server. They are supplied through the `cache_store` property; the separate `llm.cache` switch still opens `diskcache` and does not use either of them.

### Reduction

This layer turns model text into checked records. Each stage is a module of plain functions with no model call or shared state, so each can be tested independently.

**parsing.** Removes one surrounding code fence, parses JSON, and accepts a bare object or the supported single-key array wrappers. Ambiguous envelopes are resolved against requested attributes, and duplicate JSON keys are rejected at any depth. Invalid input raises a parse error carrying the raw text.

**aligner.** Maps returned keys to requested attributes by exact, case-insensitive, then normalized matching. It reports unmapped and remapped keys, preserves the first value in a collision, and avoids ambiguous fuzzy matches. With no requested attributes, records pass through unchanged. The exact collision and ambiguity rules are specified in [04-detailed-architecture.md](04-detailed-architecture.md#3-reduction).

**verifier.** Coerces values to their declared types where the coercion is unambiguous, covering integers, floats, booleans, and dates in several common written forms. A value that will not coerce is left as it came back, because dropping a record over a formatting mismatch loses real data, and it is reported alongside the records rather than kept quietly. The module also provides exact duplicate removal, optionally keyed on a single attribute.

**identity and coverage.** `identity` normalizes scalar and composite keys consistently across continuation, reduction, and rule validation, so one record is one record wherever it is recognized. `coverage` merges the leaves into one record set and reports what merging them cost: the gaps, the origins of each record, and the run's outcome. The key-level counts a caller reads as `marginal_key_yields`, `incomplete_key_records` and `normalization_collisions` are observed a leaf at a time by the continuation loop in `execution` and summed by the facade. None of them is a completeness claim.

### Shared Contracts

**types.** The data contracts and the exception hierarchy: `Attr`, `Usage`, `Outcome`, `Capability`, `ExecMeta`, `Estimate`, `FetchResult`, the `LlmClientProtocol`, `BudgetAwareClient`, and `CacheStore` protocols, and `LlmSourceError` with `ConfigError`, `QueryError`, `TransportError`, `ParseError`, and `BudgetExceeded` beneath it. `Outcome` is the closed vocabulary `ExecMeta.outcome` draws on, which is what makes an empty result a valid answer carrying a reason rather than an absence, and `Capability` is the closed vocabulary a client declares from. Contracts live here so that the modules and any platform adapter share them without circular imports.

**_constants.** Protocol and variant strings, the three op codes, the names of the synthesized database, collection, entity, and attribute, and the two property keys a fetch hands down under, one for the run's budget and one for the list this call's usage entries go into. This module defines no environment-based request settings.

### Compatibility Alias

`compat/blue_data.py` binds `BlueDataLlmSource` to `LlmSource` and does nothing else. The module existed when `blue-data` was an optional extra and the core matched the source surface by shape, and it held a subclass that added the real inheritance for callers who wanted it. The core now carries that inheritance, so the name is kept only so an import written against the old module keeps working, and it is an alias rather than a subclass because a second class would be a second method resolution order to get wrong for no behavior of its own.

### The Data Layer Dependency

The runtime dependencies are `blue-data>=1.0.0,<2` and `tenacity>=9.0.0`. `blue-data` supplies the base class and brings `pandas`, `numpy`, and `jsonpath-ng` into the installation. `litellm` and `diskcache` remain optional and are imported lazily.

The line the dependency does not cross is the one above the data layer. Nothing here imports `blue` or `blue_data_ops`, and a test asserts it, because a data source that reaches into the runtime calling it can only be used inside that runtime. Depending on the layer this source belongs to is a different thing from depending on the orchestrator that queries it.

## What the architecture deliberately does not contain

- A calibrated cardinality or completeness measure. Non-flat bootstrap can infer a schema, and preserve or enrich caller declarations according to `plan.schema_mode`. Per-slice estimates guide splitting; `meta.row_estimate` exposes only the accepted root hint for operations, not an expected count or recall measure.
- An empty-partition fallback. The task tree, the fan-out runner and the continuation loop ship; a valid empty part is answered work and creates no gap merely for being empty.
- A validator stack. Format verification against declared types ships; factual verification does not, and per-run reporting is not a substitute for it.
- A retrieval or grounding path. The premise is closed-book.
- A distributed runtime dependency. Ready leaves run with bounded local concurrency through serializable task and result contracts. No worker service, broker, or deployed cluster is supplied. The two cache stores are a process-local dictionary and a directory of JSON files; another backend remains a caller choice behind the protocol.
