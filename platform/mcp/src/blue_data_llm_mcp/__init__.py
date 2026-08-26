"""MCP server for the LLM data source.

Importing this package does not import ``mcp``; ``server`` does, and only when the
server itself is started.
"""

from __future__ import annotations

from blue_data_llm_mcp.tools import TOOLS, ToolError, ToolSpec, dispatch

__version__ = "1.1.1"

__all__ = ["TOOLS", "ToolError", "ToolSpec", "__version__", "dispatch"]
