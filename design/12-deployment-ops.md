# LlmSource — Deployment / Ops

## Overview

This repository builds three packages. **All three are currently installed from a checkout** because none is available on PyPI or an internal package index. Current installation instructions are in `docs/installation.md`, `lib/README.md`, and the interface READMEs. This document also describes the planned distribution process.

| Package | Contents | Entry point |
|---------|----------|-------------|
| `blue-data-llm-source` | The Python library, for embedding in applications, planners, and operators | — |
| `blue-data-llm-cli` | The `blue-llmdb` command | `blue-llmdb` |
| `blue-data-llm-mcp` | The MCP server, speaking over stdio | `blue-data-llm-mcp` |

Both interface packages depend on the library and add only their interface framework. Python callers install the library, command-line users install the library and CLI package, and MCP clients install the library and MCP package.

There is no Docker image. The library holds no state, opens no port, and starts no process. The MCP server uses stdio and is started by its client. No current interface requires a network-reachable service.

Operations require no backend provisioning, data migration, or backups. Operators manage provider credentials, spending ceilings, and the process that embeds the library.

`blue-data` supplies the inherited `DataSource` base class and also installs `pandas`, `numpy`, and `jsonpath-ng`. Because `blue-data` is not on a public package index, install it from a checkout before this package.

---

## 1. Versioning

The package follows [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`).

- **PATCH**: bug fixes, no interface changes
- **MINOR**: backwards-compatible new features
- **MAJOR**: breaking changes to the Python library API, to the source query surface, or to the result contract

A prompt change is treated as at least MINOR because it can change the source's response to an unchanged request. Callers with recorded expectations need to know about the change.

Each package declares its version in `pyproject.toml` and as `__version__` in its top-level module. A test compares all three pairs. Another test scans Markdown examples of `blue-llmdb metadata inspect` and `llmdb_inspect_metadata` and verifies that they match the exported library version. The declarations are still synchronized manually.

---

## 2. PyPI Packages

`blue-data-llm-source` exposes the Python library. Its runtime dependencies are `blue-data>=0.1.0`, which supplies the `DataSource` base class the source inherits, and `tenacity>=9.0.0`. The provider transport and the `diskcache` backend are extras.

The commands below describe the planned index route. Today neither these packages nor `blue-data` is on an index, so install from a checkout: `lib/scripts/install.sh` puts `blue-data` in place first, and the two interface installers route through that one.

```
pip install blue-data-llm-source                 # library; brings blue-data and tenacity
pip install 'blue-data-llm-source[llm]'          # + provider transport and disk cache
pip install 'blue-data-llm-source[dev]'          # + test, lint, type-check, build, publish tooling
pip install 'blue-data-llm-source[all]'          # currently equivalent to [llm]
```

There is no `blue-data` extra. It gated the compatibility adapter while the dependency was optional, and it is removed, not kept as a no-op.

`blue-data` also installs `pandas`, `numpy`, and `jsonpath-ng`. It is not available on a public package index, so install it from a checkout before installing this package.

The base install is still usable without a provider. A caller that injects its own client, or passes an `llm_fn`, never needs the `llm` extra, and its process acquires no provider SDK.

The two interface packages pull the library from the index, and from a checkout each has an
installer that puts the prerequisite in place first:

```
pip install blue-data-llm-cli                    # the `blue-llmdb` command, from an index
pip install blue-data-llm-mcp                    # the MCP server, from an index

platform/llm-cli/scripts/install.sh                  # or from a checkout
platform/llm-mcp/scripts/install.sh
```

An MCP client is pointed at the server the usual way, by naming the console entry point as the command to run.

`lib/scripts/` carries the three commands that produce and publish the artifact, and both interface packages carry the same three:

| Script | Action |
|--------|--------|
| `build.sh` | `python -m build`, producing a wheel and an sdist |
| `install.sh` | Editable install with the `dev` and `llm` extras, for development |
| `publish.sh` | `twine upload dist/*` |

---

## 3. Storage Backends

None. LlmSource does not bundle, require, or manage a database, a cache server, or a message broker, and it will not start.

The optional response cache is the only form of storage, and it is local. Enabling `llm.cache` writes a `diskcache` cache to a local directory. The default is `.llm_cache` for a caller embedding the library and `cache` under the CLI home for `blue-llmdb`; operators should place it on a writable path and set a retention policy. The library also ships two stores that use only the standard library: `MemoryCache`, which operates in process, and `FileCache`, which writes one JSON file per entry in a directory. Both are passed through the `cache_store` property. `FileCache` has the same operational shape as the `llm.cache` directory. A caller that wants a shared cache supplies its own store through the same property. The library does not depend on a particular backend or open a connection on the caller's behalf.

Neither shipped store expires an entry. `MemoryCache` evicts least-recently-used entries past its ceiling, and `FileCache` grows until something clears it, so a long-lived deployment should treat the cache directory as it would any other unmanaged local cache.

---

## 4. Configuration

Library configuration uses **properties** passed to the constructor or an individual call, not process-wide environment variables. This allows two `LlmSource` instances in the same process to use different budgets.

Per-call configuration has operational significance. Each property below can be passed to one request through `properties` on `fetch` or `optional_properties` on the query surface. It is merged over the instance properties for that request and is not written back. One source can therefore serve a cheap speculative question and an expensive authorised one under different ceilings, without a second source or a period in which the source has the wrong configuration.

The full property table is in [09-api-interface-spec.md](09-api-interface-spec.md). The operationally significant ones:

| Property | Operational meaning |
|----------|--------------------|
| `llm.model` | Which model answers, and therefore what a call costs. The model's entry in the package's table fixes its request shape and the rates its bound is built from |
| `llm.max_parallel` | Concurrency bound per event loop; the main lever against provider rate limiting |
| `llm.rate_limit` | Not supported. Refused by name with `ConfigError`. Use `llm.max_parallel`, or throttle above the library |
| `llm.stream` | Set it when the provider configuration or proxy in front of you requires a stream. It buys nothing else: a fetch has no answer until the last chunk lands, and a stream the provider sends no token counts with cannot be priced, so the run reports `accounting_known` false and its cost ceiling is enforced against the reserved bound |
| `llm.cache`, `llm.cache_dir` | Local response cache and where it is written |
| `llm.max_tokens` | The output allowance, and therefore the floor of every cost claim and of every estimate; the main lever on whether a cost ceiling binds or refuses |
| `llm.price_per_input_token`, `llm.price_per_output_token` | Rates for a model the shipped price table does not carry; both required together, and consumed locally rather than forwarded |
| `limits.max_cost` | Spending ceiling per run. An upper bound is claimed before an attempt, so one that would cross the ceiling is refused. A measured cost settles the claim; otherwise [the cost bound](../lib/README.md#the-cost-bound) sets the charge |
| `limits.max_calls`, `limits.max_seconds`, `limits.max_rows` | The other three run ceilings |

All five `limits.*` keys are also the ones to hand in per request. A ceiling set on the source is the deployment's default, and a ceiling passed with a request is that request's own.

### Environment variables

| Variable | Read by | Required | Description |
|----------|---------|----------|-------------|
| Provider credential variable | The provider SDK, never this package | Yes, when the default client reaches a provider | The library does not read, store, log, or forward it |

That is the whole list *for the library*. The library names no environment variable of its own and reads none. The model is selected through the `llm.model` property, and the CLI exposes it as `--model`.

**Which layer owns configuration.** The library is embedded in a caller's process, where process-wide environment variables cannot express different budgets for two sources; each variable name has only one value. Properties can set different values for each source. An adapter has different needs. `blue-llmdb` is a short-lived process per invocation, with one user, and requiring a person to retype a model name and four ceilings for every command would create a configuration problem the library cannot solve. The CLI therefore reads a documented `BLUE_DATA_*` set, resolves it against its own profiles, and passes the result as properties. This preserves the library's configuration rule. The full parameter list and precedence chain are in [09-api-interface-spec.md](09-api-interface-spec.md). Whoever starts the MCP server configures it, so it reads the same names from its environment on every call, the ten `llm.*` settings (`BLUE_DATA_LLM_MODEL` and nine `BLUE_DATA_LLM_SOURCE_*` variables) and the three operator ceilings (`BLUE_DATA_LLM_SOURCE_LIMIT_COST`, `_LIMIT_CALLS` and `_LIMIT_SECONDS`). A caller may pass a lower ceiling per tool call but cannot raise or remove an operator ceiling; plan settings come only from the call. A malformed value fails that call as a masked `ConfigError` rather than stopping the server.

The credential belongs to the provider SDK, so this package cannot detect when it is absent as a configuration error. The call fails with a library `TransportError` and CLI exit code 5. Configuration errors that this package detects use `ConfigError` and exit code 4. When diagnosing a fresh deployment, an operator should interpret code 5 with a credentials message as "the key is missing or wrong" rather than as a transport fault.

No property, environment variable, or log line produced by this package carries a credential value. Both interface packages apply masking: error messages, log lines, tracebacks, and request-side fields in run reports pass through the library's `redact`. It masks values held in the environment under a credential's name and text that matches a key's shape. Provider SDK exceptions make this necessary. Records are not masked because they are the answer.

---

## 5. Spend Control

Provider cost is the primary operational risk. The following controls apply together.

- Every run is bounded on four axes: row count, elapsed time, API cost, and provider call count. The time axis bounds admission and cooperative waiting, not the whole run; [what it bounds](../lib/README.md#what-the-time-ceiling-actually-bounds) lists the exclusions.
- Call count, elapsed time, and cost are all claimed **before** the provider is contacted, under a lock, so a concurrent fan-out cannot cross a ceiling and report it afterwards.
- A call's price is unknown until it returns, so the cost claim is an upper bound rather than an estimate. It uses the prompt's length in UTF-8 bytes plus a framing allowance at the model's published input rate, and the full output allowance at its output rate. The bound assumes a byte-pair tokenizer consumes at least one byte of the encoded prompt per token, so no tokenizer can exceed it. A measured cost settles the claim and releases any overshoot; when the response cannot prove the charge, [the cost bound](../lib/README.md#the-cost-bound) sets it and the result reports its accounting as unknown.
- If a bound cannot be established, the ceiling is **refused** rather than approximated. A model without a published or caller-supplied price, or an `llm.max_tokens` value that is not a finite positive number, causes `limits.max_cost` to raise a `ConfigError` on the first fetch. The operator receives a refusal rather than an invented bound.
- The minimum cost bound is the full output allowance at the model's output rate and is independent of the prompt. With the default `gpt-6-luna` model and `llm.max_tokens=25000`, the minimum is about $0.01375, including OpenAI's 10% regional-processing uplift. A lower `limits.max_cost` refuses the call before provider contact. Reducing `llm.max_tokens` reduces this minimum proportionally.
- The claim is per provider attempt, so a retry sequence spends the call budget rather than escaping it, and each attempt rechecks the deadline before it runs. Retry depth narrows along the budget chain, so a fetch drawing on a caller-owned budget cannot make more attempts than that budget allows.
- The timeout handed to the provider is capped at the time the run has left, so a call started just inside a deadline cannot run for its own full timeout past it. In the async path a deadline also cancels the wait itself, which is what holds when a provider ignores the timeout it was given.
- A run stopped by limits returns a result whose `stop_reasons` names every observed axis and whose `stopped_by` retains the first. A row-only trim reports `outcome=truncated` and CLI exit 0. Another stop reports `stopped_by_limit` and exit 7 unless interrupted work also produced an answered part; that case reports `partial` and exit 8 while retaining the stop reasons.
- A cache hit consumes no provider call, no tokens and no cost, the attempt included. It is not free on every axis: rows it returns count against the row cap, and elapsed-time admission still applies.
- Local operations that can fail happen before reservation, so their failures are uncharged. The order is admit, import, claim: the bound is checked against the budget without reserving anything, so a refused request never reaches the network. The claim is made immediately before entering the provider. Claiming before the import exposed the claim to a sibling sharing the budget, which could then be refused even though the final ledger showed the allowance as free. Everything after the claim, including reading the response, runs inside one guard that resolves it. This prevents a malformed response from leaving headroom reserved while a retry proceeds. A call that reached the provider and failed retains its claim at the bound, because a ceiling that counts only successful calls would not bound spend on failed calls.
- A ceiling applies only to an axis the answering client declares through `budget_capabilities()`. The default client declares all four. A callable passed as `llm_fn` declares rows alone: the source cannot know its cost, stop it, or determine how many provider calls it makes. A call, cost, or time ceiling over that callable is therefore refused. Each configured ceiling on an undeclared axis raises a `ConfigError` on the first fetch, so a deployment that injects its own client learns at the boundary rather than from a bill. `limits.max_rows` is unaffected because the source counts the records itself.
- `LlmSource.estimate` reports the same bound for a request before it runs, and reaching it costs nothing and contacts nothing. That is the tool for sizing a ceiling against real traffic rather than against the worked example in this document, and for a service that wants to refuse an oversized request itself instead of leaving it to the budget.

An operator embedding the library in a service sets default ceilings at construction and tightens them per request. `limits.max_cost` and `limits.max_calls` on the source apply to every fetch. The same keys in `properties` on `fetch`, or in `optional_properties` on `execute_query` and `execute_query_nl`, apply only to the request that carries them and are not written back. A request with a different tolerance carries its own value; the next request uses the default again.

Size `limits.max_cost` above the floor described above and set `llm.max_tokens` to what the answers actually need, since those two numbers are the ones that decide whether the ceiling is a bound or a blanket refusal. `estimate` is how to pick them against real requests rather than against the worked example here.

A service that wants one ceiling across a sequence of requests constructs a `Budget` and passes it to each `fetch` as `budget=`. Each fetch derives a child and cannot spend more than the parent has left. Use this when the total matters rather than the limit on each individual request. Deployments that fan out across many requests should also set `llm.max_parallel`. The budget limits total spend, while `llm.max_parallel` caps the number of calls in flight. Finer-grained control belongs above this library, where the full request sequence is visible.

Over MCP, the operator and the caller both set ceilings. `BLUE_DATA_LLM_SOURCE_LIMIT_COST`, `_LIMIT_CALLS` and `_LIMIT_SECONDS` in the server's environment bound every call; the `llmdb_query` schema carries `limits.max_cost`, `limits.max_seconds`, and `limits.max_calls`, and the lower of the operator's and the call's value applies, so an agent can tighten a ceiling but never raise or remove it. `llmdb_estimate` sizes a request against the ceiling that applies before it runs. Both use the same code described above. Without an operator ceiling, a call that sets none is unbounded.

---

## 6. Observability

The library emits no logs, metrics, or traces. It has no logging configuration, OpenTelemetry dependency, or metrics endpoint. Adding observability to a library that runs inside someone else's process is a decision for that process.

Instead, the library returns accounting data. Every `FetchResult` carries an `ExecMeta` describing the run:

| Field | Operational use |
|-------|-----------------|
| `outcome` | Why the result looks the way it does, including why an empty one is empty |
| `model`, `calls`, `cache_hits` | What answered, how often, and how much came from cache |
| `prompt_tokens`, `completion_tokens`, `cost` | Spend attribution per request |
| `latency_seconds` | End-to-end time, including parsing and reduction |
| `stopped_by`, `truncated_to` | Whether a ceiling ended the run, and which one |
| `unmapped_keys`, `remapped_keys` | Whether the model is drifting away from the requested attribute names |
| `type_mismatches` | Which values did not fit the type the caller declared |
| `key_collisions` | Which values were dropped because two returned keys answered the same attribute |

The last three rows are useful for alerts. For an unchanged request, rising rates of unmapped keys, type mismatches, or collisions can show that a model update changed the source's output before a column fills with nulls.

`outcome` is the field to group an empty-result rate by. An empty result is a normal answer here, and the vocabulary distinguishes an empty ask from a model that returned nothing, a result that reduction filtered away, and a run a ceiling stopped, which are four different operational problems.

An embedding service is expected to emit these fields into its own observability stack. Both interface packages already surface a compact form of them, through the same `meta_view` serializer, so one run describes itself the same way whichever door the caller came through. `llmdb_query` returns the whole of it alongside the records on every call, the four alignment diagnostics included. The CLI publishes the same object under `--format json`; under `--format table` it prints the accounting as a footer with the gaps and every stop reason, and puts the outcome, `key_collisions` and the other key- and value-level diagnostics behind `--verbose`.

During a fetch, the CLI redirects interpreter-level provider writes through `sys.stdout` to standard
error. This covers the known provider banners, leaving one JSON object on standard output during a
normal successful `--format json` command. Direct file-descriptor writes and output produced after
the fetch returns are outside the redirect.

---

## 7. CI/CD Pipeline

No CI/CD platform is configured and no automated publication path exists yet. The table below is a
proposed pipeline rather than a description of one that runs; the stages are written down now so that
whichever platform is chosen implements the same ones.

| Stage | Trigger | Actions |
|-------|---------|---------|
| **Verify** | Every push and pull request | `ruff format --check`, `ruff check`, `pyright` in strict mode, and the three unit suites, all offline |
| **Compatibility** | Every push to the default branch | Run the signature contract test against the installed `blue-data` |
| **Build** | Every push to the default branch | Build the wheel and sdist for each of the three packages |
| **Publish** | Git tag matching `v*.*.*` | Publish all three to PyPI, library first |

**Verify** is intended to pass before any merge and runs without credentials or network access. Each package's suite runs from its own directory against its own configuration. **Compatibility** was separated because it required an extra dependency. `blue-data` is now a core dependency, but the separation remains because a failure indicates that an upstream contract changed. **Publish** would be gated on a semver tag pushed by a maintainer. The library publishes first because the other two packages depend on it.

Until that pipeline exists, the checks in **Verify** are run by hand from each package directory.

---

## 8. Release Process

1. Bump the version in all six declarations: `lib/pyproject.toml` and `lib/src/blue_data_llm_source/__init__.py`, `platform/llm-cli/pyproject.toml` and `platform/llm-cli/src/blue_data_llm_cli/__init__.py`, `platform/llm-mcp/pyproject.toml` and `platform/llm-mcp/src/blue_data_llm_mcp/__init__.py`. The two interface modules are the ones that get missed; the version test in `lib/tests/unit/test_version.py` fails the release if they are.
2. Update `CHANGELOG.md`, calling out any prompt change explicitly.
3. Merge to the default branch.
4. Push a semver tag.
5. Build and upload all three packages, library first.

Step 5 is manual today, because no publication pipeline is configured. No package here has been uploaded to an index, so the step has not been exercised. Whenever it is automated, the credential belongs in the platform's secret store and must never appear in the repository or in a build log.

The three packages currently share one version. They can be versioned independently if an interface begins to change on a different cadence from the library.
