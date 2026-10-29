"""Which part produced each record a caller receives.

The attribution is computed where the leaves are merged and then passes through five
transforms before anyone sees it: alignment builds new records, verification checks them,
empty records are dropped, exact duplicates are dropped again, and a row cap trims the tail.
Three of those five change the length of the list. Attribution must follow the surviving
records.

The expected origin is checked independently of the library's bookkeeping. Each record
carries the producing part's name, written by the model double from its prompt.
"""

from __future__ import annotations

import json
from typing import Any

from blue_data_llm_source.source import LlmSource

VALUES = ["North", "South", "Corsica"]
ANALYSIS = json.dumps(
    {
        "ask": "commercial airports in France",
        "constraints": {"region": VALUES},
        "row_estimate": 200,
        "schema": {"attrs": [{"name": "name"}], "key": "name"},
    }
)
PROPOSAL = json.dumps(
    {
        "key": "region",
        "values": VALUES,
        "ambiguity": 0,
        "child_estimates": [10, 10, 10],
    }
)

LEAF_OF = {value: f"0.{i}" for i, value in enumerate(VALUES)}
"""Plan order is the enumerated order, so a value names the leaf that will carry it."""


class Regional:
    """Answers each part with records naming the part, so origin is readable off the row."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = answers

    def region_of(self, prompt: str) -> str:
        found = [value for value in VALUES if value in prompt]
        assert len(found) == 1, f"expected one region in the prompt, got {found}"
        return found[0]

    def __call__(self, prompt: str, properties: Any = None) -> str:
        if "Prepare a structured data request" in prompt:
            return ANALYSIS
        if "Propose one useful partition" in prompt:
            return PROPOSAL
        return json.dumps(self.answers[self.region_of(prompt)])


def fetch(model: Regional, **props: Any) -> Any:
    merged: dict[str, Any] = {"plan.mode": "partitioned", "plan.key": "name"}
    merged.update(props)
    return LlmSource().fetch(
        "commercial airports in France", attrs=["name"], llm_fn=model, properties=merged
    )


def named(region: str, n: int = 1) -> list[dict[str, Any]]:
    return [{"name": f"{region.lower()}-{i}"} for i in range(n)]


def expected_origin(record: dict[str, Any]) -> str:
    """The leaf that produced *record*, read off the record rather than off the library."""
    region = str(record["name"]).split("-")[0]
    match = [value for value in VALUES if value.lower() == region]
    assert len(match) == 1, f"unreadable record name {record['name']!r}"
    return LEAF_OF[match[0]]


class TestAttributionSurvivesTheTransformsBelowIt:
    def test_every_record_is_attributed_to_the_part_that_produced_it(self) -> None:
        result = fetch(Regional({value: named(value) for value in VALUES}))
        assert len(result.meta.record_origins) == len(result.records)
        for record, origins in zip(result.records, result.meta.record_origins, strict=True):
            assert origins == [expected_origin(record)]

    def test_attribution_survives_a_record_dropped_for_being_empty(self) -> None:
        # A part answering with a field nobody asked for aligns to an all-None record and is
        # dropped. Every origin after that position then names the wrong part, and the list
        # is still exactly as long as it should be -- which is why nothing catches it.
        result = fetch(
            Regional(
                {
                    "North": [{"other": "x"}],
                    "South": named("South"),
                    "Corsica": named("Corsica"),
                }
            )
        )
        assert [r["name"] for r in result.records] == ["south-0", "corsica-0"]
        assert result.meta.record_origins == [["0.1"], ["0.2"]]

    def test_a_record_two_parts_produced_is_attributed_to_both(self) -> None:
        # Deduplication below the merge drops rather than merges, so a record two parts
        # returned keeps one origin and the fact that both had it is lost. Naming one part
        # is not a smaller claim than naming both -- it is a different and false one.
        shared = [{"name": "north-0"}]
        result = fetch(Regional({"North": shared, "South": shared, "Corsica": named("Corsica")}))
        assert [r["name"] for r in result.records] == ["north-0", "corsica-0"]
        assert result.meta.record_origins[0] == ["0.0", "0.1"]

    def test_attribution_is_trimmed_with_the_rows_it_describes(self) -> None:
        result = fetch(
            Regional({value: named(value) for value in VALUES}), **{"limits.max_rows": 2}
        )
        assert result.meta.truncated_to == 2
        assert len(result.meta.record_origins) == len(result.records) == 2
        for record, origins in zip(result.records, result.meta.record_origins, strict=True):
            assert origins == [expected_origin(record)]

    def test_a_flat_run_attributes_its_one_part(self) -> None:
        # The account is produced for every run, a one-part run included, so the field is
        # never absent on a path a caller might switch on.
        model = Regional({value: named(value) for value in VALUES})
        result = LlmSource().fetch("commercial airports in North", attrs=["name"], llm_fn=model)
        assert result.meta.record_origins == [["0.0"]] * len(result.records)
