# blue-data-llm-source

An LLM-as-data-source layer: a natural language request in, records shaped to a requested schema out, drawn from the model's own knowledge rather than from a store.

## What it does

- **Fetch** structured records for a question that has no table behind it: which countries belong to a trade bloc, which companies listed in a given year, what the standard container sizes are.
- **Shape** the result to requested attributes, with optional types, and check each value against the type it was given. A value that is present and fails its type is reported as a mismatch and kept, not dropped. An attribute the model omitted arrives as `None`, and so does an explicit JSON `null`; neither is a mismatch, and the result does not distinguish them. **An empty mismatch list therefore means "nothing present failed its type", not "every requested field is populated"** — check for `None` yourself if a field is required.
- **Report** what came back: the model used, calls, tokens, cost, elapsed time, keys that could not be mapped, keys that collided, and which limit stopped a run.
- **Bound** every request on four axes at once: rows, time, API cost, and call count. Calls, time, and cost are claimed before the provider is contacted; rows are capped once the records are parsed. A call's price is not known until it returns, so the cost claim is an upper bound computed against the price table this package carries, reconciled against the real figure afterwards, and a cost ceiling whose bound cannot be established is refused rather than claimed against a guess. **That table is a static snapshot of published list prices with no effective date and no freshness check**, so the bound is only as current as the table: if a listed price rises, or a dated model alias bills differently, or a proxy applies its own rates, the reservation is too small and the difference shows up at settlement rather than being prevented. Treat `limits.max_cost` as a control against known rates, not as a hard billing stop — and supply `llm.price_per_input_token` and `llm.price_per_output_token` to bound against rates you maintain. The ceiling travels with the request: a `limits.*` key in the properties of one call binds that call and does not leak into the next. Pass a `Budget` of your own when you want one total across a sequence of requests instead. The time axis is the weakest of the four: it bounds admission and cooperative waiting, not work that never yields. [What the time ceiling actually bounds](lib/README.md#what-the-time-ceiling-actually-bounds) is the one place the exclusions are listed. The other three axes hold as written.
- **Estimate** what a request would cost before running it. `estimate` builds the prompt this source would actually send, bounds it, contacts nothing, and returns what the whole fetch could cost — the bound each attempt reserves, accumulated over the attempts a fetch is allowed, the same way the budget ledger accumulates it. A planner can size a fan-out and an operator can check a request against a ceiling without spending anything to find out.
- **Plug in** to the data layer under protocol `llm`, variant `openai`, inheriting the same `DataSource` base class every other source in the layer inherits. **That shared surface returns records and nothing else.** `execute_query` and `execute_query_nl` return a plain `list[dict]`, so everything under **Report** above — the outcome, the stop reason, accounting, unmapped and remapped keys, type mismatches, collisions, truncation — is discarded on that path. The signature is the base class's and this source cannot widen it. Call `fetch` directly when you need to know whether the result you got is one you should trust; use the shared surface when a caller only wants rows.

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

# The default table shows rows and accounting only. Add --verbose for the
# integrity fields — type mismatches, discarded keys, the stop reason — which
# are the ones that tell you whether to trust what you just got.
llm --verbose fetch "commercial airports in France" --attr name --attr iata_code
```

From Python. `llm_fn` is any `(prompt) -> str` callable, and standing one in here is what makes this block runnable with no key and no network; drop it and the request goes to the model named by `llm.model`, which needs the `llm` extra and a credential in the environment.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "the member states of the European Union",
    attrs=[{"name": "country", "type": "string"},
           {"name": "accession_year", "type": "int"}],
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

A ceiling holds only on an axis the answering client declares, and each configured ceiling on an axis it does not declare is refused with a `ConfigError` at the first fetch rather than reported as binding. The default client declares all four. A callable passed as `llm_fn` declares rows and nothing else, because nothing here can know what that callable is billed, how to stop it, or how many provider calls one invocation of it becomes, so `limits.max_calls`, `limits.max_cost`, and `limits.max_seconds` over one are refused. The row cap needs nothing from the client and always holds. [lib/COMPATIBILITY.md](lib/COMPATIBILITY.md) states the whole contract in one place, including what a client must implement to be held to the other three.

Size a cost ceiling against the floor of the bound, which is the whole `llm.max_tokens` allowance at the model's output rate and does not depend on the prompt. With the shipped defaults of `gpt-4o` and 4096 output tokens that floor is about $0.041, so a `--limit-cost` or `limits.max_cost` below it refuses every call before making one. Lower `llm.max_tokens` to what the answers need rather than raising the ceiling.

## Dependencies

Two runtime dependencies, `blue-data>=0.1.0` and `tenacity>=9.0.0`. The provider layer sits behind the `llm` extra and is imported lazily, so the package installs and its test suite runs with no provider SDK present.

**`blue-data` is not on any public index and is not bundled here, so you need it before anything below will install.** It is the sibling data-layer repository in this organisation; get a checkout from whoever owns it. `lib/scripts/install.sh` looks for it at `../../blue-data/lib` relative to this package — the layout you get by cloning both side by side — and `BLUE_DATA_PATH=/path/to/blue-data/lib` points it elsewhere. Without it the installer exits before doing anything, which is a missing sibling rather than a broken package.

There is no pinned revision, only the `>=0.1.0` floor: this source tracks a moving base class. `lib/tests/unit/compat/test_contract.py` compares the installed `blue_data` against what this source expects and prints the path it checked, so run the library suite after installing and before trusting the pairing. An installed copy can be older than a checkout, and verifying against a version nobody runs is worse than not verifying.

`LlmSource` inherits `blue-data`'s `DataSource` base class, as every source in the data layer does, so a divergence from that surface is a type error rather than a drift discovered by whoever hits it. What that costs is `pandas`, `numpy`, and `jsonpath-ng`, which `blue-data` brings with it, so the install pulls in a scientific stack this package does not itself use. What it does not cost is the orchestration layer: nothing here imports `blue` or `blue-data-ops`, and depending on the data layer is not depending on the runtime that calls it.

Inheriting the base class does not make the source reachable by protocol name. Building the source and handing the instance to `register_source` is the supported path. `connect_source` then returns that live instance by name, and reconstructs an equivalent source from the recorded class path once the live entry is gone, so the registration outlives the object that made it.

`blue_data_llm_source.compat.blue_data.BlueDataLlmSource` is an alias for `LlmSource`, kept so an existing import keeps working. It is not a subclass, there is no separate adapter class, and there is no extra to install for it.

## What it does not do

Answers come from the model, so they are sampled rather than looked up. The library checks each record against the schema it was asked for and reports every deviation it finds; it does not reject a record for deviating. It does not verify that a claim is true, and nothing here should be read as saying otherwise.

**A short answer and a complete one look identical here.** A model asked for two hundred records may return forty, stop at a plausible place, and report nothing wrong — and `outcome` will be `ok`, because `ok` means "records survived and no limit fired", not "this is all of them". There is no expected row count, coverage account, or continuation token in this release, so **nothing in the result lets you detect an incomplete answer**. That check is yours. It is the reason this release is not suitable for exhaustive lists, or for joins whose correctness depends on having every row.

Decomposing a large request into partitioned sub-requests, identifying a schema when none is given, and estimating how many records an answer should contain are v2.0 work. `estimate` sizes what a request would cost, which is a different question from how large its answer ought to be, and nothing here answers the second one.

This release makes one model **completion** per request, not one provider call: a fetch may retry, up to `limits.max_retries + 1` attempts, which is three by default. `meta.calls` counts provider attempts, so a request that retried twice reports three, and it is attempts that consume the call and cost ceilings.

## Packages

| Package | Description |
|---|---|
| [lib/](lib/README.md) | `blue-data-llm-source` — the library |
| [platform/cli/](platform/cli/README.md) | `blue-data-llm-cli` — the `llm` command |
| [platform/mcp/](platform/mcp/README.md) | `blue-data-llm-mcp` — MCP server |

[lib/COMPATIBILITY.md](lib/COMPATIBILITY.md) records what this library does differently from the language-model source in the Blue monolith and from the data-source design document, with the reason for each. It is the one place the budget contract is stated in full, so read it before porting a caller or writing a client.
