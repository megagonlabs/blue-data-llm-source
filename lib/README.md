# blue-data-llm-source

Python library for querying a language model through the `DataSource` interface and returning structured records with execution metadata.

## Install

Run these commands from the `blue-data-llm-source` checkout root, in the same root `.venv` used by the [quick start](../docs/quickstart.md):

```bash
python -m venv .venv && source .venv/bin/activate
./lib/scripts/install.sh
```

If `blue-data` is not beside this checkout, run the installer with its `lib/` directory instead:

```bash
BLUE_DATA_PATH=/path/to/blue-data/lib ./lib/scripts/install.sh
```

The core depends on `blue-data` and `tenacity`. `blue-data` supplies the `DataSource` base class and also installs `pandas`, `numpy`, and `jsonpath-ng`. The installer adds the `dev` and `llm` extras; `llm` supplies `litellm` and `diskcache`. The base package imports neither until needed. Store-dependent tests skip if `diskcache` is absent.

## LlmSource

```text
LlmSource(name="llm", properties=None, client=None)
```

`LlmSource` inherits `blue_data.source.source.DataSource` and implements the shared query and metadata surface. The base class provides default empty results for several methods, so a contract test verifies that every required method resolves to `LlmSource`. Transaction methods raise `NotImplementedError`; the supported orchestrator-tool methods are narrowed locally, and `execute_query_nl_tools` is refused.

Inheriting the base class does not make the source reachable by protocol name. `DataRegistry.connect_source` returns the live registered instance while one is present, and reconstructs a new source from the recorded class path and properties once it is not. The route is to build the source yourself and hand the instance to `register_source`. That is enough: the registry records the class alongside `name` and `properties`, and rebuilds an `LlmSource` from that record, so a source registered this way survives a round trip through the registry.

The registry stores the source's own properties, not its injected client or the client's settings. Put ceilings, model and rates on the source when they must survive registration and reconstruction; a rebuilt source starts with the default client.

`properties` carries connection details and configuration. `client` injects a model client, so tests and alternative providers need no subclassing. The full contract is in [Injecting a client](#injecting-a-client) below. In brief, the client provides two methods that take the prompt first and merged per-call properties second, plus an optional method declaring which ceilings it supports.

[COMPATIBILITY.md](COMPATIBILITY.md) covers integration with the `DataSource` base class and migration from the earlier LLM source, including its budget contract. Read it before porting a caller.

### fetch

```text
fetch(query, attrs=None, context=None, max_records=None, properties=None, llm_fn=None,
      budget=None) -> FetchResult
afetch(...)   # the async form; fetch() wraps it
```

`query` is the question. `attrs` is either a list of names or a list of `{"name": ..., "type": ...}` dicts. `context` is free text that may clarify an attribute name. `max_records` caps the returned rows. `llm_fn` is a `(prompt) -> str` callable used as the client for that call. `budget` is a `Budget` you own. When supplied, each fetch derives a child from it, so ceilings accumulate across calls using that budget. Without it, the fetch builds a budget from the configured limits for that call alone.

Synchronous query methods work inside a running event loop by waiting for an owned worker thread. They block the calling thread and delay its cancellation callbacks. Use `afetch` for concurrent async work and for a client or `llm_fn` that needs the caller's own event loop; a synchronous call hangs on such a client. Thread-affine objects, such as an sqlite connection used inside `llm_fn`, fail on the worker thread. Under `asyncio.run`, the first Ctrl-C cancels the in-flight fetch when the synchronous call runs in the runner's main task. A call in another task observes cancellation only if that task receives the request. In a plain event-loop callback, that signal cancels the runner's main task while the fetch continues; a second Ctrl-C interrupts the waiting thread. Schedule and await `afetch` in a task for cancellable asynchronous work. Once the bridge receives an interrupt or task-cancellation request, it raises as soon as the fetch stops, or after five seconds if the client ignores cancellation; that work then finishes in the background. Time limits cancel the same cooperative way, so they cannot forcibly stop a client that ignores cancellation either. See [`COMPATIBILITY.md`](COMPATIBILITY.md).

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
                 optional_properties=None, read_only=True) -> list[dict]
translate_nl_to_native_query(query, schema, llm_fn) -> str | None
```

`execute_query` takes natural language, an operation dict, or a JSON-encoded object with an `op` key, such as `'{"op":"LLM.MODELS"}'`. Other strings remain natural-language queries. Supported ops are `LLM.FETCH`, `LLM.MODELS`, and `LLM.ESTIMATE`; names are case-sensitive, and an unknown op raises `QueryError` listing them. `LLM.ESTIMATE` reads the same `query`, `attrs` or `attr_names`, and `context` keys as `LLM.FETCH`. It returns the estimate as a one-record list, matching this surface's return type. Call `estimate` directly to get the `Estimate` object.

For every other source in the layer, `llm_fn` translates natural language into a native dialect. This source has no other dialect, so `translate_nl_to_native_query` is the identity and `llm_fn` is used as the model client instead of being ignored.

Both methods take `optional_properties`, and that is where a ceiling goes when you are reaching the source through this surface. Per-call properties merge over the instance's for that request and are not written back. A `limits.*` value can only tighten the effective ceiling; `None` leaves it unchanged; `limits.max_retries=None` is invalid.

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
                optional_properties=None, read_only=True) -> Any
```

`DataSource` offers a tool-calling interface for an orchestrating model: a source advertises tools, and the caller invokes them one at a time. Its tools describe a relational source (databases, collections, columns, row samples, and foreign keys). Five of its eight tools are supported here.

| Tool | Returns |
|------|---------|
| `list_databases` | `["llm"]`, the one virtual database |
| `list_collections` | `["public"]`, the one virtual collection |
| `get_schema` | The virtual entity with its one text attribute, and an empty relation map |
| `get_sample` | `None`: no stored samples or statistics; no provider call |
| `execute_query` | The records for `tool_input["query"]`, fetched through the query surface |

The other three are not advertised. `get_metadata`, `get_column_stats`, and `get_relations` have no useful stored metadata to return. The advertised definitions come from the shared base class and are filtered by name.

`execute_nl_tool` refuses any other name with a `ConfigError` naming the five that are served. Nothing on this path raises outside `LlmSourceError`, including a name that is not a string and a `tool_input` missing a key the tool requires.

`execute_query` is the one tool here that reaches a provider, and it takes its ceilings where the query surface takes them: a `limits.*` key in `optional_properties` reaches `execute_query` and binds that call.

`tool_input` accepts string field names declared in the advertised schema, plus `database` and `collection` context. Unsupported fields now raise `QueryError` before provider contact instead of being ignored. Pass ceilings in the outer `optional_properties` argument; a `limits.*` key or nested `optional_properties` map in `tool_input` is refused. The advertised schemas are unchanged.

`execute_query_nl_tools` is not served and raises `ConfigError`. It can make up to ten provider calls, which this source's ceilings do not govern. `execute_query_nl` is the natural-language route; its spend and time ceilings are enforced.

The shared `commit` key in `optional_properties` is accepted and ignored by this read-only source. Shared `read_only` keywords are accepted by `execute_query_nl`, `execute_nl_tool` and `execute_query_nl_tools`; `False` enables no writes. `execute_query_nl_tools` also accepts `max_sample_size=None` and remains unsupported. All four creation methods accept `overwrite=False`; either value returns `{}` without mutation. `fetch_entity_stats` accepts keyword-only `row_sample_size=10` and returns `None`. Advertised `get_sample` forwards shared `sample_size` to it and returns the same unavailable result, without generating or retaining samples.

### Metadata

`fetch_databases()` returns one virtual database, `fetch_database_collections()` one virtual collection, and `fetch_database_collection_entities()` one virtual entity with one text attribute, so a registry sync has a hierarchy to walk. `fetch_metadata()` returns the protocol and variant. The remaining `fetch_*` methods return empty.

`protocol` is always `llm` and cannot be overridden. `variant` defaults to `openai` and can be set through `properties["connection"]["variant"]`.

## Injecting a client

Injection is structural, and the shape is checked when the client is resolved: a client missing a method, or one whose signature cannot be called as `complete(prompt, properties)`, is refused with a `ConfigError` naming the contract rather than failing later with a raw `TypeError`. What no check can prove is that a client honors a budget it declares, so capability declarations remain its own responsibility. This is the whole contract:

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

Two keys arrive in `properties` on every call the source makes. `budget` carries the run's `Budget`, which the client claims against per provider attempt. `usage_sink` carries a list this call owns, and whatever `Usage` entries the client appends to it become the fetch's token counts and model name; client-wide usage totals cannot be attributed to one fetch when clients are shared. Both are passed per call instead of on the client for the same reason: one client can serve several concurrent fetches.

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
        return frozenset({Capability.CALLS, Capability.COST, Capability.ROWS, Capability.ESTIMATE})

    def bound_for_attempt(self, prompt: str, properties: dict[str, Any]) -> float:
        # ESTIMATE requires a bound consistent with the reservation below.
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

This client does not declare `Capability.SECONDS`. A client that awaits a provider must enforce the deadline on that wait before it can promise a time ceiling.

This `MyClient` controls its own settlement; `DefaultLlmClient` keeps at least each contacted reservation under `limits.max_cost`. Claim inside `budget.attempt(...)` and resolve the claim on every exit path. The context manager registers cleanup before taking the claim; `reserve` is also available but leaves a window in which an interrupt can orphan a claim. `settle(actual)` releases any excess, `keep_unpriced()` retains the claim for an attempt that reached the provider but cannot be priced, and `release()` returns the claim for an attempt that never reached the provider. An outstanding claim reduces the budget's remaining headroom.

Declare only capabilities the client can guarantee. `DefaultLlmClient` declares `Capability.ALL` because it makes the provider call itself. `FunctionClient`, which wraps an `llm_fn`, declares only `Capability.ROWS`: it cannot know the function's cost, stop it at a deadline, or determine how many provider calls one invocation makes. A call, cost, or time ceiling on an `llm_fn` is therefore refused rather than settled at zero.

## Configuration

Source settings override client settings; a call can only tighten the resulting ceilings. For the rules on `None`, zero, retries, row caps, caller-supplied prices, and a per-call model or rates under a configured cost ceiling, see the [compatibility guide](COMPATIBILITY.md#deviations-from-the-earlier-implementation).

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

Each distinct resolved `llm.cache_dir` opens an on-disk backend that holds file descriptors. A leading `~` and environment variables are expanded before the directory is checked; unresolved variables are refused. On POSIX systems, the directory must be owned by the current user and inaccessible to group and other users; a new directory is created with mode `0700`. An existing directory with broader permissions is refused without changing it. A client keeps at most eight backends open and closes the least recently used beyond that. `DefaultLlmClient.close()` releases them all, and the client is also a context manager. A long-lived service that rotates cache directories by tenant or day should close the client.

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
- LiteLLM's bundled price map, as of LiteLLM 1.102.1, does not know the GPT-6 models. LiteLLM uses that map when `LITELLM_LOCAL_MODEL_COST_MAP=True` is set. Its cost calculator then answers zero, which this package reads as an unknown cost. Under a ceiling the attempt keeps at least its reservation; without a ceiling it reports the [usage bound](#the-cost-bound) where available. The remote price map LiteLLM fetches at import when that variable is not set prices GPT-6 Luna and Sol as of September 2026.
- With that bundled map, LiteLLM 1.102.1 also rejects `temperature` at effort `none` for a GPT-6 name it does not know. It raises the error inside the attempt, so under a cost ceiling the attempt keeps its reservation like any other failed provider call. Leave `llm.temperature` unset on GPT-6 with that map.
- The usage bound applies the long-context rates and the regional uplift to every call on a model that has them, and prices every uncached prompt token as a cache write. Under a ceiling the reservation remains the minimum charge.
- Regional processing is modeled only as the 10% uplift, always assumed on the models above. Other data-residency pricing is not modeled, and neither is Fast mode: a response served at the `priority` tier cannot use the usage bound, so under a ceiling it keeps at least its reservation. A higher readable calculator figure raises the ledger charge.

### Streaming

`llm.stream=True` supports provider configurations and proxies that require a stream. A fetch parses one whole JSON document into records, so chunks are reassembled before parsing. Streaming returns no partial records and offers no latency advantage here; it follows the same pricing path as a non-streamed response.

Streaming changes accounting when the provider omits token counts, which many providers do. In that case the call cannot be priced: the reservation remains at its upper bound, the usage record reports `cost_known=False`, and the fetch reports `accounting_known=False`. The same rule applies whenever a call's cost cannot be read; missing accounting does not make the call free. A stream that includes usage follows the same accounting path as a non-streamed call.

The reassembled response is not priced from a token count computed here. The provider library's assembler fills in a usage block from a local tokenizer when the stream sent none, and neither those counts nor a price derived from them is what was billed, so both are dropped, not reported as measurements.

`llm.stream=False` remains the default and is what the source sends unless you ask otherwise.

### Rate limiting

`llm.rate_limit` is **not supported**. Client-side call spacing can interfere with per-attempt budget admission, so the setting raises `ConfigError`. Cap concurrency with `llm.max_parallel`, or throttle above this library where the whole request sequence is visible.

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

**Where the clock starts.** `fetch` and `afetch` normalize the attributes, context, limits and query *before* the run's budget exists; that work is outside the ceiling entirely. The budget is then constructed, or derived from one you supplied. A budget you supplied started its own clock whenever you created it, which may be long before this call. Resolving and shape-checking the client, binding it, building the call metadata and rendering the prompt all happen after construction but before `acomplete`, so they are charged to the ceiling and can only overrun it rather than be stopped by it.

**What is excluded.** Inside the window, anything that does not yield:

- **synchronous work inside the answering client**: importing the provider on first use, materializing the response object, and the provider's own cost accounting;
- **synchronous setup between budget construction and the call**: client resolution and shape checking, call metadata, and prompt rendering, as described above;
- **work that declines cancellation**: the deadline cancels the wait, then waits for the provider to acknowledge cancellation. A provider that catches `CancelledError` and continues can hold the call open; its eventual cost is still charged to the run;
- **post-processing after the client returns**: parsing, aligning, verifying, and deduplicating the records, which grows with the size of the response;
- **a caller-supplied cache store that blocks**, since `CacheStore` is a synchronous protocol and a store that reaches a network is invisible from here;
- **the synchronous `complete` path and the adapters around it**, which pass the provider a timeout capped at the remaining budget but cannot preempt a provider that ignores it, and whose event-loop startup and shutdown sit outside the measured window entirely.

`stopped_by: max_seconds` reports the ceiling crossed, but a large response or slow store may delay the result past that ceiling. For a hard wall-clock stop, impose a deadline above this library, in a task or process you can cancel.

The key names form a closed set. An unknown key, including `limit.*` or a bare `limits`, raises `ConfigError` and lists the accepted names. Values are also validated when the ceiling is built: a wrong type, `bool`, negative number, or non-finite number raises `ConfigError` naming the field. `NaN` is explicitly rejected because every spend comparison with it is false, leaving an apparently configured ceiling unenforced.

### The cost bound

`llm/pricing.py` bounds one call against its stored rates or refuses the request. The rates are a static snapshot of published list prices, without an effective date or freshness check. A price change or different billing by an alias or proxy can make the reservation too small; later accounting may raise the ledger charge without detecting the full invoice difference. Set `llm.price_per_input_token` and `llm.price_per_output_token` to use rates you maintain.

The bound prices the prompt's UTF-8 byte length plus a framing allowance at the input rate and the full `llm.max_tokens` allowance at the output rate. For a model with cache-write or long-context tiers, the pre-call bound applies the most expensive of those rates even below the provider's billing threshold; it also includes the regional uplift where applicable. For a table model, supplied rates are treated as base rates and these adjustments still apply; the bound cannot fall below the table's own rate bound. For a model outside the table, supplied rates must cover every billable tier.

Byte length avoids an average bytes-per-token estimate, which can be exceeded by short words, punctuation, or non-Latin text. For the byte-pair tokenizers used here, each token consumes at least one byte of the encoded prompt; character count does not provide that property. The bound is reserved before provider contact and remains the minimum ledger charge under a cost ceiling. `estimate` reports the same reservation used for admission. Neither amount predicts the provider's invoice.

**Settling the charge.** Under `limits.max_cost`, every attempt that reaches the provider keeps at least its pre-call reservation. A readable LiteLLM calculator figure or a [usage bound](#the-cost-bound) raises that charge when higher. The reported `cost` is the amount charged to the budget ledger, not a claim about the invoice. `cost_known=False` when the reservation or usage bound determines the charge, or when the calculator cannot prove every billed category, even if its readable figure is higher. A readable calculator figure at least as high as the reservation yields `cost_known=True` only for a model priced at table rates without additional billing categories.

LiteLLM can return zero for a model missing from its price map or for missing usage. A contacted call with zero calculator cost is treated as unpriced. The usage bound is calculated as:

```text
U = uplift × [ (prompt − cached) × max(input, cache write) × Tin
             + cached × cached input × Tin
             + completion × output × Tout ]
```

`Tin` and `Tout` are 2 and 1.5 on a model with a long-context tier and 1 otherwise, and `uplift` is 1.10 on the models it applies to. `U` requires table rates, a matching answered model, a `default` or `flex` service tier, valid prompt, completion and cached-token counts, at least one prompt token, and at least one output token when the answer has text. It can raise the ledger charge above the reservation, but cannot lower it. A charge above the ceiling is recorded after the contacted attempt because that call has already happened.

Without a cost ceiling nothing is reserved. A readable calculator figure is reported as measured; an unreadable one is reported as `U` where it can be built, and otherwise as zero, both with `cost_known=False`.

### What `cost` means

`meta.cost` and a usage record's `cost` depend on whether a cost ceiling was set, which model answered, and whether LiteLLM's calculator could price the call. Read `accounting_known` (`cost_known` on a usage record) first.

| Cost ceiling | Calculator figure | `cost` | `accounting_known` |
|---|---|---|---|
| none | readable | the calculator figure | true |
| none | zero or unreadable | `U` where its inputs are proven, otherwise `0` (unmeasured) | false |
| set | readable for a table model at table rates without extra billing categories, and at least the reservation | the calculator figure | true |
| set | any other response | at least the reservation; a higher readable figure or `U` wins | false |

A cache hit costs `0` and is measured. An answer from an `llm_fn` reports `0` with `accounting_known` false, because nothing here can see what the callable spent.

Where the bound cannot be established, `limits.max_cost` raises `ConfigError` rather than claiming an invented figure. That happens when the model has no published price, since a model this table has never heard of may cost more than anything in it, and when `llm.max_tokens` is not a finite positive number, since output with no cap can run until the provider stops it. `pricing.rates_for` returns `None` for an unlisted model and `pricing.can_bound` answers the question without raising. No bound is computed at all when no cost ceiling is configured, so a run bounded on calls or rows is never refused over an unfamiliar model.

**The minimum cost bound is the full output allowance.** The prompt contributes relatively little; `llm.max_tokens` at the model's output rate, with the long-context output multiplier and regional uplift where they apply, contributes the rest. With the default `gpt-6-luna` model and `llm.max_tokens=25000`, the output-only floor is about $0.020625. A lower `limits.max_cost` refuses the call before provider contact. Reduce `llm.max_tokens` when the answer needs fewer tokens.

`pricing.resolve_rates` accepts `llm.price_per_input_token` and `llm.price_per_output_token`, supplied together, for a model the table does not carry. For such a model the rates are used as given, so they must already cover every tier it can be billed at: cache writes, a long-context tier, regional processing. For a model the table does carry, supplied rates are read as its base input and output rates: the entry's cache-write ratio, long-context tier and regional uplift still apply to them, and the bound is never lower than the table's own, so supplying the published base rates cannot drop a charge the model is known to carry. Both are accepted `llm.*` overrides, consumed by the client and never forwarded to the provider, so an unlisted model can be given a cost ceiling by whoever knows what it costs. Supplying one commits you to both, and a value that is not a finite positive number is refused instead of being passed over in favor of the table, since an override that was meant and then ignored produces a bound lower than the one you asked for.

### Which ceilings bind

A ceiling binds only on an axis the answering client declares through `budget_capabilities()`. A configured ceiling on an undeclared axis raises `ConfigError` at the first fetch, naming that ceiling and what the client declared. `limits.max_rows` binds any client, since the records are counted here.

The practical case is `llm_fn`. `FunctionClient` declares rows alone, so `limits.max_calls`, `limits.max_cost`, or `limits.max_seconds` on a run that supplies `llm_fn` is refused.

For the same reason, **a result produced by an `llm_fn` sets `meta.accounting_known` to false**. Its `calls`, `cost`, and token counts describe only the library's visible bookkeeping. A callable may answer locally, contact one provider, or fan out to several; none of that work is visible to the wrapper. The result therefore reports unknown accounting instead of presenting `cost=0.0` as a measurement.

### Bounding one request

Limits configured on the source apply to every fetch it makes. To bound a single request instead, pass the ceiling with the request: `properties` on `fetch` and `afetch`, or `optional_properties` on `execute_query` and `execute_query_nl`. Ordinary per-call properties override the instance's values; `limits.*` and `max_records` only tighten configured ceilings. Cost, row, call and time ceilings accept `None` as no change; `limits.max_retries=None` is invalid. Per-call values are not written back.

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
| `plan.mode` | `flat` | `flat` asks once. `partitioned` asks the whole request first and splits it by a key the analysis found when that answer falls short. `row_by_row` asks once per entity in a list the analysis found. An unknown value raises `ConfigError`. |
| `plan.max_continuations` | `3` in `partitioned`, else `0` | How many times a part may ask again for records it did not get. At `0` each part asks once. It bounds asks per part, not provider attempts or planning calls; each ask may still be retried up to `limits.max_retries` times, three attempts by default. |
| `plan.max_leaves` | `100` | Most terminal slices. A partitioned split that would exceed it is refused and its slice runs as one request, and row-by-row keeps its batch slice. |
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
| `plan.leaf_prompt` | `keyed-leaf-fetch-1` | The leaf prompt contract for non-flat modes. `keyed-leaf-fetch-2` adds a completeness requirement: return every matching record rather than a sample, give the expected count as a sentence that says it is not a limit, and return `[]` only when nothing matches the column filters within the request scope. It also splits the leaf's constraints. A constraint that partitioning introduced and that names one schema column, matched without regard to case, is sent as a column filter every record must satisfy. Everything else is request scope that records need not carry: every constraint the analysis step or the caller set for the whole request, even one whose key matches a column, and child-specific slices on keys that name no column, such as a decade. It applies to the root, single-leaf and child prompts, and travels on wire version 5. The count is still omitted when `plan.expose_estimate` is false. Refused in `flat` mode, which sends the unversioned fetch prompt. |
| `plan.child_wording` | `parent` | How a child leaf's retrieval wording is chosen in `partitioned` mode. `parent` gives every child its parent's wording, separated only by its filter. `canonical` derives it from the root request and the child's validated filters, such as `…, restricted to the slice the column filters define ("region" = "North"); the column filters are authoritative`, and never asks a model to write it. Only the column filters are named, by the rule `keyed-leaf-fetch-2` uses. Request scope stays out of the wording: its request-level part is already in the request, and a child-specific slice on a key that names no column, such as `decade = "1940s"`, reaches the leaf as per-child request scope. Names and values are quoted as JSON literals, so a model-proposed key reads as data. Refused outside `partitioned`. |
| `plan.filter_enforcement` | `flag` | What happens to a record whose checkable filter column contradicts its leaf's filter. `flag` keeps it; `drop` removes it at the leaf, before reduction merges it into anything. Both count it in `meta.filter_contradictions`. `drop` is refused in `flat` mode, which has no filter. |
| `plan.range_filters` | `False` | In `partitioned` mode, reads a partition proposal written as integer spans, such as `1946–1952`, on a column the schema declares `int` or `integer`, as inclusive `Range` filters instead of exact text that no record's value can equal. The spans must not overlap and each must start at or below its end, or the proposal is refused with the reason. Requires `plan.leaf_prompt="keyed-leaf-fetch-2"`, because a range travels only on that contract's wire version. |
| `plan.partition_guardrails` | `False` | In `partitioned` mode, sends the `partition-2` proposal prompt, which adds generic rules: no identifier or continuous-measurement keys, no placeholder or catch-all values, and a value count calibrated against the slice's own row estimate and `plan.target_rows_per_leaf`. It also refuses a proposed key the schema declares `int`, `integer`, `float` or `number` unless its values are integers or integer spans covering at most 1,000 integers; the reason appears in `meta.gaps` as `guard_collapsed`. Adds no call. |
| `plan.split_policy` | `evidence` in `partitioned`, else `always` | In `partitioned` mode, whether the root is asked first (`evidence`, the default) or always splits (`always`). Under `evidence` the root leaf runs first as a probe, with its continuations. The plan splits only when the provider says the probe's answer hit its output allowance, the answer ended mid-document and only its complete records could be read, the continuations ran out while the answer was still adding records, the probe failed outright (reported as a `probe_failed` gap), the probe holds no keys although the estimate expects records, or it holds fewer than half the keys an estimate above `plan.split_threshold_rows` expects. Otherwise the probe's answer is the result, with no proposal call. A probe that meets `plan.exact_rows` is accepted unless its answer was cut or ran out of continuations; a target of zero asks nothing and reports no `split_decision`. When the plan does split, a split-key value in the probe's records with a complete key that no proposed child asks for is added to the proposal, with its number of distinct keys as its estimate. If no proposal survives, including one those values take past `plan.max_partitions`, the probe's answer is the result. Otherwise the probe's records are merged with the children's under the origin `root`. The probe and its continuations count in every published bound. `meta.split_decision` says what was decided. `evidence` is refused outside `partitioned`. |
| `plan.split_threshold_rows` | `120` | Under `plan.split_policy="evidence"`, the estimate above which a short probe answer triggers a split. A value other than 120 is refused with any other policy. |
| `plan.distribution_search` | `False` | Direct-Python-only opt-in for rule-backed distribution selection. Requires `partitioned`. |
| `plan.identity_partitions` | empty | Direct-Python-only list of versioned finite-domain rule mappings accepted by `IdentityPartitionRule.from_dict()`. |
| `plan.max_strategy_candidates` | `5` | Direct-Python-only candidate bound. Must be at least `1` when distribution search is enabled. |

The five settings from `plan.leaf_prompt` to `plan.partition_guardrails` are experimental. Neither an accepted probe nor a completed split establishes that every matching record was found.

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
figures. Use `planning.worst_case_attempts(PlanLimits(...), mode, split_policy=...)` for the conservative configured
attempt diagnostic; a hand formula that omits the selected schedule or proposal work understates the
request.

A partitioned request's figure is a worst case over the plan's caps, not a forecast.
`planning.planned_logical_calls(limits, mode, planning_order=..., distribution_search=...,
split_policy=...)` counts the planning schedule's fact calls (one for `planning.combined`, two for
the locked schedules, three for `planning.schema_first`, the default, and
`planning.cardinality_first`), the root probe and its continuations under `split_policy="evidence"`,
then the split proposals, or the distribution calls when distribution search is on, then the leaves
with their continuations. `LlmSource` runs a partitioned request under `split_policy="evidence"`
with three continuations unless told otherwise, so the root and each leaf may ask four times. With
the caps of `PlanLimits()` (twenty partitions at depth two, 100 leaves), `max_continuations=3`,
`split_policy="evidence"` and the default schedule, that is 428 calls: three fact calls, the root's
four asks, at most 21 partition proposals (one per node that can split, the root and its twenty
children) and at most 100 leaves of four asks each. With the default retries that is 1,284 provider
attempts. At the flat per-attempt bound of a short request, about $0.0209 on `gpt-6-luna` and
$0.0431 on `gpt-4o`, the worst case is roughly $26.78 and $55.40; leaf prompts carry their
constraints and are a little longer. With `PlanLimits()` and the default `split_policy="always"`,
the figure is 124 calls and 372 attempts. A fetch is not refused against that figure. Each planning
call and each provider attempt is admitted against the ceiling when it is made, so a ceiling below
the worst case stops the run where the money runs out and reports it in `stopped_by`.

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
kept unmerged because no key was identified. A valid empty response alone is not a gap.

For non-flat requests, `split_coverage_short` reports when all accepted child estimates sum below
the parent's lower estimate. `observed_coverage_short` reports when complete distinct identities
before the row cap fall below the root's lower estimate. These estimate-based warnings do not
change `outcome` or prove missing records. A short response without such evidence remains undetected.

## FetchResult and ExecMeta

`FetchResult` holds `records` and `meta`, and supports `len()` and iteration over the records. Those
remain its only dataclass fields. Direct Python distribution-search results expose
`.planning_receipt` and `.with_planning_receipt(...)`; `to_dict()` adds a top-level
`planning_receipt` only when one exists.
Results without distribution search and refusals before plan construction keep the two-key
serialization.

`ExecMeta` reports `gaps`, `stop_reasons`, `stopped_by`, `record_origins`, `asked`, `outcome`, `model`, `calls`, `prompt_tokens`, `completion_tokens`, `cost`, `latency_seconds` (published as `elapsed_seconds` in the CLI and MCP JSON), `cache_hits`, `requested_attrs`, `row_estimate`, `cardinality_estimate`, `primary_keys`, `observed_key_count`, `marginal_key_yields`, `incomplete_key_records`, `normalization_collisions`, `repaired_numbers`, `filter_contradictions`, `split_decision`, `observed_keys`, `remapped_keys`, `unmapped_keys`, `type_mismatches`, `key_collisions`, `unchecked_types`, `truncated_to`, and `accounting_known`.

`row_estimate` is the accepted non-flat bootstrap hint for the root slice. A reported zero remains
zero. Flat mode, an unknown estimate, or a bootstrap transformation rejected during fallback reports
`None`. It is neither the returned row count nor an expected-completeness measure.

`stop_reasons` is the ordered, distinct list of extraction limits and discarded final output.
`stopped_by` is its first member for callers that consume one reason. `gaps` carries operational
diagnostics, including planning fallback, failed or unserved tasks, and discarded records. An empty
gap list says only that no supported diagnostic fired.

**Read `accounting_known` before reading `cost`.** It is `False` when the answer came from a caller-supplied `llm_fn`, when provider accounting is unreadable, or when a call under a hard cost ceiling was charged its reservation or a higher usage bound, or its calculator could not prove every billed category ([settling the charge](#the-cost-bound)). In those cases `cost` is the conservative ledger charge, not an exact invoice. For an `llm_fn`, a `cost` of `0.0` means "not measured", not "free", and a `calls` value counts invocations of the callable rather than provider requests.

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

`ConfigError` covers more than a bad key. A ceiling configured on an axis the resolved client does not declare raises one, as does an unknown or invalid `limits.*` value, and so does a `limits.max_cost` whose bound cannot be established because the model has no published price or `llm.max_tokens` is not a finite positive number. `QueryError` covers the same attribute requested twice. Two *distinct* names whose fuzzy forms coincide (`first name` and `first_name`, or two scripts a normalizer flattens together) are not refused: the fuzzy index simply drops the entry it cannot separate, so exact keys still match and a key that only matches fuzzily is reported in `meta.unmapped_keys` rather than assigned by guess.

A missing provider credential is **not** a `ConfigError`. The provider SDK handles authentication. With no answered extraction response and no retained records, a failed attempt raises `TransportError` if the retry policy is exhausted or `max_calls` prevents the next retry. If `max_cost` or `max_seconds` refuses that retry, or a ceiling refuses the first attempt, the fetch returns `stopped_by_limit`. If an extraction response has already been parsed, the fetch keeps its records with `outcome=partial` and a gap for the failed part. The library reads credential environment values only to mask diagnostic text. A `TransportError` mentioning credentials may indicate an absent or invalid key.

`BudgetExceeded` raised inside a fetch does not reach the caller. A refusal before any extraction
answer returns no records with `outcome=stopped_by_limit`. If later work is refused after an
extraction answered, the answered records are retained with `outcome=partial`. In both cases the
stop fields name the axis. `BudgetExceeded` remains public for callers driving a `Budget` directly.

`ParseError` carries the raw response text, because re-running a query is not guaranteed to reproduce the failure.

## Tests

Run these commands from `lib/` after installing the `dev` and `llm` extras in the active environment.

```bash
python -m pytest tests/unit -q
```

Unit tests only. Every I/O boundary is doubled and no test touches the network. Coverage is not collected by default and no threshold is enforced; pass `--cov=src` to `pytest` to measure it.

The Pyright development gate uses a separate `lib/.venv` because `pyproject.toml` points there. Create it from `lib/` and install the development dependencies in it before running that gate:

```bash
python -m venv .venv && source .venv/bin/activate
```

### Component and example suites

With each component's `dev` extra and the library's `llm` extra installed, run these commands from the repository root:

```bash
(cd lib && python -m pytest)
(cd platform/llm-cli && python -m pytest)
(cd platform/llm-mcp && python -m pytest)
python -m pytest -q tests/integration/test_offline_examples.py
```

The last command runs 14 no-key examples in isolated processes and compares their output with the captured examples. Its guard blocks Python socket connections, datagram sends, and subprocess helpers. Provider-backed examples are excluded.

### The contract test

`tests/unit/compat/test_contract.py` compares this source's method signatures against `blue_data.source.source.DataSource` and fails when they drift.

`LlmSource` inherits that base class, so most divergence is already a type error. The contract test also catches untyped signature drift and required methods that resolve to a default base implementation. Six transaction methods are recorded in the test's `OUT_OF_SCOPE` map and refuse by name; the three orchestrator-tool methods are overridden locally because they have source-specific behavior.

The test prints the path of the `blue_data` package it inspected and includes that path in failures. Run it after installing or upgrading `blue-data` so the comparison covers the package used by the current environment.

## Distribution

None of the three packages is on a package index. Install from source; `blue-data` must be installed first. See [Install](#install) above and the dependency section of the top-level [README](../README.md).
