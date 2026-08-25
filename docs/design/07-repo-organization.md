# LlmSource — Repo Organization

## Directory Tree

```
blue-data-llm-source/
│
├── LICENSE
├── CHANGELOG.md
├── README.md                               # what the source is, quick start, dependency posture
├── .gitignore
│
├── docs/
│   └── design/                             # design documents 01 through 13
│
├── lib/                                    # Python library (blue-data-llm-source PyPI package)
│   ├── pyproject.toml                      # package metadata, extras, tool config
│   ├── README.md                           # library usage and API reference
│   ├── COMPATIBILITY.md                    # deviations from the spec and the earlier implementation
│   ├── scripts/
│   │   ├── build.sh                        # build wheel and sdist
│   │   ├── install.sh                      # editable install with the dev and llm extras
│   │   └── publish.sh                      # publish to PyPI
│   ├── src/
│   │   └── blue_data_llm_source/
│   │       ├── __init__.py                 # re-exports the facade, types, clients, and constants
│   │       ├── py.typed                    # PEP 561 marker — package ships inline types
│   │       ├── _constants.py               # protocol, variant, op codes, virtual hierarchy names
│   │       ├── types.py                    # data contracts, protocols, exception hierarchy
│   │       ├── checks.py                   # argument validation with model-readable messages
│   │       ├── source.py                   # LlmSource — facade and both query surfaces
│   │       │
│   │       ├── prompts/
│   │       │   └── __init__.py             # every prompt, plus rendering and building
│   │       │
│   │       ├── llm/                        # the model boundary
│   │       │   ├── __init__.py
│   │       │   ├── budget.py               # Limits, Budget, Reservation — four-axis enforcement
│   │       │   ├── pricing.py              # the cost bound a reservation claims, or a refusal
│   │       │   └── client.py               # DefaultLlmClient, FunctionClient
│   │       │
│   │       ├── cache/                      # CacheStore implementations
│   │       │   ├── __init__.py
│   │       │   ├── memory.py               # MemoryCache — bounded, LRU eviction
│   │       │   └── file.py                 # FileCache — one JSON file per entry
│   │       │
│   │       ├── reduction/                  # model text to checked records
│   │       │   ├── __init__.py
│   │       │   ├── parsing.py              # fence stripping, JSON to records
│   │       │   ├── aligner.py              # key-to-attribute mapping, Alignment
│   │       │   └── verifier.py             # type coercion, mismatch reporting, deduplication
│   │       │
│   │       └── compat/                     # the older adapter name, now an alias
│   │           ├── __init__.py
│   │           └── blue_data.py            # BlueDataLlmSource — an alias for LlmSource
│   │
│   └── tests/
│       ├── conftest.py                     # autouse offline guard and the shared doubles
│       ├── record_cassettes.py             # records the cassettes; not part of the test run
│       ├── cassettes/                      # recorded provider responses, plus a spend receipt
│       ├── doubles/
│       │   ├── clients.py                  # ScriptedClient, FailingClient
│       │   ├── cache.py                    # cache double
│       │   ├── provider.py                 # LitellmStub, chat_payload, install_litellm
│       │   └── network.py                  # offline guard: network, subprocess, database
│       └── unit/
│           ├── test_source_fetch.py        # the fetch path end to end
│           ├── test_source_surface.py      # execute_query, execute_query_nl, metadata
│           ├── test_source_budget.py       # limits reaching the result
│           ├── test_estimate.py            # the cost bound answered before a call
│           ├── test_packaging.py           # the dependency line: blue_data yes, blue no
│           ├── test_source_fixtures.py     # the worked examples from the source spec
│           ├── test_cassette_fixtures.py   # the same examples replayed from recordings
│           ├── test_checks.py              # argument validation and message content
│           ├── test_types.py               # contracts and the exception hierarchy
│           ├── test_version.py             # the two declared versions agree
│           ├── test_offline_guard.py       # the guard itself refuses a socket
│           ├── test_offline_coverage.py    # the guard covers subprocess and database too
│           ├── llm/                        # budget, pricing, and the client split by concern
│           ├── cache/                      # both shipped stores against the protocol
│           ├── prompts/                    # rendering and substitution safety
│           ├── reduction/                  # parsing, alignment, verification
│           └── compat/                     # inheritance, base order, signature contract
│
└── platform/                               # external interface services
    ├── cli/                                # CLI (blue-data-llm-cli PyPI package)
    │   ├── pyproject.toml                  # depends on lib, click, rich; entry point `llm`
    │   ├── README.md                       # command reference; every llm command is run by a test
    │   ├── scripts/{build.sh,install.sh,publish.sh}
    │   ├── src/
    │   │   └── blue_data_llm_cli/
    │   │       ├── commands.py             # Click group, fetch and info, table and JSON printers
    │   │       └── config.py               # option translation and the exit-code table
    │   └── tests/
    │       └── unit/                       # fetch, info, config, errors, README commands
    │
    └── mcp/                                # MCP server (blue-data-llm-mcp PyPI package)
        ├── pyproject.toml                  # depends on lib and mcp; entry point blue-data-llm-mcp
        ├── README.md                       # tool reference and client configuration
        ├── scripts/                        # install.sh
        ├── src/
        │   └── blue_data_llm_mcp/
        │       ├── tools.py                # tool specs, JSON schemas, translation, dispatch
        │       └── server.py               # stdio transport; the only `mcp` import
        └── tests/
            └── unit/                       # tools, schemas, and the import boundary
```

---

## Layout Rationale

### `lib/` — the Python library

All business logic lives under `lib/src/`. The `src/` layout keeps the package off the import path by accident, so nothing imports it just because the working directory happens to be the project root. The test suite puts `src` on the path explicitly through `pythonpath` in `pyproject.toml`, so tests run against the working tree; the packaging gates are what exercise the installed artifact.

Two documents sit beside the code. `README.md` doubles as the API reference. `COMPATIBILITY.md` records what this library does differently from the source design document and from the earlier implementation in the platform, with the reason for each, so that anyone porting a caller reads the deviations in one place rather than discovering them one at a time.

`lib/scripts/` provides three scripts:

| Script | Purpose |
|--------|---------|
| `build.sh` | Builds the wheel and sdist via `python -m build` |
| `install.sh` | Installs the package in editable mode with the `dev` and `llm` extras |
| `publish.sh` | Publishes to PyPI via `twine upload` |

### Module-to-architecture mapping

| Module | Architecture component |
|--------|-----------------------|
| `source.py` | `LlmSource` — the facade over `blue_data`'s `DataSource`, both query surfaces, `estimate`, and the metadata surface. The only module that imports `blue_data` |
| `checks.py` | Request Intake — argument validation |
| `prompts/` | Request Intake — the single prompt module |
| `llm/client.py` | Model Boundary — `DefaultLlmClient` and `FunctionClient` |
| `llm/budget.py` | Model Boundary — `Limits`, `Budget`, and `Reservation`, the four-axis enforcement |
| `llm/pricing.py` | Model Boundary — the upper bound a cost reservation claims and `estimate` reports, or the refusal that replaces it |
| `cache/memory.py`, `cache/file.py` | Model Boundary — the two shipped `CacheStore` implementations |
| `reduction/parsing.py` | Reduction — response text to record mappings |
| `reduction/aligner.py` | Reduction — key-to-attribute mapping and reporting |
| `reduction/verifier.py` | Reduction — type coercion, mismatch reporting, and deduplication |
| `types.py` | Shared contracts — `Attr`, `Usage`, `Outcome`, `Capability`, `ExecMeta`, `Estimate`, `FetchResult`, the protocols including `BudgetAwareClient`, and the exception hierarchy |
| `_constants.py` | Shared vocabulary — protocol, variant, the three op codes, the synthesised hierarchy, and the budget and usage-sink property keys |
| `compat/blue_data.py` | Backwards-compatible alias for `LlmSource`, kept for an older import |

### One prompt module

Every prompt the package sends lives in `prompts/`, not as a constant on the class that uses it. Prompts are the highest-churn text in a library of this kind, and holding two copies of one prompt in two modules is how they diverge in wording and in variable naming without anyone noticing. One home means a change to a prompt is visible as a change to one file, and the recorded responses that cover it move with it.

### `compat/` is one-directional

The core imports nothing from `compat/`, and `compat/` imports the core. The direction mattered when the dependency was optional; it is kept because a module that exists only to preserve an older name should not be something the core depends on.

### `types.py` owns the contracts

Every shared type, protocol, and exception is defined in `lib/src/blue_data_llm_source/types.py`. This prevents circular imports between the facade, the model boundary, and reduction, and it gives any platform adapter one module to import rather than three.

### `platform/` — external interface services

Each external interface is an independent deployable unit under `platform/`. They depend on `lib` and contain no business logic, so that the library carries no CLI or server dependency and each service can be built, versioned, and released on its own schedule.

| Service | Package | State | Notes |
|---------|---------|-------|-------|
| `cli/` | `blue-data-llm-cli` | Implemented | `llm fetch` and `llm info`; Click and Rich |
| `mcp/` | `blue-data-llm-mcp` | Implemented | `llm_fetch`, `llm_estimate`, and `llm_info` over stdio |
| `api/` | — | Not implemented | No REST adapter is scheduled |

Each service follows the same internal structure:

| Path | Purpose |
|------|---------|
| `pyproject.toml` | Package metadata, dependencies including the library, and the console entry point |
| `README.md` | Command or tool reference for that interface |
| `src/<package>/` | Interface adapter code, split into declaration and translation |
| `tests/unit/` | Unit tests for the adapter layer |

Both services split their two modules the same way, for the same reason. The CLI keeps every mapping between what a person typed and what the library accepts in `config.py`, so `commands.py` is option declaration and printing. The MCP server keeps the tool specifications and the argument translation in `tools.py`, which imports nothing from the protocol SDK, so `server.py` owns the transport alone. In both cases the module a test cares about is the one with no framework in it.

Each service's `pyproject.toml` adds the library's source directory to the test path, so the suites run against a checkout without an install step.

### `tests/`

Library tests live under `lib/tests/` and mirror the source layout. Each platform service carries its own `tests/` directory for adapter-layer tests.

Facade tests sit directly under `unit/`, split by concern rather than by module: the fetch path, the source surface, budget behaviour reaching the result, the estimate, and the worked examples from the source specification each get their own file. Argument validation, the shared contracts, the declared version, the dependency line, and the offline guard get one file each for the same reason. Modules with several distinct concerns get a directory: `llm/` splits the client into configuration, calls, caching, transport, and accounting, and holds the budget tests separately, and `cache/` exercises both shipped stores against the published protocol.

`test_packaging.py` is the odd one and earns its place. It scans the package for an import of `blue` or `blue_data_ops` and fails on either, because the dependency this package now takes is on the data layer and the line it must not cross is the orchestration layer above it. A rule that lives only in a document is a rule until someone adds an import.

`llm/` also holds `test_pricing.py`, which covers the bound both the cost ceiling and `estimate` rest on. `lib/tests/cassettes/` holds four recorded provider responses and a receipt stating what the recording round cost. `test_cassette_fixtures.py` replays them through the real pipeline, so the specified worked examples are covered end to end with no network connection. `record_cassettes.py` sits beside the suite rather than in it, because it is the one thing here that makes real calls, and it is run by hand when a prompt change makes the recordings stale.

`lib/tests/doubles/` holds the doubles the suite needs, one per boundary the package owns:

| Double | Boundary |
|--------|----------|
| `ScriptedClient`, `FailingClient` | The model, injected structurally as a client |
| `LitellmStub` | The provider transport, installed over the lazy import |
| Cache double | A caller-supplied store |
| Offline guard | Network, subprocess, and database access, refused and counted |

Each test subdirectory carries an `__init__.py`, so identically named modules in sibling directories do not collide during collection.

Platform adapter tests define their own fixtures rather than importing the library's, since each adapter exercises a different entry point, and each replaces the single source-construction function in its own package to inject a scripted client. No adapter test reaches a provider, and the MCP tool tests run without the protocol SDK installed.

The CLI carries one test of an unusual kind: it reads its own README, extracts every `llm` command from its shell blocks, and runs each one. A command that stops working in the README fails the suite rather than surviving as a false claim.
