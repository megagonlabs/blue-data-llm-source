# blue-data-llm-source Examples

Runnable examples are grouped by backend, feature, integration, and usage scenario.

Each script has a matching `.output.txt` file containing captured standard output. Thirteen examples were captured offline; four were captured from provider-backed runs.

Thirteen of the seventeen scripts require no credential, network access, or configuration. They use a scripted `llm_fn` or an injected client to exercise prompt rendering, attribute alignment, type checking, limits, budgets, gaps, outcome precedence, planning modes, and the registry round trip. The remaining four scripts require a provider key. Each script identifies its execution mode in a header comment, and the table at the end of this file records the same distinction.

## Quick start

Install `blue-data` and the library with its `llm` extra as shown in [Installation](../docs/installation.md), then activate that environment. Run every command below from the repository root.

```bash
python3 examples/backends/function_client_quickstart.py
```

The quickstart needs no credential. The four provider-backed scripts read `OPENAI_API_KEY` from the environment. If it is not already set, run this block first and enter the key at the prompt. The shell hides the value and keeps it out of history.

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

This example uses `DefaultLlmClient` through litellm and reports the model, token counts, and settled cost reconciled against the earlier estimate. The client is injected so `close()` releases the cache backends it opens. The library does not read credentials; the provider SDK reads `OPENAI_API_KEY`. If it is missing, the call raises a `TransportError` mentioning credentials.

```bash
python3 examples/backends/default_client_demo.py
```

**Requires:** `OPENAI_API_KEY`, and the `llm` extra | **[Sample output](backends/default_client_demo.output.txt)**, from a real provider call

### The two cache stores (`cache_stores_demo.py`)

`MemoryCache` and `FileCache` driven through the `get`/`set` pair the `CacheStore` protocol declares, the same pair `DefaultLlmClient` uses. Bounded LRU eviction, persistence across instances, and a `clear()` that will not delete a file the cache did not write, which matters because a cache directory is one a caller merely selected.

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

Ten canned responses cover the main attribute outcomes. A numeric string under a declared `integer` is converted; a number under a declared `string` is retained and reported as a mismatch. The example also covers `unmapped_keys`, `key_collisions`, `unchecked_types`, and removal of a record that answers none of the requested attributes.

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

The one cost demo that needs no credential, because `estimate` contacts nothing by construction. A priced model, the cost floor that the output allowance alone implies, a model the rate table has never heard of answering with a reason instead of a guess, caller-supplied rates for that model, an `llm_fn` that cannot price itself, and the proof that `plan.*` settings are not read.

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

The example prints the call sequence for `flat`, flat with continuations, `partitioned`, and `row_by_row`, making the cost of each shape visible. The scripted planner recognises each planning prompt by its opening line and uses a fixed response, so the same four sequences appear on every run.

```bash
python3 examples/feature-demos/planning_modes_demo.py
```

**API key:** not required | **[Sample output](feature-demos/planning_modes_demo.output.txt)**

### The orchestrator tool surface (`tool_surface_demo.py`)

`get_nl_tools` advertises four of the base class's eight tools and `execute_nl_tool` serves exactly those four, refusing anything else by name. `execute_query_nl_tools` (the tool-driven loop) is refused outright, because it drives up to ten provider calls this source's ceilings do not govern.

```bash
python3 examples/feature-demos/tool_surface_demo.py
```

**API key:** not required | **[Sample output](feature-demos/tool_surface_demo.output.txt)**

---

## framework-demos/: agentic framework integrations

Both examples use [LangGraph](https://github.com/langchain-ai/langgraph) and are provider-backed. One implements a fixed pipeline; the other lets an agent select its own tools.

### Direct Python API (`langgraph_direct_demo.py`)

The pipeline has two nodes. `retrieve` calls `LlmSource.fetch` under a `Budget` owned by the graph, then `respond` composes a sentence. The records travel with `meta.outcome` and `meta.gaps`; the writing node is instructed to report when retrieval is incomplete. An agent that reads only `records` cannot distinguish a complete answer from a truncated one.

```bash
pip install langgraph langchain-openai
python3 examples/framework-demos/langgraph_direct_demo.py
```

**Requires:** `OPENAI_API_KEY`, `langgraph`, `langchain-openai` | **[Sample output](framework-demos/langgraph_direct_demo.output.txt)**, from a real agent run

### MCP-based integration (`langgraph_mcp_demo.py`)

No source-specific code on the LangGraph side: `langchain-mcp-adapters` launches `blue-data-llm-mcp` as a stdio subprocess, discovers its five tools, and a ReAct agent decides what to call. The system prompt makes the agent price a request with `llmdb_estimate` before running it, and pass its own ceilings. An operator can also set ceilings for every call through the server's environment, which a call can lower but never raise; without one, a call that sets no ceiling has none.

```bash
pip install langgraph langchain-openai langchain-mcp-adapters
pip install -e lib/ -e platform/llm-mcp/
python3 examples/framework-demos/langgraph_mcp_demo.py
```

**Requires:** `OPENAI_API_KEY`, `langgraph`, `langchain-openai`, `langchain-mcp-adapters`, and the MCP package on PATH | **[Sample output](framework-demos/langgraph_mcp_demo.output.txt)**, from a real agent run

---

## integration-demos/: two or more features working together

### Registering the source in the data layer (`register_source_demo.py`)

The example registers a built instance with `register_source`, then retrieves it with `connect_source`. After the live instance is dropped, `connect_source` rebuilds an equivalent source from the recorded class path, making the registration durable. It then exercises the shared interface: `execute_query_nl` with a string, `execute_query` with an `op` dict, and a `limits.*` ceiling in `optional_properties` that applies to one call.

```bash
python3 examples/integration-demos/register_source_demo.py
```

**API key:** not required | **[Sample output](integration-demos/register_source_demo.output.txt)**

### Estimate, decide, fetch, reconcile (`estimate_then_fetch_demo.py`)

This example estimates a request without provider contact, compares it with a caller-owned ceiling, reduces the output allowance, runs the request under a `Budget`, and reconciles the settled cost with the reservation. Both `estimate` and runtime reservation call `bound_for_attempt`, so they use the same bound.

```bash
python3 examples/integration-demos/estimate_then_fetch_demo.py
```

**API key:** not required (`LedgerClient` is a local double whose accounting path is the real one) | **[Sample output](integration-demos/estimate_then_fetch_demo.output.txt)**

---

## scenario-demos/: end-to-end scenarios

### Knowing when an enumeration is short (`bounded_enumeration_demo.py`)

A model asked to enumerate a set larger than its output allowance may return a valid but incomplete list. Counting records does not distinguish a complete result from a truncated one, and an empty gap list does not establish completeness.

The example declares an identifying column with `plan.key`, enables continuations, and reads `gaps`. A run that exhausts the set finishes without a gap; a run that reaches its cap while records are still arriving reports `leaf_at_cap` and the count reached.

The three runs return 6 of 24 records without a diagnostic, 18 of 24 with a named gap, and 24 of 24 without a gap. The scripted model returns at most six items per request so the example exercises merging and continuation accounting.

```bash
python3 examples/scenario-demos/bounded_enumeration_demo.py
```

**API key:** not required | **[Sample output](scenario-demos/bounded_enumeration_demo.output.txt)**

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

The rest of the project's documentation lives under `docs/`.
