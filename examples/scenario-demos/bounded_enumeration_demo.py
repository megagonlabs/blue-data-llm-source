"""When the answer is longer than one completion, and nothing in the answer says so.

**Problem.** A model asked to enumerate a set larger than its output allowance returns a
valid, well-formed, *short* list. There is no marker in it. A caller counting records sees
twenty-something rows and no error, and has no way to tell a complete answer from a
truncated one -- which is why ``meta.gaps`` being empty on a flat run is not a
completeness claim and this library does not pretend it is.

**Solution.** Declare an identifying column with ``plan.key`` so repeated asks can be
merged, allow continuations with ``plan.max_continuations``, and then read ``gaps``. A run
that exhausted the set finishes with no diagnostic. A run that hit its cap while records
were still arriving says ``leaf_at_cap`` and names the number it reached, so "we have all
of them" and "we ran out of asks" are different answers rather than the same one.

The model here is scripted: it holds a 24-item list and will return at most six items per
ask, which is the behaviour being worked around. No provider is contacted, so the counts
below are arithmetic over real library behaviour rather than a model's mood on the day.

Run from the repository root:
    python3 examples/scenario-demos/bounded_enumeration_demo.py
"""

import json

from blue_data_llm_source import FetchResult, LlmSource

DEPARTMENTS = [
    "Ain",
    "Aisne",
    "Allier",
    "Alpes-de-Haute-Provence",
    "Hautes-Alpes",
    "Alpes-Maritimes",
    "Ardeche",
    "Ardennes",
    "Ariege",
    "Aube",
    "Aude",
    "Aveyron",
    "Bouches-du-Rhone",
    "Calvados",
    "Cantal",
    "Charente",
    "Charente-Maritime",
    "Cher",
    "Correze",
    "Corse-du-Sud",
    "Haute-Corse",
    "Cote-d'Or",
    "Cotes-d'Armor",
    "Creuse",
]
PAGE = 6
QUERY = "the departments of France, by name"


class ShortLister:
    """Returns at most six departments per ask, skipping any the prompt already excludes."""

    def __init__(self) -> None:
        self.asks = 0

    def __call__(self, prompt: str) -> str:
        self.asks += 1
        remaining = [name for name in DEPARTMENTS if f'"{name}"' not in prompt]
        return json.dumps([{"name": name} for name in remaining[:PAGE]])


def report(label: str, model: ShortLister, result: FetchResult) -> None:
    print(label)
    print("  asks made ", model.asks)
    print("  records   ", len(result), f"of {len(DEPARTMENTS)}")
    print("  outcome   ", result.meta.outcome)
    print("  gaps      ", result.meta.gaps or "none fired")
    print()


def main() -> None:
    source = LlmSource()

    flat = ShortLister()
    report(
        "one flat ask: a short answer that looks like a complete one",
        flat,
        source.fetch(QUERY, attrs=["name"], llm_fn=flat),
    )

    capped = ShortLister()
    report(
        "a key and two continuations: still short, and this time the run says so",
        capped,
        source.fetch(
            QUERY,
            attrs=["name"],
            llm_fn=capped,
            properties={"plan.key": "name", "plan.max_continuations": 2},
        ),
    )

    full = ShortLister()
    report(
        "enough continuations to exhaust the set: complete, and no diagnostic fires",
        full,
        source.fetch(
            QUERY,
            attrs=["name"],
            llm_fn=full,
            properties={"plan.key": "name", "plan.max_continuations": 8},
        ),
    )

    print("Before: 6 of 24 records, no error, no diagnostic, no way to know.")
    print("After:  24 of 24, or a named gap saying exactly how far the run got.")
    print()
    print(
        "Continuations cost asks. plan.max_continuations bounds asks per part, and each"
    )
    print(
        "ask may still be retried up to limits.max_retries times, so the ceiling on the"
    )
    print("whole request is a Budget rather than this one number.")


if __name__ == "__main__":
    main()
