import asyncio
import os
import runpy
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import cast

from pytest import MonkeyPatch

from blue_data_llm_mcp import tools


def test_langgraph_demo_passes_selected_environment_and_carries_history(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, dict[str, object]] = {}
    agent_inputs: list[list[object]] = []
    agent_options: dict[str, object] = {}

    class Message:
        def __init__(self, content: str, tool_calls: list[dict[str, str]] | None = None) -> None:
            self.content = content
            self.tool_calls = tool_calls or []

    class Agent:
        async def ainvoke(self, state: dict[str, object]) -> dict[str, list[object]]:
            messages = [
                Message(cast(dict[str, str], message)["content"])
                if isinstance(message, dict)
                else message
                for message in cast(list[object], state["messages"])
            ]
            agent_inputs.append(messages)
            task = cast(Message, messages[-1]).content
            if len(agent_inputs) == 3:
                assert any(
                    cast(Message, item).content
                    == (
                        "What would it cost to ask for the commercial airports in metropolitan "
                        "France with their name and IATA code?"
                    )
                    for item in messages
                )
            tool_name = (
                "llmdb_inspect_model",
                "llmdb_estimate",
                "llmdb_query",
            )[len(agent_inputs) - 1]
            return {
                "messages": [
                    *messages,
                    Message(f"answer to {task}", [{"name": tool_name}]),
                ]
            }

    class Client:
        def __init__(self, config: dict[str, dict[str, object]]) -> None:
            captured.update(config)

        async def get_tools(self) -> list[object]:
            return []

    def fake_chat_openai(**kwargs: object) -> object:
        return object()

    def fake_create_agent(*args: object, **kwargs: object) -> Agent:
        agent_options.update(kwargs)
        return Agent()

    adapters = ModuleType("langchain_mcp_adapters")
    adapters_client = ModuleType("langchain_mcp_adapters.client")
    setattr(adapters_client, "MultiServerMCPClient", Client)
    langchain_openai = ModuleType("langchain_openai")
    setattr(langchain_openai, "ChatOpenAI", fake_chat_openai)
    langchain = ModuleType("langchain")
    langchain_agents = ModuleType("langchain.agents")
    setattr(langchain_agents, "create_agent", fake_create_agent)
    for name, module in (
        ("langchain_mcp_adapters", adapters),
        ("langchain_mcp_adapters.client", adapters_client),
        ("langchain_openai", langchain_openai),
        ("langchain", langchain),
        ("langchain.agents", langchain_agents),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setenv("PATH", "/example/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
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
    output = StringIO()
    with redirect_stdout(output):
        asyncio.run(namespace["main"]())

    child = cast(dict[str, str], captured["llm"]["env"])
    assert captured["llm"]["command"] == "blue-data-llm-mcp"
    assert child["PATH"] == "/example/bin"
    assert child["OPENAI_API_KEY"] == "test-placeholder"
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
    assert len(agent_inputs) == 3
    assert agent_options["system_prompt"] == namespace["SYSTEM"]
    assert any(
        cast(Message, item).content
        == (
            "What would it cost to ask for the commercial airports in metropolitan "
            "France with their name and IATA code?"
        )
        for item in agent_inputs[2]
    )
    lines = [line for line in output.getvalue().splitlines() if line.startswith("called")]
    assert lines == [
        "called ['llmdb_inspect_model']",
        "called ['llmdb_estimate']",
        "called ['llmdb_query']",
    ]
