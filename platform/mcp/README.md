# blue-data-llm-mcp

An MCP server over `blue-data-llm-source`. It exposes three tools — a fetch, a cost estimate, and an info call — and does nothing else: arguments in, library call, JSON result out. Validation, alignment, and budgets all belong to the library, and its error messages are written to be read by a model, so they are passed through rather than reworded.

**A ceiling here is per call, and a call that sets none has none.** `llm_fetch` takes `limits.max_cost`, `limits.max_seconds`, and `limits.max_calls`, and `llm_estimate` says what a request could cost before it runs. But the server still constructs its source with no properties, so nothing is configured out of band on the agent's behalf: an agent that omits the ceilings can spend whatever the configured provider charges. That is what to weigh before handing this transport a live credential. [Ceilings](#ceilings) below has the detail and the alternative.

## Decomposition

`llm_fetch` answers with one model call by default. A large ask can be split into parts, each
asked separately and merged on the way back, at a call per part. Nothing selects a shape for
the caller.

| Argument | Meaning |
|---|---|
| `plan.mode` | `flat` is the one-completion default; `partitioned` builds a bounded recursive tree; `row_by_row` asks per identified entity. An unknown value is refused. |
| `plan.max_continuations` | How many times a part may ask again for records it did not get. `0` by default, so each part asks once. It bounds asks per part, not provider attempts or planning calls; each ask may still be retried up to `limits.max_retries` times, three attempts by default. |
| `plan.max_leaves` | The most parts a plan may hold — the term that bounds its worst-case cost. |
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

These are the 14 standard planning controls shared with Python and the CLI. The MCP schema intentionally
does not expose `plan.distribution_search`, `plan.identity_partitions`, or
`plan.max_strategy_candidates`, and `llm_fetch` returns only records and meta, not the direct-Python
planning receipt.

Non-flat calls also spend on the selected planning schedule and may make bounded proposal or entity
enumeration calls before leaf extraction. The library admits those calls incrementally against the
shared budget; `plan.max_continuations` is not a bound on the whole request.

The result always carries `gaps`, `stop_reasons`, `requested_attrs`, `row_estimate`,
`cardinality_estimate`, `primary_keys`, `observed_key_count`, `marginal_key_yields`, and
`incomplete_key_records`. `row_estimate` is the accepted non-flat bootstrap root hint or JSON
`null`. `gaps` reports an observed failed or interrupted part, a ceiling, or discarded output; a
valid empty answer alone does not create one. It is present even when empty, so a program branches
on what it says rather than on whether the key arrived. `stop_reasons` retains simultaneous limits
in precedence order; `stopped_by`, when set, is its first member.

## Install and run

```bash
scripts/install.sh              # from a checkout
blue-data-llm-mcp
```

`pip install blue-data-llm-mcp` works only where `blue-data` is resolvable, which means an internal index. It is a core dependency of the library underneath and is not on the public one, so a plain install fails to resolve it. From a checkout, `scripts/install.sh` puts it in place first; set `BLUE_DATA_PATH` if it does not sit alongside this repository.

This package points `pyright` at a `.venv` in its own directory, matching the sibling subrepos. Create and activate one before installing, or the type gate reports every import as unresolved instead of reporting real typing errors:

```bash
python -m venv .venv && source .venv/bin/activate
```

The server speaks MCP over stdio, so it is started by the client rather than by hand. Point a client at it the usual way:

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

## Tools

### `llm_fetch`

Fetch structured records from a language model's own knowledge.

| Input | Type | Required | Meaning |
|---|---|---|---|
| `query` | string | no | What to ask for, in plain language. Omitted or empty, the call succeeds with no records and `outcome: empty_query` |
| `attrs` | array | no | Attributes every record must carry: `"country"` or `{"name": "year", "type": "int"}` |
| `context` | string | no | Text that clarifies the query or an attribute name |
| `max_records` | integer, minimum 1 | no | Stop after this many records |
| `limits.max_cost` | number, minimum 0 | no | Refuse a call that would take this run's spend past this many dollars |
| `limits.max_seconds` | number, minimum 0 | no | Refuse a call once the run has been going this long |
| `limits.max_calls` | integer, minimum 0 | no | Refuse more than this many provider calls, retries included |
| `plan.mode` | string | no | `flat` (default), `partitioned`, or `row_by_row` |
| `plan.max_continuations` | integer, minimum 0 | no | Extra asks allowed per leaf |
| `plan.max_leaves` | integer, minimum 1 | no | Maximum terminal slices |
| `plan.key` | string | no | Record identity attribute |
| `plan.partition_key` | string | no | Preferred root split dimension |
| `plan.max_depth` | integer, minimum 0 | no | Deepest partition level |
| `plan.max_partitions` | integer, minimum 0 | no | Maximum children proposed per node |
| `plan.target_rows_per_leaf` | integer, minimum 1 | no | Row-estimate hint for descendant splits |
| `plan.schema_mode` | string | no | `preserve` or `enrich` |
| `plan.keys` | array of strings | no | Ordered primary-key fields for composite identity |
| `plan.exact_rows` | integer, minimum 0 | no | Single-leaf hard target; a multi-leaf plan reports `exact_rows_not_leaf_bound` instead |
| `plan.max_exclusion_bytes` | integer, minimum 0 | no | Encoded byte cap for whole-key exclusions |
| `plan.planning_order` | string | no | One of five planning schedules; the locked schedules require a non-empty caller schema and ordered `plan.keys` |
| `plan.expose_estimate` | boolean | no | Include or omit the soft estimate in extraction prompts |

The `limits.*` keys are the library's own property names, spelled here exactly as they are on a source or on the command line. See [Ceilings](#ceilings).

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
    "gaps": [],
    "stop_reasons": [],
    "requested_attrs": ["country", "accession_year"],
    "row_estimate": null,
    "cardinality_estimate": null,
    "primary_keys": [],
    "observed_key_count": 0,
    "marginal_key_yields": [],
    "incomplete_key_records": 0
  }
}
```

`outcome` is always present and says why the result looks the way it does: `ok`, `empty_query`, `model_returned_none`, `filtered_to_empty`, `partial`, `stopped_by_limit`, or `truncated`. It is what tells an empty record list that the model found nothing apart from one a limit cut short.

`gaps`, `stop_reasons`, `requested_attrs`, `row_estimate`, `cardinality_estimate`, `primary_keys`,
`observed_key_count`, `marginal_key_yields`, and `incomplete_key_records` are always present, as
above. `meta` also carries `normalization_collisions`, `record_origins`, `asked`, format diagnostics,
`truncated_to`, `stopped_by`, and `accounting_known` when applicable. Read `accounting_known` before
`cost`: when false, a zero cost means unmeasured. A budget stop is a result rather than a tool error.

### `llm_estimate`

What one **flat** `llm_fetch` of the same request could cost, without calling the model. It reads no `plan.*` setting, so a request with parts or continuations can cost several times this figure — size a ceiling for a split request from its part count. Same `query`, `attrs`, and `context`, and the same three `limits.*` keys: a call ceiling caps the attempts a fetch may make, so it caps what a fetch can cost. There is no `max_records`, because a row cap cannot change the answer.

```json
{"query": "the member states of the European Union", "attrs": ["country"]}
```

```json
{
  "bounded": true,
  "max_cost": 0.12924,
  "max_cost_per_attempt": 0.04308,
  "max_attempts": 3,
  "reason": null,
  "model": "gpt-4o",
  "prompt_bytes": 784,
  "max_output_tokens": 4096
}
```

`max_cost` bounds the whole of a `flat` fetch rather than predicting it, and reads no `plan.*` setting: `max_cost_per_attempt` accumulated `max_attempts` times, with the entire output allowance reserved at the model's output rate and the prompt bounded by its encoded length. A typical call costs a fraction of it. It is the same figure the reservation claims, so a request that estimates inside a ceiling is not refused when it runs, and a ceiling set to exactly `max_cost` admits every attempt the figure accounted for. Both are computed from this package's static price table rather than from anything the provider reports, so they agree with each other and not necessarily with an invoice. Neither cost is rounded, for the same reason — a figure a run can exceed is not a bound.

`bounded` is `false` when no bound can be established, which happens for a model this library has no rates for and was given none. `reason` then says what is missing. That is a successful call rather than an error, because asking what something might cost is reasonable even where the answer cannot be given. Nothing on this path contacts the provider.

### `llm_info`

No inputs. Returns the protocol, the variant, and the version of the library underneath:

```json
{"protocol": "llm", "variant": "openai", "version": "1.2.0"}
```

## Ceilings

Three axes bound what a call may spend: `limits.max_cost` in dollars, `limits.max_seconds`, and
`limits.max_calls`, which counts retries. Each provider attempt is claimed before contact. A refusal
before any extraction answer returns no records with `outcome=stopped_by_limit`; a later refusal
after an extraction answered retains those records with `outcome=partial`. In both cases the stop
fields name the axis.

`max_records` is not one of them. It caps the rows in the result, not the spend: the model is asked once and the surplus records are dropped on arrival.

There is a floor a cost ceiling has to clear. The bound reserves the whole output allowance at the model's output rate, so a `limits.max_cost` below `max_cost_per_attempt` refuses every call before making one. Ask `llm_estimate` for that figure rather than guessing at it; lowering the allowance itself needs `llm.max_tokens`, which is a library setting and is not exposed here.

The ceilings are per call and nothing sets them for the agent. The server constructs its source with no properties, so a call that names no ceiling has none, and a ceiling one call sets does not carry to the next. A deployment that cannot leave that to the agent embeds the library and configures `limits.*` on the source it builds, or wraps this server in a process that does. The library's ceilings are described in [lib/README.md](../../lib/README.md) and stated in full in [lib/COMPATIBILITY.md](../../lib/COMPATIBILITY.md).

## Errors

Every `LlmSourceError` becomes a failed tool call whose text is `Kind: message`, with the library's message unchanged — `QueryError`, `ConfigError`, `TransportError`, or `ParseError`. Asking for a tool that does not exist returns `UnknownTool` and names the ones that do.

```text
ConfigError: no model configured (set llm.model)
```

An empty or missing query is not an error. It comes back as a successful call with no records and `outcome: empty_query`.

A missing or wrong provider credential arrives as `TransportError`, not `ConfigError`. The library never reads a credential, so it cannot report one as missing; the provider SDK reports it as a call that failed, and the message names the environment variable it looked for.

## Layout

`tools.py` holds the tool definitions and the argument translation and imports nothing from `mcp`. `server.py` holds the transport and is the only module that does. That split is what lets the tests run against the tools directly, with no server and no `mcp` installed, which is also how they run in CI.

## Tests

```bash
python3 -m pytest tests/unit -q
python3 -m ruff format --check . && python3 -m ruff check .
```
