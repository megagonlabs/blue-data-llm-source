# LlmSource — Deployment / Ops

## Overview

Three packages are built from this repository. **None of them is published yet** — not to PyPI and not to an internal index — so every install today is from source, and the routes below describe how they are intended to be distributed rather than how they currently reach anyone. `lib/README.md` and the interface READMEs are the current install instructions.

| Package | Contents | Entry point |
|---------|----------|-------------|
| `blue-data-llm-source` | The Python library, for embedding in applications, planners, and operators | — |
| `blue-data-llm-cli` | The `llm` command | `llm` |
| `blue-data-llm-mcp` | The MCP server, speaking over stdio | `blue-data-llm-mcp` |

Both interface packages depend on the library and add only their own framework, so a caller that wants records installs one package and a caller that wants a terminal installs two.

There is no Docker image. The library holds no state, opens no port, and starts no process of its own, and the MCP server speaks over stdio and is started by its client rather than run as a service. A container image becomes worth building at the point an interface needs to be reachable over a network, which nothing here currently is.

The operational surface is correspondingly small. There is no backend to provision, no migration to run, and no data to back up. What an operator does manage is a provider credential, a spending ceiling, and whichever process embeds the library.

The library does carry a dependency worth knowing about at provisioning time. `blue-data` supplies the `DataSource` base class the source inherits, and it brings `pandas`, `numpy`, and `jsonpath-ng` into any environment this package is installed into. It is internal rather than public-index, so it is installed from its checkout first.

---

## 1. Versioning

The package follows [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`).

- **PATCH** — bug fixes, no interface changes
- **MINOR** — backwards-compatible new features
- **MAJOR** — breaking changes to the Python library API, to the source query surface, or to the result contract

A change to the prompt is treated as MINOR at least, because it changes what the source returns for an unchanged request, and a caller with its own recorded expectations needs the signal.

Each of the three packages declares its version twice, as `version` in its `pyproject.toml` and as `__version__` in its top-level module, and the pairs are kept in step by hand. A test reads all six and fails when a package's two disagree, so a release cannot ship a package that reports one number to `pip` and another to `import`. It also checks that the version quoted in the `llm info` and `llm_info` examples is the one the library exports, because those outputs are read as fact. Deriving the declarations from one another is still the better fix and is not done.

---

## 2. PyPI Packages

`blue-data-llm-source` exposes the Python library. Its runtime dependencies are `blue-data>=0.1.0`, which supplies the `DataSource` base class the source inherits, and `tenacity>=9.0.0`. The provider transport and the `diskcache` backend are extras.

Every command below assumes `blue-data` is already resolvable, which means an internal index.
From a checkout it is not, and `lib/scripts/install.sh` puts it in place first; the two
interface installers route through that one.

```
pip install blue-data-llm-source                 # library; brings blue-data and tenacity
pip install 'blue-data-llm-source[llm]'          # + provider transport and disk cache
pip install 'blue-data-llm-source[dev]'          # + test, lint, type-check, build, publish tooling
pip install 'blue-data-llm-source[all]'          # currently equivalent to [llm]
```

There is no `blue-data` extra. It gated the compatibility adapter while the dependency was optional, and it is removed rather than kept as a no-op.

What an operator should know before provisioning is what `blue-data` brings with it: `pandas`, `numpy`, and `jsonpath-ng`. It is an internal package rather than a public-index one, so it is installed from its checkout before this package, the way the sibling source subrepos already are.

The base install is still usable without a provider. A caller that injects its own client, or passes an `llm_fn`, never needs the `llm` extra, and its process acquires no provider SDK.

The two interface packages pull the library from the index, and from a checkout each has an
installer that puts the prerequisite in place first:

```
pip install blue-data-llm-cli                    # the `llm` command, from an index
pip install blue-data-llm-mcp                    # the MCP server, from an index

platform/cli/scripts/install.sh                  # or from a checkout
platform/mcp/scripts/install.sh
```

An MCP client is pointed at the server the usual way, by naming the console entry point as the command to run.

`lib/scripts/` carries the three commands that produce and publish the artifact, and the CLI package carries the same three:

| Script | Action |
|--------|--------|
| `build.sh` | `python -m build`, producing a wheel and an sdist |
| `install.sh` | Editable install with the `dev` and `llm` extras, for development |
| `publish.sh` | `twine upload dist/*` |

---

## 3. Storage Backends

None. LlmSource does not bundle, require, or manage a database, a cache server, or a message broker, and it will not start.

The optional response cache is the only thing resembling storage, and it is local. Enabling `llm.cache` writes a `diskcache` cache to a directory on the local filesystem, defaulting to `.llm_cache`, which an operator should place on a writable path with a retention policy of its choosing. The library also ships two stores that need nothing beyond the standard library, `MemoryCache` in process and `FileCache` as one JSON file per entry under a directory; both are passed through the `cache_store` property, and `FileCache` has the same operational shape as the `llm.cache` directory. A caller wanting a shared cache supplies its own store through the same property. The library does not depend on any particular backend and does not open a connection on the caller's behalf.

Neither shipped store expires an entry. `MemoryCache` evicts least-recently-used entries past its ceiling, and `FileCache` grows until something clears it, so a long-lived deployment should treat the cache directory as it would any other unmanaged local cache.

---

## 4. Configuration

Configuration is by **properties**, passed to the constructor or per call, not by environment variables. This differs from the sibling sources deliberately: LlmSource is embedded in a caller's process rather than run as a service, and a source constructed twice in one process with two different budgets is a normal thing to want, which a process-wide environment cannot express.

Per call is the part with operational weight. Every property below can be handed to a single request through `properties` on `fetch` or `optional_properties` on the query surface, where it is merged over the instance's for that request and not written back. One source can therefore serve a cheap speculative question and an expensive authorised one under different ceilings, without a second source and without a window in which the source is configured for the wrong one.

The full property table is in [09-api-interface-spec.md](09-api-interface-spec.md). The operationally significant ones:

| Property | Operational meaning |
|----------|--------------------|
| `llm.model` | Which model answers, and therefore what a call costs |
| `llm.max_parallel` | Concurrency bound per event loop; the main lever against provider rate limiting |
| `llm.rate_limit` | Not supported. Refused with `ConfigError`; deferred to v2.0 |
| `llm.stream` | Set it when the provider configuration or proxy in front of you requires a stream. It buys nothing else: a fetch has no answer until the last chunk lands, and a stream the provider sends no token counts with cannot be priced, so the run reports `accounting_known` false and its cost ceiling is enforced against the reserved bound |
| `llm.cache`, `llm.cache_dir` | Local response cache and where it is written |
| `llm.max_tokens` | The output allowance, and therefore the floor of every cost claim and of every estimate; the main lever on whether a cost ceiling binds or refuses |
| `llm.price_per_input_token`, `llm.price_per_output_token` | Rates for a model the shipped price table does not carry; both required together, and consumed locally rather than forwarded |
| `limits.max_cost` | Spending ceiling per run. An upper bound on what an attempt can cost is claimed before it runs, so an attempt that would cross the ceiling is refused rather than reported afterwards, and the bound is reconciled against the real price once the call returns |
| `limits.max_calls`, `limits.max_seconds`, `limits.max_rows` | The other three run ceilings |

All five `limits.*` keys are also the ones to hand in per request. A ceiling set on the source is the deployment's default, and a ceiling passed with a request is that request's own.

### Environment variables

| Variable | Read by | Required | Description |
|----------|---------|----------|-------------|
| Provider credential variable | The provider SDK, never this package | Yes, when the default client reaches a provider | The library does not read, store, log, or forward it |

That is the whole list. The package names no environment variable of its own and reads none. The model is selected through the `llm.model` property, and the CLI exposes it as `--model`.

Because the credential belongs to the provider SDK, an absent one is not a configuration error this package can detect. It surfaces as a failed call: a `TransportError` from the library, and exit code 5 from the CLI, not the `ConfigError` and exit code 4 that cover the configuration this package does check. An operator diagnosing a fresh deployment should read code 5 with a credentials message as "the key is missing or wrong" rather than as a transport fault.

No property, environment variable, or log line produced by this package carries a credential value.

---

## 5. Spend Control

The one operational risk this library carries is cost, so the controls are worth stating together.

- Every run is bounded on four axes: row count, elapsed time, API cost, and provider call count. The time axis bounds admission and cooperative waiting rather than the whole run; [what it bounds](../../lib/README.md#what-the-time-ceiling-actually-bounds) lists the exclusions.
- Call count, elapsed time, and cost are all claimed **before** the provider is contacted, under a lock, so a concurrent fan-out cannot cross a ceiling and report it afterwards.
- A call's price is not known until it returns, so the cost claim is an upper bound rather than an estimate: the prompt's length in UTF-8 bytes plus a framing allowance at the model's published input rate, plus the whole output allowance at its output rate. It rests on a byte-pair tokenizer consuming at least one byte of the encoded prompt per token it emits, so no tokenizer can exceed it. The bound is reconciled against the real cost when the call returns and the overshoot is released, so an over-claim costs headroom during the call and nothing after it.
- Where a bound cannot be established the ceiling is **refused**, not approximated. A model with no published price and no caller-supplied price, or an `llm.max_tokens` that is not a finite positive number, makes a `limits.max_cost` raise a `ConfigError` at the first fetch. The operator wanted a ceiling and gets a refusal instead of a number that was invented.
- The figure to size a ceiling against is the floor of that bound, which is the whole output allowance at the model's output rate and does not depend on the prompt at all. With the shipped defaults of `gpt-4o` and `llm.max_tokens` of 4096 that floor is about $0.041, so a `limits.max_cost` below it refuses every call before one is made. The lever is `llm.max_tokens`: halving it halves the floor. Raising the ceiling to accommodate an output allowance the answers never use buys nothing.
- The claim is per provider attempt, so a retry sequence spends the call budget rather than escaping it, and each attempt rechecks the deadline before it runs. Retry depth narrows along the budget chain, so a fetch drawing on a caller-owned budget cannot make more attempts than that budget allows.
- The timeout handed to the provider is capped at the time the run has left, so a call started just inside a deadline cannot run for its own full timeout past it. In the async path a deadline also cancels the wait itself, which is what holds when a provider ignores the timeout it was given.
- A run stopped by a limit returns a result whose metadata names the axis, rather than raising, so a caller can treat a ceiling as a normal outcome. The row cap is the one axis that leaves a usable result: it reports `outcome=truncated` with the records it kept, and the command line exits 0 for it. Every other axis reports `outcome=stopped_by_limit` and exits 7.
- A cache hit consumes no provider call, no tokens and no cost, the attempt included. It is not free on every axis: rows it returns count against the row cap, and elapsed-time admission still applies.
- Everything that can fail locally happens before the reservation, so such a failure is uncharged. The order is admit, import, claim: the bound is checked against the budget without taking anything, so a request the ceiling refuses never reaches the network, and the claim itself is the last step before the provider is entered. Claiming before the import instead made the claim visible to a sibling sharing the budget, which was then refused on an allowance the final ledger said was free. Everything after it, including reading the response, sits inside one guard that resolves the claim, so a malformed response cannot leave headroom outstanding while the retry takes more. A call that reached the provider and failed keeps its claim at the bound, because a ceiling that counts only successes does not bound spend: a failing provider is still a provider being paid to fail.
- A ceiling holds only on an axis the answering client declares through `budget_capabilities()`. The default client declares all four. A callable passed as `llm_fn` declares rows alone, because nothing here can know what that callable is billed, how to stop it, or how many provider calls one invocation of it becomes, so a call, cost, or time ceiling over one is refused. Each configured ceiling on an undeclared axis raises a `ConfigError` at the first fetch rather than being silently reported, so a deployment that injects its own client learns at the boundary rather than from a bill. `limits.max_rows` is unaffected, since the source counts the records itself.
- `LlmSource.estimate` reports the same bound for a request before it runs, and reaching it costs nothing and contacts nothing. That is the tool for sizing a ceiling against real traffic rather than against the worked example in this document, and for a service that wants to refuse an oversized request itself rather than have the budget refuse it.

An operator embedding the library in a service sets a floor at construction and tightens it per request. `limits.max_cost` and `limits.max_calls` on the source apply to every fetch it makes; the same keys in `properties` on `fetch`, or in `optional_properties` on `execute_query` and `execute_query_nl`, apply to the one request they arrive with and are not written back, which is what makes a per-request ceiling safe to use routinely rather than exceptionally. A request whose tolerance differs from the default carries its own number, and the request after it is back to the default.

Size `limits.max_cost` above the floor described above and set `llm.max_tokens` to what the answers actually need, since those two numbers are the ones that decide whether the ceiling is a bound or a blanket refusal. `estimate` is how to pick them against real requests rather than against the worked example here.

A service that wants one ceiling over a sequence of requests, rather than a bound on each, constructs a `Budget` and passes it to each `fetch` as `budget=`; each fetch derives a child from it and can never spend more than the parent has left. That is a different question from the one above and answers it differently, so reach for it when the total is what matters. Deployments that fan out over many requests should also set `llm.max_parallel`, since the budget bounds spend but does not bound the rate at which it is spent. `llm.max_parallel` caps how many calls are in flight at once, which is the lever this version offers; anything finer belongs above this library, where the whole request sequence is visible rather than one call at a time.

Over MCP the ceiling is the caller's to set. The `llm_fetch` schema carries `limits.max_cost`, `limits.max_seconds`, and `limits.max_calls`, and `llm_estimate` sizes a request before it runs, both reaching the same code described above. What the server does not do is set them: it constructs its source with no properties, so a call that names no ceiling has none. An operator who cannot leave that to the agent wraps the server in a process that constructs the source itself.

---

## 6. Observability

The library emits no logs, no metrics, and no traces. It has no logging configuration, no OpenTelemetry dependency, and no metrics endpoint, and adding one to a library that runs inside someone else's process is a decision for the process rather than for the library.

What it does instead is return the accounting. Every `FetchResult` carries an `ExecMeta` describing the run:

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

The last three rows are the ones worth alerting on. A rising rate of unmapped keys, type mismatches, or collisions for an unchanged request is the earliest visible sign that a model update has changed what the source returns, and it is visible before anyone notices a column full of nulls.

`outcome` is the field to group an empty-result rate by. An empty result is a normal answer here, and the vocabulary distinguishes an empty ask from a model that returned nothing, a result that reduction filtered away, and a run a ceiling stopped, which are four different operational problems.

An embedding service is expected to emit these fields into its own observability stack. Both interface packages already surface a compact form of them: the MCP server returns the accounting alongside the records in every tool result, including `outcome` on every call, and the CLI prints the accounting as a footer with the outcome and the key-level and value-level fields behind `--verbose`. Both carry `key_collisions` when there is one to report, the MCP server on every call and the CLI behind `--verbose`.

One caution for anything scraping the CLI's machine-readable output. The provider library writes coloured banners to standard output on some failure paths, so `--format json` can emit those lines alongside the JSON object and break a pipe into a JSON reader. A collector that needs a clean stream should read the accounting from `ExecMeta` through the library rather than from a pipe.

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

**Verify** is intended to pass before any merge, and it runs with no credential and no network. Each package's suite runs from its own directory against its own configuration. **Compatibility** used to be separated because it needed an extra the library did not otherwise require; `blue-data` is now a core dependency, so the separation is kept for the remaining reason, which is that its failure means an upstream contract moved rather than that this package broke. **Publish** would be gated on a semver tag pushed by a maintainer, and the library publishes first because the other two depend on it.

Until that pipeline exists, the checks in **Verify** are run by hand from each package directory, which is what the gate figures quoted in the changelog come from.

---

## 8. Release Process

1. Bump the version in all six declarations: `lib/pyproject.toml` and `lib/src/blue_data_llm_source/__init__.py`, `platform/cli/pyproject.toml` and `platform/cli/src/blue_data_llm_cli/__init__.py`, `platform/mcp/pyproject.toml` and `platform/mcp/src/blue_data_llm_mcp/__init__.py`. The two interface modules are the ones that get missed; the version test in `lib/tests/unit/test_version.py` fails the release if they are.
2. Update `CHANGELOG.md`, calling out any prompt change explicitly.
3. Merge to the default branch.
4. Push a semver tag.
5. Build and upload all three packages, library first.

Step 5 is manual today, because no publication pipeline is configured. Nothing here has been
published yet, so the step has not been exercised. Whenever it is automated, the credential belongs
in the platform's secret store and must never appear in the repository or in a build log.

The three packages are versioned together in this release. Independent versioning is the reason they are separate packages and remains available; it becomes worth using at the point one interface changes on a different cadence from the library.
