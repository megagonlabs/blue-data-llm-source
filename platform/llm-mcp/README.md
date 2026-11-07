# blue-data-llm-mcp

This package exposes `blue-data-llm-source` through an MCP server named `blue-data-llm`. The server uses stdio and provides five tools and no resources. It validates each tool call against the advertised schema, translates the arguments into a library call, and returns JSON. The library remains responsible for domain validation, configuration, alignment, and budgets. MCP validation and deployment refusals name the affected argument or setting. Library failures retain a known error kind and use fixed messages.

**Set an operator ceiling before exposing a provider credential.** `llmdb_query` accepts `limits.max_cost`, `limits.max_seconds`, and `limits.max_calls`, and `llmdb_estimate` calculates a cost bound before a query runs. The operator can set the same ceilings for every call with `BLUE_DATA_LLM_SOURCE_LIMIT_COST`, `_LIMIT_CALLS` and `_LIMIT_SECONDS`; a call can lower them but never raise or remove them. Without an operator ceiling, a call that sets none is unbounded. See [Ceilings](#ceilings).

## Tools

| Tool | What it does | Contacts the provider |
|---|---|---|
| `llmdb_query` | Fetch structured records from a language model's own knowledge. | yes |
| `llmdb_estimate` | Bound what one flat query of the same request could cost. | no |
| `llmdb_list_models` | List the model names this package carries a published price for. | no |
| `llmdb_inspect_model` | Report one model's rates and whether a cost ceiling can be bounded against it. | no |
| `llmdb_inspect_metadata` | Report the protocol, the variant, and the library version. | no |

The `llmdb_` prefix prevents collisions when a client loads several data-source servers into one tool namespace. The remainder of each name matches the corresponding `blue-llmdb` operation.

Internal builds before 0.2.0.0 exposed `llm_fetch`, `llm_estimate` and `llm_info`. These names are no longer supported. A client that calls one receives an `UnknownTool` error listing the available tools.

### `llmdb_query`

Fetch structured records from a language model's own knowledge.

| Input | Type | Required | Meaning |
|---|---|---|---|
| `query` | string | no | What to ask for, in plain language. Omitted or empty, the call succeeds with no records and `outcome: empty_query` |
| `attrs` | array | no | Attributes every record must carry: `"country"` or `{"name": "year", "type": "int"}`. Each attribute once; the two spellings name the same attribute, and a repeat is refused before the model is reached |
| `context` | string | no | Text that clarifies the query or an attribute name |
| `max_records` | integer, minimum 1 | no | Keep at most this many returned records; the model still answers in full |
| `limits.max_cost` | number, minimum 0 | no | Refuse a call that would take this run's spend past this many dollars |
| `limits.max_seconds` | number, minimum 0 | no | Refuse a call once the run has been going this long |
| `limits.max_calls` | integer, minimum 0 | no | Refuse more than this many provider calls, retries included |
| `plan.mode` | enum: `flat`, `partitioned`, `row_by_row` | no | How the ask is decomposed. `flat` is the default |
| `plan.max_continuations` | integer, minimum 0 | no | Extra asks allowed per leaf. `3` by default in `partitioned`, `0` otherwise |
| `plan.max_leaves` | integer, minimum 1 | no | Maximum terminal slices |
| `plan.partition_key` | string | no | Preferred root split dimension |
| `plan.max_depth` | integer, minimum 0 | no | Deepest partition level |
| `plan.max_partitions` | integer, minimum 0 | no | Maximum children proposed per node |
| `plan.target_rows_per_leaf` | integer, minimum 1 | no | Row-estimate hint for descendant splits |
| `plan.schema_mode` | enum: `preserve`, `enrich` | no | Keep declared attributes, or let planning append inferred ones |
| `plan.planning_order` | enum: `planning.cardinality_first`, `planning.schema_first`, `planning.locked_sequential`, `planning.locked_parallel`, `planning.combined` | no | Order used to obtain analysis, schema and cardinality facts |
| `plan.expose_estimate` | boolean | no | Include or omit the soft estimate in extraction prompts |
| `plan.key` | string | no | Record identity attribute |
| `plan.keys` | array of strings | no | Ordered primary-key fields for composite identity |
| `plan.exact_rows` | integer, minimum 0 | no | Single-leaf hard target; a multi-leaf plan reports `exact_rows_not_leaf_bound` instead |
| `plan.max_exclusion_bytes` | integer, minimum 0 | no | Encoded byte cap for whole-key exclusions |
| `plan.leaf_prompt` | enum: `keyed-leaf-fetch-1`, `keyed-leaf-fetch-2` | no | Leaf prompt contract for non-flat modes |
| `plan.child_wording` | enum: `parent`, `canonical` | no | How a partitioned child is worded |
| `plan.filter_enforcement` | enum: `flag`, `drop` | no | Keep or remove records that contradict their part's filter |
| `plan.range_filters` | boolean | no | Read integer-span partition values as range filters |
| `plan.partition_guardrails` | boolean | no | Proposal rules and the numeric-key reject |
| `plan.split_policy` | enum: `always`, `evidence` | no | Ask the root first (`evidence`, the `partitioned` default), or always split it |
| `plan.split_threshold_rows` | integer, minimum 0 | no | Estimate above which a short probe answer splits |

The rows follow schema order. Each of the seven enum `plan.*` settings above has a closed set of values: its schema declares a JSON Schema `enum`, and the server refuses values outside that set before calling the library.

The `type` on an attribute is deliberately **not** closed. The library checks a value against a type name it recognizes (`int`, `integer`, `float`, `number`, `bool`, `boolean`, `date`, `str`, `string`, `text`) and accepts any other name unchecked, reporting it once in `meta.unchecked_types`. Refusing an unrecognized name here would make that field unreachable over this transport.

Nothing is required. A missing query is a successful call with no records, not an error.

The dots in `limits.max_cost` and `plan.mode` are part of each argument name. Pass flat keys, not nested `limits` or `plan` objects; a nested `limits` object is refused as `unknown argument 'limits'`. For example:

```json
{"query": "the member states of the European Union", "attrs": ["country"], "limits.max_cost": 0.05, "plan.mode": "partitioned"}
```

A call and its result:

```json
{
  "query": "the member states of the European Union",
  "attrs": ["country", {"name": "accession_year", "type": "int"}],
  "limits.max_cost": 0.05
}
```

```json
{
  "records": [
    {"country": "France", "accession_year": 1958}
  ],
  "meta": {
    "outcome": "ok",
    "model": "gpt-6-luna",
    "calls": 1,
    "prompt_tokens": 12,
    "completion_tokens": 34,
    "cost": 0.020862875000000003,
    "accounting_known": false,
    "elapsed_seconds": 0.31,
    "row_estimate": null,
    "cardinality_estimate": null,
    "primary_keys": [],
    "observed_key_count": 0,
    "marginal_key_yields": [1],
    "incomplete_key_records": 0,
    "filter_contradictions": 0,
    "gaps": [],
    "stop_reasons": [],
    "requested_attrs": ["country", "accession_year"]
  }
}
```

If this call includes `"max_records": 1` and the model produces more than one record, the response keeps one record and reports `outcome: truncated`; generation still consumed the full model answer.

`outcome` is always present and explains the result: `ok`, `empty_query`, `model_returned_none`, `filtered_to_empty`, `partial`, `stopped_by_limit`, or `truncated`. Use it to distinguish a model-produced empty result from one caused by filtering, interruption, or a limit.

`accounting_known` appears when it is false; it is omitted when accounting is measured. The other fields shown in the sample are always present. `gaps` reports an observed failed or interrupted part, a ceiling, or discarded output; a valid empty answer alone does not create one. `stop_reasons` retains simultaneous limits in precedence order. `row_estimate` is the accepted non-flat bootstrap root hint or JSON `null`.

`meta` also carries `normalization_collisions`, `repaired_numbers`, `split_decision`, `unchecked_types`, `record_origins`, `asked`, `stopped_by`, `truncated_to`, `cache_hits`, `unmapped_keys`, `remapped_keys`, `type_mismatches`, `key_collisions`, and `accounting_known`, each when it applies. An absent key there means the thing did not happen. Read `accounting_known` before `cost`: under a cost ceiling, a contacted attempt keeps at least its reservation in the ledger. When `accounting_known` is false, `cost` is a conservative ledger value or an unmeasured zero, not billed spend. [What `cost` means](../../lib/README.md#what-cost-means) lists each case. A budget stop is a result rather than a tool error.

### `llmdb_estimate`

What one **flat** `llmdb_query` of the same request could cost, without calling the model. It reads no `plan.*` setting, so a request with parts or continuations can cost several times this figure. Size a ceiling for a split request from its part count. Same `query`, `attrs`, and `context`, and the same three `limits.*` keys: a call ceiling caps the attempts a fetch may make, so it caps what a fetch can cost. There is no `max_records`, because a row cap cannot change the answer.

```json
{"query": "the member states of the European Union", "attrs": ["country"]}
```

```json
{
  "bounded": true,
  "max_cost": 0.062570475,
  "max_cost_per_attempt": 0.020856825,
  "max_attempts": 3,
  "reason": null,
  "model": "gpt-6-luna",
  "prompt_bytes": 779,
  "max_output_tokens": 25000,
  "attempts_within_ceiling": null
}
```

`attempts_within_ceiling` is `null` when no cost ceiling applies to the call. With one, from the call or from the operator, it is how many of `max_attempts` fit under it, each at `max_cost_per_attempt`; `0` means the first attempt would be refused.

`max_cost` bounds the whole of a `flat` fetch rather than predicting it: `max_cost_per_attempt` accumulated `max_attempts` times, with the entire output allowance reserved at the model's output rate and the prompt bounded by its encoded length. A typical call costs a fraction of it. It is the same figure the reservation claims, so a request that estimates inside a ceiling is not refused when it runs, and a ceiling set to exactly `max_cost` admits every attempt the figure accounted for. Both are computed from the package or configured rates, not a provider quote. The bound assumes those rates and the provider's output-token allowance hold; it is not an invoice prediction. Neither cost is rounded, for the same reason: a figure a run can exceed is not a bound.

`bounded` is `false` when no bound can be established, such as when the library has no rates for a model and none were supplied. `reason` gives fixed guidance about pricing, output allowance, or client cost support. This is a successful call, even when it cannot provide a cost bound. An empty call `llmdb_estimate {}` succeeds with `bounded: true`, `max_cost: 0`, and `reason: empty_query`. This path does not contact the provider.

### `llmdb_list_models`

No inputs. The pricing bases this package carries a published price for, sorted. These are bases and not the whole set of boundable names: a dated variant prices against the base it extends, so `gpt-4o-2024-08-06` is boundable and is not on the list. A ceiling needs a listed name, a dated variant of one, or rates the deployment supplied; `llmdb_inspect_model` settles a single name without this ambiguity.

```json
{
  "models": [
    "gpt-3.5-turbo",
    "gpt-4",
    "gpt-4-turbo",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4o",
    "gpt-4o-2024-05-13",
    "gpt-4o-mini",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.5",
    "gpt-5.6",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6-sol",
    "o1",
    "o3",
    "o3-mini",
    "o4-mini"
  ]
}
```

The price table is a snapshot of published list prices and can become outdated. This tool publishes model names; use `llmdb_inspect_model` to retrieve a model's rates.

### `llmdb_inspect_model`

One model's rates, and whether this server could bound a `limits.max_cost` ceiling against it.

| Input | Type | Required | Meaning |
|---|---|---|---|
| `model` | string | **yes** | Model name to look up. Exact names and dated variants only, with or without an `openai/` prefix; a longer name is a different model, not a variant of the one it starts with |

```json
{"model": "gpt-4o"}
```

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

| Field | Meaning |
|---|---|
| `priceable` | Whether the package's price table carries this model |
| `price_per_input_token`, `price_per_output_token` | Published list prices in USD per token, or `null` |
| `max_output_tokens` | The explicit deployment allowance, or the inspected model's default |
| `cost_ceiling_supported` | Whether a cost bound can be established on this deployment. It needs rates and a finite output allowance, so a priceable model on a source with no allowance is still `false`, and a model the table does not carry is `true` where the deployment supplied `llm.price_per_input_token` and `llm.price_per_output_token`. It is the same question `llmdb_estimate` answers as `bounded` |
| `published_defaults` | What the package's model table records for this model: `reasoning_efforts` it accepts, `default_reasoning_effort`, when it accepts a `temperature` (`always`, `effort_none_only` or `never`), the `output_token_parameter` the request uses, `cached_input_price_per_token`, `cache_write_price_per_token` and `long_context_threshold_tokens`. These are the model's published defaults; a deployment setting such as `BLUE_DATA_LLM_SOURCE_REASONING_EFFORT` overrides them. `null` for a model the table does not carry |

A model the table does not carry answers with `priceable: false`, null rates and null `published_defaults`. That is a successful call, the same way `bounded: false` is on an estimate.

### `llmdb_inspect_metadata`

No inputs. Returns the protocol, the variant, and the version of the library underneath. It reads no deployment setting, so it still answers while a malformed one is failing the query, estimate and model-inspection tools:

```json
{"protocol": "llm", "variant": "openai", "version": "1.0.0"}
```

## Decomposition

By default, `llmdb_query` answers with one model call. A large request can be split into parts, queried separately, and merged, with one or more calls per part. The caller selects the decomposition mode.

| Argument | Meaning |
|---|---|
| `plan.mode` | `flat` is the one-completion default; `partitioned` builds a bounded recursive tree; `row_by_row` asks per identified entity. |
| `plan.max_continuations` | How many times a part may ask again for records it did not get. `3` by default in `partitioned`; `0` in the other modes, so each part asks once. It bounds asks per part, not provider attempts or planning calls; each ask may still be retried up to `limits.max_retries` times, three attempts by default. |
| `plan.max_leaves` | The most parts a plan may hold, the term that bounds its worst-case cost. Default `100`. |
| `plan.key` | The attribute identifying a record, so parts returning the same one merge. Must be among the requested attributes. |
| `plan.partition_key` | Preferred root split dimension. Descendants may choose another dimension. |
| `plan.max_depth` | Deepest partition level. Default `2`; `0` disables subdivision. |
| `plan.max_partitions` | Most children proposed for one node. Default `20`. |
| `plan.target_rows_per_leaf` | Uncertain row-count hint for descendant splitting. Default `50`; it is not a completeness target. |
| `plan.schema_mode` | `preserve` keeps declared attributes; `enrich` lets non-flat planning append inferred ones. |
| `plan.keys` | Ordered primary-key fields for composite identity. |
| `plan.exact_rows` | Hard target for a single-leaf plan. A multi-leaf plan does not apply it and reports `exact_rows_not_leaf_bound`. |
| `plan.max_exclusion_bytes` | Encoded byte cap for whole-key exclusions in a continuation prompt. |
| `plan.planning_order` | One of five planning schedules. The locked schedules require a non-empty caller schema and ordered `plan.keys`. |
| `plan.expose_estimate` | Whether extraction prompts include the soft estimate. |
| `plan.leaf_prompt` | `keyed-leaf-fetch-1` is the default. `keyed-leaf-fetch-2` asks for every matching record, states the expected count as a sentence, and allows an empty answer only when nothing matches. Refused in `flat`. |
| `plan.child_wording` | `parent` is the default. `canonical` derives each partitioned child's wording from the request and its own filters. |
| `plan.filter_enforcement` | `flag` keeps records that contradict their part's filter; `drop` removes them at the part, before merging. Both count them in `meta.filter_contradictions`. |
| `plan.range_filters` | Reads partition values written as integer spans, on an integer column, as range filters. Needs `partitioned` and `plan.leaf_prompt` `keyed-leaf-fetch-2`. |
| `plan.partition_guardrails` | Sends proposal rules against identifier, measurement, placeholder and catch-all values, and refuses a numeric key bucketed like a measurement. Needs `partitioned`. |
| `plan.split_policy` | `evidence` is the default in `partitioned`. It asks the root first, with its continuations, and splits only when that answer was cut off, was still growing when the continuations ran out, failed, came back empty although records are expected, or has fewer than half the records an estimate above `plan.split_threshold_rows` expects. Otherwise that answer is the result, and `meta.split_decision` says which. The root's asks count in the bound. `always` splits the root whatever it holds. `evidence` needs `partitioned`. |
| `plan.split_threshold_rows` | Under `evidence`, the estimate above which a short probe answer splits. Default `120`. |

`plan.leaf_prompt`, `plan.child_wording`, `plan.filter_enforcement`, `plan.range_filters` and `plan.partition_guardrails` are experimental. Check the returned records and diagnostics; these planning settings do not establish completeness.

These are the 21 standard planning controls shared with Python and the CLI. The MCP schema intentionally does not expose `plan.max_excluded_keys`, `plan.distribution_search`, `plan.identity_partitions`, or `plan.max_strategy_candidates`, and `llmdb_query` returns only records and meta, not the direct-Python planning receipt.

Non-flat calls also spend on the selected planning schedule, and may ask the whole request first and make bounded proposal or entity enumeration calls before leaf extraction. The library admits those calls incrementally against the shared budget; `plan.max_continuations` is not a bound on the whole request.

## Argument names, and the CLI's flags

The `limits.*` and `plan.*` keys are the library's own property names, spelled here exactly as a source and the CLI's profile file spell them. A renamed key at this boundary would be read by nothing, because the library validates the keys it is given.

The `blue-llmdb` CLI spells the same settings as hyphenated flags, because a dot is not usable in a long option. The mapping is one to one.

| MCP argument | CLI flag |
|---|---|
| `max_records` | `--max-records` |
| `limits.max_cost` | `--limit-cost` |
| `limits.max_seconds` | `--limit-seconds` |
| `limits.max_calls` | `--limit-calls` |
| `plan.mode` | `--plan-mode` |
| `plan.max_continuations` | `--plan-continuations` |
| `plan.max_leaves` | `--plan-leaves` |
| `plan.key` | `--plan-key` |
| `plan.keys` | `--plan-keys` |
| `plan.exact_rows` | `--plan-exact-rows` |
| `plan.max_exclusion_bytes` | `--plan-exclusion-bytes` |
| `plan.partition_key` | `--plan-partition-key` |
| `plan.max_depth` | `--plan-depth` |
| `plan.max_partitions` | `--plan-partitions` |
| `plan.target_rows_per_leaf` | `--plan-target-rows` |
| `plan.schema_mode` | `--plan-schema-mode` |
| `plan.planning_order` | `--plan-planning-order` |
| `plan.expose_estimate` | `--plan-expose-estimate` / `--no-plan-expose-estimate` |
| `plan.leaf_prompt` | `--plan-leaf-prompt` |
| `plan.child_wording` | `--plan-child-wording` |
| `plan.filter_enforcement` | `--plan-filter-enforcement` |
| `plan.range_filters` | `--plan-range-filters` / `--no-plan-range-filters` |
| `plan.partition_guardrails` | `--plan-partition-guardrails` / `--no-plan-partition-guardrails` |
| `plan.split_policy` | `--plan-split-policy` |
| `plan.split_threshold_rows` | `--plan-split-threshold-rows` |

`max_records` maps to `Limits.max_rows` and uses the tighter of the argument and any configured row ceiling for that call. The MCP surface has no separate `limits.max_rows` argument; the server refuses that spelling to avoid two names for the same setting.

## What this server does not expose

| Not exposed | Why |
|---|---|
| `profile add/remove/list/config/inspect/select` | CLI-local state under `~/.blue-llmdb/`, written on the operator's disk. `profile config` also accepts a credential, which is not something an agent call should write. |
| `model select` | The same CLI-local state. A server started per client session has nowhere to put a selection. |
| The `llm.*` request settings as call arguments | A model, its allowance and its prices are the operator's choice, and a cost ceiling is claimed against them, so no call argument sets them. The server reads them from its own environment instead, under the CLI's variable names: `BLUE_DATA_LLM_MODEL` and the `BLUE_DATA_LLM_SOURCE_*` settings listed in [Configuration](#configuration). |
| A `--format` equivalent | An MCP tool's wire format is fixed JSON by protocol. `--format table/json/nl/csv` is a CLI concern and is deliberately not mirrored here. |
| MCP resources | This server is tools-only. The price table contains 27 names with no paging or subscription behavior, and each other read is either a live provider call, which a client must not be able to trigger by prefetching context, or a fact already returned by a tool. |
| The library's registry surface (`fetch_schema`, `fetch_databases`, `fetch_database_collections` and the rest) | They exist so a registry sync has a hierarchy to walk. `fetch_databases()` returns one synthetic name and `fetch_schema()` returns `{}`; there is nothing for an agent to read. |
| `translate_nl_to_native_query` | Natural language is already the native query form. This compatibility method returns the query unchanged, or `None` when it is empty; it has no separate translation for an agent to request. |
| `connect()` and `disconnect()` | The source holds no persistent connection. `connect()` returns `{}` and `disconnect()` does nothing, so there is no connection lifecycle to expose as MCP tools. |
| The `DataSource` orchestrator-tool surface (`get_nl_tools`, `execute_nl_tool`, `execute_query_nl`) | That is how Blue's own orchestrator drives a source, and it is already a tool-dispatch layer. Putting it behind an MCP tool would nest one tool protocol inside another and give an agent two routes to the same fetch. `llmdb_query` is that fetch. |

## Ceilings

Three axes bound what a call may spend: `limits.max_cost` in dollars, `limits.max_seconds`, and `limits.max_calls`, which counts retries. Each provider attempt is claimed before contact. With no answered extraction response and no retained records, a ceiling refusal returns no records with `outcome=stopped_by_limit`; after a failed attempt, a call-count refusal of the retry instead raises `TransportError`. If an extraction response has already been parsed, either failure retains its records with `outcome=partial` and a gap for the failed part. Limit results name the axis in the stop fields.

`max_records` caps returned rows; it is not a provider-call or cost ceiling. Non-flat plans, continuations, and retries can make additional calls.

Every cost ceiling must meet a minimum. The bound reserves the full output allowance at the model's output rate, including any long-context output multiplier and OpenAI's 10% regional uplift. A `limits.max_cost` below `max_cost_per_attempt` therefore refuses every call before it starts. Use `llmdb_estimate` to obtain that figure. The output allowance is controlled by the deployment setting `BLUE_DATA_LLM_SOURCE_MAX_TOKENS`; no call argument changes it.

A cost ceiling also needs rates for the model, from the price table or from the `BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN` and `BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN` this deployment was started with. Ask `llmdb_inspect_model` first: `cost_ceiling_supported: false` means `limits.max_cost` will refuse every call on this server, whatever value it is given.

An operator can set ceilings for every call with `BLUE_DATA_LLM_SOURCE_LIMIT_COST`, `BLUE_DATA_LLM_SOURCE_LIMIT_CALLS` and `BLUE_DATA_LLM_SOURCE_LIMIT_SECONDS` (see [Configuration](#configuration)). A call's own `limits.*` argument can lower one of these for that call but never raise or remove it: the ceiling that applies is the lower of the two. Without an operator ceiling, a call that names none has none, and a ceiling one call sets does not carry to the next. The library's ceilings are described in [lib/README.md](../../lib/README.md) and stated in full in [lib/COMPATIBILITY.md](../../lib/COMPATIBILITY.md).

## Errors

Every tool follows the same error rule. A call that produces no result is an MCP-level error with `isError: true`. A completed call with a readable result is successful at the protocol level; partial results and stops are represented in the result fields.

Error text uses `Kind: message`. MCP argument validation identifies the field and constraint. Below-minimum errors include the supplied number; enum errors list accepted values without repeating the submitted value, and unknown keys are quoted. Deployment validation does not repeat setting values. Library failures use fixed text by error kind; provider and unexpected failures do not include exception details. Credential-shaped content in argument names is replaced with `***`.

| Kind | Raised for |
|---|---|
| `InvalidArguments` | An argument the advertised schema refuses: an unknown key, a wrong type, a value below a declared minimum, a blank value where the schema declares a pattern, a repeated entry in an array declared unique, a value outside a declared enum, a missing required argument, or a withdrawn setting. Fix the call. |
| `UnknownTool` | A tool name this server does not serve. The message names the ones it does. |
| `QueryError`, `ConfigError`, `TransportError`, `ParseError` | The library refused or failed. Known validation cases give fixed guidance; provider and parse failures have stable messages. |
| `BudgetExceeded` | A configured limit stopped the call without a readable result. The fixed message is `Configured limit reached.` |
| `LlmSourceError`, `InternalError` | Other library or server failures. The message contains no underlying exception text. |

```text
ConfigError: BLUE_DATA_LLM_SOURCE_MAX_TOKENS must be a whole number of at least 1.
```

Caller mistakes such as an invalid `plan.leaf_prompt` for flat mode, `plan.keys` outside requested `attrs`, or a locked planning order without its required schema and keys receive fixed `InvalidArguments` guidance naming the field. When a provider failure raises `TransportError`, the tool returns `TransportError: Provider request failed` with a fixed category in parentheses if structured error data identifies `authentication`, `network`, `rate_limit`, or `model_not_found`; otherwise it remains generic. Raw provider text is not returned. Malformed JSON-RPC can fail in the SDK before a tool handler runs and receives an SDK protocol error rather than a tool `Kind: message` error.

That one is a deployment message: the variable is set where the server is started, not on this surface, and `llmdb_query`, `llmdb_estimate` and `llmdb_inspect_model` fail with it until the variable is fixed. See [Configuration](#configuration).

In internal builds before 0.2.0.0 an argument refusal was also reported as `ConfigError`, so "unknown argument" and "no model configured" shared one prefix with two different remedies. They are told apart now.

These are payload facts rather than errors:

| Situation | Where it appears |
|---|---|
| An empty or missing query | `meta.outcome: empty_query`, on a successful call with no records |
| A budget stop with a readable result | `meta.outcome: stopped_by_limit` or `partial`, with the axis in `meta.stopped_by`. The records obtained before the stop are in the payload |
| A cost that cannot be bounded | `bounded: false` with `reason`, on `llmdb_estimate` |
| A model with no published price | `priceable: false` with null rates, on `llmdb_inspect_model` |

A missing or wrong provider credential fails the provider request, not configuration validation. The provider SDK handles authentication. With no answered extraction response and no retained records, an exhausted retry policy or a `max_calls` refusal after the failure raises `TransportError` with a fixed MCP message. If `max_cost` or `max_seconds` refuses that retry, or a ceiling refuses the first attempt, the result has `outcome: stopped_by_limit`. If an extraction response has already been parsed, the result is partial with a gap for the failed part. The library reads credential environment values only to mask diagnostic text.

## Result shapes

On success, each tool returns one MCP text content block containing a JSON object. SDK clients parse the block's `text`; this server does not populate `structuredContent`. Its fields correspond to the library result wrapped by the tool. Only `llmdb_query` has a `meta` object, because only `LlmSource.fetch` produces one. There is no shared envelope around the five.

## Install and run

From the checkout root, with `blue-data` beside it, use the root `.venv`:

```bash
python3 -m venv .venv
source .venv/bin/activate
./platform/llm-mcp/scripts/install.sh
command -v blue-data-llm-mcp
```

If `.venv` already exists, activate it and run only the installer. It installs `blue-data`, the library's `dev` and `llm` extras, and the MCP package's `dev` extra. Alternatively, use Conda from the same checkout root:

The [installation guide](../../docs/installation.md) is the reference for these Conda steps.

Run these commands from the `blue-data-llm-source` checkout with `blue-data` beside it. Create the environment once:

```bash
ls -d ../blue-data/lib &&
conda create --name blue-llmdb python=3.12 pip
```

Then activate it and install in dependency order:

```bash
conda activate blue-llmdb &&
python -m pip install -e ../blue-data/lib &&
python -m pip install -e "./lib[llm]" &&
python -m pip install -e ./platform/llm-mcp &&
command -v blue-data-llm-mcp
```

If the environment already exists, start at `conda activate blue-llmdb`. The last command prints the server's absolute path. Set it as your MCP client's command:

```json
{
  "mcpServers": {
    "blue-data-llm": {
      "command": "/absolute/path/from/command-v/blue-data-llm-mcp",
      "env": {"BLUE_DATA_LLM_SOURCE_LIMIT_COST": "0.50"}
    }
  }
}
```

After setting the ceiling, pass `OPENAI_API_KEY` through the client's environment or secret settings. A desktop client may not inherit variables from your shell. The client starts the server over stdio; the server opens no port. Use `blue-data-llm-mcp --help` or `--version` to inspect the command. Unsupported command options exit 2 with fixed guidance. Without an option, `blue-data-llm-mcp` waits for protocol messages on standard input.

To choose a model, output allowance and cost ceiling, add these variables to the server environment:

```json
{
  "mcpServers": {
    "blue-data-llm": {
      "command": "/absolute/path/from/command-v/blue-data-llm-mcp",
      "env": {
        "BLUE_DATA_LLM_MODEL": "gpt-6-sol",
        "BLUE_DATA_LLM_SOURCE_MAX_TOKENS": "8000",
        "BLUE_DATA_LLM_SOURCE_LIMIT_COST": "0.50"
      }
    }
  }
}
```

`python -m blue_data_llm_mcp.server` runs the same server from a checkout. Neither this MCP package nor its `blue-data` dependency is available from the public package index, so install from the checkouts. You can also run `platform/llm-mcp/scripts/install.sh` in an active environment; it installs the development extras and accepts `BLUE_DATA_PATH` for a non-sibling checkout. [Installation](../../docs/installation.md) covers `venv` and the component-local `.venv` used by Pyright.

## Configuration

This server reads no configuration file and no CLI profile. It reads these variables from the environment its client starts it with, under the names the CLI uses for the same settings:

| Variable | Setting | Read as |
|---|---|---|
| `BLUE_DATA_LLM_MODEL` | `llm.model` | a model name |
| `BLUE_DATA_LLM_SOURCE_REASONING_EFFORT` | `llm.reasoning_effort` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh` or `max` |
| `BLUE_DATA_LLM_SOURCE_MAX_TOKENS` | `llm.max_tokens` | a whole number |
| `BLUE_DATA_LLM_SOURCE_TEMPERATURE` | `llm.temperature` | a number |
| `BLUE_DATA_LLM_SOURCE_TIMEOUT` | `llm.timeout` | seconds |
| `BLUE_DATA_LLM_SOURCE_MAX_PARALLEL` | `llm.max_parallel` | a whole number |
| `BLUE_DATA_LLM_SOURCE_CACHE` | `llm.cache` | `1`, `true`, `yes`, `on`, or `0`, `false`, `no`, `off` |
| `BLUE_DATA_LLM_SOURCE_CACHE_DIR` | `llm.cache_dir` | a directory; a leading `~` and environment variables are expanded |
| `BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN` | `llm.price_per_input_token` | USD per token |
| `BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN` | `llm.price_per_output_token` | USD per token |
| `BLUE_DATA_LLM_SOURCE_LIMIT_COST` | `limits.max_cost`, for every call | dollars, zero or more |
| `BLUE_DATA_LLM_SOURCE_LIMIT_CALLS` | `limits.max_calls`, for every call | a whole number, zero or more |
| `BLUE_DATA_LLM_SOURCE_LIMIT_SECONDS` | `limits.max_seconds`, for every call | seconds, zero or more |

The server reads its process environment on every call. Restart the MCP client or server process after changing the client configuration; an already-running process does not inherit later shell changes. An unset variable leaves the library default in place. A blank `LIMIT` variable fails the call with a `ConfigError`; zero is a valid ceiling. Other blank variables keep their defaults, so with none set the server runs `gpt-6-luna` at medium reasoning with a 25,000-token allowance. A value that cannot be read as its type, or that the library refuses, fails that call with a `ConfigError` naming the variable and what it must be, never the value itself, and the server keeps running. A configured model name the package does not publish, and the cache directory, are masked as `***` wherever they would appear in a result or an error, since a deployment setting can hold anything and a caller's transcript is not the operator's to control.

The rate and allowance settings are not masked. Any MCP caller can work out `BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN`, `BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN` and `BLUE_DATA_LLM_SOURCE_MAX_TOKENS` from `max_output_tokens`, from two `llmdb_estimate` results, or from `meta.cost`. Do not put a confidential rate in them. The three `LIMIT` variables are operator ceilings: a call's `limits.*` argument applies only where it is lower, so an agent can tighten a ceiling for one call but not loosen it. No other `BLUE_DATA_*` variable is read: the planning settings stay per-call arguments, and a call's `plan.*` arguments apply to that call only.

The provider SDK reads `OPENAI_API_KEY` from the server process environment for authentication. The library reads values held under credential environment names only to mask diagnostics; no tool accepts a credential as an argument. Text about a call passes through `redact`, including errors, model names, requested attributes, rewritten queries, unchecked types, and unbounded-estimate reasons. Returned records are the query result and retain their values.

The `blue-llmdb` CLI stores profiles and applies its own environment-variable precedence chain under `~/.blue-llmdb/`. The MCP server reads none of that state, only the variables above. Both interfaces use the property names shown in the mapping table above.

## Layout

`tools.py` defines the tools and translates their arguments without importing `mcp`. `server.py` implements the transport and is the only module that imports `mcp`. This separation lets tests call the tools directly without the server or `mcp` installed, including in CI.

## Tests

Run these commands from `platform/llm-mcp/` in the active environment.

```bash
python3 -m pytest tests/unit -q
python3 -m ruff format --check . && python3 -m ruff check .
```

For the type check, create a `.venv` in this directory before installing. `pyproject.toml` points `pyright` at it, and without one pyright reports every import as unresolved instead of real typing errors:

```bash
python -m venv .venv && source .venv/bin/activate
```

Tests compare the documented tool names, schemas, enums, and JSON examples with the `tools/list` and `tools/call` handlers.
