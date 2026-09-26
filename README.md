# blue-data-llm-source

`blue-data-llm-source` is a Blue data source that makes a language model's general knowledge, including common-sense information, queryable as typed records (natural language → LLM → table). A caller submits a natural-language request and may declare an attribute schema. For larger data requests, the source can plan retrieval beyond a single model response. In planned modes, it can rewrite the request, infer or complete the output schema, estimate row count, and divide the query into hierarchical data tasks. It executes those tasks under explicit limits, checks the returned records against the schema, and merges records that share an identified key. Together, partitioning, task execution, and merging form a map-reduce process. The Python library, command-line interface, and MCP server expose the records and execution metadata. The records can be used alongside those from other Blue data sources.

[Full documentation](docs/README.md) · [Quick start](docs/quickstart.md) · [Installation](docs/installation.md) · [Examples](examples/README.md) · [Changelog](CHANGELOG.md)

## Data source

`LlmSource` uses a language model as its source of records. It has no maintained table, document corpus, or index; optional caches can reuse earlier model responses. For example, a query for European Union member states can declare `country` and `accession_year:int` as output attributes.

The source implements the Blue `DataSource` interface under protocol `llm` and variant `openai`. A Postgres source uses the same interface under protocol `sql` and variant `postgres`.

## Primary uses

* `LlmSource` gives structured access to common-sense and other model knowledge, particularly when an agent needs a large set of records or lacks a suitable maintained data source. Callers can use the typed records directly or pass them to operators for filtering, aggregation, and joins with other sources.

* `LlmSource` can generate a table needed to combine data from existing sources. An agent or data planner can request the table on demand. For example, generated `eu_members(country, accession_year)` records can be joined on `country` with Postgres `vendors(name, country)` rows. A downstream operator performs the join.

* Large sets of records can exceed the capacity of a single model response or be returned incompletely. Partitioned retrieval divides a request into bounded parts, can request additional records for a part, and merges results by key. It reports observed gaps and limits, but does not verify factual accuracy or guarantee full coverage.

Model-generated records require independent verification when factual accuracy or coverage matters. The model's knowledge may be outdated. Use a maintained source for authoritative data, and a document or vector source for facts in private documents.

## Features

- Natural-language queries return records with caller-declared attributes and types. In planned modes, the source can infer an output schema and key when the caller does not provide them.
- Planning can rewrite the request, estimate the expected number of rows, and divide a large request into bounded parts. The row estimate is a planning hint, not a completeness measure.
- Partitioned retrieval can continue a part to request more records, merge records with the same key, and report which parts produced each record. The opt-in `plan.split_policy="evidence"` asks once before splitting; [the changelog](CHANGELOG.md#220--candidate) reports its measured results. With the default client, `llm.max_parallel` limits concurrent provider calls per event loop.
- Values are checked against declared types, and returned fields are aligned with requested attributes. Mismatches and unmatched fields remain visible in result metadata.
- Every request can cap returned rows. Provider-call, time, and cost ceilings are available when the configured client supports usage reservation. `estimate` checks a flat fetch without provider contact and returns a cost bound when pricing is available; otherwise it reports why no bound is available.
- Each `fetch` result includes records and metadata about the outcome, model usage, cost, elapsed time, and detected gaps. Optional caches can reuse equivalent model responses.
- The Python library, command-line interface, MCP server, and Blue `DataSource` interface expose the source to applications and agent workflows.

`LlmSource` does not verify facts or measure completeness. `meta.outcome=ok` means no execution failure was detected; an empty `meta.gaps` means no supported diagnostic fired. Neither field establishes that the records are correct or complete. [docs/guide.md](docs/guide.md) explains these fields and their limits.

## Using it

### Install

Keep `blue-data` in a separate checkout beside `blue-data-llm-source`, under the same parent directory. From this repository root, its library is at `../blue-data/lib`:

```text
workspace/
├── blue-data/
└── blue-data-llm-source/
```

Run these commands from `blue-data-llm-source`. Create the environment once; Conda may ask for confirmation. If `blue-llmdb` already exists, skip this block and activate it below.

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

If Conda cannot activate the environment, run `conda init zsh` (or `conda init bash`) once, open a new terminal, return to this checkout, and rerun the second block. If the CLI is already installed, activate its environment and start with `blue-llmdb metadata inspect`. [Installation](docs/installation.md) also covers `venv`, the install scripts, and the MCP package.

### Python

`fetch` asks for records; `estimate` checks the cost of one flat fetch without contacting a provider and returns a bound when pricing is available. The `llm_fn` below stands in for the model so the snippet runs offline. Omit it to use the model named by `llm.model`, which needs the `llm` extra and a provider key in the environment.

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
