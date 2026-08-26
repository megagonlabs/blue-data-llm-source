# LlmSource — Decision Log

Architectural decisions are recorded here as they are made, throughout the design process.

---

## ADR-001 — Programming Language: Python

**Date:** 2026-07-29
**Status:** Accepted

**Decision:** Python, minimum version 3.10.

**Rationale:** Python is the language of the data layer this source plugs into and of the provider SDKs it calls. Choosing anything else would mean writing a binding for both ends to save nothing.

**Consequences:** All library classes, interfaces, and examples are written in Python. Type hints use syntax available from 3.10 onward, including `X | Y` unions, which is why the floor is 3.10 rather than lower.

---

## ADR-002 — Package Manager: pip with pyproject.toml

**Date:** 2026-07-29
**Status:** Accepted

**Decision:** Use `pip` with `pyproject.toml` and setuptools. No lockfile tool.

**Rationale:** Standard tooling with no additional toolchain for a consumer to install. A library with one runtime dependency does not benefit from a resolver that a consumer must also adopt.

**Consequences:** Dependencies and extras are declared in `pyproject.toml`. Consumers resolve against their own constraints, which is the correct behaviour for a library rather than an application.

---

## ADR-003 — Standalone: One Runtime Dependency, Everything Else an Extra

**Date:** 2026-07-29
**Status:** Accepted

**Superseded in part by ADR-023.** `blue-data` is now a declared core dependency. The rest of this decision stands: nothing depends on the orchestration platform or the operator library, and no infrastructure requirement is introduced.

**Decision:** The library declares no dependency on the Blue orchestration platform, on `blue-data`, or on the operator library. Its only runtime dependency is `tenacity`. The provider transport (`litellm`) and the on-disk cache (`diskcache`) sit behind an `llm` extra and are imported lazily at the point of use. `blue-data` sits behind a `blue-data` extra used only by the optional compatibility adapter. No infrastructure dependency of any kind is introduced: no server, no broker, no required database.

**Rationale:** This source is embedded in other people's processes. A planner, an operator, a CLI, and an agent framework each pull it in, and each pays for whatever it declares. A source that requires the orchestration platform to answer a question about the European Union is a source most callers will not take. Keeping the required set to one package also keeps the test suite honest: the suite passes with no provider SDK installed, which means the boundaries really are doubled rather than nominally doubled.

**Consequences:** `pip install blue-data-llm-source` brings in `tenacity` and nothing else. The default client raises a `ConfigError` naming the extra when it needs a package that is not installed, rather than failing on an import error the caller cannot interpret. Anything the library wants from the wider ecosystem it must either implement or accept by injection. Caching is where that shows: rather than depending on a cache library, the source declares a two-method store protocol and implements it twice on the standard library, once in process and once as a directory of JSON files. The `llm.cache` switch predates both and still opens `diskcache` from the extra, which is a loose end rather than a decision.

---

## ADR-004 — Match the Source Query Surface by Shape, Not by Inheritance

**Date:** 2026-07-29
**Status:** Accepted

**Superseded by ADR-023.** `LlmSource` inherits `DataSource` rather than matching it by shape, so the deviation this decision recorded no longer exists.

**Decision:** `LlmSource` carries `name`, `properties`, `execute_query`, `execute_query_nl`, `translate_nl_to_native_query`, and the metadata surface with the signatures the data layer's sources present, and imports nothing from the data layer to do so. An optional `blue-data` extra makes `compat.blue_data.BlueDataLlmSource` importable, which subclasses the real `DataSource` and delegates every behaviour to `LlmSource`. The core never imports the adapter.

**Rationale:** The data layer's registry annotates its source parameter as a type hint and does not check it: it reads the source's name and properties and stores the object, and it constructs by protocol without validating the type. A class with the right methods and the right signatures therefore works at runtime with no inheritance and no import. That is what makes ADR-003 and platform compatibility hold at the same time, rather than one being traded for the other. The specification for this source calls for inheriting from the base class, and this is a deliberate deviation from it, recorded rather than resolved quietly: the standalone posture was judged the more valuable of the two, and the adapter recovers inheritance for anyone who wants it.

**Consequences:** Two consumers, two installs. A caller who wants records installs the base package; a caller who wants static type checking or an `isinstance` result installs the extra. There is one implementation of every behaviour, so there is one place to fix a defect. Duck-typed compatibility can rot silently when the upstream contract moves, so a signature contract test runs in the job that installs the extra and compares both surfaces parameter for parameter with `inspect.signature`. Without that test the failure would surface as a runtime error inside someone else's integration; with it, it is one failing CI job.

---

## ADR-005 — Protocol `llm`, Variant `openai`

**Date:** 2026-07-29
**Status:** Accepted

**Decision:** The source reports protocol `llm` and variant `openai` in its connection properties. Protocol names the source family and is not caller-settable; variant names the implementation within the family and is.

**Rationale:** The data source specification gives every source both identifiers, and it uses them consistently: `sql`/`postgres` and `sql`/`mysql` are two variants of one protocol, and this source is specified as `llm`/`openai`. The distinction is load-bearing rather than cosmetic. A second provider variant is already in design, and a registry needs to distinguish two implementations of the same family without treating them as unrelated sources. Registering a vendor name as the protocol would make the family unnameable and the second variant a new protocol.

**Consequences:** A registry that recognises `llm` gains every implementation in the family at once. The `LLM.MODELS` op reports both fields, and `variant` is exposed as a read-only property. Recognising `llm` in the data layer registry is a small separate change; nothing here waits on it, because the source does not need to be constructed by that registry to work.

---

## ADR-006 — `llm_fn` Is the Model Client, Not a Translator

**Date:** 2026-07-29
**Status:** Accepted, amended by [ADR-022](#adr-022--a-bound-or-a-refusal-and-capability-declared-per-axis) — a callable declares the **row** axis only. The four-axis statement below was true of the intent and never of the code.

**Decision:** When `execute_query_nl` receives an `llm_fn`, that callable is used as the model client for the request, wrapped in a `FunctionClient`. The configured client answers when none is supplied. `translate_nl_to_native_query` returns the request unchanged.

**Rationale:** `execute_query_nl` is a template method: translate natural language into the source's native dialect through `llm_fn`, then execute the translated query. For a relational source the native dialect is SQL, for a graph source it is a graph query language, and the callable earns its place by producing one. For this source the native dialect **is** natural language, so translation is the identity and the parameter has no translation to perform. That leaves a callable typed `(prompt: str) -> str`, which is precisely the shape of a model call. Accepting it and dropping it would waste the one thing the caller has already configured, and would leave the source's most obvious injection point unused.

**Consequences:** A caller who already holds a model reuses it and pays for no second configuration. A caller who does not is unaffected. Client resolution has a documented order: `llm_fn` first, then a client injected at construction, then the default client. The `FunctionClient` wrapping the callable is built per fetch and holds the run's budget, against which it claims one attempt priced at nothing, so all four axes apply to the one call this release makes. What it cannot bound is anything the callable does internally, such as its own retries, because it cannot see them. A caller that retries inside its own model call and wants each attempt counted should inject a budget-aware client at construction instead.

---

## ADR-007 — Injected Model Client Behind a Structural Protocol

**Date:** 2026-07-29
**Status:** Accepted

**Decision:** The model boundary is a structural protocol with `complete` and `acomplete`. Any object carrying both can be injected, and no code performs an `isinstance` check against a concrete client class. The library never reads, stores, or forwards a provider credential.

**Rationale:** A structural protocol makes the boundary substitutable without inheritance, which matters in three separate places. A caller integrating from another framework substitutes its own client. A test substitutes a scripted double without patching a vendor SDK. And a second provider variant, already in design, substitutes a different transport without touching anything above the boundary. Keeping credentials outside the library follows from the same seam: a source that is handed a client has nothing to leak.

**Consequences:** The library's testability and its provider independence come from the same decision, which means neither can be traded away separately. `DefaultLlmClient` and `FunctionClient` ship as the two implementations. An injected client is responsible for the token detail it reports: the facade folds whatever usage records the client exposes into the run metadata for tokens and the model name. The call count and the spend come from the run's budget instead, so a client that does not claim against the budget it is handed reports zero calls and zero cost however many it made, whatever usage it recorded. That is why a call, cost, or time ceiling is refused for a client declaring no budget rather than measured through its usage; see ADR-021.

---

## ADR-008 — Execution Bounded on Four Axes, Enforced Before Spend

**Date:** 2026-07-29
**Status:** Accepted, superseded in part by ADR-019 and ADR-021

**Current account:** the ceiling and capability rules that ship are ADR-022 as corrected there. This entry records the decision as it was accepted and is kept for the reasoning behind it, not as a description of current behaviour.

One of the claims below is no longer true of the code: a contacted attempt is not
released. Cost was checked rather than claimed between ADR-019 and ADR-021, and is
claimed before spend again. The text stands as it was accepted; ADR-019 and ADR-021
record what changed and why.

**Decision:** Every run is bounded on row count, wall-clock time, API cost, and provider call count. Each limit is configurable and defaults to unlimited. Call count, elapsed time, and cost are checked and claimed before the provider is contacted, under one lock, once per provider attempt rather than once per operation. The timeout passed to the provider is capped at the wall-clock time the run has left. Rows are admitted after parsing, because the count is not known until then. A run stopped by a limit returns an empty or truncated result whose metadata names the axis, rather than raising. A cache hit consumes no provider call and no cost, though rows it returns still count, and a call that fails before reaching the provider releases its reservation.

**Rationale:** Accounting usage after each call records an overrun rather than preventing one. That is adequate for a single call and wrong the moment a caller fans out, because by the time the total is read the money is gone. Reserving under a lock is what makes the ceiling a ceiling rather than a report, and it is what allows the next release's concurrent execution to be added without revisiting the budget. Attempt-level rather than operation-level reservation for the same reason one level down: a run configured for two retries that reserves once makes three times the calls its limit allows, which is a suggestion rather than a limit. The time axis needs both halves to hold, so each attempt rechecks the deadline before it reserves, and no single call is given a timeout longer than the time remaining. Four axes rather than one because they fail differently: cost protects the account, calls protect against a runaway loop, time protects a caller with a deadline, and rows protect a caller that asked for ten and would otherwise receive two hundred.

**Consequences:** `Budget` and `Limits` are separate from the client, so a client of any kind can be handed one. Reservation and settlement are two steps, which means a client must release what it reserved and does not spend. A configured `max_calls` counts provider attempts, so a caller reading it as logical operations will find a retrying run reaching the ceiling sooner than expected; that is the intended reading. Returning rather than raising on a limit makes a ceiling a normal outcome a caller handles in the same branch as an empty answer, at the cost of one field to check. Retry depth is carried on `Limits` alongside the four axes and read by the client, rather than fixed in the client, so that a configured retry count is a setting the code honours rather than one it accepts and ignores.

**Correction (ADR-024):** the wall-clock statement above is narrowed. The time axis bounds admission and the cooperative waits in `DefaultLlmClient.acomplete`, not the whole run. The decision text stands as accepted; [ADR-024](#adr-024--the-time-ceiling-bounds-waiting-not-the-whole-run) and [what the time ceiling actually bounds](../../lib/README.md#what-the-time-ceiling-actually-bounds) state what it covers and what it excludes. The row, cost and call axes are unaffected.

---

## ADR-009 — One Prompt Module, Single-Pass Substitution

**Date:** 2026-07-29
**Status:** Accepted

**Decision:** Every prompt the package sends lives in one module, `prompts/`, rather than as constants on the classes that use them. Substitution is a single `str.format_map` over a fixed set of named fields, applied once. No template engine is used, and substitution is never repeated.

**Rationale:** Two things go wrong with prompts held next to the code that sends them. They get copied, and the copies diverge in wording and in variable naming without anyone noticing that two callers are now asking two different questions. And they get substituted with a template engine, which interprets rather than inserts. Running substitution repeatedly until the string stops changing, which is one way a prompt pipeline grows, expands any template syntax that happens to appear in a caller's own text: a request about `${HOME}` or a context containing braces is rewritten before the model sees it. A single pass over named fields inserts caller text and interprets nothing.

**Consequences:** A prompt change is a change to one file, and the recorded responses covering it move with it. A caller's request reaches the model as written, whatever characters it contains. Adding a prompt for a future capability means adding it to this module rather than to the component that uses it, which is a small discipline with an obvious payoff the second time a prompt is reused.

---

## ADR-010 — Format Verification Ships, Factual Verification Does Not

**Date:** 2026-07-29
**Status:** Accepted

**Decision:** The source verifies that a record matches the shape it was asked for: declared attributes present, values coerced to declared types where the coercion is unambiguous, exact duplicates removed. It does not verify that a returned value is true. No validator ships, and nothing in the result is presented as a correctness signal.

**Rationale:** These are two capabilities that are easy to conflate and have different fates. Format verification has an oracle: the caller declared a type, and a value either coerces to it or does not. The check is mechanical, deterministic, and free of a model call. Factual verification has no cheap oracle here, because the thing being verified is the source of the verification, and asking the same model whether its own answer is right is not an independent check. Reporting per-run metadata under a heading that implies correctness would let a real capability be marked delivered by something that does not deliver it, which is worse than not shipping it. This source is classified as probabilistic and sampled in the data source taxonomy, alongside vector search, so saying it does not establish truth is consistent with how the layer already describes it.

**Consequences:** The verifier is a pure function with no model call, tested against constructed input. A caller needing factual confidence composes this source with another one and performs that check itself, which is a planner composition rather than a feature of this source. The library documentation says plainly that a record is checked for shape and not for truth, and repeats it wherever a reader might otherwise infer more.

---

## ADR-011 — Align and Report, Never Drop Silently

**Date:** 2026-07-29
**Status:** Accepted

**Decision:** Returned keys are mapped onto requested attributes by exact match, then case-insensitive match, then a normalised match ignoring case and separators. A key matched under a different spelling is reported as remapped. A key that matches nothing is reported as unmapped. Every key the model produced is reported as observed. A value that will not coerce to its declared type is returned as it arrived, the record is kept, and the value is reported as a type mismatch carrying its record index, attribute, declared type, and original value.

**Rationale:** A model asked for `iata_code` returns `iataCode` or `IATA Code`, which the normalised form recovers, or a bare `IATA`, which it does not and which is reported unmapped, and the difference is spelling rather than meaning. Selecting only the requested columns and discarding the rest turns that into an empty column, which the caller discovers much later and cannot diagnose, because the evidence was thrown away at the point of loss. Three cheap match rules recover most of it, and reporting recovers the diagnosis for the rest. The same reasoning applies one level down: dropping a whole record because one field arrived in an unexpected format loses data the caller asked for, while returning it uncoerced loses nothing. Keeping it silently is the part that does not hold, because a caller that declared a type has been told its records are typed and one of them is not, so the failure is reported for the same reason an unmapped key is.

**Consequences:** `ExecMeta` carries three key-level fields and one value-level field that most results will leave empty, which is the point: a rising unmapped-key or mismatch rate for an unchanged request is the earliest visible sign that a model update has changed the source's output. Records carry `None` for an attribute the model did not supply, rather than omitting the key, so a caller can treat the result as rectangular when it asked for attributes. Callers that require strict typing must check `type_mismatches` and act on it, because the source reports rather than enforces.

---

## ADR-012 — Async Core with Thin Synchronous Adapters

**Date:** 2026-07-29
**Status:** Accepted

**Decision:** I/O-bound methods are `async def`. `afetch` is the core. `fetch`, `execute_query`, and `execute_query_nl` are synchronous adapters that bridge to it and contain no logic beyond argument extraction. Calling a synchronous adapter from inside a running event loop raises, naming the async method to use. Pure functions in reduction and validation stay synchronous.

**Rationale:** The source query surface this library matches is synchronous, so `execute_query` and `execute_query_nl` cannot be coroutines without breaking the contract they exist to satisfy. That leaves two options: write the core synchronously and lose concurrency, or write it asynchronously and bridge. The next release fans out concurrently over sub-requests, and a concurrency-heavy fan-out retrofitted onto a synchronous core is a rewrite rather than an addition, so the core is async now. Bridging costs three short methods. Making pure functions coroutines would cost every caller an `await` and buy nothing.

**Consequences:** There is one implementation of the fetch algorithm, and the adapters cannot drift from it. A caller already inside an event loop must use `afetch`, and is told so by an explicit error rather than by a nested-loop failure far from its cause. The client exposes both `complete` and `acomplete`, so an injected client must implement both.

---

## ADR-013 — Type Checker: Pyright in Strict Mode

**Date:** 2026-07-29
**Status:** Accepted

**Decision:** Use `pyright` in strict mode for static type checking, with `ruff` at line length 100 targeting Python 3.10 and the `E`, `F`, `I`, and `UP` rule sets for formatting and linting. Type errors fail CI.

**Rationale:** A library whose whole value is returning well-shaped data should be able to describe its own shapes. Strict mode enforces complete annotations rather than partial ones, which is the difference between types that document and types that decorate. `UP` keeps the code on the modern syntax the 3.10 floor makes available, so annotations do not drift back toward `typing` imports.

**Consequences:** The package includes a `py.typed` marker, so consumers type-check against it. `Any` is confined to public parameters that deliberately accept several shapes and are normalised immediately, and nothing downstream of that normalisation sees it. Strict mode occasionally requires an explicit cast or a targeted suppression at a boundary with an untyped dependency; each carries a reason at the site.

---

## ADR-014 — Unit Tests Only, Offline, Every I/O Boundary Doubled

**Date:** 2026-07-29
**Status:** Accepted

**Decision:** One test tier: unit tests. Every I/O boundary is doubled, the suite makes no network call, and it passes with no provider SDK installed. Coverage is measured and reported, and no threshold is enforced. Every configuration key has a test proving that changing it changes observable behaviour.

**Rationale:** There is no store to integrate with, so an integration tier would test only the provider, which is not this library's contract and cannot be tested deterministically. Doubling the model boundary is free because that boundary is already a protocol for other reasons. Enforcing a coverage percentage measures the wrong thing for a library this size; the question that matters is whether every public member is exercised, and that is asked directly instead. The per-key configuration test exists because a setting that is accepted and ignored produces runs whose recorded configuration does not describe what happened, and that failure is invisible until someone tries to reproduce a result.

**Consequences:** The suite runs on any checkout, on any machine, with no credential, which removes the incentive to skip a test because running it costs money. An autouse guard replaces the network, subprocess, and database entry points, raises on any attempt, counts what was attempted, and fails a test whose counts are not all zero; each class has its own test. Provider behaviour is covered by a stub installed over the lazy import and by constructed payloads for the shapes a provider produces rarely. The four worked examples from the source specification are covered a second way, by replaying provider responses recorded once and committed as plain JSON, which exercises the real parsing and reduction path without a network connection and without a recording library. Those tests assert shape rather than fact, since the answer a model gives has a date on it. A signature contract test against the data layer base class runs only in the job that installs that extra.

---

## ADR-015 — One Model Call per Request in This Release

**Date:** 2026-07-29
**Status:** Accepted, with one term corrected — this release makes one **completion** per request, taking up to `limits.max_retries + 1` provider attempts, three by default. `meta.calls` counts attempts, and it is attempts that consume the call and cost ceilings. Everything else below stands.

**Decision:** This release makes exactly one model call per request. Query rewriting, automatic schema identification, cardinality estimation, request partitioning, hierarchical task creation, concurrent execution, pagination, empty-partition fallback, cross-partition entity resolution, coverage accounting, and per-field confidence are **not** in this release. Sub-request caching is not either; the only caching is whole-response caching inside the client.

**Rationale:** The failure this source exists around is that a model asked for two hundred records returns forty and reports nothing wrong, and decomposition is the answer to it. But decomposition is a large mechanism whose interfaces are much easier to design correctly against a working single-shot implementation than against a sketch. Shipping the small complete thing first produces a foundation whose shape was earned: the contracts that matter for one call (the request, the record, the result, the budget, the client protocol) are exactly the ones the larger mechanism will extend, and they can be extended once rather than guessed at twice. It also gives callers something usable now for the requests that fit in one call, which is most of them.

**Consequences:** A request whose answer exceeds what one completion enumerates comes back short, and the result carries no estimate against which a caller could detect that. The documentation says so explicitly rather than leaving it to be discovered, because a caller planning around this source needs to know which checks remain its own. Some of the next release's work will touch files this release ships, which is accepted: no interface is frozen before there is evidence for its shape.

---

## ADR-016 — Distribution: PyPI Package with Extras, No Server Image

**Date:** 2026-07-29
**Status:** Accepted as the intended route; **not executed**. None of the three packages is published, to PyPI or to an internal index, and every install is from source. Read the tenses below as intent.

**Decision:** Three PyPI packages ship: the library with optional extras, the CLI, and the MCP server. Each interface package depends on the library and adds only its own framework. No container image is published.

**Rationale:** There is nothing to serve over a network. The library holds no state and opens no port, and the MCP server speaks over stdio and is started by its client, so an image would wrap a subprocess in a container for no gain. An image becomes worth building when an interface needs to be reachable over a network, and its shape should be decided then, against a real service, rather than reserved now. Separate packages rather than extras on one package because each interface carries a framework the library must not: a caller that wants records should not acquire a CLI toolkit or a protocol SDK to get them.

**Consequences:** The release pipeline builds and publishes three artifacts, library first, since the other two depend on it. The three are versioned together in this release; independent versioning is what the split buys and becomes worth using when one interface changes on a different cadence. Operators embedding the library manage the process that embeds it.

---

## ADR-017 — Interface Adapters Split Translation from Framework

**Date:** 2026-07-29
**Status:** Accepted

**Decision:** Each interface package is two modules. One holds everything that maps between the outside world and the library: option parsing, argument translation, tool schemas, and error-to-exit-code mapping. The other holds the framework: Click command declarations and printing, or the protocol transport. The framework SDK is imported only by the second module, and the MCP package asserts that boundary with a test. Each package exposes a single source-construction function, which is the only seam its tests replace.

**Rationale:** The part of an adapter worth testing is the translation, and the part that makes testing awkward is the framework. Separating them means the tool schemas can be validated, and the argument mapping exercised, without a transport or an installed SDK, which is how both suites run in CI. The MCP server takes this further by writing its tool schemas by hand rather than deriving them from handler signatures: the JSON is the contract an agent reads, and a contract should be written where it is read rather than inferred from a Python signature that happens to produce it.

**Consequences:** Two small modules per interface instead of one larger one. The import boundary is a rule rather than a convention, so it gets a test. A single construction function per package is the whole injection story for adapter tests, which keeps their fixtures short. The CLI additionally executes every shell block in its own README against a scripted client, so a documented command that stops working fails the suite rather than surviving as a false claim.

---

## ADR-018 — An Empty Result Is an Answer, and It Carries Its Reason

**Date:** 2026-07-31
**Status:** Accepted

**Decision:** An empty result is a valid answer from this source rather than a failure, and `ExecMeta.outcome` states why. The vocabulary is closed: `ok`, `empty_query`, `model_returned_none`, `filtered_to_empty`, `stopped_by_limit`, `truncated`. An empty or whitespace request is one of these cases and returns an empty result with `empty_query` rather than raising; a request that is not a string is still a caller mistake and raises. The field rides on `FetchResult`, so it reaches `fetch` and `afetch` and not `execute_query` or `execute_query_nl`, which return `list[dict]` by the contract of the surface they match.

**Rationale:** The layer's other sources treat an absent result as an empty list, so raising on an empty request would make this source the exception in a set of sources a planner is meant to treat uniformly. But collapsing every empty case into an empty list throws away the fact that matters most. "You asked nothing", "the model says there are none", "everything it returned was filtered out", and "a ceiling stopped the run" call for four different responses from a planner: fix the request, ask a different source, loosen the shape, or raise the limit and retry. A closed vocabulary is what lets that be a branch rather than a guess, and closing it is what keeps a later addition from silently changing the meaning of an existing value.

**Consequences:** `Outcome` is exported from the package root, because a caller comparing against a plain `str` field otherwise has to hardcode the literals. The row cap owns the outcome whenever it fires, reporting `stopped_by_limit` when it leaves nothing rather than attributing the emptiness to reduction. A caller that treated an exception as its empty-request signal must read `outcome` instead, which is a breaking change against the behaviour recorded in the library's compatibility notes and is called out there. The CLI exits 0 for an empty query and prints the outcome under `--verbose`; the MCP server returns `outcome` in every tool result, so an agent distinguishes a ceiling, an empty ask, and a model that knows nothing without parsing a message. The source surface gains nothing from this, which is the price of matching a signature that returns records, and a caller needing the reason uses the library surface.


---

## ADR-019 — Which Ceilings Are Claimed, Which Are Settled, and Who They Bind

**Date:** 2026-08-01
**Status:** Accepted. Supersedes ADR-008 in part. Superseded in part by ADR-021.

**Current account:** the ceiling and capability rules that ship are ADR-022 as corrected there. This entry records the decision as it was accepted and is kept for the reasoning behind it, not as a description of current behaviour.

Three of the claims below are no longer true of the code: cost is claimed before the
attempt rather than checked, a reservation can be handed back through
`Reservation.release`, and an injected client that declares no budget is refused a
provider-level ceiling rather than bound by one. The text stands as it was accepted;
ADR-021 records what changed and why.

**Decision:** Call count and wall-clock time are *claimed* before the provider is contacted, under one lock, once per provider attempt. Rows are admitted after parsing. Cost is *checked* before each attempt and *settled* after it: an attempt that starts under the ceiling may finish over it, and the overrun is recorded in `stopped_by` and refuses the next attempt. An attempt that reached the provider is never released, whatever it returns; everything that can fail locally happens before the reservation, and no method exists to hand a reservation back. Every ceiling applies identically whichever client answered — default, injected, or a supplied callable — and a run that finished over one reports `outcome` as `stopped_by_limit` rather than `ok`.

**Rationale:** ADR-008 claimed all three of call count, time and cost were claimed before spend. Cost never was, and could not be: a call's price is not known until it returns, so claiming it needs a per-attempt estimate from the model and the bounded request. Stating an enforcement the code did not perform was worse than the weaker guarantee, because a caller sizing a ceiling against that sentence would size it wrong.

The release of a failed attempt was the more damaging half. It was written so a transport failure would not consume a caller's call allowance, which reads as generous and means a run configured for two retries reaches the provider three times under a ceiling of one — reinstating, through the failure path, exactly the escape that per-attempt reservation was introduced to close. A ceiling that only counts successes does not bound spend, because a failing provider is still a provider being paid to fail.

Binding every client path identically is the same principle applied one level out. A limit that holds for the default client and not for an injected one is not a limit, it is a default; and a caller reading one number in the configuration is entitled to have it mean one thing.

**Consequences:** `release_uncharged` is removed rather than left unused. A free-standing counter decrement carries no evidence that the attempt it undoes stayed local, so any future caller could rebuild the escape; a rollback that is genuinely needed should carry a reservation handle that can be released once. `ExecMeta.calls` now reports provider entries rather than successful ones, so a run whose only attempt failed reports one call, not zero. Spending the last allowed call is not a stop: nothing was refused, and reporting one would make an ordinary run under an exact ceiling look truncated. A reserved cost ceiling remains v2.0's obligation, recorded in criterion 6 of that release's acceptance rather than quietly retired.

---

**Correction (ADR-024):** the wall-clock statement above is narrowed. The time axis bounds admission and the cooperative waits in `DefaultLlmClient.acomplete`, not the whole run. The decision text stands as accepted; [ADR-024](#adr-024--the-time-ceiling-bounds-waiting-not-the-whole-run) and [what the time ceiling actually bounds](../../lib/README.md#what-the-time-ceiling-actually-bounds) state what it covers and what it excludes. The row, cost and call axes are unaffected.

## ADR-020 — An Absent Answer and a Null Answer Are Different Facts

**Date:** 2026-08-01
**Status:** Accepted

**Decision:** Alignment records, per record, which requested attributes the model actually answered, separately from the values it answered with. A record that answered none of them is dropped before deduplication and row admission. A record whose only answer is an explicit null is kept.

**Rationale:** Alignment fills every requested attribute, so a response whose keys all failed to map becomes a record of nothing but `None`. Returned, it says the model answered when it did not, and a caller counting records gets a number with no content behind it. But a JSON null is also `None`, so deciding this from the values cannot tell "not answered" from "answered null" — and the second is a real answer that a nullable attribute is expected to produce. Dropping it would lose data silently, before any stage that could report which case occurred.

Recording presence at the point where it is known, rather than inferring it later from a value that cannot carry it, is the only version of this that is not a guess.

**Consequences:** `Alignment` gains a positional list of matched attribute names, which every stage between alignment and the drop must preserve in order. Without requested attributes there is nothing to be missing and records pass through unchanged. `filtered_to_empty` becomes reachable, which it was not before: nothing in the pipeline could remove a record, so a closed vocabulary advertised a branch the code could not produce while the branch that ran labelled a content-free record `ok`.

---

## ADR-021 — Cost Is Reserved Against an Estimate, and a Ceiling Binds Only a Client That Can Hold It

**Date:** 2026-08-03
**Status:** Accepted. Supersedes ADR-019 in part, and restores the cost half of ADR-008. Superseded in part by ADR-022.

**Current account:** the ceiling and capability rules that ship are ADR-022 as corrected there. This entry records the decision as it was accepted and is kept for the reasoning behind it, not as a description of current behaviour.

Four of the claims below are no longer true of the code. The estimate is an upper bound
rather than an average, so it counts characters instead of dividing them by three, and
`pricing.estimate_cost` no longer exists. An unlisted model is refused rather than priced
at the table's most expensive rate. Capability is declared per axis through a method rather
than by carrying a `budget` attribute, so a cost or time ceiling against `llm_fn` is now
refused where this text has it binding. And `ExecMeta` reads its token counts from a
per-call list rather than from the client's usage history. The text stands as it was
accepted; ADR-022 records what changed and why. Its closing claim that neither interface
package surfaces `key_collisions` is also out of date: the MCP server reports the field on
every call and the CLI prints it under `--verbose`.

**Decision:** `Budget.reserve(estimated_cost)` claims one provider attempt and an upper bound on its cost together, under one lock, and returns a `Reservation`. The estimate comes from `pricing.estimate_cost`: the prompt as sent converted at three characters per token at the model's published input rate, plus the whole `max_tokens` allowance at its output rate, with a model that is not in the table priced at the most expensive rate in it. The reservation resolves exactly once, through `settle` for a call whose real cost could be read, `keep_unpriced` for one that reached the provider and could not be priced, or `release` for an attempt that never reached the provider. `Budget.settle` and `Budget.reserve_call` no longer exist.

A call, cost, or time ceiling binds only a client that declares a `budget` attribute, which `types.BudgetAwareClient` names as a protocol. The run's budget is handed to such a client under the `budget` property on each call rather than set on the client, and a configured `limits.max_calls`, `limits.max_cost`, or `limits.max_seconds` against a client declaring no budget raises `ConfigError`. `limits.max_rows` still binds for any client. `ExecMeta.calls` and `ExecMeta.cost` are read from the budget rather than summed from usage entries; tokens and the model name still come from usage.

`fetch` and `afetch` take an optional `budget`. Supplied, the fetch derives a child from it through `Budget.derive`, which spends from what the parent has left, so the effective ceiling on each axis is the smallest along the chain and the caller's ceilings accumulate across every call the budget is handed to. Omitted, one is built per fetch from the configured limits.

Two returned keys that normalise to the same requested attribute no longer resolve silently: the first is kept and the second is reported in `ExecMeta.key_collisions` with its record index, the attribute, both keys, and the ignored value. The same attribute requested twice raises `QueryError`. Two *requested* attributes that merely normalise alike do not: the fuzzy index drops the entry it cannot separate.

**Rationale:** ADR-019 recorded that cost could not be claimed because a call's price is not known until it returns, and deferred a reserved ceiling to v2.0. That was true about the price and wrong about the conclusion. A ceiling cannot be claimed without a number to claim, but the number does not have to be exact — it has to be an upper bound. Estimating one is cheap, the sibling operator library already does it one layer up for a semantic join, and being wrong high is released on settlement while being wrong low has already let the ceiling be crossed. What a checked ceiling could not do is the whole reason to have one: ten concurrent leaves each saw room for one call and collectively overshot by ten calls' worth, and a cost that could not be read became a free call, so a ceiling went on admitting every remaining attempt while the running total stayed where it started.

The client boundary is the same argument applied to who is being bounded. ADR-019 said every ceiling applies identically whichever client answered, which was the right principle applied to a mechanism that could not carry it: the facade reserved one call on an injected client's behalf, and one invocation of an opaque client is not evidence of one provider call. Such a client may answer from its own cache, make five calls, or make none. Reporting a ceiling in that case is worse than refusing it, because the caller believes a number that is not holding anything. Refusing at the first fetch, with a message naming the limits and the ways out, moves the discovery from a bill to a boundary.

A caller-owned budget follows from the same reading of what a limit means. A ceiling configured on the source bounded one request, while a caller reading it usually means a unit of work; derivation is what lets both exist without either one lying, since a child can never hand out more than its parent holds.

Collisions are the tolerance in the match rules coming back around. Matching that ignores case and separators is what recovers `IATA Code` for `iata_code`, and it is also what lets two returned keys reach one attribute. Letting the later one win made the surviving value depend on the order the model emitted its keys in, which is not something a caller can reason about, and which spelling was meant is not knowable here. So the choice is reported rather than resolved, exactly as an unmapped key is. Two requested attributes that normalise alike are the caller's ambiguity instead, and raise, because no returned value could be assigned to either without guessing.

**Consequences:** A run is refused slightly earlier than its real spend would require, since the estimate assumes the whole output allowance is used and an unknown model costs the table's maximum. That is the direction to be wrong in for a ceiling, and it is worth stating plainly because a caller sizing `limits.max_cost` against actual invoices will find it binding sooner than the invoices suggest. `pricing.PRICES` is public list-price data that goes stale, in the safe direction, and is a file to update rather than a mechanism to redesign.

`Reservation` replaces the free-standing counter decrement ADR-019 removed, and does so on the terms ADR-019 set: a handle that resolves once and carries the evidence of what it undoes. `Budget.settle` and `Budget.reserve_call` are gone, so a caller written against them does not compile against this release; the migration is `reserve(estimate)` followed by one of the three resolutions.

An injected client now has a contract to meet rather than a courtesy to accept. A client that declared no budget and was previously bounded by a reservation the facade made for it is now refused those three ceilings outright, which is a behaviour change for any caller relying on the weaker form. Adding a `budget` attribute and claiming against the budget arriving on the `budget` property is the fix, and the shipped clients are the reference for it.

`ExecMeta.calls` and `ExecMeta.cost` no longer come from usage, which closes a race that a cursor into a shared usage list could not: two concurrent fetches through one reused client can read the same cursor and each slice both entries. The visible consequence is that a client which does not claim against the run's budget reports zero calls and zero cost, which is a correct report of what the budget saw and is why such a client is refused a ceiling rather than measured through one.

`ExecMeta.key_collisions` is a field most results leave empty, like the three key-level fields beside it, and for the same reason: a rising collision rate for an unchanged request is a signal about the model rather than about the caller. Neither interface package surfaces it yet, which is a gap in those adapters rather than in the contract.

---

**Correction (ADR-024):** where this decision covers `Capability.SECONDS`, the ceiling a client is held to on that axis bounds admission and cooperative waiting rather than the whole run. The capability rule itself — a ceiling binds only a client that declares the axis — is unchanged, as is everything this decision says about cost.

## ADR-022 — A Bound or a Refusal, and Capability Declared Per Axis

**Date:** 2026-08-03
**Status:** Accepted, corrected in part below. Supersedes ADR-021 in part.

Four claims in the accepted text are no longer true of the code, and the text stands as it was accepted rather than being rewritten. The bound counts the prompt's **UTF-8 bytes**, not its characters: a tokenizer with no merge for a character emits several tokens for it, so characters do not bound tokens and bytes do. `FunctionClient` declares **rows only**, not calls and rows, because one invocation of a caller's function is not one provider call. A repeated key in one JSON object is **rejected** rather than preserved under a renamed key, and `parsing.duplicate_origin` no longer exists. And the caller-supplied price keys are **reachable** through the shipped client, which the accepted text recorded as a known gap.

**Decision:** `pricing.bound_cost(prompt, model, max_tokens, properties)` replaces `pricing.estimate_cost`. It returns `len(prompt) + FRAMING_TOKENS` at the model's input rate plus the whole `max_tokens` allowance at its output rate, and it raises `ConfigError` when that figure cannot be established: when the model has no published price and the caller supplied none, or when `llm.max_tokens` is not a finite positive number. `rates_for` returns `None` for an unlisted model rather than the dearest entry in the table. A caller who knows what an unlisted model costs supplies `llm.price_per_input_token` and `llm.price_per_output_token`, which must be given together. `can_bound` answers the same question without raising. No bound is computed at all when no cost ceiling is configured, so a run bounded on other axes is never refused over a model this package has not heard of.

`types.Capability` names four axes, `CALLS`, `ROWS`, `SECONDS`, and `COST`, with `ALL` covering the set, and `BudgetAwareClient` becomes a protocol with one method, `budget_capabilities() -> frozenset[str]`, in place of a `budget` attribute. `DefaultLlmClient` declares `Capability.ALL`; `FunctionClient` declares calls and rows. The source refuses each configured ceiling whose axis the answering client does not declare, naming the ceilings and the axes that were declared. `Capability` is exported from the package root alongside `Budget`, `Limits`, `Reservation`, and `BudgetAwareClient`.

`Limits` validates every field on construction, refusing a wrong type, a `bool`, a negative value, or a non-finite value with a `ConfigError`, and an unknown `limits.*` property key raises listing the accepted set. A provider cost that arrives non-finite is treated as unreadable rather than as zero. Retry depth narrows along the budget chain through `Budget.effective_retries()`, like every other axis. Everything after the provider entry point is invoked, including materialising the response, sits inside one guard, so a reservation resolves on every path out of an attempt. A derived budget does not inherit its parent's `stopped_by`.

The source hands the client a list under the property key `usage_sink` and reads token counts and the model name only from it; calls and cost continue to come from the budget. `parsing.parse_records` preserves both of two identical JSON keys, renaming the later one internally and exposing the original through `parsing.duplicate_origin`, so alignment reports the collision and keeps the first. Two *requested* attributes that normalise alike, including two identical spellings, raise `QueryError`. `source.decide_outcome` becomes a single public ordered rule: any `stopped_by` wins, yielding `truncated` when the row cap fired and records remain and `stopped_by_limit` otherwise; then records present is `ok`, nothing from the model is `model_returned_none`, and the remainder is `filtered_to_empty`.

**Rationale:** ADR-021 argued that a ceiling needs a number to claim and that the number has to be an upper bound rather than an exact one. It then claimed a figure that was not an upper bound. Dividing prompt characters by three is the average for English prose, and text of short words, punctuation, or non-Latin script tokenizes close to one token per character, so the figure was exceeded by ordinary input. Pricing an unlisted model at the table's most expensive entry has the same defect one level up: a model the table has never heard of may cost more than anything in it. A bound that is usually right is a ceiling that is occasionally crossed by exactly the call that crosses it, which is the failure reserving was introduced to remove. Counting characters is loose in the only safe direction and rests on a property no tokenizer can violate. Where even that is unavailable the honest move is to refuse the ceiling and say why, because a caller who asked for a ceiling is better served by an error naming what is missing than by a number that was invented for them.

The capability change is the same principle applied to who is being bounded. ADR-021 treated a `budget` attribute as the whole contract, and attribute presence is not evidence of behaviour: a client holding `budget = None` satisfied the protocol while making unbounded calls under a configured ceiling. A method has to be written on purpose. Splitting the declaration by axis follows from what a client can actually prove. `FunctionClient` can prove it invoked the caller's function once; it cannot know whether that function contacts a provider, what it is billed, or how to interrupt it, and under the single-flag version it settled such a call at a cost of zero, which made a cost ceiling appear to hold over a callable that might be spending anything. Zero is not a measurement, and reporting one is the same class of error as inventing a price.

The rest are defects in the machinery around those two decisions, each of the same shape: a ceiling that was configured and did not bind. A `NaN` limit compares false against every spend, so it read as configured and enforced nothing. A misspelled `limits.*` key was accepted and ignored. Retry depth was read from the child budget alone, so a derived budget could spend more attempts than its parent authorised. A response that failed to materialise left its claim outstanding, so the retry reserved again against headroom that never came back. A child inheriting `stopped_by` reported an earlier refusal as its own outcome. Two concurrent fetches through one reused client each sliced the other's tokens out of a shared usage list. Two identical JSON keys were resolved by the decoder keeping the last, silently and in the opposite direction from every other collision here. And the outcome was assigned in pieces along the fetch path, which produced combinations nobody chose, such as an empty result reading `model_returned_none` while `stopped_by` named the limit that emptied it.

**Consequences:** A cost ceiling now has a floor that a caller must size against, and it is the whole `max_tokens` allowance at the model's output rate, independent of the prompt. With the shipped defaults that floor is about $0.041, so a small `limits.max_cost` against a large `llm.max_tokens` refuses every call before making one. That is the correct behaviour for a bound and it is a surprise if it is not written down, so it is stated wherever a ceiling is documented, along with the remedy, which is to lower `llm.max_tokens` rather than to raise the ceiling.

A cost or time ceiling on a run that supplies `llm_fn` is now refused. ADR-006 recorded that all four axes applied to that path; two of them did not, and the version that appeared to apply them did so by settling at zero. The migration is to inject a client declaring those axes, or to bound the run on rows. Not calls: an earlier wording of this sentence said calls and rows, which contradicts the rows-only correction three paragraphs above and the shipped `FunctionClient`.

The caller-supplied price keys are documented and not currently reachable through the shipped client, which validates `llm.*` overrides against a closed set that does not include them and does so before the bound is computed. The escape hatch exists in `pricing` and is available to a client of one's own; wiring it through `DefaultLlmClient` is a small change that has not been made, and it is recorded here rather than left for a caller to discover from a `ConfigError` naming the accepted overrides.

`ExecMeta.calls` and `ExecMeta.cost` still come from the budget, and the token counts now come from a list the fetch owns. A client that ignores `usage_sink` reports no tokens and no model name for that fetch, which is a gap in detail rather than a wrong number, and is the direction to fail in.

`stopped_by` outranking everything else means a run that produced records and was found over its cost ceiling during settlement reports `stopped_by_limit` rather than `ok`. A caller that branched on the record list alone will see no change; a caller that branched on `outcome` sees a run it previously read as complete reported as cut short, which is what it was.


---

## ADR-023 — The Data Layer Is a Dependency, the Ceiling Is a Query Input, and the Cost Is Answerable in Advance

**Date:** 2026-08-03
**Status:** Accepted. Supersedes ADR-003 in part and ADR-004 entirely.

**Decision:** Three changes, settled together because they describe one position on how this source relates to the layer around it.

`blue-data` is a declared core dependency and `LlmSource` inherits `blue_data.source.source.DataSource`. Shape matching is retired, the core dependencies become exactly `blue-data>=0.1.0` and `tenacity>=9.0.0`, and the `blue-data` extra is removed rather than left as a no-op.

A spending ceiling is an input to the query. `execute_query` and `execute_query_nl` accept `optional_properties`, and a `limits.*` key there binds that call and no other. A caller-owned budget that accumulates across calls remains available and is the secondary route.

`estimate(query, attrs, context, properties)` answers what one fetch could cost, before anything is spent, returning an `Estimate` carrying `bounded`, `max_cost`, `reason`, `model`, `prompt_bytes`, and `max_output_tokens`. It is reachable both as a method and as the `LLM.ESTIMATE` op, which returns the estimate as a one-record list because that is what the query surface returns. `Estimate` and `OP_ESTIMATE` are exported from the package root.

**Rationale:** Every other source in the layer depends on `blue-data` and inherits from it. Standing apart bought a smaller install and cost the thing inheritance provides: with shape matching, a divergence from the upstream contract is discovered by whoever hits it, and with inheritance it is a type error. For a package whose whole point is that a configured limit actually holds, the second is worth more than the first.

The ceiling belongs to the request rather than to the source because a caller builds one source and asks it many questions, each with its own tolerance. An accumulated total is a different question and a rarer one, so it stays available rather than being the shape everything is fitted to.

`estimate` exists because a caller deciding whether to run a request should not have to run it to find out what it might cost. Sharing the bound with the reservation is the part that matters: an estimate a caller trusts and a ceiling the run enforces have to be the same number, or a request that estimates within budget is refused when it runs.

**Consequences:** The install acquires `pandas`, `numpy` and `jsonpath-ng` through `blue-data`, and `blue-data` is not on a public index, so it must be installed from its checkout first — the same shape the other source subrepos already have, and now documented here rather than discovered from a resolver error.

Base-class order becomes load-bearing. `DataSource` is a plain class whose every method returns an empty result, so a method left unoverridden answers nothing rather than failing, and a contract test asserts each shared method resolves to this class.

Inheriting does not make this source reachable by protocol name. An instance is reached through `register_source`.

**Updated 2026-08-21.** Two statements above described the base class as it stood when this decision was taken, and it has since moved. `connect_source` no longer depends on a protocol arm: registration records the source class, and a later `connect_source` returns the live instance or rebuilds an equivalent one from that record, so a registration outlives the object that made it. And the base class is no longer uniformly empty: its transaction methods raise `NotImplementedError` naming this source, and its orchestrator-tool methods carry real behaviour that this source overrides with a refusal. The decision itself stands; only the description of what surrounds it has aged.

**Updated for 1.1.** The four tools this source can serve — `list_databases`, `list_collections`, `get_schema` and `execute_query` — are advertised again, with their definitions taken from the base class so the schemas cannot drift from its dispatcher. The tool-driven query loop stays refused, because it drives provider calls this source's ceilings do not govern. The sentence above describes 1.0.

`compat.blue_data.BlueDataLlmSource` becomes an alias for `LlmSource`. It existed to add inheritance for callers who installed an optional extra; a subclass would now be a second class to test and a second method resolution order to get wrong, for no behaviour the class does not already have.

An unpriceable model makes `estimate` return `bounded=False` with a reason rather than raising, because asking what a request might cost is fair even where the answer is unknown.


## ADR-024 — The Time Ceiling Bounds Waiting, Not the Whole Run

**Status:** Accepted. Narrows the time-axis claim in ADR-008 and ADR-019, and the `Capability.SECONDS` part of ADR-021. The row, cost and call axes are unchanged, as is everything else in all three.

**Context:** `limits.max_seconds` was documented as a wall-clock bound on a run, and measurement
found it was not one. Checking the ceiling only between attempts let a single provider call run
indefinitely past it. Putting the whole awaited path under one deadline bounds every ordinary wait —
the provider call, the parallelism semaphore — to close to the ceiling. What remains past it is a
class of paths rather than an overlooked one: synchronous work that no deadline can interrupt, such
as serialisation, cost accounting, and post-processing of a large response, and providers that catch
cancellation and answer anyway. Each of these reports `max_seconds` truthfully and can report it many
times past the ceiling. `limits.max_seconds` is therefore not an end-to-end deadline.

**Decision:** State the guarantee the code can keep. `limits.max_seconds` bounds admission and every
cooperative wait in `DefaultLlmClient.acomplete`: it refuses the next attempt at the ceiling, caps
backoff, caps the provider timeout, and cancels a wait in flight. It is not an end-to-end wall-clock
bound. `lib/README.md` holds the single canonical statement with the full exclusion list, and every
other document points at it rather than repeating it, so the list cannot go stale in one place and
not another. A run that finishes over the ceiling reports that rather than succeeding: a late answer
from work that ignored its cancellation is refused, because a ceiling that fails silently is worse
than one that reports late.

Abandoning work that declines its cancellation, rather than waiting for the acknowledgement, was
implemented and then withdrawn. It released the caller and cost more than it bought: the caller's
own cancellation no longer reached the work, the abandoned task could be collected mid-flight or
keep a one-shot loop from returning, and it settled real cost into the caller's budget after a
result reporting none had been returned. A library that shares an event loop with its caller has no
isolation boundary to offer, so the exclusion is documented rather than engineered around.

The alternative was to keep the stronger claim and make it true. That needs an isolation boundary a
deadline can abandon — a subprocess, or a worker whose result is dropped rather than awaited —
around work that is currently a method call. `asyncio` cannot interrupt code that is not awaiting,
and `asyncio.to_thread` does not help: the loop joins its executor at shutdown, so abandoning a
thread's result does not stop the work or release the process. That is a different architecture
from a library a caller imports, and it buys a guarantee this library's callers have not asked for.

A ceiling documented as holding what it does not is worse than a narrower one documented honestly,
because the caller stops watching.

**Correction:** `llm.rate_limit` was withdrawn from v1.0 and is refused with a `ConfigError`, so the
rate-limit pause this decision originally covered no longer exists. The provider call and the
parallelism semaphore are unaffected, and so is everything else this decision says.

**Consequences:** A caller who needs a hard wall-clock stop imposes it above this library, in a task
or process it can cancel. `stopped_by: max_seconds` remains truthful about which ceiling was crossed
and may be reported well after it. The cost and call axes are unaffected: each is claimed
before the provider is contacted and none depends on interrupting work in flight.

## ADR-025 — A Stream Whose Cost Cannot Be Read Is Charged At Its Bound

**Status:** Accepted in 1.1, when `llm.stream=True` stopped being refused. Applies the same rule ADR-021 sets for an unreadable cost, to a case that looks different and is not.

**Context:** Streaming exists here for compatibility rather than speed. Records are parsed from a complete JSON document, so a stream is accumulated in full before anything else happens and no caller sees a partial result. Some provider configurations and proxies require or default to streaming, and without support those callers cannot use the package at all.

The accounting is the part that needed deciding. A provider streaming a response may send no usage figures with it. The obvious approach — assemble the chunks into a response and price that — produces a number, and the number is wrong: the assembler fills the usage block from a local tokenizer when the stream carried none, and the pricing function then prices that estimate as though the provider had reported it. Stripping the block instead is no better, because pricing a response with no usage returns zero, and zero settles as a free call.

Both failures are quiet. One overcharges or undercharges by an invented amount, the other reports a billed call as free, and each leaves a ceiling acting on a figure that does not describe what happened.

**Decision:** Whether usage was reported is read from the raw chunks, before assembly, and carried alongside the payload. A stream that reported usage is priced normally. A stream that did not keeps its reservation at the bound the attempt reserved, exactly as an unreadable cost does on the non-streaming path, and the result reports `accounting_known` false.

**Consequences:** A caller streaming from a provider that sends no usage is charged the reserved bound rather than the real cost, which is higher than the truth and is the only direction that is safe for a ceiling. The figure is honest about being a bound because `accounting_known` says so. Nothing was added to the provider call to improve the odds of receiving usage — an extra transport option could break the very proxies this feature exists for.

The rule is one rule, not two: an unreadable cost is charged at its bound wherever it occurs.
