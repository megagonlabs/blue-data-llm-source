# Architecture

This page traces a request through the system, describes each component's responsibility, and shows where provider calls occur.

For contributor-oriented detail, see [`design/03-high-level-architecture.md`](../design/03-high-level-architecture.md) and [`design/04-detailed-architecture.md`](../design/04-detailed-architecture.md).

## Overview

The repository is two tiers.

| Tier | Contents | Depends on |
|---|---|---|
| `lib/` | `LlmSource` and everything it delegates to. The only business logic in the repository | `blue-data`, `tenacity`, optionally a provider SDK |
| `platform/` | Two deployable adapters over `lib/`, each a separately versioned package | `lib/` |

Every path that returns records ends up in `LlmSource.afetch`. The adapters translate arguments and format output; they hold no retrieval, planning, or budgeting logic of their own. The command-line interface is documented in [cli.md](cli.md) and the MCP server in [mcp.md](mcp.md).

```
                          External interfaces

  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
  │  Python lib   │  │  MCP server   │  │      CLI      │
  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘
          └──────────────────┴──────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                               LlmSource                                  │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                          Request intake                            │  │
│  │        checks (validation) · prompts (rendering) · op dispatch     │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                       Planning and execution                       │  │
│  │  facts · schedules · task tree · partition safety · leaf mapping   │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌──────────────────────────────┐    ┌────────────────────────────────┐  │
│  │        Model boundary        │    │           Reduction            │  │
│  │  LlmClientProtocol           │    │  parsing                       │  │
│  │  DefaultLlmClient            │    │  aligner                       │  │
│  │  FunctionClient              │    │  verifier                      │  │
│  │  Budget · Limits · pricing   │    │  identity · coverage           │  │
│  │  cache: MemoryCache/FileCache│    │                                │  │
│  └──────────────────────────────┘    └────────────────────────────────┘  │
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

The MCP server uses stdio.

## Components

| Component | Module | Role |
|---|---|---|
| `LlmSource` | `source.py` | The facade, and the only class most callers touch. Resolves the client, assembles limits, folds accounting into the result, decides the outcome |
| Argument checks | `checks.py` | Normalizes the query, attributes, context, and counts. Every message names the offending value and the accepted form |
| Prompts | `prompts/` | Every prompt this package sends, in one module. Caller text is inserted as a value and never re-read as a template |
| Planning | `planning/` | Builds analysis, schema, and cardinality facts for a non-flat request, then a bounded task tree |
| Execution | `execution/` | Turns accepted plan leaves into versioned tasks and runs them with bounded asynchronous concurrency in one process under the shared budget |
| Model boundary | `llm/` | `DefaultLlmClient`, `FunctionClient`, `Budget`, `Limits`, `Reservation`, the model table in `models`, and the cost bound in `pricing` |
| Cache stores | `cache/` | `MemoryCache` and `FileCache`, both standard-library only |
| Reduction | `reduction/` | Parses the response, aligns returned keys to requested attributes, coerces declared types, and removes duplicates |
| Shared contracts | `types.py`, `views.py`, `_constants.py` | The dataclasses, protocols, exception hierarchy, outcome vocabulary, and the one serialization of `ExecMeta` every adapter publishes |
| Compatibility alias | `compat/blue_data.py` | Binds `BlueDataLlmSource` to `LlmSource` so an older import keeps working |

`LlmSource` extends `blue_data.source.source.DataSource`, so it implements the data-layer source type directly. Within the library, only `source.py` imports `blue_data`; the CLI uses its shared configuration engine; the package does not import the `blue` or `blue_data_ops` orchestration layers. A test enforces that dependency boundary.

## Data flow: a flat fetch

The default path sends one question in one model call and returns one list of records.

1. Requested attributes, context, and row caps are normalized. Properties are resolved in order: defaults, injected-client properties, instance properties, then per-call properties. Later values take precedence, so source properties override client settings. Per-call ceilings are then combined by minimum with the resolved configuration. A call's `None` adds no cap and cannot clear an explicit source ceiling; an explicit source `None` can override a client ceiling through property precedence. Explicit retry `None` is invalid; `max_records` is clamped by the effective row ceiling. Every `llm.*`, `limits.*`, and `plan.*` name is checked against a closed set.
2. The query is checked next. `None` and empty or whitespace strings return `outcome="empty_query"` after property-name and limit validation, before a run budget is built or a client is called. Other non-string inputs raise.
3. The run's budget is built from the resulting `Limits`: a fresh one, or a child derived from a `Budget` you supplied, which spends only what you have left.
4. A client is resolved: an `llm_fn` passed to this call wins, then a client injected at construction, then the built-in provider client. Its shape is checked, and every configured ceiling on an axis it does not declare is refused.
5. The prompt is rendered and sent. Before each provider attempt the client bounds what that attempt can cost and claims it, along with one call and the elapsed-time check, under a lock.
6. The response is parsed as a list of record objects, returned keys are aligned to the attributes you asked for, values are coerced to their declared types, records that answered nothing are dropped, and exact duplicates are removed.
7. Surviving records are offered to the row ceiling. Anything over it is dropped and the truncation point recorded.
8. Accounting is folded into `ExecMeta`, the stop reasons are ordered, the outcome is decided from all of it, and a `FetchResult` comes back.

Steps 1 to 4 and 6 to 8 contact nothing. Step 5 is the only place money is spent.

## Data flow: a decomposed fetch

A large request can be split into smaller requests. The caller selects the shape with `plan.mode`, whose default is `flat`.

| Mode | What it does | Extra model calls before extraction |
|---|---|---|
| `flat` | Asks once | None |
| `partitioned` | Asks the root first and, when that answer was not enough, splits the ask along a key the planning stages found, asks each part separately, merges on the way back | One fact schedule, the root's ask and its continuations, then bounded partition proposals |
| `row_by_row` | Asks once per entity in a list the planning stages enumerated | One fact schedule, then a bounded entity enumeration |

For a non-flat request the planning stages run first, under one of five closed schedules named by `plan.planning_order`. Each stage sends one prompt and parses one JSON object: what the request is actually asking for, what schema the answer should have, and roughly how many rows to expect. The combined schedule makes at most one such call, the two locked schedules at most two, and the other two at most three.

A planning stage that fails to parse does not fail the request. It falls back to what the caller declared, records a diagnostic in `meta.gaps`, and the run continues, so a decomposed request degrades to a flat one rather than raising.

Planning is breadth-first and bounded on all axes: `plan.max_depth`, `plan.max_partitions`, `plan.max_leaves`, `plan.max_strategy_candidates`, and the shared budget. An expansion that cannot fit is refused and its slice runs as one leaf.

Ready leaves then run concurrently through the leaf driver, each asking the same client under the same budget. Each leaf may ask again for records it did not get, up to `plan.max_continuations`, which is three by default in `partitioned` mode. Records from overlapping parts are merged on an identity built from `plan.keys`, and `meta.record_origins` records which parts produced each record.

Direct Python callers can additionally opt into rule-backed distribution search over finite identity domains they declare. A split is accepted only after containment, pairwise disjointness, and coverage are established, and the result may carry a `PlanningReceipt` binding the configured rule digests, the planning facts, and the plan. The receipt checks internal agreement. It does not verify factual truth, completeness, or answer quality. Neither the command-line interface nor the MCP server exposes distribution search or the receipt.

## Data flow: an estimate

`estimate` bounds the cost of a flat fetch without contacting a provider.

1. Properties are merged the way a fetch merges them, so the figure is priced against the configuration the run would actually use.
2. An empty request returns a bounded estimate of zero, because it never reaches a model.
3. The same prompt the fetch path would send is rendered.
4. The client that would answer is resolved and asked for its bound. Only a client that drives the provider itself can give one, so a client that declares `Capability.ESTIMATE` and implements `bound_for_attempt` produces a figure and anything else produces `bounded=False` with a reason.
5. The per-attempt figure is accumulated once per attempt the fetch is permitted, giving a bound on the whole fetch rather than on one try.

The estimate and runtime reservation use the same prompt, model, output allowance, and bound calculation. A request estimated within a ceiling therefore uses the same bound when it runs.

`estimate` reads no `plan.*` setting. Its bound covers one flat fetch, including retries. A non-flat run may make planning and extraction calls with different prompts; this estimate does not bound their combined cost.

## The budget

The budget has four optional axes. `None` means unlimited on that axis.

| Axis | When it is enforced | Enforced by |
|---|---|---|
| `limits.max_calls` | Claimed before each provider attempt, retries included | The answering client |
| `limits.max_seconds` | Checked at admission and bounds each cooperative wait | The answering client |
| `limits.max_cost` | An upper bound is claimed before the attempt. A contacted attempt keeps at least the reservation; a higher readable figure raises the charge ([cost bound](../lib/README.md#the-cost-bound)) | The answering client |
| `limits.max_rows` | Applied once the records are parsed, because that is when the count exists | `LlmSource` itself |

Calls, time, and cost are claimed *before* the provider is contacted. Cost is unknown until a call returns, so the reservation uses a bound against the package or configured rates: the prompt's UTF-8 byte length plus a framing allowance at the model's input rate, plus the full output allowance at its output rate, raised for the cache-write, long-context and regional-processing charges the model's table entry records. If the bound cannot be established, the request is refused under a cost ceiling. Stale rates or inaccurate provider usage can still leave the ledger below the invoice. [The cost bound](../lib/README.md#the-cost-bound) has the full rule, including how a call is settled once it returns.

Claims use one lock shared by a budget family, so concurrent leaves cannot collectively overspend while each sees room for another call. Each claim resolves exactly once: it is settled at the charge [the cost bound](../lib/README.md#the-cost-bound) assigns, or released if the attempt never reached the provider.

A caller-owned `Budget` accumulates across every call that receives it. Each fetch derives a child that spends from the parent's remaining allowance, so the effective ceiling on each axis is the smaller of the parent and child limits. A planner can share one total across several fetches.

Which ceilings bind depends on the client. A client declares its axes through `budget_capabilities()`, and a ceiling on an axis it does not declare is refused at the first fetch rather than reported as holding. The practical case is `llm_fn`: a wrapper around your callable can prove it invoked the callable once and nothing more, so it declares rows alone.

The time axis covers admission and cooperative waits, but not work that never yields. `asyncio` cannot interrupt code that is not awaiting. See [what the time ceiling actually bounds](../lib/README.md#what-the-time-ceiling-actually-bounds) for the exclusions.

## The model boundary

One structural protocol, two methods: `complete(prompt, properties)` and `acomplete(prompt, properties)`. Any object carrying them can be substituted, and nothing in the package performs an `isinstance` check against a concrete client class. `afetch` calls `acomplete`, and `fetch` wraps `afetch`, so the asynchronous form is the one every fetch reaches.

Two clients are included. `DefaultLlmClient` reaches a provider through `litellm`, imported lazily inside the call so the package can be installed and tested without a provider SDK; it declares every capability. `FunctionClient` wraps a plain `(prompt) -> str` callable, providing the implementation behind the data layer's `llm_fn` parameter and a simple offline path.

Two properties reach whichever client answers, on every call rather than as attributes on the client: `budget` carries the run's budget, and `usage_sink` carries a list this call owns. The fetch reads its token counts and model name from that list alone, which is what stops two concurrent fetches through one client from reporting each other's tokens.

Caching sits behind the same boundary. A `cache_store` property takes any object with `get` and `set`, and `MemoryCache` and `FileCache` ship for the slot. A cache hit consumes no provider attempt, no tokens, and no cost; rows it returns still count against a row ceiling, and admission still checks elapsed time.

## Reduction

Four stages of plain functions. None of them calls a model, holds state, or imports another layer, so each is testable on its own.

| Stage | What it does | What it reports |
|---|---|---|
| `parsing` | Strips one surrounding code fence, reads the body as JSON, and produces a list of record objects. A single object is wrapped; a wrapper object whose only key is `items`, `rows`, `data`, or `results` is unwrapped | `ParseError` carrying the raw text when the response cannot be read as records, including when an object repeats a key at any depth |
| `aligner` | Maps returned keys onto requested attributes (exact, then case-insensitive, then a normalized match ignoring separators) | `meta.remapped_keys`, `meta.unmapped_keys`, `meta.key_collisions` |
| `verifier` | Coerces values to their declared types where the coercion is unambiguous, drops records that answered nothing, removes exact duplicates | `meta.type_mismatches`, `meta.unchecked_types` |
| `identity` and `coverage` | Builds a composite key from `plan.keys`, consistently across continuations, merging, and rule validation | `meta.primary_keys`, `meta.observed_key_count`, `meta.marginal_key_yields`, `meta.incomplete_key_records`, `meta.normalization_collisions` |

A value that will not coerce is kept and reported because a formatting mismatch should not lose a record. An unmatched returned key is reported so a caller can diagnose an empty column.

## The data layer

`LlmSource` registers and answers like any other source in the layer. It reports protocol `llm` and variant `openai`, and synthesizes a one-database, one-collection, one-entity hierarchy so a registry sync has something to walk. A model has none of its own.

Inheriting the base class does not make the source reachable by protocol name. Build the source and hand the instance to `register_source`; the registry records the class alongside `name` and `properties`, and `connect_source` returns the live instance while it is present and reconstructs a new source from the record afterwards.

The registry stores the source's own properties, not its injected client or the client's settings. Put ceilings, model and rates on the source when they must survive registration and reconstruction; a rebuilt source starts with the default client.

`execute_query` and `execute_query_nl` return a plain `list[dict]`, because that is what the shared surface returns. Call `fetch` when you need the outcome, the stop information, the accounting, or the schema diagnostics.

## Result limits and local execution

- Per-slice row estimates guide splitting. `meta.row_estimate` is a planning hint, not a completeness or recall measure; an empty `meta.gaps` means no supported diagnostic fired.
- Type verification checks format against declared types. The library does not verify whether a record is true. Answers come from the model's own knowledge, without corpus retrieval or grounding.
- Leaves run with bounded local concurrency through serializable task and result contracts. The included caches are a process-local dictionary and a directory of JSON files; other backends can use the `CacheStore` protocol.
- The configured `llm.model` is used for planning and extraction; the source does not select a model automatically.
- Reduction normalizes declared keys and deduplicates by those keys; it does not resolve aliases between differently named entities.

## Where to go next

- [features.md](features.md): what it can do, one short example each.
- [guide.md](guide.md): the same components, task by task.
- [extending.md](extending.md): the seams in the diagram you are meant to implement against.
- [`design/03-high-level-architecture.md`](../design/03-high-level-architecture.md) and [`design/04-detailed-architecture.md`](../design/04-detailed-architecture.md): detailed architecture for contributors.
