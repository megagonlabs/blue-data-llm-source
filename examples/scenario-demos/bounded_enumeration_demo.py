"""Show how continuations report an incomplete enumeration.

A flat ask can return a valid but incomplete list without a gap diagnostic.
``plan.key`` merges repeated asks, and ``plan.max_continuations`` bounds them.
When the cap is reached while records are still arriving, ``meta.gaps`` reports
``leaf_at_cap``. The fixture is a fictional catalog of 24 items. Its scripted
model returns at most six items per ask; no provider is contacted.

Run from the repository root:
    python3 examples/scenario-demos/bounded_enumeration_demo.py
"""

import json

from blue_data_llm_source import FetchResult, LlmSource

ITEMS = [f"Item {index:02d}" for index in range(1, 25)]
PAGE = 6
QUERY = "the items in a fictional 24-item catalog, by name"


class ShortLister:
    """Return at most six catalog items per ask, skipping excluded names."""

    def __init__(self) -> None:
        self.asks = 0

    def __call__(self, prompt: str) -> str:
        self.asks += 1
        remaining = [name for name in ITEMS if f'"{name}"' not in prompt]
        return json.dumps([{"name": name} for name in remaining[:PAGE]])


def report(label: str, model: ShortLister, result: FetchResult) -> None:
    print(label)
    print("  asks made ", model.asks)
    print("  records   ", len(result), f"of {len(ITEMS)} synthetic items")
    print("  outcome   ", result.meta.outcome)
    print("  gaps      ", result.meta.gaps or "none fired")
    print()


def main() -> None:
    source = LlmSource()
    print("Synthetic 24-item catalog")
    print()

    flat = ShortLister()
    report(
        "one flat ask",
        flat,
        source.fetch(QUERY, attrs=["name"], llm_fn=flat),
    )

    capped = ShortLister()
    report(
        "a key and two continuations",
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
        "enough continuations to exhaust the set",
        full,
        source.fetch(
            QUERY,
            attrs=["name"],
            llm_fn=full,
            properties={"plan.key": "name", "plan.max_continuations": 8},
        ),
    )

    print("A flat ask returned 6 of 24 synthetic items without a gap diagnostic.")
    print("Continuations either exhausted this fixture or reported a named gap.")
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
