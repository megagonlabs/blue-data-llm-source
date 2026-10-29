"""plan.mode: one ask, a split ask, an ask per entity -- and continuations within a part.

One model call answers a request by default. ``plan.mode`` selects a different structure:
``partitioned`` splits the ask by a key the planning stage found, ``row_by_row`` asks once
per entity it enumerated. Nothing picks a shape for you, and ``flat`` makes no planning
call at all.

``plan.max_continuations`` is the orthogonal control: how many times one part may ask
again for records it did not get. It needs ``plan.key``, because without an identifying
column the parts cannot be merged and continuation is disabled with a gap saying so.

The planners below use fixed scripts and recognize planning prompts by their opening
lines, so their call sequences are deterministic and no provider is contacted.
``plan.planning_order`` is set to ``planning.combined`` for the two
non-flat modes, the single-call bootstrap schedule; the shipped default asks for the same
facts across several calls.

Run from the repository root:
    python3 examples/feature-demos/planning_modes_demo.py
"""

import json

from blue_data_llm_source import FetchResult, LlmSource

AIRPORT_FACTS = json.dumps(
    {
        "ask": "commercial airports in France",
        "constraints": {},
        "row_estimate": 120,
        "schema": {"attrs": [{"name": "name"}, {"name": "city"}], "key": "name"},
    }
)
PARTITION = json.dumps(
    {
        "key": "region",
        "values": ["North", "South", "Corsica"],
        "ambiguity": 0,
        "child_estimates": [8, 8, 4],
    }
)
BY_REGION = {
    "North": [
        {"name": "Charles de Gaulle", "city": "Paris"},
        {"name": "Orly", "city": "Paris"},
    ],
    "South": [{"name": "Nice Cote d'Azur", "city": "Nice"}],
    "Corsica": [{"name": "Ajaccio Napoleon Bonaparte", "city": "Ajaccio"}],
}

CAPITAL_FACTS = json.dumps(
    {
        "ask": "the capital of each listed country",
        "constraints": {},
        "row_estimate": 3,
        "schema": {
            "attrs": [{"name": "country"}, {"name": "capital"}],
            "key": "country",
        },
    }
)
CAPITALS = {"Canada": "Ottawa", "France": "Paris", "Japan": "Tokyo"}
ENTITIES = json.dumps(
    {"keys": ["country"], "values": [{"country": name} for name in CAPITALS]}
)


ALL_AIRPORTS = [row for rows in BY_REGION.values() for row in rows]


class WholeAnswer:
    """Answers the whole ask in one response, which is what a flat fetch expects."""

    def __init__(self) -> None:
        self.sequence: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.sequence.append("extract")
        return json.dumps(ALL_AIRPORTS)


class Paged:
    """Answers one record per ask, then nothing -- a model that will not enumerate at once."""

    def __init__(self) -> None:
        self.sequence: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.sequence.append("extract")
        page = len(self.sequence) - 1
        return json.dumps(ALL_AIRPORTS[page : page + 1])


class Airports:
    """Planning answers and per-region rows for the airport request."""

    def __init__(self) -> None:
        self.sequence: list[str] = []

    def __call__(self, prompt: str) -> str:
        if "Prepare a structured data request" in prompt:
            self.sequence.append("bootstrap")
            return AIRPORT_FACTS
        if "Propose one useful partition" in prompt:
            self.sequence.append("proposal")
            return PARTITION
        self.sequence.append("extract")
        for region, rows in BY_REGION.items():
            if region in prompt:
                return json.dumps(rows)
        return "[]"


class Capitals:
    """Planning answers, an entity list, and one row per entity."""

    def __init__(self) -> None:
        self.sequence: list[str] = []

    def __call__(self, prompt: str) -> str:
        if "Prepare a structured data request" in prompt:
            self.sequence.append("bootstrap")
            return CAPITAL_FACTS
        if "Enumerate every entity" in prompt:
            self.sequence.append("entities")
            return ENTITIES
        self.sequence.append("extract")
        for country, capital in CAPITALS.items():
            if country in prompt:
                return json.dumps([{"country": country, "capital": capital}])
        return "[]"


def combined() -> LlmSource:
    return LlmSource(properties={"plan.planning_order": "planning.combined"})


def show(label: str, sequence: list[str], result: FetchResult) -> None:
    print(label)
    print("  calls   ", sequence)
    print("  records ")
    for record in result.records:
        print("     ", record)
    print("  origins ", result.meta.record_origins)
    print("  outcome ", result.meta.outcome)
    print("  gaps    ", result.meta.gaps)
    print()


def main() -> None:
    flat = WholeAnswer()
    show(
        "flat (the default): one call, no planning stage",
        flat.sequence,
        LlmSource().fetch(
            "commercial airports in France", attrs=["name", "city"], llm_fn=flat
        ),
    )

    continued = Paged()
    show(
        "flat with continuations: the same part asks again, merging on plan.key",
        continued.sequence,
        LlmSource().fetch(
            "commercial airports in France",
            attrs=["name", "city"],
            llm_fn=continued,
            properties={"plan.key": "name", "plan.max_continuations": 4},
        ),
    )

    split = Airports()
    show(
        "partitioned: bootstrap, one partition proposal, then one extraction per part",
        split.sequence,
        combined().fetch(
            "commercial airports in France",
            attrs=["name", "city"],
            llm_fn=split,
            properties={
                "plan.mode": "partitioned",
                "plan.key": "name",
                "plan.max_depth": 1,
            },
        ),
    )

    per_row = Capitals()
    show(
        "row_by_row: bootstrap, an entity enumeration, then one extraction per entity",
        per_row.sequence,
        combined().fetch(
            "the capital of each listed country",
            attrs=["country", "capital"],
            llm_fn=per_row,
            properties={"plan.mode": "row_by_row", "plan.key": "country"},
        ),
    )

    print(
        "record_origins names the part each record came from, so a split result can be"
    )
    print("traced back to the ask that produced it.")


if __name__ == "__main__":
    main()
