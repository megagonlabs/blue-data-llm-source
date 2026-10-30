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
| `blue-data-llm-source` | `lib/` | 1.0.0 | `blue-data>=1.0.0,<2`, `tenacity>=9.0.0` |
| `blue-data-llm-cli` | `platform/llm-cli/` | 1.0.0 | `blue-data-llm-source[llm]==1.0.0`, `blue-data==1.0.0`, `click>=8.0`, `rich>=13.0` |
| `blue-data-llm-mcp` | `platform/llm-mcp/` | 1.0.0 | `blue-data-llm-source[llm]==1.0.0`, `mcp>=1.28.1,<2` |

The `mcp` requirement is bounded below 2 rather than only floored. The low-level server API that component is built on was removed in `mcp` 2.0, so a bare floor would resolve to a release where the server cannot import. The 1.28.1 floor excludes releases with a published advisory.

## Requirements

- Python 3.12 or 3.13 (declared range `>=3.12,<3.14`). Python 3.12 is the standard setup.
- A local checkout of the `blue-data` repository. It is not on a public package index, so it must be installed from source.
- A provider credential in the environment for any request that reaches a model. The provider SDK uses it for authentication; the library reads credential environment values only for diagnostic masking. A request with an injected client or an `llm_fn` needs none.

Obtain access to the separate `blue-data` repository from your organization, then clone it into the parent directory of this checkout and check out its `1.0` branch. The CLI requires `blue-data==1.0.0`, which provides `blue_data.utils.config`. The commands below expect two sibling repositories, so `../blue-data/lib` resolves from the `blue-data-llm-source` root:

```text
workspace/
├── blue-data/
└── blue-data-llm-source/
```

Installing `blue-data` also installs `pandas`, `numpy`, and `jsonpath-ng`.

The CLI normally creates its profile directory on first use. By default it writes `~/.blue-llmdb/.profiles` and `.selected.profile`. If the directory cannot be created, `metadata inspect`, `model list`, `model inspect`, and `data estimate` use in-memory defaults; queries and profile writes need a writable home. Set `BLUE_DATA_LLM_SOURCE_HOME` to a writable directory for those commands when the default home is unavailable.

## Scripts

Each component has a `scripts/` directory containing the same three scripts. They resolve paths from their own locations and can run from any working directory.

| Script | What it does |
|---|---|
| `install.sh` | Editable install (`pip install -e .`) with the component's own extras, after installing everything it depends on |
| `build.sh` | `python -m build` into the component's `dist/`, producing a wheel and an sdist |
| `publish.sh` | Checks that `dist/` contains only the current version's wheel and sdist, then validates and uploads them to the URL supplied as its argument |

`lib/scripts/install.sh` installs `blue-data` from a checkout first, then `lib` with the `dev` and `llm` extras. It looks for `blue-data` at `../blue-data/lib` relative to the repository root, and exits with a message naming the path when it is not there. Set `BLUE_DATA_PATH` to point it elsewhere.

Both platform `install.sh` scripts call `lib/scripts/install.sh` first and then install their own package with the `dev` extra. Running one of them is therefore enough to install everything that component needs.

## Conda environment

Use these steps to install the packages in a Conda environment. The quick start uses a checkout-local `venv`.

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

When `blue-data` lives elsewhere, replace both `../blue-data/lib` paths with its `lib/` directory. The Conda environment works for running the packages and examples. Type checks use the component-local `.venv` paths configured in `pyproject.toml`. Core and CLI type checks also read `../blue-data/lib/src` in the sibling layout above because the SDK wheel has no typing marker. If the SDK checkout is elsewhere, update their Pyright `extraPaths` to its `lib/src` directory; `BLUE_DATA_PATH` only controls the installer.

## Editable install

Satisfy [Requirements](#requirements) first. Both routes below assume a `blue-data` checkout is already on disk, and `lib/scripts/install.sh` exits with a message rather than guessing when it cannot find one.

Every route below needs network access. Only `blue-data` comes from the local checkout; all other dependencies are resolved from a package index. These editable installs make Python import each package from its working tree rather than from a copy under `site-packages`. After installing the CLI component, `blue-llmdb --version` prints the installed version.

Use a new Python 3.12 environment for these commands. If `.venv` exists, activate it and check `python --version`. The creation blocks stop if `.venv` already exists.

The whole repository, in dependency order. Run this from the `blue-data-llm-source` root:

```bash
test ! -e .venv &&
python3.12 -m venv .venv &&
source .venv/bin/activate &&
lib/scripts/install.sh &&
platform/llm-cli/scripts/install.sh &&
platform/llm-mcp/scripts/install.sh
```

With `blue-data` somewhere other than the sibling default, replace `/path/to/blue-data/lib` with that checkout's `lib/` directory before running this block:

```bash
export BLUE_DATA_PATH=/path/to/blue-data/lib
lib/scripts/install.sh
platform/llm-cli/scripts/install.sh
platform/llm-mcp/scripts/install.sh
```

The library alone, which is all you need to use it from Python. Run this from the checkout root:

```bash
test ! -e .venv &&
python3.12 -m venv .venv &&
source .venv/bin/activate &&
lib/scripts/install.sh &&
python -c "import blue_data_llm_source; print(blue_data_llm_source.__version__)"
```

## Manual install

The same thing without the scripts, one package at a time. Replace `/path/to/blue-data/lib` with the `lib/` directory in your `blue-data` checkout:

```bash
python -m pip install -e /path/to/blue-data/lib
python -m pip install -e lib
python -m pip install -e platform/llm-cli
python -m pip install -e platform/llm-mcp
```

This is the route to take when `blue-data` is already installed, or when you want a different set of extras than the scripts choose. `blue-data` must be in place before `lib`, or the install fails to resolve it and reads as a broken package rather than a missing sibling.

## Optional extras

### `blue-data-llm-source` (`lib/`)

| Extra | Packages | Required for |
|---|---|---|
| `llm` | `litellm>=1.102.0,<2` and `diskcache>=5.6,<6` | Reaching a provider through the built-in client, and the `llm.cache` on-disk response cache |
| `all` | the same dependencies | An alias for `llm` |
| `dev` | `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `pyright`, `build`, `twine` | Running the tests and the lint, type, build, and publish gates |

```bash
python -m pip install -e "lib[llm]"
```

LiteLLM and DiskCache are imported lazily inside the call that needs them. The library can be installed without them; store-dependent tests skip when `diskcache` is absent. Install the `dev` and `llm` extras to run those tests as well. A request that would reach a provider without `litellm` raises `ConfigError` naming the extra.

### `blue-data-llm-cli` and `blue-data-llm-mcp`

| Extra | Packages | Required for |
|---|---|---|
| `dev` | `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `pyright`, `build`, `twine` | Running that component's tests and gates |

The MCP server's `dev` extra additionally carries `jsonschema`, which its schema tests use.

Neither platform package has a provider extra of its own. Both depend on `blue-data-llm-source[llm]` unconditionally, since every deployment of either can reach a model.

## Installing exact built wheels

Installed copies of the earlier releases in the [changelog](../CHANGELOG.md) report their original numbers: 0.1.1.0 to 0.2.2.1 report 1.1.0 to 2.2.1, which pip ranks above this release, and 0.1.0.0 reports 1.0.0, the same number as this one. A version number alone does not identify a build. Use wheels built from the checkouts above, or obtain them from your release maintainer. The CLI requires shared `blue-data==1.0.0`; build that wheel from the shared SDK's `1.0` checkout. Set `BLUE_DATA_WHEEL`, `LIBRARY_WHEEL`, `CLI_WHEEL` and `MCP_WHEEL` to the four local wheel paths. Install them into a fresh environment with compatible dependencies already installed:

```bash
python -m pip install --no-index --no-deps --force-reinstall \
  "$BLUE_DATA_WHEEL" "$LIBRARY_WHEEL" \
  "$CLI_WHEEL" "$MCP_WHEEL"
```

Run outside the source trees with `PYTHONPATH` unset. `--no-deps` does not resolve dependencies: check the installed requirements separately. An ordinary upgrade from 0.2.2.1 (installed copies report 2.2.1) will not select 1.0.0, which pip ranks lower.

Tested with Python 3.12.14 and LiteLLM 1.106.5 on macOS ARM64. The packages declare Python 3.12 and 3.13, the versions on which the whole Blue DI 1.0 family installs and runs together.

### Python 3.15 installation

Python 3.15 is outside the supported range, and pip refuses to install these packages on it. The fastuuid and LiteLLM compatibility builders under `lib/scripts/` and `lib/scripts/python315-constraints.txt` are kept in the repository but are not part of a supported installation.

## Building distributions

```bash
lib/scripts/build.sh
platform/llm-cli/scripts/build.sh
platform/llm-mcp/scripts/build.sh
```

Each writes a wheel and an sdist into that component's `dist/`. `build.sh` needs the `build` package, which comes with the `dev` extra.

The isolated build resolves `setuptools>=68` and `wheel` from the configured package index unless they are already cached. The sdists contain package source and metadata, but not the repository's tests or `lib/COMPATIBILITY.md`.

### Installing from built wheels

From the `blue-data-llm-source` root in an active virtual environment, install the sibling `blue-data` checkout first, then give pip all three local wheels in one command:

```bash
python -m pip install ../blue-data/lib
python -m pip install lib/dist/*.whl platform/llm-cli/dist/*.whl platform/llm-mcp/dist/*.whl
```

The library wheel requires `blue-data>=1.0.0,<2`; a CLI or MCP wheel also requires `blue-data-llm-source[llm]==1.0.0`. Pip cannot find those local dependencies if you give it only a platform wheel. If you publish these wheels, make `blue-data` and the library wheel available from the same package index as the platform wheels.

`publish.sh` requires an explicit HTTPS repository URL and exactly the current package version's wheel and sdist in `dist/`. Pass the URL as its sole argument, without credentials in it. It runs `twine check` before uploading. Twine's `--repository-url` bypasses repository credentials in `.pypirc`; use `TWINE_USERNAME` and `TWINE_PASSWORD`, keyring, or an interactive prompt. Remove older archives from `dist/` before using it. No package here is on an index; see [Distribution](#distribution).

## Running tests

Run these commands from the repository root after installing the `dev` and `llm` extras. Each command enters one component in a subshell, and its `pyproject.toml` puts the correct source roots on the path, so no `PYTHONPATH` is needed.

```bash
(cd lib && python -m pytest tests/unit -q)
(cd platform/llm-cli && python -m pytest tests/unit -q)
(cd platform/llm-mcp && python -m pytest tests/unit -q)
```

Unit tests only. Every I/O boundary is doubled and no test touches the network or needs a credential. Coverage is not collected by default and no threshold is enforced; pass `--cov=src` to measure it.

Seven pricing tests skip when the tokenizer encoding table is not already cached locally. Run `python -m pytest tests/unit/llm/test_pricing.py -rs` from `lib/` to see the skip reasons. With the table cached, those tests cross-check the byte bound against a real tokenizer.

For the type-check development gate, each `pyproject.toml` points `pyright` at a `.venv` in that component's directory. Create a separate environment there and install that component's development dependencies before running Pyright; the root `.venv` is the user install. Pyright may need to download Node on its first run.

`lib/tests/unit/compat/test_contract.py` compares this source's method signatures with the installed `blue_data.source.source.DataSource` and prints the inspected package path. Run it after installing or upgrading `blue-data` so the comparison covers the package used by the current environment.

## Environment variables

The library defines no environment-based request settings. The install script and provider SDK use the entries below; the library also reads credential-like environment values to mask diagnostic text.

| Variable | Read by | Purpose |
|---|---|---|
| `BLUE_DATA_PATH` | `lib/scripts/install.sh` | Where to find the `blue-data` checkout's `lib` directory. Defaults to `../blue-data/lib` from the repository root, a `blue-data` checkout beside this one |
| The provider's API key variable | The provider SDK for authentication; the library redactor for diagnostic masking | Authenticates a request that reaches a model. Which name applies depends on the model you configure |

The provider SDK handles authentication. An absent or wrong key can fail a provider request. With no answered extraction response and no retained records, the fetch raises `TransportError` if the retry policy is exhausted or `max_calls` prevents the next retry. If `max_cost` or `max_seconds` refuses that retry, or a ceiling refuses the first attempt, it returns `stopped_by_limit`. If an extraction response has already been parsed, the fetch keeps its records with `outcome=partial` and a gap for the failed part.

The command-line interface has its own configuration, which is documented in [cli.md](cli.md). The MCP server's is in [mcp.md](mcp.md).

## Distribution

None of the three packages or `blue-data` is on a package index. Install from local checkouts or built wheels as shown above.

## Next steps

- New to this? [quickstart.md](quickstart.md) gives one path through installation and a query.
- Writing Python against the library? [guide.md](guide.md), then [api-reference.md](api-reference.md).
- Deciding whether it fits your system? [architecture.md](architecture.md).
