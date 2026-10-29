"""LangGraph over the direct Python API: a bounded retrieval node feeding a writing node.

The pipeline has two nodes. ``retrieve`` calls ``LlmSource.fetch`` under one shared
``Budget`` across the graph's retrievals. ``respond`` makes a separate ``ChatOpenAI`` call
to turn the records into a sentence; that call is outside the retrieval budget.

``retrieve`` passes records, ``meta.outcome``, and ``meta.gaps`` to ``respond``.
The writing node mentions a detected limit or gap. An unflagged short answer can
still be incomplete; these fields do not measure completeness.

In ``langgraph_mcp_demo.py``, the agent chooses its tools and call order through the MCP
server. Here the pipeline is fixed, and the model only chooses the wording.

Requires: OPENAI_API_KEY, and the provider extra.

Run from the repository root in the active root .venv:
    python -m pip install langgraph langchain-openai
    python3 examples/framework-demos/langgraph_direct_demo.py
"""

from typing import Any, TypedDict

from blue_data_llm_source import Budget, Limits, LlmSource, LlmSourceError
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

PROPERTIES = {"llm.model": "gpt-4o-mini", "llm.max_tokens": 512, "llm.temperature": 0}
RUN = Budget(Limits(max_cost=0.05, max_calls=4))
SOURCE = LlmSource(properties=PROPERTIES)
WRITER = ChatOpenAI(model="gpt-4o-mini", temperature=0)


class State(TypedDict, total=False):
    task: str
    question: str
    attrs: list[str]
    limits: dict[str, Any]
    records: list[dict[str, Any]]
    outcome: str
    gaps: list[str]
    reply: str


def retrieve(state: State) -> State:
    """The library call, bounded by the graph's own budget."""
    try:
        result = SOURCE.fetch(
            state["question"],
            attrs=state["attrs"],
            properties=state.get("limits") or None,
            budget=RUN,
        )
    except LlmSourceError as error:
        return {
            "records": [],
            "outcome": f"{type(error).__name__}: {error}",
            "gaps": [],
        }
    return {
        "records": result.records,
        "outcome": result.meta.outcome,
        "gaps": list(result.meta.gaps),
    }


def respond(state: State) -> State:
    """Compose the reply, and say plainly when the retrieval did not finish."""
    prompt = (
        f"Task: {state['task']}\n"
        f"Records retrieved: {state['records']}\n"
        f"Retrieval outcome: {state['outcome']}\n"
        f"Retrieval gaps: {state['gaps'] or 'none'}\n\n"
        "Answer the task from the records. If the outcome is anything other than 'ok', "
        "or a gap is listed, say in one sentence that the list may be incomplete and why."
    )
    return {"reply": WRITER.invoke(prompt).content}


def build() -> Any:
    graph = StateGraph(State)
    graph.add_node("retrieve", retrieve)
    graph.add_node("respond", respond)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "respond")
    graph.add_edge("respond", END)
    return graph.compile()


def main() -> None:
    pipeline = build()

    for task, question, attrs, limits in (
        (
            "Draft a one-line note naming the Paris airports a traveller can fly into.",
            "commercial airports serving Paris",
            ["name", "iata_code"],
            {},
        ),
        (
            "Draft the same note for the whole country.",
            "every commercial airport in metropolitan France",
            ["name", "iata_code"],
            {"limits.max_rows": 5},
        ),
    ):
        state = pipeline.invoke(
            {"task": task, "question": question, "attrs": attrs, "limits": limits or {}}
        )
        print("task    ", task)
        print("outcome ", state["outcome"])
        print("records ", len(state["records"]))
        print("gaps    ", state["gaps"] or "none")
        print("reply   ", state["reply"])
        print()

    print("budget: calls", RUN.calls, "cost", round(RUN.cost, 6))


if __name__ == "__main__":
    main()
