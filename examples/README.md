# blue-data-llm-source Examples

Runnable examples are grouped by backend, feature, integration, and usage scenario.

Each script has a matching `.output.txt` file containing captured standard output. The WikiTabGen example's captured output is its setup preview; `--live` prints the provider responses.

Most scripts require no credential or network access. They use a scripted `llm_fn` or an injected client to exercise prompt rendering, attribute alignment, type checking, limits, budgets, gaps, outcome precedence, planning modes, and the registry round trip. Provider calls require a key. The table at the end of this file identifies each script's execution mode.

## Quick start

Install `blue-data` from a separate sibling checkout at `../blue-data`, then install this library with its `llm` extra as shown in [Installation](../docs/installation.md). Activate that environment and run every command below from the `blue-data-llm-source` root.

```bash
python3 examples/backends/function_client_quickstart.py
```

The quickstart needs no credential. Provider-backed runs read `OPENAI_API_KEY` from the environment. If it is not already set, run this block first and enter the key at the prompt. The shell hides the value and keeps it out of history.

```bash
if [ -z "${OPENAI_API_KEY:-}" ]; then
    printf 'OpenAI API key: '
    IFS= read -rs OPENAI_API_KEY
    printf '\n'
    export OPENAI_API_KEY
fi
```

Then run the provider-backed example:

```bash
python3 examples/backends/default_client_demo.py
```

The CLI is a separate package. `blue-llmdb data query "..."` runs the same library from a shell; its options are documented in `docs/cli.md`.

---

## backends/: the client axis and the cache axis

This source has no storage backend. The two choices are **which client answers** (a caller's `llm_fn`, an injected client, or the built-in `DefaultLlmClient` driving a provider) and **where responses are cached**. Start here when wiring the library into an application: these choices determine which ceilings can bind and what the result's accounting means.

### Minimal quickstart (`function_client_quickstart.py`)

One fetch is answered by a local function while prompt rendering, JSON parsing, attribute alignment, type checking, and result metadata use the normal library path. `meta.accounting_known` is `False`, so a `cost` of `0.0` means "not measured", not "free".

```bash
python3 examples/backends/function_client_quickstart.py
```

**API key:** not required | **[Sample output](backends/function_client_quickstart.output.txt)**

### An injected client the source can bound (`injected_client_demo.py`)

This example injects a client that declares its supported axes through `budget_capabilities()`, reserves against the run's `Budget`, and appends a `Usage`. The result includes the model name, token counts, settled cost, and `accounting_known=True`. The library refuses the same cost ceiling for an `llm_fn` because that wrapper cannot observe or enforce provider cost.

```bash
python3 examples/backends/injected_client_demo.py
```

**API key:** not required (`LedgerClient` is a local double that prices itself) | **[Sample output](backends/injected_client_demo.output.txt)**

### Provider-backed fetch (`default_client_demo.py`)

This example uses `DefaultLlmClient` through litellm and reports the model, token counts, and settled cost reconciled against the earlier estimate. The client is injected so `close()` releases the cache backends it opens. The provider SDK reads `OPENAI_API_KEY` for authentication; the library reads credential environment values only to mask diagnostics. Authentication failure may raise `TransportError` or stop the result when the call ceiling is reached. The script exits unsuccessfully in either case.

```bash
python3 examples/backends/default_client_demo.py
```

**Requires:** `OPENAI_API_KEY`, and the `llm` extra | **[Recorded output](backends/default_client_demo.output.txt)** from an earlier provider call. Its estimate, cost, and accounting fields predate the current bound and settlement rules; run the script for current values.

### The two cache stores (`cache_stores_demo.py`)

`MemoryCache` and `FileCache` use the `get`/`set` pair declared by the `CacheStore` protocol. The example shows bounded LRU eviction, persistence across instances, and `clear()` removing only entries written by the current `FileCache` instance.

```bash
python3 examples/backends/cache_stores_demo.py
```

**API key:** not required | **[Sample output](backends/cache_stores_demo.output.txt)**

### Cache hit end to end (`file_cache_demo.py`)

`llm.cache` with `llm.cache_dir`: two identical fetches, with the second served from disk at no provider, token, or API cost. This cache is inside the client, so the first call reaches the provider and requires a credential. `cache_stores_demo.py` uses caller-supplied stores and remains offline.

```bash
python3 examples/backends/file_cache_demo.py
```

**Requires:** `OPENAI_API_KEY`, and the `llm` extra (diskcache comes with it) | **[Sample output](backends/file_cache_demo.output.txt)**, from a real provider call followed by a cache hit

---

## feature-demos/: one feature per script

Each script covers one feature using a scripted `llm_fn` or an injected client, which makes the output reproducible. All seven run offline.

### Attributes and declared types (`attrs_and_types_demo.py`)

Ten sample responses cover the main attribute outcomes. A numeric string under a declared `integer` is converted; a number under a declared `string` is retained and reported as a mismatch. The example also covers `unmapped_keys`, `key_collisions`, `unchecked_types`, and removal of a record that answers none of the requested attributes.

```bash
python3 examples/feature-demos/attrs_and_types_demo.py
```

**API key:** not required | **[Sample output](feature-demos/attrs_and_types_demo.output.txt)**

### Limits (`limits_demo.py`)

The four axes and what binds them. `limits.max_rows` truncates and reports `truncated_to`; the call, time and cost axes are refused over an `llm_fn`, one message per axis, because `FunctionClient` declares rows alone. Then the closed key set, the value checks that catch a negative or a `NaN` ceiling, and the per-request scoping that keeps a ceiling from leaking into the next call.

```bash
python3 examples/feature-demos/limits_demo.py
```

**API key:** not required | **[Sample output](feature-demos/limits_demo.output.txt)**

### estimate (`estimate_demo.py`)

An offline cost demo: `estimate` contacts nothing by construction. It shows a priced model, the cost floor from the output allowance, an unpriced model's reason, caller-supplied rates, an `llm_fn` that cannot price itself, and that `plan.*` settings are not read. The budget and estimate-then-fetch demos also need no credential.

```bash
python3 examples/feature-demos/estimate_demo.py
```

**API key:** not required | **[Sample output](feature-demos/estimate_demo.output.txt)**

### A budget across calls (`budget_demo.py`)

One `Budget` is shared by three fetches. Each derives a child and spends from the parent's remaining allowance; the third returns empty with `stopped_by` naming the exhausted axis. The example uses an `llm_fn` for the row axis and a capability-declaring client for the call and cost axes. Cost admission uses the reserved upper bound, not the settled price.

```bash
python3 examples/feature-demos/budget_demo.py
```

**API key:** not required | **[Sample output](feature-demos/budget_demo.output.txt)**

### Gaps and outcome precedence (`gaps_and_outcome_demo.py`)

The example deliberately produces every `outcome` value and shows the accompanying gap diagnostics. It also demonstrates the precedence rule: a partly answered plan outranks a cap that fires at the same moment, while the cap remains in `stop_reasons`.

```bash
python3 examples/feature-demos/gaps_and_outcome_demo.py
```

**API key:** not required | **[Sample output](feature-demos/gaps_and_outcome_demo.output.txt)**

### Planning modes (`planning_modes_demo.py`)

The example prints the call sequence for `flat`, flat with continuations, `partitioned`, and `row_by_row`, making the cost of each shape visible. The scripted planner recognizes each planning prompt by its opening line and uses a fixed response, so the same four sequences appear on every run.

```bash
python3 examples/feature-demos/planning_modes_demo.py
```

**API key:** not required | **[Sample output](feature-demos/planning_modes_demo.output.txt)**

### The orchestrator tool surface (`tool_surface_demo.py`)

`get_nl_tools` advertises five of the base class's eight tools and `execute_nl_tool` serves those five (`get_sample` returns `None` for unavailable statistics), refusing anything else by name. `execute_query_nl_tools` (the tool-driven loop) is refused outright, because it drives up to ten provider calls this source's ceilings do not govern.

```bash
python3 examples/feature-demos/tool_surface_demo.py
```

**API key:** not required | **[Sample output](feature-demos/tool_surface_demo.output.txt)**

---

## framework-demos/: AI agent framework integrations

Both examples use [LangGraph](https://github.com/langchain-ai/langgraph) and are provider-backed. One implements a fixed pipeline; the other lets an agent select its own tools.

### Direct Python API (`langgraph_direct_demo.py`)

Run this from the checkout root in the active root `.venv`. The pipeline has two nodes. `retrieve` calls `LlmSource.fetch` under a shared retrieval `Budget`, then `respond` makes a separate `ChatOpenAI` call to compose a sentence. The printed budget excludes that writing call. The records travel with `meta.outcome` and `meta.gaps`; the writing node is instructed to report detected limits or gaps. An agent that reads only `records` misses detected truncation; even an empty `meta.gaps` does not establish completeness.

```bash
python -m pip install langgraph langchain-openai
python3 examples/framework-demos/langgraph_direct_demo.py
```

The two tasks make two retrieval calls and two separate writing calls when no retry occurs. If `langchain-openai` is missing, the script stops at import with `ModuleNotFoundError: No module named 'langchain_openai'`.

**Requires:** `OPENAI_API_KEY`, `langgraph`, `langchain-openai` | **[Recorded output](framework-demos/langgraph_direct_demo.output.txt)** from an earlier provider-backed run. Its budget cost predates the current bound and settlement rules; run the script for current values.

### MCP-based integration (`langgraph_mcp_demo.py`)

Run this from the checkout root in the active root `.venv`. `langchain-mcp-adapters` launches `blue-data-llm-mcp` as a stdio subprocess and discovers its five tools. A ReAct agent decides what to call. The system prompt asks the agent to estimate cost and pass a ceiling. The example requires a finite operator cost ceiling in the server's environment. That ceiling applies to each MCP query; it does not cap total tool spend or the agent's separate `ChatOpenAI` calls. The 4,000-token allowance keeps the example's offline flat estimate below the prompt's $0.02 threshold at the package rates, so the agent can proceed to a query.

```bash
./platform/llm-mcp/scripts/install.sh
python -m pip install langgraph 'langchain>=1.0' langchain-openai langchain-mcp-adapters
export BLUE_DATA_LLM_SOURCE_LIMIT_COST=0.02
export BLUE_DATA_LLM_SOURCE_MAX_TOKENS=4000
python3 examples/framework-demos/langgraph_mcp_demo.py
```

**Requires:** `OPENAI_API_KEY`, `BLUE_DATA_LLM_SOURCE_LIMIT_COST`, `langgraph`, `langchain`, `langchain-openai`, `langchain-mcp-adapters`, and the MCP package on PATH | **[Recorded output](framework-demos/langgraph_mcp_demo.output.txt)** from a provider-backed run with library 1.0.0 and the settings above: metadata inspection, estimate, then a query for eight French airports. Tool choices and answers vary by run.

The agent carries the returned message history into each task, so the final request can refer to the French airports discussed earlier. Each follow-up resends the full conversation, including raw tool results, to the configured chat model. Input tokens and cost can grow with that history; those chat calls are outside the MCP query ceiling. It passes the provider key and [MCP deployment settings](../platform/llm-mcp/README.md#configuration) to the server subprocess. Other environment settings, including endpoint and proxy variables, are not forwarded.

---

## integration-demos/: two or more features working together

### Registering the source in the data layer (`register_source_demo.py`)

The example registers a built instance with `register_source`, then retrieves it with `connect_source`. After the live instance is dropped, `connect_source` rebuilds a new source from the recorded class path and properties in the same registry, without the injected client. It then exercises the shared interface: `execute_query_nl` with a string, `execute_query` with an `op` dict, and a `limits.*` ceiling in `optional_properties` that applies to one call.

```bash
python3 examples/integration-demos/register_source_demo.py
```

**API key:** not required | **[Sample output](integration-demos/register_source_demo.output.txt)**

### Estimate, decide, fetch, reconcile (`estimate_then_fetch_demo.py`)

This example estimates a request without provider contact, compares it with a caller-owned ceiling, reduces the output allowance, runs the request under a `Budget`, and reconciles its custom client's settled cost with the reservation. Both `estimate` and runtime reservation call `bound_for_attempt`. The double settles below its reservation; the built-in client keeps at least the reservation after provider contact under a cost ceiling. See [extending.md](../docs/extending.md) for the client contract.

```bash
python3 examples/integration-demos/estimate_then_fetch_demo.py
```

**API key:** not required (`LedgerClient` is a local double that settles below its reservation) | **[Sample output](integration-demos/estimate_then_fetch_demo.output.txt)**

---

## scenario-demos/: end-to-end scenarios

### Diagnosing bounded enumerations (`bounded_enumeration_demo.py`)

A model asked to enumerate a set larger than its output allowance may return a valid but incomplete list. Counting records does not distinguish a complete result from a truncated one, and an empty gap list does not establish completeness.

The example uses a fictional 24-item catalog, not a real geographic list. It declares an identifying column with `plan.key`, enables continuations, and reads `gaps`. In this scripted run, exhausting the known fixture finishes without a gap; reaching the cap while items are still arriving reports `leaf_at_cap` and the count reached.

The three runs return 6 of 24 synthetic items without a diagnostic, 18 of 24 with a named gap, and all 24 without a gap. The scripted model returns at most six items per request so the example exercises merging and continuation accounting. The absence of a gap alone would not establish completeness for a real request.

```bash
python3 examples/scenario-demos/bounded_enumeration_demo.py
```

**API key:** not required | **[Sample output](scenario-demos/bounded_enumeration_demo.output.txt)**

### Retrieval modes on one WikiTabGen table (`partitioned_wikitabgen_demo.py`)

This example applies `flat`, `partitioned`, and `row_by_row` retrieval to the chemical-elements query from one table in the public [WikiTabGen dataset](https://github.com/analysis-bots/WikiTabGen). Every mode receives the same query and 11 columns. The context supplies atomic numbers 1–118, but no element names or attribute values. `row_by_row` uses atomic number `Z` as its key. `partitioned` uses `Period` and `Z`, with a declared finite domain of seven periods. Each call uses GPT-6 Luna with reasoning effort `none` and a 4,096-token output allowance. Continuations are disabled, so flat makes one capped call. Selected modes divide a $0.30, 160-call, 900-second budget into equal cost and time allowances; extra calls go to the earlier selections. A single selected mode gets the full allowance. Each mode's time allowance starts when it runs, and every mode also obeys the shared 900-second deadline. Unused allowances stay unused. The ledger retains attempt reservations when provider cost is unknown, so its charges can differ from the provider invoice. Row-by-row may stop before all 118 elements fit. The script prints each request, model response, returned records, diagnostics, and a run summary.

```bash
python3 examples/scenario-demos/partitioned_wikitabgen_demo.py --live
```

Omit `--live` to preview the inputs, settings, and per-mode allowances without contacting the provider. Repeat `--mode` to choose modes and their order, such as `--mode row_by_row --mode partitioned`. With no `--mode`, all three run. Flat mode can stop at its one-response output allowance before returning all 118 rows. Row counts vary. Use the preview and `estimate()` to inspect the configured allowance before a live run. The returned records are model output and should be checked against an authoritative source when accuracy matters.

**Requires for `--live`:** `OPENAI_API_KEY` and the `llm` extra | **[Preview output](scenario-demos/partitioned_wikitabgen_demo.output.txt)**

---

## Summary table

| File | Folder | API key | Model | Output |
|---|---|---|---|---|
| [`function_client_quickstart.py`](backends/function_client_quickstart.py) | backends | none | scripted `llm_fn` | [output](backends/function_client_quickstart.output.txt) |
| [`injected_client_demo.py`](backends/injected_client_demo.py) | backends | none | injected double | [output](backends/injected_client_demo.output.txt) |
| [`default_client_demo.py`](backends/default_client_demo.py) | backends | OpenAI | provider | [output](backends/default_client_demo.output.txt) |
| [`cache_stores_demo.py`](backends/cache_stores_demo.py) | backends | none | none | [output](backends/cache_stores_demo.output.txt) |
| [`file_cache_demo.py`](backends/file_cache_demo.py) | backends | OpenAI | provider | [output](backends/file_cache_demo.output.txt) |
| [`attrs_and_types_demo.py`](feature-demos/attrs_and_types_demo.py) | feature-demos | none | scripted `llm_fn` | [output](feature-demos/attrs_and_types_demo.output.txt) |
| [`limits_demo.py`](feature-demos/limits_demo.py) | feature-demos | none | scripted `llm_fn` | [output](feature-demos/limits_demo.output.txt) |
| [`estimate_demo.py`](feature-demos/estimate_demo.py) | feature-demos | none | none contacted | [output](feature-demos/estimate_demo.output.txt) |
| [`budget_demo.py`](feature-demos/budget_demo.py) | feature-demos | none | scripted and injected | [output](feature-demos/budget_demo.output.txt) |
| [`gaps_and_outcome_demo.py`](feature-demos/gaps_and_outcome_demo.py) | feature-demos | none | scripted `llm_fn` | [output](feature-demos/gaps_and_outcome_demo.output.txt) |
| [`planning_modes_demo.py`](feature-demos/planning_modes_demo.py) | feature-demos | none | scripted `llm_fn` | [output](feature-demos/planning_modes_demo.output.txt) |
| [`tool_surface_demo.py`](feature-demos/tool_surface_demo.py) | feature-demos | none | injected double | [output](feature-demos/tool_surface_demo.output.txt) |
| [`langgraph_direct_demo.py`](framework-demos/langgraph_direct_demo.py) | framework-demos | OpenAI | provider | [output](framework-demos/langgraph_direct_demo.output.txt) |
| [`langgraph_mcp_demo.py`](framework-demos/langgraph_mcp_demo.py) | framework-demos | OpenAI | provider | [output](framework-demos/langgraph_mcp_demo.output.txt) |
| [`register_source_demo.py`](integration-demos/register_source_demo.py) | integration-demos | none | scripted and injected | [output](integration-demos/register_source_demo.output.txt) |
| [`estimate_then_fetch_demo.py`](integration-demos/estimate_then_fetch_demo.py) | integration-demos | none | injected double | [output](integration-demos/estimate_then_fetch_demo.output.txt) |
| [`bounded_enumeration_demo.py`](scenario-demos/bounded_enumeration_demo.py) | scenario-demos | none | scripted `llm_fn` | [output](scenario-demos/bounded_enumeration_demo.output.txt) |
| [`partitioned_wikitabgen_demo.py`](scenario-demos/partitioned_wikitabgen_demo.py) | scenario-demos | OpenAI with `--live` | GPT-6 Luna | [preview](scenario-demos/partitioned_wikitabgen_demo.output.txt) |

The rest of the project's documentation lives under `docs/`.
