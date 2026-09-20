# Documentation

Use this index to find installation, usage, interface, and design documentation for `blue-data-llm-source`.

## Start here

| Doc | Read this if you want to... |
|---|---|
| [quickstart.md](quickstart.md) | Get records out of a language model from a shell in the next five minutes |
| [installation.md](installation.md) | Install any combination of the library, the CLI, and the MCP server, with all optional extras |
| [features.md](features.md) | See what the library can do, at a glance, with a short offline example per feature |
| [examples/](../examples/README.md) | Run something: scripts with real captured output, organized by backend, feature, framework integration, and scenario |
| [`CHANGELOG.md`](../CHANGELOG.md) | Upgrade from an earlier release. 2.0.0 renames the command line, renames every MCP tool, and closes the `properties` map |

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

These pages describe current behavior. Treat any discrepancy between the documentation and the implementation as a documentation defect.

The package READMEs own their respective public interfaces: [`lib/README.md`](../lib/README.md), [`lib/COMPATIBILITY.md`](../lib/COMPATIBILITY.md), [`platform/llm-cli/README.md`](../platform/llm-cli/README.md), and [`platform/llm-mcp/README.md`](../platform/llm-mcp/README.md). [cli.md](cli.md) and [mcp.md](mcp.md) mirror the two platform READMEs; the remaining pages summarize the current implementation for users. Contributor-oriented design rationale is under [`design/`](../design/).
