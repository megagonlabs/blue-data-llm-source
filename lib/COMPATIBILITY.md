# Compatibility

This document lists differences from the Blue data-source specification and from the earlier LLM-source implementation. It is intended for callers migrating to this package.

## Relationship to the data-source specification

The Blue team's internal data-source design document is not included with this package. The relevant differences are described below.

**The source inherits from `DataSource`, as the document specifies.** `LlmSource` extends `blue_data.source.source.DataSource` directly, and `blue-data>=0.1.0` is a declared core dependency alongside `tenacity>=9.0.0`. The `[blue-data]` extra that used to gate the base class is gone. An earlier version of this package declared no dependency on the data layer and matched the query surface by shape instead; anything you have read describing this package as standalone, or the data layer as optional to it, belongs to that version and is no longer true.

Base-class order determines which implementations resolve. `DataSource` is a plain class, not an abstract one. Most methods return an empty result, so an unoverridden method can silently answer nothing: `fetch_databases` would return an empty list and `execute_query` would return no records without contacting a model. `LlmSource` uses `DataSource` as its base, so its implementations come first in the method resolution order. A test asserts that every method on the shared surface resolves to this class.

**Registering an already-constructed instance is the supported route**: `register_source` stores the object under its name and records its class alongside `name` and `properties`. `connect_source` returns that live instance while it exists, then reconstructs an equivalent source from the recorded class. The registration therefore outlives the original object. Dispatch by protocol name is a legacy path for older records and is not how this source is reached; inheriting the base class does not add a protocol arm.

The supported integration is to build the source and pass the instance to `register_source`. Protocol-driven construction requires an upstream addition.

`blue_data_llm_source.compat.blue_data.BlueDataLlmSource` is an alias for `LlmSource`, retained for imports written against the earlier optional adapter. It is not a subclass: the core now carries the inheritance, so a separate class would add no behaviour and introduce another method resolution order. New code should import `LlmSource`.

**Dependency impact.** `blue-data` installs `pandas`, `numpy`, and `jsonpath-ng`, although this package does not use them directly. It does not introduce a dependency on the orchestration layer: the package imports neither `blue` nor `blue_data_ops`, and a test enforces that boundary.

**Errors are raised, not returned.** The document says that on an exception the source should return only the exception. This library raises a typed error from the `LlmSourceError` hierarchy. Callers that need an error value at an operator boundary can convert the raised error. Anyone relying on the documented return-the-exception behaviour needs a `try`/`except` at the call site.

A missing or invalid provider credential is reported as `TransportError`, not `ConfigError`, because the provider SDK reads and validates the credential. The CLI maps this failure to exit code 5. `ConfigError` covers settings checked by this package: unknown overrides, invalid or unknown ceilings, ceilings the resolved client cannot enforce, and missing optional extras.

## Deviations from the earlier implementation

The earlier implementation is the LLM source as it stood before this package was split out of the combined codebase. A caller written against it is the caller this section is for.

**The protocol is `llm`, not `openai`.** The design document specifies protocol `llm` with variant `openai`, matching how `sql` covers both `postgres` and `mysql`. The earlier implementation registered under `openai`, which names a vendor where a family was specified. A registry entry pointing at the old protocol string needs updating.

**A `variant` property exists.** It defaults to `openai` and is settable. There was no such property before.

**Code fences are stripped by matching the fence.** The earlier implementation removed every occurrence of the substring `json` from the response, which corrupts any returned value containing that substring. This library matches an actual surrounding fence and leaves the content alone.

**Template substitution is single-pass and does not interpret user text.** The earlier implementation applied `Template.safe_substitute` and then Jinja2, repeatedly, until the string stopped changing, so a query containing `${...}` or `{{...}}` was expanded as a template. Here the prompt has a fixed set of named fields and user text is inserted as a value, never re-read.

**Calls are bounded.** Nothing bounded them before: no timeout, no retry, and no ceiling. This library retries transport failures with exponential backoff and enforces four limits: rows, time, cost, and call count. The time limit bounds admission and waiting rather than the whole run; [what it bounds](README.md#what-the-time-ceiling-actually-bounds) is stated exactly. Time, calls, and cost are claimed before each provider attempt, and rows are capped once records are parsed. When limits stop a run, `meta.stop_reasons` retains every observed axis and `meta.stopped_by` keeps the highest-precedence one. Records parsed before a late failure are retained when at least one extraction response was answered. Every `limits.*` name is checked against a closed set and every value against its type, so a misspelled or malformed ceiling raises `ConfigError` rather than being accepted and ignored.

**The cost claim is an upper bound, or the ceiling is refused.** A call's price is not known until it returns, so `limits.max_cost` is claimed against the prompt's length in UTF-8 bytes plus a framing allowance at the model's published input rate, plus the whole `llm.max_tokens` allowance at its output rate, raised for the cache-write, long-context and regional-processing charges a model in the [model table](README.md#supported-models) carries. A measured cost settles the claim; when the response cannot prove the charge, [the cost bound](README.md#the-cost-bound) sets it and the result reports its accounting as unknown. Counting bytes rather than dividing by an average is what makes it a bound: a byte-pair tokenizer merges byte sequences, so every token it emits consumes at least one byte of the encoded prompt, while an average is exceeded by dense text. Counting characters would not have been a bound even though it reads like one, since a tokenizer splits a character whose encoding it has no merge for. Where no bound can be established the ceiling raises `ConfigError` instead of resting on a guess: when the model carries no published price and none was supplied, and when `llm.max_tokens` is not a finite positive number.

The minimum cost bound is the full output allowance and does not depend on the prompt. With the default `gpt-6-luna` model and 25,000 output tokens, it is about $0.01375, the output rate with the 10% regional uplift. A lower `limits.max_cost` refuses the call before provider contact. Reduce `llm.max_tokens` if the answer needs fewer tokens.

**A caller can estimate a request before running it.** `LlmSource.estimate(query, attrs, context, properties)` builds the prompt, applies the same bound as a reservation, and returns an `Estimate` without contacting a provider. A request estimated within a ceiling uses the same bound at runtime. The `LLM.ESTIMATE` query operation exposes the result as a one-record list for registry callers. When the package cannot price a model, the result has `bounded=False` and explains why.

**A client declares which ceilings it can enforce, per axis.** `types.BudgetAwareClient` requires one method, `budget_capabilities() -> frozenset[str]`, returning names from `types.Capability`: `CALLS`, `ROWS`, `SECONDS`, `COST`, `ESTIMATE`, or `ALL`, which is all five. **`ESTIMATE` is not a ceiling and `ALL` includes it**, so declaring `ALL` commits a client to two further requirements: `bound_for_attempt(prompt, properties) -> float` must return a true upper bound on one provider attempt, and one call must make no more than `budget.effective_retries() + 1` provider attempts. `estimate` accumulates the per-attempt figure once per permitted attempt. A client whose retry or fallback policy ignores that allowance can therefore exceed the estimate. Such a client should omit `ESTIMATE`; the method then reports `bounded=False` with a reason. A client that declares an axis receives the run's budget under the `budget` property on every call and must reserve against it per provider attempt and resolve each reservation. A ceiling on an undeclared axis raises `ConfigError` on the first fetch, preventing callers from relying on an unenforced limit.

A method rather than an attribute is deliberate. An earlier version accepted any object carrying an attribute named `budget`, which a client holding `budget = None` satisfied while making unbounded calls under a configured ceiling. Per axis rather than as one flag is equally deliberate: `DefaultLlmClient` declares `Capability.ALL` because it makes the provider call itself, while `FunctionClient`, which wraps a callable passed as `llm_fn`, declares rows and nothing else. It can prove it invoked that callable once, and one invocation is not one provider call: the callable may answer from its own memory, contact a provider once, or fan out to several. So `limits.max_calls`, `limits.max_cost`, and `limits.max_seconds` over an `llm_fn` are **refused** where a previous version settled them at a cost of zero and appeared to hold them.

Anyone porting a caller that injects its own client should implement `budget_capabilities`, claim against the budget it receives, and append this call's `Usage` to the list arriving under the `usage_sink` property, which is where the fetch reads its token counts and model name from. A client that declares nothing still works; it is refused the three provider-level ceilings, and `limits.max_rows` needs nothing from it.

**A ceiling is an input to the query.** `execute_query` and `execute_query_nl` both take `optional_properties`, and a `limits.*` key placed there binds that call and no other: the properties are merged over the instance's for the duration of the request and are not written back. That is how a caller reaching this source through the query surface bounds a request, and it is the route to reach for first, because it needs nothing beyond the arguments the surface already takes.

**A caller can also own the budget.** Limits configured on the source, or handed to one call through `optional_properties`, bound that fetch. `fetch` and `afetch` additionally accept `budget=`, a `Budget` the caller holds, from which each fetch derives a child that spends against what the caller has left. That is the second route, and it is the one to use when the ceiling is a total across several requests rather than a bound on any one of them.

**One prompt, in one module.** There were two copies with divergent wording and different variable names, one on the source and one on the `nl2llm` operator. This library has one, in `prompts/`.

**Requests follow the model's published contract.** The earlier implementation sent every model the same arguments. This library sends each model in its table the reasoning effort, sampling and output-token parameter OpenAI documents for it, refuses a combination the model rules out before anything is reserved, and names OpenAI as the provider for a bare OpenAI model name so a model newer than LiteLLM's bundled price map still routes. The [supported models](README.md#supported-models) list each model's contract.

**Unknown configuration keys are rejected.** An unrecognised `llm.*` key raises `ConfigError` naming the accepted set. The value of failing loudly here is specific: an accepted-and-ignored setting means recorded configuration no longer describes what ran.

**Keys that collide are reported, not resolved.** The earlier implementation selected requested columns and let a later key overwrite an earlier one. Here the first key to answer a requested attribute keeps it, and the second is reported in `meta.key_collisions` with the value it carried, because which spelling the model meant is not knowable and letting the later one win makes the answer depend on JSON key order. Two *requested* attributes whose fuzzy forms coincide are a third case and are not refused: the fuzzy index drops the entry it cannot separate, exact keys still match, and a key that only matches fuzzily is reported unmapped. Two occurrences of the *identical* key inside one object are a fourth case and raise `ParseError`, at any nesting depth: two distinguishable spellings leave something to report, while one key repeated leaves nothing to tell the values apart by, and Python's decoder would otherwise keep the last and leave no trace of the first. Two *requested* attributes that normalise to the same key raise `QueryError` instead, including two identical spellings, since no returned value could be assigned to either without guessing.

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

Non-flat planning here is an adaptation to the Blue source contract, not a port: nothing in this package wraps an external planning API, and no result is promised to match one produced elsewhere. The receipt checks internal agreement among rules, planning facts, and plan serialization; it does not verify answer truth, completeness, or quality superiority.

**The virtual entity is not vendor-named.** The earlier implementation synthesised `openai_entity` and `openai_entity_attribute`. This library uses `llm_entity` and `llm_entity_attribute`, consistent with the protocol being the family.

**A fresh source is not constructed per call.** The earlier implementation built a new source object on every registry call. This library holds no connection and constructs a client per fetch, so a single instance is reusable.

## Behaviour kept deliberately

Both attribute forms work: a plain list of names, which is what the design document specifies, and a list of `{name, type}` dicts, which is what the operator uses. Types are used for verification when present.

`context` is free text that may clarify what an attribute name means, matching the documented use rather than only carrying background information.

An empty or whitespace query never reaches the model. An empty list comes back rather than an error, and this library additionally reports `outcome: empty_query` in the result metadata, so a caller can tell a skipped request apart from a model that found nothing.

The virtual hierarchy of one database, one collection, and one entity with one text attribute is preserved so a registry sync has something to walk.

## Not carried over

The `nl2llm` operator and the `NL2LLM` agent are not part of this library. Per the layer's own design, natural-language querying of a source is provided by a per-source `NL2Q*` operator, which belongs with the operator library. This package is the source that such an operator calls.
