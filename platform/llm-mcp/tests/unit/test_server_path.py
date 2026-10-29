"""Exercise the shipped async handler without starting the MCP server.

The handler body runs from its source file with a local TextContent stand-in.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import builtins
import contextlib
import importlib
import json
import logging
import os
import subprocess
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from blue_data_llm_source import ConfigError, TransportError

from blue_data_llm_mcp import __version__, tools

PACKAGE = Path(tools.__file__).parent


@pytest.mark.parametrize(
    "arguments",
    [["--help"], ["-h"], ["--version"], ["--bogus"], ["--api-key", "synthetic-secret"]],
)
def test_console_arguments_finish_without_starting_transport(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = PACKAGE / "server.py"
    module = ast.parse(path.read_text(encoding="utf-8"))
    entry = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    parser = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "_ArgumentParser"
    )
    started: list[object] = []

    def capture_run(task: object) -> None:
        started.append(task)

    def stub_serve() -> str:
        started.append("serve")
        return "serve"

    namespace: dict[str, Any] = {
        "argparse": argparse,
        "asyncio": SimpleNamespace(run=capture_run),
        "serve": stub_serve,
        "__version__": __version__,
    }
    exec(compile(ast.Module(body=[parser, entry], type_ignores=[]), str(path), "exec"), namespace)
    monkeypatch.setattr(sys, "argv", ["blue-data-llm-mcp", *arguments])

    with pytest.raises(SystemExit) as stopped:
        namespace["main"]()

    bad = arguments[0] in {"--bogus", "--api-key"}
    assert stopped.value.code == (2 if bad else 0)
    assert not started
    output = capsys.readouterr()
    if bad:
        assert "unsupported arguments; see --help" in output.err
        assert "synthetic-secret" not in output.err
    else:
        assert output.out
        if arguments == ["--version"]:
            assert output.out.strip() == __version__


def test_malformed_frame_does_not_log_caller_arguments() -> None:
    pytest.importorskip("mcp")
    marker = "SYNTHETIC_MCP_MARKER_123"
    frames: list[dict[str, object]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "llmdb_query", "arguments": [{"name": marker}]},
        },
    ]
    process = subprocess.run(
        [sys.executable, "-m", "blue_data_llm_mcp.server"],
        input="".join(json.dumps(frame) + "\n" for frame in frames),
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )
    assert process.returncode == 0
    assert marker not in process.stderr
    assert marker not in process.stdout
    assert "MCP request validation failed." in process.stderr
    response = json.loads(process.stdout.splitlines()[-1])
    assert response["error"]["code"] == -32602


def test_sdk_filter_scrubs_validation_record_and_leaves_other_logs_alone() -> None:
    path = PACKAGE / "server.py"
    module = ast.parse(path.read_text(encoding="utf-8"))
    filter_class = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "_SdkValidationFilter"
    )
    namespace: dict[str, Any] = {"logging": logging}
    exec(compile(ast.Module(body=[filter_class], type_ignores=[]), str(path), "exec"), namespace)
    sdk_filter = namespace["_SdkValidationFilter"]()
    leaked = logging.LogRecord(
        "root", logging.WARNING, __file__, 1, "Failed to validate request: %s", ("marker",), None
    )
    leaked.stack_info = "marker stack"
    assert sdk_filter.filter(leaked)
    assert leaked.msg == "MCP request validation failed."
    assert leaked.args == ()
    assert leaked.stack_info is None
    ordinary = logging.LogRecord("root", logging.WARNING, __file__, 1, "ordinary", (), None)
    assert sdk_filter.filter(ordinary)
    assert ordinary.msg == "ordinary"


def test_sdk_filter_is_removed_if_log_sink_cannot_open(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("mcp")
    server_module = importlib.import_module("blue_data_llm_mcp.server")
    root = logging.getLogger()
    before = tuple(root.filters)

    @contextlib.asynccontextmanager
    async def local_stdio() -> AsyncGenerator[tuple[None, None]]:
        yield None, None

    original_open = cast("Callable[..., Any]", builtins.open)

    def refused(path: Any, *args: Any, **kwargs: Any) -> Any:
        if path == os.devnull:
            raise OSError("synthetic sink refusal")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(server_module, "stdio_server", local_stdio)
    monkeypatch.setattr(builtins, "open", refused)
    with pytest.raises(OSError, match="synthetic sink refusal"):
        asyncio.run(server_module.serve())
    assert tuple(root.filters) == before


class _Source:
    """Stands in for LlmSource with the async path the handler must use."""

    protocol = "llm"
    variant = "openai"

    def __init__(self) -> None:
        self.async_calls = 0

    async def afetch(self, query: str, **kwargs: Any) -> Any:
        self.async_calls += 1
        from blue_data_llm_source.types import ExecMeta, FetchResult

        return FetchResult(records=[{"name": "Ada"}], meta=ExecMeta(model="m", calls=1))

    def fetch(self, query: str, **kwargs: Any) -> Any:
        raise ConfigError(
            "fetch() was called from inside a running event loop. Use afetch() instead."
        )


async def _call_tool(name: str, arguments: dict[str, Any], source: Any) -> list[str]:
    """Run the shipped handler with a source and a local TextContent stand-in."""
    path = PACKAGE / "server.py"
    module = ast.parse(path.read_text(encoding="utf-8"))
    handler = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "call_tool"
    )
    handler.decorator_list = []

    async def adispatch(tool_name: str, args: dict[str, Any]) -> Any:
        return await tools.adispatch(tool_name, args, source)

    def text_content(**fields: Any) -> SimpleNamespace:
        return SimpleNamespace(**fields)

    namespace: dict[str, Any] = {
        "tools": SimpleNamespace(adispatch=adispatch, ToolError=tools.ToolError),
        "types": SimpleNamespace(TextContent=text_content),
        "json": json,
        "Any": Any,
    }
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(path), "exec"), namespace)
    call_tool = cast(
        Callable[[str, dict[str, Any]], Awaitable[list[SimpleNamespace]]], namespace["call_tool"]
    )
    blocks = await call_tool(name, arguments)
    return [block.text for block in blocks]


def test_the_redirect_wraps_the_run_and_not_each_call() -> None:
    """Check that stdout is redirected around ``server.run``, not per call."""
    source = (PACKAGE / "server.py").read_text()
    assert "with contextlib.redirect_stdout(sink):\n                    await server.run(" in source
    assert "open(os.devnull" in source
    assert "root_logger.removeFilter(sdk_filter)" in source
    handler = source[source.index("async def call_tool") : source.index("async def serve")]
    assert "redirect_stdout" not in handler, "the redirect is back inside the per-call path"
    assert "InternalError: Tool failed." in handler


def _make_pair() -> tuple[Any, Any, Any]:
    """Two sources and the events that pin the interleaving.

    Events force A and B to overlap, then make A leave before B prints.
    """
    b_entered = asyncio.Event()
    a_left = asyncio.Event()

    class _Quiet(_Source):
        async def afetch(self, query: str, **kwargs: Any) -> Any:
            await b_entered.wait()  # hold the call open until the other one is inside
            return await super().afetch(query, **kwargs)

    class _LatePrinter(_Source):
        async def afetch(self, query: str, **kwargs: Any) -> Any:
            b_entered.set()
            await a_left.wait()  # print only after the first call has finished
            print("provider banner that must not be framed")
            return await super().afetch(query, **kwargs)

    return _Quiet(), _LatePrinter(), a_left


def test_concurrent_calls_cannot_leak_provider_output_or_strip_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Concurrent calls cannot leak provider output or change stdout after the run.

    ``redirect_stdout`` changes process-global state. The server holds one redirect for
    its lifetime so overlapping calls do not restore different stdout objects.
    """
    quiet, late, a_left = _make_pair()
    original = sys.stdout

    async def exercise() -> None:
        # Mirrors serve(): one redirect for the lifetime, every call inside it.
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with contextlib.redirect_stdout(sink):
                # A leaves before B prints, exposing any per-call redirect race.
                a = asyncio.create_task(_call_tool("llmdb_query", {"query": "a"}, quiet))
                b = asyncio.create_task(_call_tool("llmdb_query", {"query": "b"}, late))
                await a
                a_left.set()
                await b

    asyncio.run(exercise())

    captured = capsys.readouterr()
    assert "provider banner" not in captured.out, "provider output reached the JSON-RPC stream"
    assert "provider banner" not in captured.err, "provider output reached stderr"
    assert sys.stdout is original, "stdout was left pointing somewhere else"


def test_an_async_handler_can_fetch_without_hitting_the_sync_refusal() -> None:
    source = _Source()
    out = asyncio.run(_call_tool("llmdb_query", {"query": "who?"}, source))
    payload = json.loads(out[0])
    assert payload["records"] == [{"name": "Ada"}]
    assert source.async_calls == 1


SENTINEL = "sk-canary-NOTAKEY"
"""A key literal in the shape a provider stamps one with. A sentinel, not a credential."""


@pytest.mark.parametrize("format_value", [str, repr, json.dumps])
def test_an_unclassified_failure_has_no_exception_text_or_cause(format_value: Any) -> None:
    """The SDK puts the raised ValueError text in the tool result."""

    class _Broken(_Source):
        async def afetch(self, query: str, **kwargs: Any) -> Any:
            raise RuntimeError(f"request failed: {format_value(SENTINEL)}")

    with pytest.raises(ValueError) as raised:
        asyncio.run(_call_tool("llmdb_query", {"query": "who?"}, _Broken()))

    assert SENTINEL not in str(raised.value)
    assert str(raised.value) == "InternalError: Tool failed."
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_the_sync_path_is_never_taken_from_the_async_handler() -> None:
    """The async handler must call ``afetch`` and never ``fetch``."""
    source = _Source()
    asyncio.run(_call_tool("llmdb_query", {"query": "who?"}, source))
    assert source.async_calls == 1


def test_info_works_through_the_async_handler() -> None:
    out = asyncio.run(_call_tool("llmdb_inspect_metadata", {}, _Source()))
    payload = json.loads(out[0])
    assert payload["protocol"] == "llm"
    assert payload["variant"] == "openai"


def test_an_unknown_tool_surfaces_as_a_value_error() -> None:
    with pytest.raises(ValueError, match="UnknownTool"):
        asyncio.run(_call_tool("nope", {}, _Source()))


def test_a_known_library_refusal_keeps_safe_query_guidance() -> None:
    from blue_data_llm_source.types import QueryError

    class Failing(_Source):
        async def afetch(self, query: str, **kwargs: Any) -> Any:
            raise QueryError("query must not be empty or whitespace")

    with pytest.raises(ValueError, match="query must not be empty or whitespace"):
        asyncio.run(_call_tool("llmdb_query", {"query": " "}, Failing()))


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        (RuntimeError, "InternalError: Tool failed."),
        (TransportError, "TransportError: Provider request failed."),
    ],
)
def test_valid_operator_setting_echo_is_not_in_server_error(
    error_type: type[Exception],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = "27183"
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", value)
    assert tools.deployment_properties()["llm.max_tokens"] == int(value)

    class _Broken(_Source):
        async def afetch(self, query: str, **kwargs: Any) -> Any:
            raise error_type(f"provider echoed max_tokens={value}")

    with pytest.raises(ValueError) as raised:
        asyncio.run(_call_tool("llmdb_query", {"query": "who?"}, _Broken()))

    assert str(raised.value) == expected
    assert value not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_the_shipped_handler_awaits_the_async_dispatcher() -> None:
    """Read the shipped file as text.

    Importing it needs the MCP package, which is absent here. Reading the source keeps
    the assertion tied to what actually ships rather than to this test's reconstruction
    of it.
    """
    from pathlib import Path

    server_py = Path(__file__).resolve().parents[2] / "src" / "blue_data_llm_mcp" / "server.py"
    body = server_py.read_text(encoding="utf-8")
    assert "await tools.adispatch(" in body
    assert "tools.dispatch(" not in body.replace("tools.adispatch(", "")


def test_the_server_module_imports_when_the_sdk_is_installed() -> None:
    """Import the server against an installed protocol SDK.

    The tools module has no SDK dependency, so this test checks the server import
    separately. Skip it when the SDK is absent.
    """
    pytest.importorskip("mcp")

    from blue_data_llm_mcp import server

    assert server.server is not None
    assert callable(server.main)


def test_the_handshake_reports_this_package_and_not_the_sdk() -> None:
    """The handshake reports the package version rather than the SDK version."""
    pytest.importorskip("mcp")

    from blue_data_llm_mcp import __version__
    from blue_data_llm_mcp import server as shipped

    options = shipped.server.create_initialization_options()

    assert options.server_name == "blue-data-llm"
    assert options.server_version == __version__
    assert options.instructions, "a client has nothing to read about what this server is for"


def test_the_sdk_is_bounded_below_the_next_major() -> None:
    """A floor on a dependency whose major version removed the API is not a constraint."""
    pyproject = (PACKAGE.parents[1] / "pyproject.toml").read_text(encoding="utf-8")

    assert '"mcp>=1.28.1,<2"' in pyproject, "the SDK must stay below 2.0, which removed this API"


def test_a_dotted_ceiling_name_survives_the_protocol_layer() -> None:
    """The ceilings are advertised under names containing a dot, which nothing else here is.

    A property name is a wire-level detail once it leaves this package: it goes into the
    advertised `inputSchema` and comes back in a client's arguments. The direct-dispatch
    tests never see either. This drives the shipped server through the installed SDK, and it
    is `llmdb_estimate` because that one answers with no credential and contacts nothing.

    Skip the SDK path when the protocol SDK is absent.
    """
    pytest.importorskip("mcp")

    from mcp import types

    from blue_data_llm_mcp import server as shipped

    async def listed() -> Any:
        request = types.ListToolsRequest(method="tools/list")
        return await shipped.server.request_handlers[types.ListToolsRequest](request)

    async def estimated() -> Any:
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="llmdb_estimate", arguments={"query": "who?", "limits.max_calls": 1}
            ),
        )
        return await shipped.server.request_handlers[types.CallToolRequest](request)

    advertised = {tool.name: tool.inputSchema for tool in asyncio.run(listed()).root.tools}
    assert set(advertised) == {
        "llmdb_query",
        "llmdb_estimate",
        "llmdb_list_models",
        "llmdb_inspect_model",
        "llmdb_inspect_metadata",
    }
    for name in ("llmdb_query", "llmdb_estimate"):
        assert "limits.max_cost" in advertised[name]["properties"]

    result = asyncio.run(estimated()).root
    assert not result.isError, result.content[0].text
    payload = json.loads(result.content[0].text)
    # Narrowed to one attempt by the ceiling that arrived with the call, which is the whole
    # round trip: advertised name out, same name back, read by the library.
    assert payload["max_attempts"] == 1


def test_the_real_sdk_handler_gives_the_deferred_answer_not_a_schema_error() -> None:
    """Through the installed SDK request handler, not `dispatch` alone.

    The SDK can reject an extra property before the handler supplies guidance for an
    unsupported setting. Exercise the installed SDK path when available.
    """
    pytest.importorskip("mcp")

    from mcp import types

    from blue_data_llm_mcp import server as shipped

    async def exercise() -> Any:
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="llmdb_query", arguments={"query": "x", "llm.rate_limit": 10}
            ),
        )
        return await shipped.server.request_handlers[types.CallToolRequest](request)

    result = asyncio.run(exercise()).root
    message = result.content[0].text if result.content else ""

    assert result.isError
    assert "llm.rate_limit" in message
    assert "v2.0" not in message
    assert "max_parallel" in message


def test_the_real_sdk_handler_hides_provider_failure_text(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("mcp")

    from mcp import types

    from blue_data_llm_mcp import server as shipped

    class Failing:
        async def afetch(self, query: str, **kwargs: Any) -> Any:
            raise TransportError("provider rejected private setting 27183")

    monkeypatch.setattr(tools, "build_source", Failing)

    async def exercise() -> Any:
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name="llmdb_query", arguments={"query": "who?"}),
        )
        return await shipped.server.request_handlers[types.CallToolRequest](request)

    result = asyncio.run(exercise()).root
    assert result.isError
    assert "TransportError: Provider request failed." in result.content[0].text
    assert "27183" not in result.content[0].text


def _sdk_tool_listing() -> Any:
    """The shipped server's own tools/list handler, through the installed SDK."""
    from mcp import types

    from blue_data_llm_mcp import server as shipped

    async def listed() -> Any:
        request = types.ListToolsRequest(method="tools/list")
        return await shipped.server.request_handlers[types.ListToolsRequest](request)

    return asyncio.run(listed()).root.tools


def test_what_the_server_advertises_is_exactly_what_the_specs_declare() -> None:
    """The snapshot check for a protocol surface.

    Every other schema test reads `tools.TOOLS`. This reads what a client is actually sent,
    so a spec that never reaches the wire, or a name or schema the transport reshapes on the
    way out, fails here rather than in somebody's client.
    """
    pytest.importorskip("mcp")

    advertised = {
        tool.name: {"description": tool.description, "schema": tool.inputSchema}
        for tool in _sdk_tool_listing()
    }
    declared = {
        spec.name: {"description": spec.description, "schema": spec.input_schema}
        for spec in tools.TOOLS
    }

    assert advertised == declared
    assert list(advertised) == [spec.name for spec in tools.TOOLS]


def test_the_advertised_closed_sets_reach_the_wire() -> None:
    """That the enums are *there*, not merely that the schema has not changed.

    A snapshot alone would also accept bare strings for these parameters.
    """
    pytest.importorskip("mcp")

    schemas = {tool.name: tool.inputSchema for tool in _sdk_tool_listing()}
    properties = schemas["llmdb_query"]["properties"]

    assert properties["plan.mode"]["enum"] == ["flat", "partitioned", "row_by_row"]
    assert properties["plan.schema_mode"]["enum"] == ["preserve", "enrich"]
    assert properties["plan.planning_order"]["enum"] == [
        "planning.cardinality_first",
        "planning.schema_first",
        "planning.locked_sequential",
        "planning.locked_parallel",
        "planning.combined",
    ]
    assert schemas["llmdb_inspect_model"]["required"] == ["model"]

    # The open set, asserted open at the wire for the same reason: an enum here would
    # refuse a declared type the library accepts and reports in meta.unchecked_types.
    attribute = properties["attrs"]["items"]["oneOf"][1]["properties"]["type"]
    assert "enum" not in attribute


def test_a_refused_argument_is_an_mcp_error_and_a_priceless_model_is_not() -> None:
    """The error rule at the protocol layer, both halves of it.

    A call that produced no result comes back `isError` with `Kind: message`. A call that
    ran and has an answer -- even the answer "this model has no published price" -- comes
    back as a success with the fact in the payload.
    """
    pytest.importorskip("mcp")

    from mcp import types

    from blue_data_llm_mcp import server as shipped

    def call(name: str, arguments: dict[str, Any]) -> Any:
        async def exercise() -> Any:
            request = types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(name=name, arguments=arguments),
            )
            return await shipped.server.request_handlers[types.CallToolRequest](request)

        return asyncio.run(exercise()).root

    refused = call("llmdb_query", {"query": "x", "plan.mode": "recursive"})
    assert refused.isError
    assert refused.content[0].text.startswith("InvalidArguments: ")

    missing = call("llmdb_inspect_model", {})
    assert missing.isError
    assert missing.content[0].text.startswith("InvalidArguments: ")

    answered = call("llmdb_inspect_model", {"model": "nobody-prices-this"})
    assert not answered.isError, answered.content[0].text
    assert json.loads(answered.content[0].text)["priceable"] is False

    listed = call("llmdb_list_models", {})
    assert not listed.isError
    assert "gpt-4o" in json.loads(listed.content[0].text)["models"]
