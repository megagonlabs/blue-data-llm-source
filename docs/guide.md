# Developer Guide

This guide covers the main library tasks: asking a question, shaping the answer, setting cost bounds, splitting a large request, and evaluating the result metadata.

Every example here runs as shown. Most supply an `llm_fn`, a plain `(prompt) -> str` callable used as the model for that call, so they execute offline and for free. The two examples that would reach a provider say so in a line above the block.

## Setup

Install the library, then confirm it imports.

```bash
lib/scripts/install.sh
python -c "import blue_data_llm_source; print(blue_data_llm_source.__version__)"
```

[installation.md](installation.md) covers the extras, the platform packages, and the `blue-data` prerequisite.

## Your first fetch

`LlmSource()` needs no arguments. `fetch` takes the question, the attributes you want back, and the callable that answers.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "the member states of the European Union",
    attrs=["country"],
    llm_fn=lambda prompt: '[{"country": "France"}, {"country": "Germany"}]',
)
for record in result.records:
    print(record)
print(result.meta.outcome)
```

`FetchResult` supports `len()` and iteration over its records, so `len(result)` and `for record in result` both work. The `result.is_empty` property reports whether the record list is empty.

`attrs` accepts a list of names, a list of `Attr` objects, or a list of `{"name": ..., "type": ...}` dicts. `context` is free text that clarifies what an attribute name means. Put "airport code in the IATA three-letter form" there rather than in the question.

## Asking for typed attributes

Declare a type and the value is coerced where the coercion is unambiguous. A value that will not coerce is kept as it arrived and reported, because dropping a record over a formatting mismatch loses data you asked for.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "the busiest airports in Paris",
    attrs=[
        {"name": "name", "type": "string"},
        {"name": "iata_code", "type": "string"},
        {"name": "passengers", "type": "int"},
    ],
    llm_fn=lambda prompt: (
        '[{"name": "Charles de Gaulle", "iata_code": "CDG", "passengers": "70,000,000"},'
        ' {"name": "Orly", "iata_code": "ORY", "passengers": "not reported"}]'
    ),
)
print(result.records)
print(result.meta.type_mismatches)
```

`int`, `integer`, `float`, `number`, `bool`, `boolean`, and `date` are the names this library checks. Any other name is still sent to the model as an instruction (which is legitimate) and is reported once in `meta.unchecked_types`, so a misspelled type cannot look like a check that is happening.

Only a genuine thousands grouping is stripped from a number, so a decimal comma is not silently turned into a hundredfold error.

## Reading the result metadata

`result.meta` is where the library says what happened. Read four things before you use the records.

| Field | Read it for |
|---|---|
| `outcome` | Whether this is a complete answer, an empty one with a reason, a truncated one, or a partial one |
| `stopped_by` | Which ceiling ended the run, when one did |
| `gaps` | Operational diagnostics that may explain missing rows |
| `unmapped_keys`, `key_collisions`, `type_mismatches` | Whether the records are shaped the way you asked |

```python
from blue_data_llm_source import LlmSource, meta_view

source = LlmSource()
result = source.fetch(
    "the busiest airports in Paris",
    attrs=["name", "iata_code"],
    llm_fn=lambda prompt: '[{"Name": "Charles de Gaulle", "IATA": "CDG"}]',
)
print(result.meta.outcome, result.meta.stopped_by, result.meta.gaps)
print(result.meta.remapped_keys, result.meta.unmapped_keys)
print(meta_view(result.meta, verbose=True)["unmapped_keys"])
```

`Name` reached `name` because the match ignores case, and that remapping is reported. `IATA` reached nothing (`iata` and `iatacode` are different normalised forms), so it is reported unmapped rather than assigned by guess.

`meta_view` is the single JSON-ready serialization every adapter publishes. Use it instead of writing your own, or two consumers of the same run will describe it differently.

**`gaps` being empty means no supported diagnostic fired.** It is not a completeness claim. Nothing in a short answer says it is short, so this library does not pretend to detect one.

## Bounding one request

A ceiling passed with the call binds that call. It is merged over the instance's properties for the duration of the request and is not written back.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
answer = lambda prompt: '[{"name": "Charles de Gaulle"}, {"name": "Orly"}]'

bounded = source.fetch("airports in France", attrs=["name"], llm_fn=answer,
                       properties={"limits.max_rows": 1})
unbounded = source.fetch("airports in France", attrs=["name"], llm_fn=answer)
print(len(bounded), bounded.meta.outcome, bounded.meta.truncated_to)
print(len(unbounded), unbounded.meta.outcome)
```

`max_records` is the same row ceiling as a positional argument, and it replaces `limits.max_rows` for that call.

Four axes exist. Which of them bind depends on the client that answers. See [Which ceilings bind](#which-ceilings-bind) below.

| Key | Effect |
|---|---|
| `limits.max_rows` | Truncates the result and sets `meta.truncated_to` |
| `limits.max_seconds` | Refuses an attempt once elapsed time reaches it, and cancels a wait in flight |
| `limits.max_cost` | Refuses an attempt whose cost bound would cross it |
| `limits.max_calls` | Refuses an attempt past that many, counting every retry |
| `limits.max_retries` | Retries per call; the client makes this many attempts plus one |

The names are a closed set. `limits.max_cst` raises `ConfigError` listing what is accepted, and so does `limit.max_rows` or a bare `limits`. Every value is checked as the ceiling is built: a wrong type, a `bool`, a negative number, or a `NaN` is refused rather than accepted while enforcing nothing.

## Bounding a sequence with one total

Configure a ceiling on the source and it applies to every fetch separately. To bound a whole unit of work with one total, build a `Budget` and hand it to each call.

```python
from blue_data_llm_source import Budget, Limits, LlmSource

source = LlmSource()
run = Budget(Limits(max_rows=2))
answer = lambda prompt: '[{"name": "Charles de Gaulle"}]'

first = source.fetch("airports in France", attrs=["name"], llm_fn=answer, budget=run)
second = source.fetch("airports in Spain", attrs=["name"], llm_fn=answer, budget=run)
third = source.fetch("airports in Italy", attrs=["name"], llm_fn=answer, budget=run)
print(len(first), len(second), len(third))
print(third.meta.outcome, third.meta.stopped_by, run.rows, run.committed)
```

Each fetch derives a child from that budget, so the effective ceiling on every axis is the smaller of the child's and whatever the parent has left, retry depth included. The third fetch above comes back empty with `stopped_by` naming `max_rows` rather than raising. A child does not inherit its parent's `stopped_by`, so a later affordable request under the same budget does not report an earlier refusal as its own.

A budget starts its clock when you create it, which matters for `max_seconds`: the ceiling covers the whole unit of work and does not restart with each fetch.

## Estimating before you spend

`estimate` contacts nothing. It builds the prompt this source would actually send and bounds it against the model's published rates.

```python
from blue_data_llm_source import LlmSource

source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 512})
sized = source.estimate("commercial airports in France", attrs=["name", "iata_code"])
print(sized.bounded, round(sized.max_cost, 6))
print(sized.model, sized.prompt_bytes, sized.max_output_tokens)
print(sized.max_attempts, round(sized.max_cost_per_attempt, 6))
```

`max_cost` bounds the whole fetch, not one try: it is the per-attempt figure accumulated once per attempt the fetch is permitted, added the way the budget ledger adds it. Size a ceiling against `max_cost`, not against `max_cost_per_attempt`, or one transient failure consumes the ceiling and the retry is refused.

**The minimum cost bound is the full output allowance.** The prompt contributes relatively little; `llm.max_tokens` at the model's output rate contributes the rest. With the default `gpt-4o` model and 4096 output tokens, the minimum is about $0.041. A lower `limits.max_cost` refuses the call before provider contact. Reduce `llm.max_tokens` when the answer needs fewer tokens.

An unpriced model is answered, not raised on:

```python
from blue_data_llm_source import LlmSource, can_bound, priceable_models

print(priceable_models())
print(can_bound("gpt-4o", 4096), can_bound("a-model-nobody-here-prices", 4096))
unpriced = LlmSource().estimate("anything", properties={"llm.model": "a-model-nobody-here-prices"})
print(unpriced.bounded, unpriced.reason)
```

Supply `llm.price_per_input_token` and `llm.price_per_output_token` together to bound a model the table does not carry. The table is a static snapshot of published list prices with no effective date and no freshness check, so treat `limits.max_cost` as a control against known rates rather than as a hard billing stop.

`estimate` reads no `plan.*` setting. It describes a flat fetch; a decomposed request costs this figure times the calls it makes.

## Choosing a model and provider settings

Provider settings are `llm.*` keys in `properties`. The accepted set is closed, so a typo raises instead of being ignored.

**The block below reaches a real provider and spends money.** It needs the `llm` extra installed and a provider credential in the environment.

```python
from blue_data_llm_source import LlmSource

source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 512})
result = source.fetch("the member states of the European Union", attrs=["country"])
print(len(result), result.meta.model, result.meta.calls, result.meta.cost)
```

| Key | Meaning |
|---|---|
| `llm.model` | Model name. Defaults to `gpt-4o` |
| `llm.temperature`, `llm.max_tokens`, `llm.stream`, `llm.timeout` | Forwarded to the provider |
| `llm.cache`, `llm.cache_dir` | Enable and locate the on-disk response cache |
| `llm.max_parallel` | Concurrency bound per event loop, default 8 |
| `llm.price_per_input_token`, `llm.price_per_output_token` | Rates for a model the price table does not carry |

The last four are consumed here and never sent to the provider. `llm.rate_limit` is refused by name with a `ConfigError`: client-side call spacing has to reconcile with per-attempt budget admission, and this library does not do it. Cap concurrency with `llm.max_parallel`, or throttle above this library where the whole request sequence is visible.

`llm.stream=True` is accepted and **does not make anything faster**: a fetch parses one whole JSON document, so the chunks are read to the end and reassembled before anything downstream sees them. Set it only because a provider configuration or proxy requires it. One thing changes: a stream carries token counts only if the provider chooses to send them, and when none arrive the call cannot be priced, so the reservation stays at its bound and the result reports `accounting_known=False`.

This library never reads a credential, so it cannot report one missing. An absent or wrong key arrives as a `TransportError` from the provider SDK.

## Caching responses

Two stores ship. Hand one to a run through the `cache_store` property, and the client that reaches the provider consults it with `get` and `set` alone.

**The block below reaches a real provider on the first call and answers the second from the cache.**

```python
from blue_data_llm_source import LlmSource, MemoryCache

source = LlmSource(properties={"cache_store": MemoryCache()})
first = source.fetch("the member states of the European Union", attrs=["country"])
second = source.fetch("the member states of the European Union", attrs=["country"])
print(first.meta.cache_hits, second.meta.cache_hits, second.meta.cost)
```

A cache hit consumes no provider call, no tokens, and no cost, and its usage record marks it. Rows it returns still count against a row ceiling, and admission still checks elapsed time, which is what keeps those ceilings binding across a sequence.

`MemoryCache(max_entries=512)` is in-process and evicts least-recently-used entries. `FileCache(directory=".llm_cache")` writes one JSON file per entry; its `clear()` removes only entries it wrote, so pointing it at a directory that already holds JSON cannot delete that JSON. Anything with `get` and `set` works (see [extending.md](extending.md#cache-backends)).

The separate `llm.cache` switch opens a `diskcache` backend instead and does not use either store. Treat the two paths as unconnected.

## Splitting a large request

`plan.mode` defaults to `flat`: one question, one call. `partitioned` splits along a key the planning stages find and merges the parts on the way back.

```python
import json

from blue_data_llm_source import LlmSource

AIRPORTS = {
    "Île-de-France": [{"name": "Charles de Gaulle", "region": "Île-de-France"},
                      {"name": "Orly", "region": "Île-de-France"}],
    "Provence": [{"name": "Marseille Provence", "region": "Provence"}],
}


def answer(prompt: str) -> str:
    """Stand in for the model. Each branch is one prompt a real run would send."""
    if prompt.startswith("Your task is to prepare a data request"):
        return json.dumps({"ask": "commercial airports in France", "constraints": {}})
    if prompt.startswith("Resolve the output schema"):
        return json.dumps({"attrs": [{"name": "name"}, {"name": "region"}],
                           "primary_keys": ["name"]})
    if prompt.startswith("Estimate the number of records"):
        return json.dumps({"expected": 3, "lower": 2, "upper": 8, "row_unit": "airports"})
    if prompt.startswith("Propose one useful partition"):
        return json.dumps({"key": "region", "values": list(AIRPORTS),
                           "ambiguity": 0.1, "child_estimates": [2, 1]})
    for region, records in AIRPORTS.items():
        if region in prompt:
            return json.dumps(records)
    return "[]"


result = LlmSource().fetch(
    "commercial airports in France",
    attrs=["name", "region"],
    llm_fn=answer,
    properties={"plan.mode": "partitioned", "plan.key": "name",
                "plan.partition_key": "region", "plan.max_depth": 1},
)
for record in result.records:
    print(record)
print(result.meta.calls, result.meta.row_estimate, result.meta.record_origins)
print(result.meta.gaps)
```

Six calls: three planning stages, one partition proposal, two leaves. A real run sends each of those prompts to a provider, so a split request costs several times what the same request costs flat. Size it with the part count in hand, or ask in `flat` mode and read [Estimating before you spend](#estimating-before-you-spend).

The settings worth knowing first:

| Property | Default | Meaning |
|---|---|---|
| `plan.mode` | `flat` | `flat`, `partitioned`, or `row_by_row` |
| `plan.key` | none | The attribute identifying a record, so parts returning the same record merge |
| `plan.keys` | empty | Ordered fields that jointly identify one record, when one attribute is not enough |
| `plan.partition_key` | none | Preferred root split dimension. Descendants may choose another and cannot repeat an ancestor's |
| `plan.max_depth` | `2` | Deepest partition level. `0` keeps the planning stages and disables subdivision |
| `plan.max_partitions` | `4` | Most children one proposal may create |
| `plan.max_leaves` | `24` | Most terminal slices. A plan that cannot fit is rejected whole, not run as a prefix |
| `plan.planning_order` | `planning.schema_first` | Which fact schedule runs, and how many calls it may make |

The full set is in [api-reference.md](api-reference.md#plan-properties).

A planning stage whose response does not parse does not fail the request. It falls back to what you declared, records a diagnostic in `meta.gaps`, and the run continues, so the failure mode of a decomposed request is a flat one with gaps rather than an exception.

`meta.asked` is worth checking on a non-flat run. The analysis stage restates the request for retrieval, and that restated text becomes the query every part runs. The rewrite is what makes "I don't know how many countries are there?" answerable at all, and it is also what could broaden or narrow the question. The change is reported rather than prevented: when `meta.asked` is not `None`, that is the question the records answer.

## Asking again for what did not come back

A model asked for two hundred records returns forty, stops at a plausible place, and reports nothing wrong. `plan.max_continuations` lets a part ask again, naming the records it already holds.

```python
import json

from blue_data_llm_source import LlmSource

PAGES = [
    [{"name": "Charles de Gaulle"}, {"name": "Orly"}],
    [{"name": "Marseille Provence"}],
]


def answer(prompt: str) -> str:
    """Stand in for the model, returning a fresh page each time it is asked again."""
    if prompt.startswith("Your task is to prepare a data request"):
        return json.dumps({"ask": "commercial airports in France", "constraints": {}})
    if prompt.startswith("Resolve the output schema"):
        return json.dumps({"attrs": [{"name": "name"}], "primary_keys": ["name"]})
    if prompt.startswith("Estimate the number of records"):
        return json.dumps({"expected": 3, "lower": 2, "upper": 8, "row_unit": "airports"})
    return json.dumps(PAGES[1 if "Already returned:" in prompt else 0])


result = LlmSource().fetch(
    "commercial airports in France",
    attrs=["name"],
    llm_fn=answer,
    properties={"plan.mode": "partitioned", "plan.key": "name", "plan.max_depth": 0,
                "plan.max_continuations": 1},
)
print([record["name"] for record in result.records])
print(result.meta.calls, result.meta.stopped_by)
for gap in result.meta.gaps:
    print(gap)
```

The continuation prompt names the records the leaf already holds, which is the `Already returned:` line the stand-in above keys off. `plan.max_depth=0` keeps the planning stages and disables subdivision, so this is one leaf asking twice rather than a split.

At the default of `0` each part asks once. Every continuation is another billed call, which is why it is off by default.

When the cap ends a run that was still producing records, `meta.stopped_by` reports `plan.max_continuations`. Read that as "completeness was not established", not as "more records exist": the last permitted response looks identical whether the model had one row left or a thousand.

`plan.max_excluded_keys` and `plan.max_exclusion_bytes` bound how much of the already-held list a continuation prompt may carry. An uncapped list eventually becomes the request.

## Using it through the data layer

`LlmSource` extends the shared `DataSource` base class, so a registry can hold it like any other source.

```python
from blue_data_llm_source import LlmSource

source = LlmSource(name="knowledge")
print(source.protocol, source.variant)
print(source.fetch_databases(), source.fetch_database_collections("llm"))
print(source.execute_query({"op": "LLM.MODELS"}))
print(source.execute_query({"op": "LLM.ESTIMATE", "query": "airports in France",
                            "attrs": ["name"]})[0]["bounded"])
```

Build the source and hand the instance to `register_source`. Inheriting the base class does not make the source reachable by protocol name: the registry records the class alongside `name` and `properties`, and `connect_source` returns the live instance while it is present and reconstructs an equivalent one from that record afterwards.

`execute_query` accepts a natural-language string or a dict carrying an `op` key, one of `LLM.FETCH`, `LLM.MODELS`, or `LLM.ESTIMATE`. Both `execute_query` and `execute_query_nl` return a plain `list[dict]`, because that is what the shared surface returns. Call `fetch` when you also need the outcome, the accounting, or the diagnostics.

A per-request ceiling goes in `optional_properties`:

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
print(source.execute_query_nl(
    "commercial airports in France",
    llm_fn=lambda prompt: '[{"name": "Charles de Gaulle"}, {"name": "Orly"}]',
    optional_properties={"attrs": ["name"], "limits.max_rows": 1},
))
```

**`optional_properties` cannot inject a client.** `execute_query_nl` takes `llm_fn` as a parameter, but `execute_query` does not. Placing `llm_fn` in `optional_properties` raises `ConfigError` before provider contact because the property map is closed. To use a custom model with `execute_query`, inject the client at construction:

```python
import asyncio
from typing import Any

from blue_data_llm_source import Capability, LlmSource


class ScriptedClient:
    def budget_capabilities(self) -> frozenset[str]:
        return frozenset({Capability.ROWS})

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return asyncio.run(self.acomplete(prompt, properties))

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return '[{"name": "Charles de Gaulle"}, {"name": "Orly"}]'


source = LlmSource(client=ScriptedClient())
print(source.execute_query("commercial airports in France",
                           optional_properties={"attrs": ["name"], "limits.max_rows": 1}))
```

The orchestrator tool surface works the same way. `get_nl_tools` advertises the four tools this source can answer, `execute_nl_tool` runs one by name, and `execute_query_nl_tools` is refused with a `ConfigError` naming the bounded path: it drives a loop of up to ten provider calls that this source's ceilings never see.

```python
from blue_data_llm_source import ConfigError, LlmSource

source = LlmSource()
print([tool["name"] for tool in source.get_nl_tools()])
print(source.execute_nl_tool("get_schema", {"database": "llm", "collection": "public"}))
try:
    source.execute_nl_tool("get_sample", {"database": "llm"})
except ConfigError as exc:
    print(exc)
```

## Driving it from async code

`afetch` is the real entry point and `fetch` wraps it. Calling `fetch` from inside a running event loop raises rather than deadlocking.

```python
import asyncio

from blue_data_llm_source import ConfigError, LlmSource


async def main() -> None:
    source = LlmSource()
    answer = lambda prompt: '[{"name": "Orly"}]'
    result = await source.afetch("airports in France", attrs=["name"], llm_fn=answer)
    print(result.records)
    try:
        source.fetch("airports in France", attrs=["name"], llm_fn=answer)
    except ConfigError as exc:
        print(exc)


asyncio.run(main())
```

`llm.max_parallel` bounds how many provider calls one event loop makes at once, which is what a fan-out of leaves runs under.

## Which ceilings bind

A ceiling binds only on an axis the answering client declares, and a configured ceiling on an axis it does not declare raises `ConfigError` at the first fetch. Reporting a ceiling that is not holding anything is worse than declining to hold it, because you would believe it.

```python
from blue_data_llm_source import ConfigError, LlmSource

source = LlmSource()
try:
    source.fetch("airports", llm_fn=lambda prompt: "[]", properties={"limits.max_cost": 1.0})
except ConfigError as exc:
    print(exc)
```

The practical case is `llm_fn`. The wrapper around your callable can prove it invoked the callable once and nothing more. It cannot know whether that callable contacts a provider, how many times, or what it is billed, so it declares rows alone, and `limits.max_calls`, `limits.max_cost`, and `limits.max_seconds` are refused there.

For the same reason **a result produced by an `llm_fn` sets `meta.accounting_known` to false**. When it is false, `calls`, `cost`, and the token counts describe this library's own bookkeeping and nothing else: a `cost` of `0.0` means "not measured", not "free". Read `accounting_known` before reading `cost`.

To hold a cost, call, or time ceiling over your own model call, write a client that declares those axes. [extending.md](extending.md#the-injected-client) has the contract.

`limits.max_rows` binds any client, because this source counts the records itself.

## Handling errors

Nothing escapes `LlmSourceError` from `fetch`, `afetch`, or `estimate`. Anything unexpected is wrapped with the original attached as `__cause__`, so `except LlmSourceError` is sufficient and the original type is still there for whoever debugs it. `KeyboardInterrupt` and cancellation are deliberately not caught.

```python
from blue_data_llm_source import LlmSourceError, LlmSource, ParseError

source = LlmSource()
try:
    source.fetch("airports", attrs=["name"], llm_fn=lambda prompt: "not json at all")
except ParseError as exc:
    print(type(exc).__name__, exc.raw)
except LlmSourceError as exc:
    print(type(exc).__name__, exc)
```

| Exception | Raised for |
|---|---|
| `ConfigError` | Rejected configuration: an unknown override, an invalid or unknown ceiling, a ceiling the client cannot hold, a cost bound that cannot be established, a missing optional extra |
| `QueryError` | A malformed query or attribute list, including the same attribute requested twice |
| `TransportError` | A provider failure or a malformed provider response, including a missing or wrong credential |
| `ParseError` | A response that cannot be read as records. Carries the raw text as `.raw`, because re-running is not guaranteed to reproduce it |
| `BudgetExceeded` | A limit that would be crossed. Public for callers driving a `Budget` directly |

`BudgetExceeded` raised *inside* a fetch does not reach you. A refusal before any extraction answered returns no records with `outcome="stopped_by_limit"`; a refusal after one answered retains those records with `outcome="partial"`. In both cases the stop fields name the axis.

## What to check before trusting the records

Results are model-generated. This library verifies shape, never truth.

1. **`meta.outcome`.** `ok` means no detected execution failure, not a complete answer.
2. **`meta.stopped_by` and `meta.stop_reasons`.** A ceiling that fired is the commonest reason an answer is short.
3. **`meta.gaps`.** A failed part, a continuation cap, a response cut off at the model's output limit, a plan refused for exceeding `plan.max_leaves`.
4. **`meta.unmapped_keys`, `meta.key_collisions`, `meta.type_mismatches`, `meta.unchecked_types`.** Whether the records are shaped the way you asked.
5. **`meta.accounting_known`.** Whether the cost figure is a measurement.
6. **`None` values.** A requested attribute the model omitted and an explicit JSON `null` both arrive as `None`, and the result does not distinguish them. An empty `type_mismatches` means "nothing present failed its type", not "every field is populated". Check for `None` yourself when a field is required.

`meta.row_estimate` is a model-generated planning hint, not an expected row count and not a recall measure. It is `None` in `flat` mode and whenever no estimate was accepted.

## Next steps

- [api-reference.md](api-reference.md): every signature, parameter, and return.
- [extending.md](extending.md): write your own client, cache, or partition rule.
- [architecture.md](architecture.md): how the pieces fit, and what is deliberately absent.
- [`lib/COMPATIBILITY.md`](../lib/COMPATIBILITY.md): read this before porting a caller; it records what this library does differently from what the caller was written against.
