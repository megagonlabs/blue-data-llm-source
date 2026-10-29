import asyncio
import os
import runpy
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

from pytest import MonkeyPatch

from blue_data_llm_mcp import tools


def test_langgraph_demo_passes_only_selected_environment(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, dict[str, object]] = {}

    class Client:
        def __init__(self, config: dict[str, dict[str, object]]) -> None:
            captured.update(config)

        async def get_tools(self) -> list[object]:
            return []

    def fake_chat_openai(**kwargs: object) -> object:
        return object()

    def fake_create_react_agent(*args: object, **kwargs: object) -> None:
        return None

    adapters = ModuleType("langchain_mcp_adapters")
    adapters_client = ModuleType("langchain_mcp_adapters.client")
    setattr(adapters_client, "MultiServerMCPClient", Client)
    langchain_openai = ModuleType("langchain_openai")
    setattr(langchain_openai, "ChatOpenAI", fake_chat_openai)
    langgraph = ModuleType("langgraph")
    langgraph_prebuilt = ModuleType("langgraph.prebuilt")
    setattr(langgraph_prebuilt, "create_react_agent", fake_create_react_agent)
    for name, module in (
        ("langchain_mcp_adapters", adapters),
        ("langchain_mcp_adapters.client", adapters_client),
        ("langchain_openai", langchain_openai),
        ("langgraph", langgraph),
        ("langgraph.prebuilt", langgraph_prebuilt),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setenv("PATH", "/example/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "example-provider-value")
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_LIMIT_COST", "0.02")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_LIMIT_CALLS", "3")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "512")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_CACHE_DIR", " ~/example-cache ")
    home_name = "USERPROFILE" if os.name == "nt" else "HOME"
    monkeypatch.setenv(home_name, "/example/home")
    monkeypatch.setenv("UNRELATED_SECRET", "private-parent-value")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("PYTHONPATH", "/private/parent/path")

    demo = Path(__file__).resolve().parents[4] / "examples/framework-demos/langgraph_mcp_demo.py"
    namespace = runpy.run_path(str(demo), run_name="demo_under_test")
    cast(list[object], namespace["TASKS"]).clear()
    asyncio.run(namespace["main"]())

    child = cast(dict[str, str], captured["llm"]["env"])
    assert captured["llm"]["command"] == "blue-data-llm-mcp"
    assert child["PATH"] == "/example/bin"
    assert child["OPENAI_API_KEY"] == "example-provider-value"
    assert child["BLUE_DATA_LLM_MODEL"] == "gpt-4o-mini"
    assert child["BLUE_DATA_LLM_SOURCE_LIMIT_COST"] == "0.02"
    assert child["BLUE_DATA_LLM_SOURCE_LIMIT_CALLS"] == "3"
    assert child["BLUE_DATA_LLM_SOURCE_MAX_TOKENS"] == "512"
    assert child["BLUE_DATA_LLM_SOURCE_CACHE_DIR"] == " ~/example-cache "
    assert child[home_name] == "/example/home"
    env_names = set(cast(tuple[str, ...], namespace["MCP_ENV_NAMES"]))
    assert set(child) <= env_names | {"SystemRoot", "HOME", "USERPROFILE"}
    server_settings = (*tools._ENVIRONMENT_SETTINGS, *tools._OPERATOR_LIMITS)  # pyright: ignore[reportPrivateUsage]
    assert {name for name, _, _ in server_settings} <= env_names
    assert "UNRELATED_SECRET" not in child
    assert "OPENAI_BASE_URL" not in child
    assert "PYTHONPATH" not in child
