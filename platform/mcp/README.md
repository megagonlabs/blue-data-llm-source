# blue-data-llm-mcp

An MCP server over `blue-data-llm-source`. It exposes two tools — a fetch and an info call — and does nothing else: arguments in, library call, JSON result out. Validation, alignment, and budgets all belong to the library, and its error messages are written to be read by a model, so they are passed through rather than reworded.

**This transport exposes no spending ceiling and no cost estimate.** `max_records` caps rows, not spend. An agent reaching the library this way can spend without a per-call cap and cannot size a request first, so this is not the transport to hand a live credential to when that matters. The reasons and the alternatives are in [Ceilings](#ceilings) below; closing the gap is a v1.1 item rather than an oversight.

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

A call and its result:

```json
{
  "query": "the member states of the European Union",
  "attrs": ["country", {"name": "accession_year", "type": "int"}],
  "max_records": 5
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

`meta` carries six more keys when there is something to report: `type_mismatches` for values that did not match a declared attribute type, `key_collisions` for values dropped because two returned keys answered the same attribute, `unmapped_keys` for keys the model returned that no requested attribute matched, `remapped_keys` for the ones it renamed on the way in, `truncated_to` and `stopped_by` when a limit ended the run. A budget stop is not an error — it comes back as an empty record list with `stopped_by` naming the limit.

## Ceilings

`max_records` is the only ceiling this server exposes, and it caps the rows in the result rather than what the call spends. There is no input for a cost, time, or call ceiling, and the server constructs its source with no properties, so none is configured out of band either. An agent calling `llm_fetch` can therefore run the underlying model as often as it likes, at whatever the configured provider charges.

The library's `LLM.ESTIMATE` op is not exposed either, so an agent over this transport cannot ask what a request would cost before making it. The two gaps are one gap: this server passes four arguments through and configures nothing.

That is worth knowing before pointing a client at this server with a live credential. A deployment that needs a spending ceiling embeds the library and configures `limits.*` on the source it builds, or wraps this server in a process that does. The library's ceilings are described in [lib/README.md](../../lib/README.md) and stated in full in [lib/COMPATIBILITY.md](../../lib/COMPATIBILITY.md).

### `llm_info`

No inputs. Returns the protocol, the variant, and the version of the library underneath:

```json
{"protocol": "llm", "variant": "openai", "version": "1.0.0"}
```

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
