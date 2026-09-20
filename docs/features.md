# Features

This page gives a short, runnable example for each major library feature.

Every example runs offline by supplying an `llm_fn`, a plain `(prompt) -> str` callable used as the model for that call. Omit `llm_fn` to use a configured provider. Review [the provider settings](guide.md#choosing-a-model-and-provider-settings) first because provider calls may incur charges.

## Structured records from a model's own knowledge

The library asks a model a natural-language question and returns the answer as a list of dictionaries. It does not query a backing store.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "the member states of the European Union",
    attrs=["country"],
    llm_fn=lambda prompt: '[{"country": "France"}, {"country": "Germany"}]',
)
print(result.records)
# [{'country': 'France'}, {'country': 'Germany'}]
```

## Attribute shaping with declared types

Ask for named attributes, optionally typed. Values are coerced where the coercion is unambiguous; a value that will not coerce is kept and reported rather than dropped.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "the member states of the European Union",
    attrs=[{"name": "country", "type": "string"}, {"name": "accession_year", "type": "int"}],
    llm_fn=lambda prompt: '[{"country": "Croatia", "accession_year": "2013"}]',
)
print(result.records)
# [{'country': 'Croatia', 'accession_year': 2013}]
```

`int`, `integer`, `float`, `number`, `bool`, `boolean`, and `date` are checked. Any other declared name is passed to the model as an instruction and reported once in `meta.unchecked_types`, so a misspelled type cannot check nothing in silence.

## Diagnostics on every result

Each result includes diagnostics for remapped keys, unmatched keys, type mismatches, and multiple returned keys that map to the same attribute.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "the busiest airports in Paris",
    attrs=["name", "iata_code"],
    llm_fn=lambda prompt: '[{"Name": "Charles de Gaulle", "IATA": "CDG"}]',
)
print(result.records)          # [{'name': 'Charles de Gaulle', 'iata_code': None}]
print(result.meta.remapped_keys)  # {'Name': 'name'}
print(result.meta.unmapped_keys)  # ['IATA']
```

`IATA` does not reach `iata_code`: the normalised forms are `iata` and `iatacode`, and assigning it would be a guess. It is reported instead.

## A closed outcome vocabulary

An empty result is a valid answer, and the reason it is empty is a separate fact from the records.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
print(source.fetch("", llm_fn=lambda prompt: "[]").meta.outcome)
# empty_query
print(source.fetch("airports", llm_fn=lambda prompt: "[]").meta.outcome)
# model_returned_none
```

`ok`, `empty_query`, `model_returned_none`, `filtered_to_empty`, `partial`, `stopped_by_limit`, and `truncated` are the whole vocabulary.

## Bounding one request on four axes

Rows, time, cost, and call count. Calls, time, and cost are claimed before the provider is contacted; rows are capped once the records are parsed.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "commercial airports in France",
    attrs=["name"],
    llm_fn=lambda prompt: '[{"name": "Charles de Gaulle"}, {"name": "Orly"}]',
    properties={"limits.max_rows": 1},
)
print(len(result), result.meta.outcome, result.meta.truncated_to)
# 1 truncated 1
```

A ceiling passed with the call binds that call and is not written back, so the next request through the same source is unbounded again.

## One total across a sequence of requests

Hold a `Budget` yourself and hand it to every fetch that should share it. Each fetch derives a child that spends only what the parent has left.

```python
from blue_data_llm_source import Budget, Limits, LlmSource

source = LlmSource()
run = Budget(Limits(max_rows=2))
answer = lambda prompt: '[{"name": "Charles de Gaulle"}]'

first = source.fetch("airports in France", attrs=["name"], llm_fn=answer, budget=run)
second = source.fetch("airports in Spain", attrs=["name"], llm_fn=answer, budget=run)
third = source.fetch("airports in Italy", attrs=["name"], llm_fn=answer, budget=run)
print(len(first), len(second), len(third), third.meta.stopped_by)
# 1 1 0 max_rows
```

## An upper bound on cost, before spending anything

`estimate` builds the prompt this source would actually send, bounds it, and contacts nothing. It is the same bound a run reserves against, so a request that sizes inside a ceiling is not refused when it runs.

```python
from blue_data_llm_source import LlmSource

source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 512})
sized = source.estimate("commercial airports in France", attrs=["name", "iata_code"])
print(sized.bounded, round(sized.max_cost, 6), sized.max_attempts)
# True 0.001301 3
```

A model this package carries no price for comes back `bounded=False` with a `reason` instead of raising, because "how much might this cost" is a reasonable question to ask about a model nobody here has a price for.

```python
from blue_data_llm_source import LlmSource, priceable_models, rates_for

print(len(priceable_models()), rates_for("gpt-4o"))
# 10 (2.5e-06, 1e-05)
unpriced = LlmSource().estimate("anything", properties={"llm.model": "a-model-nobody-here-prices"})
print(unpriced.bounded, unpriced.max_cost)
# False None
```

## An injectable model client

Any object with `complete` and `acomplete` can provide model responses. Injection is structural; it requires no subclass, registry, or `isinstance` check against a concrete class.

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
        return '[{"name": "Orly"}]'


print(LlmSource(client=ScriptedClient()).fetch("airports", attrs=["name"]).records)
# [{'name': 'Orly'}]
```

The full contract, including how a client is held to a ceiling, is in [extending.md](extending.md#the-injected-client).

## Response caching

Two stores ship, both standard-library only, both satisfying a two-method protocol you can implement yourself. They are handed to a run through the `cache_store` property and are consulted by the client that reaches a provider.

```python
from blue_data_llm_source import CacheStore, FileCache, MemoryCache

cache = MemoryCache(max_entries=2)
cache.set("a", {"answer": 1})
cache.set("b", {"answer": 2})
cache.set("c", {"answer": 3})
print(cache.get("a"), cache.get("c"), isinstance(cache, CacheStore))
# None {'answer': 3} True

disk = FileCache(directory=".llm_cache")
disk.set("a", {"answer": 1})
print(disk.get("a"), disk.get("nothing-was-stored-here"))
# {'answer': 1} None
disk.clear()
```

`MemoryCache` evicts least-recently-used entries past its bound. `FileCache` writes one JSON file per entry, names each file by the SHA-256 of its key, and its `clear` removes only entries it wrote. Pointing it at a directory that already holds JSON cannot delete that JSON.

## Decomposing a large request

One model call answers a request by default. `plan.mode` splits it instead: `partitioned` asks along a key the planning stages found, `row_by_row` asks once per entity they enumerated.

```python
import json

from blue_data_llm_source import LlmSource

AIRPORTS = {
    "Île-de-France": [{"name": "Charles de Gaulle"}, {"name": "Orly"}],
    "Provence": [{"name": "Marseille Provence"}],
}


def answer(prompt: str) -> str:
    if prompt.startswith("Your task is to prepare a data request"):
        return json.dumps({"ask": "commercial airports in France", "constraints": {}})
    if prompt.startswith("Resolve the output schema"):
        return json.dumps({"attrs": [{"name": "name"}], "primary_keys": ["name"]})
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
    attrs=["name"],
    llm_fn=answer,
    properties={"plan.mode": "partitioned", "plan.key": "name",
                "plan.partition_key": "region", "plan.max_depth": 1},
)
print([record["name"] for record in result.records])
# ['Charles de Gaulle', 'Orly', 'Marseille Provence']
print(result.meta.calls, result.meta.record_origins)
# 6 [['0.0'], ['0.0'], ['0.1']]
```

The scripted answers above stand in for a real model at each planning stage. Eighteen `plan.*` settings bound the shape; they are listed in [api-reference.md](api-reference.md#plan-properties).

## Attribution across a split

`meta.record_origins` names the parts of a split request that produced each record, parallel to `records`. Overlapping parts can each return the same record, so each entry is a list.

## Asking again for what did not come back

A model asked for two hundred records returns forty and reports nothing wrong. `plan.max_continuations` lets a part ask again, naming what it already holds, up to a cap. At the default of `0` each part asks once. `meta.stopped_by` reports `plan.max_continuations` when the cap ended a run that was still producing records. That says completeness was never established, not that more records exist.

## Composite identity keys

`plan.keys` declares the ordered fields that jointly identify one record. The same normalisation is used when merging parts, when excluding held records from a continuation, and when validating a partition rule, so two stages cannot disagree about whether two records are the same record.

```python
from blue_data_llm_source import Attr, key_identity

attrs = [Attr(name="iata_code", type="string"), Attr(name="year", type="int")]
first = key_identity({"iataCode": "CDG", "year": "1974"}, ["iata_code", "year"], attrs)
second = key_identity({"iata_code": " cdg ", "year": 1974}, ["iata_code", "year"], attrs)
print(first == second, first.exclusion())
# True {'iata_code': 'CDG', 'year': '1974'}
print(key_identity({"iata_code": "CDG"}, ["iata_code", "year"], attrs))
# None
```

An incomplete key is `None`, not a partial identity, because a key missing a component identifies nothing.

## A planning receipt

A direct Python call that opts into rule-backed distribution search can get a `PlanningReceipt` alongside the records, binding the configured rule digests, the planning facts, and the versioned plan. It checks internal agreement among those three. It does not verify factual truth, completeness, or answer quality.

```python
from blue_data_llm_source import LlmSource

result = LlmSource().fetch("airports", attrs=["name"], llm_fn=lambda prompt: '[{"name": "Orly"}]')
print(result.planning_receipt)
# None
```

An ordinary result carries no receipt, and `FetchResult.to_dict()` keeps its two keys. Distribution search additionally requires `plan.mode="partitioned"`, ordered `plan.keys`, and a list of declared finite-domain rules; [extending.md](extending.md#identity-partition-rules) has a runnable example, and [api-reference.md](api-reference.md#plan-properties) specifies the settings. Neither the command-line interface nor the MCP server exposes distribution search or the receipt.

## The data-layer source surface

`LlmSource` extends the shared `DataSource` base class, so it registers and answers like any other source in the layer. `execute_query` takes a natural-language string or a dict carrying an `op` key.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
print(source.execute_query({"op": "LLM.MODELS"}))
# [{'protocol': 'llm', 'variant': 'openai'}]
print(source.execute_query_nl(
    "commercial airports in France",
    llm_fn=lambda prompt: '[{"name": "Orly"}]',
    optional_properties={"attrs": ["name"]},
))
# [{'name': 'Orly'}]
```

`LLM.FETCH`, `LLM.MODELS`, and `LLM.ESTIMATE` are the supported ops. Both surfaces return a plain `list[dict]`; call `fetch` when you also need the metadata.

## An orchestrator tool surface

The base class advertises eight tools for an orchestrating model to call one at a time. Four of them can be answered from a model's own knowledge, so four are advertised and four are not.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
print([tool["name"] for tool in source.get_nl_tools()])
# ['list_databases', 'list_collections', 'get_schema', 'execute_query']
print(source.execute_nl_tool("list_databases", {}))
# ['llm']
```

`execute_nl_tool` refuses any other name with a message listing the four, and nothing on that path escapes the `LlmSourceError` hierarchy.

## One serialization of the result metadata

`meta_view` is the single JSON-ready view of `ExecMeta` that every adapter publishes, so a run cannot describe itself two ways depending on which door the caller came through.

```python
from blue_data_llm_source import LlmSource, meta_view

result = LlmSource().fetch("airports", attrs=["name"], llm_fn=lambda prompt: '[{"name": "Orly"}]')
view = meta_view(result.meta)
print(view["outcome"], view["gaps"], "cache_hits" in view)
# ok [] False
```

Three tiers: always present, present only when the thing happened, and verbose-only for the four diagnostics that carry arbitrary model output.

## Synchronous and asynchronous entry points

`afetch` is the real one; `fetch` wraps it. Calling `fetch` from inside a running event loop raises `ConfigError` naming `afetch` rather than deadlocking.

```python
import asyncio

from blue_data_llm_source import LlmSource


async def main() -> None:
    source = LlmSource()
    result = await source.afetch("airports", attrs=["name"], llm_fn=lambda prompt: '[{"name": "Orly"}]')
    print(result.records)


asyncio.run(main())
# [{'name': 'Orly'}]
```

## One error hierarchy

Nothing escapes `LlmSourceError` from `fetch`, `afetch`, or `estimate`. Anything unexpected is wrapped with the original attached as `__cause__`.

```python
from blue_data_llm_source import ConfigError, LlmSource

try:
    LlmSource().fetch("airports", llm_fn=lambda prompt: "[]", properties={"limits.max_cst": 1.0})
except ConfigError as exc:
    print(str(exc).split(".")[0])
# unknown limit 'limits
```

`ConfigError`, `QueryError`, `TransportError`, `ParseError`, and `BudgetExceeded` are the five, and every message names the offending value and the accepted form, written for an agent whose whole view of a failure is `str(exc)`.

## A typed package

`py.typed` ships, every module is annotated, and both platform packages type-check against the library's real types rather than treating each import as unknown.

## Command line and MCP server

Two adapters ship over this library. Neither holds retrieval logic of its own. See [cli.md](cli.md) and [mcp.md](mcp.md).

## Not built

There is no REST interface, no retrieval or grounding path, no factual validator, and no completeness measure. [architecture.md](architecture.md) describes these boundaries and the three implemented interfaces.
