# Repo Organization Template

A blank skeleton for a repository that ships one Python library alongside one or more deployable interfaces: a CLI, an MCP server, a REST service. Copy the tree, replace the angle-bracket names, and delete the rows that do not apply.

This is the generic form. The filled-in version for this repository, with its real modules and the rationale for each, is [07-repo-organization.md](07-repo-organization.md).

---

## Directory Tree

```
<repo-root>/
│
├── README.md                              # one page: what it is, layout, quickstart, package table
├── LICENSE                                # same license as the sibling repos in the release
├── CHANGELOG.md                           # one section per release, newest first
├── .gitignore                             # .venv/ __pycache__/ .coverage *.envrc .env build/ dist/ *.egg-info/
│
├── design/                                # internal — why it is built this way
│   ├── 01-overview.md                     # objective, what it does, design philosophy
│   ├── 02-scenarios.md                    # how it is used, end to end
│   ├── 03-high-level-architecture.md      # components and what each owns
│   ├── 04-detailed-architecture.md        # how information moves between them
│   ├── 05-software-stack.md               # language, runtime, dependencies, extras
│   ├── 06-library.md                      # key classes and functions, one section per module
│   ├── 07-repo-organization.md            # this tree, filled in, plus a layout rationale
│   ├── 08-coding-style.md                 # tooling table, typing, naming, docstrings, errors
│   ├── 09-api-interface-spec.md           # every external method, command, endpoint, tool
│   ├── 10-data-model.md                   # entities, fields, types, relationships
│   ├── 11-testing-strategy.md             # what is tested, at what level, what it proves
│   ├── 12-deployment-ops.md               # install, configure, run, operate
│   ├── 13-decision-log.md                 # ADRs — created first, appended, never finished
│   ├── design-process.md                  # the sequence above and the review protocol
│   └── repo-organization-template.md      # this file — the reusable skeleton
│
├── docs/                                  # external — how to use it; tracks current behaviour
│   ├── README.md                          # landing page: "read this if you want to..." tables
│   ├── quickstart.md                      # one path, under five minutes, copy-pasteable
│   ├── installation.md                    # every install method a script here implements
│   ├── features.md                        # every feature, one short example each
│   ├── guide.md                           # task-by-task walkthrough of the library
│   ├── api-reference.md                   # exhaustive: every signature, parameter, return
│   ├── architecture.md                    # component diagram and data flow, user-facing
│   ├── extending.md                       # the extension points and what a caller may replace
│   └── <interface>.md                     # one per external interface; mirrors its package README
│
├── examples/                              # runnable, with real captured output beside each script
│   ├── README.md                          # grouped by purpose; run command and requirements per script
│   └── <category>/
│       ├── <example>.py                   # the script, runnable as documented
│       └── <example>.output.txt           # its real captured stdout
│
├── lib/                                   # the library — the only home of business logic
│   ├── pyproject.toml                     # package metadata, extras, tool config — here, not at the root
│   ├── README.md                          # install and a minimal runnable usage snippet
│   ├── scripts/
│   │   ├── build.sh                       # build wheel and sdist
│   │   ├── install.sh                     # editable install with the dev extra
│   │   └── publish.sh                     # publish the distribution
│   ├── src/
│   │   └── <lib_package>/                 # src layout: not importable from the component root
│   │       ├── __init__.py                # the public surface, re-exported
│   │       ├── py.typed                   # ships inline types to a caller's type checker
│   │       ├── types.py                   # shared contracts, protocols, exception hierarchy
│   │       └── <layer>/                   # one directory per architecture component
│   │           ├── __init__.py
│   │           └── <component>.py
│   └── tests/
│       ├── conftest.py                    # shared fixtures and the guards that apply to every test
│       ├── doubles/                       # one double per boundary the package owns
│       └── unit/
│           └── <layer>/                   # mirrors the source layout
│
└── platform/                              # deployable interfaces — thin adapters over lib
    └── <family>-<service>/                # e.g. web-cli, user-mcp — never a bare `cli` or `mcp`
        ├── pyproject.toml                 # depends on lib; declares this interface's console entry point
        ├── README.md                      # this interface's own usage; source of truth for docs/<interface>.md
        ├── scripts/
        │   ├── build.sh                   # build this service's artifact
        │   ├── install.sh                 # install it, with lib resolved from the checkout
        │   └── publish.sh                 # publish or deploy it
        ├── src/
        │   └── <service_package>/
        │       ├── __init__.py
        │       ├── <declaration>.py       # inputs, schemas, routes — what the caller sees
        │       └── <translation>.py       # the mapping to library arguments; no business logic
        └── tests/
            └── unit/
```

---

## Mandatory, and What Varies

| Entry | Status | What varies |
|---|---|---|
| `README.md`, `LICENSE`, `.gitignore` | Mandatory | Nothing. The license matches the sibling repositories in the release |
| `CHANGELOG.md` | Mandatory once anything is released | Sectioning style, as long as released sections are records and are not rewritten |
| `design/` 01 through 13 | Mandatory, all thirteen, numbered with no gaps | A repository with no external interface still writes 09, describing the library surface |
| `design/design-process.md` | Mandatory | Nothing (it is the family's, copied as is) |
| `design/repo-organization-template.md` | Mandatory | Nothing (it is this file, copied as is) |
| `docs/` | Mandatory | Which interface documents exist: one per interface the code actually ships, and no more |
| `docs/extending.md` | Mandatory where the library has extension points | Omitted where it has none, rather than written empty |
| `examples/` | Mandatory | The categories, and whether a script needs external services, which `examples/README.md` states per script |
| `lib/` | Exactly one, always | The internal layer directories, which follow the architecture rather than a fixed list |
| `lib/src/<package>/types.py` | Mandatory | Nothing. Shared contracts live in one module so layers do not own each other's types |
| `lib/src/<package>/py.typed` | Mandatory for a typed library | Nothing |
| `platform/<service>/` | One per deployable interface; zero is valid | Which interfaces exist. A planned one is absent, not stubbed |
| `scripts/build.sh`, `scripts/install.sh` | Mandatory in `lib/` and in every service | Their contents, which differ per artifact type |
| `scripts/publish.sh` or `deploy.sh` | Mandatory in anything published or deployed | Publish for a distribution, deploy for a service |
| `Dockerfile` | Per service, where the service is containerized | Omitted entirely for a CLI or a subprocess-launched server |

---

## Naming

| Thing | Rule | Example |
|---|---|---|
| Distribution name | `blue-data[-<family>]-<service>` | `blue-data-<family>-cli` |
| Service directory | `<family>-<service>`, never a bare role name | `<family>-cli`, not `cli` |
| Importable package | The distribution name with underscores | `blue_data_<family>_cli` |
| Design document | Two-digit number, hyphen, lowercase words | `03-high-level-architecture.md` |
| Console command | Names the repository, not the role | Not a short generic word another tool also uses |

---

## Principles

**`lib/` is the only home of business logic.** Everything a caller could get wrong is decided there. A `platform/` service declares its inputs, converts them to library arguments, calls the library, and renders the result. A rule implemented in one service does not bind a caller of another, which is why a service that needs a behaviour asks the library for it rather than adding it.

**Each service is independently buildable and releasable.** Its own project file, its own dependencies and pins, its own scripts. A pin one interface needs then stops at that interface instead of reaching a caller who only wanted the library.

**`src/` layout everywhere.** Nothing is importable from a component root, so a suite cannot pass against a working tree that the built distribution does not match. Each project file puts the same roots on the test path, so suites still run on a checkout with no install step.

**One project file per component, none at the root.** The unit of release is the component. A root project file makes it the repository, and the components can then only move together.

**`types.py` owns the shared contracts.** Data contracts, protocols, and the exception hierarchy live in one module, which is what keeps the layers from importing each other for a type and what gives a service adapter one stable surface to import.

**Tests sit with what they test.** Library tests under `lib/tests/`, mirroring the source layout. Each service's adapter tests under its own `tests/`. A service's tests define their own fixtures, because each adapter exercises a different entry point.

**A doc that duplicates another says which is the source of truth.** `docs/<interface>.md` mirrors `platform/<service>/README.md` and says so in its own text, so a reader who finds a disagreement knows which one to fix.
