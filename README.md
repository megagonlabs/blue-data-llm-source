# blue-data-llm-source

`blue-data-llm-source` turns natural-language questions into structured records. It uses a model's learned knowledge; it does not query a database or document store.

[Full documentation](docs/README.md) · [Quick start](docs/quickstart.md) · [Installation](docs/installation.md) · [Examples](examples/README.md) · [Changelog](CHANGELOG.md)

## What it does

- **Fetch** structured records when no authoritative table is available: which countries belong to a trade bloc, which companies listed in a given year, or what the standard container sizes are.
- **Shape** the result to requested attributes, with optional types, and check each value against the type it was given. A value that fails its type is reported as a mismatch and kept, not dropped. A missing attribute arrives as `None`, and so does an explicit JSON `null`. An empty mismatch list means "nothing present failed its type", not "every requested field is populated", so check for `None` yourself if a field is required.
- **Report** what came back: the model used, calls, tokens, cost, elapsed time, keys that could not be mapped, keys that collided, and which limit stopped a run.
- **Bound** every request on four axes at once: rows, time, API cost, and call count. Calls, time, and cost are claimed before the provider is contacted; rows are capped once the records are parsed.
- **Estimate** the upper cost bound for a flat request without contacting a provider. `estimate` uses the same prompt and calculation as the runtime reservation. It does not account for decomposition, so a split request can cost more.
- **Plan** larger requests with bounded task trees. Python, the CLI, and MCP share 14 planning controls. Rule-backed distribution search over declared finite identity domains, together with its planning receipt, is available only through the direct Python API.
- **Integrate** with the data layer under protocol `llm` and variant `openai` through the shared `DataSource` interface. `execute_query` and `execute_query_nl` return a plain `list[dict]` without fetch metadata. Use `fetch` when you need the outcome, stop information, accounting, or schema diagnostics.

Treat `limits.max_cost` as a control based on known rates, not as a billing stop. The package uses a static price table with no effective date or freshness check. Supply `llm.price_per_input_token` and `llm.price_per_output_token` to use rates that you maintain. A ceiling applies only to the call that carries it; pass a shared `Budget` to enforce one total across several calls. The time limit covers admission and cooperative waits, with the exclusions listed under [what the time ceiling actually bounds](lib/README.md#what-the-time-ceiling-actually-bounds).

[docs/features.md](docs/features.md) has a short runnable example for each of these.

## Repository layout

```
blue-data-llm-source/
├── lib/                        # Python library (blue-data-llm-source)
│   ├── src/blue_data_llm_source/
│   └── tests/
├── platform/
│   ├── llm-cli/                # Command line: blue-data-llm-cli, the blue-llmdb command
│   │   ├── src/blue_data_llm_cli/
│   │   └── tests/
│   └── llm-mcp/                # MCP server: blue-data-llm-mcp, five tools over stdio
│       ├── src/blue_data_llm_mcp/
│       └── tests/
├── docs/                       # User-facing documentation (start at docs/README.md)
├── examples/                   # Runnable scripts with captured output (start at examples/README.md)
├── design/                     # Architecture and design-process documents
├── CHANGELOG.md
└── LICENSE
```

## Requirements

- Python 3.10 or later.
- A local checkout of the `blue-data` repository. It is not on a public package index, so it must be installed from source. `lib/scripts/install.sh` looks for it at `../../blue-data/lib`; set `BLUE_DATA_PATH` to point it elsewhere.
- A model provider API key, for anything other than an injected client or an `llm_fn`.

The runtime dependencies are `blue-data>=0.1.0` and `tenacity>=9.0.0`, with provider support in the optional `llm` extra, imported only when used. The `blue-data` floor carries no pinned revision, so run the library test suite after installing or upgrading it: `lib/tests/unit/compat/test_contract.py` checks the installed contract and prints the package path it tested. Installing `blue-data` also installs `pandas`, `numpy`, and `jsonpath-ng`. The `blue` and `blue-data-ops` packages are not runtime dependencies of this source.

The library never reads the provider key; the provider SDK does. A missing or wrong key therefore arrives as a failed call rather than as a missing setting: `TransportError` from the library, exit code 5 from the CLI.

## Quick start

```bash
# Install the library, then the command line. Both need the blue-data checkout
# named under Requirements above to already be on disk.
lib/scripts/install.sh
platform/llm-cli/scripts/install.sh

# Keep the key where the CLI can find it, out of shell history
blue-llmdb profile config OPENAI_API_KEY

# Price the request first. This contacts nothing and needs no key.
blue-llmdb data estimate "the member states of the European Union" --format nl

# Ask for records
blue-llmdb data query "the member states of the European Union" \
    --attr country --attr accession_year:int

# Ask for a bounded number, and cap what it may spend
blue-llmdb data query "commercial airports in France" \
    --attr name --attr iata_code \
    --max-records 20 --limit-cost 0.50

# The default table shows rows and accounting. Add --verbose for alignment,
# type, and collision diagnostics.
blue-llmdb data query "commercial airports in France" \
    --attr name --attr iata_code --verbose
```

The minimum cost bound is the full `llm.max_tokens` allowance at the model's output rate; it does not depend on the prompt. With the default `gpt-4o` model and 4096 output tokens, the minimum is about $0.041. A lower `--limit-cost` refuses the call before contacting the provider. If that allowance is larger than the answer requires, reduce `llm.max_tokens`.

From Python, `llm_fn` is any `(prompt) -> str` callable. Omit it to use the model named by `llm.model`, which requires the `llm` extra and a credential in the environment.

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
print(result.meta.outcome, result.meta.calls, result.meta.stop_reasons)
```

Each ceiling requires a matching client capability. The default client supports rows, calls, cost, and time. A callable passed as `llm_fn` supports row limits only; setting `limits.max_calls`, `limits.max_cost`, or `limits.max_seconds` raises `ConfigError` on the first fetch. [lib/COMPATIBILITY.md](lib/COMPATIBILITY.md) documents the client contract.

The full walkthrough, with the output at each step, the Python equivalent, and how to connect an agent, is in **[docs/quickstart.md](docs/quickstart.md)**.

## Interpreting results

Results are model-generated. Schema diagnostics report unmapped keys, type mismatches, and declared types the library cannot check. They do not verify factual accuracy. `meta.outcome=ok` means the request completed without a detected execution failure; evaluate coverage separately, using `meta.gaps` for detected shortfalls and `meta.stop_reasons` for the limits that fired. [docs/guide.md](docs/guide.md) covers how to read a result and what each field does and does not promise.

## Packages

| Package | Description |
|---|---|
| [lib/](lib/README.md) | `blue-data-llm-source`, the library |
| [platform/llm-cli/](platform/llm-cli/README.md) | `blue-data-llm-cli`, the `blue-llmdb` command |
| [platform/llm-mcp/](platform/llm-mcp/README.md) | `blue-data-llm-mcp`, the MCP server |

For client capabilities, budget enforcement, and migration notes, see [lib/COMPATIBILITY.md](lib/COMPATIBILITY.md).
