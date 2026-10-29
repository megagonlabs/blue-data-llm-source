# Extending blue-data-llm-source

This page describes the library's extension points and the contract required by each one.

There is no plugin registry and no hook mechanism. Every extension point below is a protocol you satisfy structurally: an object with the right methods, handed in as an argument or a property. Nothing here performs an `isinstance` check against a concrete class.

| Extension point | What you supply | Handed in as | What it replaces |
|---|---|---|---|
| [The injected client](#the-injected-client) | An object with `complete` and `acomplete` | `LlmSource(client=...)` | The provider call for every request through that source |
| [`llm_fn`](#llm_fn-the-one-function-form) | A `(prompt) -> str` callable | `fetch(llm_fn=...)` | The provider call for one request |
| [Cache backends](#cache-backends) | An object with `get` and `set` | `properties={"cache_store": ...}` | Where provider responses are stored |
| [Your own price table](#your-own-price-table) | Two per-token rates | `llm.price_per_input_token`, `llm.price_per_output_token` | The published rates a cost ceiling is bounded against |
| [Identity partition rules](#identity-partition-rules) | Declared finite domains | `plan.identity_partitions` | The model's own choice of how to split a request |
| [A budget you own](#a-budget-you-own) | A `Budget` | `fetch(budget=...)` | The per-call budget the source would build |
| [The stage parsers](#the-stage-parsers) | Nothing (you reuse them) | imported directly | Nothing; they are pure functions you can call yourself |

What is **not** an extension point: the prompts, the reduction pipeline, the outcome rule, and the closed key sets. Those are fixed, and changing them is a change to this library, not a use of it.

Every example on this page runs as shown, offline.

## The injected client

An injected client is any object with the two methods below. It handles every model request made through the source that received it.

```python
from typing import Any, Protocol


class InjectedClient(Protocol):
    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str: ...
    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str: ...

    # Optional. Without it, a cost, time, or call ceiling is refused for this client.
    def budget_capabilities(self) -> frozenset[str]: ...

    # Optional, and needed for `estimate` to give a figure, together with declaring
    # Capability.ESTIMATE. Without both, a request against this client is reported
    # unestimable rather than guessed at.
    def bound_for_attempt(self, prompt: str, properties: dict[str, Any]) -> float: ...
```

The shape is checked when the client is resolved for the first fetch, not when `LlmSource` is constructed. A missing method, or one whose signature cannot be called as `complete(prompt, properties)` (a keyword-only or one-argument form included) raises `ConfigError` naming the contract rather than failing later with a raw `TypeError`.

`afetch` calls `acomplete`, and `fetch` wraps `afetch`, so the asynchronous form is the one every fetch reaches. `complete` is there for a caller driving the client directly.

### What arrives in `properties`

Two keys are written by the source on every call it makes.

| Key | Type | What to do with it |
|---|---|---|
| `budget` | `Budget` | Claim against it once per provider attempt, and resolve every claim |
| `usage_sink` | `list[Usage]` | Append this call's `Usage` entries. The fetch reads its token counts and model name from this list and nothing else |

Both are passed per call instead of being set on the client, because one client can serve several concurrent fetches and an attribute would let them overwrite each other's ceiling. For the same reason, read the budget out of `properties` rather than caching it on `self`.

`meta.calls` and `meta.cost` come from the budget claims and settlements, not from counting `Usage` entries. `meta.accounting_known` also depends on the capabilities your client declares. Appending `Usage` without making a claim can report tokens while leaving calls and cost at zero.

An optional `cache_store` arrives in this map unchanged, but an injected client must read it and implement its own cache behavior. Everything else in `properties` is the merged property map for that call. Your client's own properties sit under the caller's.

### Declaring capabilities

`budget_capabilities()` says which ceilings the source may hold you to. It returns names from `Capability`, and every configured ceiling on an axis you do not name is refused with a `ConfigError` at the first fetch.

| Declare | Only if your client |
|---|---|
| `Capability.CALLS` | Knows how many provider calls one invocation makes, and stops at the limit |
| `Capability.COST` | Knows what each call is billed, and stops at the limit |
| `Capability.SECONDS` | Can stop at a deadline |
| `Capability.ROWS` | Always safe; the source counts records itself |
| `Capability.ESTIMATE` | Implements `bound_for_attempt`, **and** makes at most `budget.effective_retries() + 1` provider attempts per call |

Declare only the capabilities the client enforces. Otherwise the caller may rely on a ceiling that has no effect.

`Capability.ALL` includes all five capabilities and therefore includes both `ESTIMATE` requirements. A client whose retry or fallback policy ignores the attempt allowance can exceed its published estimate and cause a request sized to that estimate to refuse a later attempt. A client that cannot meet the requirements should omit `ESTIMATE`; `estimate` then reports `bounded=False` with a reason.

### Claiming and resolving

Claim inside `budget.attempt(...)` and resolve on every path out of it. The context manager exists so the cleanup is registered before the claim is taken; `reserve` is still correct but leaves a window in which an interrupt orphans a claim.

| Call | For |
|---|---|
| `reservation.settle(actual)` | A call that returned and whose cost you can read. A custom client may release the unused part of its reservation |
| `reservation.settle(actual, cache_hit=True)` | An answer that never reached the provider. Releases the attempt too |
| `reservation.keep_unpriced()` | A call that reached the provider and failed, or one whose cost you cannot read |
| `reservation.release()` | An attempt abandoned before the provider entry point was invoked |

An unresolved claim inside `budget.attempt(...)` is released when the block exits, including its call count. If the provider was contacted, settle it or call `keep_unpriced()` before leaving the block. A claim made with `reserve()` remains outstanding until you resolve it.

The library does not retry an injected client's calls. Implement retries inside your client, claim each provider attempt, and keep `Usage` aligned with those attempts. A configured `limits.max_retries` bounds the built-in client only.

### Errors from a custom client

| Raise | Caller sees |
|---|---|
| `TransportError`, `ParseError`, or `ConfigError` | The same error kind when no extraction response answered |
| `BudgetExceeded` | A result with `outcome="stopped_by_limit"` or `partial`, depending on earlier answers |
| Another exception | `LlmSourceError` naming the exception type and message when no extraction response answered |

If the time limit also stops work, the failure message names `max_seconds`. Resolve a contacted attempt before raising so its budget claim is retained.

### A complete client

```python
from typing import Any

from blue_data_llm_source import Budget, Capability, LlmSource, Usage

PER_ATTEMPT_BOUND = 0.002


async def my_provider_call(prompt: str) -> str:
    """Stand in for a real provider call, so this example runs offline."""
    return '[{"country": "France"}, {"country": "Germany"}]'


class MyClient:
    def budget_capabilities(self) -> frozenset[str]:
        return frozenset({Capability.CALLS, Capability.COST, Capability.ROWS, Capability.ESTIMATE})

    def bound_for_attempt(self, prompt: str, properties: dict[str, Any]) -> float:
        # ESTIMATE requires a bound consistent with the reservation below.
        return PER_ATTEMPT_BOUND

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        raise NotImplementedError("this source always calls acomplete")

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        props = properties or {}
        budget = props.get("budget") or Budget()
        with budget.attempt(PER_ATTEMPT_BOUND) as reservation:
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


source = LlmSource(properties={"limits.max_cost": 0.05, "limits.max_calls": 4}, client=MyClient())
sized = source.estimate("the member states of the European Union", attrs=["country"])
print(sized.bounded, sized.max_cost, sized.max_attempts)

result = source.fetch("the member states of the European Union", attrs=["country"])
print(result.records)
print(result.meta.model, result.meta.calls, round(result.meta.cost, 4))
print(result.meta.prompt_tokens, result.meta.completion_tokens, result.meta.accounting_known)
```

This client does not declare `Capability.SECONDS`. A client that awaits a provider must enforce the deadline on that wait before it can promise a time ceiling.

Raising from `complete` is a deliberate choice in that example, not a requirement: every fetch reaches `acomplete`, so a client embedded in async code need not implement a synchronous path that would never run. Implement both if you intend to drive the client yourself.

### What no check can prove

The shape check confirms your client can be called. It cannot confirm that a client declaring `Capability.COST` actually stops at a cost ceiling, or that one declaring `Capability.ESTIMATE` keeps within the attempt allowance. Those remain your responsibility, which is why declaring them takes a method rather than an attribute that happens to match a name.

### Swapping the provider

`DefaultLlmClient` reaches a provider through `litellm`, imported lazily. There is no provider-abstraction layer beneath it, so "use a different provider" and "inject a client" are the same task: write the client above around whatever SDK you want, and construct the source with it.

Reuse what you can. `blue_data_llm_source.llm.pricing.bound_cost(prompt, model, max_tokens, properties)` gives a true upper bound on one call against the rates it holds, which is what `bound_for_attempt` needs, and it raises `ConfigError` rather than inventing a figure when no bound can be established.

## `llm_fn`: the one-function form

For a test, a stub, or a model you already have a function for, a plain callable is enough. The library wraps it in a `FunctionClient`.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "the member states of the European Union",
    attrs=["country"],
    llm_fn=lambda prompt: '[{"country": "France"}]',
)
print(result.records, result.meta.accounting_known)
```

The callable may also be `async def`, or return a coroutine; the result is awaited.

Two things follow from what a wrapper around your function can prove. It declares `Capability.ROWS` alone, so `limits.max_calls`, `limits.max_cost`, and `limits.max_seconds` on a run supplying `llm_fn` are **refused**. And the result sets `meta.accounting_known` to `False`, so its `calls`, `cost`, and token counts describe this library's bookkeeping rather than provider work: a callable that fans out to five providers and spends real money still reports one call and no cost, because none of that is visible from here.

The library therefore reports this accounting as unknown. To enforce call, cost, or time ceilings over your own model call, implement the client protocol above, including `budget_capabilities()`.

`llm_fn` is a parameter of `fetch`, `afetch`, `estimate`, and `execute_query_nl`. It is **not** a property: a key named `llm_fn` in `properties` or in `execute_query`'s `optional_properties` raises `ConfigError` before a client is resolved because the property map is closed. `execute_query` has no `llm_fn` parameter at all, so inject at construction for that surface.

## Cache backends

Two stores ship, and the protocol is two methods.

```python
from typing import Any

from blue_data_llm_source import CacheStore, LlmSource


class CountingCache:
    """Any object with get and set is a cache store. This one counts its reads."""

    def __init__(self) -> None:
        self.reads = 0
        self._data: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        self.reads += 1
        return self._data.get(key)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value


cache = CountingCache()
print(isinstance(cache, CacheStore))
source = LlmSource(properties={"cache_store": cache})
print(source.fetch("airports", attrs=["name"], llm_fn=lambda p: '[{"name": "Orly"}]').records)
```

The client that reaches a provider drives the store with `get` and `set` alone. Do not assume `in` or `[]` will be used. They will not, so a store written to the published interface is enough.

The built-in client checks that a supplied `cache_store` has callable, synchronous `get(key)` and `set(key, value)` methods before provider contact. A missing, noncallable, or declared async method raises `ConfigError` naming that contract. A custom client must validate any store it uses itself.

| Rule | Why |
|---|---|
| A read that fails must be handled by your `get` method | Both included stores treat read failures as misses; a custom store that raises from `get` fails the fetch |
| A write that fails is skipped, not raised | The same reasoning on the other side |
| The key is a long serialized mapping | It covers the model, the prompt, and every forwarded parameter. It is not path-safe; `FileCache` hashes it before using it as a filename |
| Keep `get` fast | `CacheStore` is synchronous, and a store that reaches a network sits outside the time ceiling entirely. This is listed in [what the time ceiling bounds](../lib/README.md#what-the-time-ceiling-actually-bounds) |
| Values are JSON-shaped provider payloads | Whatever the client stored comes back to the client. Keep it serializable if your store persists |

The `cache_store` property takes precedence over the separate `llm.cache` switch. That switch opens a `diskcache` backend and does not use either included store; the two cache paths are independent.

Before implementing another cache, review `MemoryCache` and `FileCache`. `cache/memory.py` contains a locked LRU implementation; `cache/file.py` uses atomic replacement and tracks the files its instance wrote so `clear()` leaves unrelated files intact.

**A cache backend cannot be wired through an `llm_fn`.** The client is what consults the cache, and `FunctionClient` does not. The example above accepts the store and never reads it. The same is true of an injected client unless it reads `cache_store` itself; `llm.cache` is also built-in-client only. To cache your own model calls, consult the store inside the client you inject.

This offline client reads the passed store and counts its own hits:

```python
from blue_data_llm_source import Capability, LlmSource, MemoryCache


class CachedClient:
    def __init__(self):
        self.hits = 0

    def budget_capabilities(self):
        return frozenset({Capability.ROWS})

    def complete(self, prompt, properties=None):
        raise NotImplementedError("fetch calls acomplete")

    async def acomplete(self, prompt, properties=None):
        store = properties["cache_store"]
        cached = store.get(prompt)
        if cached is not None:
            self.hits += 1
            return cached
        answer = '[{"name": "Orly"}]'
        store.set(prompt, answer)
        return answer


client = CachedClient()
source = LlmSource(client=client, properties={"cache_store": MemoryCache()})
source.fetch("airports", attrs=["name"])
source.fetch("airports", attrs=["name"])
print(client.hits)  # 1
```

## Your own price table

The published rates are a static snapshot with no effective date. To bound a cost ceiling against rates you maintain, supply both:

```python
from blue_data_llm_source import LlmSource

source = LlmSource(properties={
    "llm.model": "a-model-nobody-here-prices",
    "llm.max_tokens": 512,
    "llm.price_per_input_token": 0.0000004,
    "llm.price_per_output_token": 0.0000016,
})
sized = source.estimate("commercial airports in France", attrs=["name"])
print(sized.bounded, round(sized.max_cost, 6), sized.model)
```

Presence decides that you meant to override, not validity: supplying one key commits you to both. When a cost bound is needed, a value that is not a finite positive number raises `ConfigError` instead of falling back to the table. Without a requested bound, `estimate` can report `bounded=False` with a reason. Falling back would accept an explicit instruction and then ignore it, which against a stale table produces a bound below the one you asked for.

Rates for a model the table does not carry are used as given, so they must already cover every tier it can be billed at: cache writes, a long-context tier, regional processing. Rates supplied for a model the table does carry are read as its base rates. The table's cache-write ratio, long-context tier and regional uplift still apply to them, and the bound never falls below the table's own, so supplying a model's published base rates cannot drop a charge it is known to carry.

Both keys are consumed locally and never forwarded to the provider.

Under a cost ceiling, a call priced at your rates keeps its whole reservation, or the provider's reported figure where that is higher, and reports its accounting as unknown, because nothing in the response proves the charge against rates this package did not publish.

`rates_for(model)` returns `None` for a model the table does not carry, `priceable_models()` lists the ones it does, and `can_bound(model, max_tokens, properties)` answers whether a ceiling could be claimed (without raising), so you can check before configuring one.

## Identity partition rules

When a request is split, something has to decide along which dimension and into which values. By default the model proposes it. A rule says the domain is yours: a finite list of values the model may estimate over but not alter.

This reaches a direct Python caller only. Neither the command-line interface nor the MCP server exposes it.

```python
import json

from blue_data_llm_source import LlmSource

AIRPORTS = {
    "Île-de-France": [{"name": "Charles de Gaulle", "region": "Île-de-France"},
                      {"name": "Orly", "region": "Île-de-France"}],
    "Provence": [{"name": "Marseille Provence", "region": "Provence"}],
}

RULE = {
    "dimension": "region",
    "ordered_primary_keys": ["region", "name"],
    "kind": "finite_domain",
    "allowed_domain": ["Île-de-France", "Provence"],
    "parent_scope": {},
    "provenance": "caller_declared",
    "contract_version": "identity-partition-rule-1",
}


def answer(prompt: str) -> str:
    """Stand in for the model at each stage, so this example runs offline."""
    if prompt.startswith("Your task is to prepare a data request"):
        return json.dumps({"ask": "commercial airports in France", "constraints": {}})
    if prompt.startswith("Resolve the output schema"):
        return json.dumps({"attrs": [{"name": "name"}, {"name": "region"}],
                           "primary_keys": ["name"]})
    if prompt.startswith("Estimate the number of records"):
        return json.dumps({"expected": 3, "lower": 2, "upper": 8, "row_unit": "airports"})
    if prompt.startswith("Estimate the distribution of records"):
        return json.dumps({
            "key": "region",
            "partitions": [
                {"value": "Île-de-France", "estimate": {"expected": 2, "lower": 1, "upper": 4}},
                {"value": "Provence", "estimate": {"expected": 1, "lower": 1, "upper": 2}},
            ],
            "ambiguity": 0.1,
            "reasoning": "two declared regions",
        })
    for region, records in AIRPORTS.items():
        if region in prompt:
            return json.dumps(records)
    return "[]"


result = LlmSource().fetch(
    "commercial airports in France",
    attrs=["name", "region"],
    llm_fn=answer,
    properties={
        "plan.mode": "partitioned",
        "plan.keys": ["region", "name"],
        "plan.distribution_search": True,
        "plan.identity_partitions": [RULE],
        "plan.max_depth": 1,
    },
)
print([record["name"] for record in result.records])
print(result.meta.outcome, result.meta.calls, result.meta.gaps)

receipt = result.planning_receipt
payload = receipt.to_dict()
print(payload["contract_version"], payload["planning_facts"]["selected_partition_key"])
print(payload["planning_facts"]["partition_safety_status"])
```

### What a rule must satisfy

| Field | Requirement |
|---|---|
| `dimension` | One of `plan.keys` |
| `ordered_primary_keys` | Exactly `plan.keys`, in order |
| `kind` | `"finite_domain"`, the only kind supported |
| `allowed_domain` | Non-empty JSON scalars, with no duplicates after normalization |
| `parent_scope` | The constraints under which this rule applies. `{}` makes it root-applicable, and at least one rule must be |
| `provenance` | `"caller_declared"` for a rule passed with the call, `"source_configuration"` for one set on the source |
| `contract_version` | `"identity-partition-rule-1"` |

Per-call rules **replace** the source's list; they do not add to it. Rules may not come from an injected client's own properties. Every rule is validated before the provider is contacted, so a malformed one costs nothing, and all three distribution-search settings are removed from the property map handed to the client.

`plan.distribution_search` additionally requires `plan.mode="partitioned"`, explicit ordered `plan.keys`, and `plan.max_strategy_candidates` of at least 1.

### The safety check and the receipt

A split proposed under a rule is accepted only after `PartitionSafety` establishes containment, pairwise disjointness, and coverage against the declared domain. A proposal that fails leaves the node unsplit rather than producing children that overlap or miss values.

An accepted run may carry a `PlanningReceipt`, reachable as `result.planning_receipt` and serializable with `to_dict()`. It binds the configured rule digests, the planning facts, and the versioned plan, and it checks that the three agree: that every applied rule was configured, that the counts match, and that the last applied decision is the one the facts name.

**It proves internal agreement and nothing else.** It does not establish rule authenticity, factual truth, completeness, or answer quality. Read it as a record of what the planner did, not as evidence that what it did was right.

An ordinary result carries no receipt, and `FetchResult.to_dict()` keeps its two keys.

## A budget you own

`Budget` is a public class, not only an internal one. Construct one, hand it to every call that should share a total, and read it afterwards.

```python
from blue_data_llm_source import Budget, Limits, LlmSource

source = LlmSource()
run = Budget(Limits(max_rows=3))
answer = lambda prompt: '[{"name": "Orly"}]'

for question in ("airports in France", "airports in Spain", "airports in Italy"):
    result = source.fetch(question, attrs=["name"], llm_fn=answer, budget=run)
    print(question, len(result), result.meta.outcome)

print(run.rows, run.calls, run.committed, run.stopped_by)
print(run.remaining_calls(), run.remaining_cost(), run.configured_axes())
```

Each fetch calls `derive` on it, producing a child whose effective ceiling on every axis is the smaller of its own and what the parent has left, retry depth included. Elapsed time is measured from the parent's start, so a time ceiling covers the whole unit of work rather than restarting with each fetch.

The observation methods narrow along the chain, so what you read on a derived budget is what it can actually spend, not what it was nominally given. `configured_axes()` walks the chain the other way, reporting which axes carry a ceiling anywhere. That is what the capability refusal reads, which is why a ceiling on your budget alone is still checked against the client that answers.

`check_deadline()` and `rows_exhausted()` are available to callers that drive a budget directly. Library fetch paths recheck the deadline inside `reserve` instead.

## The stage parsers

The three planning-stage parsers are pure: each takes a model's text and returns a value, and none of them calls a model. They are exported so you can reuse them: to validate a recorded response, to build a fixture, or to drive a stage yourself.

```python
import json

from blue_data_llm_source import CardinalityEstimator, QueryAnalyzer

analysis = QueryAnalyzer.parse(
    json.dumps({"ask": "commercial airports in France", "constraints": {"country": "France"}}),
    asked="airports in France",
)
print(analysis.ask, dict(analysis.constraints), analysis.rewritten)

estimate = CardinalityEstimator.parse(
    json.dumps({"expected": 40, "lower": 30, "upper": 60, "row_unit": "airports"}),
    row_unit="airports",
)
print(estimate.expected, estimate.lower, estimate.upper, estimate.parse_status)

unusable = CardinalityEstimator.parse("not json at all", row_unit="airports")
print(unusable.parse_status, unusable.expected, unusable.diagnostics)
```

A parser that cannot read its input returns a value saying so instead of raising, which is what lets a planning stage fall back to the caller's declaration and record a diagnostic instead of failing the request.

`SchemaGenerator.parse(text, declared, *, mode)` is the third. Its `declared` argument is a `Schema`, which is reachable as `blue_data_llm_source.planning.schema.Schema` but is not exported from the package root. Import it from that module if you need to call this parser directly.

## Embedding the source in an application

`LlmSource.afetch` returns a `FetchResult`. Call `result.to_dict()` when an application needs a dictionary for serialization; the result and metadata objects themselves are dataclasses. This dictionary contains the original record values and metadata. If an application publishes diagnostics, `meta_view(result.meta)` applies the metadata masking used by the CLI and MCP adapters. It leaves records unchanged, so the application must decide which record fields it can disclose.

Set operator limits on the source or on a shared `Budget` when callers can supply request properties. A limit supplied only with one request controls that request; it does not establish an application-wide ceiling. See [a budget you own](#a-budget-you-own) for a shared-budget example.

## Where to go next

- [api-reference.md](api-reference.md): the exact signature of everything above.
- [architecture.md](architecture.md): where each seam sits in the whole.
- [`lib/COMPATIBILITY.md`](../lib/COMPATIBILITY.md): the client contract stated once more, alongside what changed for a caller being ported.
- [`design/04-detailed-architecture.md`](../design/04-detailed-architecture.md): the reasoning behind the client, capability, and budget design.
