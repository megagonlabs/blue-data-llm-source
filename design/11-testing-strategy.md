# LlmSource — Testing Strategy

## Overview

LlmSource uses one test tier: **unit tests**. The source has no backing store, and tests must not contact the only external system, the model provider. Each I/O boundary has a test double. The suite runs offline without a provider SDK. It currently runs manually; the proposed CI pipeline would run it on every push.

The model boundary is a structural protocol, and the provider package is imported lazily inside the call that uses it. Tests can supply a scripted client without patching a vendor SDK, and the package remains importable and testable without the provider dependency.

---

## 1. Tooling

| Tool | Purpose |
|------|---------|
| `pytest` | Test runner |
| `pytest-asyncio` | Async test support, with `asyncio_mode = "auto"` |
| `pytest-cov` | Coverage measurement, reported and not enforced |
| `jsonschema` | Tool input schema checking, in the MCP package only |

All are declared in the `dev` extra of the package that uses them. The recorded provider responses are plain JSON files replayed by a client double, as described in section 4; no recording library is used.

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = ""
pythonpath = ["src", "."]
testpaths = ["tests/unit"]

[tool.coverage.run]
source = ["src"]
```

`asyncio_mode = "auto"` means an async test needs no decorator. `addopts` is empty, so coverage is produced on request, not on every run.

---

## 2. Test Layout

Tests mirror the source layout under `lib/tests/`, split by concern where a module has several. The
map below is representative: it names boundary and contract tests a maintainer needs to find, not
every test module in the package.

```
lib/tests/
  conftest.py                  # autouse offline guard; scripted client, cache, and provider fixtures
  record_cassettes.py          # records the cassettes by hand; not collected by the suite
  cassettes/                   # four recorded provider responses and a spend receipt
  doubles/
    clients.py                 # ScriptedClient, FailingClient — satisfy LlmClientProtocol
    truncation.py              # complete, truncated, and unanswered model responses
    provider.py                # LitellmStub, chat_payload, stream_chunks, install_litellm
    cache.py                   # a cache double
    network.py                 # the offline guard, its record, and its errors
  unit/
    test_source_fetch.py       # the fetch path: prompt, parse, align, verify, dedupe, meta
    test_source_surface.py     # execute_query op dispatch, execute_query_nl, the tool path, metadata
    test_source_budget.py      # limits reaching the result, stopped_by, which clients they bind
    test_source_fixtures.py    # the worked examples from the source specification
    test_cassette_fixtures.py  # the same examples replayed from the recordings
    test_estimate.py           # the cost bound answered before a call, and the op that reaches it
    test_checks.py             # argument validation and message content
    test_types.py              # contracts and the exception hierarchy
    test_packaging.py          # blue_data is allowed; blue and blue_data_ops are not
    test_version.py            # pyproject and __version__ agree
    test_offline_guard.py      # the guard itself refuses a socket
    test_offline_coverage.py   # subprocess and database access are refused and counted
    test_source_planner.py     # facade property mapping into planning
    test_non_flat_facade.py    # non-flat facade behavior and metadata
    test_non_flat_result_facts.py # result facts shared with the adapters
    test_distribution_source_facade.py # direct-Python distribution controls and receipt
    test_composite_identity_and_cardinality.py # composite keys and soft row estimates end to end
    test_admission_budget_chain.py # planning admission under nested budgets
    test_outcome_precedence.py # partial, stopped, truncated, and empty outcomes
    test_unanswered_classification.py # failures before and after an answered extraction
    test_dropped_constraint.py # caller constraints survive planning and execution
    test_rewritten_ask.py      # analysis rewrites reach every planned leaf
    test_record_identity.py    # scalar and composite normalized identity
    test_record_provenance.py  # leaf origins survive merge and deduplication
    test_documented_surface.py # reference docs track public types and signatures
    test_mutants.py            # guards fail when selected invariants are reversed
    prompts/
      test_fetch_prompt.py     # rendering, substitution, and template safety
      test_prompt_provenance.py # every prompt section stays in the prompt module
    llm/
      test_budget.py           # reservation, settlement, derivation, row admission, concurrency
      test_pricing.py          # the bound holds for dense text; a model with no price is refused
      test_models.py           # each model's request contract, rates, provider pin, and settlement
      test_client_config.py    # accepted overrides, control keys, reserved keys, model default
      test_client_call.py      # call arguments and response extraction
      test_client_cache.py     # hit, miss, per-directory binding, zero budget consumption
      test_client_diskcache.py # the llm.cache branch against the real backend
      test_client_transport.py # retry, transport errors, malformed response shapes
      test_client_stream.py    # a streamed call: same records, same pricing, unpriced when unpriceable
      test_client_usage.py     # token, cost, latency, and cache-hit accounting
      test_function_client.py  # the llm_fn path
      test_finish_reason.py    # provider stop-reason attestation
      test_response_attestation.py # answered, truncated, and refused response facts
    cache/
      test_stores.py           # MemoryCache and FileCache against the published protocol
    planning/
      test_admission.py        # optional planning-call look-ahead
      test_analysis.py         # query analysis and typed constraints
      test_bounds.py           # worst-case call and leaf counts
      test_cardinality.py      # structured row estimates
      test_constraint_algebra.py # containment, disjointness, and coverage
      test_coordinator.py      # schedule coordination and fallback
      test_distribution_search.py # bounded rule-backed strategy selection
      test_plan.py             # recursive plan construction and guards
      test_planning_context.py # fact digests, schedules, and estimate exposure
      test_schema.py           # schema preservation, enrichment, and keys
      test_transport.py        # versioned LeafTask wire validation
    execution/
      test_mapper.py           # concurrent leaves, continuations, and retained partials
    reduction/
      test_parsing.py          # fences, accepted shapes, malformed responses
      test_aligner.py          # match order, remapping, unmapped keys, no-attribute passthrough
      test_verifier.py         # coercions, reported mismatches, deduplication
      test_coverage.py         # stop facts and observed-key coverage
      test_salvage.py          # safe recovery of complete prefixes
    compat/
      test_blue_data.py        # the inheritance, the base order, and the alias
      test_contract.py         # signature contract against the data layer base class
      test_claims_about_the_base.py # every shipped file, scanned for claims the runtime denies
```

Each test subdirectory carries an `__init__.py`, so that identically named modules in sibling directories do not collide during collection.

Platform adapter tests live with their packages, under `platform/llm-cli/tests/unit/` and `platform/llm-mcp/tests/unit/`. The MCP server's environment settings are covered by `test_deployment_settings.py` there, against a recording stand-in for the provider library. Each package's pytest configuration adds the library's source directory to the path, so both suites run against a checkout with no install step.

---

## 3. The Doubles

Five double modules cover four boundaries in `lib/tests/doubles/`: model responses, including
truncation; provider transport; cache; and the outside world blocked by the guard.

### The model

`ScriptedClient` implements `complete` and `acomplete`, returns queued responses in order, and records the prompts it was given. `FailingClient` raises. Neither needs patching, because injection is structural: the source accepts any object with the two methods, and nothing checks its class.

```python
@pytest.fixture
def scripted() -> ScriptedClient:
    return ScriptedClient()
```

These doubles support several test needs. They record prompts, allowing tests to check how a request, its attributes, and its context were rendered without embedding prompt text. They return text rather than provider payloads, keeping parsing tests separate from client tests. They can raise errors, allowing tests to exercise reservation handling and error propagation without a network. `ScriptedClient` declares `Capability.ALL` and claims against the supplied budget at its configured cost, so it acts as a budget-aware client for ceiling tests. A separate double declares no capabilities to exercise the refusal path.

A plain callable covers the `llm_fn` path, which reaches the model through `FunctionClient` rather than through an injected client.

### The provider transport

`DefaultLlmClient` is below the client protocol, so a scripted client cannot reach it. `LitellmStub` stands in for the provider module and is installed over the lazy import, which is exactly the seam the lazy import creates. It records the call arguments it received, returns a constructed chat payload, and can be told to fail.

Because the import is lazy and the stub replaces it, the client's whole surface is testable with the provider package absent. The manual gate uses that configuration today, and the proposed CI pipeline keeps it.

Response-shape defences use constructed payloads, not recordings, because the interesting cases are the ones a provider produces rarely: a response with no `choices` list, an empty `choices` list, and a choice with no message content. Each must raise a `TransportError` naming the shape.

The same stub answers a call that asked for a stream, yielding the chunks it was built with through an ordinary iterator on the synchronous path and an asynchronous one on the other. It also carries the chunk assembler the client calls. The assembler always attaches a usage block, counting the assembled text itself when the chunks carried none, because the real one does. A double that left usage out would let a client that priced the assembled block pass here and settle an invented figure against a real provider. Removing this behavior is enough to stop the accounting tests from detecting a regression in the client's handling of it.

### The cache

A dictionary-backed double stands in for a caller-supplied store, so cache hits, misses, and per-directory binding are asserted without writing to disk. The two shipped stores are tested as production code in `unit/cache/`, not used as doubles.

### The outside world

An autouse guard replaces network, subprocess, and database entry points for every test in the tree, raises on any attempt, and counts the attempts by class. The fixture asserts at the end of each test that all three counts are zero, so a test that swallowed the resulting exception still fails. Two markers open narrow holes in it: `expects_offline_violation` for the tests that must trip the guard to prove it works, and `uses_local_store` for the tests of the `llm.cache` branch, whose backend is a local sqlite file rather than a service. Remote drivers stay blocked under both.

---

## 4. The Cassettes

Four provider responses are recorded once and committed under `lib/tests/cassettes/`. A receipt beside them states the model, the per-cassette cost, and the ceiling the recording round ran under. They cover the four worked examples the source specification gives, and `test_cassette_fixtures.py` replays them through the real pipeline: real prompt, real parse, real alignment, real verification.

The recorder is `lib/tests/record_cassettes.py`, which the suite does not collect. It refuses to start without a budget, stops before a call that would cross its ceiling, writes the receipt, and stores responses only, so no request body and no credential reaches disk. A test asserts that last property directly by scanning each cassette for credential markers.

These tests assert shape and behaviour and never the fact. The recorded model named a different president than the specification's example does, because a model answers from its own knowledge and that knowledge has a date on it. Asserting the name would make the suite fail on a re-record for a reason that has nothing to do with this library, and would quietly treat a probabilistic source as a factual one.

---

## 5. Budget Tests

The budget is the component whose correctness a caller cannot observe until it fails to protect them, so it gets direct tests, not incidental coverage.

`Budget` accepts a clock at construction, so the time axis is tested by advancing a fake clock rather than by waiting.

```python
def test_max_seconds_stops_the_run_once_the_clock_passes_the_limit():
    clock = FakeClock()
    budget = Budget(Limits(max_seconds=30.0), clock=clock)
    budget.reserve()
    clock.advance(29.0)
    budget.reserve()
    clock.advance(1.0)
    with pytest.raises(BudgetExceeded) as exc:
        budget.reserve()
    assert exc.value.limit == "max_seconds"
```

Each axis gets the same three tests: a run that stays under it completes, a run that would cross it raises naming that axis, and the name reaches `ExecMeta.stopped_by` on the returned result. A ceiling handed in per call is asserted to bind that call and not the next, since a limit that leaked forward would be a ceiling nobody configured. Seven further properties are asserted directly:

- Reservation precedes spend: a call refused by the budget never reaches the client, verified by asserting that the double recorded no prompt.
- A failed call keeps its reservation at the bound, so a provider that was contacted and raised consumes a call and a retry sequence cannot outrun the ceiling. Only a failure before the provider is reached is uncharged, which is why the client does all local work before reserving.
- A cost ceiling refuses an attempt whose bound would cross it, before the client is asked. This is the property the previous accounting could not hold: the money was already gone by the time anything noticed.
- A settled call releases what its bound overshot by, so an over-claim costs headroom during the call and nothing after it.
- A cache hit consumes no call, no tokens and no cost, and the usage record marks it; admission still checks elapsed time, on both entry points. That a hit's rows still count against `max_rows` is asserted through the fetch path rather than against the client directly
- A derived child spends from what its parent has left, and a child given a more generous ceiling than its parent holds still reports the parent's headroom.
- A malformed ceiling is refused where it is configured rather than at the call: a wrong type, a `bool`, a negative value, or a non-finite value raises `ConfigError`, and so does an unknown `limits.*` key.

The cost bound has its own test file because both the cost ceiling and `estimate` depend on it. Two tests check its properties: a bound for a prompt with the token count of a recorded call exceeds that call's cost, and a bound for text that tokenizes close to one token per character (short words, punctuation, non-Latin script) is not exceeded by the text. The tests also check that an unlisted model, a half-supplied price pair, and an absent, zero, negative, boolean, string, or non-finite output cap each raise `ConfigError`.

Three tests cover the client boundary. A client that declares no capabilities is refused a configured call, cost, or time ceiling; the same run succeeds with only `limits.max_rows`, since the source counts records itself. The tests also check that each ceiling has the same effect on every path whose client declares that axis. A concurrent pair of fetches through one reused client must report its own tokens and model in each result. The per-call `usage_sink` list provides this isolation; a cursor into shared history would not.

Two tests cover concurrency. The first runs several callers against one budget with a call ceiling and checks that the number admitted equals the ceiling. The second checks that the limit check and claim happen under the same lock, using a deliberately slow clock. `reserve` reads the clock between checking the call ceiling and claiming the attempt. The slow clock widens that interval from nanoseconds to milliseconds; if the lock were released between the steps, a second caller could enter during that interval and the test would detect it.

Row admission has its own concurrent test. It does not use the widened clock, because `add_rows` never reads the clock: its race is over the row count alone, so the barrier is what makes the callers overlap.

`test_estimate.py` covers the estimate path. It checks that the estimate contacts no provider by asserting that a scripted client recorded no prompt. An unpriceable model must return `bounded` as false with a reason rather than raise. The test also compares an estimate with the cost of a fetch using the same arguments. This checks the estimate against a real run rather than against another call to the same pricing function, which would only show that the function agrees with itself. No other test would detect a mismatch between the estimate and the ceiling.

---

## 6. Configuration Tests

Every configuration key has a test proving that changing it changes observable behaviour. A setting that is accepted and ignored produces runs whose recorded configuration does not describe what happened, which is worse than a setting that does not exist.

| Key | Observable effect asserted |
|-----|---------------------------|
| `llm.model` | Appears in the call arguments and in `ExecMeta.model` |
| `llm.reasoning_effort` | Forwarded within each reasoning model's accepted set, defaulted from its table entry, and refused before any reservation for an effort outside the set or for a model that accepts none |
| `llm.temperature`, `llm.max_tokens`, `llm.timeout` | Forwarded through the selected model's supported arguments; a temperature the model's entry rules out at the effective effort is refused before any reservation |
| `llm.stream` | Both values are forwarded; a streamed call returns the records the same unstreamed call does, is priced when the provider sends usage, and keeps its reservation when it does not |
| `llm.cache`, `llm.cache_dir` | Second identical call is a cache hit; a different directory is a miss |
| `llm.max_parallel` | Bounds in-flight calls under concurrent fetches |
| `llm.rate_limit` | Refused with `ConfigError`, naming `llm.max_parallel` instead |
| `llm.price_per_input_token`, `llm.price_per_output_token` | Make an otherwise unpriced model boundable; either key without the other is refused |
| `limits.*` and `max_records` | Stop a run and name themselves in `stopped_by` |
| `connection.variant` | Reported by the `variant` property and by `LLM.MODELS` |
| `plan.mode`, `plan.partition_key`, `plan.max_depth`, `plan.max_partitions`, `plan.target_rows_per_leaf`, `plan.max_leaves` | Change or bound the accepted task tree; a refused expansion keeps the parent leaf intact |
| `plan.max_continuations`, `plan.max_exclusion_bytes`, `plan.max_excluded_keys` | Change extraction call count or the whole-key exclusion block while respecting both caps |
| `plan.key`, `plan.keys` | Change scalar or composite identity, merge, and continuation exclusions |
| `plan.exact_rows` | Stops a single leaf at the trusted count; a multi-leaf plan reports `exact_rows_not_leaf_bound` instead |
| `plan.schema_mode`, `plan.planning_order` | Change accepted schema facts or the closed schedule and its stage counts |
| `plan.expose_estimate` | Changes only the extraction prompt; planning facts, topology, and affordability stay the same |
| `plan.leaf_prompt` | Selects the `keyed-leaf-fetch-1` or `keyed-leaf-fetch-2` leaf contract and its wire version; the default sends byte-identical prompts, both contracts execute, and the published call bounds do not move |
| `plan.child_wording` | `canonical` changes only the children's retrieval wording, derived from the root request and validated filters; `parent` sends byte-identical prompts |
| `plan.filter_enforcement` | `flag` keeps and counts contradicting records; `drop` removes them at the leaf before reduction, so they neither merge fields into a kept record nor appear in its origins |
| `plan.range_filters` | Span values on an integer column become inclusive `Range` children that serialize, render and obey as ranges; other columns, caller-declared domains and the switch off keep exact text; overlapping or reversed spans refuse the proposal |
| `plan.partition_guardrails` | Proposal prompts carry the `partition-2` rules and a count calibrated against each slice's estimate; a numeric key with bucketed values is refused with its reason in `meta.gaps`; off sends the 2.1.0 proposal prompt |
| `plan.split_policy`, `plan.split_threshold_rows` | `always` sends byte-identical prompts; `evidence` probes the root once, splits only on an output-allowance stop or a shortfall, merges root records under origin `root`, seeds children with bounded probe keys, and stays within bounds that charge the probe in every accessor |
| `plan.distribution_search`, `plan.identity_partitions`, `plan.max_strategy_candidates` | Change the bounded rule-backed candidates, accepted split, and optional planning receipt |

An unknown `llm.*` key is asserted to raise `ConfigError` whose message lists the accepted names, and control keys are asserted not to appear in the provider call arguments.

---

## 7. The Base Class Tests

Four test files cover inheritance from `DataSource`, signature compatibility, dependency boundaries, and documentation claims.

`test_blue_data.py` verifies inheritance, confirms that `BlueDataLlmSource` aliases `LlmSource`, and checks that the query surface and virtual hierarchy resolve locally. Six transaction methods are listed in the contract test's `OUT_OF_SCOPE` map with a reason for each. Tests verify that upstream refuses those methods and that exemptions disappear when their corresponding methods do. Any new upstream method must be classified before the suite passes.

`test_contract.py` compares signatures with the upstream `DataSource` using `inspect.signature` and records each deliberate variance. It covers untyped signature drift and methods that resolve to the base rather than to this class. The test prints the path of the inspected `blue_data` package so the result can be tied to the installed dependency.

`test_packaging.py` fails if the package imports `blue` or `blue_data_ops`. This keeps the source dependent on the data layer without coupling it to the orchestration runtime.

`test_claims_about_the_base.py` scans shipped `.md`, `.py`, and `.toml` files for statements contradicted by runtime behavior. Each pattern records why the statement is false and names the test that establishes the current behavior. If a statement becomes true, its pattern is updated in the same change.

---

## 8. Adapter Tests

Each platform package tests its own translation layer and nothing below it. Both replace the single source-construction function in their package to inject a scripted client, which is the only seam either needs.

**CLI.** Tests cover all twelve commands through Click's runner, plus the four layers under them: the option translation and the settings envelope, the profile store and its precedence chain, the renderer and its control-character and formula guards, and the error-to-exit-code mapping for each classified error kind. The profile tests run against a throwaway CLI home, and a guard fails the run if the real `~/.blue-llmdb` appears or disappears. One test checks the shape of the CLI itself (the command tree, the version, that every result command accepts all four formats) because those are claims about every command at once. Another reads the CLI README, extracts every `blue-llmdb` command from its shell blocks, and runs them in the order they appear against one throwaway home, since the README is a walkthrough and the sequence has to work. Install and tooling blocks are not executed. A command that stops working there fails the suite rather than surviving as a false claim about the product.

**MCP.** Tests cover the five tool handlers, ceilings through the tool path, unknown-tool dispatch, and the hand-written input schemas. A separate test verifies that tool definitions and argument translation do not import the protocol SDK. Another drives the server's `call_tool` coroutine, reconstructed from source when the SDK is absent, to cover async dispatch at the transport boundary.

---

## 9. The Offline Guarantee

The suite makes no network call, spawns no subprocess, and touches no database. An autouse guard replaces the entry points for each class, raises on any attempt, records attempts, and fails when a count is non-zero. The guard has one self-test for each class.

The one deliberate hole is the `llm.cache` branch, whose backend is a local sqlite file. Those tests carry a marker that permits sqlite and nothing else; the remote drivers stay blocked.

The suite therefore runs from a checkout without credentials or paid provider calls.

---

## 10. Coverage

Coverage is measured over `src` via `pytest-cov` when requested. No minimum threshold is enforced,
and no merge is gated on a percentage. A future CI pipeline may report it without changing that
policy.

What is gated is contract-surface coverage: every public member is either tested or explicitly recorded as out of scope with a reason. That question, "is this surface exercised", is the one a coverage number is a poor proxy for, and answering it directly is cheaper than arguing about a threshold.

---

## 11. Running Tests

Each package runs its own suite from its own directory, since each carries its own pytest configuration.

```bash
# Library
cd lib && python3 -m pytest tests/unit -q

# Library, with coverage
cd lib && python3 -m pytest tests/unit --cov=src --cov-report=term-missing

# CLI
cd platform/llm-cli && python3 -m pytest tests/unit -q

# MCP server
cd platform/llm-mcp && python3 -m pytest tests/unit -q

# Format and lint, from any package directory
python3 -m ruff format --check . && python3 -m ruff check .
```

The compatibility contract test runs everywhere, because `blue-data` is a core dependency and is always present.
