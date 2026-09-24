# blue-data-llm-source

`blue-data-llm-source` is a structured, commonsense-knowledge data source for the Blue data layer. You ask a question in plain language, name the attributes you want, and get back typed records drawn from a language model's knowledge.

[Full documentation](docs/README.md) · [Quick start](docs/quickstart.md) · [Installation](docs/installation.md) · [Examples](examples/README.md) · [Changelog](CHANGELOG.md)

## What it is

A conventional data source answers from data it stores: a table, a document collection, a graph, a vector index. This one stores nothing. It answers from what a language model learned in training, and returns the answer as records with the attributes and types you asked for, such as `country` and `accession_year:int` for "the member states of the European Union".

It is one source among many in the data layer. It registers under protocol `llm` with variant `openai`, the way a Postgres source registers under `sql` and `postgres`, and it answers on the same `DataSource` interface.

## When to use it

Use it as glue, when a data task needs data that no existing source holds: no maintained table exists, or building and keeping one is not worth it for the question at hand. Lists of trade-bloc members, companies listed in a given year, standard container sizes, or the capital of each country in a region are typical.

Because it answers on the shared `DataSource` interface, a planner can combine its records with those of SQL, document, graph and vector sources. A supplier table in Postgres has a `country` column, and the task needs to know which suppliers are in the EU:

```text
Postgres source   vendors(name, country, ...)            stored rows
LlmSource         eu_members(country, accession_year)    model knowledge
planner           vendors JOIN eu_members ON country
```

Do not use it where a maintained, authoritative source exists, where every row must be correct or the list must be complete, or for facts newer than the model's training data. It is also the wrong tool for questions about your own documents or data, which the model has never seen; use a document or vector source for those.

## What you get, and what it does not promise

- **Typed, aligned records.** Each value is checked against its declared type; a value that fails is kept and reported in `meta.type_mismatches`. Returned keys are matched to the attributes you asked for, and a key that matches none is reported rather than dropped.
- **Bounded execution.** Every request can be bounded by rows, time, cost and provider calls. Calls, time and cost are claimed before the provider is contacted, so a request that would cross a ceiling is refused, not billed. `estimate` gives the cost bound for a request without contacting anything.
- **Diagnostics on every result.** `fetch` returns the records with `meta`: the outcome, the model, calls, tokens, cost, elapsed time, the gaps it detected, and which limit stopped the run.
- **Larger requests, split and bounded.** A request can be split into parts along a key, each asked separately and merged, within caps on depth, parts and calls.

It does not verify facts and does not measure completeness. The records are model output: `meta.outcome=ok` means no execution failure was detected, and an empty `meta.gaps` means no supported diagnostic fired, not that the answer is complete or correct. Verify facts that matter against another source. [docs/guide.md](docs/guide.md) covers what each field does and does not promise.

## Using it

### Install

Run these commands from the `blue-data-llm-source` checkout with `blue-data` beside it. Create the environment once; Conda may ask for confirmation. If `blue-llmdb` already exists, skip this block and activate it below.

```bash
ls -d ../blue-data/lib &&
conda create --name blue-llmdb python=3.12 pip
```

Paste the next block after Conda finishes. The chain stops if activation or an install fails.

```bash
conda activate blue-llmdb &&
python -m pip install -e ../blue-data/lib &&
python -m pip install -e "./lib[llm]" &&
python -m pip install -e ./platform/llm-cli &&
blue-llmdb metadata inspect
```

If Conda cannot activate the environment, run `conda init zsh` (or `conda init bash`) once, open a new terminal, return to this checkout, and rerun the second block. If the packages are already installed, activate their environment and start with `blue-llmdb metadata inspect`. [Installation](docs/installation.md) also covers `venv`, the install scripts, and the MCP package.

### Python

`fetch` asks for records; `estimate` bounds what the same request could cost without contacting a provider. The `llm_fn` below stands in for the model so the snippet runs offline. Omit it to use the model named by `llm.model`, which needs the `llm` extra and a provider key in the environment.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
query = "the member states of the European Union"
attrs = [{"name": "country", "type": "string"}, {"name": "accession_year", "type": "int"}]

sized = source.estimate(query, attrs=attrs)
print(sized.bounded, sized.model, sized.max_cost)

result = source.fetch(
    query,
    attrs=attrs,
    llm_fn=lambda prompt: '[{"country": "France", "accession_year": "1958"}]',
)
print(result.records, result.meta.outcome, result.meta.type_mismatches)
```

[docs/guide.md](docs/guide.md) works through the library task by task, and [lib/README.md](lib/README.md) is the reference.

### Command line

```bash
# Keep the key where the CLI can find it, out of shell history
blue-llmdb profile config OPENAI_API_KEY

# Price the request first. This contacts nothing and needs no key.
blue-llmdb data estimate "the member states of the European Union" --format nl

# Ask for records, and cap what the request may spend
blue-llmdb data query "the member states of the European Union" \
    --attr country --attr accession_year:int --limit-cost 0.50
```

The table ends with a footer naming the model, calls, tokens, cost and elapsed time, and a line listing each setting and where it came from. [docs/quickstart.md](docs/quickstart.md) walks through the CLI with the output at each step, and [docs/cli.md](docs/cli.md) is the reference.

### MCP server for agents

`blue-data-llm-mcp` serves five tools over stdio: `llmdb_query`, `llmdb_estimate`, `llmdb_list_models`, `llmdb_inspect_model` and `llmdb_inspect_metadata`. Only `llmdb_query` contacts a provider. The operator sets the model and ceilings in the client's server config, and an agent can tighten a ceiling for one call but not raise it:

```json
{
  "mcpServers": {
    "blue-data-llm": {
      "command": "blue-data-llm-mcp",
      "env": {
        "OPENAI_API_KEY": "<YOUR_PROVIDER_KEY>",
        "BLUE_DATA_LLM_SOURCE_LIMIT_COST": "0.50"
      }
    }
  }
}
```

Install it with `python -m pip install -e ./platform/llm-mcp` in the environment above. [docs/mcp.md](docs/mcp.md) covers every tool and setting.

### In the data layer

Build the source, register it, and reach it through the registry like any other source. `execute_query_nl` returns a plain `list[dict]`; use `fetch` when you need the metadata. This call reaches the provider:

```python
from blue_data.registry.data_registry import DataRegistry
from blue_data_llm_source import LlmSource

registry = DataRegistry(name="MY_REGISTRY")
registry.register_source(LlmSource(name="llm"))

eu_members = registry.connect_source("llm").execute_query_nl(
    "the member states of the European Union",
    optional_properties={"attrs": ["country", "accession_year"], "limits.max_cost": 0.25},
)
```

A `limits.*` key in `optional_properties` bounds that call only. [lib/COMPATIBILITY.md](lib/COMPATIBILITY.md) describes how the source fits the data-layer interface and what it does differently.

## Models and cost

The default model is `gpt-6-luna` at medium reasoning. The package knows OpenAI's GPT-6, GPT-5.x, GPT-4.1, GPT-4o and o-series models, sends each the request settings it accepts, and bounds a cost ceiling against its published rates; the [supported models](lib/README.md#supported-models) table lists them with prices. The rates are a static snapshot, so treat `limits.max_cost` as a control against known rates, not a billing stop, and supply `llm.price_per_input_token` and `llm.price_per_output_token` to use rates you maintain. [The cost bound](lib/README.md#the-cost-bound) explains the bound and how a call is charged, and [what `cost` means](lib/README.md#what-cost-means) how to read the figure a run reports.

## Requirements

- Python 3.10 or later.
- A local checkout of the `blue-data` repository, beside this one. It is not published to a package index, so it is installed from the checkout. `lib/scripts/install.sh` looks for it at `../blue-data/lib` from the repository root; set `BLUE_DATA_PATH` to its `lib` directory if it is elsewhere.
- A model provider API key, for anything other than an injected client or an `llm_fn`. The library never reads the key; the provider SDK does, so a missing or wrong key arrives as a failed call: `TransportError` from the library, exit code 5 from the CLI.

The runtime dependencies are `blue-data>=0.1.0` and `tenacity>=9.0.0`, with provider support in the optional `llm` extra. `blue-data` has no pinned revision, so run the library tests after installing or upgrading it; `lib/tests/unit/compat/test_contract.py` checks the installed contract.

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


## Packages

| Package | Description |
|---|---|
| [lib/](lib/README.md) | `blue-data-llm-source`, the library |
| [platform/llm-cli/](platform/llm-cli/README.md) | `blue-data-llm-cli`, the `blue-llmdb` command |
| [platform/llm-mcp/](platform/llm-mcp/README.md) | `blue-data-llm-mcp`, the MCP server |
