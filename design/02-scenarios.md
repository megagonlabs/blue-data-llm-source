# LlmSource — Scenarios

The fetch examples in this design document share the `source` created in Scenario 1 and require the `llm` extra and provider credentials. Their record excerpts illustrate result shape; model answers can vary. Scenario 6 uses an application-supplied callback.

## Scenario 1: A planner needs a small table no source holds

A planner needs to join a vendor table in Postgres with European Union membership, but the registry has no membership table. The planner asks LlmSource for the membership data, specifies the required columns and types, and passes the returned records to a relational operator.

**Example:**

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "the member states of the European Union",
    attrs=[
        {"name": "country", "type": "string"},
        {"name": "accession_year", "type": "int"},
    ],
)
# result.records → [{"country": "Austria", "accession_year": 1995}, ...]
# result.meta.model, result.meta.calls, result.meta.cost describe what it took
```

The default path uses one prompt and one completion, with up to `limits.max_retries + 1` provider attempts (three by default). The response is stripped of any code fence, parsed as JSON, aligned to the two requested attributes, coerced to the declared types, and deduplicated.

---

## Scenario 2: The caller does not know the shape of the answer

An agent asks a question without supplying an attribute list because it does not yet know what the answer looks like. LlmSource passes the request through without an attribute specification, and the model chooses its own keys. The source does not align the records; it returns them with the model's keys and reports the keys that appeared so the caller can decide what to do next.

**Example:**

```python
result = source.fetch("what should I know about shipping container standards?")
# result.records   → records whose keys the model chose
# result.meta.observed_keys → ["designation", "length_ft", "width_ft", "max_gross_kg"]
```

In default flat mode, records produced this way can be ragged: two records need not carry the same
keys, and the source does not make them uniform. Explicit non-flat modes instead use bootstrap to
infer a schema when the caller supplies none and report the accepted root `row_estimate` hint.

---

## Scenario 3: Context disambiguates an attribute name

A caller asks for records with an attribute named `name`, which is ambiguous in the question. The `context` parameter clarifies what an attribute name means and is rendered into the prompt as its own section.

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

A caller wants three records rather than everything the model would return. Including the count in the request influences the model's response. Passing `max_records` sets a hard cap on the returned records, and the source reports when it truncates them.

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

A data layer holds a registry of sources and queries them through a common interface. `LlmSource` inherits `blue_data.source.source.DataSource` and implements its query and metadata methods, so signature drift is detected by type checking.

**Example:**

```python
source.execute_query(
    {
        "op": "LLM.FETCH",
        "query": "the member states of the European Union",
        "attr_names": ["country", "accession_year"],
    }
)
# → [{"country": "Austria", "accession_year": 1995}, ...]

source.execute_query({"op": "LLM.MODELS"})
# → [{"protocol": "llm", "variant": "openai"}]

source.execute_query(
    {"op": "LLM.ESTIMATE", "query": "the member states of the European Union"}
)
# → [{"bounded": True, "max_cost": …, "model": "gpt-6-luna", ...}]
```

Its connection properties report protocol `llm` and variant `openai`, following the same scheme under which the relational sources report `sql`/`postgres` and `sql`/`mysql`. `execute_query` also accepts a JSON-encoded object with an `op` key and dispatches it like a dict. Other strings, including malformed JSON, are treated as the request itself. An unrecognized op raises `QueryError` listing the three above.

Registration requires a constructed instance. `register_source` records the instance name, properties, and class path; `connect_source` returns the live instance or reconstructs it from that record. Construction by protocol name is not supported.

A contract test verifies that required shared-surface methods resolve to `LlmSource` rather than to default base implementations. See [The Source Base Class](04-detailed-architecture.md#the-source-base-class) for the method-level rules.

---

## Scenario 6: The caller already has a model wired up

A caller reaching the source through `execute_query_nl` passes an `llm_fn`. Other sources use this callable to translate natural language into a native dialect, such as SQL, Cypher, or a document filter, before executing the translated query. This source has no other dialect, so translation is the identity.

That leaves a callable typed `(prompt: str) -> str`, which is the shape of a model call. LlmSource therefore uses a supplied `llm_fn` as the model client for that request, falling back to its configured client when none is passed.

**Example:**

```python
records = source.execute_query_nl(
    "the member states of the European Union",
    llm_fn=my_existing_model_call,
    optional_properties={"attr_names": ["country", "accession_year"]},
)
```

A caller with a configured model reuses it. Otherwise, the configured client answers as before.

The wrapper can enforce only the row limit because it cannot observe the callable's provider calls, cost, or deadline behavior. A run that supplies `llm_fn` therefore refuses `limits.max_calls`, `limits.max_cost`, and `limits.max_seconds`, while `limits.max_rows` remains available. To enforce other axes, inject a client that declares them and reserves against the supplied budget.

---

## Scenario 7: A caller caps what a request may spend

An exploratory planner sets limits of a few cents and five seconds for a speculative request, and wants a definite answer if the request cannot stay within them. Limits are configured as properties. Each axis is claimed before provider contact, so a run that would cross a limit is refused in advance. Because a call's price is unknown until it returns, cost is claimed against a bound built from the package or configured rates. After provider contact the reservation remains the minimum ledger charge, including when calculator accounting is missing or incomplete.

**Example:**

```python
result = source.fetch(
    "commercial airports in France",
    attrs=["name", "iata_code"],
    properties={
        "limits.max_cost": 0.10,
        "limits.max_seconds": 5,
        "limits.max_calls": 1,
    },
)
# If a limit blocks the call: result.records is empty, result.meta.stopped_by names the limit,
# and result.meta.outcome is "stopped_by_limit"
```

The four axes are row count, elapsed time, API cost, and call count. Rows are capped after the records are in hand, because that is when the count is known. Time, calls, and cost are reserved before each provider attempt, under a lock, so neither a fan-out nor a retry sequence can race past a ceiling. A cache hit consumes no provider call, no tokens and no cost. Rows it returns still count against the row cap, and elapsed-time admission still applies, so a hit under a spent deadline is refused. The time axis bounds admission and cooperative waiting, not the whole run; [what it bounds](../lib/README.md#what-the-time-ceiling-actually-bounds) lists the exclusions.

The cost calculation uses the prompt's UTF-8 byte length and the full `llm.max_tokens` allowance. For listed models it includes the cache-write and long-context worst cases, plus any regional uplift. With the default `gpt-6-luna` model and 25,000 output tokens, its minimum is about $0.020625. A `limits.max_cost` of 0.10 admits the example request, while 0.01 refuses it before provider contact. Reduce `llm.max_tokens` when the answer needs fewer tokens.

The source refuses a cost ceiling when it cannot establish a bound, including when model rates are unavailable or `llm.max_tokens` is not a finite positive number. Callers can supply both token rates for an unlisted model. See [Pricing helpers](../docs/api-reference.md#pricing-helpers) for the name-matching and validation rules.

Which ceilings bind at all depends on the client that answers. A client declares its axes through `budget_capabilities()`, and each configured ceiling on an axis it did not declare is refused with a `ConfigError` naming it. The default client declares all four. A callable passed as `llm_fn` declares rows only, because nothing here can know how many provider calls that callable makes, what it is billed, or how to stop it when a deadline passes, so a call, cost, or time ceiling over an `llm_fn` is refused rather than quietly settled at zero. The row cap binds any client, since this source counts the records itself.

Capability checks include ceilings inherited through the budget chain, not only values configured on the current request.

A caller using the query surface sets per-call ceilings through `optional_properties`. General per-call properties merge over the instance properties; `limits.*` can only tighten the effective ceiling, and `None` leaves it unchanged; `limits.max_retries=None` is invalid.

```python
records = source.execute_query(
    {"op": "LLM.FETCH", "query": "commercial airports in France", "attrs": ["name"]},
    optional_properties={"limits.max_cost": 0.10, "limits.max_seconds": 5},
)
# Per-call ceilings are not written back. The next call uses the source ceilings,
# which are unbounded in this example.
```

Both routes above bound one request. A caller that wants one ceiling over a sequence of requests passes its own budget instead, which accumulates across every call it is handed to.

```python
from blue_data_llm_source import Budget, Limits

run = Budget(Limits(max_cost=0.50, max_calls=4))
first = source.fetch(
    "commercial airports in France", attrs=["name", "iata_code"], budget=run
)
second = source.fetch(
    "commercial airports in Spain", attrs=["name", "iata_code"], budget=run
)
# The second fetch draws on what the first left, and is refused once the ceiling is reached.
```

---

## Scenario 8: An operator sizes a request before running it

A planner or operator can use `estimate` to learn the possible cost of a request before running it. It estimates from the prompt the source would send, rather than from an average request, and contacts no provider. It describes a `flat` fetch and reads no `plan.*` setting, so an operator sizing a split request must estimate its parts separately.

The estimate is an upper bound rather than a prediction: it reserves the full output allowance and bounds the prompt by its encoded length. Runtime reservation uses the same calculation, so a request estimated within a ceiling is evaluated against that same bound when it runs.

**Example:**

```python
from blue_data_llm_source import LlmSource

source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 256})
estimate = source.estimate("the member states of the European Union", attrs=["country"])

if estimate.bounded and estimate.max_cost <= 0.05:
    records = source.fetch("the member states of the European Union", attrs=["country"])
```

A model without known rates returns `bounded=False` with a `reason` instead of raising. Supplying `llm.price_per_input_token` and `llm.price_per_output_token` makes the model estimable.

The same question is reachable through the query surface, for a caller that has this source through the registry rather than as an object:

```python
source.execute_query(
    {
        "op": "LLM.ESTIMATE",
        "query": "the member states of the European Union",
        "attrs": ["country"],
    }
)
# [{'bounded': True, 'max_cost': 0.00084015, 'reason': None, 'model': 'gpt-4o-mini',
#   'prompt_bytes': 779, 'max_output_tokens': 256, 'max_attempts': 3,
#   'max_cost_per_attempt': 0.00028005, 'attempts_within_ceiling': None}]
```

---

## Scenario 9: A tool-using agent reaches the source over MCP

An agent in another framework with the MCP server configured sees five tools: `llmdb_query` fetches records; `llmdb_estimate` bounds the cost of an unsplit fetch without running it; `llmdb_list_models` and `llmdb_inspect_model` report which models have a published price and whether a cost ceiling can be calculated; and `llmdb_inspect_metadata` reports the protocol, variant, and library version. Each tool name has the server's prefix, avoiding collisions when an agent uses two Blue Data servers. Each tool also has a JSON input schema. The server validates arguments against that schema before calling the library. Schema errors identify the rejected field and constraint; library failures retain a known error kind and use fixed messages.

**Example:**

```json
{ "query": "the member states of the European Union",
  "attrs": ["country", {"name": "accession_year", "type": "int"}],
  "max_records": 5,
  "limits.max_cost": 0.25 }
```

The following response is illustrative; records and usage vary by run.

```json
{
  "records": [{"country": "France", "accession_year": 1958}],
  "meta": {
    "outcome": "ok",
    "model": "gpt-6-luna",
    "calls": 1,
    "prompt_tokens": 202,
    "completion_tokens": 34,
    "cost": 0.020863,
    "elapsed_seconds": 0.31,
    "row_estimate": null,
    "cardinality_estimate": null,
    "primary_keys": [],
    "observed_key_count": 0,
    "marginal_key_yields": [1],
    "incomplete_key_records": 0,
    "gaps": [],
    "stop_reasons": [],
    "requested_attrs": ["country", "accession_year"],
    "record_origins": [["0.0"]],
    "accounting_known": false
  }
}
```

`cost` here is a conservative ledger charge rather than a measurement, which is what `accounting_known: false` says. Under a ceiling, a GPT-6 Luna response retains at least its reservation; a higher usage bound raises the charge.

An agent sets a ceiling on its own call with `limits.max_cost`, `limits.max_seconds`, and `limits.max_calls`. The operator can also set ceilings for every call through `BLUE_DATA_LLM_SOURCE_LIMIT_COST`, `BLUE_DATA_LLM_SOURCE_LIMIT_CALLS` and `BLUE_DATA_LLM_SOURCE_LIMIT_SECONDS`, and a call's own ceiling applies only where it is lower. A request with neither has no ceiling. An agent that has to stay inside one asks `llmdb_estimate` first and sets it.

`outcome` is on every result. An empty record list is not an error here, so an agent reads that field to tell an empty request from a model that returned nothing, a result reduction filtered away, and a run a ceiling stopped.

The client starts the MCP server over stdio. The server validates calls against its advertised schemas and translates arguments into library calls. Tool definitions and argument translation do not import the protocol SDK, so they can be tested with only the library installed. No REST interface is implemented or scheduled.

---

## Scenario 10: A person explores from a terminal

Someone wants to see what the source does before wiring it into anything. The `blue-llmdb` command prints records as a table, with a footer stating the record count, the model, the call count, the tokens, the row estimate, the cost, and the elapsed time, and a second footer naming the settings this run resolved and where each came from.

**Example:**

```bash
blue-llmdb data query "the member states of the European Union" --attr country --attr accession_year:int
```

```text
┏━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ country ┃ accession_year ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ France  │ 1958           │
│ Germany │ 1958           │
└─────────┴────────────────┘
2 records | model gpt-6-luna | 1 call | 46 tokens | row estimate unknown | $0.0000 | 0.31s
settings: llm.cache_dir=~/.blue-llmdb/cache (default), llm.model=gpt-6-luna (default), plan.max_continuations=0 (default)
```

Plain table output includes gaps and every stop reason. `--verbose` adds alignment, type, and collision details. Under `--format json` the run prints one object with three keys: `records`, `meta` and the same `settings` envelope the second footer prints. `meta` always includes `gaps`, `stop_reasons`, `requested_attrs` and the planning observations; `stopped_by` is the first stop reason when one exists.


---

## Scenario 11: The answer is larger than one completion can enumerate

A caller asks for all commercial airports in France with their IATA code and the region they serve. There are roughly a hundred and fifty. A single model call returns twenty to forty, stops at a plausible place, and reports no problem.

The default path still makes one completion. `plan.mode=partitioned` opts into bootstrap analysis, bounded breadth-first partition proposals, leaf extraction, and exact-key reduction. A large or unknown descendant estimate may trigger another split up to `plan.max_depth`; `plan.max_continuations` can ask a productive leaf again. These controls can gather more records from a large request, but they do not establish that the result is complete.

Non-flat bootstrap also infers a schema when none is supplied. With declared attributes, preserve mode keeps them and enrich mode may append inferred fields. Row estimates remain uncertain operational hints. `meta.gaps` and `meta.stop_reasons` report observed failures, discarded output, and interrupted work; an empty list does not certify completeness.

---

## Non-scenarios

The following capabilities are outside this source's scope:

- Grounding an answer against a retrieved document. Closed-book is the premise, not a gap. Cross-checking against another source in the layer is a composition the planner performs, not a feature of this source.
- Verifying that a returned claim is true. The source verifies that a record matches the shape it was asked for. Nothing in it establishes correctness, and no validator ships.
- Managing provider authentication. The provider SDK uses the credential; the source reads credential environment values only to mask diagnostics.
- A deployed distributed executor. The library exposes serializable tasks and results and runs them with bounded asynchronous concurrency in one process; process or machine distribution belongs in a service wrapper.
- Planning across sources or operators. That belongs to the operator layer, which calls this source rather than the other way round.
