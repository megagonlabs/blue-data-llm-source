# blue-data-llm-source Examples

Runnable examples organized by purpose, mirroring the layout the sibling `blue-data-user-source` and `blue-data-kb-source` repositories use for theirs.

Every `.output.txt` in this directory is the captured stdout of the script beside it. Every script here has one: the thirteen that need no credential were captured offline, and the four that reach a provider were captured from real runs against it.

Thirteen of the seventeen scripts run with no credential, no network and no configuration at all. The remaining four need a provider key, and all four are captured from real runs. That is not a trick of the examples: `estimate` contacts nothing by construction, and every other behaviour worth demonstrating (attribute alignment, type checking, limits, budgets, gaps, outcome precedence, planning modes, the registry round trip) can be driven by a scripted `llm_fn` or an injected client that the source treats exactly as it treats a provider. A script driven by a canned response says so in its own header comment, and the table at the end of this file says so too. None of them is dressed up as a provider demo.

## Quick start

```bash
(cd lib && scripts/install.sh)
python3 examples/backends/function_client_quickstart.py
```

`scripts/install.sh` installs `blue-data` from its checkout and then this package with the `dev` and `llm` extras, so litellm and diskcache are already in place. Every command below is run from the repository root. The quickstart needs no credential; the four provider-backed scripts need one exported before they will do anything:

```bash
export OPENAI_API_KEY=<YOUR_PROVIDER_KEY>   # placeholder: your own key
python3 examples/backends/default_client_demo.py
```

The CLI is a separate package and a separate story: `blue-llmdb data query "..."` runs the same library from a shell. Its options are documented in `docs/cli.md`, not here.

---

## backends/: the client axis and the cache axis

This source has no storage backend. What varies underneath it is **which client answers** (a caller's `llm_fn`, an injected client, or the built-in `DefaultLlmClient` driving a provider) and **where responses are cached**. Start here when wiring the library into an application, because that choice decides which ceilings can bind and whether the accounting in a result means anything.

### Minimal quickstart (`function_client_quickstart.py`)

One fetch answered by a local function. Everything except the model runs for real: prompt rendering, JSON parsing, attribute alignment, type checking, result metadata. It closes on the one thing to understand about this path: `meta.accounting_known` is False, so a `cost` of `0.0` means "not measured", not "free".

```bash
python3 examples/backends/function_client_quickstart.py
```

**API key:** not required | **[Sample output](backends/function_client_quickstart.output.txt)**

### An injected client the source can bound (`injected_client_demo.py`)

The contrast with the quickstart. A client that declares its axes through `budget_capabilities()`, claims against the run's `Budget`, and appends a `Usage` gets a real model name, token counts, a settled cost and `accounting_known=True` — and a cost ceiling that actually binds. The same ceiling over an `llm_fn` is refused, with the library's own explanation of why a ceiling it cannot hold is worse than none.

```bash
python3 examples/backends/injected_client_demo.py
```

**API key:** not required (`LedgerClient` is a local double that prices itself) | **[Sample output](backends/injected_client_demo.output.txt)**

### Provider-backed fetch (`default_client_demo.py`)

`DefaultLlmClient` through litellm: real model, real token counts, real settled cost, reconciled against the estimate taken beforehand. The client is injected rather than left to the source to build, so `close()` releases the cache backends it opens. This library reads no credential itself. The provider SDK reads `OPENAI_API_KEY`, and a missing one arrives as a `TransportError` mentioning credentials.

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

`llm.cache` with `llm.cache_dir`: two identical fetches, the second served from disk with no provider call, no tokens and no API cost. The cache lives inside the client, below the source, so the first call has to be real, which is the whole reason this one needs a credential and `cache_stores_demo.py` does not.

```bash
python3 examples/backends/file_cache_demo.py
```

**Requires:** `OPENAI_API_KEY`, and the `llm` extra (diskcache comes with it) | **[Sample output](backends/file_cache_demo.output.txt)**, from a real provider call followed by a cache hit

---

## feature-demos/: one feature per script

Each script is a single-feature deep dive, driven by a scripted `llm_fn` or an injected client so the behaviour being shown is deterministic rather than whatever a model did that afternoon. All seven run offline.

### Attributes and declared types (`attrs_and_types_demo.py`)

Ten canned responses, one per thing that can happen to a requested attribute. The asymmetry is the part worth reading: a numeric string under a declared `integer` is converted, because that is what the caller asked for, while a number under a declared `string` is reported rather than stringified, because that is the thing the caller wanted to be told about. Also `unmapped_keys`, `key_collisions`, `unchecked_types`, and the record that answers none of the requested attributes and is dropped.

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

One `Budget` handed to three fetches: each derives a child, spends from what the parent has left, and the third comes back empty with `stopped_by` naming the axis rather than raising. Shown on the row axis with an `llm_fn` and on the call and cost axes with a client that declares them. The cost refusal lands on the reservation, not on the settled price, which is the part that surprises people.

```bash
python3 examples/feature-demos/budget_demo.py
```

**API key:** not required | **[Sample output](feature-demos/budget_demo.output.txt)**

### Gaps and outcome precedence (`gaps_and_outcome_demo.py`)

Every member of the `outcome` vocabulary produced on purpose, plus the gap diagnostics beside them. It ends on the precedence rule: a partly-answered plan outranks a cap that fired at the same moment, and the cap is still in `stop_reasons` rather than being lost to the promotion.

```bash
python3 examples/feature-demos/gaps_and_outcome_demo.py
```

**API key:** not required | **[Sample output](feature-demos/gaps_and_outcome_demo.output.txt)**

### Planning modes (`planning_modes_demo.py`)

`flat`, flat with continuations, `partitioned`, and `row_by_row`, each with its call sequence printed so the cost of a shape is visible rather than described. The scripted planner recognises each planning prompt by its opening line and answers from a fixed script, so the same four sequences come back every run.

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

Both use [LangGraph](https://github.com/langchain-ai/langgraph), following the two sibling repos' own precedent, and contrast a fixed pipeline against an agent choosing its own tools. Both are provider-backed.

### Direct Python API (`langgraph_direct_demo.py`)

The pipeline has two nodes. `retrieve` calls `LlmSource.fetch` under a `Budget` the graph owns, then `respond` composes a sentence. The point of the split is what crosses between the nodes: `meta.outcome` and `meta.gaps` travel with the records, and the writing node is told to say when the retrieval was short. An agent that reads only `records` cannot tell a complete answer from a truncated one.

```bash
pip install langgraph langchain-openai
python3 examples/framework-demos/langgraph_direct_demo.py
```

**Requires:** `OPENAI_API_KEY`, `langgraph`, `langchain-openai` | **[Sample output](framework-demos/langgraph_direct_demo.output.txt)**, from a real agent run

### MCP-based integration (`langgraph_mcp_demo.py`)

No source-specific code on the LangGraph side: `langchain-mcp-adapters` launches `blue-data-llm-mcp` as a stdio subprocess, discovers its five tools, and a ReAct agent decides what to call. The system prompt makes the agent price a request with `llmdb_estimate` before running it, and pass its own ceilings, because the server constructs its source with no properties, so a call that sets no ceiling has none.

```bash
pip install langgraph langchain-openai langchain-mcp-adapters
pip install -e lib/ -e platform/llm-mcp/
python3 examples/framework-demos/langgraph_mcp_demo.py
```

**Requires:** `OPENAI_API_KEY`, `langgraph`, `langchain-openai`, `langchain-mcp-adapters`, and the MCP package on PATH | **[Sample output](framework-demos/langgraph_mcp_demo.output.txt)**, from a real agent run

---

## integration-demos/: two or more features working together

### Registering the source in the data layer (`register_source_demo.py`)

`register_source` with a built instance, then `connect_source` returning it. Once the live instance is dropped, `connect_source` rebuilds an equivalent source from the recorded class path, which is what makes a registration durable rather than a reference. Then the shared surface: `execute_query_nl` with a string, `execute_query` with an `op` dict, and a `limits.*` ceiling in `optional_properties` binding one call.

```bash
python3 examples/integration-demos/register_source_demo.py
```

**API key:** not required | **[Sample output](integration-demos/register_source_demo.output.txt)**

### Estimate, decide, fetch, reconcile (`estimate_then_fetch_demo.py`)

The hero script: size a request without contacting anything, refuse it against a ceiling the caller owns, resize it with an output allowance the answer actually needs, run it under a `Budget` carrying that ceiling, then reconcile the settled cost against the reservation. `estimate` and the run agree by construction (both ask the client the same question, `bound_for_attempt`), so a caller cannot be sized against one figure and enforced against another.

```bash
python3 examples/integration-demos/estimate_then_fetch_demo.py
```

**API key:** not required (`LedgerClient` is a local double whose accounting path is the real one) | **[Sample output](integration-demos/estimate_then_fetch_demo.output.txt)**

---

## scenario-demos/: value proposition demos

### Knowing when an enumeration is short (`bounded_enumeration_demo.py`)

**Problem:** a model asked to enumerate a set larger than its output allowance returns a valid, well-formed, short list, with no marker in it. A caller counting records cannot tell a complete answer from a truncated one, and an empty gap list is not a completeness claim.

**Solution:** declare an identifying column with `plan.key`, allow continuations, and read `gaps`. A run that exhausted the set finishes clean; a run that hit its cap while records were still arriving says `leaf_at_cap` and names the count it reached.

**Result:** 6 of 24 records with no diagnostic → 18 of 24 with a named gap → 24 of 24 clean, computed live from real merging and real continuation accounting. The model is scripted to return at most six items per ask, which is the behaviour being worked around.

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
