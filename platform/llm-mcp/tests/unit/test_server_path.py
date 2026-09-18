"""The server's own call path, exercised the way the SDK exercises it.

The tool-translation tests call ``tools`` directly, which is why they stayed green while
the shipped server was broken: its ``call_tool`` handler is async and was dispatching
synchronously, so the library refused every fetch with "called from inside a running
event loop". These tests drive the same coroutine the SDK would await.

The MCP package is not installed here, so the handler is reconstructed from the module's
own source rather than mocked. None of the server's own behaviour is stubbed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from blue_data_llm_source import ConfigError, redact

from blue_data_llm_mcp import tools

PACKAGE = Path(tools.__file__).parent


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
    """The body of the shipped call_tool handler, minus the SDK decorators."""
    try:
        payload = await tools.adispatch(name, arguments, source)
    except tools.ToolError as error:
        raise ValueError(f"{error.kind}: {error.message}") from error
    except Exception as error:
        raise ValueError(redact(f"{type(error).__name__}: {error}")) from error
    return [json.dumps(payload, default=str)]


def test_the_redirect_wraps_the_run_and_not_each_call() -> None:
    """This file reproduces the handler rather than importing it, so it can drift.

    The redirect belongs around the whole ``server.run`` and nowhere else. Per call it was
    worse than absent, for the reason the next test measures.
    """
    source = (PACKAGE / "server.py").read_text()
    assert "with contextlib.redirect_stdout(sys.stderr):\n            await server.run(" in source
    handler = source[source.index("async def call_tool") : source.index("async def serve")]
    assert "redirect_stdout" not in handler, "the redirect is back inside the per-call path"
    assert "redact(" in handler, "the handler stopped masking what it did not classify"


def _make_pair() -> tuple[Any, Any, Any]:
    """Two sources and the events that pin the interleaving.

    A must still be inside when B enters, and must leave before B prints. Left to plain
    ``sleep(0)`` scheduling, A finished before B ever entered and the overlap the test is
    about never happened — it passed against the defect.
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
    """B enters, A enters and exits, then B prints — the order a per-call redirect loses.

    ``redirect_stdout`` assigns the process-global ``sys.stdout``, and this server runs
    requests concurrently. Wrapped per call, A's exit restores the real stdout while B is
    still inside, so B's provider line goes to the JSON-RPC frame; and B's own exit then
    restores the stderr object it saw on entry, leaving ``sys.stdout`` wrong for everything
    afterwards. One redirect over the whole run cannot nest, so it cannot race.
    """
    quiet, late, a_left = _make_pair()
    original = sys.stdout

    async def exercise() -> None:
        # Mirrors serve(): one redirect for the lifetime, every call inside it.
        with contextlib.redirect_stdout(sys.stderr):
            # A enters, B enters behind it, A leaves, then B prints. That is the order a
            # per-call redirect loses: A's exit restores the frame while B is still inside.
            a = asyncio.create_task(_call_tool("llmdb_query", {"query": "a"}, quiet))
            b = asyncio.create_task(_call_tool("llmdb_query", {"query": "b"}, late))
            await a
            a_left.set()
            await b

    asyncio.run(exercise())

    captured = capsys.readouterr()
    assert "provider banner" not in captured.out, "provider output reached the JSON-RPC stream"
    assert "provider banner" in captured.err, "it must stay visible, just not on stdout"
    assert sys.stdout is original, "stdout was left pointing somewhere else"


def test_an_async_handler_can_fetch_without_hitting_the_sync_refusal() -> None:
    source = _Source()
    out = asyncio.run(_call_tool("llmdb_query", {"query": "who?"}, source))
    payload = json.loads(out[0])
    assert payload["records"] == [{"name": "Ada"}]
    assert source.async_calls == 1


SENTINEL = "sk-canary-NOTAKEY"
"""A key literal in the shape a provider stamps one with. A sentinel, not a credential."""


def test_an_unclassified_failure_leaves_the_handler_masked() -> None:
    """The SDK puts `str(exc)` in the tool result whether or not this package named it."""

    class _Broken(_Source):
        async def afetch(self, query: str, **kwargs: Any) -> Any:
            raise RuntimeError(f"request failed: api_key={SENTINEL}")

    with pytest.raises(ValueError) as raised:
        asyncio.run(_call_tool("llmdb_query", {"query": "who?"}, _Broken()))

    assert SENTINEL not in str(raised.value)
    assert "***" in str(raised.value)


def test_the_sync_path_is_never_taken_from_the_async_handler() -> None:
    """The stand-in raises if fetch() is reached, which is what used to happen."""
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


def test_a_library_error_surfaces_with_its_message_intact() -> None:
    from blue_data_llm_source.types import QueryError

    class Failing(_Source):
        async def afetch(self, query: str, **kwargs: Any) -> Any:
            raise QueryError("query must not be empty or whitespace")

    with pytest.raises(ValueError, match="query must not be empty or whitespace"):
        asyncio.run(_call_tool("llmdb_query", {"query": " "}, Failing()))


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
    """The one test that would have caught a server that cannot start.

    Every other test in this package imports ``tools``, which is SDK-free by design, so the
    suite stayed green while ``server.py`` was unimportable against the installed SDK:
    ``mcp`` was declared as ``>=1.0``, pip resolved 2.0, and the low-level decorator API
    this module is built on was removed in that release.

    Skipped when the SDK is absent, which is the supported development configuration. It is
    not skipped in any environment where the server would actually be run.
    """
    pytest.importorskip("mcp")

    from blue_data_llm_mcp import server

    assert server.server is not None
    assert callable(server.main)


def test_the_handshake_reports_this_package_and_not_the_sdk() -> None:
    """`Server(name)` alone made every client read the SDK's version as this server's.

    The SDK falls back to `pkg_version("mcp")` when no `version=` is given, so the
    initialize response said 1.27.2 while the package and the metadata tool both said
    something else. Three answers to one question, and a client sees the wrong one first.
    """
    pytest.importorskip("mcp")

    from blue_data_llm_mcp import __version__
    from blue_data_llm_mcp import server as shipped

    options = shipped.server.create_initialization_options()

    assert options.server_name == "blue-data-llm"
    assert options.server_version == __version__
    assert options.instructions, "a client has nothing to read about what this server is for"


def test_the_sdk_is_pinned_not_floored() -> None:
    """A floor on a dependency whose major version removed the API is not a constraint."""
    pyproject = (PACKAGE.parents[1] / "pyproject.toml").read_text(encoding="utf-8")

    assert "mcp==" in pyproject, "the SDK must be pinned to a version whose API this matches"
    assert "mcp>=" not in pyproject


def test_a_dotted_ceiling_name_survives_the_protocol_layer() -> None:
    """The ceilings are advertised under names containing a dot, which nothing else here is.

    A property name is a wire-level detail once it leaves this package: it goes into the
    advertised `inputSchema` and comes back in a client's arguments. The direct-dispatch
    tests never see either. This drives the shipped server through the installed SDK, and it
    is `llmdb_estimate` because that one answers with no credential and contacts nothing.

    Guarded like its siblings below: the suite is specified to run without the protocol SDK.
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

    The SDK validates `inputSchema` before the decorated handler runs, so a deferred key —
    which is an extra property, and so schema-invalid — came back as a generic
    "Input validation error". It named the key and nothing else: not the version it moved
    to, not what to use instead. The direct-dispatch tests could not see that, because they
    never went through the SDK.

    Guarded like its sibling above. Three documents state that this suite runs without the
    protocol SDK installed, and it did not: this was the one test that imported it
    unconditionally, so the claim held everywhere except here.
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
    assert "max_parallel" in message


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

    A snapshot test pins whatever shape it was given, so it would have kept these three
    parameters as bare strings forever. This asserts the property the checklist asks for.
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
