# Developer Guide

This guide covers the main library tasks: asking a question, shaping the answer, setting cost bounds, splitting a large request, and evaluating the result metadata.

Every example runs as shown. Most supply an `llm_fn`, a plain `(prompt) -> str` callable used as the model for that call, so they run offline and at no cost. The two examples that reach a provider are identified above their code blocks.

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

Declare a type and the value is coerced when the conversion is unambiguous. A value that cannot be converted is kept as received and reported; dropping a record over a formatting mismatch would lose requested data.

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

`int`, `integer`, `float`, `number`, `bool`, `boolean`, `date`, `str`, `string`, and `text` are the names this library checks. Any other name is still sent to the model as an instruction and is reported once in `meta.unchecked_types`, so a misspelled type is not mistaken for an active check.

Only a genuine thousands grouping is stripped from a number, so a decimal comma is not silently turned into a hundredfold error.

## Reading the result metadata

`result.meta` is where the library says what happened. Read four things before you use the records.

| Field | Read it for |
|---|---|
| `outcome` | `ok` means no detected execution failure; other values distinguish empty, truncated, stopped, and partial runs. No value establishes completeness |
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

`Name` matched `name` because matching ignores case, and the remapping is reported. `IATA` matched nothing (`iata` and `iatacode` are different normalized forms), so it is reported as unmapped rather than assigned by guesswork.

`meta_view` is the JSON-ready serialization published by every adapter. Use it instead of writing your own to keep the result description consistent across consumers.

**`gaps` being empty means no supported diagnostic fired.** It is not a completeness claim. Non-flat keyed fetches can report `split_coverage_short` when every accepted immediate child has a known estimate and their sum is below the parent's lower estimate. `observed_coverage_short` compares complete distinct identities with the root's parsed lower estimate, before any row cap. A comparison is skipped when its required estimates are unavailable. Model overestimates can trigger these warnings even for complete results; the warnings do not change `outcome`.

## Bounding one request

Source settings override client settings; a call can only tighten the resulting ceilings. For the rules on `None`, zero, retries, row caps, caller-supplied prices, and a per-call model or rates under a configured cost ceiling, see the [compatibility guide](../lib/COMPATIBILITY.md#deviations-from-the-earlier-implementation).

Per-call properties merge over the instance's properties for that request and are not written back. A `limits.*` value can only tighten the effective ceiling; `None` leaves the configured ceiling unchanged; `limits.max_retries=None` is invalid. `max_records` follows the same rule for the row ceiling.

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

`max_records` is the same row ceiling as a positional argument, and it is clamped by the effective `limits.max_rows` for that call.

Four axes exist. Which of them bind depends on the client that answers. See [Which ceilings bind](#which-ceilings-bind) below.

| Key | Effect |
|---|---|
| `limits.max_rows` | Truncates the result and sets `meta.truncated_to` |
| `limits.max_seconds` | Refuses an attempt once elapsed time reaches it, and cancels a wait in flight |
| `limits.max_cost` | Refuses an attempt whose cost bound would cross it |
| `limits.max_calls` | Refuses an attempt past that many, counting every retry |
| `limits.max_retries` | Retries per call, at most 9,999; the built-in client makes this many attempts plus one. An injected client owns its own retries |

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

**The minimum cost bound is the full output allowance.** The prompt contributes relatively little; `llm.max_tokens` at the model's output rate, with the long-context multiplier and OpenAI's 10% regional-processing uplift where they apply, contributes the rest. With the default `gpt-6-luna` model and 25,000 output tokens, the output-only floor is about $0.020625. A lower `limits.max_cost` refuses the call before provider contact. Reduce `llm.max_tokens` when the answer needs fewer tokens.

An unpriced model is answered, not raised on:

```python
from blue_data_llm_source import LlmSource, can_bound, priceable_models

print(priceable_models())
print(can_bound("gpt-4o", 4096), can_bound("a-model-nobody-here-prices", 4096))
unpriced = LlmSource().estimate("anything", properties={"llm.model": "a-model-nobody-here-prices"})
print(unpriced.bounded, unpriced.reason)
```

Supply `llm.price_per_input_token` and `llm.price_per_output_token` together to bound a model the table does not carry; they are used as given, so they must cover every tier the model can be billed at. Rates supplied for a model the table carries keep its cache-write, long-context and regional charges and never lower its bound. The table is a static snapshot of published list prices with no effective date and no freshness check, so treat `limits.max_cost` as a control against known rates rather than as a hard billing stop. [The cost bound](../lib/README.md#the-cost-bound) says how a call is charged once it returns, and the [known limitations](../lib/README.md#supported-models) say where the table falls short.

`estimate` reads no `plan.*` setting. Its bound covers one flat fetch, including retries. A non-flat run may make planning and extraction calls with different prompts; this estimate does not bound their combined cost.

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
| `llm.model` | Model name. Defaults to `gpt-6-luna` |
| `llm.reasoning_effort` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh` or `max`, within the set the model accepts; GPT-6 Luna defaults to `medium` |
| `llm.temperature`, `llm.max_tokens`, `llm.stream`, `llm.timeout` | Forwarded with model-specific argument names |
| `llm.cache`, `llm.cache_dir` | Enable and locate the on-disk response cache |
| `llm.max_parallel` | Concurrency bound per event loop, default 8 |
| `llm.price_per_input_token`, `llm.price_per_output_token` | Rates for a model the price table does not carry |

[Supported models](../lib/README.md#supported-models) lists the models the package knows, the reasoning efforts and default each takes, when each accepts a temperature, and its prices. A setting the model rules out raises `ConfigError` before anything is spent.

The cache, concurrency, and caller-supplied price settings are used by the library and are not sent to the provider. `llm.rate_limit` is refused by name with a `ConfigError`: client-side call spacing has to reconcile with per-attempt budget admission, and this library does not do it. Cap concurrency with `llm.max_parallel`, or throttle above this library where the whole request sequence is visible.

`llm.stream=True` is accepted and **does not make anything faster**: a fetch parses one whole JSON document, so the chunks are read to the end and reassembled before anything downstream sees them. Set it only because a provider configuration or proxy requires it. One thing changes: a stream carries token counts only if the provider chooses to send them, and when none arrive the call cannot be priced, so the reservation stays at its bound and the result reports `accounting_known=False`.

The provider SDK handles authentication. An absent or wrong key can cause a failed provider attempt. With no answered extraction response and no retained records, the fetch raises `TransportError` if the retry policy is exhausted or `max_calls` prevents the next retry. If `max_cost` or `max_seconds` refuses that retry, or a ceiling refuses the first attempt, it returns `stopped_by_limit`. If an extraction response has already been parsed, the fetch keeps its records with `outcome=partial` and a gap for the failed part. The library reads credential environment values only to mask diagnostics.

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

`MemoryCache(max_entries=512)` is in-process and evicts least-recently-used entries. `FileCache(directory=".llm_file_cache")` writes one JSON file per entry in a private directory; the explicit directory keeps this example separate from `llm.cache`, whose default is `.llm_cache`. Omitting the `FileCache` directory also uses `.llm_cache`. On POSIX systems it refuses a directory accessible to other users. Its `clear()` removes files written by that instance, leaving pre-existing files and entries written by another instance intact. Anything with `get` and `set` works (see [extending.md](extending.md#cache-backends)).

To remove `FileCache` entries left by earlier runs, first stop every process using its directory and confirm that the directory holds only this cache. Then remove that directory. For the dedicated `.llm_file_cache` directory in this example, run `rm -r -- .llm_file_cache` from the directory where it was created. A later `FileCache` construction recreates it. Do not remove a directory shared with another cache or application.

The separate `llm.cache` switch opens a `diskcache` backend instead and does not use either store. It creates its cache directory with private permissions and refuses an existing directory that is not owned by the current user or is accessible to others on POSIX systems. If an earlier run created `.llm_cache` with broader permissions, change its mode to `0700` before enabling this cache again.

## Splitting a large request

`plan.mode` defaults to `flat`: one question, one call. `partitioned` asks the whole request first and, when that answer falls short, splits along a key the planning stages find and merges the parts with it.

Use `plan.key` or `plan.keys` for the fields that identify one record. Use `plan.partition_key` to suggest a category such as a region or a period that covers the request in a few groups. A list of individual records can leave out most of the request, so a split on the only key field the slice leaves open, such as `state` for "the US states", is kept only when the request states its values or you declare them with `plan.identity_partitions`, `plan.distribution_search` and `plan.keys` (direct Python only); otherwise the slice runs as one request and `meta.gaps` records `guard_collapsed` with the reason. The refused proposal still counts toward call and cost ceilings. A key field that leaves others open, such as `year` in a `(country, year)` key, groups records and splits like any category.

Both partition prompts ask for values that together cover the current slice, or for no split when a complete partition cannot fit. A model asked for a split may list individual records, as `gpt-4o` does for the US states; setting `plan.partition_key` to a category such as a region or a period asks it for groups instead. Check `meta.gaps` for estimate warnings.

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

Nine calls to `answer`: three planning stages, the root's own ask, one partition proposal, and two for each leaf. The stand-in answers the root's ask, which names no region, with `[]`, so the root splits. Each leaf asks again because its first answer added records, and stops when the second adds none. A provider-backed run prices each prompt separately; the flat estimate does not bound their sum. Set a cost ceiling for the run or use `flat` mode. See [Estimating before you spend](#estimating-before-you-spend).

The settings worth knowing first:

| Property | Default | Meaning |
|---|---|---|
| `plan.mode` | `flat` | `flat`, `partitioned`, or `row_by_row` |
| `plan.key` | none | The attribute identifying a record, so parts returning the same record merge |
| `plan.keys` | empty | Ordered fields that jointly identify one record, when one attribute is not enough |
| `plan.partition_key` | none | Preferred root split dimension. Descendants may choose another and cannot repeat an ancestor's |
| `plan.max_depth` | `2` | Deepest partition level. `0` keeps the planning stages and disables subdivision |
| `plan.max_partitions` | `20` | Most children one proposal may create |
| `plan.max_leaves` | `100` | Most terminal slices. A split that would exceed it is refused and its slice runs as one request |
| `plan.planning_order` | `planning.schema_first` | Which fact schedule runs, and how many calls it may make |

The full set is in [api-reference.md](api-reference.md#plan-properties).

A planning stage whose response does not parse does not fail the request. It falls back to what you declared, records a diagnostic in `meta.gaps`, and the run continues, so the failure mode of a decomposed request is a flat one with gaps rather than an exception.

`meta.asked` is worth checking on a non-flat run. The analysis stage restates the request for retrieval, and that restated text becomes the query every part runs. The rewrite is what makes "I don't know how many countries are there?" answerable at all, and it is also what could broaden or narrow the question. The change is reported rather than prevented: when `meta.asked` is not `None`, that is the question the records answer.

### Asking the root before splitting

`partitioned` mode asks the root first. Under `plan.split_policy="evidence"`, the default in that mode, the whole request goes out as a single leaf with its continuations, the probe, and the plan splits only when the probe's answer was cut off, ran out of continuations while still adding records, failed, holds no keys although the estimate expects records, or holds fewer than half the keys an estimate above `plan.split_threshold_rows` (default `120`) expects. Otherwise that answer is the result. A model that stops early and adds nothing when asked to continue therefore keeps its short answer when the estimate is `plan.split_threshold_rows` or less, or missing; `meta.gaps` reports `observed_coverage_short` when the answer falls below the model's lower estimate. Lower `plan.split_threshold_rows` to apply the half-of-the-estimate rule to smaller requests. `plan.split_policy="always"` splits the root whatever it holds. The probe is an ordinary leaf prompt that names no partition value, so in a scripted `answer` it reaches the last branch:

```python
import json

from blue_data_llm_source import LlmSource

AIRPORTS = {
    "Île-de-France": [{"name": "Charles de Gaulle", "region": "Île-de-France"},
                      {"name": "Orly", "region": "Île-de-France"}],
    "Provence": [{"name": "Marseille Provence", "region": "Provence"}],
}
ALL = [record for records in AIRPORTS.values() for record in records]


def answer_with(expected, probe_answer):
    def answer(prompt: str) -> str:
        if prompt.startswith("Your task is to prepare a data request"):
            return json.dumps({"ask": "commercial airports in France", "constraints": {}})
        if prompt.startswith("Resolve the output schema"):
            return json.dumps({"attrs": [{"name": "name"}, {"name": "region"}],
                               "primary_keys": ["name"]})
        if prompt.startswith("Estimate the number of records"):
            return json.dumps({"expected": expected, "lower": 2, "upper": expected + 5,
                               "row_unit": "airports"})
        if prompt.startswith("Propose one useful partition"):
            return json.dumps({"key": "region", "values": list(AIRPORTS),
                               "ambiguity": 0.1, "child_estimates": [2, 1]})
        for region, records in AIRPORTS.items():
            if region in prompt:
                return json.dumps(records)
        # The probe: the root's own ask, naming no region.
        return json.dumps(probe_answer)
    return answer


settings = {"plan.mode": "partitioned", "plan.key": "name",
            "plan.partition_key": "region", "plan.max_depth": 1}

whole = LlmSource().fetch("commercial airports in France", attrs=["name", "region"],
                          llm_fn=answer_with(3, ALL), properties=settings)
print(whole.meta.split_decision, whole.meta.calls, len(whole.records))
# probe_accepted 5 3

short = LlmSource().fetch("commercial airports in France", attrs=["name", "region"],
                          llm_fn=answer_with(200, ALL[:1]), properties=settings)
print(short.meta.split_decision, short.meta.calls, len(short.records))
# probe_split_shortfall 10 3
```

The first request is answered by the probe: three planning calls, the probe, and one continuation that adds nothing. In the second, the estimate of 200 is above the threshold and the probe holds one key, so the plan splits: two probe calls, one proposal and two calls for each leaf, with the probe's record merged into the result under the origin `root`. `meta.split_decision` names the outcome and is absent when no probe ran. The probe and its continuations count in every published bound.

When the plan splits, any value of the split key that the probe returned but the proposal left out is added to the proposal, so its records are still asked for. If that takes the proposal past `plan.max_partitions`, the proposal is refused and the probe's answer is the result.

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

In `partitioned` mode a part with a known primary key may ask again up to three times unless you set the cap. `flat` and `row_by_row` default to `0`, so each part asks once. A continuation may make another provider call and incur a charge; a cache hit does neither.

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

Build the source and hand the instance to `register_source`. Inheriting the base class does not make the source reachable by protocol name: the registry records the class alongside `name` and `properties`, and `connect_source` returns the live instance while it is present and reconstructs a new source from that record afterwards.

The registry stores the source's own properties, not its injected client or the client's settings. Put ceilings, model and rates on the source when they must survive registration and reconstruction; a rebuilt source starts with the default client.

`cache_store` is an in-process handle. Registration keeps it on the live source and omits it from persisted properties. A reconstructed source does not recover that object; use the serializable `llm.cache` and `llm.cache_dir` settings for a persistent cache, or supply a handle for a direct call after reconstruction.

Registry queries return rows only. If a cost, call or time limit prevents every extraction answer, they raise `BudgetExceeded` instead of returning an empty success. Valid empty answers remain `[]`, and useful partial rows remain available. Use direct `fetch` or the dedicated CLI/MCP fetch surfaces when you need outcome and gap metadata for partial results.

`execute_query` accepts natural language, an operation dict, or a JSON-encoded object with an `op` key: `LLM.FETCH`, `LLM.MODELS`, or `LLM.ESTIMATE`. Other strings remain natural-language queries. Both `execute_query` and `execute_query_nl` return a plain `list[dict]`, because that is what the shared surface returns. Call `fetch` when you also need the outcome, the accounting, or the diagnostics.

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

The orchestrator tool surface works the same way. `get_nl_tools` advertises the five tools this source can answer, `execute_nl_tool` runs one by name, and `execute_query_nl_tools` is refused with a `ConfigError` naming the bounded path: it drives a loop of up to ten provider calls that this source's ceilings never see.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
print([tool["name"] for tool in source.get_nl_tools()])
print(source.execute_nl_tool("get_schema", {"database": "llm", "collection": "public"}))
print(source.execute_nl_tool("get_sample", {
    "database": "llm", "collection": "public", "entity": "llm_entity", "sample_size": 10,
}))  # None: statistics and stored samples are unavailable.
```

## Driving it from async code

Synchronous query methods work inside a running event loop by waiting for an owned worker thread. They block the calling thread and delay its cancellation callbacks. Use `afetch` for concurrent async work and for a client or `llm_fn` that needs the caller's own event loop; a synchronous call hangs on such a client. Thread-affine objects, such as an sqlite connection used inside `llm_fn`, fail on the worker thread. Under `asyncio.run`, the first Ctrl-C cancels the in-flight fetch when the synchronous call runs in the runner's main task. A call in another task observes cancellation only if that task receives the request. In a plain event-loop callback, that signal cancels the runner's main task while the fetch continues; a second Ctrl-C interrupts the waiting thread. Schedule and await `afetch` in a task for cancellable asynchronous work. Once the bridge receives an interrupt or task-cancellation request, it raises as soon as the fetch stops, or after five seconds if the client ignores cancellation; that work then finishes in the background. Time limits cancel the same cooperative way, so they cannot forcibly stop a client that ignores cancellation either. See [`lib/COMPATIBILITY.md`](../lib/COMPATIBILITY.md).

```python
import asyncio

from blue_data_llm_source import LlmSource


async def main() -> None:
    source = LlmSource()
    answer = lambda prompt: '[{"name": "Orly"}]'
    result = await source.afetch("airports in France", attrs=["name"], llm_fn=answer)
    print(result.records)
    # This synchronous call blocks the caller thread until the worker finishes.
    print(source.fetch("airports in France", attrs=["name"], llm_fn=answer).records)


asyncio.run(main())
```

`llm.max_parallel` bounds how many provider calls one event loop makes at once, which is what a fan-out of leaves runs under.

## Which ceilings bind

A ceiling binds only on an axis the answering client declares. A configured ceiling on an undeclared axis raises `ConfigError` at the first fetch.

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

Results are model-generated. This library verifies shape, never truth. Model knowledge may be outdated. Use a maintained source for authoritative facts and a document or vector source for information held in private documents.

1. **`meta.outcome`.** `ok` means no detected execution failure, not a complete answer.
2. **`meta.stopped_by` and `meta.stop_reasons`.** A ceiling that fired is the commonest reason an answer is short.
3. **`meta.gaps`.** A failed part, a continuation cap, a response cut off at the model's output limit, an expansion held intact by `plan.max_leaves`.
4. **`meta.unmapped_keys`, `meta.key_collisions`, `meta.type_mismatches`, `meta.unchecked_types`.** Whether the records are shaped the way you asked.
5. **`meta.accounting_known`.** Whether the cost figure is a measurement.
6. **`None` values.** A requested attribute the model omitted and an explicit JSON `null` both arrive as `None`, and the result does not distinguish them. An empty `type_mismatches` means "nothing present failed its type", not "every field is populated". Check for `None` yourself when a field is required.

`meta.row_estimate` is a model-generated planning hint, not an expected row count or a recall measure. It is `None` in `flat` mode and whenever no estimate was accepted.

## Next steps

- [api-reference.md](api-reference.md): every signature, parameter, and return.
- [extending.md](extending.md): write your own client, cache, or partition rule.
- [architecture.md](architecture.md): how the pieces fit, and what is deliberately absent.
- [`lib/COMPATIBILITY.md`](../lib/COMPATIBILITY.md): read this before porting a caller; it records what this library does differently from what the caller was written against.
