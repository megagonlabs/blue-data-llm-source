# Documentation

Use this index to find installation, usage, interface, and design documentation for `blue-data-llm-source`.

## Start here

| Doc | Read this if you want to... |
|---|---|
| [quickstart.md](quickstart.md) | Install the CLI and request records from a shell |
| [installation.md](installation.md) | Install any combination of the library, the CLI, and the MCP server, with all optional extras |
| [features.md](features.md) | See what the library can do, at a glance, with a short offline example per feature |
| [Supported models](../lib/README.md#supported-models) | Check which models the package knows, the reasoning efforts each accepts, and what each costs |
| [examples/](../examples/README.md) | Run something: scripts with real captured output, organized by backend, feature, framework integration, and scenario |
| [library README](../lib/README.md) | Read the library's settings, model table, and limits |
| [compatibility guide](../lib/COMPATIBILITY.md) | Port a caller from the earlier source or integrate with `DataSource` |
| [design/](../design/) | Read the architecture and interface decisions |
| [`CHANGELOG.md`](../CHANGELOG.md) | Upgrade from an earlier release. 0.2.2.1 protects diagnostics and caches; 0.2.2.0 adds the opt-in evidence-gated split and five experimental plan settings; 0.2.1.0 adds the GPT model table and reads the MCP server's settings from its environment; 0.2.0.0 renames the command line, renames every MCP tool, and closes the `properties` map |

## Upgrading from 0.2.2.0 (installed as 2.2.0)

The current packages are 1.0.0. Installed copies of earlier releases report their original numbers, so pip ranks an installed 0.1.1.0 to 0.2.2.1 (1.1.0 to 2.2.1) above 1.0.0 and an ordinary upgrade does not select the current release. The CLI and MCP require `blue-data-llm-source[llm]==1.0.0`; the CLI also pins `blue-data==1.0.0` for its protected storage hooks. See [installing exact built wheels](installation.md#installing-exact-built-wheels).

Version 0.2.2.1 (installed copies report 2.2.1) began masking credential-shaped text in CLI and MCP diagnostics and requiring private on-disk cache directories. See the [changelog](../CHANGELOG.md#0221--unreleased-as-221) for details. That dev release required `blue-data-llm-source[llm]>=2.2.1`.

## Upgrading from 0.2.1.0 (installed as 2.1.0)

At the defaults, 0.2.2.0 sends the same prompts and returns the same records as 0.2.1.0. The [changelog](../CHANGELOG.md#0220--released-as-220) lists what a caller can still observe:

- Result metadata always carries `filter_contradictions`.
- `ExecMeta` has two new fields ahead of `observed_keys`.
- The CLI and MCP server required `blue-data-llm-source[llm]>=2.2.0`.

`plan.split_policy="evidence"` is opt-in.

## Upgrading from 0.2.0.0 (installed as 2.0.0)

These 0.2.1.0 changes can alter what an existing caller gets. The [changelog](../CHANGELOG.md#0210--released-as-210) gives the setting that keeps each 0.2.0.0 behavior.

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

## Building on it

| Doc | Read this if you want to... |
|---|---|
| [architecture.md](architecture.md) | Understand how the pieces fit together: the component diagram, the three data flows, and where the money is spent |
| [extending.md](extending.md) | Supply your own model client, cache backend, price table, budget, or identity partition rules |
