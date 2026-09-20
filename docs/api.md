# REST API

**There is no REST API, and none is scheduled.** The packages contain no HTTP application, route handler, ASGI server, or web dependency.

The architecture is documented in [`design/03-high-level-architecture.md`](../design/03-high-level-architecture.md), [`design/04-detailed-architecture.md`](../design/04-detailed-architecture.md), and [`design/05-software-stack.md`](../design/05-software-stack.md). See also the user-facing [architecture summary](architecture.md#what-this-architecture-does-not-contain).

## The three interfaces that do exist

| Interface | What it is | Documented in |
|---|---|---|
| Python library | `LlmSource`, imported directly. Every other interface is a thin adapter over it. | [guide.md](guide.md), then [api-reference.md](api-reference.md) for exact signatures |
| `blue-llmdb` | A command line: twelve commands over four components, with profiles and four output formats. | [cli.md](cli.md) |
| `blue-data-llm-mcp` | An MCP server over stdio, five tools, for an agent rather than a person. | [mcp.md](mcp.md) |

Use Python for application code, the command line for interactive or scripted shell use, and MCP for model-driven clients.

## If you need HTTP

Wrap the library. `LlmSource.afetch` is async and returns a `FetchResult` whose `records` and `meta` serialize to JSON directly, so a handler in whatever framework you already run is a small amount of code. [extending.md](extending.md) covers the seams, and [architecture.md](architecture.md) covers what the adapters do and do not hold, which is what a fourth adapter would have to match: argument translation and output shaping, with validation, alignment and budgets left to the library.

Configure `limits.*` on the source you build, instead of taking ceilings from the request. A ceiling supplied by a caller is a ceiling that caller can raise.
