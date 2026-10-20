# Installation

This page describes supported installation paths for the library, CLI, and MCP server.

## Components

`blue-data-llm-source` consists of a core library (`lib/`) and two platform components (`platform/`). Install `lib` first. Both platform components depend on it, and it depends on `lib/` from a local checkout of `blue-data`, which supplies the `DataSource` base class.

```
blue-data-llm-source/
├── lib/                  blue-data-llm-source  — the core Python library
└── platform/
    ├── llm-cli/          blue-data-llm-cli     — the command-line interface
    └── llm-mcp/          blue-data-llm-mcp     — the MCP server
```

| Package | Directory | Version | Depends on |
|---|---|---|---|
| `blue-data-llm-source` | `lib/` | 2.2.0 | `blue-data>=0.1.0`, `tenacity>=9.0.0` |
| `blue-data-llm-cli` | `platform/llm-cli/` | 2.2.0 | `blue-data-llm-source[llm]>=2.2.0`, `click>=8.0`, `rich>=13.0` |
| `blue-data-llm-mcp` | `platform/llm-mcp/` | 2.2.0 | `blue-data-llm-source[llm]>=2.2.0`, `mcp>=1.28.1,<2` |

The `mcp` pin is exact rather than a floor. The low-level server API that component is built on was removed in `mcp` 2.0, so a floor would resolve to a release where the server cannot import.

## Requirements

- Python 3.10 or later.
- A local checkout of the `blue-data` repository. It is not on a public package index, so it must be installed from source.
- A provider credential in the environment for any request that reaches a model. The library never reads it; the provider SDK does. A request with an injected client or an `llm_fn` needs none.

Clone `blue-data` into the parent directory of this checkout. The commands below expect two sibling repositories, so `../blue-data/lib` resolves from the `blue-data-llm-source` root:

```text
workspace/
├── blue-data/
└── blue-data-llm-source/
```

Installing `blue-data` also installs `pandas`, `numpy`, and `jsonpath-ng`.

## Scripts

Each component has a `scripts/` directory containing the same three scripts. They resolve paths from their own locations and can run from any working directory.

| Script | What it does |
|---|---|
| `install.sh` | Editable install (`pip install -e .`) with the component's own extras, after installing everything it depends on |
| `build.sh` | `python -m build` into the component's `dist/`, producing a wheel and an sdist |
| `publish.sh` | `twine upload dist/*` |

`lib/scripts/install.sh` installs `blue-data` from a checkout first, then `lib` with the `dev` and `llm` extras. It looks for `blue-data` at `../blue-data/lib` relative to the repository root, and exits with a message naming the path when it is not there. Set `BLUE_DATA_PATH` to point it elsewhere.

Both platform `install.sh` scripts call `lib/scripts/install.sh` first and then install their own package with the `dev` extra. Running one of them is therefore enough to install everything that component needs.

## Conda environment

Run these commands from the `blue-data-llm-source` checkout. Create the environment once; Conda may ask for confirmation. If `blue-llmdb` already exists, skip this block and activate it below.

```bash
ls -d ../blue-data/lib &&
conda create --name blue-llmdb python=3.12 pip
```

Paste the next block after Conda finishes. The chain stops if activation or an install fails. `blue-data` goes in first; the platform packages then resolve the installed library and its provider extra.

```bash
conda activate blue-llmdb &&
python -m pip install -e ../blue-data/lib &&
python -m pip install -e "./lib[llm]" &&
python -m pip install -e ./platform/llm-cli &&
python -m pip install -e ./platform/llm-mcp &&
blue-llmdb metadata inspect
```

If Conda cannot activate the environment, run `conda init zsh` (or `conda init bash`) once, open a new terminal, return to this checkout, and rerun the second block. If the CLI is already installed, activate its environment and start with `blue-llmdb metadata inspect`.

When `blue-data` lives elsewhere, replace both `../blue-data/lib` paths with its `lib/` directory. The Conda environment works for running the packages and examples. The type gate uses the component-local `.venv` paths configured in `pyproject.toml`.

## Editable install

Satisfy [Requirements](#requirements) first. Both routes below assume a `blue-data` checkout is already on disk, and `lib/scripts/install.sh` exits with a message rather than guessing when it cannot find one.

Every route below needs network access. Only `blue-data` comes from the local checkout; all other dependencies are resolved from a package index. These editable installs make Python import each package from its working tree rather than from a copy under `site-packages`. After installing the CLI component, `blue-llmdb --version` prints the installed version.

The whole repository, in dependency order:

```bash
python -m venv .venv && source .venv/bin/activate
lib/scripts/install.sh
platform/llm-cli/scripts/install.sh
platform/llm-mcp/scripts/install.sh
```

With `blue-data` somewhere other than the sibling default:

```bash
BLUE_DATA_PATH=/path/to/blue-data/lib lib/scripts/install.sh
```

The library alone, which is all you need to use it from Python:

```bash
python -m venv .venv && source .venv/bin/activate
lib/scripts/install.sh
python -c "import blue_data_llm_source; print(blue_data_llm_source.__version__)"
```

## Manual install

The same thing without the scripts, one package at a time:

```bash
pip install -e /path/to/blue-data/lib
pip install -e lib
pip install -e platform/llm-cli
pip install -e platform/llm-mcp
```

This is the route to take when `blue-data` is already installed, or when you want a different set of extras than the scripts choose. `blue-data` must be in place before `lib`, or the install fails to resolve it and reads as a broken package rather than a missing sibling.

## Optional extras

### `blue-data-llm-source` (`lib/`)

| Extra | Packages | Required for |
|---|---|---|
| `llm` | `litellm>=1.102.0,<2`, `diskcache>=5.6,<6` | Reaching a provider through the built-in client, and the `llm.cache` on-disk response cache |
| `all` | the same two | An alias for `llm` |
| `dev` | `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `pyright`, `build`, `twine` | Running the tests and the lint, type, build, and publish gates |

```bash
pip install -e "lib[llm]"
```

Both `llm` packages are imported lazily inside the call that needs them. The library can therefore be installed and its full test suite run without either package. A request that would reach a provider without them raises `ConfigError` naming the extra.

### `blue-data-llm-cli` and `blue-data-llm-mcp`

| Extra | Packages | Required for |
|---|---|---|
| `dev` | `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `pyright`, `build`, `twine` | Running that component's tests and gates |

The MCP server's `dev` extra additionally carries `jsonschema`, which its schema tests use.

Neither platform package has a provider extra of its own. Both depend on `blue-data-llm-source[llm]` unconditionally, since every deployment of either can reach a model.

## Building distributions

```bash
lib/scripts/build.sh
platform/llm-cli/scripts/build.sh
platform/llm-mcp/scripts/build.sh
```

Each writes a wheel and an sdist into that component's `dist/`. `build.sh` needs the `build` package, which comes with the `dev` extra.

`publish.sh` runs `twine upload dist/*` against whatever index `twine` is configured for. No package here is on an index; see [Distribution](#distribution).

## Running tests

Run these commands from the repository root. Each command enters one component in a subshell, and its `pyproject.toml` puts the correct source roots on the path, so no `PYTHONPATH` is needed.

```bash
(cd lib && python -m pytest tests/unit -q)
(cd platform/llm-cli && python -m pytest tests/unit -q)
(cd platform/llm-mcp && python -m pytest tests/unit -q)
```

Unit tests only. Every I/O boundary is doubled and no test touches the network or needs a credential. Coverage is not collected by default and no threshold is enforced; pass `--cov=src` to measure it.

For the type check, each `pyproject.toml` points `pyright` at a `.venv` in that component's directory. Create one there before installing that component; without it pyright reports every import as unresolved instead of real typing errors.

`lib/tests/unit/compat/test_contract.py` compares this source's method signatures with the installed `blue_data.source.source.DataSource` and prints the inspected package path. Run it after installing or upgrading `blue-data` so the comparison covers the package used by the current environment.

## Environment variables

The library runtime defines no environment variables of its own. The install script and provider SDK read the two entries below.

| Variable | Read by | Purpose |
|---|---|---|
| `BLUE_DATA_PATH` | `lib/scripts/install.sh` | Where to find the `blue-data` checkout's `lib` directory. Defaults to `../blue-data/lib` from the repository root, a `blue-data` checkout beside this one |
| The provider's API key variable | The provider SDK, at call time | Authenticates a request that reaches a model. Which name applies depends on the model you configure |

Because the library never reads a credential, it cannot report one missing. An absent or wrong key arrives as a failed provider call, which reaches you as a `TransportError`. Read a `TransportError` that mentions credentials as "the key is absent or wrong" rather than as a network fault.

The command-line interface has its own configuration, which is documented in [cli.md](cli.md). The MCP server's is in [mcp.md](mcp.md).

## Distribution

None of the three packages is on PyPI or on an internal index, and neither is `blue-data`. Install from a local checkout as shown above. Where a design document describes how these are distributed, it is describing an intended route rather than one that works today.

## Next steps

- New to this? [quickstart.md](quickstart.md) is one path in under five minutes.
- Writing Python against the library? [guide.md](guide.md), then [api-reference.md](api-reference.md).
- Deciding whether it fits your system? [architecture.md](architecture.md).
