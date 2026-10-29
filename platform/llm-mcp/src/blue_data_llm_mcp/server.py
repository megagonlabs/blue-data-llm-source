"""The stdio MCP server and the package's only ``mcp`` import.

The low-level server publishes the schemas defined in ``tools.py``. FastMCP would
derive different schemas from handler signatures. The SDK is absent during static
checking, so its imports and untyped exports have targeted ``pyright: ignore`` rules.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
from typing import Any, NoReturn

import mcp.types as types  # pyright: ignore[reportMissingImports]
from mcp.server.lowlevel import (  # pyright: ignore[reportMissingImports]
    Server,  # pyright: ignore[reportUnknownVariableType]
)
from mcp.server.stdio import (  # pyright: ignore[reportMissingImports]
    stdio_server,  # pyright: ignore[reportUnknownVariableType]
)

from blue_data_llm_mcp import __version__, tools

SERVER_NAME = "blue-data-llm"


class _SdkValidationFilter(logging.Filter):
    """Replace SDK validation dumps before any stderr handler formats them."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.msg
        if (
            record.name == "root"
            and isinstance(message, str)
            and message.startswith(
                (
                    "Failed to validate request:",
                    "Message that failed validation:",
                    "Failed to validate notification:",
                )
            )
        ):
            record.msg = "MCP request validation failed."
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, f"{self.prog}: error: unsupported arguments; see --help\n")


# Sent in the handshake: data origin, enforceable ceilings, and free pricing tools.
SERVER_INSTRUCTIONS = (
    "Structured records from a language model's own knowledge, rather than from a store. "
    "llmdb_query asks the model and returns records with a meta object saying what the run "
    "cost and why rows may be missing. The operator may set ceilings for every call; a "
    "call's own limits.max_cost, limits.max_seconds or limits.max_calls can only lower them, "
    "and an axis has no ceiling when neither sets one. "
    "llmdb_estimate bounds what a query would cost, and llmdb_list_models and "
    "llmdb_inspect_model say which models can be bounded at all. Those three contact no "
    "provider and cost nothing."
)

# Pass the package version; otherwise the SDK reports its own version to clients.
server = Server(  # pyright: ignore[reportUnknownVariableType]
    SERVER_NAME, version=__version__, instructions=SERVER_INSTRUCTIONS
)


@server.list_tools()  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def list_tools() -> list[types.Tool]:  # pyright: ignore[reportUnknownParameterType,reportUnknownMemberType]
    return [  # pyright: ignore[reportUnknownVariableType]
        types.Tool(name=spec.name, description=spec.description, inputSchema=spec.input_schema)  # pyright: ignore[reportUnknownMemberType]
        for spec in tools.TOOLS
    ]


# Disable SDK prevalidation so unsupported settings receive specific guidance.
# `tools._check_arguments` validates the advertised schema inside the handler.
@server.call_tool(validate_input=False)  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:  # pyright: ignore[reportUnknownParameterType,reportUnknownMemberType]
    try:
        payload = await tools.adispatch(name, arguments)
        content = types.TextContent(type="text", text=json.dumps(payload, default=str))  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
    except tools.ToolError as error:
        public_error = f"{error.kind}: {error.message}"
    except Exception:
        public_error = "InternalError: Tool failed."
    else:
        return [content]  # pyright: ignore[reportUnknownVariableType]
    raise ValueError(public_error)


async def serve() -> None:
    """Run the stdio server, discarding unsolicited provider output for its lifetime.

    The transport holds the protocol output handle. A single process-wide redirect keeps
    concurrent tool calls from changing it independently.
    """
    async with stdio_server() as (read_stream, write_stream):  # pyright: ignore[reportUnknownVariableType]
        sdk_filter = _SdkValidationFilter()
        root_logger = logging.getLogger()
        root_logger.addFilter(sdk_filter)
        try:
            with open(os.devnull, "w", encoding="utf-8") as sink:
                with contextlib.redirect_stdout(sink):
                    await server.run(  # pyright: ignore[reportUnknownMemberType]
                        read_stream, write_stream, server.create_initialization_options()
                    )
        finally:
            root_logger.removeFilter(sdk_filter)


def main() -> None:
    """Console entry point."""
    parser = _ArgumentParser(description="Serve blue-data-llm MCP over stdio.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args()
    asyncio.run(serve())


if __name__ == "__main__":
    main()
