# LlmSource — Scenarios

## Scenario 1: A planner needs a small table no source holds

A planner is resolving a query that joins a vendor table against European Union membership. The vendor table is in Postgres. There is no membership table anywhere in the registry, and nobody is going to create one for a list that changes once a decade. The planner asks LlmSource for it, naming the columns it wants and the type of each, and receives records it can hand straight to a relational operator.

**Example:**

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "the member states of the European Union",
    attrs=[{"name": "country", "type": "string"}, {"name": "accession_year", "type": "int"}],
)
# result.records → [{"country": "Austria", "accession_year": 1995}, ...]
# result.meta.model, result.meta.calls, result.meta.cost describe what it took
```

The request becomes one prompt and one completion, taking up to `limits.max_retries + 1` provider attempts — three by default. The response is stripped of any code fence, parsed as JSON, aligned to the two requested attributes, coerced to the declared types, and deduplicated. This is the shape most requests take, and it is the one the release is built around.

---

## Scenario 2: The caller does not know the shape of the answer

An agent asks a question without supplying an attribute list, because it does not yet know what the answer looks like. LlmSource passes the request through with no attribute specification, and the model chooses its own keys. Nothing is aligned, because there is nothing to align to, and the records come back as the model shaped them. What the caller gets in return is the list of keys that actually appeared, so it can decide what to do next rather than guess.

**Example:**

```python
result = source.fetch("what should I know about shipping container standards?")
# result.records   → records whose keys the model chose
# result.meta.observed_keys → ["designation", "length_ft", "width_ft", "max_gross_kg"]
```

Records produced this way can be ragged: two records need not carry the same keys, and the source does not make them uniform. Deriving a schema when the caller supplies none is not part of this release.

---

## Scenario 3: Context disambiguates an attribute name

A caller asks for records with an attribute named `name`, which is ambiguous in the question being asked. The `context` parameter exists for exactly this. It is not general background prose; its documented use is to say what an attribute name means, and it is rendered into the prompt as its own section.

**Example:**

```python
result = source.fetch(
    "Who is the current US president?",
    attrs=["name", "age", "party"],
    context="The name in the output should be the last name.",
)
# result.records → [{"name": "…", "age": …, "party": "…"}]
```

Attributes may be given as plain names or as `{"name": ..., "type": ...}` pairs. A plain name shapes the output without constraining the value; a name with a type additionally drives coercion during verification.

---

## Scenario 4: The caller wants a bounded number of records

A caller wants three records rather than everything the model would volunteer. There are two ways to say so and they do different things. Expressing the count inside the request influences what the model returns. Passing `max_records` caps what the source will hand back, is enforced as a hard limit, and reports itself when it truncates.

**Example:**

```python
result = source.fetch(
    "Show me 3 presidents of the United States",
    attrs=["name", "year", "party"],
    context="Year in output refers to the year that the person became president.",
    max_records=3,
)
# if the model returned more: result.meta.truncated_to → 3, result.meta.stopped_by → "max_rows",
# and result.meta.outcome → "truncated"
```

`max_records` becomes the row axis of the run's budget. A run that produces more records than the cap keeps the first `max_records` of them and records both the truncation point and the limit that caused it.

---

## Scenario 5: The source is registered and queried like any other

A data layer holds a registry of sources and queries them through a common surface. LlmSource inherits `blue_data.source.source.DataSource` and overrides `execute_query`, `execute_query_nl`, and the metadata surface, so it is a source of that layer by type rather than by resemblance, and a divergence from those signatures is a type error rather than something an integrator discovers at runtime.

**Example:**

```python
source.execute_query({
    "op": "LLM.FETCH",
    "query": "the member states of the European Union",
    "attr_names": ["country", "accession_year"],
})
# → [{"country": "Austria", "accession_year": 1995}, ...]

source.execute_query({"op": "LLM.MODELS"})
# → [{"protocol": "llm", "variant": "openai"}]

source.execute_query({"op": "LLM.ESTIMATE", "query": "the member states of the European Union"})
# → [{"bounded": True, "max_cost": …, "model": "gpt-4o", ...}]
```

Its connection properties report protocol `llm` and variant `openai`, following the same scheme under which the relational sources report `sql`/`postgres` and `sql`/`mysql`. `execute_query` also accepts a plain string, which is treated as the request itself, and an unrecognised op raises `QueryError` listing the three above.

What the base class does not buy is construction by protocol name, and the two registry paths differ on exactly that. `register_source` takes an instance, stores it under its name, and reads only `name` and `properties`, so handing it an `LlmSource` works. `connect_source` returns the live instance while it is registered, and otherwise reconstructs one from the class path recorded at registration, with the properties it was registered with. Protocol-name dispatch is a legacy path for older records and is not how this source is reached. Adding that arm is a change in `blue-data`, so the supported integration is to build the source and register the instance, which is what it was before this package took the dependency.

Base-class order carries the weight that inheritance does buy, and it is worth stating because getting it wrong fails quietly. `DataSource` is a plain class rather than an abstract one. Most of its methods return an empty result rather than raising, so one left unoverridden here answers nothing instead of failing. That is not true of all of them any more: the transaction methods raise `NotImplementedError` naming this source, and the orchestrator-tool methods carry real behaviour, which is why this source overrides those three rather than inheriting them: two are narrowed to the tools it can answer, and `execute_query_nl_tools` is refused. Where a method does return empty: a registry walking the source would find a working source that reports no databases and returns no records. `LlmSource` is declared with `DataSource` as its base so its own implementations come first, and a test asserts that every method on the shared surface resolves to this class.

---

## Scenario 6: The caller already has a model wired up

A caller reaching the source through `execute_query_nl` passes an `llm_fn`. For every other source that callable exists to translate natural language into a native dialect, SQL or Cypher or a document filter, before the translated query is executed. This source has no other dialect, because natural language is its native form, so there is nothing to translate into and the translation step is the identity.

That leaves a callable typed `(prompt: str) -> str`, which is the shape of a model call. LlmSource therefore uses a supplied `llm_fn` as the model client for that request, falling back to its configured client when none is passed.

**Example:**

```python
records = source.execute_query_nl(
    "the member states of the European Union",
    llm_fn=my_existing_model_call,
    optional_properties={"attr_names": ["country", "accession_year"]},
)
```

A caller that already holds a configured model reuses it and pays nothing extra. A caller that does not is unaffected, because the configured client answers as before.

What the wrapper around that callable can be held to is rows, and nothing else. It can prove that it invoked the function once, and one invocation is not one provider call: the callable may answer from its own memory, contact a provider once, or fan out to several, and none of that is visible from here. A `limits.max_calls`, `limits.max_cost`, or `limits.max_seconds` on a run that supplies `llm_fn` is therefore refused with a `ConfigError` naming the ceiling, while `limits.max_rows` binds as it does everywhere, because this source counts the records itself. A caller that wants any of the other three over its own model call injects a client that declares those axes and claims against the budget it is handed, which is Scenario 7's boundary rather than this one's.

---

## Scenario 7: A caller caps what a request may spend

An exploratory planner is willing to spend a few cents and five seconds on a speculative request, and wants a definite answer if it cannot. Limits are configured as properties. Every axis is claimed before the provider is contacted, so a run that would cross one is refused rather than reported after the money is gone. A call's price is not known until it returns, so cost is claimed against an upper bound: the most the attempt could cost is reserved before it runs and reconciled against the real figure afterwards, which releases the difference.

**Example:**

```python
result = source.fetch(
    "commercial airports in France",
    attrs=["name", "iata_code"],
    properties={"limits.max_cost": 0.10, "limits.max_seconds": 5, "limits.max_calls": 1},
)
# If a limit blocks the call: result.records is empty, result.meta.stopped_by names the limit,
# and result.meta.outcome is "stopped_by_limit"
```

The four axes are row count, elapsed time, API cost, and call count. Rows are capped after the records are in hand, because that is when the count is known. Time, calls, and cost are reserved before each provider attempt, under a lock, so neither a fan-out nor a retry sequence can race past a ceiling. A cache hit consumes no provider call, no tokens and no cost. Rows it returns still count against the row cap, and elapsed-time admission still applies, so a hit under a spent deadline is refused. The time axis bounds admission and cooperative waiting rather than the whole run; [what it bounds](../../lib/README.md#what-the-time-ceiling-actually-bounds) lists the exclusions.

The cost bound is not tuned to the average, and the difference decides how a ceiling is sized. It counts the prompt's UTF-8 bytes rather than dividing anything by an average, because a byte-pair tokenizer merges byte sequences and so consumes at least one byte of the encoded prompt per token it emits; and it counts the whole `llm.max_tokens` allowance at the model's output rate, since nothing observable predicts how much of it a model will use. The floor of the bound is therefore that output allowance alone, which for the shipped defaults of `gpt-4o` and 4096 tokens is about $0.041. A `limits.max_cost` of 0.10 leaves room for that; a `limits.max_cost` of 0.01 would refuse every call before making one, and the fix is a smaller `llm.max_tokens` rather than a larger ceiling.

Counting the prompt's characters would not have been a bound, which is worth stating because it reads like one. A tokenizer splits a character whose encoding it has no merge for, so a composed emoji of several code points can cost more tokens than it has characters, and a 1,400-character sequence has been measured at 2,200 tokens. Bytes are what the tokenizer actually consumes, so the claim holds there and nowhere above it.

Where the bound cannot be established the ceiling is refused rather than approximated. A model this package carries no price for, with no `llm.price_per_input_token` and `llm.price_per_output_token` supplied, raises a `ConfigError` naming what is missing, as does an `llm.max_tokens` that is not a finite positive number. A model name resolves to a published price only when it matches an entry exactly or extends one with a dated suffix, so `gpt-4o-mini-2024-07-18` is priced as `gpt-4o-mini` while `gpt-4o-miniature-premium` is refused: a longer name is a different model rather than a variant of the one whose name it happens to start with. Supplying either price key commits the caller to both, and a value that is not a finite positive number is refused rather than passed over in favour of the table, since an override that was meant and then ignored produces a bound lower than the one the caller asked for.

Which ceilings bind at all depends on the client that answers. A client declares its axes through `budget_capabilities()`, and each configured ceiling on an axis it did not declare is refused with a `ConfigError` naming it. The default client declares all four. A callable passed as `llm_fn` declares rows only, because nothing here can know how many provider calls that callable makes, what it is billed, or how to stop it when a deadline passes, so a call, cost, or time ceiling over an `llm_fn` is refused rather than quietly settled at zero. The row cap binds any client, since this source counts the records itself.

The refusal reads every ceiling in the budget chain rather than only the ones this request configured. A `limits.max_cost` carried on a planner's own budget and never mentioned in the call is still a ceiling the run would have to honour, so it is checked against the answering client in the same pass as the rest.

A caller reaching the source through the query surface rather than through `fetch` sets the same ceiling the same way, because `optional_properties` is merged over the instance's properties for that call. This is the first route to reach for, since it needs nothing beyond the arguments the surface already takes and the ceiling stays with the request it arrived on.

```python
records = source.execute_query(
    {"op": "LLM.FETCH", "query": "commercial airports in France", "attrs": ["name"]},
    optional_properties={"limits.max_cost": 0.10, "limits.max_seconds": 5},
)
# The next call through the same source is unbounded again: the properties were merged for
# this request and not written back.
```

Both routes above bound one request. A caller that wants one ceiling over a sequence of requests passes its own budget instead, which accumulates across every call it is handed to.

```python
from blue_data_llm_source import Budget, Limits

run = Budget(Limits(max_cost=0.50, max_calls=4))
first = source.fetch("commercial airports in France", attrs=["name", "iata_code"], budget=run)
second = source.fetch("commercial airports in Spain", attrs=["name", "iata_code"], budget=run)
# The second fetch draws on what the first left, and is refused once the ceiling is reached.
```

---

## Scenario 8: An operator sizes a request before running it

A planner or an operator has a request in hand and a ceiling to respect, and wants to know what the request could cost before spending anything on it. `estimate` answers from the prompt this source would actually send, so the figure belongs to the request being asked about rather than to an average of requests like it. It contacts nothing.

The figure is an upper bound, not a prediction: it reserves the whole output allowance at the model's output rate and bounds the prompt by its encoded length, so a typical call costs a fraction of it. Being wrong high is the only safe direction, because a figure that can be exceeded is not a bound and a decision made against one is a decision made against a guess. It is the same bound the run reserves against, so an estimate a caller trusts and a ceiling the run enforces cannot disagree.

**Example:**

```python
from blue_data_llm_source import LlmSource

source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 256})
estimate = source.estimate("the member states of the European Union", attrs=["country"])

if estimate.bounded and estimate.max_cost <= 0.05:
    records = source.fetch("the member states of the European Union", attrs=["country"])
```

A model this package cannot price returns `bounded=False` with a `reason` rather than raising, because asking what a request might cost is fair even where the answer is unknown, and the honest answer is that it cannot say. Supplying `llm.price_per_input_token` and `llm.price_per_output_token` makes such a model estimable.

The same question is reachable through the query surface, for a caller that has this source through the registry rather than as an object:

```python
source.execute_query({"op": "LLM.ESTIMATE", "query": "...", "attrs": ["country"]})
# [{'bounded': True, 'max_cost': 0.000200, 'reason': None,
#   'model': 'gpt-4o-mini', 'prompt_bytes': 761, 'max_output_tokens': 256}]
```

---

## Scenario 9: A tool-using agent reaches the source over MCP

An agent in another framework has the MCP server configured. It sees three tools: `llm_fetch`, which fetches records for a request, `llm_estimate`, which bounds what one such fetch could cost without making it, and `llm_info`, which reports the protocol, the variant, and the library version. Each carries a JSON input schema, and the agent calls them the way it calls any other tool. Argument validation is the library's, which matters here in particular: an agent supplies arguments programmatically and sees only the exception text as its tool result, so every rejection names the offending value and the accepted form and is passed through unreworded.

**Example:**

```json
{ "query": "the member states of the European Union",
  "attrs": ["country", {"name": "accession_year", "type": "int"}],
  "max_records": 5,
  "limits.max_cost": 0.25 }
```

```json
{ "records": [{"country": "France", "accession_year": 1958}],
  "meta": {"outcome": "ok", "model": "gpt-4o", "calls": 1, "prompt_tokens": 12, "completion_tokens": 34, "cost": 0.0012, "elapsed_seconds": 0.31} }
```

The ceiling is the agent's to set. `llm_fetch` takes `limits.max_cost`, `limits.max_seconds`, and `limits.max_calls`, and the server configures none of them itself, so a request that names no ceiling has none — an agent that has to stay inside one asks `llm_estimate` first and sets it.

`outcome` is on every result. An empty record list is not an error here, so an agent reads that field to tell an empty request from a model that returned nothing, a result reduction filtered away, and a run a ceiling stopped.

The server speaks MCP over stdio and is started by the client. It is a thin adapter over the library with no logic of its own, and the split inside it is deliberate: the tool definitions and the argument translation import nothing from the protocol SDK, so both can be exercised with only the library installed. A REST interface is not implemented and is not scheduled.

---

## Scenario 10: A person explores from a terminal

Someone wants to see what the source does before wiring it into anything. The `llm` command prints records as a table, with a footer stating the record count, the model, the call count, the tokens, the cost, and the elapsed time.

**Example:**

```bash
llm fetch "the member states of the European Union" --attr country --attr accession_year:int
```

```text
┏━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ country ┃ accession_year ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ France  │ 1958           │
│ Germany │ 1958           │
└─────────┴────────────────┘
2 records | model gpt-4o | 1 call | 46 tokens | $0.0012 | 0.31s
```

`--verbose` adds what the plain output leaves out: keys the model returned that matched no requested attribute, keys it renamed on the way in, the run's outcome, the limit that stopped it, and one line per value that did not fit its declared type. The outcome line is always present, since every run has one, and the rest appear only when there is something to report. The same fields appear in the `meta` object under `--format json`.


---

## Scenario 11: The answer is larger than one completion can enumerate

A caller asks for all commercial airports in France with their IATA code and the region they serve. There are roughly a hundred and fifty. A single model call returns twenty to forty, stops at a plausible place, and reports no problem.

This release does not solve that. It makes one completion per request — up to `limits.max_retries + 1` provider attempts of it — and a request whose answer exceeds what one completion enumerates comes back short. What the caller does get is the accounting to notice it: the returned record count, the model and call count, and any limit that fired. What it does not get is an estimate of how many records there should have been, so nothing in this release can distinguish a short answer from a complete one on its own.

Decomposing a request into partitioned sub-requests, deriving a schema when none is supplied, and estimating the size of an answer before fetching it are the next release. They are described here as absent rather than omitted, because a caller planning around this source needs to know which of its own checks it still has to perform.

---

## Non-scenarios

Recorded so that scope has something written down to argue against:

- Grounding an answer against a retrieved document. Closed-book is the premise, not a gap. Cross-checking against another source in the layer is a composition the planner performs, not a feature of this source.
- Verifying that a returned claim is true. The source verifies that a record matches the shape it was asked for. Nothing in it establishes correctness, and no validator ships.
- Owning provider credentials. The source is given a client or a callable and never reads, stores, or logs a key.
- Distributed execution across processes or machines. The library carries no infrastructure dependency, so anything of that kind belongs in a service wrapper rather than here.
- Planning across sources or operators. That belongs to the operator layer, which calls this source rather than the other way round.
