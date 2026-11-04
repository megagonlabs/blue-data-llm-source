"""A ReAct agent over the real MCP server, choosing its own tools and its own order.

The LangGraph code does not depend on the source implementation. ``langchain-mcp-adapters`` launches
``blue-data-llm-mcp`` as a stdio subprocess, discovers the five tools it advertises --
``llmdb_query``, ``llmdb_estimate``, ``llmdb_list_models``, ``llmdb_inspect_model`` and
``llmdb_inspect_metadata`` -- and hands them to a ``create_agent``
agent, which decides what to call and when.

The system prompt tells the agent to estimate a request before running it.
``llmdb_estimate`` contacts no provider, so the agent can estimate the cost before deciding
whether to proceed.

**Set an operator ceiling before providing a live credential.** The server reads
``BLUE_DATA_LLM_SOURCE_LIMIT_COST``, ``_LIMIT_CALLS`` and ``_LIMIT_SECONDS`` from its
environment as ceilings for every call; an agent's ``limits.*`` can lower them but never
raise them. Without an operator ceiling, an agent that omits ``limits.max_cost`` can spend
whatever the provider charges. The system prompt also asks the agent to pass its own. The operator ceiling covers MCP
queries; it does not cap the agent's ``ChatOpenAI`` calls. Each follow-up resends the full
conversation history, including raw tool results, to the chat model. Chat-model input tokens
grow as that history accumulates.

Requires: OPENAI_API_KEY, the provider extra, ``langgraph``, ``langchain``,
``langchain-openai``, ``langchain-mcp-adapters``, and the MCP package installed so the
``blue-data-llm-mcp`` console script is on PATH.

Run from the repository root in the active root .venv:
    ./platform/llm-mcp/scripts/install.sh
    python -m pip install langgraph 'langchain>=1.0' langchain-openai langchain-mcp-adapters
    export BLUE_DATA_LLM_SOURCE_LIMIT_COST=0.02
    export BLUE_DATA_LLM_SOURCE_MAX_TOKENS=4000
    python3 examples/framework-demos/langgraph_mcp_demo.py
"""

import asyncio
import math
import os
from typing import Any

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI

SYSTEM = (
    "You answer questions by calling the blue-data-llm tools. "
    "Before any llmdb_query, call llmdb_estimate for the same request; it contacts no "
    "provider and costs nothing. If the estimate is above 0.02, say so and do not fetch. "
    "Always pass limits.max_cost and max_records on llmdb_query. "
    "Report the meta.outcome and meta.gaps the tool returns alongside the records, and "
    "say plainly when a result may be incomplete."
)

TASKS = [
    "Which library version and provider variant is this server running?",
    (
        "What would it cost to ask for the commercial airports in metropolitan France "
        "with their name and IATA code?"
    ),
    "Go ahead and get me at most eight of them, with name and IATA code.",
]

MCP_ENV_NAMES = (
    "PATH",
    "OPENAI_API_KEY",
    "BLUE_DATA_LLM_MODEL",
    "BLUE_DATA_LLM_SOURCE_REASONING_EFFORT",
    "BLUE_DATA_LLM_SOURCE_MAX_TOKENS",
    "BLUE_DATA_LLM_SOURCE_TEMPERATURE",
    "BLUE_DATA_LLM_SOURCE_TIMEOUT",
    "BLUE_DATA_LLM_SOURCE_MAX_PARALLEL",
    "BLUE_DATA_LLM_SOURCE_CACHE",
    "BLUE_DATA_LLM_SOURCE_CACHE_DIR",
    "BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN",
    "BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN",
    "BLUE_DATA_LLM_SOURCE_LIMIT_COST",
    "BLUE_DATA_LLM_SOURCE_LIMIT_CALLS",
    "BLUE_DATA_LLM_SOURCE_LIMIT_SECONDS",
)


def require_operator_ceiling() -> None:
    """Require a finite MCP cost ceiling before starting the agent."""
    raw = os.environ.get("BLUE_DATA_LLM_SOURCE_LIMIT_COST", "").strip()
    try:
        ceiling = float(raw)
    except ValueError:
        ceiling = float("nan")
    if not (math.isfinite(ceiling) and ceiling >= 0):
        raise SystemExit(
            "Set BLUE_DATA_LLM_SOURCE_LIMIT_COST to a finite, non-negative dollar amount"
        )


def mcp_child_env() -> dict[str, str]:
    names = MCP_ENV_NAMES + (("SystemRoot",) if os.name == "nt" else ())
    if os.environ.get("BLUE_DATA_LLM_SOURCE_CACHE_DIR", "").strip().startswith("~"):
        names += ("USERPROFILE",) if os.name == "nt" else ("HOME",)
    return {name: os.environ[name] for name in names if name in os.environ}


async def main() -> None:
    require_operator_ceiling()
    client = MultiServerMCPClient(
        {
            "llm": {
                "command": "blue-data-llm-mcp",
                "args": [],
                "transport": "stdio",
                "env": mcp_child_env(),
            }
        }
    )

    tools = await client.get_tools()
    print("tools discovered:", [tool.name for tool in tools])
    print()

    agent = create_agent(
        ChatOpenAI(model="gpt-4o-mini", temperature=0),
        tools,
        system_prompt=SYSTEM,
    )

    history: list[Any] = []
    for task in TASKS:
        print("task  ", task)
        turn_start = len(history)
        history.append({"role": "user", "content": task})
        state = await agent.ainvoke({"messages": history})
        history = list(state["messages"])
        turn_messages = history[turn_start:]
        called = [
            call["name"]
            for message in turn_messages
            for call in getattr(message, "tool_calls", []) or []
        ]
        print("called", called or "no tools")
        print("answer", state["messages"][-1].content)
        print()


if __name__ == "__main__":
    asyncio.run(main())
