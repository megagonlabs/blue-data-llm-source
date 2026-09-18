"""The orchestrator tool surface: four tools served, four withheld, one refused outright.

``DataSource`` offers a tool-calling interface for an orchestrating model -- the source
advertises tools, the caller invokes them one at a time. That tool set describes a
relational source, and only four of its eight tools mean anything for a model behind a
virtual database, so this source advertises those four and nothing else. The definitions
are the base class's own, filtered by name rather than rewritten, so an advertised schema
cannot drift from the dispatcher that reads it.

``execute_query_nl_tools`` -- the tool-driven loop -- is refused. It drives up to ten
provider calls that this source's ceilings do not govern, so serving it would put an
unbounded spend path behind a method this package never chose to offer.

The client here is a local double returning a canned response, so the ``execute_query``
tool answers without a provider.

Run from the repository root:
    python3 examples/feature-demos/tool_surface_demo.py
"""

import json
from typing import Any

from blue_data_llm_source import ConfigError, LlmSource


class Scripted:
    """The smallest client the source accepts: two methods, one canned answer."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._answer(prompt)

    async def acomplete(
        self, prompt: str, properties: dict[str, Any] | None = None
    ) -> str:
        return self._answer(prompt)

    def _answer(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def refused(source: LlmSource, name: Any) -> None:
    try:
        source.execute_nl_tool(name, {})
    except ConfigError as error:
        print(f"  {name!r}: {error}")


def main() -> None:
    client = Scripted('[{"name": "Orly", "city": "Paris"}]')
    source = LlmSource(client=client)

    print("advertised tools")
    for tool in source.get_nl_tools():
        print(f"  {tool['name']}")

    print()
    print("the virtual hierarchy a registry sync walks")
    print("  protocol / variant ", source.protocol, "/", source.variant)
    print("  list_databases     ", source.execute_nl_tool("list_databases", {}))
    print(
        "  list_collections   ",
        source.execute_nl_tool("list_collections", {"database": "llm"}),
    )
    schema = source.execute_nl_tool(
        "get_schema", {"database": "llm", "collection": "public"}
    )
    print("  get_schema         ", json.dumps(schema))

    print()
    print("execute_query is the one tool that reaches a model")
    records = source.execute_nl_tool(
        "execute_query",
        {
            "query": {
                "op": "LLM.FETCH",
                "query": "airports near Paris",
                "attrs": ["name", "city"],
            }
        },
    )
    print("  records", records)
    print("  the model was asked", len(client.prompts), "time(s)")

    print()
    print("a ceiling passed through this surface binds the call it arrived with")
    capped = source.execute_nl_tool(
        "execute_query",
        {
            "query": {
                "op": "LLM.FETCH",
                "query": "airports near Paris",
                "attrs": ["name"],
            }
        },
        optional_properties={"limits.max_rows": 0},
    )
    print("  records", capped)

    print()
    print("anything else is refused by name")
    refused(source, "get_metadata")
    refused(source, "get_sample")
    refused(source, 42)

    print()
    print("and the tool-driven loop is refused outright")
    try:
        source.execute_query_nl_tools("airports near Paris")
    except ConfigError as error:
        print(" ", error)


if __name__ == "__main__":
    main()
