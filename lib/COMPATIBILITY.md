# Compatibility

This guide describes `LlmSource` integration with `blue-data` and migration from the earlier LLM source.

Supported Python versions are 3.12 and 3.13 (`>=3.12,<3.14`). [Installation](../docs/installation.md#requirements) lists the versions the 1.0 release was tested with.

## Integration with blue-data

**The source inherits from `DataSource`.** `LlmSource` extends `blue_data.source.source.DataSource` directly. `blue-data>=1.0.0,<2` and `tenacity>=9.0.0` are core dependencies; the data layer is required at install time.

Base-class order determines which implementations resolve. `DataSource` is a plain class, not an abstract one. Most methods return an empty result, so an unoverridden method can silently answer nothing: `fetch_databases` would return an empty list and `execute_query` would return no records without contacting a model. `LlmSource` uses `DataSource` as its base, so its implementations come first in the method resolution order. A test asserts that every method on the shared surface resolves to this class.

**Registering an already-constructed instance is the supported route**: `register_source` stores the object under its name and records its class alongside `name` and `properties`. `connect_source` returns that live instance while it exists, then reconstructs a new source from the recorded class and properties. The registration therefore outlives the original object. Client-only configuration is not part of that record; see the configured-ceiling rules below. Dispatch by protocol name is a legacy path for older records and is not how this source is reached; inheriting the base class does not add a protocol arm.

Protocol-driven construction requires an upstream addition.

`blue_data_llm_source.compat.blue_data.BlueDataLlmSource` is an alias for `LlmSource`, retained for imports written against the earlier optional adapter. It is not a subclass: the core now carries the inheritance, so a separate class would add no behavior and introduce another method resolution order. New code should import `LlmSource`.

Shared `optional_properties["commit"]` is accepted and ignored. `read_only` on `execute_query_nl`, `execute_nl_tool` and `execute_query_nl_tools` never enables writes, and the latter still refuses the tool-driven loop (including `max_sample_size`). Creation methods accept `overwrite=False` and return `{}` for either value. The virtual entity's granular attributes match its bulk `properties`; relation metadata/attributes remain `{}` and statistics remain `None`. Advertised `get_sample` forwards `sample_size` to keyword-only `fetch_entity_stats(..., row_sample_size=10)` and returns `None` without provider contact or stored samples.

`LLM.FETCH` is a read operation because it does not change source data. It can send the query to the configured provider and incur charges. Configure `limits.max_cost` or an operator ceiling to bound provider spend independently of read-only dispatch.

Synchronous query methods called inside a running event loop wait for an owned worker thread, blocking the calling thread and delaying its cancellation callbacks. Prefer `afetch` for concurrent work and for clients tied to the caller's loop. When the waiting thread receives `KeyboardInterrupt`, the bridge cancels the in-flight fetch and re-raises once it stops, or after five seconds if the client ignores cancellation. Under `asyncio.run`, the first Ctrl-C requests cancellation of the runner's main task. When the synchronous call runs in that main task, the bridge observes its cancellation request and cancels the fetch. A call in another task observes cancellation only if that task receives the request. In a plain event-loop callback there is no current task to observe: the fetch may finish before the first interrupt is raised by `asyncio.run`. A second Ctrl-C interrupts the waiting thread; use a task running `afetch` to avoid blocking the loop. Time limits and Ctrl-C cancel cooperatively, so a client that ignores cancellation cannot be forcibly stopped and may finish in the background. A normal interpreter shutdown waits for that worker to finish.

**Dependency impact.** `blue-data` installs `pandas`, `numpy`, and `jsonpath-ng`, although this package does not use them directly. It does not introduce a dependency on the orchestration layer: the package imports neither `blue` nor `blue_data_ops`, and a test enforces that boundary.

**Errors are raised, not returned.** This library raises typed errors from the `LlmSourceError` hierarchy. Callers that need an error value at an operator boundary can convert the raised error. Use a `try`/`except` at the call site to convert a raised error to a value.

A missing or invalid provider credential fails the provider request, not configuration validation: the provider SDK reads and validates the credential. With no answered extraction response and no retained records, a failed attempt produces `TransportError` (CLI exit 5) if the retry policy is exhausted or `max_calls` prevents the next retry. If `max_cost` or `max_seconds` refuses that retry, or a ceiling refuses the first attempt, the fetch returns `stopped_by_limit` (CLI exit 7). If an extraction response has already been parsed, the fetch keeps its records with `outcome=partial` and a gap for the failed part. `ConfigError` covers settings checked by this package: unknown overrides, invalid or unknown ceilings, ceilings the resolved client cannot enforce, and missing optional extras.

## Default client logging

`DefaultLlmClient` loads LiteLLM lazily. Before importing it, the client raises the effective logging threshold of `LiteLLM`, `LiteLLM Router` and `LiteLLM Proxy` to at least WARNING, including when the host enabled INFO or DEBUG. Stricter levels and existing handlers are preserved. LiteLLM INFO/DEBUG diagnostics are discarded, and its provider-error help banner is suppressed.

Keep `litellm.set_verbose` and LiteLLM's debug switch (`turn_on_debug()` or `_turn_on_debug()`, depending on the version) disabled in stdio hosts. Output emitted by a host's earlier LiteLLM import cannot be suppressed retroactively. Explicit provider debugging and host handlers that write to stdout can still corrupt the protocol. The library does not redirect process-wide stdout; stdio hosts should guard embedded sources at the transport boundary.

## Deviations from the earlier implementation

The earlier implementation is the LLM source as it stood before this package was split out of the combined codebase. A caller written against it is the caller this section is for.

**The protocol is `llm`, not `openai`.** The protocol uses variant `openai`, matching how `sql` covers both `postgres` and `mysql`. The earlier implementation registered under `openai`, which names a vendor where a family was specified. A registry entry pointing at the old protocol string needs updating.

**A `variant` property exists.** It defaults to `openai` and is settable. There was no such property before.

**Code fences are stripped by matching the fence.** The earlier implementation removed every occurrence of the substring `json` from the response, which corrupts any returned value containing that substring. This library matches an actual surrounding fence and leaves the content alone.

**Template substitution is single-pass and does not interpret user text.** The earlier implementation applied `Template.safe_substitute` and then Jinja2, repeatedly, until the string stopped changing, so a query containing `${...}` or `{{...}}` was expanded as a template. Here the prompt has a fixed set of named fields and user text is inserted as a value, never re-read.

**Calls can be bounded.** The earlier implementation had no timeout, retry policy, or ceilings. This library retries transport failures with exponential backoff and supports four optional limits: rows, time, cost, and call count. The time limit bounds admission and waiting rather than the whole run; [what it bounds](README.md#what-the-time-ceiling-actually-bounds) is stated exactly. Configured call and cost ceilings are checked before each provider attempt, elapsed time is checked at admission and during cooperative waits, and rows are capped once records are parsed. When limits stop a run, `meta.stop_reasons` retains every observed axis and `meta.stopped_by` keeps the highest-precedence one. Records parsed before a late failure are retained when at least one extraction response was answered. Every `limits.*` name is checked against a closed set and every value against its type, so a misspelled or malformed ceiling raises `ConfigError` rather than being accepted and ignored.

**The cost claim is an upper bound against the package or configured rates, or the ceiling is refused.** A call's price is not known until it returns, so `limits.max_cost` is claimed against the prompt's length in UTF-8 bytes plus a framing allowance at the model's published input rate, plus the whole `llm.max_tokens` allowance at its output rate, raised for the cache-write, long-context and regional-processing charges a model in the [model table](README.md#supported-models) carries. Every contacted attempt keeps at least that claim under a cost ceiling; a higher readable calculator figure or usage bound raises the charge. The result reports accounting as unknown when the reservation or usage bound determines the charge, or the calculator cannot prove every billed category. Stale rates or inaccurate provider usage can still leave the ledger below the invoice. [The cost bound](README.md#the-cost-bound) explains the calculation. Counting bytes rather than dividing by an average is what makes it a bound: a byte-pair tokenizer merges byte sequences, so every token it emits consumes at least one byte of the encoded prompt, while an average is exceeded by dense text. Counting characters would not have been a bound even though it reads like one, since a tokenizer splits a character whose encoding it has no merge for. Where no bound can be established the ceiling raises `ConfigError` instead of resting on a guess: when the model carries no published price and none was supplied, and when `llm.max_tokens` is not a finite positive number.

The minimum cost bound is the full output allowance and does not depend on the prompt. With the default `gpt-6-luna` model and 25,000 output tokens, the output-only floor is about $0.020625, including the long-context output multiplier and 10% regional uplift. A lower `limits.max_cost` refuses the call before provider contact. Reduce `llm.max_tokens` if the answer needs fewer tokens.

**A caller can estimate a request before running it.** `LlmSource.estimate(query, attrs, context, properties)` builds the prompt, applies the same bound as a reservation, and returns an `Estimate` without contacting a provider. A request estimated within a ceiling uses the same bound at runtime. The `LLM.ESTIMATE` query operation exposes the result as a one-record list for registry callers. When the package cannot price a model, the result has `bounded=False` and explains why.

**A client declares which ceilings it can enforce, per axis.** `types.BudgetAwareClient` requires one method, `budget_capabilities() -> frozenset[str]`, returning names from `types.Capability`: `CALLS`, `ROWS`, `SECONDS`, `COST`, `ESTIMATE`, or `ALL`, which is all five. **`ESTIMATE` is not a ceiling and `ALL` includes it**, so declaring `ALL` commits a client to two further requirements: `bound_for_attempt(prompt, properties) -> float` must return a true upper bound on one provider attempt, and one call must make no more than `budget.effective_retries() + 1` provider attempts. `estimate` accumulates the per-attempt figure once per permitted attempt. A client whose retry or fallback policy ignores that allowance can therefore exceed the estimate. Such a client should omit `ESTIMATE`; the method then reports `bounded=False` with a reason. A client that declares an axis receives the run's budget under the `budget` property on every call and must reserve against it per provider attempt and resolve each reservation. A ceiling on an undeclared axis raises `ConfigError` on the first fetch, preventing callers from relying on an unenforced limit.

The method requires a client to declare its capabilities explicitly; an attribute named `budget` alone does not establish that it enforces ceilings. `DefaultLlmClient` declares `Capability.ALL` because it makes the provider call itself. `FunctionClient`, which wraps a callable passed as `llm_fn`, declares rows alone: one invocation may answer from memory, contact a provider once, or fan out to several. `limits.max_calls`, `limits.max_cost`, and `limits.max_seconds` over an `llm_fn` are therefore refused.

Anyone porting a caller that injects its own client should implement `budget_capabilities`, claim against the budget it receives, and append this call's `Usage` to the list arriving under the `usage_sink` property, which is where the fetch reads its token counts and model name from. A client that declares nothing still works; it is refused the three provider-level ceilings, and `limits.max_rows` needs nothing from it.

**A ceiling is an input to the query.** `execute_query` and `execute_query_nl` both take `optional_properties`. Per-call properties apply only to that request and are not written back. Ordinary properties override the instance's values; `limits.*` and `max_records` can only tighten the configured ceilings, and `None` leaves those ceilings unchanged; `limits.max_retries=None` is invalid. This lets a caller bound a request through the existing query arguments.

The registry stores the source's own properties, not its injected client or the client's settings. Put ceilings, model and rates on the source when they must survive registration and reconstruction; a rebuilt source starts with the default client.

Client properties set the initial configuration, then source properties override them. The resulting configured ceiling combines by minimum with any per-call ceiling. Call `None` adds no cap and cannot clear an explicit source ceiling; source `None` can override a client ceiling through property precedence. Zero remains a cap. Invalid configured or call values fail even when another limit is tighter. Explicit retry caps also bind; `limits.max_retries=None` is invalid. The retry default applies only when no layer declares one. `max_records` must be a positive integer and is clamped by the effective row ceiling.

A configured cost ceiling is bounded only against published prices or the rates configured with it. While the client or source sets a non-`None` `limits.max_cost`, a per-call `llm.price_per_input_token` or `llm.price_per_output_token`, even `None`, raises `ConfigError` before provider contact. A per-call `llm.model` other than the configured one does not inherit the configured rates: a model with published prices is bounded at them, and one without is refused with `ConfigError`. Without a configured cost ceiling, rates supplied per call keep working and remain the caller's own assertion. Numeric cost limits do not authenticate configured prices or opaque injected clients.

**A caller can also own the budget.** Limits configured on the source, or handed to one call through `optional_properties`, bound that fetch. `fetch` and `afetch` additionally accept `budget=`, a `Budget` the caller holds, from which each fetch derives a child that spends against what the caller has left. That is the second route, and it is the one to use when the ceiling is a total across several requests rather than a bound on any one of them.

**One prompt, in one module.** The fetch prompt is in `prompts/`.

**Requests follow the model's published contract.** The earlier implementation sent every model the same arguments. This library sends each model in its table the reasoning effort, sampling and output-token parameter OpenAI documents for it, refuses a combination the model rules out before anything is reserved, and names OpenAI as the provider for a bare OpenAI model name so a model newer than LiteLLM's bundled price map still routes. The [supported models](README.md#supported-models) list each model's contract.

**Unknown configuration keys are rejected.** An unrecognized `llm.*` key raises `ConfigError` naming the accepted set. The value of failing loudly here is specific: an accepted-and-ignored setting means recorded configuration no longer describes what ran.

**Keys that collide are reported, not resolved.** The earlier implementation selected requested columns and let a later key overwrite an earlier one. Here the first key to answer a requested attribute keeps it, and the second is reported in `meta.key_collisions` with the value it carried. JSON key order determines which value survives, and the source cannot determine which spelling the model intended. Distinct requested names whose fuzzy forms coincide are accepted: the fuzzy index drops the ambiguous entry, exact returned keys still match, and a key that only matches fuzzily is reported unmapped. Two occurrences of the *identical* key inside one object raise `ParseError`, at any nesting depth: Python's decoder would otherwise keep the last value without a trace of the first. An exact duplicate in the requested attribute list raises `QueryError`.

**An empty result carries one outcome.** Partial interruption with an answered response reports `partial`, while simultaneous caps remain in `stop_reasons`. Otherwise a non-row stop reports `stopped_by_limit`; a row-only cap reports `truncated` when records remain and `stopped_by_limit` when none do. With no stop, records report `ok`, an answered empty response reports `model_returned_none`, and records removed by reduction report `filtered_to_empty`.

**Non-flat results report `row_estimate` as an uncertain root planning hint.** Zero is retained;
unknown, flat, and rejected bootstrap results use `None`. The field is not a returned-row count or a
completeness estimate, and a valid empty answer alone does not create a gap.

**Planning is bounded, and rule-backed distribution search is a direct-Python opt-in.** Python,
CLI, and MCP share 21 standard planning controls. Python source and per-call properties additionally
accept `plan.max_excluded_keys` as a count bound on continuation exclusions. They also accept
`plan.distribution_search`, `plan.identity_partitions`, and `plan.max_strategy_candidates`.
Distribution search requires partitioned mode, explicit ordered keys, and declared finite-domain
rules that are validated before provider contact and removed from provider properties. Per-call
rules replace source rules. `fetch` and `afetch` return a distribution-search result that may expose
a versioned planning receipt without changing the two-field `FetchResult` dataclass shape. The
shared `execute_query` methods can receive the same properties but return rows only, as their base
contract requires. CLI and MCP intentionally omit distribution search and the receipt.

Non-flat planning here is an adaptation to the Blue source contract, not a port: nothing in this package wraps an external planning API, and no result is promised to match one produced elsewhere. The receipt checks consistency among rules, planning facts and the serialized plan; it does not verify answer accuracy or completeness.

**The virtual entity is not vendor-named.** The earlier implementation synthesized `openai_entity` and `openai_entity_attribute`. This library uses `llm_entity` and `llm_entity_attribute`, consistent with the protocol being the family.

**A source instance is reusable.** It holds no connection and constructs a client per fetch.

## Behavior kept deliberately

Both attribute forms work: a plain list of names and a list of `{name, type}` dicts. Types are used for verification when present.

`context` is free text that may clarify what an attribute name means, matching the documented use rather than only carrying background information.

An empty or whitespace query never reaches the model. An empty list comes back rather than an error, and this library additionally reports `outcome: empty_query` in the result metadata, so a caller can tell a skipped request apart from a model that found nothing.

The virtual hierarchy of one database, one collection, and one entity with one text attribute is preserved so a registry sync has something to walk.

## Not carried over

The `nl2llm` operator and the `NL2LLM` agent are not part of this library. Per the layer's own design, natural-language querying of a source is provided by a per-source `NL2Q*` operator, which belongs with the operator library. This package is the source that such an operator calls.
