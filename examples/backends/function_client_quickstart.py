"""Quickstart: one fetch, answered by a local function instead of a provider.

``llm_fn`` is a ``(prompt) -> str`` callable the source uses as its model client for
that call. The remaining steps use the normal library path: prompt rendering, JSON
parsing, attribute alignment, type checking, and result metadata. This is the shortest
useful example of the library and needs no credential or network access.

The response below is canned, so ``meta.accounting_known`` is ``False``. A callable may
answer from memory, call one provider, or make several provider calls; the source cannot
observe which occurred.

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
