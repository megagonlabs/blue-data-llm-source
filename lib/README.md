# blue-data-llm-source

Python library for querying a language model through the `DataSource` interface and returning structured records with execution metadata.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
scripts/install.sh               # blue-data from its checkout, then this package
BLUE_DATA_PATH=/path/to/blue-data/lib scripts/install.sh   # if it lives elsewhere
```

The core depends on `blue-data` and `tenacity`. `blue-data` supplies the `DataSource` base class and also installs `pandas`, `numpy`, and `jsonpath-ng`. The `llm` extra adds `litellm` and `diskcache`; both are imported lazily, so the base package and its tests work without them.

## LlmSource

```text
LlmSource(name="llm", properties=None, client=None)
```

`LlmSource` inherits `blue_data.source.source.DataSource` and implements the shared query and metadata surface. The base class provides default empty results for several methods, so a contract test verifies that every required method resolves to `LlmSource`. Transaction methods raise `NotImplementedError`; the supported orchestrator-tool methods are narrowed locally, and `execute_query_nl_tools` is refused.

Inheriting the base class does not make the source reachable by protocol name. `DataRegistry.connect_source` returns the live registered instance while one is present, and reconstructs an equivalent source from the recorded class path once it is not. The route is to build the source yourself and hand the instance to `register_source`. That is enough: the registry records the class alongside `name` and `properties`, and rebuilds an `LlmSource` from that record, so a source registered this way survives a round trip through the registry.

`properties` carries connection details and configuration. `client` injects a model client, so tests and alternative providers need no subclassing. The full contract is in [Injecting a client](#injecting-a-client) below. In brief, the client provides two methods that take the prompt first and merged per-call properties second, plus an optional method declaring which ceilings it supports.

[COMPATIBILITY.md](COMPATIBILITY.md) records what this library does differently from the data-source design document, and from the behaviour a caller being ported was written against, including the whole budget contract in one place. Read it before porting a caller.

### fetch

```text
fetch(query, attrs=None, context=None, max_records=None, properties=None, llm_fn=None,
      budget=None) -> FetchResult
afetch(...)   # the async form; fetch() wraps it
```

`query` is the question. `attrs` is either a list of names or a list of `{"name": ..., "type": ...}` dicts. `context` is free text that may clarify an attribute name. `max_records` caps the returned rows. `llm_fn` is a `(prompt) -> str` callable used as the client for that call. `budget` is a `Budget` you own. When supplied, each fetch derives a child from it, so ceilings accumulate across calls using that budget. Without it, the fetch builds a budget from the configured limits for that call alone.

`fetch` raises `ConfigError` if called from inside a running event loop. Use `afetch` there.

Returns a `FetchResult` with `records` and `meta`. A direct Python fetch with rule-backed
distribution search enabled can also expose an optional `planning_receipt` property; the dataclass
itself retains its two original fields.

### estimate

```text
estimate(query, attrs=None, context=None, properties=None, llm_fn=None) -> Estimate
```

Returns an upper cost bound for one **flat** fetch of `query` without contacting a provider. The method ignores `plan.*` settings, so a request with parts or continuations can cost several times this figure. It renders the actual prompt and bounds the entire fetch, including retries. `max_cost` is calculated by adding `max_cost_per_attempt` once per permitted attempt in the same order as the budget ledger. Repeated addition and multiplication can differ in binary floating point from six attempts onward; even a one-unit difference in the last place can cause the final attempt of a request sized to its estimate to be refused. The default is three attempts, so this matters only when `limits.max_retries` exceeds four. Use the whole-fetch `max_cost`, not the per-attempt figure, when setting a ceiling.

`llm_fn` is accepted so the estimate is asked of whatever would actually answer, and a callable cannot price itself, so a request against one is reported unestimable. The same is true of an injected client that does not implement `bound_for_attempt`.

The returned `Estimate` carries `bounded`, `max_cost`, `max_cost_per_attempt`, `max_attempts`, `model`, `prompt_bytes`, `max_output_tokens`, `attempts_within_ceiling` (how many attempts fit under a `limits.max_cost` ceiling, `None` without one) and, when unbounded, `reason`.

A planner sizing a fan-out uses it to decide how wide to go; an operator uses it to check a request against a ceiling before spending anything on it.

For a model without known rates, the method returns `bounded=False` and a `reason` that identifies the missing information. An empty request returns `bounded=True` with a `max_cost` of zero because it never reaches a model.

```python
from blue_data_llm_source import LlmSource

source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 512})
sized = source.estimate("commercial airports in France", attrs=["name", "iata_code"])
print(sized.bounded, round(sized.max_cost, 6), sized.prompt_bytes, sized.max_output_tokens)

unpriced = source.estimate("the same question", properties={"llm.model": "an-unlisted-model"})
print(unpriced.bounded, unpriced.max_cost, unpriced.reason)
```

### The source query surface

```text
execute_query(query, database=None, collection=None, optional_properties=None) -> list[dict]
execute_query_nl(query, database=None, collection=None, schema=None, llm_fn=None,
                 optional_properties=None) -> list[dict]
translate_nl_to_native_query(query, schema, llm_fn) -> str | None
```

`execute_query` takes either a natural language string or a dict with an `op` key. Supported ops are `LLM.FETCH`, `LLM.MODELS`, and `LLM.ESTIMATE`; an unknown op raises `QueryError` listing them. `LLM.ESTIMATE` reads the same `query`, `attrs` or `attr_names`, and `context` keys as `LLM.FETCH`. It returns the estimate as a one-record list, matching this surface's return type. Call `estimate` directly to get the `Estimate` object.

For every other source in the layer, `llm_fn` translates natural language into a native dialect. This source has no other dialect, so `translate_nl_to_native_query` is the identity and `llm_fn` is used as the model client instead of being ignored.

Both methods take `optional_properties`, and that is where a ceiling goes when you are reaching the source through this surface. A `limits.*` key placed there is merged over the instance's properties for the duration of that call and is not written back, so it bounds the request it was passed with and nothing after it.

```python
from blue_data_llm_source import LlmSource


def answer(prompt: str) -> str:
    return '[{"name": "Charles de Gaulle"}, {"name": "Orly"}]'


source = LlmSource()
bounded = source.execute_query_nl(
    "commercial airports in France",
    llm_fn=answer,
    optional_properties={"attrs": ["name"], "limits.max_rows": 1},
)
unbounded = source.execute_query_nl(
    "commercial airports in France",
    llm_fn=answer,
    optional_properties={"attrs": ["name"]},
)
print(bounded)
print(unbounded)
```

### The orchestrator tool surface

```text
get_nl_tools(database=None, collection=None) -> list[dict]
execute_nl_tool(name, tool_input, database=None, collection=None,
                optional_properties=None) -> Any
```

`DataSource` offers a tool-calling interface for an orchestrating model: a source advertises tools, and the caller invokes them one at a time. Its tools describe a relational source (databases, collections, columns, row samples, and foreign keys). Four of its eight tools are supported here.

| Tool | Returns |
|------|---------|
| `list_databases` | `["llm"]`, the one virtual database |
| `list_collections` | `["public"]`, the one virtual collection |
| `get_schema` | The virtual entity with its one text attribute, and an empty relation map |
| `execute_query` | The records for `tool_input["query"]`, fetched through the query surface |

The other four are not advertised. `get_metadata`, `get_column_stats`, and `get_relations` return empty results without explanation. `get_sample` raises a `TypeError` in the base dispatcher, which passes `row_sample_size` to `fetch_entity_stats`, a method that takes no such argument. The definitions above come from the base class and are filtered by name, so the advertised schema cannot drift from its dispatcher.

`execute_nl_tool` refuses any other name with a `ConfigError` naming the four that are served. Nothing on this path raises outside `LlmSourceError`, including a name that is not a string and a `tool_input` missing a key the tool requires.

`execute_query` is the one tool here that reaches a provider, and it takes its ceilings where the query surface takes them: a `limits.*` key in `optional_properties` reaches `execute_query` and binds that call.

`execute_query_nl_tools` is not served and raises `ConfigError`. It can make up to ten provider calls, which this source's ceilings do not govern. `execute_query_nl` is the natural-language route; its spend and time ceilings are enforced.

### Metadata

`fetch_databases()` returns one virtual database, `fetch_database_collections()` one virtual collection, and `fetch_database_collection_entities()` one virtual entity with one text attribute, so a registry sync has a hierarchy to walk. `fetch_metadata()` returns the protocol and variant. The remaining `fetch_*` methods return empty.

`protocol` is always `llm` and cannot be overridden. `variant` defaults to `openai` and can be set through `properties["connection"]["variant"]`.

## Injecting a client

Injection is structural, and the shape is checked when the client is resolved: a client missing a method, or one whose signature cannot be called as `complete(prompt, properties)`, is refused with a `ConfigError` naming the contract rather than failing later with a raw `TypeError`. What no check can prove is that a client honours a budget it declares, so capability declarations remain its own responsibility. This is the whole contract:

```python
from typing import Any, Protocol


class InjectedClient(Protocol):
    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str: ...
    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str: ...

    # Optional. Without it, a cost, time, or call ceiling is refused for this client.
    def budget_capabilities(self) -> frozenset[str]: ...

    # Optional, and required for `estimate` to answer, together with declaring
    # `Capability.ESTIMATE`. Without both, a request against this client is reported
    # unestimable rather than guessed at.
    def bound_for_attempt(self, prompt: str, properties: dict[str, Any]) -> float: ...
```

**Declaring `Capability.ESTIMATE` adds two requirements.** `bound_for_attempt` must return a true upper bound on one provider attempt, and one call must make no more than `budget.effective_retries() + 1` attempts using the `budget` passed in `properties`. A client whose retry or fallback policy ignores this allowance can exceed its published estimate and cause a later attempt to be refused. Such a client should omit `ESTIMATE`; the method then reports `bounded=False` with a reason.

`bound_for_attempt` is what makes `estimate` and the run agree: the estimate and each
reservation both call it, so the figure a caller sizes against and the figure a ceiling
enforces cannot be computed two different ways. It returns the most one attempt at *prompt*
could cost, and `estimate` accumulates it once per attempt a fetch permits. A client that
cannot price itself simply omits it. `estimate` then returns `bounded=False` with a reason,
and a cost ceiling against that client is refused separately by `budget_capabilities`.
`DefaultLlmClient` implements the method from
the same call arguments the attempt itself uses.

Both completion methods take the prompt first and the merged per-call properties second, and both return the model's text. `afetch` calls `acomplete`, and `fetch` wraps `afetch`, so the asynchronous form is the one every fetch reaches; `complete` is there for a caller driving the client directly.

Two keys arrive in `properties` on every call the source makes. `budget` carries the run's `Budget`, which the client claims against per provider attempt. `usage_sink` carries a list this call owns, and whatever `Usage` entries the client appends to it become the fetch's token counts and model name; the fetch reads nothing else, because a client's own history cannot be sliced back into per-request totals when two fetches share it. Both are passed per call instead of on the client for the same reason: one client can serve several concurrent fetches.

`budget_capabilities` says which ceilings the client can be held to, returning axis names from `Capability`. It is a method, not an attribute, so that satisfying the contract takes a deliberate implementation rather than a name that happens to match. Every configured ceiling on an axis the client does not name is refused with a `ConfigError` at the first fetch, and a client omitting the method declares nothing and is refused all three provider-level ceilings; `limits.max_rows` binds regardless, since the source counts the records itself.

```python
import asyncio
from typing import Any

from blue_data_llm_source import Budget, Capability, LlmSource, Usage


async def my_provider_call(prompt: str) -> str:
    return '[{"country": "France"}, {"country": "Germany"}]'


PER_ATTEMPT_BOUND = 0.002


class MyClient:
    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def bound_for_attempt(self, prompt: str, properties: dict[str, Any]) -> float:
        # The same figure the reservation below claims. Declaring Capability.ALL includes
        # ESTIMATE, so without this the client says it supports a bounded estimate and
        # then cannot produce one.
        return PER_ATTEMPT_BOUND

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return asyncio.run(self.acomplete(prompt, properties))

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        props = properties or {}
        budget = props.get("budget") or Budget()
        # `attempt`, not `reserve`: its cleanup is installed before the claim is taken, so an
        # interrupt between them cannot leave a claim with no handle to resolve it.
        with budget.attempt(PER_ATTEMPT_BOUND) as reservation:  # the most this call can cost
            try:
                text = await my_provider_call(prompt)
            except BaseException:
                reservation.keep_unpriced()
                raise
            reservation.settle(0.0011)  # what it did cost
        sink = props.get("usage_sink")
        if sink is not None:
            sink.append(Usage(model="my-model", prompt_tokens=188, completion_tokens=89))
        return text


source = LlmSource(
    properties={"limits.max_cost": 0.05, "limits.max_calls": 4},
    client=MyClient(),
)
sized = source.estimate("the member states of the European Union", attrs=["country"])
print(sized.bounded, sized.max_cost)  # True 0.006 (accumulated over the attempts)

result = source.fetch("the member states of the European Union", attrs=["country"])
print(result.records)
print(result.meta.model, result.meta.calls, round(result.meta.cost, 4), result.meta.outcome)
```

Claim inside `budget.attempt(...)` and resolve the claim on every exit path. The context manager registers cleanup before taking the claim; `reserve` is also available but leaves a window in which an interrupt can orphan a claim. `settle(actual)` releases any excess, `keep_unpriced()` retains the claim for an attempt that reached the provider but cannot be priced, and `release()` returns the claim for an attempt that never reached the provider. An outstanding claim reduces the budget's remaining headroom.

Declare only capabilities the client can guarantee. `DefaultLlmClient` declares `Capability.ALL` because it makes the provider call itself. `FunctionClient`, which wraps an `llm_fn`, declares only `Capability.ROWS`: it cannot know the function's cost, stop it at a deadline, or determine how many provider calls one invocation makes. A call, cost, or time ceiling on an `llm_fn` is therefore refused rather than settled at zero.

## Configuration

Provider settings are `llm.*` keys in `properties`. The accepted set is closed; an unknown key raises `ConfigError` listing the accepted keys.

| Key | Meaning |
|---|---|
| `llm.model` | Model name. Defaults to `gpt-6-luna`. An `openai/` prefix is read as the same model |
| `llm.reasoning_effort` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`. Each model accepts a subset and has its own default, listed under [Supported models](#supported-models) |
| `llm.temperature`, `llm.max_tokens`, `llm.stream`, `llm.timeout` | Passed to the provider with model-specific argument names; what `llm.stream=True` does to the accounting is [below](#streaming) |
| `llm.cache`, `llm.cache_dir` | Enable and locate the on-disk cache |
| `llm.max_parallel` | Concurrency bound, per event loop |

The numeric keys carry a range as well as a name, checked at the same boundary and raising the same `ConfigError`. `llm.max_tokens` is a whole number of at least 1, or `None` for no cap. A reasoning model defaults to 25,000 and sends it as `max_completion_tokens`; GPT-4.1 and GPT-4o default to 4,096 and send `max_tokens`. `llm.temperature` is a finite number; active reasoning and temperature cannot be sent together. `llm.timeout` is a finite positive number of seconds, and `llm.max_parallel` a whole number of at least 1.

`llm.cache`, `llm.cache_dir`, and `llm.max_parallel` are consumed here and never sent to the provider, and so are `llm.price_per_input_token` and `llm.price_per_output_token`, which supply the rates for a model `pricing.PRICES` does not carry.

Each distinct `llm.cache_dir` opens an on-disk backend that holds file descriptors. A client keeps at most eight open and closes the least recently used beyond that. `DefaultLlmClient.close()` releases them all, and the client is also a context manager. A short-lived script needs neither. A long-lived service that rotates cache directories by tenant or day should close the client, or it may eventually exhaust the process's file descriptors.

### Supported models

One table in `llm/models.py` holds, for each model, the request it is sent and the rates its cost bound is built from, so the two cannot disagree. `blue-llmdb model list` and `priceable_models()` list the names, and `blue-llmdb model inspect NAME` and `published_defaults(NAME)` report one model's entry. Prices are USD per million tokens, copied from OpenAI's published pricing, and go stale.

| Model | Reasoning effort (default in bold) | Input | Cached input | Cache write | Output |
|---|---|---|---|---|---|
| `gpt-6-astra` | low, **medium**, high, xhigh, max | $10.00 | $1.00 | $12.50 | $50.00 |
| `gpt-6-sol` | none, low, **medium**, high, xhigh, max | $2.00 | $0.20 | $2.50 | $10.00 |
| `gpt-6-luna` | none, low, **medium**, high, xhigh, max | $0.10 | $0.01 | $0.125 | $0.50 |
| `gpt-5.6`, `gpt-5.6-sol` | none, low, **medium**, high, xhigh, max | $4.00 | $0.40 | $5.00 | $20.00 |
| `gpt-5.6-terra` | none, low, **medium**, high, xhigh, max | $2.00 | $0.20 | $2.50 | $12.00 |
| `gpt-5.6-luna` | none, low, **medium**, high, xhigh, max | $0.20 | $0.02 | $0.25 | $1.20 |
| `gpt-5.5` | none, low, **medium**, high, xhigh | $5.00 | $0.50 | — | $30.00 |
| `gpt-5.4` | **none**, low, medium, high, xhigh | $2.50 | $0.25 | — | $15.00 |
| `gpt-5.4-mini` | **none**, low, medium, high, xhigh | $0.75 | $0.075 | — | $4.50 |
| `gpt-5.4-nano` | **none**, low, medium, high, xhigh | $0.20 | $0.02 | — | $1.25 |
| `gpt-5` | minimal, low, **medium**, high | $1.25 | $0.125 | — | $10.00 |
| `gpt-5-mini` | minimal, low, **medium**, high | $0.25 | $0.025 | — | $2.00 |
| `gpt-5-nano` | minimal, low, **medium**, high | $0.05 | $0.005 | — | $0.40 |
| `gpt-4.1` | not accepted | $2.00 | $0.50 | — | $8.00 |
| `gpt-4.1-mini` | not accepted | $0.40 | $0.10 | — | $1.60 |
| `gpt-4.1-nano` | not accepted | $0.10 | $0.025 | — | $0.40 |
| `gpt-4o` | not accepted | $2.50 | $1.25 | — | $10.00 |
| `gpt-4o-2024-05-13` | not accepted | $5.00 | — | — | $15.00 |
| `gpt-4o-mini` | not accepted | $0.15 | $0.075 | — | $0.60 |
| `o3` | low, **medium**, high | $2.00 | $0.50 | — | $8.00 |
| `o3-mini` | low, **medium**, high | $1.10 | $0.55 | — | $4.40 |
| `o4-mini` | low, **medium**, high | $1.10 | $0.275 | — | $4.40 |
| `o1` | low, **medium**, high | $15.00 | $7.50 | — | $60.00 |
| `gpt-4-turbo` | price only | $10.00 | — | — | $30.00 |
| `gpt-4` | price only | $30.00 | — | — | $60.00 |
| `gpt-3.5-turbo` | price only | $0.50 | — | — | $1.50 |

`gpt-5.6` is OpenAI's alias for GPT-5.6 Sol and is billed as Sol. A name matches its row exactly or with a dated suffix such as `gpt-5.5-2026-04-23`, after an `openai/` prefix is removed; a snapshot with its own row, such as `gpt-4o-2024-05-13`, is priced by that row rather than by the family name it extends. Any longer name is a different model.

Every reasoning model is sent `reasoning_effort`, defaulting as above, and a 25,000-token allowance as `max_completion_tokens`. GPT-6 Luna and Sol, and the GPT-5.6, GPT-5.5 and GPT-5.4 families, accept `temperature` only at effort `none`. GPT-6 Astra, the GPT-5 family and the o-series refuse it at every effort. GPT-4.1 and GPT-4o accept no reasoning effort, are sent temperature 0 unless you set one, and get a 4,096-token allowance as `max_tokens`. An effort or a temperature the model's entry rules out raises `ConfigError` before anything is reserved, so it costs nothing.

The GPT-6, GPT-5.6, GPT-5.5 and GPT-5.4 models (not the GPT-5.4 mini and nano) have a long-context tier: above 272,000 prompt tokens the whole request is billed at twice the input and cache rates and one and a half times the output rate. The GPT-6, GPT-5.6, GPT-5.5 and GPT-5.4 families, mini and nano included, carry OpenAI's 10% uplift for regional processing. The cost bound applies both; [The cost bound](#the-cost-bound) says how.

The price-only rows carry rates and nothing else. A call to one of them is sent exactly as configured, with no provider named. A bare name from another provider's family that the installed LiteLLM cannot route is refused with a `ConfigError` before anything is reserved, telling you to name the provider as `provider/model`.

A bare name in one of OpenAI's naming families (`gpt-`, `chatgpt-`, or `o` and a digit) is sent to OpenAI even when the table does not carry it, because LiteLLM's bundled price map lags new models and cannot otherwise route them. A `provider/model` name keeps the routing you gave it. A name the table does not carry gets no default effort and no default temperature, and its allowance is sent as `max_tokens` for LiteLLM to translate; it has no rates either, so a cost ceiling on it needs `llm.price_per_input_token` and `llm.price_per_output_token`.

**Known limitations.**

- GPT-5.6 Sol's rates, and so the `gpt-5.6` alias's, are a promotion OpenAI offers through at least 2026-11-21. When the price rises, the table's rates are too low and so is every bound built from them, until the table is updated. Supply `llm.price_per_input_token` and `llm.price_per_output_token` to bound at the new rates in the meantime.
- LiteLLM's bundled price map, as of LiteLLM 1.102.1, does not know the GPT-6 models. LiteLLM uses that map when `LITELLM_LOCAL_MODEL_COST_MAP=True` is set. Its cost calculator then answers zero, which this package reads as an unknown cost and settles at the [usage bound](#the-cost-bound). The remote price map LiteLLM fetches at import when that variable is not set prices GPT-6 Luna and Sol as of September 2026.
- With that bundled map, LiteLLM 1.102.1 also rejects `temperature` at effort `none` for a GPT-6 name it does not know. It raises the error inside the attempt, so under a cost ceiling the attempt keeps its reservation like any other failed provider call. Leave `llm.temperature` unset on GPT-6 with that map.
- The usage bound is conservative. It applies the long-context rates and the regional uplift to every call on a model that has them, and prices every uncached prompt token as a cache write, so a ceiling admits fewer calls than real spend would allow.
- Regional processing is modelled only as the 10% uplift, always assumed on the models above. Other data-residency pricing is not modelled, and neither is Fast mode: a response served at the `priority` tier is not settled at the usage bound, so under a ceiling it keeps its whole reservation.

### Streaming

`llm.stream=True` is accepted. It exists for compatibility and **it does not make anything faster.** A fetch parses one whole JSON document into records, so there is no partial result for a caller to receive: the chunks are read to the end, reassembled into a single response, and handed to the same path a non-streamed response takes. The same records come back, priced the same way, at the same time or slightly later. The reason to set it is that some provider configurations and proxies require or default to a stream, and until now those callers could not use this package at all.

Streaming changes accounting when the provider omits token counts, which many providers do. In that case the call cannot be priced: the reservation remains at its upper bound, the usage record reports `cost_known=False`, and the fetch reports `accounting_known=False`. The same rule applies whenever a call's cost cannot be read; missing accounting does not make the call free. A stream that includes usage follows the same accounting path as a non-streamed call, including the usage bound [described below](#the-cost-bound).

The reassembled response is not priced from a token count computed here. The provider library's assembler fills in a usage block from a local tokenizer when the stream sent none, and neither those counts nor a price derived from them is what was billed, so both are dropped, not reported as measurements.

`llm.stream=False` remains the default and is what the source sends unless you ask otherwise.

### Rate limiting

`llm.rate_limit` is **not supported**. Client-side call spacing has to reconcile with per-attempt budget admission and every arrangement tried let one corrupt the other, so it is refused by name with a `ConfigError` rather than implemented. Cap concurrency with `llm.max_parallel`, or throttle above this library where the whole request sequence is visible.

## Limits

Four axes, each optional. Calls, time, and cost are all claimed before a call. Rows are capped once the records are parsed, because that is when the count exists. The time axis bounds waiting, not the whole run; [what it bounds](#what-the-time-ceiling-actually-bounds) says exactly what.

| Key | Effect |
|---|---|
| `limits.max_rows` | Truncates the result and sets `meta.truncated_to` |
| `limits.max_seconds` | Refuses an attempt once elapsed time reaches it, and cancels a wait in flight ([what it bounds](#what-the-time-ceiling-actually-bounds)) |
| `limits.max_cost` | Refuses an attempt whose cost bound would cross it |
| `limits.max_calls` | Refuses an attempt past that many, counting every retry |
| `limits.max_retries` | Retries per call, at most 9,999; the client makes this many attempts plus one, narrowed to the smallest depth along the budget chain |

When a limit stops a run, `meta.stopped_by` names it and the records collected so far are returned rather than discarded. Reservation is atomic, so concurrent callers cannot race past a ceiling. A cache hit consumes no provider call, no tokens and no API cost, and its usage record marks it. Rows it returns still count against a row ceiling, and admission still checks elapsed time, so a cached result can be truncated or refused by a caller-owned budget that earlier requests have already spent, which is what keeps those ceilings binding across a sequence. A cache store that blocks remains outside the time ceiling, as the exclusions list says.

### What the time ceiling actually bounds

Read this before relying on `limits.max_seconds`, because it promises less than the other three axes.

It bounds **admission and every cooperative wait in `DefaultLlmClient.acomplete`**. It refuses the next attempt once elapsed time reaches the ceiling, caps retry backoff, caps the timeout handed to the provider, and cancels an `await` in flight (a provider call, or a queue on the parallelism semaphore) at the deadline, raising `BudgetExceeded` on the `max_seconds` axis. A run that finishes over the ceiling is never returned as a success, because a ceiling that fails quietly is worse than one that reports late.

It is **not** an end-to-end wall-clock guarantee, and Python offers no way to make it one: `asyncio` cannot interrupt code that is not awaiting. Work that does not yield runs to completion and is measured only once it has finished.

**Where the clock starts.** `fetch` and `afetch` normalise the attributes, context, limits and query *before* the run's budget exists; that work is outside the ceiling entirely. The budget is then constructed, or derived from one you supplied. A budget you supplied started its own clock whenever you created it, which may be long before this call. Resolving and shape-checking the client, binding it, building the call metadata and rendering the prompt all happen after construction but before `acomplete`, so they are charged to the ceiling and can only overrun it rather than be stopped by it.

**What is excluded.** Inside the window, anything that does not yield:

- **synchronous work inside the answering client**: importing the provider on first use, materialising the response object, and the provider's own cost accounting;
- **synchronous setup between budget construction and the call**: client resolution and shape checking, call metadata, and prompt rendering, as described above;
- **work that declines its cancellation**: the deadline cancels the wait and then waits for the cancellation to be acknowledged, so a provider that catches `CancelledError` and keeps going holds the call open. Abandoning it instead was tried and was worse: your own cancellation stopped reaching the work, the abandoned task could be collected mid-flight or keep `asyncio.run` from returning, and it settled real cost into your budget after a result reporting none had already been returned;
- **post-processing after the client returns**: parsing, aligning, verifying, and deduplicating the records, which grows with the size of the response;
- **a caller-supplied cache store that blocks**, since `CacheStore` is a synchronous protocol and a store that reaches a network is invisible from here;
- **the synchronous `complete` path and the adapters around it**, which pass the provider a timeout capped at the remaining budget but cannot preempt a provider that ignores it, and whose event-loop startup and shutdown sit outside the measured window entirely.

So `stopped_by: max_seconds` is always truthful about which ceiling was crossed, and a run may report it well after the ceiling (with a large response or a slow store, several times over). Where you need a hard wall-clock stop, impose it above this library, in a task or process you can cancel.

This is the only full description of the time ceiling. Other references link here to avoid duplicate descriptions that could become inconsistent.

The key names form a closed set. An unknown key, including `limit.*` or a bare `limits`, raises `ConfigError` and lists the accepted names. Values are also validated when the ceiling is built: a wrong type, `bool`, negative number, or non-finite number raises `ConfigError` naming the field. `NaN` is explicitly rejected because every spend comparison with it is false, leaving an apparently configured ceiling unenforced.

### The cost bound

`llm/pricing.py` produces an upper bound on one call *against the rates it holds*, or refuses. Those rates are a static snapshot of published list prices with no effective date and no freshness check, so everything below bounds the call's size correctly and is only as accurate as the table's prices. If a listed price rises, or an alias or proxy bills differently, the reservation is too small and the difference appears at settlement. Supply `llm.price_per_input_token` and `llm.price_per_output_token` to bound against rates you maintain. The bound is the prompt's length in UTF-8 bytes plus a framing allowance at the model's published input rate, plus the whole `llm.max_tokens` allowance at its output rate. Three rules from the [model table](#supported-models) raise it: a prompt counted at 1,024 tokens or more on a model with a cache-write price is bounded at that price, since the response does not say how much of the prompt was written to cache; a prompt above 272,000 tokens on a model with a long-context tier is bounded at that tier's rates; and a model subject to the regional uplift has 10% added. Supplied rates for a table model are read as its base rates: the same three rules apply to them, and the bound never falls below the table's own. Rates for a model outside the table are used as given, so they must already cover every tier that model can be billed at. Counting bytes rather than dividing them by an average bytes-per-token figure is what makes it a bound: a byte-pair tokenizer merges byte sequences, so every token it emits consumes at least one byte of the encoded prompt and no tokenizer can exceed the count, while an average is exceeded by text of short words, punctuation, or non-Latin script. Counting characters would not have been a bound, although it reads like one, because a tokenizer splits a character whose encoding it has no merge for. The bound is reserved before the attempt and settled when the response arrives. `estimate` reports the reservation, so a request sized against it and a run bounded by it cannot disagree about admission. Neither figure is a claim about what the provider will invoice.

**Settling the charge.** LiteLLM's cost calculator settles the reservation outright, as a measured cost, when its figure is readable and the model is in the table at table rates with no cache-write price, no long-context tier and no regional uplift. The calculator prices at standard rates, so it cannot prove any of those charges. A calculator zero is never readable for a call that reached the provider, because the calculator answers zero for a model its price map does not carry and for a missing or all-zero usage block, and every request sends a prompt. Every other call under a cost ceiling is charged a usage bound instead, with `cost_known=False`:

```text
U = uplift × [ (prompt − cached) × max(input, cache write) × Tin
             + cached × cached input × Tin
             + completion × output × Tout ]
```

`Tin` and `Tout` are 2 and 1.5 on a model with a long-context tier and 1 otherwise, and `uplift` is 1.10 on the models it applies to. A readable calculator figure above `U` wins. `U` is built only where every input to it is proven: the rates are the table's own, the model that answered resolves to the same row as the one requested, the response's `service_tier` is `default` or `flex`, and the usage reports whole prompt, completion and cached-token counts with the cached count no larger than the prompt, at least one prompt token, and at least one output token when the answer has text. Where any of those fails, the call keeps its whole reservation, or the greater of the reservation and a readable calculator figure. A `U` above the reservation is charged in full and recorded as crossing the ceiling, because the call has already been made.

Without a cost ceiling nothing is reserved. A readable calculator figure is reported as measured; an unreadable one is reported as `U` where it can be built, and otherwise as zero, both with `cost_known=False`.

### What `cost` means

`meta.cost` and a usage record's `cost` depend on whether a cost ceiling was set, which model answered, and whether LiteLLM's calculator could price the call. Read `accounting_known` (`cost_known` on a usage record) first.

| Cost ceiling | Model and rates | Calculator figure | `cost` | `accounting_known` |
|---|---|---|---|---|
| none | any | readable | the calculator's figure | true |
| none | a table model at table rates | zero or unreadable | the usage bound `U`, where the response proves its inputs | false |
| none | a model outside the table, or `U` cannot be built | zero or unreadable | `0`, meaning not measured | false |
| set | a table model at table rates with no cache-write price, long-context tier or regional uplift: GPT-5, GPT-4.1, GPT-4o, the o-series and the older GPT rows | readable | the calculator's figure | true |
| set | a table model at table rates with any of those: GPT-6, GPT-5.6, GPT-5.5 and the GPT-5.4 family | any | the greater of `U` and a readable figure, or the reservation where `U` cannot be built | false |
| set | a table model whose calculator figure is zero or unreadable | zero or unreadable | `U`, or the reservation where `U` cannot be built | false |
| set | a model outside the table, or caller-supplied rates | any | the greater of the reservation and a readable figure | false |

A cache hit costs `0` and is measured. An answer from an `llm_fn` reports `0` with `accounting_known` false, because nothing here can see what the callable spent.

Where the bound cannot be established, `limits.max_cost` raises `ConfigError` rather than claiming an invented figure. That happens when the model has no published price, since a model this table has never heard of may cost more than anything in it, and when `llm.max_tokens` is not a finite positive number, since output with no cap can run until the provider stops it. `pricing.rates_for` returns `None` for an unlisted model and `pricing.can_bound` answers the question without raising. No bound is computed at all when no cost ceiling is configured, so a run bounded on calls or rows is never refused over an unfamiliar model.

**The minimum cost bound is the full output allowance.** The prompt contributes relatively little; `llm.max_tokens` at the model's output rate, with the regional uplift where it applies, contributes the rest. With the default `gpt-6-luna` model and `llm.max_tokens=25000`, the minimum is about $0.01375. A lower `limits.max_cost` refuses the call before provider contact. Reduce `llm.max_tokens` when the answer needs fewer tokens.

`pricing.resolve_rates` accepts `llm.price_per_input_token` and `llm.price_per_output_token`, supplied together, for a model the table does not carry. For such a model the rates are used as given, so they must already cover every tier it can be billed at: cache writes, a long-context tier, regional processing. For a model the table does carry, supplied rates are read as its base input and output rates: the entry's cache-write ratio, long-context tier and regional uplift still apply to them, and the bound is never lower than the table's own, so supplying the published base rates cannot drop a charge the model is known to carry. Both are accepted `llm.*` overrides, consumed by the client and never forwarded to the provider, so an unlisted model can be given a cost ceiling by whoever knows what it costs. Supplying one commits you to both, and a value that is not a finite positive number is refused instead of being passed over in favour of the table, since an override that was meant and then ignored produces a bound lower than the one you asked for.

### Which ceilings bind

A ceiling binds only on an axis the answering client declares through `budget_capabilities()`, and each configured ceiling on an undeclared axis raises `ConfigError` at the first fetch naming that ceiling and what the client did declare. Reporting a ceiling that is not holding anything is worse than declining to hold it, because the caller believes it. `limits.max_rows` binds any client, since the records are counted here.

The practical case is `llm_fn`. `FunctionClient` declares rows alone, so `limits.max_calls`, `limits.max_cost`, or `limits.max_seconds` on a run that supplies `llm_fn` is refused.

For the same reason, **a result produced by an `llm_fn` sets `meta.accounting_known` to false**. Its `calls`, `cost`, and token counts describe only the library's visible bookkeeping. A callable may answer locally, contact one provider, or fan out to several; none of that work is visible to the wrapper. The result therefore reports unknown accounting instead of presenting `cost=0.0` as a measurement.

### Bounding one request

Limits configured on the source apply to every fetch it makes. To bound a single request instead, pass the ceiling with the request: `properties` on `fetch` and `afetch`, or `optional_properties` on `execute_query` and `execute_query_nl`, both merge over the instance's properties for that call and are not written back, so the ceiling binds the call it arrived with and does not leak into the next.

### A budget across calls

The route above bounds one request. To bound a sequence with one total, build a budget and pass it:

```python
from blue_data_llm_source import Budget, Limits, LlmSource


def answer(prompt: str) -> str:
    return '[{"name": "Charles de Gaulle", "iata_code": "CDG"}]'


source = LlmSource()
run = Budget(Limits(max_rows=2))

first = source.fetch("commercial airports in France", llm_fn=answer, budget=run)
second = source.fetch("commercial airports in Spain", llm_fn=answer, budget=run)
third = source.fetch("commercial airports in Italy", llm_fn=answer, budget=run)

print(len(first), len(second), len(third))
print(third.meta.outcome, third.meta.stopped_by, run.rows)
```

Each fetch derives a child from that budget, so it spends from what the parent has left and the effective ceiling on every axis is the smaller of the two, retry depth included. The three fetches above return one record, one record, and none: by the third the parent's two rows are gone, so it comes back empty with `stopped_by` naming `max_rows` rather than raising. The row axis is the one this example can use with an `llm_fn`, since `FunctionClient` declares no other; a call, cost, or time ceiling on a caller-owned budget needs a client that declares that axis, and is refused otherwise. A child does not inherit its parent's `stopped_by`, so a later affordable request under the same budget does not report an earlier refusal as its own. `Budget` also reports `committed`, which is settled spend plus the reservations still outstanding.

## Decomposition

One model call answers a request by default. A large ask can be split into parts, each asked
separately and merged on the way back. Nothing selects a shape for you.

| Property | Default | Meaning |
|---|---|---|
| `plan.mode` | `flat` | `flat` asks once. `partitioned` splits the ask by a key the analysis found. `row_by_row` asks once per entity in a list the analysis found. An unknown value raises `ConfigError`. |
| `plan.max_continuations` | `0` | How many times a part may ask again for records it did not get. At `0` each part asks once. It bounds asks per part, not provider attempts or planning calls; each ask may still be retried up to `limits.max_retries` times, three attempts by default. |
| `plan.max_leaves` | `100` | Most terminal slices. An expansion that cannot fit is rejected as a whole; row-by-row falls back to the intact batch slice rather than running a prefix. |
| `plan.key` | none | The attribute identifying a record, so parts returning the same record merge. Must be one of the attributes you asked for. |
| `plan.partition_key` | none | Preferred root split dimension. Descendants may choose another dimension and cannot repeat an ancestor dimension. |
| `plan.max_depth` | `2` | Deepest partition level. `0` disables subdivision while retaining non-flat bootstrap analysis. |
| `plan.max_partitions` | `20` | Most children one proposal may create. A value below `2` leaves the node unsplit. |
| `plan.target_rows_per_leaf` | `50` | Uncertain row-count hint above which a descendant may split again. Unknown estimates are also eligible. This is not a completeness target. |
| `plan.schema_mode` | `preserve` | `preserve` keeps declared attributes. `enrich` lets non-flat bootstrap append inferred attributes while retaining caller declarations. Flat plus `enrich` is rejected because flat makes no planning call. |
| `plan.keys` | empty | Ordered fields that jointly identify one record. Required explicitly for distribution search. |
| `plan.exact_rows` | none | Hard target when the plan has one leaf. A multi-leaf plan does not apply it and adds `exact_rows_not_leaf_bound` to `meta.gaps`. |
| `plan.max_exclusion_bytes` | `16384` | Encoded byte cap for whole-key exclusions in a continuation prompt. |
| `plan.max_excluded_keys` | `50` | Direct-Python-only count cap for whole-key exclusions; CLI and MCP use this default. |
| `plan.planning_order` | `planning.schema_first` | One of five schedules: cardinality first, schema first, locked sequential, locked parallel, or combined. The locked schedules require a non-empty caller schema and ordered `plan.keys`. |
| `plan.expose_estimate` | `True` | Whether extraction prompts include the soft estimate. Planning facts and task transport retain it either way. |
| `plan.distribution_search` | `False` | Direct-Python-only opt-in for rule-backed distribution selection. Requires `partitioned`. |
| `plan.identity_partitions` | empty | Direct-Python-only list of versioned finite-domain rule mappings accepted by `IdentityPartitionRule.from_dict()`. |
| `plan.max_strategy_candidates` | `5` | Direct-Python-only candidate bound. Must be at least `1` when distribution search is enabled. |

### Rule-backed distribution search

Distribution search is opt-in and is exposed only through direct Python calls. It
requires `plan.mode="partitioned"`, explicit ordered `plan.keys`, and a nonempty list of declared
finite-domain rules. Every rule's `ordered_primary_keys` must exactly match `plan.keys`; its
`dimension` must be one of those keys; and at least one rule must apply at the root. Rule domains are
nonempty JSON scalars with normalized duplicates rejected. Source rules use
`source_configuration`; per-call rules use `caller_declared` and replace the source list. Invalid
rules fail before provider contact, and the distribution-search settings are removed from the
provider property map.

An accepted split records its rule and safety decision in the plan. A distribution-search result may
carry a `PlanningReceipt` with `contract_version`, configured rule digests, planning facts, and the
versioned fetch plan. The receipt checks internal consistency; it does not prove rule authenticity,
factual truth, completeness, or answer quality. CLI and MCP intentionally omit distribution search
and this receipt.

`flat` makes no planning call. For non-flat modes, `planning.combined` makes at most one fact call,
the locked schedules make at most two, and cardinality first or schema first make at most three. The
planner may then make bounded proposal, distribution, or entity-enumeration calls before extracting
the leaves. Planning is breadth first and bounded by depth, per-node partitions, total leaves,
strategy candidates, and the shared execution budget.

```python
result = source.fetch(
    "commercial airports in France",
    attrs=["name", "city"],
    properties={"plan.mode": "partitioned", "plan.key": "name"},
)
```

**What this does not do.** `estimate` does not price a decomposed request; it bounds a flat fetch,
retries included, and reads none of these settings. Pass any `plan.*` value and it returns the same
figures. Use `planning.worst_case_attempts(PlanLimits(...), mode)` for the conservative configured
attempt diagnostic; a hand formula that omits the selected schedule or proposal work understates the
request.

A partitioned request's figure is a worst case over the plan's caps, not a forecast.
`planning.planned_logical_calls(limits, mode, planning_order=..., distribution_search=...)` counts the
planning schedule's fact calls (one for `planning.combined`, two for the locked schedules, three for
`planning.schema_first`, the default, and `planning.cardinality_first`), then the split proposals, or
the distribution calls when distribution search is on, then the leaves. With the default caps, twenty
partitions at depth two and 100 leaves, and the default schedule, that is 124: three fact calls, at
most 21 partition proposals (one per node that can split, the root and its twenty children) and at
most 100 leaves. With the default retries that is 372 provider attempts. At the flat per-attempt bound
of a short request, about $0.0138 on `gpt-6-luna` and $0.0431 on `gpt-4o`, the worst case is roughly
$5.15 and $16.05; leaf prompts carry their constraints and are a little longer.
A fetch is not refused against that figure. Each planning call and each provider attempt is admitted
against the ceiling when it is made, so a ceiling below the worst case stops the run where the money
runs out and reports it in `stopped_by`.

A split plan is not reserved at its worst case. Before each optional planning call, the coordinator
checks that the planning call and one extraction attempt fit the current call, cost, and deadline
headroom. Each provider attempt still claims the budget atomically. A later leaf can therefore remain
unserved after another consumer spends shared headroom. Flat work is weighed one attempt at a time.

## Gaps

`meta.gaps` is a list of operational diagnostics that may explain missing rows, and it is always
present. An empty list says that no supported diagnostic fired. It does not establish completeness.

```python
for gap in result.meta.gaps:
    print(gap)  # leaf_failed: leaf 0.1 raised TransportError(...)
```

It reports what this source can see: a part that failed, a continuation cap that fired, a
response cut off at the model's output limit, a plan refused for exceeding `plan.max_leaves`, records
kept unmerged because no key was identified. A valid empty response alone is not a gap. It does not
report a model that simply answered with fewer records than exist because nothing in such a response
says it is short.

## FetchResult and ExecMeta

`FetchResult` holds `records` and `meta`, and supports `len()` and iteration over the records. Those
remain its only dataclass fields. Direct Python distribution-search results expose
`.planning_receipt` and `.with_planning_receipt(...)`; `to_dict()` adds a top-level
`planning_receipt` only when one exists.
Results without distribution search and refusals before plan construction keep the two-key
serialization.

`ExecMeta` reports `gaps`, `stop_reasons`, `stopped_by`, `record_origins`, `asked`, `outcome`, `model`, `calls`, `prompt_tokens`, `completion_tokens`, `cost`, `latency_seconds` (published as `elapsed_seconds` in the CLI and MCP JSON), `cache_hits`, `requested_attrs`, `row_estimate`, `cardinality_estimate`, `primary_keys`, `observed_key_count`, `marginal_key_yields`, `incomplete_key_records`, `normalization_collisions`, `repaired_numbers`, `observed_keys`, `remapped_keys`, `unmapped_keys`, `type_mismatches`, `key_collisions`, `unchecked_types`, `truncated_to`, and `accounting_known`.

`row_estimate` is the accepted non-flat bootstrap hint for the root slice. A reported zero remains
zero. Flat mode, an unknown estimate, or a bootstrap transformation rejected during fallback reports
`None`. It is neither the returned row count nor an expected-completeness measure.

`stop_reasons` is the ordered, distinct list of extraction limits and discarded final output.
`stopped_by` is its first member for callers that consume one reason. `gaps` carries operational
diagnostics, including planning fallback, failed or unserved tasks, and discarded records. An empty
gap list says only that no supported diagnostic fired.

**Read `accounting_known` before reading `cost`.** It is `False` when the answer came from a caller-supplied `llm_fn`, when provider accounting is unreadable, or when a call under a hard cost ceiling was charged a usage bound or its reservation because the response does not prove every billed category ([settling the charge](#the-cost-bound)). In those cases `cost` is conservative bookkeeping, not an exact invoice. For an `llm_fn`, a `cost` of `0.0` means "not measured", not "free", and a `calls` value counts invocations of the callable rather than provider requests.

`unmapped_keys` matters. When the model returns a key that cannot be matched to a requested attribute, the key is reported there rather than dropped in silence. `key_collisions` is the same idea for the opposite problem: when two returned keys map to one requested attribute, the first wins and the second is reported with the value it carried, because which spelling the model meant is not knowable here. Two occurrences of the identical key inside one JSON object are a different case and raise `ParseError`, since there the two values have nothing to tell them apart by and Python's decoder would otherwise keep the last of the pair without a trace.

A response is read as strict JSON first. When that fails, one repair is tried: a bare number written with thousands separators in an object value, such as `"population": 4,470`, loses its separators and the text is parsed again under the same strict rules. Nothing inside a string, no key, and no array element is touched, so `[1,234]` stays two numbers. `repaired_numbers` counts the numbers read this way, so a result that needed it says so. A response that still fails raises the `ParseError` its original text produced.

`outcome` uses the closed vocabulary `ok`, `empty_query`, `model_returned_none`,
`filtered_to_empty`, `partial`, `stopped_by_limit`, and `truncated`. A partial plan outranks a
simultaneous cap because every cap still appears in `stop_reasons`. Without interruption, any
non-row stop produces `stopped_by_limit`;
`max_rows` produces `truncated` when records remain and `stopped_by_limit` when none do. With no stop,
records produce `ok`, an answered empty response produces `model_returned_none`, and records removed
by reduction produce `filtered_to_empty`.

`calls` and `cost` are read from the run's budget rather than summed from usage, so they count what was actually claimed, including an attempt that reached the provider and failed. Tokens and the model name come from the `Usage` entries the client appended to this call's `usage_sink` list, and from nothing else, which is what keeps two concurrent fetches through one client from reporting each other's tokens.

## Errors

`LlmSourceError` is the root error. Its subclasses are `ConfigError` for rejected configuration, `QueryError` for malformed queries, `TransportError` for provider failures and malformed provider responses, `ParseError` for responses that cannot be read as records, and `BudgetExceeded` when a limit would be crossed.

No other exception type escapes `fetch`, `afetch`, or `estimate`. Unexpected exceptions are wrapped in `LlmSourceError` with the original attached as `__cause__`, so callers can catch the library root while maintainers retain the original type. `KeyboardInterrupt` and cancellation are not caught. The three entry points enforce this boundary directly.

`ConfigError` covers more than a bad key. A ceiling configured on an axis the resolved client does not declare raises one, as does an unknown or invalid `limits.*` value, and so does a `limits.max_cost` whose bound cannot be established because the model has no published price or `llm.max_tokens` is not a finite positive number. `QueryError` covers the same attribute requested twice. Two *distinct* names whose fuzzy forms coincide (`first name` and `first_name`, or two scripts a normaliser flattens together) are not refused: the fuzzy index simply drops the entry it cannot separate, so exact keys still match and a key that only matches fuzzily is reported in `meta.unmapped_keys` rather than assigned by guess.

A missing provider credential is **not** a `ConfigError`. This library never reads a credential, so it cannot find one missing; the provider SDK reports it as a failed call, which arrives as a `TransportError`. Read a `TransportError` mentioning credentials as "the key is absent or wrong" rather than as a network fault.

`BudgetExceeded` raised inside a fetch does not reach the caller. A refusal before any extraction
answer returns no records with `outcome=stopped_by_limit`. If later work is refused after an
extraction answered, the answered records are retained with `outcome=partial`. In both cases the
stop fields name the axis. `BudgetExceeded` remains public for callers driving a `Budget` directly.

`ParseError` carries the raw response text, because re-running a query is not guaranteed to reproduce the failure.

## Tests

```bash
python -m pytest tests/unit -q
```

Unit tests only. Every I/O boundary is doubled and no test touches the network. Coverage is not collected by default and no threshold is enforced; pass `--cov=src` to `pytest` to measure it.

For the type check, create a `.venv` in this directory before installing. `pyproject.toml` points `pyright` at it, and without one pyright reports every import as unresolved instead of real typing errors:

```bash
python -m venv .venv && source .venv/bin/activate
```

### The contract test

`tests/unit/compat/test_contract.py` compares this source's method signatures against `blue_data.source.source.DataSource` and fails when they drift.

`LlmSource` inherits that base class, so most divergence is already a type error. The contract test also catches untyped signature drift and required methods that resolve to a default base implementation. Six transaction methods are recorded in the test's `OUT_OF_SCOPE` map and refuse by name; the three orchestrator-tool methods are overridden locally because they have source-specific behavior.

The test prints the path of the `blue_data` package it inspected and includes that path in failures. Run it after installing or upgrading `blue-data` so the comparison covers the package used by the current environment.

## Distribution

None of the three packages is on PyPI or on an internal index. Install from source; `blue-data` must be installed first. See [Install](#install) above and the dependency section of the top-level [README](../README.md). References in design documents to how these packages are "distributed" describe the intended route, which is not currently available.
