# LlmSource — Detailed Architecture

## The Source Base Class

`LlmSource` is declared as `class LlmSource(DataSource)`, taking `blue_data.source.source.DataSource` as its base, which is the same base every other source in the data layer takes. `blue-data>=0.1.0` is a declared core dependency for that reason, alongside `tenacity>=9.0.0`, and it brings `pandas`, `numpy`, and `jsonpath-ng` into the install. `blue_data` is imported in exactly one module, `source.py`, and nothing in the package imports `blue` or `blue_data_ops`, which a test asserts: depending on the data layer this source belongs to is one thing, and depending on the orchestrator that queries it is another.

The base order carries weight that is easy to miss. `DataSource` is a plain class rather than an abstract one. Most of its methods return an empty result rather than raising, so one left unoverridden here answers nothing instead of failing. That is not true of all of them any more: the transaction methods raise `NotImplementedError` naming this source, and the orchestrator-tool methods carry real behaviour, which is why this source overrides those three with a refusal rather than inheriting them. Listed the other way round, or simply left unwritten, `fetch_databases` would return an empty list and `execute_query` would return no records without a model ever being contacted, and nothing would report a problem. A test walks the shared surface and asserts each method resolves to `LlmSource`, which is what turns an ordering mistake into a failing test rather than a silently empty source.

Inheritance does not extend to construction by protocol name. `DataRegistry.connect_source` returns the live instance while it is registered, and reconstructs one from the class path recorded at registration once it is not, so the registration outlives the object. `DataRegistry.register_source` takes an already-built instance, stores it under its name, and reads only `name` and `properties`, which is the route that works. A protocol-name arm would be an upstream change, and is not needed: reconstruction goes through the recorded class.

## External Interfaces

All interfaces are thin adapters over the Python library and ultimately call `LlmSource.afetch`. Neither exposes `estimate`, so a caller sizing a request before running it reaches the library directly.

| Interface | Package | Technology | Primary Use | State |
|-----------|---------|-----------|-------------|-------|
| Python Library | `blue-data-llm-source` | Native Python API | Embedding the source in applications, planners, and operators | Shipped |
| MCP Server | `blue-data-llm-mcp` | MCP over stdio | LLM agent tool use, through `llm_fetch` and `llm_info` | Shipped |
| CLI | `blue-data-llm-cli` | Click + Rich | Interactive exploration, debugging, scripting | Shipped |
| REST API | — | — | — | Not implemented, not scheduled |

### Python Library

`LlmSource` is the native interface. Direct method calls with no serialisation overhead. Everything else wraps this layer and holds no logic of its own.

### MCP Server

Two modules with a deliberate split. `tools.py` holds the tool specifications, their JSON input schemas, the argument translation, and a dispatcher that routes by tool name and raises for an unknown one; it imports nothing from the protocol SDK. `server.py` holds the transport, registers the two handlers, and is the only module that imports the SDK. The split is what allows the schemas and the translation to be exercised with only the library installed, which is also how they run in CI.

A tool result carries the records and a compact accounting object. A library error becomes a failed tool call whose text is the error kind followed by the library's own message, unreworded, because those messages are written to be read by a model.

### CLI

Two modules, likewise split. `commands.py` declares the Click group, the `fetch` and `info` commands, their options, and the table and JSON printers. `config.py` holds every mapping between what a person typed and what the library accepts: attribute parsing, limit assembly, property construction, source construction, and the error-to-exit-code table. Click commands are synchronous, so the adapter calls the library's synchronous wrapper rather than bridging to the async core itself.

`config.build_source` is the single seam the tests replace to inject a scripted client.

---

## Information Flow Overview

### Fetch Path

1. Caller invokes `afetch(query, attrs, context, max_records, properties, llm_fn, budget)`, or `fetch`, which wraps it.
2. The ceiling is assembled and the adapter keys are checked before this step, so a misspelled `limits.*` key is refused ahead of a malformed request: both are caller mistakes, and the one that can be answered without looking at the request is answered first. `checks` then validates and normalises each argument: the request is stripped and a non-string is rejected, attributes become a list of `Attr`, context becomes one block of text, and `max_records` must be a positive integer when present.
3. An empty request ends the run here, with no records and an outcome of `empty_query`. No model is called, and no budget is constructed.
4. Source properties are merged with any per-call properties, with the per-call values winning.
5. `Limits` is assembled from the merged properties. A `max_records` argument, when given, replaces the row limit for this run.
6. The run's budget is produced from those limits: a fresh `Budget` when the caller supplied none, and otherwise a child derived from the caller's, which spends from what that budget has left. A client is then resolved: a supplied `llm_fn` wins, then a client injected at construction, then a `DefaultLlmClient` built from the merged properties and handed the budget.
7. The resolved client is asked which axes it can be held to, through `budget_capabilities()`. The ceilings weighed against that answer come from `Budget.configured_axes()`, which walks the whole chain, so a ceiling the caller set on its own budget and never repeated in this request is checked as well; reading the child's own limits alone let a parent-only `max_cost` reach a client that had never claimed it could hold one. Every configured ceiling on an axis the client did not declare is refused with a `ConfigError` naming those ceilings, the axes the client did declare, and the ways out; `limits.max_rows` is unaffected, since this source counts the records itself. The run's budget is then added to the per-call properties under the key `budget`, and a fresh empty list under the key `usage_sink`, which is how a budget-aware client receives both.
8. `prompts.build_fetch_prompt` renders the request, the attribute block, and the context into one prompt.
9. The client is called. A `BudgetExceeded` raised at this point returns an empty result whose metadata names the limit that fired and whose outcome is `stopped_by_limit`. An error already in the hierarchy propagates unchanged. An unexpected ordinary exception is wrapped in `LlmSourceError` at the entry point, with the original attached as `__cause__`, so `except LlmSourceError` is sufficient for a caller. `BaseException` — a cancellation, a `KeyboardInterrupt` — is not caught and still propagates.
10. `parsing.parse_records` reads the response as a list of record objects, rejecting one that repeats a key inside a single object, and `aligner.align` maps returned keys onto the requested attributes and collects what it could not place, including any second key that reached an attribute another key had already answered.
11. `verifier.verify` coerces values to their declared types and returns the records alongside every value that would not coerce, which is recorded as `type_mismatches`. `verifier.drop_empty` then removes records that answered none of the requested attributes, using the per-record set of matched keys rather than the values, so an explicit null survives and an unanswered attribute does not. `verifier.dedupe` then removes exact duplicates.
12. The remaining records are offered to the budget's row axis. Anything over the ceiling is dropped, and the truncation point, the limit, and an outcome of `truncated` are recorded.
13. The run's accounting is folded into `ExecMeta`. Calls and cost come from the budget, which claimed them under a lock. Token counts and the model name come from the usage entries the client appended to this call's `usage_sink` list, and only those: an injected client may be reused across fetches, so its whole history would make every metadata field cumulative, and two concurrent fetches through one client can read the same cursor into that list and each slice both entries. A private per-call list removes the cursor rather than narrowing it. The budget is then asked whether any ceiling was crossed during the run, which is how a limit reached during settlement or by the deadline check reaches `stopped_by`.
14. The outcome is decided last, by `decide_outcome`, from the records that survived, what the model returned, whether the row cap fired, and `stopped_by`. Elapsed time is stamped and a `FetchResult` is returned.

### Estimate Path

1. Caller invokes `estimate(query, attrs, context, properties)`, or reaches the same code through the `LLM.ESTIMATE` op.
2. Instance properties are merged with any per-call properties, and the model name and the output allowance are read from the merge.
3. An empty request returns a bounded estimate of zero. It never reaches a model, so it cannot cost anything.
4. `checks` normalises the attributes and the context, and `prompts.build_fetch_prompt` renders the same prompt the fetch path would send. No provider is contacted. A client *is* resolved and a budget *is* built, because the bound comes from the client that would answer: it must declare `Capability.ESTIMATE` and expose `bound_for_attempt`, and one that does neither produces `bounded` false carrying a reason rather than a figure.
5. `pricing.bound_cost` bounds that prompt. A `ConfigError` from it becomes an `Estimate` with `bounded` false carrying the error's text as `reason`, rather than propagating, because asking what a request might cost is reasonable even for a model this package cannot price and the honest answer there is that it cannot say.
6. Otherwise the result is an `Estimate` carrying `bounded`, `max_cost`, `model`, `prompt_bytes`, `max_output_tokens`, `max_cost_per_attempt`, and `max_attempts`. `max_cost` bounds the whole fetch: it is the per-attempt figure accumulated over the permitted attempts, so a ceiling set to exactly `max_cost` admits every attempt the figure accounted for.

The figure is the same one the client reserves against for the same request, since both reach `bound_cost` with the same rendered prompt, the same model, and the same output allowance. That is the property worth protecting rather than an incidental consequence: an estimate a caller trusts and a ceiling the run enforces have to be one number, or a request that sizes inside a ceiling is refused when it runs.

### Source-Surface Path

1. Caller invokes `execute_query(query, database, collection, optional_properties)`.
2. If `query` is a mapping, its `op` key selects the operation. `LLM.MODELS` returns protocol and variant without a model call. `LLM.ESTIMATE` reads `query`, `attrs` or `attr_names`, and `context` from the mapping, runs the estimate path above with `optional_properties` as its properties, and returns the estimate as a one-record list, because a list of records is what this surface returns. `LLM.FETCH` reads `query`, `attrs` or `attr_names`, `context`, and `max_records` from the mapping, and an unrecognised op raises a `QueryError` naming the supported ones.
3. If `query` is a string, it is treated as the request itself.
4. Recognised keys are removed from `optional_properties` and become arguments; anything left over is passed through as per-call properties.
5. The synchronous `fetch` runs the fetch path above, and the records alone are returned. `database` and `collection` are accepted for signature compatibility and are not used, because a model has neither.

Step 4 is how a spending ceiling reaches a request made through this surface, and it is the route to reach for first. A `limits.*` key in `optional_properties` survives the extraction, is merged over the instance's properties for this call, and is never written back, so it bounds the request it arrived with and the next request through the same source is unbounded again. A caller builds one source and asks it many questions, each with its own tolerance, which is the shape that argument already has.

### Natural-Language Path

1. Caller invokes `execute_query_nl(query, database, collection, schema, llm_fn, optional_properties)`.
2. The same argument extraction applies as for `execute_query`, without op dispatch, since a natural-language request is always the request itself. `limits.*` keys reach the run the same way and bind this call alone.
3. `llm_fn` is passed through to client resolution. Wrapped in a `FunctionClient`, it becomes the model client for this run, which declares the rows axis alone, so a call, cost, or time ceiling handed in alongside it is refused.
4. Records alone are returned.

`translate_nl_to_native_query` exists as the documented override point for sources that need a different translation strategy. Here it returns the request unchanged, because natural language is this source's native form.

### Outcome Rule

`decide_outcome(records, model_returned, row_cap_fired, stopped_by)` is a single ordered rule, published on the source module so it can be read and tested without running a fetch. It replaced assignments spread through the fetch path, which kept producing combinations nobody chose: a ceiling crossed during settlement left an empty result reading `model_returned_none` while `stopped_by` named the limit, and a late rewrite that fired only when the outcome was still `ok` could not correct it.

The order is the precedence, and the first clause is the one that matters. Any `stopped_by` outranks what the run produced, because a caller deciding what to do next needs to know the answer was cut short before it can trust anything about its shape. Within that clause the row cap is the one limit that can leave a usable result behind, so it yields `truncated` when records remain and `stopped_by_limit` otherwise, as does every other axis. With no ceiling in play, records present is `ok`, nothing from the model is `model_returned_none`, and records that reduction removed is `filtered_to_empty`.

### Budget Path

1. `Limits` carries a ceiling per axis, where `None` means unlimited on that axis. Every field is validated on construction, so a wrong type, a `bool`, a negative value, or a non-finite value raises `ConfigError` rather than becoming a ceiling that is present and enforces nothing. The property names are a closed set for the same reason the `llm.*` names are, and an unknown `limits.*` key raises listing the accepted ones.
2. Before each provider attempt, the caller bounds what it can cost through `pricing.bound_cost` and calls `reserve(bound)`. The budget checks the call count, the elapsed time, and the committed cost plus that bound under a lock, raises `BudgetExceeded` naming the first limit that would be crossed, and otherwise claims one attempt and the bound together. It returns a `Reservation` carrying what it claimed. When no cost ceiling is configured anywhere up the chain, no bound is computed and the reservation is made at zero.
3. A failure after the provider was contacted calls `keep_unpriced`, which leaves the bound standing as the charge: the call happened, and treating an unreadable or failed call as free is how a ceiling comes to admit every remaining attempt while the running total stays where it started. Everything that can fail locally happens before the reservation instead, so such a failure is genuinely uncharged.
4. After a successful call, `settle(actual)` replaces the bound with the real cost and releases the difference. `settle(actual, cache_hit=True)` and `release()` give the whole claim back, including the attempt, for a call that never reached the provider.
5. After records are parsed, `add_rows` admits as many as the row ceiling allows and reports how many were kept.
6. When the run ends, `note_exhausted` records a ceiling that completed work has crossed, without raising, so a limit reached during settlement is reported rather than lost. Only a ceiling something actually crossed: spending the last allowed call refuses nothing, and reporting it would make an ordinary run under an exact limit look truncated.

A reservation is a handle rather than a bare number, and it resolves exactly once. That is the whole reason it exists: a free-standing counter decrement carries no evidence that the attempt it undoes never happened, which is how a retry escape gets rebuilt by whoever calls it next.

Reservation is per provider attempt, not per operation. A retry is an attempt and reserves again, so a run configured for two retries cannot make three times the calls its limit allows, and each attempt rechecks the deadline on the way through. The client separately caps the timeout it hands the provider at `remaining_seconds`, so a single slow call cannot run past a deadline the budget has already accepted.

A budget may also be derived. `derive(limits)` returns a child that spends from what its parent has left rather than receiving a fresh allowance, so the effective ceiling on every axis is the smaller of the child's and the parent's remaining headroom, and a child with no limits of its own is bounded entirely by its parent. Every claim and every release travels up the chain, and the child measures elapsed time from the parent's start, so a caller's time ceiling covers the whole unit of work rather than restarting with each fetch. Retry depth narrows the same way, through `effective_retries`, which was for a while the one axis read from the child alone and therefore the one axis a child could exceed its parent on. Without derivation a planner could hand ten fan-out leaves a full budget each.

What a child does not take from its parent is `stopped_by`. That field records what refused a particular attempt, and a child inheriting it would make a later affordable request under the same caller-owned budget report the earlier refusal as its own outcome.

The budget reaches whichever client answers as a per-call property rather than as an attribute on the client, because one client can serve several concurrent fetches and an attribute would let them overwrite each other's ceiling. `DefaultLlmClient` reads that property and claims against it, falling back to the budget it was constructed with, and declares every axis. `FunctionClient` is built by the source per fetch and claims one attempt priced at nothing, so that a reservation exists to resolve on every path out of the call, but it declares rows alone: a supplied callable does its own billing if it bills at all, and one invocation of it is not one provider call, so `limits.max_calls` is refused there alongside the cost and time ceilings rather than measured against something else and given the same name. A client injected at construction declares its own axes the same way, and each configured ceiling on an axis it did not name is refused, because one invocation of a client that cannot account for itself is not evidence of one provider call. The row axis is applied by the facade after the response is in hand and therefore binds whichever client answered.

### Client Call Path

1. Source properties and per-call properties are merged.
2. Call arguments are assembled: the prompt becomes a single user message, and each `llm.*` property is examined. An unrecognised name raises a `ConfigError` listing the accepted ones. Control names are consumed locally and never forwarded. Reserved transport names are stripped after the merge, so an override cannot smuggle one through. The model defaults to `gpt-4o` when none is configured.
3. A cache is selected. An explicit `cache_store` property wins; otherwise `llm.cache` enables a `diskcache` cache under `llm.cache_dir`, and a missing `diskcache` raises a `ConfigError` naming the extra that provides it. The key is the serialised call arguments.
4. On a cache hit the payload is returned without reserving anything. Latency is still recorded, and the usage record is marked as a cache hit with zero tokens and zero cost.
5. On a miss a per-event-loop semaphore bounds concurrency.
6. The retry loop begins. Each attempt caps the provider timeout at the time the budget has left, bounds those call arguments through `pricing.bound_cost` when a cost ceiling is configured, and reserves one attempt and that bound against the budget the run handed down, which is also where the deadline is rechecked. A configured `llm.timeout` is checked for being a finite positive number before it is capped, because a `NaN` survives the comparison that narrows it and would be handed to the provider as the timeout, and an integer too large to become a float raises a `ConfigError` naming the field rather than an `OverflowError` from inside the conversion. The call arguments are built and priced first, so anything that can fail locally fails uncharged. The bound is then checked against the budget *without claiming*, because importing the shipped adapter fetches a remote price map on first use and a request the budget was always going to refuse should not reach the network to be told so. The provider is imported next and the attempt claimed last, immediately before entry: a claim held across the import was visible to a sibling sharing the budget, which was refused on an allowance the final ledger said was free. A missing provider extra raises `ConfigError` naming it, uncharged, because nothing has been claimed at that point. Any provider exception becomes a `TransportError` carrying the original and keeps its reservation unpriced, because the provider was contacted. Retry depth comes from `Budget.effective_retries()`, as that many attempts plus one, so a derived budget cannot spend more attempts than its parent authorised, and a retry that cannot reserve raises `BudgetExceeded` naming the ceiling.
7. The response is materialised to a mapping and rejected unless it carries a `choices` list. Materialisation sits inside the same guard as the call, so a response that cannot be read resolves its reservation instead of leaving the claim outstanding while the retry takes another. The guard also covers the exits that are not ordinary exceptions, a cancelled task or a keyboard interrupt among them, since the provider was contacted either way and an unresolved claim on a caller-owned budget is headroom every later request loses. Cost is read from the provider response, and a cost that cannot be read, including a non-finite one, is reported as unknown rather than as zero, because converting an accounting failure into a free call is how a ceiling comes to admit every remaining attempt while the running total stays where it started.
8. A known cost settles the reservation, which releases whatever the bound overshot by. An unknown one resolves it at the bound, which then stands as the call's recorded cost as well. The cache stores the payload, usage is recorded on the client and appended to the call's `usage_sink` list when one was supplied, and the message content is extracted. A response with no choices, or a choice with no content, raises a `TransportError`.

---

## 1. Request Intake

Intake owns everything between the caller's arguments and the prompt text. It performs no I/O.

### checks

**Responsibilities**

- Strip a request and reject one that is not a string, naming the type that arrived. An empty or whitespace request is returned as empty rather than rejected, because it is an empty ask rather than a caller mistake, and the fetch path answers it with an outcome of `empty_query`.
- Normalise attributes from a list of names, a list of `Attr`, or a list of mappings with a `name` key and an optional `type`, rejecting anything else by index.
- Normalise context from a string or a list of strings into one block of text, discarding blank entries.
- Require a positive integer where a count is expected, rejecting booleans, which are integers in Python and are never a count.

A stricter `require_query` is also published, which rejects the empty request as well. The fetch path does not use it; it exists for a caller that wants an empty ask to be an error on its own side.

Every message names the offending value and the accepted form. This is written for the agent case: a caller reaching the library through a tool interface sees only the exception text as its tool result, and a message it can act on is the difference between a self-correcting caller and a stuck one.

### prompts

**Responsibilities**

- Hold every prompt the package sends, in one module.
- Render the attribute block, listing a bare name when no type was declared and `name: type` when one was.
- Build the fetch prompt from the request, the rendered attributes, and the context.

**Substitution**

Substitution is a single `str.format_map` over a fixed set of named fields. It is deliberately not a template engine, and it is deliberately not repeated. Caller text is inserted into the prompt and is never interpreted as a template, so a request or a context that happens to contain template syntax arrives at the model as written.

The prompt instructs the model to return a JSON array of objects with uniform keys and no commentary, to use exactly the specified attributes when any are given, to match declared types, to choose its own keys when none are given, and to treat context as clarification of what an attribute name means.

---

## 2. Model Boundary

### LlmClientProtocol

A structural protocol with `complete(prompt, properties)` and `acomplete(prompt, properties)`. Substitution is by shape, so a test double, a caller's own client, or a wrapped callable all satisfy it without inheriting anything. The shape is verified when the client is resolved. Each method is bound against the arguments this source passes, so a signature that cannot take `properties` positionally — a keyword-only or keyword-catchall form as well as a one-argument one — is refused with a `ConfigError` naming the contract rather than failing later with a raw `TypeError`.

### Capability and BudgetAwareClient

`Capability` names the axes a client can be held to: `calls`, `rows`, `seconds`, `cost`, and `estimate` — the last is not a ceiling but the declaration that the client can price an attempt, which `estimate` requires before it will give a figure — with `ALL` covering the set. `BudgetAwareClient` adds one method, `budget_capabilities() -> frozenset[str]`, to the two above, declared as a separate protocol because it answers a different question: not whether an object can be called as a model, but which ceilings this source may hold it to.

A method rather than an attribute, because satisfying the protocol should take a deliberate implementation. The version this replaced accepted any object carrying an attribute named `budget`, which a client with `budget = None` satisfied while making unbounded calls under a configured ceiling. Attribute presence is not evidence of behaviour.

Per axis rather than as one flag, because what a client can prove differs by axis. A client that drives the provider itself can prove how many calls it made, what each cost, and when to stop. A wrapper around a caller's function can prove that it invoked the function once and nothing more, since it cannot know whether that function contacts a provider, how many times, what it is billed, or how to interrupt it. A single flag would force such a client to claim all four or none, and it claimed all four: a cost ceiling against an `llm_fn` used to settle at zero and appear to hold.

The axes are also weighed against every ceiling in the budget chain rather than against this request's own limits, through `Budget.configured_axes()`. A caller that holds a budget with a cost ceiling and passes it to a fetch that configures nothing is still asking for that ceiling to hold, and reading the child alone let exactly that case through to a client which had declared it could not hold one.

### pricing

A genuine upper bound on what one call can cost, or a refusal to produce one, in its own module because a ceiling cannot be claimed without a number to claim and a provider's price is not known until it answers.

| Element | Value | Reason |
|---------|-------|--------|
| `FRAMING_TOKENS` | 64 | The role labels, separators, and per-message overhead a provider adds around the prompt. Generous, because it is added to a bound rather than to an estimate |
| `PRICES` | Published per-token input and output list prices, matched exactly or with a dated suffix | A dated or regional variant of a listed model resolves to its family, while a longer name that merely starts with a listed one does not |
| `rates_for(model)` | The listed rates, or `None` | A model absent from the table may be priced above everything in it, so there is no entry that can stand in for it |
| `resolve_rates(model, properties)` | Caller-supplied rates when either price key is present, the listed rates when neither is, `ConfigError` when nothing usable is available | Someone who knows what an unlisted model costs can bound it; both halves are required because a bound needs both, and presence rather than validity decides that an override was meant |
| `bound_cost(prompt, model, max_tokens, properties)` | `len(prompt.encode("utf-8")) + FRAMING_TOKENS` at the input rate, plus the whole `max_tokens` allowance at the output rate | Tokens never outnumber the bytes of the encoded prompt, so this cannot be exceeded by any byte-pair tokenizer; nothing observable predicts how much of the output allowance a model will use |
| `can_bound(model, max_tokens, properties)` | Whether a bound could be produced | Lets a caller ask before configuring a ceiling that would be refused |

The module either produces a figure that cannot be exceeded or raises `ConfigError`, and the refusal is the point. The premise it rests on is that a byte-pair tokenizer merges byte sequences, so every token it emits consumes at least one byte of the encoded prompt. Characters do not carry that property, although they read as though they do: a tokenizer splits a character whose encoding it has no merge for, so a composed emoji of seven code points can cost eleven tokens, and a 1,400-character sequence has been measured at 2,200. The bound is loose in the safe direction, since English runs three to four bytes per token, and looseness is the correct failure for a ceiling because the overshoot is released on settlement while an undershoot has already let the ceiling be crossed.

Two matching rules follow from the same reasoning. A model name resolves only when it equals a listed name or extends one with a hyphen and then digits and hyphens alone, because `gpt-4o-miniature-premium` is a different model from `gpt-4o-mini` and an open prefix match priced it as the cheaper one, as it priced `o1-pro` as `o1`. And a caller-supplied price is checked rather than merely consulted: if either `llm.price_per_input_token` or `llm.price_per_output_token` appears, both must be finite positive numbers or the call is refused, since falling back to the table on an unreadable override accepts an explicit instruction and then ignores it, which against a stale table produces a bound below the one that was asked for.

The same reasoning removed the fallback that priced an unlisted model at the table's dearest entry, since a model the table has never heard of may cost more than that, and a figure invented for it is not a bound either.

The consequence for a caller is that the floor of the bound is the whole `max_tokens` allowance at the model's output rate. With the shipped defaults of `gpt-4o` and 4096 output tokens that floor is about $0.041, so a smaller `limits.max_cost` refuses every call before one is made, and the remedy is a smaller `llm.max_tokens` rather than a larger ceiling.

`LlmSource.estimate` publishes this module's answer for a request the caller has in hand, which is how that floor stops being a surprise. It reaches `bound_cost` with the prompt the fetch path would render, so the figure it reports and the figure a run reserves against are the same number, and a caller can size a ceiling against the request it is going to bound rather than against an example. Where `bound_cost` raises, `estimate` reports the refusal as an unbounded result carrying the reason instead of propagating it, since a caller asking what something might cost is entitled to an answer of "not knowable here".

### DefaultLlmClient

**Responsibilities**

- Translate properties into provider call arguments, rejecting unknown overrides.
- Consult and populate an optional cache.
- Reserve budget around each provider attempt, and resolve the reservation afterwards by settling it against the real cost or keeping it at the bound.
- Bound concurrency per event loop.
- Retry transport failures and validate the response shape.
- Record per-call usage covering model, prompt and completion tokens, cost, latency, and cache-hit state, on its own history and on the `usage_sink` list the call carried.
- Declare `Capability.ALL`, since it makes the provider call and can therefore enforce every axis.

**Accepted overrides**

| Property | Forwarded to provider | Meaning |
|----------|----------------------|---------|
| `llm.model` | yes | Model name; defaults to `gpt-4o` |
| `llm.temperature` | yes | Sampling temperature |
| `llm.max_tokens` | yes | Response length ceiling |
| `llm.stream` | yes | Streaming flag, forwarded as `False`; `True` is refused and deferred to v1.1 |
| `llm.timeout` | yes | Per-call timeout, checked as a finite positive number and then capped at the time the budget has left |
| `llm.cache` | no | Enables the `diskcache` response cache |
| `llm.cache_dir` | no | Directory for that cache; defaults to `.llm_cache` |
| `llm.max_parallel` | no | Concurrency bound per event loop; defaults to 8 |
| `llm.rate_limit` | — | Not supported in v1.0; refused with `ConfigError` and deferred to v1.1 |
| `llm.price_per_input_token` | no | Input rate for a model `pricing.PRICES` does not carry |
| `llm.price_per_output_token` | no | Output rate for the same model |

The set is closed. An `llm.*` name outside it raises rather than passing through, because a setting that is accepted and ignored produces a run whose recorded configuration does not describe what happened. Transport-shaped names are stripped after every merge, so no override reaches the provider under a name the client did not choose to send.

**Cache selection**

A `cache_store` property wins if one is present, and the client drives it with `get` and `set` alone. Otherwise `llm.cache` opens a `diskcache` cache, held per directory rather than per client, so a per-call `llm.cache_dir` override gets its own cache instead of reusing whichever was opened first. The key is the serialised call arguments in both cases, which means it covers the model, the prompt, and every forwarded parameter.

### Cache stores

Two stores ship in `cache/`, both implementing the `get`/`set` protocol and both built on the standard library, so neither needs the provider extra and neither needs a server.

`MemoryCache` is in-process and bounded, evicting least-recently-used entries past `max_entries`. Bounded rather than unbounded because the values are provider responses, and a long-running process asking many distinct questions would otherwise grow without limit.

`FileCache` writes one JSON file per entry into a directory, naming each file by the SHA-256 of its key because cache keys are long serialised mappings and are not path-safe. Writes go to a temporary file and are moved into place, so a reader never sees a half-written entry, and an entry that cannot be read or written is treated as a miss rather than raised.

Both are reached through `cache_store`. The `llm.cache` switch still opens `diskcache` and does not use either of them; treat the two paths as unconnected until that is reconciled in code.

### FunctionClient

Wraps a plain `(prompt: str) -> str` callable, awaiting the result when the callable returns a coroutine. This is what makes the source surface's `llm_fn` parameter meaningful for this source, and it is the simplest way to drive the library from a test with a scripted response. It holds the run's budget and claims one attempt against it per call, priced at nothing, since a supplied callable does its own billing if it bills at all; the claim exists so that there is a reservation to resolve on every path out of the call, including a cancellation, rather than because the attempt has been measured.

It declares rows and nothing else, so a `limits.max_calls`, `limits.max_cost`, or `limits.max_seconds` against an `llm_fn` is refused at the first fetch. Calls were declared here at first, on the reasoning that one invocation is one call. That is the same guess as reporting the cost at zero: a callable may answer from memory, contact one provider, or fan out to several, and none of it is visible from here, while `limits.max_calls` counts provider entries everywhere else. A caller that needs any of the three over its own model call writes a client that declares those axes and claims against the budget it is handed.

### Budget, Limits, and Reservation

**Responsibilities**

- Hold the configured ceiling on each axis, and refuse a ceiling that is malformed rather than accepting one that cannot enforce anything.
- Refuse the attempt that would cross one, naming the axis in the raised error.
- Track claimed attempts, outstanding reservations, settled cost, elapsed time, and rows, and record which limit stopped a run.
- Derive a child that spends from what this budget has left.

**Validation on construction**

Each `Limits` field is checked as the object is built. `max_rows`, `max_calls`, and `max_retries` must be whole numbers and not booleans; `max_seconds` and `max_cost` must be real, finite numbers; none may be negative. A violation raises `ConfigError` naming the field and the value. The non-finite check earns its place: a `NaN` ceiling compares false against every spend, so it reads as configured and enforces nothing, which is the exact failure this component exists to prevent. A non-finite provider cost is treated the same way one level down, as unreadable rather than as zero.

**Enforcement order within `reserve`**

1. Every parent, outermost first, against the same three ceilings.
2. Call count, against `max_calls`.
3. Elapsed time, against `max_seconds`.
4. Settled cost plus outstanding reservations plus this attempt's bound, against `max_cost`.

The checks and the claim that follows happen under one lock shared by the whole family, so two budgets in a chain cannot both be admitted against the same headroom. Claiming the bound rather than only checking the total is what closes the same race on the cost axis: ten concurrent leaves each commit a bound before spending, so they cannot collectively overshoot by ten calls' worth while each sees room for one. The cost comparison is strict, so an attempt that would land exactly on the ceiling is admitted. `reserve` is invoked once per provider attempt, so step 3 is also the deadline recheck between retries. Rows are handled separately by `add_rows`, which returns how many of the offered records fit under `max_rows` and marks the run as stopped by the row limit when it had to drop any.

**Resolving a reservation**

| Method | Effect | Used for |
|--------|--------|----------|
| `settle(actual)` | Replaces the bound with the real cost, releasing the overshoot | A call that returned and whose cost could be read |
| `settle(actual, cache_hit=True)` | Releases the bound and the attempt | An answer that never reached the provider |
| `keep_unpriced()` | Leaves the bound standing as the charge | A call that reached the provider and failed, one whose cost could not be read, or one abandoned by a cancellation or an interrupt |
| `release()` | Gives the bound and the attempt back | An attempt abandoned before the provider entry point was invoked |

A reservation resolves exactly once and raises if resolved twice. `release` is correct only before the provider entry point is invoked; once it is, the attempt counts whatever it returns, or every failed retry is free. Every path out of an attempt resolves its reservation, which is why the client's guard covers materialising the response as well as making the call: a claim left outstanding is headroom the budget never grants again, and the retry that follows takes more of it. The guard reaches beyond the ordinary exception hierarchy for the same reason. A cancelled task, a `KeyboardInterrupt`, and a `SystemExit` each leave an attempt that reached the provider, and leaving that claim outstanding poisons a caller-owned budget for every request after it, so the estimate stands and the exit is re-raised unchanged.

**Observation**

`remaining_seconds` reports the time left, and the client uses it to cap the timeout it passes the provider. Without that, a call started just inside a deadline could run for its own full timeout past it, and the ceiling would hold for the decision to call and not for the call. In `acomplete` the same figure also bounds the await itself, which is what holds when a provider ignores the timeout it was handed; it cannot bound work that never yields, and [ADR-024](13-decision-log.md#adr-024--the-time-ceiling-bounds-waiting-not-the-whole-run) lists what that leaves out. `remaining_calls` and `remaining_cost` report the same headroom on their axes, `effective_retries` reports the retry depth, `committed` reports settled spend plus outstanding reservations, and each of them narrows to the tightest value along the parent chain, so a caller reading one on a derived budget sees what it can actually spend rather than what it was nominally given.

`configured_axes` answers a different question and walks the chain the other way: which axes carry a ceiling anywhere from this budget to the root, whatever its value. That is what the capability refusal reads, because a ceiling set on a parent is still a ceiling the run has to honour and the client that has to honour it is the one answering this request.

`check_deadline` and `rows_exhausted` are published for callers and are not reached from within this release, which makes one call per request and rechecks the deadline inside `reserve`. `note_exhausted` is reached: the facade calls it once the run is over, so a ceiling that completed work crossed is reported without being raised.

---

## 3. Reduction

Three modules of plain functions. None of them calls a model, holds state, or imports another layer.

### parsing

**Responsibilities**

- Remove one surrounding code fence when present.
- Read the body as JSON and produce a list of record mappings.
- Raise a parse error carrying the raw text when the response cannot be read as records.

**Fence handling**

The fence is matched as a fence: optional whitespace, three backticks, an optional language tag, the body, and a closing fence. Matching the fence rather than searching for a language name means a data value containing that name survives intact.

**Accepted shapes**

| Response shape | Treatment |
|----------------|-----------|
| JSON array of objects | Used as-is |
| Single JSON object | Wrapped as a one-record list |
| Object whose only key is `items`, `rows`, `data`, or `results`, holding an array | Unwrapped to that array |
| The same key beside others, holding an array | Whichever reading answers more of the requested attributes; the record itself when nothing was requested |
| Object repeating a key, at any depth | `ParseError`, with the raw text attached |
| Anything else | `ParseError`, with the raw text attached |

Accepting a bare object and the common wrapper keys costs nothing and avoids failing a correct answer over a formatting detail. A `ParseError` carries the raw response because a caller debugging one needs to see it, and re-running the request is not guaranteed to reproduce it. `NaN`, `Infinity`, and `-Infinity` are rejected, because Python's decoder accepts them by default, they are not JSON, and a record carrying one fails later somewhere less obvious than here. A number written out in full rather than as one of those words — `1e309` — is rejected on the same grounds and needs its own hook to catch: it is an ordinary JSON literal that overflows to infinity in the float conversion, so the constant hook never sees it, and it would be written back out as `Infinity` by a caller re-serialising the result.

**A repeated key**

An object that carries the same key twice is rejected, and the check runs at every nesting depth rather than on record keys alone, since alignment only ever sees record-level keys and nesting would otherwise be a way past it. Python's decoder keeps the last of such a pair and leaves no trace that the first existed, which resolves the ambiguity the opposite way from the rest of this pipeline and does it silently. Rejecting is the honest answer, because which of two values for one key the model meant is not knowable here and keeping either is a guess the caller never sees.

This is a different case from two keys that merely normalise alike, and the two are answered differently on purpose. `First Name` beside `first_name` leaves two distinguishable keys, so alignment can keep the first, report the second in `ExecMeta.key_collisions` with the value it carried, and hand the caller enough to decide. One key repeated leaves nothing to tell the values apart by and nothing to name in a report.

### aligner

**Responsibilities**

- Collect every key that appeared across the returned records, in order of first appearance.
- Map each key onto a requested attribute, or record that it could not be mapped.
- Produce records carrying exactly the requested attributes, with `None` where a value was absent.
- Record which attributes each record actually answered, and report the keys whose values were not used because another key had already answered the same attribute.

**Match order**

1. Exact name match.
2. Case-insensitive match.
3. Normalised match, comparing names with case and every non-alphanumeric character removed.

With no requested attributes there is nothing to align to, so the records pass through unchanged and only the observed keys are reported. A key matched under a different spelling is recorded as a remapping, and a key that matched nothing is recorded as unmapped. Neither is discarded quietly, because a caller that asked for `iata_code` and received `IATA` should learn that from the result rather than from an empty column.

**Collisions**

The normalised form also unifies the Unicode spellings of one name, because a letter with an accent can arrive as a single character or as a letter followed by a combining mark. The two render identically, but a combining mark is not a word character, so without that step the stripping that removes separators deleted it and the two spellings normalised apart — an attribute the model had answered came back unmapped, depending on an encoding choice no one can see. Accents are not folded away, since they distinguish names in many languages.

Match rules that ignore case and separators can send two returned keys to the same attribute, which is the price of recovering `iataCode` for a requested `iata_code`. The first key to arrive keeps the attribute and the second is reported as a collision carrying its record index, the attribute, the key that was kept, the key that was ignored, and the value that key held. Overwriting made the surviving value depend on the order the model happened to emit its keys, which is not a property a caller can reason about, and which of two spellings the model meant is not knowable here. Reporting the ignored value is what keeps the choice recoverable by a caller that can decide.

Two *requested* attributes whose fuzzy forms coincide are a different case and are not refused: the fuzzy index drops the entry it cannot separate, so exact keys still match and a key that matches only fuzzily is reported unmapped rather than assigned by guess. Only the same name requested twice raises `QueryError`. There the ambiguity is in the request rather than in the answer, so no returned value could be assigned to either attribute without guessing, and the caller is the only party able to resolve it.

### verifier

**Responsibilities**

- Coerce values to their declared types where the coercion is unambiguous.
- Leave values that will not coerce as they arrived, and report each one.
- Drop records that answered none of the requested attributes, from the recorded set of matched attributes rather than from the values.
- Remove exact duplicates, optionally keyed on one attribute.

**Coercions**

| Declared type | Accepted input | Result |
|---------------|----------------|--------|
| `int`, `integer` | integer, whole float, digit string with optional thousands separators | integer |
| `float`, `number` | number, numeric string with optional thousands separators | float |

Only a genuine thousands grouping is stripped: three-digit groups, optionally signed, optionally with a dot decimal part. A blind strip made the decimal comma a hundredfold or thousandfold error reported as `outcome=ok`.
| `bool`, `boolean` | boolean, or one of `true`/`yes`/`y`/`1` and `false`/`no`/`n`/`0` in any case | boolean |
| `date` | string in `YYYY-MM-DD`, `YYYY/MM/DD`, `DD-MM-YYYY`, or `Month D, YYYY` | ISO date string |

A value that fits none of these is returned untouched. Verification reports rather than discards: dropping a record because one field arrived in an unexpected format loses data the caller asked for. Attributes with no declared type are not touched at all, and a request with no typed attributes skips the pass entirely.

`verify` returns two things, the records and a list of the values that did not fit, each carrying the record index, the attribute, the declared type, and the value as it arrived. That list becomes `ExecMeta.type_mismatches`. Keeping such a value silently would tell the caller its records are typed when one of them is not, which is the same silent loss the aligner exists to prevent one level up.

Deduplication compares whole records by default, using a stable representation of their sorted key-value pairs, and compares a single attribute when a key is named.
