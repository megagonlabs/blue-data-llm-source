"""meta.outcome, meta.stop_reasons and meta.gaps: the fields to read before the records.

An empty list of records is a valid answer, so the interesting question is never "how
many rows" but "why does this look the way it does". ``outcome`` answers it from a closed
vocabulary, ``stop_reasons`` lists every extraction limit that fired, ``stopped_by`` is
the first of them for a caller that wants one, and ``gaps`` carries operational
diagnostics that may explain missing rows.

``gaps`` is always present and an empty list means only that no supported diagnostic
fired. It is not a completeness guarantee: a model that simply answered with fewer records
than exist produces no gap, because nothing in such a response says it is short.

Every response below is scripted, so each outcome is produced on purpose rather than
waited for. No provider is contacted.

Run from the repository root:
    python3 examples/feature-demos/gaps_and_outcome_demo.py
"""

import json

from blue_data_llm_source import FetchResult, LlmSource

BOOTSTRAP = json.dumps(
    {
        "ask": "commercial airports in France",
        "constraints": {},
        "row_estimate": 120,
        "schema": {"attrs": [{"name": "name"}, {"name": "city"}], "key": "name"},
    }
)
PROPOSAL = json.dumps(
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
SPLIT = {"plan.mode": "partitioned", "plan.key": "name", "plan.max_depth": 1}

# The single-call bootstrap schedule, so one scripted answer covers the whole planning
# stage. The default schedule asks for the same facts across several calls.
COMBINED = LlmSource(properties={"plan.planning_order": "planning.combined"})


def planner(broken: str | None = None):
    """A scripted planner: one bootstrap answer, one partition proposal, rows per part."""

    def answer(prompt: str) -> str:
        if "Prepare a structured data request" in prompt:
            return BOOTSTRAP
        if "Propose one useful partition" in prompt:
            return PROPOSAL
        if broken is not None and broken in prompt:
            raise RuntimeError("the provider refused this part")
        for region, rows in BY_REGION.items():
            if region in prompt:
                return json.dumps(rows)
        return "[]"

    return answer


def report(label: str, result: FetchResult) -> None:
    meta = result.meta
    print(label)
    print("  records     ", len(result))
    print("  outcome     ", meta.outcome)
    print("  stopped by  ", meta.stopped_by)
    print("  stop reasons", meta.stop_reasons)
    for gap in meta.gaps:
        print("  gap:", gap)
    if not meta.gaps:
        print("  gap: none fired")
    print()


def main() -> None:
    flat = LlmSource()
    rows = '[{"name": "Orly", "city": "Paris"}]'

    report(
        "ok -- records, nothing stopped",
        flat.fetch("airports", attrs=["name"], llm_fn=lambda p: rows),
    )
    report(
        "empty_query -- nothing was asked",
        flat.fetch("", attrs=["name"], llm_fn=lambda p: rows),
    )
    report(
        "model_returned_none -- the model answered, and its answer was empty",
        flat.fetch("airports", attrs=["name"], llm_fn=lambda p: "[]"),
    )
    report(
        "filtered_to_empty -- it answered, and nothing it returned was what was asked for",
        flat.fetch("airports", attrs=["name"], llm_fn=lambda p: '[{"altitude": 119}]'),
    )
    report(
        "truncated -- a row cap fired and records remain",
        flat.fetch(
            "airports",
            attrs=["name"],
            llm_fn=lambda p: '[{"name": "Orly"}, {"name": "CDG"}]',
            properties={"limits.max_rows": 1},
        ),
    )
    report(
        "stopped_by_limit -- the same cap, with nothing left after it",
        flat.fetch(
            "airports",
            attrs=["name"],
            llm_fn=lambda p: '[{"name": "Orly"}]',
            properties={"limits.max_rows": 0},
        ),
    )
    report(
        "key_unavailable -- continuation asked for without a key to merge on",
        flat.fetch(
            "airports",
            attrs=["name"],
            llm_fn=lambda p: "[]",
            properties={"plan.max_continuations": 1},
        ),
    )

    report(
        "a split request where every part answered",
        COMBINED.fetch(
            "commercial airports in France",
            attrs=["name", "city"],
            llm_fn=planner(),
            properties=SPLIT,
        ),
    )
    report(
        "partial -- one part was lost, and the run says so instead of reporting ok",
        COMBINED.fetch(
            "commercial airports in France",
            attrs=["name", "city"],
            llm_fn=planner(broken="North"),
            properties=SPLIT,
        ),
    )
    report(
        "partial outranks a cap that fired at the same time; the cap is still in stop_reasons",
        COMBINED.fetch(
            "commercial airports in France",
            attrs=["name", "city"],
            llm_fn=planner(broken="North"),
            properties={**SPLIT, "limits.max_rows": 1},
        ),
    )
    report(
        "guard_collapsed -- the expansion did not fit under plan.max_leaves",
        COMBINED.fetch(
            "commercial airports in France",
            attrs=["name", "city"],
            llm_fn=planner(),
            properties={**SPLIT, "plan.max_leaves": 2},
        ),
    )
    report(
        "exact_rows_not_leaf_bound -- a root row target cannot be applied to separate parts",
        COMBINED.fetch(
            "commercial airports in France",
            attrs=["name", "city"],
            llm_fn=planner(),
            properties={**SPLIT, "plan.exact_rows": 10},
        ),
    )


if __name__ == "__main__":
    main()
