# LlmSource — Software Stack

This repository builds three packages that are not available on a package index: the library, an MCP server, and a CLI. Each is independently versioned. The two interface packages depend on the library; the library does not depend on either adapter package.

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

The library depends on the data layer and nothing above it. `blue-data` supplies the `DataSource` base class inherited by every source in the layer. The package does not import the Blue orchestration platform or the operator library. It also requires no server, broker, or database.

`blue-data` also installs `pandas`, `numpy`, and `jsonpath-ng`; these are dependencies of the data layer, not choices made by this package. Since `blue-data` is not published to a public index, install it from its checkout before installing this package, as with the other source subrepositories.

| Layer | Status |
|-------|--------|
| Blue orchestration platform | Never a dependency |
| `blue-data` | Declared core dependency; supplies the `DataSource` base class |
| Operator library | Not a dependency in either direction |
| Provider SDK | Optional extra, imported lazily inside the default client |
| Cache backend | Two stores ship on the standard library; `diskcache` from the `llm` extra backs the `llm.cache` switch, and a caller may supply any store satisfying the protocol |

Inheritance makes upstream signature changes visible to the type checker at this package's boundary. It adds a core dependency but keeps the inherited source contract explicit.

As a result, `pip install blue-data-llm-source` brings in two runtime packages and their dependencies instead of one. The test suite still runs without a provider SDK or network access.

---

## Runtime Dependencies

| Package | Constraint | Source | Role |
|---------|-----------|--------|------|
| `blue-data` | `>=1.0.0,<2` | internal | The `DataSource` base class `LlmSource` inherits; brings `pandas`, `numpy`, and `jsonpath-ng` |
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
| Default model | `gpt-6-luna`, medium reasoning |
| Known models | One table in `llm/models.py`: each model's request contract and published rates |
| Protocol | `llm` |
| Variant | `openai` |
| Credential handling | The provider SDK handles authentication. The library reads credential environment values only to mask diagnostics |

`litellm` is imported inside the call that uses it, so the package installs, imports, and runs tests without the provider SDK. It is a transport shim. The client builds its own call arguments and manages retry, caching, concurrency, and accounting without adding a provider-agnostic abstraction over the request.

The client also uses two `litellm` functions beyond transport, avoiding the need to maintain copies of their contracts. `completion_cost` reads what a call was billed. `stream_chunk_builder` rebuilds a response from streamed chunks, allowing `llm.stream=True` without a separate downstream path. Neither function is trusted blindly. An unreadable cost, including a calculator result of zero for a call that reached the provider, is reported as unknown. The client also works around the assembler's practice of filling in token counts with a local tokenizer rather than using those counts for accounting. See `04-detailed-architecture.md` for details.

One behavior of that dependency reaches the process boundary and is worth recording. On some
failure paths `litellm` writes colored banners to standard output rather than standard error. The
CLI redirects interpreter-level `sys.stdout` writes for the synchronous fetch boundary, and the MCP
server redirects them for the server run. Those redirects cover the known banners, but not direct
file-descriptor writes or output produced outside the protected lifetime. A program embedding the
library owns its process streams and may still receive the dependency's output.

The model boundary is a structural protocol with two methods:

| Method | Signature | Role |
|--------|-----------|------|
| `complete` | `complete(prompt: str, properties: dict \| None = None) -> str` | Synchronous completion |
| `acomplete` | `acomplete(prompt: str, properties: dict \| None = None) -> str` | Asynchronous completion |

Any object carrying both can be injected. Two implementations ship: `DefaultLlmClient`, which calls a provider, and `FunctionClient`, which wraps a plain `(prompt: str) -> str` callable.

The `BudgetAwareClient` protocol adds `budget_capabilities() -> frozenset[str]` to the two completion methods. It returns axis names from `types.Capability` and declares which provider-call ceilings the client can enforce. The source refuses each configured ceiling on an undeclared axis, including ceilings inherited through the budget chain. The row ceiling does not depend on the client because the source counts records itself. `DefaultLlmClient` declares all four axes. `FunctionClient` declares only rows because it cannot know what a caller's function costs, how to stop it, or how many provider calls one invocation makes. The run passes the budget to the client as a per-call property so one client can serve concurrent fetches without sharing their ceilings. It also passes a per-call list under `usage_sink` for the same reason. `estimate` is not a ceiling; it declares that the client can price an attempt in advance, which `LlmSource.estimate` requires before returning a figure. `DefaultLlmClient` declares all five capabilities.

LiteLLM's bundled price map lags new models, so the client does not rely on it to route or to price. A bare OpenAI model name is sent with the provider named, and the cost bound is built from the package's own table. The calculator's figure is a settlement input only.

**Configuration.** The provider SDK reads its credential from the environment. The library also reads credential environment values to mask diagnostics; it does not use them to configure a request. The model is selected through the `llm.model` property. The CLI and MCP server read `BLUE_DATA_LLM_MODEL` and the `BLUE_DATA_LLM_SOURCE_*` settings and pass them down as properties.

---

## Storage

The source maintains no dataset or schema to migrate. Optional caches can retain model responses across calls. The CLI also stores profiles locally; neither form of storage is an authoritative data source.

Caching is optional and is a property of the client rather than of the source. Two forms exist:

| Form | Selected by | Backing |
|------|-------------|---------|
| Supplied store | a `cache_store` property carrying any object with `get` and `set` | `MemoryCache`, `FileCache`, or whatever the caller provides |
| `diskcache` cache | `llm.cache` truthy, with an optional `llm.cache_dir` | `diskcache`, from the `llm` extra |

`types.CacheStore` declares the store protocol as `get` and `set`, and the client drives a store with those two methods alone.

Two implementations ship in `cache/` and need nothing beyond the standard library. `MemoryCache` is in process and bounded, evicting least-recently-used entries past its ceiling. `FileCache` writes one JSON file per entry into a directory, keyed by the SHA-256 of the cache key, replacing each file atomically so a reader never sees a partial entry. Both are reached through `cache_store`.

The `llm.cache` switch is separate from both stores and opens `diskcache`. That persistent cache requires the `llm` extra, while the included file store does not. Treat the two cache paths as independent until they are reconciled in code.

---

## External Interfaces

### MCP Server — `blue-data-llm-mcp`

| Concern | Choice |
|---------|--------|
| Library | `mcp>=1.28.1,<2`, the official MCP Python SDK |
| Server | Low-level server over stdio |
| Tools | `llmdb_query`, `llmdb_estimate`, `llmdb_list_models`, `llmdb_inspect_model`, `llmdb_inspect_metadata` |
| Entry point | `blue-data-llm-mcp` |
| Configuration | `BLUE_DATA_LLM_MODEL` and nine `BLUE_DATA_LLM_SOURCE_*` request settings from the process environment, read on every call, plus three operator ceilings (`_LIMIT_COST`, `_LIMIT_CALLS`, `_LIMIT_SECONDS`) that a call can lower but not raise |
| Dependencies | `blue-data-llm-source[llm]==1.0.0`, `mcp>=1.28.1,<2` |

The low-level server is used rather than the decorator-derived one because the tool schemas are written by hand. Deriving a schema from a handler signature would put the tool contract in a Python signature instead of in the JSON a client actually sees, and that JSON is the contract an agent reads.

`mcp` is bounded below 2 rather than merely floored, which is unusual enough to state. The low-level server's decorator API this module is built on was removed in `mcp` 2.0, so a `>=1.0` constraint resolves to a release where the server cannot import at all. The floor of 1.28.1 excludes releases with a published advisory; `blue-data`'s MCP packages use the same `>=1.28.1,<2` bound.

The `mcp` import is confined to the transport module. Tool definitions and argument translation live in a module that imports only the library, so offline tests exercise both without the SDK installed.

### CLI — `blue-data-llm-cli`

| Concern | Choice |
|---------|--------|
| Argument parsing | `click>=8.0` |
| Output formatting | `rich>=13.0` |
| Commands | 12, over the `profile`, `data`, `model` and `metadata` components |
| Entry point | `blue-llmdb` |
| Configuration | Profiles under `~/.blue-llmdb`, using `blue_data.utils.config` with protected CLI storage hooks |
| Dependencies | `blue-data-llm-source[llm]==1.0.0`, `blue-data==1.0.0`, `click>=8.0`, `rich>=13.0` |

Click commands are synchronous and call the library's synchronous wrapper. Rich markup is disabled on output, because a record value containing square brackets is data rather than a style tag.

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
| `blue-data` | `>=1.0.0,<2` | internal | The `DataSource` base class this source inherits (required) |
| `tenacity` | `>=9.0.0` | PyPI | Transport retry (required) |
| `litellm` | `>=1.102.0,<2` | PyPI | Provider transport (`llm` extra) |
| `diskcache` | `>=5.6,<6` | PyPI | On-disk response cache (`llm` extra) |
| `mcp` | `>=1.28.1,<2` | PyPI | MCP server SDK (`blue-data-llm-mcp` runtime) |
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
