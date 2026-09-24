"""A ReAct agent over the real MCP server, choosing its own tools and its own order.

The LangGraph code does not depend on the source implementation. ``langchain-mcp-adapters`` launches
``blue-data-llm-mcp`` as a stdio subprocess, discovers the five tools it advertises --
``llmdb_query``, ``llmdb_estimate``, ``llmdb_list_models``, ``llmdb_inspect_model`` and
``llmdb_inspect_metadata`` -- and hands them to a ``create_react_agent``
agent, which decides what to call and when.

The system prompt tells the agent to estimate a request before running it.
``llmdb_estimate`` contacts no provider, so the agent can estimate the cost before deciding
whether to proceed.

**Set an operator ceiling before providing a live credential.** The server reads
``BLUE_DATA_LLM_SOURCE_LIMIT_COST``, ``_LIMIT_CALLS`` and ``_LIMIT_SECONDS`` from its
environment as ceilings for every call; an agent's ``limits.*`` can lower them but never
raise them. Without an operator ceiling, an agent that omits ``limits.max_cost`` can spend
whatever the provider charges. The system prompt also asks the agent to pass its own.

Requires: OPENAI_API_KEY, the provider extra, and the MCP package installed so the
``blue-data-llm-mcp`` console script is on PATH.

Run from the repository root:
    pip install langgraph langchain-openai langchain-mcp-adapters
    pip install -e lib/ -e platform/llm-mcp/
    python3 examples/framework-demos/langgraph_mcp_demo.py
"""

import asyncio
import os

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

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


async def main() -> None:
    client = MultiServerMCPClient(
        {
            "llm": {
                "command": "blue-data-llm-mcp",
                "args": [],
                "transport": "stdio",
                "env": dict(os.environ),
            }
        }
    )

    tools = await client.get_tools()
    print("tools discovered:", [tool.name for tool in tools])
    print()

    agent = create_react_agent(
        ChatOpenAI(model="gpt-4o-mini", temperature=0),
        tools,
        prompt=SYSTEM,
    )

    for task in TASKS:
        print("task  ", task)
        state = await agent.ainvoke({"messages": [{"role": "user", "content": task}]})
        called = [
            call["name"]
            for message in state["messages"]
            for call in getattr(message, "tool_calls", []) or []
        ]
        print("called", called or "no tools")
        print("answer", state["messages"][-1].content)
        print()


if __name__ == "__main__":
    asyncio.run(main())
