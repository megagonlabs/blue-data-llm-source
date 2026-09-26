# Documentation

Use this index to find installation, usage, interface, and design documentation for `blue-data-llm-source`.

## Start here

| Doc | Read this if you want to... |
|---|---|
| [quickstart.md](quickstart.md) | Get records out of a language model from a shell in the next five minutes |
| [installation.md](installation.md) | Install any combination of the library, the CLI, and the MCP server, with all optional extras |
| [features.md](features.md) | See what the library can do, at a glance, with a short offline example per feature |
| [Supported models](../lib/README.md#supported-models) | Check which models the package knows, the reasoning efforts each accepts, and what each costs |
| [examples/](../examples/README.md) | Run something: scripts with real captured output, organized by backend, feature, framework integration, and scenario |
| [`CHANGELOG.md`](../CHANGELOG.md) | Upgrade from an earlier release. 2.2.0 adds the opt-in evidence-gated split and five experimental plan settings; 2.1.0 adds the GPT model table and reads the MCP server's settings from its environment; 2.0.0 renames the command line, renames every MCP tool, and closes the `properties` map |

## Upgrading from 2.1.0

At the defaults, 2.2.0 sends the same prompts and returns the same records as 2.1.0. The [changelog](../CHANGELOG.md#220--candidate) lists what a caller can still observe:

- Result metadata always carries `filter_contradictions`.
- `ExecMeta` has two new fields ahead of `observed_keys`.
- The CLI and MCP server require `blue-data-llm-source[llm]>=2.2.0`.

`plan.split_policy="evidence"` is opt-in. It has been measured with GPT-4o only.

## Upgrading from 2.0.0

These 2.1.0 changes can alter what an existing caller gets. The [changelog](../CHANGELOG.md#210--released) gives the setting that keeps each 2.0.0 behaviour.

- The default model is `gpt-6-luna`, not `gpt-4o`.
- `plan.max_partitions` and `plan.max_leaves` default to 20 and 100, not 4 and 24.
- `LlmSource.properties` no longer lists the package defaults.
- CLI profile files readable or writable by group or others are refused until you `chmod 600` them.
- The `claude-*` price rows are removed.
- The MCP server reads its model, request settings and operator ceilings from its environment.

## Using it

| Doc | Read this if you want to... |
|---|---|
| [guide.md](guide.md) | Work through the Python library task by task: asking, shaping the answer, bounding the cost, splitting a large request, deciding whether to trust the result |
| [api-reference.md](api-reference.md) | Look up an exact method signature, parameter, or return type |
| [cli.md](cli.md) | Use the `blue-llmdb` command (every command, profiles and configuration, the four output formats, and the exit codes) |
| [mcp.md](mcp.md) | Connect Claude or another MCP client to this source. Every tool, with the client config and what a per-call ceiling covers |
| [api.md](api.md) | Understand why there is no REST API and choose among the three available interfaces |

## Building on it

| Doc | Read this if you want to... |
|---|---|
| [architecture.md](architecture.md) | Understand how the pieces fit together: the component diagram, the three data flows, and where the money is spent |
| [extending.md](extending.md) | Supply your own model client, cache backend, price table, budget, or identity partition rules |

## Document maintenance

These pages describe current behavior. Any discrepancy between the documentation and the implementation is a documentation defect.

The package READMEs define their respective public interfaces: [`lib/README.md`](../lib/README.md), [`lib/COMPATIBILITY.md`](../lib/COMPATIBILITY.md), [`platform/llm-cli/README.md`](../platform/llm-cli/README.md), and [`platform/llm-mcp/README.md`](../platform/llm-mcp/README.md). [cli.md](cli.md) and [mcp.md](mcp.md) mirror the platform READMEs; the remaining pages summarize the current implementation for users. Contributor-oriented design rationale is under [`design/`](../design/).
