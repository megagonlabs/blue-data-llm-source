# LlmSource — Testing Strategy

## Overview

LlmSource uses a single test tier: **unit tests**. There is no integration tier, because there is no store to integrate with and the only external system is a model provider, which a test must not reach. Every I/O boundary is doubled. The suite runs offline, on every push in CI, and passes with no provider SDK installed.

That last property is a design consequence rather than a testing convenience. The model boundary is a structural protocol and the provider package is imported lazily inside the call that uses it, so a test substitutes a scripted client rather than patching a vendor SDK, and the whole package remains importable and exercisable without one.

---

## 1. Tooling

| Tool | Purpose |
|------|---------|
| `pytest` | Test runner |
| `pytest-asyncio` | Async test support, with `asyncio_mode = "auto"` |
| `pytest-cov` | Coverage measurement, reported and not enforced |
| `jsonschema` | Tool input schema checking, in the MCP package only |

All are declared in the `dev` extra of the package that uses them. No recording library is declared or used. Recorded provider responses exist, and they are plain JSON files replayed by a client double, which is described in section 4.

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = ""
pythonpath = ["src", "."]
testpaths = ["tests/unit"]

[tool.coverage.run]
source = ["src"]
```

`asyncio_mode = "auto"` means an async test needs no decorator. `addopts` is empty, so coverage is produced on request rather than on every run.

---

## 2. Test Layout

Tests mirror the source layout under `lib/tests/`, split by concern where a module has several.

```
lib/tests/
  conftest.py                  # autouse offline guard; scripted client, cache, and provider fixtures
  record_cassettes.py          # records the cassettes by hand; not collected by the suite
  cassettes/                   # four recorded provider responses and a spend receipt
  doubles/
    clients.py                 # ScriptedClient, FailingClient — satisfy LlmClientProtocol
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
    prompts/
      test_fetch_prompt.py     # rendering, substitution, and template safety
    llm/
      test_budget.py           # reservation, settlement, derivation, row admission, concurrency
      test_pricing.py          # the bound holds for dense text; a model with no price is refused
      test_client_config.py    # accepted overrides, control keys, reserved keys, model default
      test_client_call.py      # call arguments and response extraction
      test_client_cache.py     # hit, miss, per-directory binding, zero budget consumption
      test_client_diskcache.py # the llm.cache branch against the real backend
      test_client_transport.py # retry, transport errors, malformed response shapes
      test_client_stream.py    # a streamed call: same records, same pricing, unpriced when unpriceable
      test_client_usage.py     # token, cost, latency, and cache-hit accounting
      test_function_client.py  # the llm_fn path
    cache/
      test_stores.py           # MemoryCache and FileCache against the published protocol
    reduction/
      test_parsing.py          # fences, accepted shapes, malformed responses
      test_aligner.py          # match order, remapping, unmapped keys, no-attribute passthrough
      test_verifier.py         # coercions, reported mismatches, deduplication
    compat/
      test_blue_data.py        # the inheritance, the base order, and the alias
      test_contract.py         # signature contract against the data layer base class
```

Each test subdirectory carries an `__init__.py`, so that identically named modules in sibling directories do not collide during collection.

Platform adapter tests live with their packages, under `platform/cli/tests/unit/` and `platform/mcp/tests/unit/`. Each package's pytest configuration adds the library's source directory to the path, so both suites run against a checkout with no install step.

---

## 3. The Doubles

Four boundaries, four doubles, all in `lib/tests/doubles/`. The model, the provider transport, the cache, and the outside world the guard closes off.

### The model

`ScriptedClient` implements `complete` and `acomplete`, returns queued responses in order, and records the prompts it was given. `FailingClient` raises. Neither needs patching, because injection is structural: the source accepts any object with the two methods, and nothing checks its class.

```python
@pytest.fixture
def scripted() -> ScriptedClient:
    return ScriptedClient()
```

Four properties of these doubles matter to the tests built on them. They record the prompt, so a test can assert what a request, its attributes, and its context rendered to without owning the prompt text. They return text rather than a provider payload, so parsing tests and client tests stay separate. They can raise, so reservation handling and error propagation are exercised without simulating a network. And `ScriptedClient` declares `Capability.ALL` and claims against the budget it is handed on the call, priced at whatever cost it was built with, which is what makes it a budget-aware client rather than an opaque one and is how the ceiling tests reach the client boundary at all. A separate double declaring no capabilities stands in for the opaque case, and it is what the refusal path is tested against.

A plain callable covers the `llm_fn` path, which reaches the model through `FunctionClient` rather than through an injected client.

### The provider transport

`DefaultLlmClient` is below the client protocol, so a scripted client cannot reach it. `LitellmStub` stands in for the provider module and is installed over the lazy import, which is exactly the seam the lazy import creates. It records the call arguments it received, returns a constructed chat payload, and can be told to fail.

Because the import is lazy and the stub replaces it, the client's whole surface is testable with the provider package absent, which is how CI runs it.

Response-shape defences use constructed payloads rather than recordings, because the interesting cases are the ones a provider produces rarely: a response with no `choices` list, an empty `choices` list, and a choice with no message content. Each must raise a `TransportError` naming the shape.

The same stub answers a call that asked for a stream, yielding the chunks it was built with through an ordinary iterator on the synchronous path and an asynchronous one on the other, and it carries the chunk assembler the client calls. One property of that assembler is deliberate rather than incidental: it always attaches a usage block, counting the assembled text itself when the chunks carried none, because the real one does. A double that simply left usage out would let a client that priced the assembled block pass here and settle an invented figure against a real provider, so the double reproduces the trap rather than smoothing it away. Removing that behaviour is enough on its own to stop the accounting tests failing when the client's handling of it is reverted, which is what makes it load-bearing.

### The cache

A dictionary-backed double stands in for a caller-supplied store, so cache hits, misses, and per-directory binding are asserted without writing to disk. The two shipped stores are tested as production code in `unit/cache/`, not used as doubles.

### The outside world

An autouse guard replaces network, subprocess, and database entry points for every test in the tree, raises on any attempt, and counts the attempts by class. The fixture asserts at the end of each test that all three counts are zero, so a test that swallowed the resulting exception still fails. Two markers open narrow holes in it: `expects_offline_violation` for the tests that must trip the guard to prove it works, and `uses_local_store` for the tests of the `llm.cache` branch, whose backend is a local sqlite file rather than a service. Remote drivers stay blocked under both.

---

## 4. The Cassettes

Four provider responses are recorded once and committed under `lib/tests/cassettes/`, alongside a receipt stating the model, the per-cassette cost, and the ceiling the recording round ran under. They cover the four worked examples the source specification gives, and `test_cassette_fixtures.py` replays them through the real pipeline: real prompt, real parse, real alignment, real verification.

The recorder is `lib/tests/record_cassettes.py`, which the suite does not collect. It refuses to start without a budget, stops before a call that would cross its ceiling, writes the receipt, and stores responses only, so no request body and no credential reaches disk. A test asserts that last property directly by scanning each cassette for credential markers.

These tests assert shape and behaviour and never the fact. The recorded model named a different president than the specification's example does, because a model answers from its own knowledge and that knowledge has a date on it. Asserting the name would make the suite fail on a re-record for a reason that has nothing to do with this library, and would quietly treat a probabilistic source as a factual one.

---

## 5. Budget Tests

The budget is the component whose correctness a caller cannot observe until it fails to protect them, so it gets direct tests rather than incidental coverage.

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

The bound itself gets its own file, because it is the number both the cost ceiling and `estimate` rest on, and its previous version was wrong in a way no ceiling test would catch. Two tests state the property directly: a bound computed for a prompt of a recorded call's token count still exceeds what that call cost, and a bound for text that tokenizes close to one token per character — short words, punctuation, non-Latin script — is not exceeded by it. The refusals are asserted too: an unlisted model, a half-supplied price pair, and an output cap that is absent, zero, negative, boolean, a string, or non-finite each raise `ConfigError`.

The client boundary gets three tests of its own, because the ceiling means nothing without them. A client that declares no capabilities is refused a configured call, cost, or time ceiling, and the same run with only `limits.max_rows` succeeds, since the source counts the records itself. The same ceiling is asserted to read the same on every path that declares the axis, because a limit that holds for one client and not another is a default rather than a limit. And a concurrent pair of fetches through one reused client is asserted to report its own tokens and its own model on each result, which is the property the per-call `usage_sink` list exists for and which a cursor into a shared history could not hold.

Concurrency gets two tests rather than one, and the second is the interesting one. The first runs several callers against one budget with a call ceiling and asserts that the number admitted equals the ceiling exactly. The second asserts that the check and the claim happen under the same lock, using a clock that deliberately takes its time. `reserve` reads the clock between checking the call ceiling and claiming the attempt, and a slow clock widens that window from nanoseconds to milliseconds. If the lock were released between the two steps, the widened window would let a second caller through, and the test would see it.

Row admission has its own concurrent test. It does not use the widened clock, because `add_rows` never reads the clock: its race is over the row count alone, so the barrier is what makes the callers overlap.

`test_estimate.py` covers the other end of the same number. Three of its assertions are the ones that matter. The estimate contacts nothing, verified by handing the source a scripted client and asserting it recorded no prompt. An unpriceable model comes back with `bounded` false and a reason rather than raising, since a caller asking what a request might cost is entitled to an answer of "not knowable here". And the estimate bounds what a fetch of the same arguments actually spends, asserted as an inequality against a real run rather than by recomputing the same pricing call: comparing an estimate against another call of the function that produced it proves only that an implementation matches itself: an estimate a caller trusts and a ceiling the run enforces have to be one figure, or a request that sizes inside a ceiling is refused when it runs, and that is a divergence no other test in the suite would notice.

---

## 6. Configuration Tests

Every configuration key has a test proving that changing it changes observable behaviour. A setting that is accepted and ignored produces runs whose recorded configuration does not describe what happened, which is worse than a setting that does not exist.

| Key | Observable effect asserted |
|-----|---------------------------|
| `llm.model` | Appears in the call arguments and in `ExecMeta.model` |
| `llm.temperature`, `llm.max_tokens`, `llm.timeout` | Forwarded to the provider call arguments |
| `llm.stream` | Both values are forwarded; a streamed call returns the records the same unstreamed call does, is priced when the provider sends usage, and keeps its reservation when it does not |
| `llm.cache`, `llm.cache_dir` | Second identical call is a cache hit; a different directory is a miss |
| `llm.max_parallel` | Bounds in-flight calls under concurrent fetches |
| `llm.rate_limit` | Refused with `ConfigError` naming the v2.0 deferral |
| `limits.*` and `max_records` | Stop a run and name themselves in `stopped_by` |
| `connection.variant` | Reported by the `variant` property and by `LLM.MODELS` |

An unknown `llm.*` key is asserted to raise `ConfigError` whose message lists the accepted names, and control keys are asserted not to appear in the provider call arguments.

---

## 7. The Base Class Tests

`LlmSource` inherits the data layer's `DataSource`, and three files cover what that gives, what it does not, and the line it must not cross.

`test_blue_data.py` asserts the inheritance itself, that `BlueDataLlmSource` is the same object as `LlmSource` rather than a subclass of it, and that the query surface and the virtual hierarchy answer from this class rather than from the base. That last assertion is why the file exists. The companion contract test does not require every upstream method to resolve locally: six transaction methods are listed in its `OUT_OF_SCOPE` map with the reason for each, one test asserts that upstream really does refuse those for this source rather than answering empty, and another fails if an exemption outlives the method it exempts. Anything upstream adds that is on neither list fails the test until someone decides which it is, which is what makes an inherited addition impossible to ship unreviewed. Most `DataSource` methods return an empty result rather than raising, so one left unoverridden makes the source report no databases and return no records without failing anywhere, which is the quietest way a source can be broken.

`test_contract.py` compares signatures against the upstream `DataSource` with `inspect.signature`, parameter by parameter, alongside a table of deliberate variances each carrying its reason. Inheritance turns most upstream drift into a type error, so what this covers is the rest: a signature that still type-checks because the base method is untyped, and a method that resolves to the base rather than to this class. It prints the path of the `blue_data` it compared against, because an installed copy can be older than a checkout and confirming against a version nobody runs is worse than not checking.

`test_packaging.py` guards the other side of the same line. It scans the package for an import of `blue` or `blue_data_ops` and fails on either, because depending on the data layer this source belongs to is one thing and depending on the orchestration runtime that queries it would make the source unusable outside that runtime. A boundary that lives only in a document holds until someone adds an import.

---

## 8. Adapter Tests

Each platform package tests its own translation layer and nothing below it. Both replace the single source-construction function in their package to inject a scripted client, which is the only seam either needs.

**CLI.** Tests cover the `fetch` and `info` commands through Click's runner, the option translation in isolation, and the error-to-exit-code mapping for each classified error kind. One further test reads the CLI README, extracts every `llm` command from its shell blocks, and runs each one against a scripted client. Install and tooling blocks are not executed. A command that stops working there fails the suite rather than surviving as a false claim about the product.

**MCP.** Tests cover the three tool handlers, the ceilings binding through the tool path, the dispatcher's behaviour on an unknown tool name, and the input schemas, which are validated with `jsonschema` because they are hand-written and are the contract an agent actually reads. A separate test asserts the import boundary: the module holding the tool definitions and the translation must not import the protocol SDK, which is what keeps that module testable without it.

---

## 9. The Offline Guarantee

The suite makes no network call, spawns no subprocess, and touches no database. All three are asserted rather than assumed: an autouse guard replaces the entry points for each class, raises on any attempt, counts what was attempted, and fails the test if any count is non-zero. Guarding sockets alone would leave a suite free to shell out or open a database and still call itself offline. The guard has its own tests, one per class, because a guard that silently stopped working would take the guarantee with it.

The one deliberate hole is the `llm.cache` branch, whose backend is a local sqlite file. Those tests carry a marker that permits sqlite and nothing else; the remote drivers stay blocked.

The guarantee is what makes the suite runnable by anyone with a checkout, on any machine, with no credential. It also removes the incentive to skip a test because running it costs money, which is how a suite quietly stops covering its own boundaries.

---

## 10. Coverage

Coverage is measured over `src` via `pytest-cov` and reported in CI output. No minimum threshold is enforced, and no merge is gated on a percentage.

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
cd platform/cli && python3 -m pytest tests/unit -q

# MCP server
cd platform/mcp && python3 -m pytest tests/unit -q

# Format and lint, from any package directory
python3 -m ruff format --check . && python3 -m ruff check .
```

The compatibility contract test runs everywhere, because `blue-data` is a core dependency and is always present.
