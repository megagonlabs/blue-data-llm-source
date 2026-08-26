# LlmSource — Software Stack

Three packages are built from this repository, none of them published yet: the library, an MCP server, and a CLI. Each is independently versioned and depends on the library, so the library itself carries no server or CLI dependency.

---

## Language & Runtime

| Concern | Choice |
|---------|--------|
| Language | Python |
| Minimum version | 3.10 |
| Package manager | pip |
| Project config | `pyproject.toml` |
| Build backend | setuptools |

---

## Dependency Posture

The library declares one dependency on the data layer and none on anything above it. `blue-data` supplies the `DataSource` base class this source inherits, as every source in the layer does. Nothing here imports the Blue orchestration platform or the operator library: depending on the data layer is not depending on the runtime that calls it. No infrastructure requirement is introduced either way — no server, no broker, no database.

`blue-data` brings `pandas`, `numpy`, and `jsonpath-ng` with it, which is the layer's cost of entry rather than this package's choice, and it is not published to a public index. Install it from its checkout before installing this package, the way the other source subrepos do.

| Layer | Status |
|-------|--------|
| Blue orchestration platform | Never a dependency |
| `blue-data` | Declared core dependency; supplies the `DataSource` base class |
| Operator library | Not a dependency in either direction |
| Provider SDK | Optional extra, imported lazily inside the default client |
| Cache backend | Two stores ship on the standard library; `diskcache` from the `llm` extra backs the `llm.cache` switch, and a caller may supply any store satisfying the protocol |

An earlier version of this package declared no dependency at all and matched the source query surface by shape. Inheritance replaced that, because the two positions fail differently: under shape matching an upstream signature change is discovered by whoever integrates against it, and under inheritance it is a type error at the boundary that owns it. For a package whose value rests on a configured limit actually holding, that trade is worth an install.

The consequence for a caller is that `pip install blue-data-llm-source` brings in two runtime packages and their own dependencies, where it used to bring one. The test suite still runs with no provider SDK installed and no network access.

---

## Runtime Dependencies

| Package | Constraint | Source | Role |
|---------|-----------|--------|------|
| `blue-data` | `>=0.1.0` | internal | The `DataSource` base class `LlmSource` inherits; brings `pandas`, `numpy`, and `jsonpath-ng` |
| `tenacity` | `>=9.0.0` | PyPI | Retry with exponential backoff around provider transport errors |

That is the whole required set. Everything else is an extra.

---

## Optional Extras

| Extra | Packages | Purpose |
|-------|----------|---------|
| `llm` | `litellm`, `diskcache` | Provider transport and the cache the `llm.cache` switch opens |
| `dev` | `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `pyright`, `build`, `twine` | Test, lint, type-check, build, and publish tooling |
| `all` | `litellm`, `diskcache` | Convenience alias for `llm` |

There is no `blue-data` extra. It existed to gate the compatibility adapter while the dependency was optional, and it is removed rather than kept as a no-op, so the metadata does not advertise a choice that is no longer there.

---

## LLM

| Concern | Choice |
|---------|--------|
| Provider transport | `litellm` |
| Default model | `gpt-4o` |
| Protocol | `llm` |
| Variant | `openai` |
| Credential handling | None. The library receives a client or a callable and never reads a key |

`litellm` is imported inside the call that uses it rather than at module import, so the package installs, imports, and runs its tests with no provider SDK present. It is a transport shim rather than a framework: the client builds its own call arguments, owns its own retry, cache, concurrency, and accounting, and does not adopt a provider-agnostic abstraction layer over the request itself.

Two pieces of it are used as more than transport, and both are places where writing our own would mean keeping a second copy of somebody else's contract. `completion_cost` reads what a call was billed. `stream_chunk_builder` rebuilds one response from the chunks of a streamed call, which is what lets `llm.stream=True` be supported without a separate downstream path. Neither is trusted blindly: an unreadable cost is reported as unknown rather than as zero, and the assembler's habit of filling in token counts from a local tokenizer is worked around rather than repeated as accounting. `04-detailed-architecture.md` has the detail.

One behaviour of that dependency reaches the outside world and is worth recording. On some failure paths `litellm` writes its own coloured banners to standard output rather than to standard error, and this package has no hook to suppress them. Anything reading a process's standard output as machine-readable text, such as the CLI under `--format json`, can therefore receive lines this package did not write.

The model boundary is a structural protocol with two methods:

| Method | Signature | Role |
|--------|-----------|------|
| `complete` | `complete(prompt: str, properties: dict \| None = None) -> str` | Synchronous completion |
| `acomplete` | `acomplete(prompt: str, properties: dict \| None = None) -> str` | Asynchronous completion |

Any object carrying both can be injected. Two implementations ship: `DefaultLlmClient`, which calls a provider, and `FunctionClient`, which wraps a plain `(prompt: str) -> str` callable.

A second protocol, `BudgetAwareClient`, adds one method to those two: `budget_capabilities() -> frozenset[str]`, returning axis names from `types.Capability`. It is how a client says which ceilings its provider calls can be held to, and each configured ceiling on an axis it does not name is refused for that run, counting every ceiling in the budget chain rather than only the ones this request set; the row ceiling needs nothing from the client, since the source counts the records itself. `DefaultLlmClient` declares all four axes, and `FunctionClient` declares rows alone, because it cannot know what a caller's function is billed, how to stop it, or how many provider calls one invocation of it becomes. The run hands its budget to the client as a per-call property rather than setting it on the client, because one client can serve several concurrent fetches, and it hands down a per-call list under `usage_sink` for the same reason. One name in that set, `estimate`, is not a ceiling: it declares that the client can price an attempt in advance, which `LlmSource.estimate` requires before it will give a figure. `DefaultLlmClient` declares all five.

**Configuration.** The provider credential is read by the provider SDK from its own environment variable and never by this package. The model is selected through the `llm.model` property, and the package names no environment variable of its own.

---

## Storage

None. There is no persistent store, no schema to migrate, and no state that survives a call.

Caching is optional and is a property of the client rather than of the source. Two forms exist:

| Form | Selected by | Backing |
|------|-------------|---------|
| Supplied store | a `cache_store` property carrying any object with `get` and `set` | `MemoryCache`, `FileCache`, or whatever the caller provides |
| `diskcache` cache | `llm.cache` truthy, with an optional `llm.cache_dir` | `diskcache`, from the `llm` extra |

`types.CacheStore` declares the store protocol as `get` and `set`, and the client drives a store with those two methods alone.

Two implementations ship in `cache/` and need nothing beyond the standard library. `MemoryCache` is in process and bounded, evicting least-recently-used entries past its ceiling. `FileCache` writes one JSON file per entry into a directory, keyed by the SHA-256 of the cache key, replacing each file atomically so a reader never sees a partial entry. Both are reached through `cache_store`.

The `llm.cache` switch is not wired to either of them and still opens `diskcache`, which means the persistent cache reached by that switch requires the `llm` extra while the shipped file store does not. Treat the two paths as unconnected until this is reconciled in code.

---

## External Interfaces

### MCP Server — `blue-data-llm-mcp`

| Concern | Choice |
|---------|--------|
| Library | `mcp==1.27.2`, the official MCP Python SDK |
| Server | Low-level server over stdio |
| Tools | `llm_fetch`, `llm_estimate`, `llm_info` |
| Entry point | `blue-data-llm-mcp` |
| Dependencies | `blue-data-llm-source[llm]>=1.1.0`, `mcp==1.27.2` |

The low-level server is used rather than the decorator-derived one because the tool schemas are written by hand. Deriving a schema from a handler signature would put the tool contract in a Python signature instead of in the JSON a client actually sees, and that JSON is the contract an agent reads.

`mcp` is pinned rather than floored, which is unusual enough to state. The low-level server's decorator API this module is built on was removed in `mcp` 2.0, so a `>=1.0` constraint resolves to a release where the server cannot import at all. `blue-data-ops` pins the same version for the same reason.

The `mcp` import is confined to the transport module. Tool definitions and argument translation live in a module that imports only the library, so both are exercised in CI with no SDK installed.

### CLI — `blue-data-llm-cli`

| Concern | Choice |
|---------|--------|
| Argument parsing | `click>=8.0` |
| Output formatting | `rich>=13.0` |
| Commands | `llm fetch`, `llm info` |
| Entry point | `llm` |
| Dependencies | `blue-data-llm-source[llm]>=1.1.0`, `click>=8.0`, `rich>=13.0` |

Click commands are synchronous and call the library's synchronous wrapper. Rich markup is disabled on output, because a record value containing square brackets is data rather than a style tag.

### REST API

Not implemented and not scheduled. No HTTP framework, ASGI server, or route handler exists in this repository.

---

## Testing

| Concern | Choice |
|---------|--------|
| Test runner | `pytest` |
| Async support | `pytest-asyncio`, with `asyncio_mode = "auto"` |
| Coverage | `pytest-cov`, reported and not enforced |
| Schema checking | `jsonschema`, in the MCP package only |
| Test scope | Unit tests only, with every I/O boundary doubled |

The suite runs offline, enforced by an autouse guard that refuses and counts network, subprocess, and database access, and asserts at the end of each test that all three counts are zero. The model boundary is a protocol, so a scripted double substitutes for a provider without patching an SDK, and the provider transport is covered by a stub module installed over the lazy import. Neither `litellm` nor `mcp` need be installed for the respective suites to pass. Coverage is configured over `src` and is produced on request rather than on every run; no threshold gates a merge.

Four recorded provider responses are committed under `lib/tests/cassettes/` and replayed through the real pipeline, which covers the specified worked examples end to end without a network connection. They are produced by a separate cassette recorder that is not part of the test run, which caps what a capture may spend and stores provider responses only. No recording library is involved: the cassettes are plain JSON read by a client double.

---

## Tooling

| Concern | Tool | Config |
|---------|------|--------|
| Formatting | `ruff format` | `pyproject.toml` |
| Linting | `ruff` (`E`, `F`, `I`, `UP`) | `pyproject.toml` |
| Type checking | `pyright`, strict mode | `pyproject.toml` |
| Build | `build` | `lib/scripts/build.sh` |
| Publish | `twine` | `lib/scripts/publish.sh` |

Line length is 100 and the target version is 3.10. Details are in [08-coding-style.md](08-coding-style.md).

---

## Summary

| Package | Constraint | Source | Role |
|---------|-----------|--------|------|
| `blue-data` | `>=0.1.0` | internal | The `DataSource` base class this source inherits (required) |
| `tenacity` | `>=9.0.0` | PyPI | Transport retry (required) |
| `litellm` | latest stable | PyPI | Provider transport (`llm` extra) |
| `diskcache` | latest stable | PyPI | On-disk response cache (`llm` extra) |
| `mcp` | `==1.27.2` | PyPI | MCP server SDK (`blue-data-llm-mcp` runtime) |
| `click` | `>=8.0` | PyPI | CLI argument parsing (`blue-data-llm-cli` runtime) |
| `rich` | `>=13.0` | PyPI | CLI output formatting (`blue-data-llm-cli` runtime) |
| `pytest` | latest stable | PyPI | Test runner (dev) |
| `pytest-asyncio` | latest stable | PyPI | Async test support (dev) |
| `pytest-cov` | latest stable | PyPI | Coverage measurement (dev) |
| `jsonschema` | latest stable | PyPI | Tool input schema checking (MCP dev) |
| `ruff` | latest stable | PyPI | Format and lint (dev) |
| `pyright` | latest stable | PyPI | Strict type checking (dev) |
| `build` | latest stable | PyPI | Wheel and sdist (dev) |
| `twine` | latest stable | PyPI | PyPI upload (dev) |
