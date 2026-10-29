"""Requested attributes, declared types, and what the source reports about the answer.

``attrs`` is either a list of names or a list of ``{"name": ..., "type": ...}`` dicts.
A declared type converts compatible values and reports incompatible ones. For example, a
numeric string declared as ``integer`` becomes a number. A number declared as ``string``
is reported in ``meta.type_mismatches`` rather than converted to a string. Neither value
is dropped. This flags a model response that supplies a number for a name field.

The source also reports three cases where data might otherwise be dropped silently:
``unmapped_keys`` for a returned key that matches no requested attribute,
``key_collisions`` for two returned keys that map to one attribute, and
``unchecked_types`` for a declared type this source does not know how to check.

Every response below is canned, so the deviations are deterministic and no provider is
contacted.

Run from the repository root:
    python3 examples/feature-demos/attrs_and_types_demo.py
"""

from blue_data_llm_source import LlmSource


def scripted(response: str):
    def answer(prompt: str) -> str:
        return response

    return answer


def show(title: str, query: str, attrs: object, response: str) -> None:
    result = LlmSource().fetch(query, attrs=attrs, llm_fn=scripted(response))
    meta = result.meta
    print(title)
    print("  response      ", response)
    print("  records       ", result.records)
    print("  outcome       ", meta.outcome)
    print("  unmapped keys ", meta.unmapped_keys)
    print("  key collisions", meta.key_collisions)
    print("  mismatches    ", meta.type_mismatches)
    print("  unchecked     ", meta.unchecked_types)
    print()


def main() -> None:
    typed = [
        {"name": "name", "type": "string"},
        {"name": "founded", "type": "integer"},
    ]

    show(
        "1. types declared and honored",
        "two Swiss universities",
        typed,
        '[{"name": "ETH Zurich", "founded": 1855}]',
    )

    show(
        "2. a numeric string under a declared integer is converted, not reported",
        "two Swiss universities",
        typed,
        '[{"name": "ETH Zurich", "founded": "1855"}]',
    )

    show(
        "3. a value that will not convert keeps what the model sent and is reported",
        "two Swiss universities",
        typed,
        '[{"name": "ETH Zurich", "founded": "the 1850s"}]',
    )

    show(
        "4. the other direction: a number under a declared string is reported, not stringified",
        "two Swiss universities",
        [{"name": "name", "type": "string"}],
        '[{"name": 1855}]',
    )

    show(
        "5. a key nobody asked for: reported rather than discarded quietly",
        "two Swiss universities",
        typed,
        '[{"name": "ETH Zurich", "founded": 1855, "rector": "Example Rector"}]',
    )

    show(
        "6. two keys for one attribute: the first wins, the second is reported with its value",
        "two Swiss universities",
        ["name"],
        '[{"name": "ETH Zurich", "Name": "EPFL"}]',
    )

    show(
        "7. a type this source does not check is named rather than pretended",
        "two Swiss universities",
        [{"name": "name", "type": "string"}, {"name": "campus", "type": "geojson"}],
        '[{"name": "ETH Zurich", "campus": {"lat": 47.3, "lon": 8.5}}]',
    )

    show(
        "8. an attribute the model left out is filled with None, and the record is kept",
        "two Swiss universities",
        ["name", "founded"],
        '[{"name": "ETH Zurich"}]',
    )

    show(
        "9. an explicit JSON null is indistinguishable from an absent key here",
        "two Swiss universities",
        ["name", "founded"],
        '[{"name": "ETH Zurich", "founded": null}]',
    )

    show(
        "10. a record answering none of the requested attributes is dropped entirely",
        "two Swiss universities",
        ["name", "founded"],
        '[{"rector": "Example Rector"}]',
    )


if __name__ == "__main__":
    main()
