"""Registering the source in the data layer, then querying it like any other source.

``LlmSource`` inherits ``blue_data.source.source.DataSource``, but the base class alone
does not register it under a protocol name. Build the instance and pass it to
``DataRegistry.register_source``. The registry records the class path, ``name``, and
``properties``. While the live instance exists, ``connect_source`` returns it; after it is
dropped, the registry rebuilds a source from the recorded class and properties, without
the injected client.

From there the source answers through the shared surface: ``execute_query_nl`` for a
natural-language string, ``execute_query`` for a dict carrying an ``op``. Both take a
``limits.*`` ceiling in ``optional_properties``, merged over the instance's properties for
that call only.

For other sources in this layer, ``llm_fn`` translates natural language into a native
dialect. This source has no other dialect, so ``translate_nl_to_native_query`` returns its
input unchanged and ``llm_fn`` serves as the model client. The ``op`` interface has no
``llm_fn`` parameter, so this example injects the client at construction. Neither path
contacts a provider.

Run from the repository root:
    python3 examples/integration-demos/register_source_demo.py
"""

import json
from typing import Any

from blue_data.registry.data_registry import DataRegistry
from blue_data_llm_source import LlmSource

RESPONSE = json.dumps(
    [
        {"name": "Charles de Gaulle", "iata_code": "CDG"},
        {"name": "Orly", "iata_code": "ORY"},
        {"name": "Nice Cote d'Azur", "iata_code": "NCE"},
    ]
)


class Scripted:
    """A client with the two methods injection requires, and one canned answer."""

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return RESPONSE

    async def acomplete(
        self, prompt: str, properties: dict[str, Any] | None = None
    ) -> str:
        return RESPONSE


def answer(prompt: str) -> str:
    return RESPONSE


def main() -> None:
    registry = DataRegistry(name="EXAMPLE_REGISTRY")
    source = LlmSource(
        name="llm",
        properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 512},
        client=Scripted(),
    )
    registry.register_source(source)

    print("registered sources ", registry.list_sources())
    recorded = registry.get_source_properties("llm")
    print("recorded class     ", recorded["_class"])
    connection = recorded["connection"]
    print(
        "recorded connection",
        {"protocol": connection["protocol"], "variant": connection["variant"]},
    )
    print("recorded model     ", recorded["llm.model"])

    print()
    print("execute_query_nl: a plain string in, a list of dicts out")
    records = registry.connect_source("llm").execute_query_nl(
        "commercial airports in France",
        llm_fn=answer,
        optional_properties={"attrs": ["name", "iata_code"]},
    )
    for record in records:
        print("  ", record)

    print()
    print("the same call with a ceiling that binds only this call")
    capped = registry.connect_source("llm").execute_query_nl(
        "commercial airports in France",
        llm_fn=answer,
        optional_properties={"attrs": ["name"], "limits.max_rows": 1},
    )
    print("  ", capped)

    print()
    print("execute_query with an op dict, routed through the registry")
    print("  LLM.MODELS  ", registry.execute_query("llm", {"op": "LLM.MODELS"}))
    fetched = registry.execute_query(
        "llm",
        {
            "op": "LLM.FETCH",
            "query": "commercial airports in France",
            "attrs": ["name"],
        },
    )
    print("  LLM.FETCH   ", fetched)
    sized = registry.execute_query(
        "llm",
        {
            "op": "LLM.ESTIMATE",
            "query": "commercial airports in France",
            "attrs": ["name"],
        },
    )
    print("  LLM.ESTIMATE", sized)

    print()
    print(
        "drop the live instance and the registry rebuilds one from the recorded class"
    )
    del registry.sources["llm"]
    rebuilt = registry.connect_source("llm")
    print("  same object ", rebuilt is source)
    print("  type        ", type(rebuilt).__name__)
    print("  name        ", rebuilt.name)
    print("  protocol    ", rebuilt.protocol, "/", rebuilt.variant)
    print(
        "  properties  ",
        rebuilt.properties["llm.model"],
        rebuilt.properties["llm.max_tokens"],
    )
    print()
    print(
        "A rebuilt source carries the recorded properties but not the injected client,"
    )
    print("so a query against it goes through llm_fn or the default provider client.")
    print(
        "  rebuilt, with llm_fn:",
        rebuilt.execute_query_nl(
            "commercial airports in France",
            llm_fn=answer,
            optional_properties={"attrs": ["name"], "limits.max_rows": 1},
        ),
    )


if __name__ == "__main__":
    main()
