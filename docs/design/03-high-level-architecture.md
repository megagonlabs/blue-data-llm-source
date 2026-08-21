# LlmSource — High Level Architecture

## Diagram

```
                          External Interfaces

  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
  │  Python Lib   │  │  MCP Server   │  │      CLI      │  │   REST API    │
  │   (shipped)   │  │   (shipped)   │  │   (shipped)   │  │(not implem'd) │
  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘  └───────────────┘
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

Interface adapters sit in front of LlmSource and delegate to the core library. They share no internal logic, and each is a translation layer over the Python API. Each is a separately versioned package that depends on the library, so the library carries no CLI or server dependency of its own.

**Python Library** — Native Python API (`LlmSource` and the types it returns), published as `blue-data-llm-source`. This is the canonical interface, and every other interface wraps it.

**MCP Server** — Published as `blue-data-llm-mcp`, speaking MCP over stdio. Exposes two tools, `llm_fetch` and `llm_info`, so an LLM agent can query the source within an agentic context. Internally split so that the tool definitions and the argument translation import nothing from the protocol SDK, and one module owns the transport.

**CLI** — Published as `blue-data-llm-cli`, providing the `llm` command with `fetch` and `info` subcommands. Built with Click for argument parsing and Rich for output. Option translation is separated from command declaration, so one module maps what a person typed onto library arguments and the other declares options and prints.

**REST API** — Not implemented and not scheduled. No HTTP application, route handlers, or server dependencies exist in any package.

### LlmSource

The facade, and the only class most callers touch. It inherits `blue_data.source.source.DataSource`, so it is a source of the data layer by type rather than by resemblance. Its constructor initialises connection properties and accepts an optional client; its methods are short and delegate to the modules below.

The base order is load-bearing rather than cosmetic, and it is the one thing about the inheritance worth stating twice. `DataSource` is a plain class, not an abstract one. Most of its methods return an empty result rather than raising, so one left unoverridden here answers nothing instead of failing. That is not true of all of them any more: the transaction methods raise `NotImplementedError` naming this source, and the orchestrator-tool methods carry real behaviour, which is why this source overrides those three with a refusal rather than inheriting them. Where a method does return empty: a registry walking the source would find one that reports no databases and returns no records, silently. `DataSource` is therefore the base and this class's own implementations come first in the method resolution order, which a test asserts for every method on the shared surface.

- Presents three surfaces over the same core. The library surface is `afetch` (async) and `fetch` (a synchronous wrapper over it). The sizing surface is `estimate`, which contacts nothing. The source surface is `execute_query` and `execute_query_nl`, inherited from `DataSource` and overridden here, plus `translate_nl_to_native_query`, which is the identity because natural language is this source's native form.
- Holds the connection properties that report `protocol` and `variant`. Protocol names the source family and is fixed at `llm`; variant names the implementation within it and defaults to `openai`.
- Resolves which client answers a given request: a supplied `llm_fn` wins, then a client injected at construction, then the default client built from properties.
- Assembles the run's `Limits` from the instance properties merged with whatever the call carried, so a ceiling handed to one request through `properties` or `optional_properties` binds that request and is not written back. It then produces the budget the run spends against: a fresh one when the caller supplied none, and otherwise a child derived from the caller's, so a request can never spend more than the caller has left. It hands that budget to the client under a per-call property rather than setting it on the client, because one client can serve several concurrent fetches and an attribute would let them overwrite each other's ceiling.
- Answers `estimate` without contacting a provider. It resolves the client that would answer, because only that client knows what an attempt costs and it must declare `Capability.ESTIMATE` for a bound to be given at all, then builds the prompt the fetch path would send and bounds it through `pricing`. The figure describes the request being asked about. It bounds the whole fetch rather than one attempt: `max_cost` is `max_cost_per_attempt` accumulated over `max_attempts`, so a ceiling set to exactly `max_cost` admits every attempt the figure accounted for.
- Refuses each configured ceiling whose axis the resolved client does not declare, and folds the run's accounting into the returned `ExecMeta`: entries and spend from the budget, which claimed them under a lock, and token counts and the model name from the per-call usage list it handed the client for this call alone.
- Decides the outcome through one published ordered rule, `decide_outcome`, so a ceiling that fired is never contradicted by a result that reads as complete.
- Answers the metadata calls a registry walks, over a synthesised one-database, one-collection, one-entity hierarchy, since a model has none of its own.

### Request Intake

Everything that happens between a caller's arguments and the text sent to a provider.

**checks** — Argument validation. Rejects a request that is not a string and passes an empty one through as an empty ask, normalises attributes from either a list of names or a list of `{name, type}` mappings, normalises context from a string or a list of strings, and requires positive integers where a count is expected. Messages name the offending value and the accepted form, because an agent calling through a tool interface sees only the exception text.

**prompts** — Every prompt the package sends, in one module. One template, one renderer for the attribute block, and one builder. Substitution is a single `str.format_map` over a fixed set of named fields, which inserts caller text without interpreting it.

**Op dispatch** — `execute_query` accepts either a plain string or a mapping carrying an `op` key. `LLM.FETCH` runs a fetch; `LLM.MODELS` reports protocol and variant; `LLM.ESTIMATE` bounds what the same fetch could cost, without contacting anything, and returns it as a one-record list because that is what this surface returns. An unrecognised op raises an error that lists the supported ones. Both surfaces also take `optional_properties`, which is where a per-request ceiling goes: the keys are merged over the instance's properties for that call and are not written back.

### Model Boundary

The one place the package touches a provider, and the seam that makes the rest of it testable offline.

**LlmClientProtocol** — A structural protocol with two methods, `complete` and `acomplete`. Any object carrying them can be injected. Nothing in the package performs an `isinstance` check against a concrete client class.

**Capability and BudgetAwareClient** — `Capability` names the axes a client can be held to: calls, rows, time, and cost. `BudgetAwareClient` adds one method, `budget_capabilities()`, to the two above, and what it returns is what the source will hold the client to. A method rather than an attribute, because the version this replaced treated an attribute named `budget` as evidence that ceilings would bind, which a client holding `budget = None` satisfied while making unbounded calls. Per axis rather than as one flag, because a client that drives the provider can prove what it spent while a wrapper around a caller's function can prove only that it called the function once. Each configured ceiling on an axis the client did not declare is refused rather than reported as holding, and the ceilings counted are every one in the budget chain rather than only those this request configured, since a ceiling a caller carries on its own budget still has to be honoured by whichever client answers.

**DefaultLlmClient** — Calls a provider through `litellm`, which is imported lazily inside the call so the package installs and its tests run with no provider SDK present. It applies a closed set of accepted `llm.*` overrides, so an unknown setting raises rather than passing silently through; strips control keys that must never reach the provider; retries transport failures with exponential backoff; enforces a per-loop concurrency bound; consults an optional cache; validates the response shape before extracting text; and records per-call usage covering model, tokens, cost, latency, and cache-hit state.

**FunctionClient** — Wraps a plain `(prompt: str) -> str` callable as a client. This is what gives the source surface's `llm_fn` parameter a real meaning here, and it is also the simplest way to drive the library from a test with a scripted response. It declares the rows axis alone, because it cannot know what a caller's function is billed, how to stop it, or even how many provider calls one invocation of it becomes, so a call, cost, or time ceiling over an `llm_fn` is refused.

**Budget, Limits, and Reservation** — `Limits` is the configured ceiling on each axis, where `None` means unlimited, and each field is validated as it is built so that a `NaN` or a negative ceiling is refused rather than accepted while enforcing nothing. `Budget` tracks spend against it and refuses the attempt that would cross one. Calls, elapsed time, and cost are claimed before the provider is contacted, under a lock shared by the whole budget family, so a concurrent fan-out cannot race past a ceiling. `Budget.reserve` returns a `Reservation`, which is a handle rather than a bare number so that the claim resolves exactly once: `settle` replaces the bound with the real cost and releases the difference, `keep_unpriced` leaves the bound standing as the charge when the real cost cannot be read, and `release` gives a claim back for an attempt that never reached the provider. Every path out of an attempt resolves its reservation, including a response that cannot be materialised and an exit that is not an ordinary exception at all, such as a cancelled task or a keyboard interrupt, or the retry that follows takes headroom the failed attempt never gave back and a caller-owned budget stays poisoned for everything after it. Reservation is per provider attempt, so a retry reserves again and rechecks the deadline, and the timeout handed to the provider is capped at the time the run has left. Rows are admitted after the fact, because the count is not known until the records are parsed. A cache hit consumes no provider attempt, no tokens and no cost; rows it returns still count, and admission still checks elapsed time.

`Budget.derive` produces a child that spends from what its parent has left, so the effective ceiling on every axis is the smaller of the child's and the parent's remaining headroom, retry depth included. That is what lets a caller hand one budget to several fetches without each of them receiving a full allowance, and it is why the caller-supplied `budget` argument bounds a unit of work rather than a request. The child does not inherit the parent's `stopped_by`, so a later affordable request does not report an earlier refusal as its own.

**pricing** — A genuine upper bound on what one call can cost, or a refusal to produce one. The bound is the prompt's length in UTF-8 bytes plus a framing allowance at the model's published input rate, plus the whole `max_tokens` allowance at its output rate, and it rests on tokens never outnumbering bytes, since a byte-pair tokenizer merges byte sequences and cannot emit a token that consumes none of them. Where the bound cannot be established the module raises rather than inventing a figure, which happens when the model has no published price and the caller supplied none, when a supplied price cannot be read as a finite positive number, or when the output allowance is not a finite positive number. A bound that is usually right is a ceiling that is occasionally crossed, so an average-case estimate, a stand-in price for an unlisted model, and an open prefix match that let a longer name inherit a shorter model's rates were all removed. This is also what `LlmSource.estimate` reports, which is why an estimate and the ceiling that enforces it cannot disagree: they are one figure asked for at two different moments.

**cache** — Two stores implementing the `CacheStore` protocol, `MemoryCache` in process with least-recently-used eviction and `FileCache` on disk with one JSON file per entry. Both use the standard library only, so a caller gets a working cache without the provider extra and without a cache server. They are supplied through the `cache_store` property; the separate `llm.cache` switch still opens `diskcache` and does not use either of them.

### Reduction

Turning a model's text into checked records. Each stage is a module of plain functions with no model call and no shared state, so each is testable on its own.

**parsing** — Strips one surrounding code fence if present, matched as a fence rather than as a substring, then reads the body as JSON. A single object is accepted and wrapped, and an object whose only key is `items`, `rows`, `data`, or `results` and holds the array is unwrapped, because models produce all three shapes and rejecting them would fail a correct answer on a formatting detail. Where that key sits beside others the object is ambiguous — a wrapper with a count and a record with a list-valued attribute have the same shape — so the reading that answers more of the requested attributes wins, and with nothing requested the record reading is taken, because it discards nothing. An object that repeats a key, at any depth, is rejected instead: which of two values for one key the model meant is not knowable here, and Python's decoder would otherwise keep the last and leave no trace that the first existed. Anything else raises a parse error carrying the raw text.

**aligner** — Maps the keys a model returned onto the attributes the caller asked for, trying exact match, then case-insensitive match, then a normalised match that ignores separators and case. With no requested attributes the records pass through unchanged. Keys that match nothing are collected and reported rather than dropped, and keys that were matched under a different spelling are reported as remapped. Two returned keys that reach the same attribute are a collision: the first is kept and the second is reported with the value it carried, because which spelling the model meant is not knowable here and letting the later one win would make the answer depend on the order the keys happened to be emitted in. Two keys that are byte-for-byte identical never reach this stage, because parsing rejects the object instead; the two cases look alike and are answered differently, since `First Name` beside `first_name` still carries two distinguishable keys to report while one key repeated carries nothing to tell the values apart by. Two *requested* attributes that merely normalise alike are not refused. The fuzzy index drops the entry it cannot separate, so an exact key still matches and a key that matches only fuzzily is reported unmapped rather than assigned by guess. Only the same name requested twice raises `QueryError`, since that is the one case with nothing to tell the two apart by. Detailed architecture states the same rule.

**verifier** — Coerces values to their declared types where the coercion is unambiguous, covering integers, floats, booleans, and dates in several common written forms. A value that will not coerce is left as it came back, because dropping a record over a formatting mismatch loses real data, and it is reported alongside the records rather than kept quietly. The module also provides exact duplicate removal, optionally keyed on a single attribute.

### Shared Contracts

**types** — The data contracts and the exception hierarchy: `Attr`, `Usage`, `Outcome`, `Capability`, `ExecMeta`, `Estimate`, `FetchResult`, the `LlmClientProtocol`, `BudgetAwareClient`, and `CacheStore` protocols, and `LlmSourceError` with `ConfigError`, `QueryError`, `TransportError`, `ParseError`, and `BudgetExceeded` beneath it. `Outcome` is the closed vocabulary `ExecMeta.outcome` draws on, which is what makes an empty result a valid answer carrying a reason rather than an absence, and `Capability` is the closed vocabulary a client declares from. Contracts live here so that the modules and any platform adapter share them without circular imports.

**_constants** — Protocol and variant strings, the three op codes, the names of the synthesised database, collection, entity, and attribute, and the two property keys a fetch hands down under, one for the run's budget and one for the list this call's usage entries go into. No environment variable is named, read, or logged by this package.

### Compatibility Alias

`compat/blue_data.py` binds `BlueDataLlmSource` to `LlmSource` and does nothing else. The module existed when `blue-data` was an optional extra and the core matched the source surface by shape, and it held a subclass that added the real inheritance for callers who wanted it. The core now carries that inheritance, so the name is kept only so an import written against the old module keeps working, and it is an alias rather than a subclass because a second class would be a second method resolution order to get wrong for no behaviour of its own.

### The Data Layer Dependency

`blue-data>=0.1.0` and `tenacity>=9.0.0` are the whole runtime set. `blue-data` supplies the base class above and brings `pandas`, `numpy`, and `jsonpath-ng` along with it, which is what the install costs beyond this package's own code. `litellm` and `diskcache` remain optional and lazily imported.

The line the dependency does not cross is the one above the data layer. Nothing here imports `blue` or `blue_data_ops`, and a test asserts it, because a data source that reaches into the runtime calling it can only be used inside that runtime. Depending on the layer this source belongs to is a different thing from depending on the orchestrator that queries it.

## What the architecture deliberately does not contain

- A planning layer. There is no query rewriter, schema identifier, cardinality estimator, or partitioner. One request becomes one model call.
- An execution layer. There is no task tree, no fan-out runner, no pagination loop, and no empty-partition fallback.
- A validator stack. Format verification against declared types ships; factual verification does not, and per-run reporting is not a substitute for it.
- A retrieval or grounding path. The premise is closed-book.
- An infrastructure dependency. No server, no broker, no required database. The two cache stores that ship are a process-local dictionary and a directory of JSON files, and any other backend is a caller's choice behind the protocol.
