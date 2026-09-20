# blue-data-llm-mcp

This package exposes `blue-data-llm-source` through an MCP server named `blue-data-llm`. The server uses stdio and provides five tools and no resources. It validates each tool call against the advertised schema, translates the arguments into a library call, and returns JSON. The library remains responsible for domain validation, configuration, alignment, and budgets. Library errors are returned with their original wording so a model-driven client can act on them.

**Each ceiling applies to one call. A call that sets no ceiling is unbounded.** `llmdb_query` accepts `limits.max_cost`, `limits.max_seconds`, and `limits.max_calls`, while `llmdb_estimate` calculates a cost bound before a query runs. The server constructs its source with no default properties, so omitted ceilings are not supplied elsewhere and the provider may charge without a per-call spending cap. Configure the client accordingly before exposing a provider credential. See [Ceilings](#ceilings) for details and alternatives.

## Tools

| Tool | What it does | Contacts the provider |
|---|---|---|
| `llmdb_query` | Fetch structured records from a language model's own knowledge. | yes |
| `llmdb_estimate` | Bound what one flat query of the same request could cost. | no |
| `llmdb_list_models` | List the model names this package carries a published price for. | no |
| `llmdb_inspect_model` | Report one model's rates and whether a cost ceiling can be bounded against it. | no |
| `llmdb_inspect_metadata` | Report the protocol, the variant, and the library version. | no |

The `llmdb_` prefix prevents collisions when a client loads several data-source servers into one tool namespace. The remainder of each name matches the corresponding `blue-llmdb` operation.

Versions before 2.0.0 exposed `llm_fetch`, `llm_estimate` and `llm_info`. Those names are removed rather than aliased; a client still calling one gets an `UnknownTool` error naming every tool that does exist.

### `llmdb_query`

Fetch structured records from a language model's own knowledge.

| Input | Type | Required | Meaning |
|---|---|---|---|
| `query` | string | no | What to ask for, in plain language. Omitted or empty, the call succeeds with no records and `outcome: empty_query` |
| `attrs` | array | no | Attributes every record must carry: `"country"` or `{"name": "year", "type": "int"}`. Each attribute once; the two spellings name the same attribute, and a repeat is refused before the model is reached |
| `context` | string | no | Text that clarifies the query or an attribute name |
| `max_records` | integer, minimum 1 | no | Stop after this many records |
| `limits.max_cost` | number, minimum 0 | no | Refuse a call that would take this run's spend past this many dollars |
| `limits.max_seconds` | number, minimum 0 | no | Refuse a call once the run has been going this long |
| `limits.max_calls` | integer, minimum 0 | no | Refuse more than this many provider calls, retries included |
| `plan.mode` | enum: `flat`, `partitioned`, `row_by_row` | no | How the ask is decomposed. `flat` is the default |
| `plan.max_continuations` | integer, minimum 0 | no | Extra asks allowed per leaf |
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

The rows are in schema order. The three `plan.*` sets above are closed: the schema declares a JSON Schema `enum` for each, and a value outside it is refused before the library is reached.

The `type` on an attribute is deliberately **not** closed. The library checks a value against a type name it recognises (`int`, `integer`, `float`, `number`, `bool`, `boolean`, `date`, `str`, `string`, `text`) and accepts any other name unchecked, reporting it once in `meta.unchecked_types`. Refusing an unrecognised name here would make that field unreachable over this transport.

Nothing is required. A missing query is a successful call with no records, not an error.

A call and its result:

```json
{
  "query": "the member states of the European Union",
  "attrs": ["country", {"name": "accession_year", "type": "int"}],
  "max_records": 5,
  "limits.max_cost": 0.25
}
```

```json
{
  "records": [
    {"country": "France", "accession_year": 1958}
  ],
  "meta": {
    "outcome": "ok",
    "model": "gpt-4o",
    "calls": 1,
    "prompt_tokens": 12,
    "completion_tokens": 34,
    "cost": 0.0012,
    "elapsed_seconds": 0.31,
    "row_estimate": null,
    "cardinality_estimate": null,
    "primary_keys": [],
    "observed_key_count": 0,
    "marginal_key_yields": [],
    "incomplete_key_records": 0,
    "gaps": [],
    "stop_reasons": [],
    "requested_attrs": ["country", "accession_year"]
  }
}
```

`outcome` is always present and explains the result: `ok`, `empty_query`, `model_returned_none`, `filtered_to_empty`, `partial`, `stopped_by_limit`, or `truncated`. Use it to distinguish a model-produced empty result from one caused by filtering, interruption, or a limit.

Every field in that sample is always present, so a program branches on what it says rather than on whether the key arrived. `gaps` reports an observed failed or interrupted part, a ceiling, or discarded output; a valid empty answer alone does not create one. `stop_reasons` retains simultaneous limits in precedence order. `row_estimate` is the accepted non-flat bootstrap root hint or JSON `null`.

`meta` also carries `normalization_collisions`, `unchecked_types`, `record_origins`, `asked`, `stopped_by`, `truncated_to`, `cache_hits`, `unmapped_keys`, `remapped_keys`, `type_mismatches`, `key_collisions`, and `accounting_known`, each when it applies. An absent key there means the thing did not happen. Read `accounting_known` before `cost`: when false, a zero cost means unmeasured. A budget stop is a result rather than a tool error.

### `llmdb_estimate`

What one **flat** `llmdb_query` of the same request could cost, without calling the model. It reads no `plan.*` setting, so a request with parts or continuations can cost several times this figure. Size a ceiling for a split request from its part count. Same `query`, `attrs`, and `context`, and the same three `limits.*` keys: a call ceiling caps the attempts a fetch may make, so it caps what a fetch can cost. There is no `max_records`, because a row cap cannot change the answer.

```json
{"query": "the member states of the European Union", "attrs": ["country"]}
```

```json
{
  "bounded": true,
  "max_cost": 0.1292025,
  "max_cost_per_attempt": 0.0430675,
  "max_attempts": 3,
  "reason": null,
  "model": "gpt-4o",
  "prompt_bytes": 779,
  "max_output_tokens": 4096
}
```

`max_cost` bounds the whole of a `flat` fetch rather than predicting it: `max_cost_per_attempt` accumulated `max_attempts` times, with the entire output allowance reserved at the model's output rate and the prompt bounded by its encoded length. A typical call costs a fraction of it. It is the same figure the reservation claims, so a request that estimates inside a ceiling is not refused when it runs, and a ceiling set to exactly `max_cost` admits every attempt the figure accounted for. Both are computed from this package's static price table, never from anything the provider reports, so they agree with each other and not necessarily with an invoice. Neither cost is rounded, for the same reason: a figure a run can exceed is not a bound.

`bounded` is `false` when no bound can be established, which happens for a model this library has no rates for and was given none. `reason` then says what is missing. That is a successful call rather than an error, because asking what something might cost is reasonable even where the answer cannot be given. Nothing on this path contacts the provider.

### `llmdb_list_models`

No inputs. The pricing bases this package carries a published price for, sorted. These are bases and not the whole set of boundable names: a dated variant prices against the base it extends, so `gpt-4o-2024-08-06` is bindable and is not on the list. A ceiling needs a listed name, a dated variant of one, or rates the deployment supplied; `llmdb_inspect_model` settles a single name without this ambiguity.

```json
{
  "models": [
    "claude-3-5-haiku",
    "claude-3-5-sonnet",
    "claude-3-opus",
    "gpt-3.5-turbo",
    "gpt-4",
    "gpt-4-turbo",
    "gpt-4o",
    "gpt-4o-mini",
    "o1",
    "o3-mini"
  ]
}
```

Names rather than rates. The price table is a snapshot of published list prices and goes stale; a name is a stable thing to publish, and `llmdb_inspect_model` gives the rates for one.

### `llmdb_inspect_model`

One model's rates, and whether this server could bound a `limits.max_cost` ceiling against it.

| Input | Type | Required | Meaning |
|---|---|---|---|
| `model` | string | **yes** | Model name to look up. Exact names and dated variants only; a longer name is a different model, not a variant of the one it starts with |

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
  "cost_ceiling_supported": true
}
```

| Field | Meaning |
|---|---|
| `priceable` | Whether the package's price table carries this model |
| `price_per_input_token`, `price_per_output_token` | Published list prices in USD per token, or `null` |
| `max_output_tokens` | The output allowance this server's source is configured with |
| `cost_ceiling_supported` | Whether a cost bound can be established on this deployment. It needs rates and a finite output allowance, so a priceable model on a source with no allowance is still `false`, and a model the table does not carry is `true` where the deployment supplied `llm.price_per_input_token` and `llm.price_per_output_token`. It is the same question `llmdb_estimate` answers as `bounded` |

A model the table does not carry answers with `priceable: false` and null rates. That is a successful call, the same way `bounded: false` is on an estimate.

### `llmdb_inspect_metadata`

No inputs. Returns the protocol, the variant, and the version of the library underneath:

```json
{"protocol": "llm", "variant": "openai", "version": "2.0.0"}
```

## Decomposition

`llmdb_query` answers with one model call by default. A large ask can be split into parts, each asked separately and merged on the way back, at a call per part. Nothing selects a shape for the caller.

| Argument | Meaning |
|---|---|
| `plan.mode` | `flat` is the one-completion default; `partitioned` builds a bounded recursive tree; `row_by_row` asks per identified entity. |
| `plan.max_continuations` | How many times a part may ask again for records it did not get. `0` by default, so each part asks once. It bounds asks per part, not provider attempts or planning calls; each ask may still be retried up to `limits.max_retries` times, three attempts by default. |
| `plan.max_leaves` | The most parts a plan may hold, the term that bounds its worst-case cost. |
| `plan.key` | The attribute identifying a record, so parts returning the same one merge. Must be among the requested attributes. |
| `plan.partition_key` | Preferred root split dimension. Descendants may choose another dimension. |
| `plan.max_depth` | Deepest partition level. Default `2`; `0` disables subdivision. |
| `plan.max_partitions` | Most children proposed for one node. Default `4`. |
| `plan.target_rows_per_leaf` | Uncertain row-count hint for descendant splitting. Default `50`; it is not a completeness target. |
| `plan.schema_mode` | `preserve` keeps declared attributes; `enrich` lets non-flat planning append inferred ones. |
| `plan.keys` | Ordered primary-key fields for composite identity. |
| `plan.exact_rows` | Hard target for a single-leaf plan. A multi-leaf plan does not apply it and reports `exact_rows_not_leaf_bound`. |
| `plan.max_exclusion_bytes` | Encoded byte cap for whole-key exclusions in a continuation prompt. |
| `plan.planning_order` | One of five planning schedules. The locked schedules require a non-empty caller schema and ordered `plan.keys`. |
| `plan.expose_estimate` | Whether extraction prompts include the soft estimate. |

These are the 14 standard planning controls shared with Python and the CLI. The MCP schema intentionally does not expose `plan.distribution_search`, `plan.identity_partitions`, or `plan.max_strategy_candidates`, and `llmdb_query` returns only records and meta, not the direct-Python planning receipt.

Non-flat calls also spend on the selected planning schedule and may make bounded proposal or entity enumeration calls before leaf extraction. The library admits those calls incrementally against the shared budget; `plan.max_continuations` is not a bound on the whole request.

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

`max_records` maps to `Limits.max_rows` and overrides any configured value for that call. The MCP surface has no separate `limits.max_rows` argument; the server refuses that spelling to avoid two names for the same setting.

## What this server does not expose

| Not exposed | Why |
|---|---|
| `profile add/remove/list/config/inspect/select` | CLI-local state under `~/.blue-llmdb/`, written on the operator's disk. `profile config` also accepts a credential, which is not something an agent call should write. |
| `model select` | The same CLI-local state. A server started per client session has nowhere to put a selection. |
| The `llm.*` request settings (`model`, `max_tokens`, `temperature`, `timeout`, `max_parallel`, `cache`, `cache_dir`, `price_per_input_token`, `price_per_output_token`) | Deployment configuration on this transport, not per-call arguments. The server builds its source from the process configuration the client started it with, and a cost ceiling is claimed against that configured model. |
| A `--format` equivalent | An MCP tool's wire format is fixed JSON by protocol. `--format table/json/nl/csv` is a CLI concern and is deliberately not mirrored here. |
| MCP resources | This server is tools-only. The price table contains ten names with no paging or subscription behavior, and each other read is either a live provider call, which a client must not be able to trigger by prefetching context, or a fact already returned by a tool. |
| The library's registry surface (`fetch_schema`, `fetch_databases`, `fetch_database_collections` and the rest) | They exist so a registry sync has a hierarchy to walk. `fetch_databases()` returns one synthetic name and `fetch_schema()` returns `{}`; there is nothing for an agent to read. |
| The `DataSource` orchestrator-tool surface (`get_nl_tools`, `execute_nl_tool`, `execute_query_nl`) | That is how Blue's own orchestrator drives a source, and it is already a tool-dispatch layer. Putting it behind an MCP tool would nest one tool protocol inside another and give an agent two routes to the same fetch. `llmdb_query` is that fetch. |

## Ceilings

Three axes bound what a call may spend: `limits.max_cost` in dollars, `limits.max_seconds`, and `limits.max_calls`, which counts retries. Each provider attempt is claimed before contact. A refusal before any extraction answer returns no records with `outcome=stopped_by_limit`; a later refusal after an extraction answered retains those records with `outcome=partial`. In both cases the stop fields name the axis.

`max_records` is not one of them. It caps the rows in the result, not the spend: the model is asked once and the surplus records are dropped on arrival.

There is a floor a cost ceiling has to clear. The bound reserves the whole output allowance at the model's output rate, so a `limits.max_cost` below `max_cost_per_attempt` refuses every call before making one. Ask `llmdb_estimate` for that figure rather than guessing at it; lowering the allowance itself needs `llm.max_tokens`, which is a deployment setting and is not exposed here.

A cost ceiling also needs rates for the model, from the price table or from the `llm.price_per_input_token` and `llm.price_per_output_token` this deployment was configured with. Ask `llmdb_inspect_model` first: `cost_ceiling_supported: false` means `limits.max_cost` will refuse every call on this server, whatever value it is given.

The ceilings are per call and nothing sets them for the agent. The server constructs its source with no properties, so a call that names no ceiling has none, and a ceiling one call sets does not carry to the next. A deployment that cannot leave that to the agent embeds the library and configures `limits.*` on the source it builds, or wraps this server in a process that does. The library's ceilings are described in [lib/README.md](../../lib/README.md) and stated in full in [lib/COMPATIBILITY.md](../../lib/COMPATIBILITY.md).

## Errors

Every tool follows the same error rule. A call that produces no result is an MCP-level error with `isError: true`. A completed call with a readable result is successful at the protocol level; partial results and stops are represented in the result fields.

Error text uses `Kind: message` and preserves the library's wording. Before the text is returned, credential-shaped content is replaced with `***` because provider exceptions can include request headers. For a value outside a declared set, the message identifies the argument and lists accepted values without repeating the rejected value. Unknown keys are quoted so callers can locate misspellings.

| Kind | Raised for |
|---|---|
| `InvalidArguments` | An argument the advertised schema refuses: an unknown key, a wrong type, a value below a declared minimum, a blank value where the schema declares a pattern, a repeated entry in an array declared unique, a value outside a declared enum, a missing required argument, or a withdrawn setting. Fix the call. |
| `UnknownTool` | A tool name this server does not serve. The message names the ones it does. |
| `QueryError`, `ConfigError`, `TransportError`, `ParseError` | The library refused or failed, and the kind is its exception class. Fix the request or the deployment, as the message says. |

```text
ConfigError: no model configured (set llm.model)
```

That one is a deployment message: the model is configured where the server is started, not on this surface. See [What this server does not expose](#what-this-server-does-not-expose).

Before 2.0.0 an argument refusal was also reported as `ConfigError`, so "unknown argument" and "no model configured" shared one prefix with two different remedies. They are told apart now.

These are payload facts rather than errors:

| Situation | Where it appears |
|---|---|
| An empty or missing query | `meta.outcome: empty_query`, on a successful call with no records |
| A budget stop | `meta.outcome: stopped_by_limit` or `partial`, with the axis in `meta.stopped_by`. The records obtained before the stop are in the payload, which is why `BudgetExceeded` is not in the table above |
| A cost that cannot be bounded | `bounded: false` with `reason`, on `llmdb_estimate` |
| A model with no published price | `priceable: false` with null rates, on `llmdb_inspect_model` |

A missing or wrong provider credential arrives as `TransportError`, not `ConfigError`. The library never reads a credential, so it cannot report one as missing; the provider SDK reports it as a call that failed, and the message names the environment variable it looked for.

## Result shapes

Each tool returns a JSON object carrying the library return type it wraps, and nothing more. Only `llmdb_query` has a `meta` object, because only `LlmSource.fetch` produces one. There is no shared envelope around the five.

## Install and run

```bash
scripts/install.sh              # from a checkout
blue-data-llm-mcp
```

`pip install blue-data-llm-mcp` works only where `blue-data` is resolvable, which means an internal index. It is a core dependency of the library underneath and is not on the public one, so a plain install fails to resolve it. From a checkout, `scripts/install.sh` puts it in place first; set `BLUE_DATA_PATH` if it does not sit alongside this repository.

This package points `pyright` at a `.venv` in its own directory. Create and activate one before installing, or the type check reports every import as unresolved:

```bash
python -m venv .venv && source .venv/bin/activate
```

The MCP client starts the server over stdio. The server opens no network port. Configure a client as follows:

```json
{
  "mcpServers": {
    "blue-data-llm": {
      "command": "blue-data-llm-mcp",
      "env": {"OPENAI_API_KEY": "..."}
    }
  }
}
```

`python -m blue_data_llm_mcp.server` runs the same thing from a checkout.

## Configuration

This server reads no configuration file and no `BLUE_DATA_*` environment variable. It constructs its source with no properties, so every setting is the library's default and every ceiling is whatever arrived with the call.

`OPENAI_API_KEY` in the snippet above belongs to the provider SDK. The SDK reads it from the server process environment; neither this package nor the library reads it directly, and no tool accepts a credential as an argument. Text about a call passes through `redact`, including errors, model names, requested attributes, rewritten queries, unchecked types, and unbounded-estimate reasons. A test examines every returned string and requires it to be redacted or explicitly exempted. Returned records are exempt because they are the query result; a token-shaped record value remains data.

The `blue-llmdb` CLI stores profiles and applies its own environment-variable precedence chain under `~/.blue-llmdb/`. The MCP server does not read that state because ceilings are explicit per-call inputs. A deployment that requires server-side default ceilings should embed the library. Both interfaces use the property names shown in the mapping table above.

## Layout

`tools.py` holds the tool definitions and the argument translation and imports nothing from `mcp`. `server.py` holds the transport and is the only module that does. That split is what lets the tests run against the tools directly, with no server and no `mcp` installed, which is also how they run in CI.

## Tests

```bash
python3 -m pytest tests/unit -q
python3 -m ruff format --check . && python3 -m ruff check .
```

Tests compare the documented tool names, schemas, enums, and JSON examples with the `tools/list` and `tools/call` handlers.
