# REST API

**There is no REST API.** No HTTP application, route handler, ASGI server, or web dependency exists in any package in this repository, and none is scheduled.

The decision is recorded in [`design/03-high-level-architecture.md`](../design/03-high-level-architecture.md), the component status table in [`design/04-detailed-architecture.md`](../design/04-detailed-architecture.md), and the stack choices in [`design/05-software-stack.md`](../design/05-software-stack.md). [architecture.md](architecture.md#what-this-architecture-deliberately-does-not-contain) is the user-facing version of the same statement.

## The three interfaces that do exist

| Interface | What it is | Documented in |
|---|---|---|
| Python library | `LlmSource`, imported directly. Every other interface is a thin adapter over it. | [guide.md](guide.md), then [api-reference.md](api-reference.md) for exact signatures |
| `blue-llmdb` | A command line: twelve commands over four components, with profiles and four output formats. | [cli.md](cli.md) |
| `blue-data-llm-mcp` | An MCP server over stdio, five tools, for an agent rather than a person. | [mcp.md](mcp.md) |

Pick by caller. Python when you are writing code against the library, the command line when a person or a shell script is driving, MCP when a model-driven client is.

## If you need HTTP

Wrap the library. `LlmSource.afetch` is async and returns a `FetchResult` whose `records` and `meta` serialize to JSON directly, so a handler in whatever framework you already run is a small amount of code. [extending.md](extending.md) covers the seams, and [architecture.md](architecture.md) covers what the adapters do and do not hold, which is what a fourth adapter would have to match: argument translation and output shaping, with validation, alignment and budgets left to the library.

Configure `limits.*` on the source you build, instead of taking ceilings from the request. A ceiling supplied by a caller is a ceiling that caller can raise.
