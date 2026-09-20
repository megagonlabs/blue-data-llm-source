# LlmSource — Repo Organization

## Directory Tree

This tree lists the packages and the files that define their main boundaries. It is a navigation map rather than an exhaustive inventory of test modules.

```
blue-data-llm-source/
│
├── LICENSE                                 # BSD 3-Clause with the attribution requirement
├── CHANGELOG.md                            # one section per release, breaking changes first
├── README.md                               # what the source is, quick start, dependency posture
├── .gitignore                              # environments, caches, build output, direnv files
│
├── design/                                 # design documents 01 through 13, the process
│                                           #   note, and the layout template
│
├── docs/                                   # reader documentation
│   ├── README.md                           # the map into the rest of this directory
│   ├── quickstart.md                       # first records in a few minutes
│   ├── installation.md                     # the three packages and their extras
│   ├── features.md                         # what the source does, feature by feature
│   ├── guide.md                            # the long-form walkthrough
│   ├── api-reference.md                    # the library surface
│   ├── architecture.md                     # the shape of the code, for a contributor
│   ├── extending.md                        # clients, cache stores, and prompts
│   ├── cli.md                              # the `blue-llmdb` command reference
│   ├── mcp.md                              # the MCP tool reference
│   └── api.md                              # states that there is no HTTP interface
│
├── examples/                               # runnable scripts, each beside its captured stdout
│   ├── README.md                           # what each one shows, and what it needs to run
│   ├── backends/                           # the client axis and the cache axis
│   ├── feature-demos/                      # one behaviour each
│   ├── framework-demos/                    # the source inside an agent framework
│   ├── integration-demos/                  # the source inside a registry
│   └── scenario-demos/                     # a whole task, end to end
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
│   │       ├── planning/                   # immutable facts, schedules, task trees, rule safety
│   │       │   ├── __init__.py             # public planning exports
│   │       │   ├── admission.py            # look-ahead checks before optional planning calls
│   │       │   ├── analysis.py             # query analysis and typed constraints
│   │       │   ├── bounds.py               # planning limits and worst-case counts
│   │       │   ├── cardinality.py          # cardinality evidence and parsing
│   │       │   ├── context.py              # immutable planning context and schedule names
│   │       │   ├── coordinator.py          # bounded plan coordination and planning facts
│   │       │   ├── distribution.py         # candidate partition strategies
│   │       │   ├── partition_safety.py     # finite identity rules and accepted decisions
│   │       │   ├── plan.py                 # FetchPlan and its versioned wire form
│   │       │   ├── receipt.py              # closed optional distribution-planning receipt
│   │       │   ├── schedules.py            # the five closed planning schedules
│   │       │   ├── schema.py               # schema and ordered primary-key contracts
│   │       │   └── transport.py            # versioned LeafTask execution DTO
│   │       │
│   │       ├── execution/                  # accepted plan to bounded extraction
│   │       │   ├── __init__.py             # public execution exports
│   │       │   ├── local.py                # LeafTask execution adapter
│   │       │   └── mapper.py               # concurrent leaves and continuation loop
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
│   │       │   ├── verifier.py             # type coercion and mismatch reporting
│   │       │   ├── identity.py             # normalized scalar and composite key identity
│   │       │   └── coverage.py             # deduplication and observed key coverage
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
│       │   ├── __init__.py
│       │   ├── clients.py                  # ScriptedClient, FailingClient
│       │   ├── cache.py                    # cache double
│       │   ├── provider.py                 # LitellmStub, chat_payload, install_litellm
│       │   ├── truncation.py               # complete, truncated, and unanswered responses
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
│           ├── cache/                      # both included stores against the protocol
│           ├── prompts/                    # rendering and substitution safety
│           ├── planning/                   # facts, schedules, plans, bounds, rule safety, receipts
│           ├── execution/                  # leaf mapping, continuation, interruption
│           ├── reduction/                  # parsing, alignment, verification
│           └── compat/                     # inheritance, base order, signature contract,
│                                           #   and the scan for claims the runtime contradicts
│
└── platform/                               # external interface services
    ├── llm-cli/                            # CLI (blue-data-llm-cli PyPI package)
    │   ├── pyproject.toml                  # depends on lib, click, rich; entry point `blue-llmdb`
    │   ├── README.md                       # command reference; every command in it is run by a test
    │   ├── scripts/{build.sh,install.sh,publish.sh}
    │   ├── src/
    │   │   └── blue_data_llm_cli/
    │   │       ├── commands.py             # the command tree: data, model, metadata
    │   │       ├── profile.py              # the `profile` group, family-standard
    │   │       ├── profile_spec.json       # this CLI's own configuration parameters
    │   │       ├── configstore.py          # profile store, precedence chain, secret storage
    │   │       ├── config.py               # option translation, error vocabulary, the streams
    │   │       └── formats.py              # one renderer for table, json, nl and csv
    │   └── tests/
    │       └── unit/                       # commands, profiles, config, formats, errors,
    │                                       #   and the README walkthrough
    │
    └── llm-mcp/                            # MCP server (blue-data-llm-mcp PyPI package)
        ├── pyproject.toml                  # depends on lib and mcp; entry point blue-data-llm-mcp
        ├── README.md                       # tool reference and client configuration
        ├── scripts/{build.sh,install.sh,publish.sh}
        ├── src/
        │   └── blue_data_llm_mcp/
        │       ├── tools.py                # tool specs, JSON schemas, translation, dispatch
        │       └── server.py               # stdio transport; the only `mcp` import
        └── tests/
            └── unit/                       # tools, schemas, ceilings, the server path,
                                            #   and the import boundary
```

---

## Layout Rationale

### `lib/`: the Python library

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
| `source.py` | `LlmSource`: the facade over `blue_data`'s `DataSource`, both query surfaces, `estimate`, and the metadata surface. The only module that imports `blue_data` |
| `checks.py` | Request Intake: argument validation |
| `prompts/` | Request Intake: the single prompt module |
| `planning/` | Planning: immutable facts, five schedules, bounded task trees, identity-rule safety, distribution-strategy selection, and optional receipts |
| `execution/` | Execution: versioned leaf tasks, concurrent mapping, and bounded continuation |
| `llm/client.py` | Model Boundary: `DefaultLlmClient` and `FunctionClient` |
| `llm/budget.py` | Model Boundary: `Limits`, `Budget`, and `Reservation`, the four-axis enforcement |
| `llm/pricing.py` | Model Boundary: the upper bound a cost reservation claims and `estimate` reports, or the refusal that replaces it |
| `cache/memory.py`, `cache/file.py` | Model Boundary: the two included `CacheStore` implementations |
| `reduction/parsing.py` | Reduction: response text to record mappings |
| `reduction/aligner.py` | Reduction: key-to-attribute mapping and reporting |
| `reduction/verifier.py` | Reduction: type coercion, mismatch reporting, and deduplication |
| `reduction/identity.py`, `reduction/coverage.py` | Reduction: normalized composite identity, deduplication, marginal yield, incomplete keys, and collision evidence |
| `types.py` | Shared contracts: `Attr`, `Usage`, `Outcome`, `Capability`, `ExecMeta`, `Estimate`, `FetchResult`, the protocols including `BudgetAwareClient`, and the exception hierarchy |
| `_constants.py` | Shared vocabulary: protocol, variant, the three op codes, the synthesised hierarchy, and the budget and usage-sink property keys |
| `compat/blue_data.py` | Backwards-compatible alias for `LlmSource`, kept for an older import |

### One prompt module

Every prompt the package sends lives in `prompts/`, not as a constant on the class that uses it. Prompts are the highest-churn text in a library of this kind, and duplicate copies can diverge in wording and variable naming. Keeping one maintained copy makes prompt changes and their recorded responses visible together.

### Compatibility import direction

The core imports nothing from `compat/`, and `compat/` imports the core. The direction mattered when the dependency was optional; it is kept because a module that exists only to preserve an older name should not be something the core depends on.

### Contract ownership

Base request, result, accounting, protocol, and exception contracts live in
`lib/src/blue_data_llm_source/types.py`. Planning-specific contracts live under `planning/` and are
re-exported where they are public. This keeps the facade, provider boundary, execution, and reduction
layers from owning each other's types while giving platform adapters one stable package surface.

### `design/` and `docs/`

Two directories of prose at the root, addressed to different readers. `design/` holds the numbered documents that describe what this repository is and why it is shaped this way, plus the note on how they are produced and the layout template they follow; it is read by whoever is changing the design. `docs/` is written for whoever is using the result, and runs from a quickstart through the feature tour to the per-interface references. A claim belongs in one of them and is linked from the other rather than copied.

### `platform/`: external interface services

Each external interface is an independent deployable unit under `platform/`. They depend on `lib` and contain no business logic, so that the library carries no CLI or server dependency and each service can be built, versioned, and released on its own schedule.

| Service | Package | State | Notes |
|---------|---------|-------|-------|
| `llm-cli/` | `blue-data-llm-cli` | Implemented | `blue-llmdb`, 12 commands over four components; Click and Rich |
| `llm-mcp/` | `blue-data-llm-mcp` | Implemented | `llmdb_query`, `llmdb_estimate`, `llmdb_list_models`, `llmdb_inspect_model` and `llmdb_inspect_metadata` over stdio |
| `llm-api/` | — | Not implemented | No REST adapter is scheduled |

Each service follows the same internal structure:

| Path | Purpose |
|------|---------|
| `pyproject.toml` | Package metadata, dependencies including the library, and the console entry point |
| `README.md` | Command or tool reference for that interface |
| `src/<package>/` | Interface adapter code, split into declaration and translation |
| `tests/unit/` | Unit tests for the adapter layer |

Both services separate declaration from translation, for the same reason. The CLI keeps every mapping between what a person typed and what the library accepts in `config.py`, every rendering decision in `formats.py`, and the profile engine in `configstore.py`, so `commands.py` is option declaration and printing and `profile.py` is the Click layer over the engine. The MCP server keeps the tool specifications and the argument translation in `tools.py`, which imports nothing from the protocol SDK, so `server.py` owns the transport alone. In both cases the modules a test cares about are the ones with no framework in them.

Each service's `pyproject.toml` adds the library's source directory to the test path, so the suites run against a checkout without an install step.

### `tests/`

Library tests live under `lib/tests/` and mirror the source layout. Each platform service carries its own `tests/` directory for adapter-layer tests.

Facade tests sit directly under `unit/`, split by concern rather than by module: the fetch path,
planning and execution through the facade, result facts, budgets and admission, outcomes, record
identity and provenance, estimates, documented surfaces, and worked examples each get their own
file. Modules with several distinct concerns get a directory. `planning/` covers facts, schedules,
plans, bounds, distribution search, and task transport; `execution/` covers leaf mapping and
continuation; `llm/` splits client configuration, calls, caching, transport, accounting, and budget;
`reduction/` covers parsing, salvage, alignment, verification, and coverage; and `cache/` exercises
both included stores against the published protocol.

`test_packaging.py` checks a repository-wide dependency boundary rather than one module. It scans the package for an import of `blue` or `blue_data_ops` and fails on either, because this package depends on the data layer but not the orchestration layer above it.

`llm/` also holds `test_pricing.py`, which covers the bound both the cost ceiling and `estimate` rest on. `lib/tests/cassettes/` holds four recorded provider responses and a receipt stating what the recording round cost. `test_cassette_fixtures.py` replays them through the real pipeline, so the specified worked examples are covered end to end with no network connection. `record_cassettes.py` sits beside the suite rather than in it, because it is the one thing here that makes real calls, and it is run by hand when a prompt change makes the recordings stale.

`lib/tests/doubles/` holds the doubles the suite needs, one per boundary the package owns:

| Double | Boundary |
|--------|----------|
| `ScriptedClient`, `FailingClient` | The model, injected structurally as a client |
| `TruncatingModel` | Complete, truncated, refused, and unanswered model responses |
| `LitellmStub` | The provider transport, installed over the lazy import |
| Cache double | A caller-supplied store |
| Offline guard | Network, subprocess, and database access, refused and counted |

Each test subdirectory carries an `__init__.py`, so identically named modules in sibling directories do not collide during collection.

Platform adapter tests define their own fixtures rather than importing the library's, since each adapter exercises a different entry point, and each replaces the single source-construction function in its own package to inject a scripted client. No adapter test reaches a provider, and the MCP tool tests run without the protocol SDK installed.

The CLI carries one test of an unusual kind, which reads its own README and runs the commands it documents; see [11-testing-strategy.md](11-testing-strategy.md).
