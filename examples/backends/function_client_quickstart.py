"""Quickstart: one fetch, answered by a local function instead of a provider.

``llm_fn`` is a ``(prompt) -> str`` callable the source uses as its model client for
that call. Everything else runs for real -- prompt rendering, JSON parsing, attribute
alignment, type checking, result metadata -- so the shortest useful example of this
library needs no credential and no network.

The response below is canned, which is also why ``meta.accounting_known`` comes back
False: a callable may answer from memory, call one provider, or fan out to five, and
none of that is visible from inside the source.

Run from the repository root:
    python3 examples/backends/function_client_quickstart.py
"""

from blue_data_llm_source import LlmSource

CANNED = """[
  {"name": "Charles de Gaulle", "iata_code": "CDG", "opened": 1974},
  {"name": "Orly", "iata_code": "ORY", "opened": 1932},
  {"name": "Nice Cote d'Azur", "iata_code": "NCE", "opened": 1956}
]"""


def answer(prompt: str) -> str:
    """Stand in for the model. The prompt is ignored; the answer is fixed."""
    return CANNED


def main() -> None:
    source = LlmSource()
    result = source.fetch(
        "commercial airports in France",
        attrs=[
            {"name": "name", "type": "string"},
            {"name": "iata_code", "type": "string"},
            {"name": "opened", "type": "integer"},
        ],
        llm_fn=answer,
    )

    print("records")
    for record in result.records:
        print(" ", record)

    meta = result.meta
    print()
    print("outcome          ", meta.outcome)
    print("requested attrs  ", meta.requested_attrs)
    print("observed keys    ", meta.observed_keys)
    print("unmapped keys    ", meta.unmapped_keys)
    print("type mismatches  ", meta.type_mismatches)
    print("gaps             ", meta.gaps)
    print("stopped by       ", meta.stopped_by)
    print()
    print("model            ", meta.model)
    print("calls            ", meta.calls)
    print("cost             ", meta.cost)
    print("accounting known ", meta.accounting_known)
    print()
    print("A cost of 0.0 with accounting_known False means 'not measured', not 'free'.")


if __name__ == "__main__":
    main()
