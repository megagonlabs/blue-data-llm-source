# blue-data-llm-source

The library. A language model presented as a data source.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
scripts/install.sh               # blue-data from its checkout, then this package
BLUE_DATA_PATH=/path/to/blue-data/lib scripts/install.sh   # if it lives elsewhere
```

The virtual environment is not optional for the type gate. `pyproject.toml` points
`pyright` at `.venv` in this directory, so running it without one reports every import
as unresolved rather than reporting real typing errors.

The core depends on `blue-data` and `tenacity`. `blue-data` supplies the `DataSource` base class this source inherits, and it pulls `pandas`, `numpy`, and `jsonpath-ng` along with it, which is the part worth knowing before you install. `litellm` and `diskcache` come with the `llm` extra and are imported lazily, so the package works and its tests run without them.

## LlmSource

```text
LlmSource(name="llm", properties=None, client=None)
```

`LlmSource` inherits `blue_data.source.source.DataSource`, so it is a data source by type, not by resemblance. The base is a plain class, not an abstract one. That makes the base order load-bearing. Most of the base's methods return an empty result instead of raising, so one left unoverridden here answers nothing instead of failing. That is no longer true of all of them: the transaction methods raise `NotImplementedError` naming this source, and the orchestrator-tool methods carry real behaviour. This source overrides those three instead of inheriting them: two are narrowed to the tools it can answer, and `execute_query_nl_tools` is refused. Where a method does return empty, a registry walking the surface would see a source that works and returns nothing. So the class is declared with `DataSource` as its base, its own implementations come first, and a test asserts that the methods this source must answer resolve here and not to the base.

Inheriting the base class does not make the source reachable by protocol name. `DataRegistry.connect_source` returns the live registered instance while one is present, and reconstructs an equivalent source from the recorded class path once it is not. The route is to build the source yourself and hand the instance to `register_source`. That is enough: the registry records the class alongside `name` and `properties`, and rebuilds an `LlmSource` from that record, so a source registered this way survives a round trip through the registry.

`properties` carries connection details and configuration. `client` injects a model client, so tests and alternative providers need no subclassing. The full contract is in [Injecting a client](#injecting-a-client) below; the short version is two methods with the prompt first and the merged per-call properties second, plus an optional third method declaring which ceilings the client can be held to.

[COMPATIBILITY.md](COMPATIBILITY.md) records what this library does differently from the data-source design document, and from the behaviour a caller being ported was written against, including the whole budget contract in one place. Read it before porting a caller.

### fetch

```text
fetch(query, attrs=None, context=None, max_records=None, properties=None, llm_fn=None,
      budget=None) -> FetchResult
afetch(...)   # the async form; fetch() wraps it
```

`query` is the question. `attrs` is either a list of names or a list of `{"name": ..., "type": ...}` dicts. `context` is free text that may clarify what an attribute name means. `max_records` caps the rows returned. `llm_fn` is a `(prompt) -> str` callable used as the client for that call. `budget` is a `Budget` you own; supply one and the fetch derives a child from it, so the ceilings accumulate across every call you hand it to, and omit it to have one built from the configured limits for that call alone.

`fetch` raises `ConfigError` if called from inside a running event loop. Use `afetch` there.

Returns a `FetchResult` with `records` and `meta`. A direct Python fetch with rule-backed
distribution search enabled can also expose an optional `planning_receipt` property; the dataclass
itself retains its two original fields.

### estimate

```text
estimate(query, attrs=None, context=None, properties=None, llm_fn=None) -> Estimate
```

What one **flat** fetch of `query` could cost at most, answered without contacting anything. It reads no `plan.*` setting, so a request with parts or continuations can cost several times this figure. It builds the prompt this source would actually send and bounds it, so the figure belongs to the request you are asking about, not to an average of requests like it, and it bounds the whole fetch rather than one attempt. A fetch may retry, so `max_cost` is the per-attempt figure accumulated once per attempt, added the way the budget ledger adds it, not multiplied. Below six attempts the two are identical in binary floating point. From six upward they diverge, and the gap widens with the attempt count: about half of all per-attempt figures differ by one unit in the last place at six attempts, and nearly all of them differ by more at a hundred. Any of it is enough to refuse the final attempt of a request sized against its own estimate. The shipped default is three attempts, so this is latent until someone raises `limits.max_retries` past four. The estimate accumulates the way the thing it is checked against accumulates, so it cannot drift out from under a caller who does. Sizing a ceiling against the per-attempt figure means one transient failure consumes it and the retry is refused.

`llm_fn` is accepted so the estimate is asked of whatever would actually answer, and a callable cannot price itself, so a request against one is reported unestimable. The same is true of an injected client that does not implement `bound_for_attempt`.

The returned `Estimate` carries `bounded`, `max_cost`, `max_cost_per_attempt`, `max_attempts`, `model`, `prompt_bytes`, `max_output_tokens` and, when unbounded, `reason`.

A planner sizing a fan-out uses it to decide how wide to go; an operator uses it to check a request against a ceiling before spending anything on it.

A model this package cannot price returns `bounded=False` with `reason` saying what is missing, instead of raising, because "how much might this cost" is a reasonable question to ask about a model nobody here has a price for and the honest answer to it is that we cannot tell you. An empty request returns `bounded=True` with a `max_cost` of zero, since it never reaches a model.

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

`execute_query` takes either a natural language string or a dict with an `op` key. Supported ops are `LLM.FETCH`, `LLM.MODELS`, and `LLM.ESTIMATE`; an unknown op raises `QueryError` listing them. `LLM.ESTIMATE` reads the same `query`, `attrs` or `attr_names`, and `context` keys `LLM.FETCH` does, and returns the estimate as a one-record list, because that is what this surface returns. A caller that wants the `Estimate` object itself calls `estimate` directly.

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

`DataSource` offers a tool-calling interface for an orchestrating model: a source advertises tools, and the caller invokes them one at a time. Its tool set describes a relational source (databases, collections, columns, row samples, foreign keys), and four of its eight tools answer here.

| Tool | Returns |
|------|---------|
| `list_databases` | `["llm"]`, the one virtual database |
| `list_collections` | `["public"]`, the one virtual collection |
| `get_schema` | The virtual entity with its one text attribute, and an empty relation map |
| `execute_query` | The records for `tool_input["query"]`, fetched through the query surface |

The other four are not advertised. `get_metadata`, `get_column_stats`, and `get_relations` would answer with an empty result and no explanation, and `get_sample` raises a `TypeError` from the base dispatcher, which passes `row_sample_size` to a `fetch_entity_stats` that takes no such argument. The four definitions above are the base class's own, filtered by name, not rewritten here, so an advertised schema cannot drift from the dispatcher that reads it.

`execute_nl_tool` refuses any other name with a `ConfigError` naming the four that are served. Nothing on this path raises outside `LlmSourceError`, including a name that is not a string and a `tool_input` missing a key the tool requires.

`execute_query` is the one tool here that reaches a provider, and it takes its ceilings where the query surface takes them: a `limits.*` key in `optional_properties` reaches `execute_query` and binds that call.

`execute_query_nl_tools` is not served and raises `ConfigError`. It drives a loop of up to ten provider calls that this source's ceilings do not govern, so serving it would put an unbounded spend path behind a method this package never chose to offer. `execute_query_nl` is the natural-language route, and its spend and time ceilings are enforced.

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

**Declaring `Capability.ESTIMATE` commits you to two things**, because `estimate` reports a bound on the whole fetch and reaches it by accumulating the per-attempt figure once per permitted attempt. First, `bound_for_attempt` returns a true upper bound on one provider attempt. Second, one call makes at most `budget.effective_retries() + 1` provider attempts, reading `budget` from the properties handed to it. A client with its own retry or fallback policy that ignores the allowance will spend past the published estimate, and a caller who sized a ceiling from that estimate then has an attempt refused. A client that cannot promise the second omits the capability, and `estimate` reports `bounded=False` with a reason, the honest answer rather than a figure the run can exceed.

`bound_for_attempt` is what makes `estimate` and the run agree: the estimate and each
reservation both call it, so the figure a caller sizes against and the figure a ceiling
enforces cannot be computed two different ways. It returns the most one attempt at *prompt*
could cost, and `estimate` accumulates it once per attempt a fetch permits. A client that
cannot price itself simply omits it. `estimate` then returns `bounded=False` with a reason,
which is the honest answer to a question it cannot answer, and a cost ceiling against that
client is refused separately by `budget_capabilities`. `DefaultLlmClient` implements it from
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
print(sized.bounded, sized.max_cost)  # True 0.006 — per-attempt, accumulated over the attempts

result = source.fetch("the member states of the European Union", attrs=["country"])
print(result.records)
print(result.meta.model, result.meta.calls, round(result.meta.cost, 4), result.meta.outcome)
```

Claim inside `budget.attempt(...)` and resolve on every path out of it. The context manager exists so the cleanup is registered before the claim is taken; `reserve` is still available and still correct, but it leaves a window in which an interrupt orphans a claim. `settle(actual)` releases whatever the claim overshot by, `keep_unpriced()` leaves the claim standing for an attempt that reached the provider and cannot be priced, and `release()` gives back an attempt that never reached it. A claim left outstanding is headroom the budget never grants again.

Declare only what the client can prove. `DefaultLlmClient` declares `Capability.ALL` because it makes the provider call itself. `FunctionClient`, which wraps an `llm_fn`, declares `Capability.ROWS` and nothing else, because it cannot know what the caller's function is billed, how to stop it when a deadline passes, or even how many provider calls one invocation of it becomes, so a call, cost, or time ceiling over an `llm_fn` is refused rather than settled at zero.

## Configuration

Provider settings are `llm.*` keys in `properties`. The accepted set is closed, and an unknown key raises `ConfigError` naming what is accepted, so a typo fails loudly instead of being ignored.

| Key | Meaning |
|---|---|
| `llm.model` | Model name. Defaults to `gpt-4o` |
| `llm.temperature`, `llm.max_tokens`, `llm.stream`, `llm.timeout` | Passed to the provider; what `llm.stream=True` does to the accounting is [below](#streaming) |
| `llm.cache`, `llm.cache_dir` | Enable and locate the on-disk cache |
| `llm.max_parallel` | Concurrency bound, per event loop |

The numeric keys carry a range as well as a name, checked at the same boundary and raising the same `ConfigError`. `llm.max_tokens` is a whole number of at least 1, or `None` for no cap. `llm.temperature` is a finite number; no narrower range is imposed, because the usable span differs by provider and refusing a value the provider accepts would be worse than letting it through. `llm.timeout` is a finite positive number of seconds, and `llm.max_parallel` a whole number of at least 1.

`llm.cache`, `llm.cache_dir`, and `llm.max_parallel` are consumed here and never sent to the provider, and so are `llm.price_per_input_token` and `llm.price_per_output_token`, which supply the rates for a model `pricing.PRICES` does not carry.

Each distinct `llm.cache_dir` opens an on-disk backend holding file descriptors. A client keeps at most eight open and closes the coldest beyond that, and `DefaultLlmClient.close()` releases them all. It is also a context manager. A short-lived script needs neither; a long-lived service that rotates cache directories, one per tenant or per day, does, because nothing else releases those handles and the process eventually runs out of descriptors for anything at all.

### Streaming

`llm.stream=True` is accepted. It exists for compatibility and **it does not make anything faster.** A fetch parses one whole JSON document into records, so there is no partial result for a caller to receive: the chunks are read to the end, reassembled into a single response, and handed to the same path a non-streamed response takes. The same records come back, priced the same way, at the same time or slightly later. The reason to set it is that some provider configurations and proxies require or default to a stream, and until now those callers could not use this package at all.

One thing does change, and it is worth knowing before you set it. A stream carries the provider's token counts only if the provider chooses to send them, and many do not. When none arrive the call cannot be priced: the reservation stays at the bound claimed for the attempt instead of settling at zero, the usage record reports `cost_known=False`, and the fetch reports `accounting_known=False`. That is the same treatment any call whose cost cannot be read already gets — an unreadable price is not a free call. A stream that does carry usage is priced exactly like a non-streamed one, and reports its accounting as known.

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
| `limits.max_retries` | Retries per call; the client makes this many attempts plus one, narrowed to the smallest depth along the budget chain |

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

This list is the only copy. Everywhere else in the documentation that mentions the time ceiling links here instead of restating it, so it cannot go stale in one place and not another.

The key names are a closed set, and an unknown one raises `ConfigError` listing what is accepted, as do the near misses `limit.*` and a bare `limits`. Every value is checked as the ceiling is built: a wrong type, a `bool`, a negative number, or a non-finite number raises `ConfigError` naming the field. A `NaN` ceiling is the case worth stating, because it compares false against every spend and so reads as configured while enforcing nothing.

### The cost bound

`llm/pricing.py` produces an upper bound on one call *against the rates it holds*, or refuses. Those rates are a static snapshot of published list prices with no effective date and no freshness check, so everything below bounds the call's size correctly and is only as accurate as the table's prices. If a listed price rises, or an alias or proxy bills differently, the reservation is too small and the difference appears at settlement. Supply `llm.price_per_input_token` and `llm.price_per_output_token` to bound against rates you maintain. The bound is the prompt's length in UTF-8 bytes plus a framing allowance at the model's published input rate, plus the whole `llm.max_tokens` allowance at its output rate. Counting bytes rather than dividing them by an average bytes-per-token figure is what makes it a bound: a byte-pair tokenizer merges byte sequences, so every token it emits consumes at least one byte of the encoded prompt and no tokenizer can exceed the count, while an average is exceeded by text of short words, punctuation, or non-Latin script. Counting characters would not have been a bound, although it reads like one, because a tokenizer splits a character whose encoding it has no merge for. The bound is reserved before the attempt and reconciled against the real cost afterwards, which releases the overshoot. `estimate` reports this same figure, so a request sized against it and a run bounded by it cannot disagree with *each other*: both compute from the same table, and neither is a claim about what the provider will invoice.

Where the bound cannot be established, `limits.max_cost` raises `ConfigError` rather than claiming an invented figure. That happens when the model has no published price, since a model this table has never heard of may cost more than anything in it, and when `llm.max_tokens` is not a finite positive number, since output with no cap can run until the provider stops it. `pricing.rates_for` returns `None` for an unlisted model and `pricing.can_bound` answers the question without raising. No bound is computed at all when no cost ceiling is configured, so a run bounded on calls or rows is never refused over an unfamiliar model.

**Size a cost ceiling against the floor, which is the output allowance alone.** The prompt contributes little; `llm.max_tokens` at the model's output rate contributes the rest, whatever the answer turns out to be. With the shipped defaults of `gpt-4o` and `llm.max_tokens` of 4096 that floor is about $0.041, so `limits.max_cost` below it refuses every call before one is made. Lower `llm.max_tokens` to what the answers actually need and the floor falls with it; raising the ceiling to cover an allowance nothing uses buys nothing.

`pricing.resolve_rates` accepts `llm.price_per_input_token` and `llm.price_per_output_token`, supplied together, for a model the table does not carry. Both are accepted `llm.*` overrides, consumed by the client and never forwarded to the provider, so an unlisted model can be given a cost ceiling by whoever knows what it costs. Supplying one commits you to both, and a value that is not a finite positive number is refused instead of being passed over in favour of the table, since an override that was meant and then ignored produces a bound lower than the one you asked for.

### Which ceilings bind

A ceiling binds only on an axis the answering client declares through `budget_capabilities()`, and each configured ceiling on an undeclared axis raises `ConfigError` at the first fetch naming that ceiling and what the client did declare. Reporting a ceiling that is not holding anything is worse than declining to hold it, because the caller believes it. `limits.max_rows` binds any client, since the records are counted here.

The practical case is `llm_fn`. `FunctionClient` declares rows alone, so `limits.max_calls`, `limits.max_cost`, or `limits.max_seconds` on a run that supplies `llm_fn` is refused.

For the same reason, **a result produced by an `llm_fn` sets `meta.accounting_known` to false**, and its `calls`, `cost` and token counts describe this library's own bookkeeping, not provider work. A callable that fans out to five providers and spends real money still reports one call and no cost, because none of that is visible from here. Reporting it as unknown is the honest answer; reporting `cost=0.0` as though it were measured is not. An earlier version settled such a call at a cost of zero, which made a cost ceiling appear to hold over a callable that might be spending anything, and counted one invocation as one provider call, which it is not: the callable may answer from memory, contact one provider, or fan out to several.

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
| `plan.max_leaves` | `24` | Most terminal slices. An expansion that cannot fit is rejected as a whole; row-by-row falls back to the intact batch slice rather than running a prefix. |
| `plan.key` | none | The attribute identifying a record, so parts returning the same record merge. Must be one of the attributes you asked for. |
| `plan.partition_key` | none | Preferred root split dimension. Descendants may choose another dimension and cannot repeat an ancestor dimension. |
| `plan.max_depth` | `2` | Deepest partition level. `0` disables subdivision while retaining non-flat bootstrap analysis. |
| `plan.max_partitions` | `4` | Most children one proposal may create. A value below `2` leaves the node unsplit. |
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

Distribution search is opt-in and is exposed only through direct Python calls in this release. It
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

`ExecMeta` reports `gaps`, `stop_reasons`, `stopped_by`, `record_origins`, `asked`, `outcome`, `model`, `calls`, `prompt_tokens`, `completion_tokens`, `cost`, `latency_seconds`, `cache_hits`, `requested_attrs`, `row_estimate`, `cardinality_estimate`, `primary_keys`, `observed_key_count`, `marginal_key_yields`, `incomplete_key_records`, `normalization_collisions`, `observed_keys`, `remapped_keys`, `unmapped_keys`, `type_mismatches`, `key_collisions`, `unchecked_types`, `truncated_to`, and `accounting_known`.

`row_estimate` is the accepted non-flat bootstrap hint for the root slice. A reported zero remains
zero. Flat mode, an unknown estimate, or a bootstrap transformation rejected during fallback reports
`None`. It is neither the returned row count nor an expected-completeness measure.

`stop_reasons` is the ordered, distinct list of extraction limits and discarded final output.
`stopped_by` is its first member for callers that consume one reason. `gaps` carries operational
diagnostics, including planning fallback, failed or unserved tasks, and discarded records. An empty
gap list says only that no supported diagnostic fired.

**Read `accounting_known` before reading `cost`.** It is `False` when the answer came from a caller-supplied `llm_fn`, because a callable may answer from memory, contact one provider, or fan out to several, and none of that is visible from here. When it is `False` the accounting fields describe this library's own bookkeeping and nothing else, so a `cost` of `0.0` means "not measured", not "free", and a `calls` of `1` counts one invocation of your callable rather than one provider request.

`unmapped_keys` matters. When the model returns a key that cannot be matched to a requested attribute, the key is reported there rather than dropped in silence. `key_collisions` is the same idea for the opposite problem: when two returned keys map to one requested attribute, the first wins and the second is reported with the value it carried, because which spelling the model meant is not knowable here. Two occurrences of the identical key inside one JSON object are a different case and raise `ParseError`, since there the two values have nothing to tell them apart by and Python's decoder would otherwise keep the last of the pair without a trace.

`outcome` uses the closed vocabulary `ok`, `empty_query`, `model_returned_none`,
`filtered_to_empty`, `partial`, `stopped_by_limit`, and `truncated`. A partial plan outranks a
simultaneous cap because every cap still appears in `stop_reasons`. Without interruption, any
non-row stop produces `stopped_by_limit`;
`max_rows` produces `truncated` when records remain and `stopped_by_limit` when none do. With no stop,
records produce `ok`, an answered empty response produces `model_returned_none`, and records removed
by reduction produce `filtered_to_empty`.

`calls` and `cost` are read from the run's budget rather than summed from usage, so they count what was actually claimed, including an attempt that reached the provider and failed. Tokens and the model name come from the `Usage` entries the client appended to this call's `usage_sink` list, and from nothing else, which is what keeps two concurrent fetches through one client from reporting each other's tokens.

## Errors

`LlmSourceError` is the root. Under it: `ConfigError` for rejected configuration, `QueryError` for a malformed query, `TransportError` for provider failures and malformed provider responses, `ParseError` for a response that cannot be read as records, and `BudgetExceeded` when a limit would be crossed.

Nothing else escapes `fetch`, `afetch` or `estimate`. Anything unexpected is wrapped in `LlmSourceError` with the original attached as `__cause__`, so `except LlmSourceError` is sufficient and the original type is still there for a maintainer. A `KeyboardInterrupt` or a cancellation is deliberately not caught. Those are not this library's to reclassify. The guarantee is enforced at the entry points rather than assembled from per-site discipline, which is what made it hold everywhere it was remembered and fail everywhere it was not.

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

### The contract test

`tests/unit/compat/test_contract.py` compares this source's method signatures against `blue_data.source.source.DataSource` and fails when they drift.

`LlmSource` inherits that base class, so most divergence is already a type error. The contract test covers what inheritance does not: a signature that still type-checks because the base method is untyped, and a method that resolves to the base class instead of to this one. Most `DataSource` methods return an empty result, so one left unoverridden answers nothing instead of failing, which is the quietest way a source can be broken. Not all of them: six transaction methods are recorded in the test's `OUT_OF_SCOPE` map and refuse by name, and the three orchestrator-tool methods carry real behaviour, which is why this source overrides them locally rather than inheriting them.

It prints the path of the `blue_data` it compared against, and names that path in any failure. Check it before changing this source to match a signature: an installed copy can be older than a checkout, and confirming against a version nobody runs is worse than not checking at all.

## Distribution

None of the three packages is on PyPI or on an internal index. Install from source, and note that `blue-data` must be installed first. See [Install](#install) above and the dependency section of the top-level [README](../README.md). Where a design document describes how these are "distributed", it is describing the intended route rather than one you can use today.
