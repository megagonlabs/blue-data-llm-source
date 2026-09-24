"""The four limit axes, what binds them, and what a refused ceiling looks like.

Rows are capped after parsing, so ``limits.max_rows`` applies to any
client. Calls, time and cost are claimed before a provider attempt, so they bind only a
client that declares that axis through ``budget_capabilities()``. ``FunctionClient``, the
wrapper around an ``llm_fn``, declares rows alone -- it cannot know what a caller's
callable is billed, how to stop it at a deadline, or how many provider calls one
invocation becomes. The other three ceilings are therefore refused with a ``ConfigError``.

The key set is closed, and each value is checked when the ceiling is built. A typo or
``NaN`` therefore raises an error instead of appearing configured without taking effect.

Every fetch below is answered by a canned function. Nothing contacts a provider.

Run from the repository root:
    python3 examples/feature-demos/limits_demo.py
"""

from blue_data_llm_source import ConfigError, LlmSource

THREE = """[
  {"name": "Charles de Gaulle"},
  {"name": "Orly"},
  {"name": "Nice Cote d'Azur"}
]"""


def answer(prompt: str) -> str:
    return THREE


def fetch(**limits: object):
    return LlmSource().fetch(
        "commercial airports in France",
        attrs=["name"],
        llm_fn=answer,
        properties=limits or None,
    )


def refused(label: str, **limits: object) -> None:
    try:
        fetch(**limits)
    except ConfigError as error:
        print(f"  {label}")
        print(f"    ConfigError: {error}")


def main() -> None:
    print("max_rows truncates and says so")
    capped = fetch(**{"limits.max_rows": 2})
    print("  records     ", [r["name"] for r in capped.records])
    print("  outcome     ", capped.meta.outcome)
    print("  truncated to", capped.meta.truncated_to)
    print("  stopped by  ", capped.meta.stopped_by)
    print("  stop reasons", capped.meta.stop_reasons)
    print("  gaps        ", capped.meta.gaps)

    print()
    print("a row ceiling that leaves nothing is stopped_by_limit, not truncated")
    nothing = fetch(**{"limits.max_rows": 0})
    print("  records ", nothing.records)
    print("  outcome ", nothing.meta.outcome)

    print()
    print("the provider-level axes are refused over an llm_fn, one message per axis")
    refused("cost", **{"limits.max_cost": 0.05})
    refused("calls", **{"limits.max_calls": 3})
    refused("time", **{"limits.max_seconds": 10})

    print()
    print("the key set is closed, and near misses are named")
    refused("unknown key", **{"limits.max_words": 5})
    refused("wrong prefix", **{"limit.max_rows": 5})

    print()
    print("values are checked when the ceiling is built")
    refused("negative", **{"limits.max_rows": -1})
    refused("boolean", **{"limits.max_rows": True})
    refused("not a number", **{"limits.max_rows": float("nan")})

    print()
    print("a ceiling passed with the request binds that request and does not leak")
    source = LlmSource()
    first = source.fetch(
        "commercial airports in France",
        attrs=["name"],
        llm_fn=answer,
        properties={"limits.max_rows": 1},
    )
    second = source.fetch(
        "commercial airports in France", attrs=["name"], llm_fn=answer
    )
    print("  with the ceiling   ", len(first), "record(s),", first.meta.outcome)
    print("  the next call      ", len(second), "record(s),", second.meta.outcome)


if __name__ == "__main__":
    main()
