# blue-data-llm-mcp

An MCP server over `blue-data-llm-source`. It exposes three tools — a fetch, a cost estimate, and an info call — and does nothing else: arguments in, library call, JSON result out. Validation, alignment, and budgets all belong to the library, and its error messages are written to be read by a model, so they are passed through rather than reworded.

**A ceiling here is per call, and a call that sets none has none.** `llm_fetch` takes `limits.max_cost`, `limits.max_seconds`, and `limits.max_calls`, and `llm_estimate` says what a request could cost before it runs. But the server still constructs its source with no properties, so nothing is configured out of band on the agent's behalf: an agent that omits the ceilings can spend whatever the configured provider charges. That is what to weigh before handing this transport a live credential. [Ceilings](#ceilings) below has the detail and the alternative.

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
    "elapsed_seconds": 0.31
  }
}
```

`outcome` is always present and says why the result looks the way it does: `ok`, `empty_query`, `model_returned_none`, `filtered_to_empty`, `stopped_by_limit`, or `truncated`. It is what tells an empty record list that the model found nothing apart from one a limit cut short.

`meta` carries eight more keys when there is something to report: `type_mismatches` for values that did not match a declared attribute type, `key_collisions` for values dropped because two returned keys answered the same attribute, `unmapped_keys` for keys the model returned that no requested attribute matched, `remapped_keys` for the ones it renamed on the way in, `truncated_to` and `stopped_by` when a limit ended the run, `unchecked_types` for declared type names this library cannot check, and `accounting_known` when the figures do not describe provider work. Read that last one before reading `cost`: when it is `false`, a cost of `0` means "not measured" rather than "free". A budget stop is not an error — it comes back as an empty record list with `stopped_by` naming the limit.

### `llm_estimate`

What one `llm_fetch` of the same request could cost, without calling the model. Same `query`, `attrs`, and `context`, and the same three `limits.*` keys: a call ceiling caps the attempts a fetch may make, so it caps what a fetch can cost. There is no `max_records`, because a row cap cannot change the answer.

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

`max_cost` bounds the whole fetch rather than predicting it: `max_cost_per_attempt` accumulated `max_attempts` times, with the entire output allowance reserved at the model's output rate and the prompt bounded by its encoded length. A typical call costs a fraction of it. It is the same figure the reservation claims, so a request that estimates inside a ceiling is not refused when it runs, and a ceiling set to exactly `max_cost` admits every attempt the figure accounted for. Both are computed from this package's static price table rather than from anything the provider reports, so they agree with each other and not necessarily with an invoice. Neither cost is rounded, for the same reason — a figure a run can exceed is not a bound.

`bounded` is `false` when no bound can be established, which happens for a model this library has no rates for and was given none. `reason` then says what is missing. That is a successful call rather than an error, because asking what something might cost is reasonable even where the answer cannot be given. Nothing on this path contacts the provider.

### `llm_info`

No inputs. Returns the protocol, the variant, and the version of the library underneath:

```json
{"protocol": "llm", "variant": "openai", "version": "1.1.1"}
```

## Ceilings

Three axes bound what a call may spend: `limits.max_cost` in dollars, `limits.max_seconds`, and `limits.max_calls`, which counts retries. Each is claimed before the provider is contacted, so a call that would cross one is refused rather than accounted for afterwards. A crossed ceiling is not an error — the records list comes back empty, `outcome` is `stopped_by_limit`, and `stopped_by` names the axis.

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
