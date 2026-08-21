"""The stdio MCP server. This module holds the only ``mcp`` import in the package.

The low-level server is used rather than FastMCP because the tool schemas are written by
hand in ``tools.py``. FastMCP derives a schema from the handler signature, which would
put the tool contract in a Python signature instead of the JSON the client actually sees.

``mcp`` is a server-only dependency, so the type checker runs here with the SDK absent and
nothing it exports carries a type. That is what every ``pyright: ignore`` below is for.
Each one names its rule, so installing the SDK restores real checking.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from typing import Any

import mcp.types as types  # pyright: ignore[reportMissingImports]
from mcp.server.lowlevel import (  # pyright: ignore[reportMissingImports]
    Server,  # pyright: ignore[reportUnknownVariableType]
)
from mcp.server.stdio import (  # pyright: ignore[reportMissingImports]
    stdio_server,  # pyright: ignore[reportUnknownVariableType]
)

from blue_data_llm_mcp import tools

SERVER_NAME = "blue-data-llm"

server = Server(SERVER_NAME)  # pyright: ignore[reportUnknownVariableType]


@server.list_tools()  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def list_tools() -> list[types.Tool]:  # pyright: ignore[reportUnknownParameterType,reportUnknownMemberType]
    return [  # pyright: ignore[reportUnknownVariableType]
        types.Tool(name=spec.name, description=spec.description, inputSchema=spec.input_schema)  # pyright: ignore[reportUnknownMemberType]
        for spec in tools.TOOLS
    ]


# validate_input=False because the SDK's prevalidator runs before this handler and answered
# a deferred key with a generic schema error, losing the message that says what to use
# instead. `tools._check_arguments` performs the advertised validation itself, deferred keys
# first, and the schema tests assert the schemas use no construct it does not cover.
@server.call_tool(validate_input=False)  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:  # pyright: ignore[reportUnknownParameterType,reportUnknownMemberType]
    try:
        payload = await tools.adispatch(name, arguments)
    except tools.ToolError as error:
        # The low-level server turns a raised exception into a tool result marked
        # isError carrying str(exc), which is how the library's message reaches the
        # caller unchanged.
        raise ValueError(f"{error.kind}: {error.message}") from error
    return [types.TextContent(type="text", text=json.dumps(payload, default=str))]  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]


async def serve() -> None:
    """Run the server over stdio until the client disconnects.

    Standard output is the JSON-RPC frame here, and the provider stack prints to it — a
    cost-map warning, a version banner. Anything written there is not a message, and a
    client is entitled to fail on it rather than resynchronise around it.

    So interpreter-level stdout points at stderr for the whole run, entered *after* the
    transport has taken its own handle to the real one. Wrapping each tool call instead was
    worse than not wrapping it: ``redirect_stdout`` assigns the process-global ``sys.stdout``
    and this server runs requests concurrently, so two overlapping calls exiting out of
    order put the later one's output on the wire anyway and left ``sys.stdout`` pointing at
    stderr afterwards. One redirect over the whole lifetime cannot nest, so it cannot race.
    """
    async with stdio_server() as (read_stream, write_stream):  # pyright: ignore[reportUnknownVariableType]
        with contextlib.redirect_stdout(sys.stderr):
            await server.run(read_stream, write_stream, server.create_initialization_options())  # pyright: ignore[reportUnknownMemberType]


def main() -> None:
    """Console entry point."""
    asyncio.run(serve())


if __name__ == "__main__":
    main()
