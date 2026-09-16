# blue-data-llm-source

An LLM-as-data-source layer: a natural language request in, records shaped to a requested schema out, drawn from the model's own knowledge rather than from a store.

## What it does

- **Fetch** structured records for a question that has no table behind it: which countries belong to a trade bloc, which companies listed in a given year, what the standard container sizes are.
- **Shape** the result to requested attributes, with optional types, and check each value against the type it was given. A value that is present and fails its type is reported as a mismatch and kept, not dropped. An attribute the model omitted arrives as `None`, and so does an explicit JSON `null`; neither is a mismatch, and the result does not distinguish them. **An empty mismatch list therefore means "nothing present failed its type", not "every requested field is populated"** — check for `None` yourself if a field is required.
- **Report** what came back: the model used, calls, tokens, cost, elapsed time, keys that could not be mapped, keys that collided, and which limit stopped a run.
- **Bound** every request on four axes at once: rows, time, API cost, and call count. Calls, time, and cost are claimed before the provider is contacted; rows are capped once the records are parsed. A call's price is not known until it returns, so the cost claim is an upper bound computed against the price table this package carries, reconciled against the real figure afterwards, and a cost ceiling whose bound cannot be established is refused rather than claimed against a guess. **That table is a static snapshot of published list prices with no effective date and no freshness check**, so the bound is only as current as the table: if a listed price rises, or a dated model alias bills differently, or a proxy applies its own rates, the reservation is too small and the difference shows up at settlement rather than being prevented. Treat `limits.max_cost` as a control against known rates, not as a hard billing stop — and supply `llm.price_per_input_token` and `llm.price_per_output_token` to bound against rates you maintain. The ceiling travels with the request: a `limits.*` key in the properties of one call binds that call and does not leak into the next. Pass a `Budget` of your own when you want one total across a sequence of requests instead. The time axis is the weakest of the four: it bounds admission and cooperative waiting, not work that never yields. [What the time ceiling actually bounds](lib/README.md#what-the-time-ceiling-actually-bounds) is the one place the exclusions are listed. The other three axes hold as written.
- **Estimate** what a *flat* request would cost before running it — it reads no decomposition setting, so a split or continued request can cost more. `estimate` builds the prompt this source would actually send, bounds it, contacts nothing, and returns what the whole fetch could cost — the bound each attempt reserves, accumulated over the attempts a fetch is allowed, the same way the budget ledger accumulates it. A planner can size a fan-out and an operator can check a request against a ceiling without spending anything to find out.
- **Plan** larger requests through bounded task trees. CLI and MCP expose 14 planning controls.
  Direct Python callers can also use rule-backed distribution search over declared finite identity
  domains and request a planning receipt.
- **Plug in** to the data layer under protocol `llm`, variant `openai`, through the shared
  `DataSource` interface. `execute_query` and `execute_query_nl` return a plain `list[dict]` without
  fetch metadata. Call `fetch` when you need the outcome, stop information, accounting, or schema
  diagnostics.

## Repository layout

```
blue-data-llm-source/
├── lib/                      # Python library (blue-data-llm-source)
│   ├── src/blue_data_llm_source/
│   └── tests/
├── platform/
│   ├── cli/                  # Command-line interface (llm)
│   └── mcp/                  # MCP server
└── docs/design/              # Architecture and design documents
```

## Requirements

- Python 3.10 or later
- A model provider API key, for anything other than an injected client

```bash
export OPENAI_API_KEY=...
```

The library never reads that variable; the provider SDK does. A missing or wrong key therefore arrives as a failed call rather than as a missing setting: `TransportError` from the library, exit code 5 from the CLI.

## Quick start

```bash
# Install the library with provider support
lib/scripts/install.sh          # installs blue-data from its checkout, then the library

# Install the CLI
platform/cli/scripts/install.sh # the command line, through the same prerequisite

# Ask for records
llm fetch "the member states of the European Union" \
    --attr country --attr accession_year:int

# Ask for a bounded number, and cap what it may spend
llm fetch "commercial airports in France" \
    --attr name --attr iata_code \
    --max-records 20 --limit-cost 0.10

# Allow one continuation after the first extraction ask. Flat medium/high effort
# needs a requested key; explicit --plan-continuations remains the advanced control.
llm fetch "commercial airports in France" \
    --attr name --attr iata_code --plan-key iata_code --effort medium

# The default table shows rows and accounting only. Add --verbose for the
# integrity fields — type mismatches, discarded keys, the stop reason — which
# are the ones that tell you whether to trust what you just got.
llm --verbose fetch "commercial airports in France" --attr name --attr iata_code
```

From Python, `llm_fn` can be any `(prompt) -> str` callable. Omit it to use the model named by
`llm.model`; provider-backed requests require the `llm` extra and a credential in the environment.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "the member states of the European Union",
    attrs=[
        {"name": "country", "type": "string"},
        {"name": "accession_year", "type": "int"},
    ],
    llm_fn=lambda prompt: '[{"country": "France", "accession_year": "1958"}]',
)
for record in result.records:
    print(record)
print(result.meta.outcome, result.meta.calls, result.meta.cost)
# accounting_known is False here: this answered through llm_fn, and a callable's
# provider work is invisible from here, so the call count and cost above are this
# library's own bookkeeping rather than anything measured.
print(result.meta.accounting_known)
```

Ask what a request would cost before committing to it. The figure is an upper bound on the prompt this source would actually send, and it is the same bound a run reserves against, so a request that sizes inside a ceiling is not refused when it runs.

```python
from blue_data_llm_source import LlmSource

source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 512})
sized = source.estimate("commercial airports in France", attrs=["name", "iata_code"])
print(sized.bounded, round(sized.max_cost, 6), sized.model, sized.max_output_tokens)
```

Each ceiling requires a matching client capability. The default client supports rows, calls, cost,
and time. A callable passed as `llm_fn` supports row limits only; setting `limits.max_calls`,
`limits.max_cost`, or `limits.max_seconds` raises `ConfigError` on the first fetch.
[lib/COMPATIBILITY.md](lib/COMPATIBILITY.md) documents the client contract.

Size a cost ceiling against the floor of the bound, which is the whole `llm.max_tokens` allowance at the model's output rate and does not depend on the prompt. With the shipped defaults of `gpt-4o` and 4096 output tokens that floor is about $0.041, so a `--limit-cost` or `limits.max_cost` below it refuses every call before making one. Lower `llm.max_tokens` to what the answers need rather than raising the ceiling.

## Dependencies

The runtime dependencies are `blue-data>=0.1.0` and `tenacity>=9.0.0`. Provider support is available
through the optional `llm` extra and is imported only when used.

`blue-data` is not distributed on a public package index. Install it from a checkout of the sibling
repository before installing this package. `lib/scripts/install.sh` looks for it at
`../../blue-data/lib`, or set `BLUE_DATA_PATH=/path/to/blue-data/lib` for another location.

The dependency has a `>=0.1.0` version floor and no pinned revision. After installation, run the
library test suite. `lib/tests/unit/compat/test_contract.py` checks the installed `blue_data` contract
and prints the package path it tested.

`LlmSource` inherits `blue-data`'s `DataSource` base class. Installing `blue-data` also installs its
`pandas`, `numpy`, and `jsonpath-ng` dependencies. The `blue` and `blue-data-ops` packages are not
runtime dependencies of this source.

Inheriting the base class does not make the source reachable by protocol name. Building the source and handing the instance to `register_source` is the supported path. `connect_source` then returns that live instance by name, and reconstructs an equivalent source from the recorded class path once the live entry is gone, so the registration outlives the object that made it.

`blue_data_llm_source.compat.blue_data.BlueDataLlmSource` is an alias for `LlmSource` for existing
imports.

## Interpreting results

Results are model-generated. Requested attributes that are missing from the response appear as
`None`. Schema diagnostics report unmapped keys, type mismatches, and declared types the library
cannot check (`meta.unchecked_types`). They do not verify factual accuracy. Records with schema
deviations remain in the result so callers can inspect or filter them.

`meta.outcome=ok` means the request completed without a detected execution failure; evaluate result
coverage separately. `meta.gaps` lists detected shortfalls such as a failed part, an enforced limit,
or an incomplete response. An empty list means no shortfall was detected. `meta.row_estimate` is a
model-generated planning hint rather than an expected result count. It is `None` in the default
`flat` mode and whenever no estimate was accepted.

`meta.stop_reasons` lists the extraction limits that fired, in precedence order;
`meta.stopped_by` is its first entry. Check these fields, `meta.gaps`, and the schema diagnostics
before using the records. Planning options and library examples are in
[lib/README.md](lib/README.md).

## Packages

| Package | Description |
|---|---|
| [lib/](lib/README.md) | `blue-data-llm-source` — the library |
| [platform/cli/](platform/cli/README.md) | `blue-data-llm-cli` — the `llm` command |
| [platform/mcp/](platform/mcp/README.md) | `blue-data-llm-mcp` — MCP server |

For client capabilities, budget enforcement, and migration notes, see
[lib/COMPATIBILITY.md](lib/COMPATIBILITY.md).
