# blue-data-llm-source

`blue-data-llm-source` makes an LLM's parametric knowledge queryable as structured tables (natural language → LLM → table).

Agentic systems may require common-sense information beyond the coverage of maintained data sources, or additional tables that connect records across sources. For large data requests, the source can rewrite queries, infer an output schema, estimate row count, and organize retrieval into hierarchical tasks, with partitioned execution and result aggregation following a MapReduce pattern.

→ **[Full documentation](docs/README.md)** · **[Quick start](docs/quickstart.md)** · **[Installation](docs/installation.md)** · **[Examples](examples/README.md)** · **[CLI](docs/cli.md)** · **[MCP](docs/mcp.md)**

## What it does
A caller submits a natural-language request and may declare an attribute schema. For larger data requests, the source can plan retrieval beyond a single model response. In planned modes, it can rewrite the request, infer or complete the output schema, estimate row count, and divide the query into hierarchical data tasks. It executes those tasks under explicit limits, checks the returned records against the schema, and merges records that share an identified key. The Python library, command-line interface, and MCP server expose the records and execution metadata. The records can be used alongside those from other Blue data sources.

- **Fetch** records from a natural-language request, using caller-declared attributes and types or a schema inferred in a planned mode.
- **Plan** requests in a selected non-flat mode by rewriting the query, estimating row count, and forming bounded hierarchical tasks. The estimate is a planning hint, not a coverage measure.
- **Partition** a request into smaller model calls, continue a part when more records are needed, and merge records by an identified key.
- **Check** returned fields and declared types while reporting outcomes, model usage, costs, observed gaps, and record origins in partitioned runs.
- **Bound** returned rows when a row limit is set and, with a reservation-aware client, provider calls, time, and estimated cost. Optional caches reuse equivalent responses.
- **Integrate** generated records with other Blue sources through the shared `DataSource` interface. The Python library, `blue-llmdb` command, and MCP server expose the records to callers.

See [features](docs/features.md) for the behavior and its limits, and [examples](examples/README.md) for runnable programs.

## Repository layout

```text
blue-data-llm-source/
├── README.md                       # this overview
├── CHANGELOG.md                    # release history
├── LICENSE                         # repository license
├── NOTICE                          # attribution notice shipped with each package
├── CODE_OF_CONDUCT.md              # Contributor Covenant code of conduct
├── .gitignore                      # local environments, caches, and build output
├── lib/                            # blue-data-llm-source Python library
│   ├── README.md                   # library methods, settings, and limits
│   ├── COMPATIBILITY.md            # DataSource integration and migration
│   ├── LICENSE                     # library license
│   ├── NOTICE                      # attribution notice
│   ├── pyproject.toml              # package metadata and development tools
│   ├── scripts/install.sh          # installs sibling blue-data and the library
│   ├── scripts/build.sh            # builds wheel and sdist
│   ├── scripts/publish.sh          # validates and uploads distributions
│   ├── src/blue_data_llm_source/   # source, planning, execution, and model clients
│   └── tests/                      # library tests
├── platform/
│   ├── llm-cli/                    # blue-llmdb command, installer, license and notice, and tests
│   └── llm-mcp/                    # blue-data-llm-mcp stdio server, installer, license and notice, and tests
├── docs/                           # installation, user guides, and API reference
│   ├── README.md                   # documentation index
│   ├── quickstart.md               # provider-backed walkthrough
│   ├── installation.md             # installation paths and extras
│   ├── features.md                 # feature examples and limits
│   ├── guide.md                    # Python usage
│   ├── api-reference.md            # library API and settings
│   ├── extending.md                # client and cache extensions
│   ├── cli.md                      # command reference
│   ├── mcp.md                      # server and tool reference
│   └── architecture.md             # component and request flows
├── examples/                       # runnable scripts and captured output
│   ├── backends/                   # model-client and cache examples
│   ├── feature-demos/              # individual source behaviors
│   ├── framework-demos/            # agent framework integrations
│   ├── integration-demos/          # Blue data-layer examples
│   └── scenario-demos/             # complete retrieval tasks
├── tests/
│   └── integration/                # offline integration checks
└── design/                         # architecture, interface, and repository documents
```

## Requirements

- Python 3.12 or 3.13 (declared range `>=3.12,<3.14`); the commands below use Python 3.12.
- A `blue-data` checkout beside this repository, on its `1.0` branch: the CLI requires `blue-data==1.0.0`, which provides `blue_data.utils.config`. Its `lib/` directory is a local dependency and is not available from a package index. The installer looks for `../blue-data/lib` from this repository root; set `BLUE_DATA_PATH` to that directory if the checkout is elsewhere.
- Access to the declared Python dependencies through a package index or local wheels during installation.
- A provider API key for calls made through the built-in model client. The offline quick start below uses a local function and needs no key.

Keep the two checkouts under one parent directory:

```text
workspace/
├── blue-data/
└── blue-data-llm-source/
```

For a provider-backed OpenAI call, enter the key without putting its value in shell history:

```bash
printf 'OpenAI API key: '
IFS= read -rs OPENAI_API_KEY
printf '\n'
export OPENAI_API_KEY
```

See [installation](docs/installation.md) for component installers, extras, and other supported environments.

## Quick start

From the `blue-data-llm-source` checkout, with `blue-data` beside it and Python 3.12 installed. Use a new environment; for an existing `.venv`, check its Python version before reusing it.

```bash
test ! -e .venv &&
python3.12 -m venv .venv &&
source .venv/bin/activate &&
python --version &&
./lib/scripts/install.sh &&
./platform/llm-cli/scripts/install.sh &&
python examples/backends/function_client_quickstart.py
```

The example passes a local answer through the normal fetch, parsing, type-checking, and result-metadata path. The CLI is also installed in this environment. For a provider-backed query or optional MCP setup, continue with the [full quick start](docs/quickstart.md).

## Packages

| Package | Description |
|---|---|
| [lib/](lib/README.md) | `blue-data-llm-source`: the `LlmSource` Python library; installs no console command |
| [platform/llm-cli/](platform/llm-cli/README.md) | `blue-data-llm-cli`: installs the `blue-llmdb` command for profiles, model inspection, cost estimation, and record queries |
| [platform/llm-mcp/](platform/llm-mcp/README.md) | `blue-data-llm-mcp`: installs the `blue-data-llm-mcp` command serving five tools over stdio |

## Code of conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md), the same one that governs Blue. By participating, you are expected to uphold it. Report unacceptable behavior to contact_oss@megagon.ai.

## Disclosures

This software may include, incorporate, or access open source software (OSS) components, datasets and other third party components, including those identified below. The license terms respectively governing the datasets and third-party components continue to govern those portions, and you agree to those license terms may limit any distribution, use, and copying. You may use any OSS components under the terms of their respective licenses, which may include BSD 3, Apache 2.0, and other licenses. In the event of conflicts between Megagon Labs, Inc. (“Megagon”) license conditions and the OSS license conditions, the applicable OSS conditions governing the corresponding OSS components shall prevail. You agree not to, and are not permitted to, distribute actual datasets used with the OSS components listed below. You agree and are limited to distribute only links to datasets from known sources by listing them in the datasets overview table below. You agree that any right to modify datasets originating from parties other than Megagon are governed by the respective third party’s license conditions. You agree that Megagon grants no license as to any of its intellectual property and patent rights. THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS (INCLUDING MEGAGON) “AS IS” AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE. You agree to cease using, incorporating, and distributing any part of the provided materials if you do not agree with the terms or the lack of any warranty herein. While Megagon makes commercially reasonable efforts to ensure that citations in this document are complete and accurate, errors may occur. If you see any error or omission, please help us improve this document by sending information to contact_oss@megagon.ai.

This repository's [LICENSE](LICENSE) is BSD 3-Clause with an additional Attribution / NOTICE Requirement. Redistributions must retain the [NOTICE](NOTICE) or equivalent attribution; it is not an unmodified BSD-3-Clause grant. Each package archive includes its license and notice. Third-party components retain their own terms.

### Datasets

All datasets used within the product are listed below (including their copyright holders and the license information).

For Datasets having different portions released under different licenses, please refer to the included source link specified for each of the respective datasets for identifications of dataset files released under the identified licenses.

</br>

| ID  | Dataset Name | Modified | Copyright Holder | Upstream Link | License  |
|-----|----------------------------------|----------|------------------|-----------------------------------------------------------------------------------------------------------|--------------------|
| 1 | WikiTabGen (table #15 query and 11 column names, embedded in [`examples/scenario-demos/partitioned_wikitabgen_demo.py`](examples/scenario-demos/partitioned_wikitabgen_demo.py); the dataset is not distributed) | No | Not established | [link](https://github.com/analysis-bots/WikiTabGen) | Not established locally |

The test cassettes contain generated model responses, with a recording script and a `RECEIPT.json` spend record under `lib/tests/`; they are fixtures, not an external benchmark dataset. The local source does not establish the WikiTabGen license or redistribution rights; this reference grants none. Provider-generated output and data supplied by callers require their own rights and accuracy review.

### Open Source Software (OSS) Components

All OSS components used within the product are listed below (including their copyright holders and the license information).

For OSS components having different portions released under different licenses, please refer to the included Upstream link(s) specified for each of the respective OSS components for identifications of code files released under the identified licenses.

</br>

| ID  | OSS Component Name | Modified | Copyright Holder | Upstream Link | License  |
|-----|----------------------------------|----------|------------------|-----------------------------------------------------------------------------------------------------------|--------------------|
| 1 | Blue data | No | Megagon Labs, Inc. | [link](https://github.com/megagonlabs/blue-data) | BSD 3-Clause with Attribution / NOTICE Requirement |
| 2 | tenacity | No | Julien Danjou | [link](https://github.com/jd/tenacity) | Apache Software License |
| 3 | LiteLLM | No | Berri AI | [link](https://github.com/BerriAI/litellm) | MIT License |
| 4 | fastuuid | No | Omer Katz | [link](https://pypi.org/project/fastuuid/) | BSD License |
| 5 | diskcache | No | Grant Jenks | [link](https://github.com/grantjenks/python-diskcache) | Apache Software License |
| 6 | click | No | Pallets | [link](https://github.com/pallets/click) | BSD License |
| 7 | rich | No | Will McGugan | [link](https://github.com/Textualize/rich) | MIT License |
| 8 | MCP Python SDK | No | Anthropic, PBC | [link](https://github.com/modelcontextprotocol/python-sdk) | MIT License |
| 9 | langgraph (examples only) | No | LangChain, Inc. | [link](https://github.com/langchain-ai/langgraph) | MIT License |
| 10 | langchain (examples only) | No | LangChain, Inc. | [link](https://pypi.org/project/langchain/) | MIT License |
| 11 | langchain-openai (examples only) | No | LangChain, Inc. | [link](https://pypi.org/project/langchain-openai/) | MIT License |
| 12 | langchain-mcp-adapters (examples only) | No | LangChain, Inc. | [link](https://github.com/langchain-ai/langchain-mcp-adapters) | MIT License |

The first eight rows cover the direct runtime dependencies and the transitive fastuuid dependency. The last four are installed separately to run the framework examples, which list them in their install commands. The LiteLLM row covers the MIT-licensed core; separately licensed enterprise code has its own terms. Transitive dependencies, including pandas, NumPy and jsonpath-ng through Blue data, retain their own licenses and bundled notices. Development tools (`pytest`, `pytest-asyncio`, `pytest-cov`, `jsonschema`, Ruff, Pyright, build and Twine) are separate from the runtime inventory; consult their licenses when installing them.
