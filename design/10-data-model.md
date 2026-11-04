# LlmSource — Data Model

## Overview

LlmSource maintains no dataset, index, or schema to migrate. It obtains an answer from the model or an optional cache, shapes the records, checks them against the caller's declaration, and reports what it took to obtain them. A file-backed cache can retain responses across calls.

The data model therefore consists of five contracts rather than a storage layout:

- **The virtual hierarchy**: the databases, collections, and entities a registry expects to walk, synthesized because a model has none
- **The request**: what a caller supplies and what each part means
- **The record**: what a returned row is, and what the source guarantees about it
- **The result**: the records plus the accounting that describes the run
- **The estimate**: what a request could cost, answered before it is run

An optional cache adds a sixth contract for its key and invalidation behavior. An implicit invalidation rule can cause correctness problems after a model upgrade.

---

## 1. The Virtual Hierarchy

The data layer uses the same hierarchy for every source: `Source → Database → Collection → Entity | Relation → Attribute`. A source may skip levels it does not have. A language model has none, so the source synthesizes one of each for a registry sync to walk.

| Level | Value | Note |
|-------|-------|------|
| Source | `name` given at construction, `llm` by default | |
| Protocol | `llm` | Names the source family; not caller-settable |
| Variant | `openai` | Names the implementation within the family |
| Database | `llm` | One, named for the family rather than for a vendor |
| Collection | `public` | One |
| Entity | `llm_entity` | One virtual entity |
| Attribute | `llm_entity_attribute`, of type `text` | One virtual attribute on that entity |
| Relation | none | A model has no relations to report |

These names have no meaning to the fetch path. They give a registry a well-formed hierarchy to walk and let callers inspect the source in the same way as others. Statistics are reported as `None` because a model has no row counts.

---

## 2. The Request

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `query` | `str` | yes | The natural language request. `None`, an empty string, or whitespace is accepted and answered with an empty result carrying `empty_query` as its outcome; any other non-string is rejected |
| `attrs` | list | no | The output attributes the caller wants. Names, `Attr` instances, or `{"name", "type"}` mappings |
| `context` | `str` or list of `str` | no | Additional text. Its documented use is to say what an attribute name means, and it is rendered as its own prompt section |
| `max_records` | `int` | no | A positive integer cap on returned records |
| `properties` | mapping | no | Per-call configuration, merged over the instance's for this call and not written back. A `limits.*` value can only tighten the effective ceiling; `None` leaves it unchanged; `limits.max_retries=None` is invalid. `optional_properties` carries the same keys on the source query surface |

The three input fields are a query, optional attribute names, and optional context that may resolve ambiguity in those names. The source accepts both plain names and typed attributes. Plain names shape the output; typed attributes also drive coercion.

Planning snapshots the normalized attributes into an ordered immutable schema. A caller may still construct a schema from a list, but later changes to that list cannot change a queued `LeafTask` or the identity used by reduction. Constraints use string, integer, finite number, or boolean values; null, non-finite numbers, and blank strings are not valid constraint values.

### Planning state

Non-flat requests pass through immutable planning contracts before execution:

| Contract | Meaning |
|----------|---------|
| `DataTask` | Semantic query state: original and retrieval queries, typed constraints, accepted schema and cardinality, and partition identity. It has no provider handle, prompt, mutable parent link, or timestamp identity |
| `QueryAnalysisResult` | Accepted rewrite and typed constraints, plus the root `DataTask` and parse diagnostics |
| `SchemaDefinition` | Ordered immutable attributes, ordered primary keys, and the source of the key decision. `Schema` remains its public compatibility projection |
| `CardinalityEstimate` | Soft `expected`, `lower`, and `upper` evidence with its row unit, source, parse status, and diagnostics |
| `IdentityPartitionRule` | Caller- or source-owned finite identity domain with ordered primary keys, parent scope, provenance, and a closed versioned wire form |
| `PartitionSafety` / `AppliedPartitionDecision` | Machine-checkable containment, disjointness, and coverage evidence plus the exact accepted rule |
| `FetchPlan` / `PlanSchemaContract` | Versioned task tree and the ordered identity/schema facts needed to re-check its accepted decisions |
| `PlanningFacts` / `PlanningReceipt` | Planning counters and decisions, bound to configured rule digests and the serialized plan for a direct Python distribution-search result |

`PlanningContext` collects the accepted query-analysis, schema, and cardinality facts. Its canonical
digest covers their semantic content and excludes the schedule, stage receipts, context-level
diagnostics, and `plan.expose_estimate`. Equivalent locked sequential and locked parallel runs
therefore have the same digest even though their wall time can differ. The safety decision, fetch
plan, planning counters, and optional receipt are later contracts built from those facts.

`LeafTask` is a different object. It is the versioned execution DTO produced after planning, with
the prompt version and execution settings needed to cross the local or process boundary. Accepted
`prompt_version` values are `leaf-fetch-1` for legacy wire version 1, `keyed-leaf-fetch-1`
for wire versions 2, 3, 4 and 6, and `keyed-leaf-fetch-2` for wire version 5. Wire version 6 is a `keyed-leaf-fetch-1` task seeded with `held_exclusions` and `held_total`, the keys a root probe already returned; wire version 5 carries the same two fields. They name what the leaf prompt states: the keyed contracts
give the model the ordered primary key and may give it a row estimate, and the legacy contract
gives neither. `keyed-leaf-fetch-2`, selected by `plan.leaf_prompt`, also asks for every matching record, states the estimate as a sentence rather than an object, and presents constraints that partitioning introduced on a schema column as column filters and all others, request-level constraints included, as request scope. A wire version 5 task carries `partition_keys`, the filter names partitioning introduced. Each wire version admits exactly one prompt version, so a task built for one contract cannot be answered with the other. `LeafTask` transports the cardinality estimate even when `plan.expose_estimate` is false;
that setting only omits the estimate from extraction prompts. Model cardinality remains advisory
throughout planning and extraction. `plan.exact_rows` is a hard target only when the plan has one
leaf. A multi-leaf plan does not apply it and reports `exact_rows_not_leaf_bound` in `meta.gaps`.

Rule-backed distribution search is available only through direct Python calls. It requires partitioned mode,
explicit ordered keys, declared finite-domain rules, and at least one rule applicable at the root.
Invalid rules fail before provider contact. A planning receipt establishes internal agreement among
the configured rule digests, planning facts, and plan; it does not establish authenticity, factual
truth, completeness, or answer quality.

`max_records` caps what the source returns; it does not instruct the model. A count in the request influences what the model produces. A caller wanting both must provide both.

---

## 3. The Record

A record is a JSON object. That is the whole contract, and the guarantees around it depend on whether attributes were requested.

**With requested attributes.** In flat mode, every returned record carries exactly the requested attribute names as its keys, in the order requested, with `None` where the model supplied no value. Non-flat planning can enrich the output schema with inferred attributes; `requested_attrs` still records the caller declaration. Keys the model returned under a different spelling are mapped onto the requested name where the mapping is unambiguous. Keys that map to nothing are reported and do not appear in the record. Where two returned keys map to the same attribute, the first holds it and the second is reported with the value it carried, since which spelling was meant is not knowable here.

**Without requested attributes.** Flat records pass through as the model produced them, so keys may vary between records and no key is guaranteed. In non-flat modes, bootstrap infers a schema and may identify a key; the effective schema then shapes the records. `requested_attrs` remains empty so the caller can distinguish inferred shape from its own declaration.

**In both cases.** Values are whatever the model returned, coerced only where a type was declared and the coercion is unambiguous. Nothing is checked for truth, and nothing is looked up anywhere.

---

## 4. Attribute Types

A declared type drives coercion in the verifier.

| Declared type | Accepted input | Stored as |
|---------------|----------------|-----------|
| `int`, `integer` | integer; whole float; digit string, with optional thousands separators | `int` |
| `float`, `number` | number; numeric string, with optional thousands separators | `float` |
| `bool`, `boolean` | boolean; `true` / `yes` / `y` / `1`; `false` / `no` / `n` / `0`, in any case | `bool` |
| `date` | `YYYY-MM-DD`, `YYYY/MM/DD`, `DD-MM-YYYY` (only when the day is above 12 or equals the month; any other `NN-NN-YYYY` value is kept and reported as a type mismatch), or `Month D, YYYY` | ISO date `str` |
| `str`, `string`, `text` | a string | unchanged |
| anything else | any | unchanged, and the name is reported |

A comma is treated as a thousands separator only where it actually is one: groups of exactly three digits, optionally signed, optionally with a dot decimal part. The decimal comma `"3,80"` stays unchanged and is reported as a type mismatch; removing its comma would change the value to `380.0`. Anything that is not a thousands grouping keeps its commas, fails to convert, and is reported as a type mismatch.

`str`, `string` and `text` are checked rather than converted. A declared string that arrives as the number `1299` is reported as a mismatch and left as it arrived, because `str()` succeeds on every value in Python: a string declaration that coerced could never fail, which would make it a check that validates nothing while the caller believes their records are typed. Declare `string` for a value that is textually meaningful, such as an identifier or a code, where a bare number coming back is worth knowing about. For a quantity, declare `int` or `float`, which do convert and report only when conversion is impossible.

A type name outside this table is accepted, since the declared type is sent to the model verbatim as an instruction and `price: money` is a reasonable thing to ask for. Nothing here can check it, so no value fails against it, and the name is reported once in `ExecMeta.unchecked_types` rather than raised against every value. That way a misspelled type is visible without being an accusation about the data.

Type names are matched case-insensitively after stripping. A value that will not coerce is returned as it arrived, and the record is kept: dropping a row because one field came back in an unexpected format loses data the caller asked for, and reporting is cheaper than recovering. Every such value is listed in `ExecMeta.type_mismatches`, so a caller told its records are typed can see which ones are not.

Booleans are deliberately excluded from integer and float coercion. In Python a boolean is an integer, and silently turning `True` into `1` in a column declared `int` would hide a model that answered the wrong question.

---

## 5. The Result

### `FetchResult`

| Field | Type | Description |
|-------|------|-------------|
| `records` | `list[dict]` | The records, after alignment, coercion, deduplication, and row capping |
| `meta` | `ExecMeta` | What the run did and what it cost |

These remain the only dataclass fields. A direct Python distribution-search result may expose an
optional `planning_receipt` property. `to_dict()` adds a top-level receipt only when one exists;
results without distribution search keep the two-key form. The CLI and MCP omit the receipt.

### `ExecMeta`

| Field | Type | Description |
|-------|------|-------------|
| `gaps` | `list[str]` | Why rows may be missing, one entry per shortfall observed. Always present; empty means nothing was observed to be missing, not that the answer is complete |
| `outcome` | `str` | Why the result looks the way it does, from the closed vocabulary below |
| `model` | `str` | The model that answered, as reported by the client |
| `calls` | `int` | Provider attempts claimed against the run's budget. A cache hit is not one, and a contacted attempt that failed is |
| `prompt_tokens`, `completion_tokens` | `int` | Token counts, summed across the usage entries this call produced; zero for a cache hit |
| `cost` | `float` | Spend charged against the run's budget; zero for a cache hit. When `accounting_known` is false, this is bookkeeping or a conservative bound rather than an exact bill |
| `accounting_known` | `bool` | Whether `calls`, `cost` and the token counts describe measured provider work. False when a caller-supplied `llm_fn` answered, provider accounting is missing, or a cost-ceiling charge rests on the reservation, a usage bound, or incomplete calculator categories. Treat provider spend as unknown rather than zero |
| `latency_seconds` | `float` | Wall-clock time for the whole run, including parsing and reduction |
| `cache_hits` | `int` | Calls answered from cache |
| `requested_attrs` | `list[str]` | The attribute names the caller asked for |
| `row_estimate` | `int \| None` | Accepted non-flat bootstrap row-count hint for the root slice. Zero is retained; flat, unknown, and discarded bootstrap results use `None` |
| `cardinality_estimate` | `dict[str, object] \| None` | Accepted non-flat bootstrap estimate with `expected`, `lower`, `upper`, `row_unit`, `source`, `parse_status`, and `diagnostics`. Flat mode or planning without this structure uses `None` |
| `primary_keys` | `list[str]` | Ordered identifying attributes used for execution and reduction; empty when none was established |
| `observed_key_count` | `int` | Distinct complete normalized keys in the final returned records after reduction, deduplication, and row capping; zero when no primary key exists |
| `marginal_key_yields` | `list[int]` | New normalized key identities from each extraction attempt in leaf order; when no primary key exists, new exact records per attempt |
| `incomplete_key_records` | `int` | Distinct per-leaf records missing at least one primary-key value; zero when no primary key exists |
| `normalization_collisions` | `list[dict]` | Per-leaf complete keys that compare equal after declared-type-aware normalization but use different display values; each entry carries `primary_keys`, `kept`, and `collided` |
| `split_decision` | `str \| None` | What the root probe decided under `plan.split_policy="evidence"`: `probe_accepted` (the probe's answer is the result), `probe_split_length` (the provider said its output allowance ended the answer), `probe_split_incomplete` (the answer ended mid-document and only its complete records were read, with no reason from the provider), `probe_split_shortfall` (fewer than half the keys an estimate above `plan.split_threshold_rows` expects) or `probe_split_failed` (no usable answer). `None` under the default policy, when the root could not split and was not probed, or when `plan.exact_rows=0` answered the root without a call, and then absent from `FetchResult.to_dict()` and `meta_view` |
| `repaired_numbers` | `int` | Numbers the model wrote with thousands separators in an object value, such as `4,470`, read as `4470`. Such a response is not valid JSON; strict parsing is tried first and this is the one repair applied before parsing it again. `0` when nothing was repaired |
| `filter_contradictions` | `int` | Records whose checkable filter column contradicts the filter of the leaf that returned them, counted once per record before reduction. A filter is checkable when it names a declared attribute of a recognized type and that type can read both the record's value and the filter's; strings compare after case and whitespace folding, numbers by value. Kept under `plan.filter_enforcement="flag"`, removed at the leaf under `"drop"`, and counted either way. `0` means none was observed |
| `observed_keys` | `list[str]` | Every key the model produced, in order of first appearance |
| `remapped_keys` | `dict[str, str]` | Returned key mapped to the requested attribute it was matched to |
| `unmapped_keys` | `list[str]` | Returned keys that matched no requested attribute |
| `type_mismatches` | `list[dict]` | Every value that would not coerce to its declared type |
| `key_collisions` | `list[dict]` | Every returned key whose value was not used because another key had already answered the same attribute |
| `unchecked_types` | `list[str]` | Declared type names this library cannot check, reported once each |
| `truncated_to` | `int \| None` | Record count after a row cap took effect |
| `stop_reasons` | `list[str]` | Ordered distinct extraction limits and discarded-final-output facts. Always present |
| `stopped_by` | `str \| None` | First member of `stop_reasons`, retained as the singular compatibility projection |
| `record_origins` | `list[list[str]]` | Which parts produced each record, parallel to `records`. A list per record, because overlapping parts can each return the same one |
| `asked` | `str \| None` | The question that actually ran, when an analysis call restated it. `None` when nothing changed it |

These fields separate observations that the records alone cannot carry. `observed_keys`, remapping, mismatches, and collisions report shape changes. `stop_reasons` keeps simultaneous limits from overwriting one another, while `stopped_by` keeps the former singular interface. `requested_attrs` remains the caller declaration even when an inferred or enriched schema shapes the output. `row_estimate` is the `expected` compatibility value from `cardinality_estimate`. The estimate, `observed_key_count`, and `marginal_key_yields` describe planning input and this run's observations; they do not establish completeness. `gaps` reports operational failures and fallbacks; it is not a completeness score.

`calls` and `cost` are read from the budget rather than summed from the usage entries. The budget claimed them under a lock, while two concurrent fetches through one reused client can read the same cursor into a shared usage list and each slice both entries, so a count taken from that list can carry another request's spend. The token detail and the model name still come from usage entries, but from a list the fetch created and handed the client for that one call under the `usage_sink` property, not from the client's own history. A private list removes the cursor instead of narrowing it. The consequence to know is that a client which does not claim against the run's budget reports zero calls and zero cost however many it made, which is why such a client is refused the ceilings on the axes it did not declare, and is not measured through them.

### `Outcome`

An empty result is a valid answer from this source rather than a failure, which is how the layer's other sources treat an absent result. But "you asked nothing", "the model says there are none", "everything it returned was filtered out", and "a limit stopped the run" are different facts, and a planner deciding whether to ask elsewhere needs to tell them apart. `outcome` is the field that keeps them apart, and its vocabulary is closed.

| Value | Meaning |
|-------|---------|
| `ok` | Records were produced and no limit intervened |
| `empty_query` | The request was absent, empty, or whitespace, so no model was called |
| `model_returned_none` | The model returned no records at all |
| `filtered_to_empty` | The model returned records and reduction left none, which happens when no record answered any requested attribute |
| `partial` | Extraction was answered but some work was interrupted, including a failed split leaf or a late continuation failure after records were parsed |
| `stopped_by_limit` | A non-row stop fired without an interrupted partial answer; records may remain when a limit is detected after extraction. A row cap that leaves no records also takes this outcome |
| `truncated` | The row ceiling cut the result short and records remain |

The field rides on `FetchResult`, so it reaches a caller through `fetch` and `afetch` alone. `execute_query` and `execute_query_nl` return the record list by the contract of the surface they match, and a caller that needs the reason for an empty list uses the library surface.

Partial interruption outranks a simultaneous cap because each cap remains readable in `stop_reasons`.
Without interruption, any non-row stop reports `stopped_by_limit`. A row-only cap reports
`truncated` when records remain and `stopped_by_limit` when none do. `filtered_to_empty` and
`model_returned_none` are reachable only when no stop fired.

### `TypeMismatch`

Each entry in `type_mismatches` is a mapping with four keys.

| Key | Type | Description |
|-----|------|-------------|
| `record_index` | `int` | Position of the record in the returned list |
| `attribute` | `str` | The requested attribute whose value did not fit |
| `declared_type` | `str` | The type the caller declared, lowercased |
| `value` | `str` | The repr of the value as it arrived, before coercion was attempted |

The record is still returned and the value is still present, uncoerced. Reporting rather than dropping is the same choice made for unmapped keys, applied to a value instead of a column: the caller keeps the data and learns that one field is not what it asked for.

### `KeyCollision`

Each entry in `key_collisions` is a mapping with five keys.

| Key | Type | Description |
|-----|------|-------------|
| `record_index` | `int` | Position of the record in the returned list |
| `attribute` | `str` | The requested attribute both keys reached |
| `kept_key` | `str` | The returned key whose value is in the record |
| `ignored_key` | `str` | The returned key whose value was not used |
| `ignored_value` | any | The value that key carried |

Match rules that ignore case and separators are what recover `IATA Code` or `iataCode` for a requested `iata_code`, and the same tolerance lets two returned keys reach one attribute. The first encountered answer is kept, so JSON key order determines the surviving value. Which of the two spellings the model meant is not knowable here, so the later one is reported with the value it carried rather than resolved or dropped.

Two occurrences of the identical key in one JSON object are a different case and never reach alignment: parsing rejects the object with a `ParseError`, at any nesting depth. Python's decoder keeps the last of a duplicate pair and leaves no trace of the first, which resolves the ambiguity in the opposite direction from every other collision here and does it silently. Two spellings leave two distinguishable keys, so the ignored one can be named in a report; one key repeated leaves nothing to name, and rejecting is the only answer that is not a guess the caller cannot see.

Two *requested* attributes whose fuzzy forms coincide are a different case and are not refused: the fuzzy index drops the entry it cannot separate, so exact keys still match and a key that matches only fuzzily is reported unmapped rather than assigned by guess. Only the same name requested twice raises `QueryError`.

### `Usage`

Per call rather than per run, recorded by the client and folded into `ExecMeta`.

| Field | Type | Description |
|-------|------|-------------|
| `model` | `str` | Model used for this call |
| `prompt_tokens`, `completion_tokens` | `int` | Token counts; zero on a cache hit |
| `cost` | `float` | Recorded attempt cost. It is zero for a cache hit or a failed attempt with unknown accounting; a successful unpriced response may carry a conservative bound. The budget owns the run's retained charge |
| `latency_seconds` | `float` | Wall-clock time for this call, recorded on a cache hit as well |
| `cache_hit` | `bool` | Whether the response came from cache |
| `finish_reason` | `str \| None` | Provider-reported generation stop reason, or `None` when no usable reason was reported |
| `cost_known` | `bool` | Whether this call's provider cost was observed; false when the reservation or a usage bound determines the charge, or calculator categories are incomplete |

A cache hit records latency and marks itself, and carries no tokens and no cost. This is deliberate: a caller reading a cost total should see what was spent, and a caller reading a latency total should see what was waited for, and those two are not the same measurement.

---

## 6. The Estimate

`Estimate` is the only contract here that describes a request that has not run. It is what `LlmSource.estimate` returns and what the `LLM.ESTIMATE` op reports as a one-record list, and it is produced without contacting anything.

| Field | Type | Description |
|-------|------|-------------|
| `bounded` | `bool` | Whether a bound could be established at all |
| `max_cost` | `float \| None` | The most one **flat** fetch of these arguments could cost, or `None` when unbounded. Reads no `plan.*` setting, so a decomposed or continued request can exceed it |
| `reason` | `str \| None` | What was missing, when `bounded` is false |
| `model` | `str` | The model the figure was computed for |
| `prompt_bytes` | `int` | Encoded length of the prompt that was rendered |
| `max_output_tokens` | `int` | The output allowance the bound reserved |
| `max_cost_per_attempt` | `float \| None` | The most one provider attempt could cost |
| `attempts_within_ceiling` | `int \| None` | With a cost ceiling, how many of `max_attempts` fit under it at the per-attempt bound; `0` means the first attempt is refused. An empty request costs nothing, so all its attempts fit. `None` without a ceiling or a bound |
| `max_attempts` | `int` | Attempts the retry allowance permits, the first one included |

`max_cost` bounds the whole of a `flat` fetch, not one attempt: it is `max_cost_per_attempt` accumulated
`max_attempts` times, the same way the ledger accumulates it, so a ceiling set to exactly `max_cost`
admits every attempt the figure accounted for. Accumulated rather than multiplied, because the two
differ by a unit in the last place and that is enough to refuse the last attempt. Sizing a ceiling
against the per-attempt figure instead means one transient failure consumes it and the retry is
refused.

`max_cost` is an upper bound, not a prediction. The estimate and the reservation use the same calculation. It prices the entire output allowance at the model's output rate, so the actual cost is usually lower; it must not underestimate the cost because the value is used to enforce a configured ceiling.

An unbounded estimate is an answer rather than a failure, which is the same choice `Outcome` makes one level up. A caller asking what a request might cost is not yet asking for a ceiling, so a model this package has no price for produces `bounded` false carrying `reason`, and the caller can act on that. An empty request produces `bounded` true with a `max_cost` of zero, since it never reaches a model.

`prompt_bytes`, `max_output_tokens`, `max_cost_per_attempt` and `max_attempts` are reported so a caller can see what the figure was built from rather than having to take it whole. Three of them are the levers on it: the prompt grows with the request and the attributes, the output allowance is `llm.max_tokens`, and the attempt count is `limits.max_retries` plus one.

---

## 7. Budget State

Not persisted. Scoped to a single fetch unless the caller supplies a budget of its own, in which case the fetch derives a child and the state below accumulates in the caller's.

| Field | Type | Description |
|-------|------|-------------|
| `limits` | `Limits` | The configured ceilings; `None` on an axis means unlimited. Every field is validated on construction, so a wrong type, a `bool`, a negative value, or a non-finite value raises `ConfigError` rather than becoming a ceiling that enforces nothing |
| `calls` | `int` | Provider attempts claimed so far |
| `cost` | `float` | Cost settled so far |
| `reserved` | `float` | Cost claimed by attempts that have not yet resolved |
| `committed` | `float` | `cost` plus `reserved`, which is what the cost ceiling is compared against |
| `rows` | `int` | Rows admitted so far |
| `stopped_by` | `str \| None` | The axis that refused an attempt, truncated a result, or was found crossed at the end of the run |
| `elapsed` | `float` | Seconds since the budget was constructed, or since its parent was, for a derived child |

Call count, elapsed time, and cost are all checked and claimed under one lock before a provider is contacted, so a concurrent fan-out cannot admit two calls that each saw room for one. Rows are admitted after parsing, because the count is not known until then.

Cost is claimed against a bound built from the package or configured rates rather than an average-case estimate. The bound is reserved before the attempt and remains the minimum ledger charge after provider contact. A higher readable calculator figure or usage bound raises it; incomplete calculator categories leave accounting unknown ([cost bound](../lib/README.md#the-cost-bound)). If no bound can be established, the request is refused. Concurrent attempts therefore cannot all spend against the same remaining budget.

A derived child holds its own copy of every field above and spends from what its parent has left, so a claim recorded here is also recorded in every budget up the chain, and the effective ceiling on each axis is the smallest along it, retry depth included. The one field a child does not take from its parent is `stopped_by`, because it records what refused a particular attempt, and inheriting it would make a later affordable request report the earlier refusal as its own.

---

## 8. The Cache Key

The optional response cache is keyed on the serialized provider call arguments, sorted by key. That covers the model name, the fully rendered prompt, and every forwarded parameter such as temperature and maximum tokens.

| Included in the key | Not included in the key |
|---------------------|-------------------------|
| Model name | Provider-side model version |
| Rendered prompt, including request, attributes, and context | Prompt template version, except as it changes the rendered text |
| Forwarded provider parameters | Limits, which do not change the answer |

The consequence a caller must know: an entry does not expire when the provider updates a model behind an unchanged name. Cache directories are held per directory rather than per client, so a per-call `cache_dir` override gets its own cache instead of reusing whichever was opened first. Callers who need version-sensitive invalidation should carry a version marker in the model name or in the cache directory.

The same key is used whichever store answers. Two stores ship, `MemoryCache` and `FileCache`, both keyed this way and both supplied through the `cache_store` property; neither carries a time-to-live, so an entry lives until the store is cleared or, for `MemoryCache`, until least-recently-used eviction reaches it.

---

## 9. What the Data Model Deliberately Omits

Recorded so that a caller planning around this source knows which checks remain its own.

- No schema or key is factually verified. Non-flat bootstrap can infer both, while preserve mode keeps caller declarations and enrich mode may append fields. An inferred key controls deterministic merging; it is model-produced structure, not external truth.
- No calibrated cardinality or completeness measure is reported. `cardinality_estimate` preserves the accepted non-flat bootstrap range, unit, source, parse status, and diagnostics, while `row_estimate` exposes its `expected` value for compatibility. Flat mode and planning without a usable structure use `None`. These fields do not state how many records should exist or whether they were all returned. `observed_key_count` and `marginal_key_yields` describe this run only. `Estimate` answers what a request could cost, which is a third question again.
- No per-field confidence is reported. The source reports what it did, not how sure the model was.
- Provenance is carried alongside the records, not inside them. A record is the model's answer and nothing is added to it, so `meta.record_origins` runs parallel to `records` and names the part or parts that returned each one. A list per record because overlapping parts can each return the same record and all of them are true. It names the parts that returned a record; it does not claim they are the only parts that hold it. Lineage beyond that — which prompt, which attempt — is not carried.
- No fact is verified. A record is checked against the shape it was asked for, and nothing in the result should be read as establishing that a value is true.
