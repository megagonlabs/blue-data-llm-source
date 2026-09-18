# Documentation

Landing page for all documentation in `blue-data-llm-source`. What to read depends on what you are doing.

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
| [api.md](api.md) | REST API — there is none, and this points at the three interfaces that do exist |

## Building on it

| Doc | Read this if you want to... |
|---|---|
| [architecture.md](architecture.md) | Understand how the pieces fit together: the component diagram, the three data flows, and where the money is spent |
| [extending.md](extending.md) | Supply your own model client, cache backend, price table, budget, or identity partition rules |

## Something's out of date

These pages track the current behavior of the code, not the plan for it. If you find one that has drifted, that is a bug in the docs.

The source of truth is always the code itself, then [`lib/README.md`](../lib/README.md), [`lib/COMPATIBILITY.md`](../lib/COMPATIBILITY.md), [`platform/llm-cli/README.md`](../platform/llm-cli/README.md), and [`platform/llm-mcp/README.md`](../platform/llm-mcp/README.md). [cli.md](cli.md) and [mcp.md](mcp.md) are synced copies of the last two and say so in their own text; [api-reference.md](api-reference.md) and [architecture.md](architecture.md) name a design document as theirs, in their own text. Every other page here is written from the code and is its own source. Design rationale lives under [`design/`](../design/), written for contributors rather than users.
