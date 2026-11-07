"""An empty modeled enumeration makes the request unsatisfiable.

These probes enter through the public facade. They distinguish an explicit empty constraint
from a filled enumeration and from a request with no constraints, and verify that the empty
case cannot widen into an extraction of the original request.
"""

from __future__ import annotations

import json
from typing import Any

from blue_data_llm_source.source import LlmSource


def analysis(constraints: dict[str, Any]) -> str:
    return json.dumps(
        {
            "ask": "airports",
            "constraints": constraints,
            "row_estimate": 30,
            "schema": {"attrs": [{"name": "name"}], "key": None},
        }
    )


def model(constraints: dict[str, Any]) -> Any:
    def fn(prompt: str, properties: Any = None) -> str:
        if "Prepare a structured data request" in prompt:
            return analysis(constraints)
        return json.dumps([{"name": "Orly"}])

    return fn


def fetch(constraints: dict[str, Any], **props: Any) -> Any:
    merged: dict[str, Any] = {
        "plan.mode": "partitioned",
        "plan.split_policy": "always",
        "plan.max_continuations": 0,
        "plan.planning_order": "planning.combined",
    }
    merged.update(props)
    return LlmSource().fetch(
        "airports", attrs=["name"], llm_fn=model(constraints), properties=merged
    )


class TestAnEnumerationThatNamedNothingIsUnsatisfiable:
    def test_empty_constraint_is_reported(self) -> None:
        result = fetch({"region": []})

        assert result.records == []
        assert any("empty constraint" in gap for gap in result.meta.gaps)

    def test_the_empty_constraint_is_not_reported_as_widening(self) -> None:
        result = fetch({"region": []})

        assert result.meta.outcome == "model_returned_none"
        assert not [gap for gap in result.meta.gaps if gap.startswith("constraint_dropped")]

    def test_a_filled_enumeration_reports_no_such_gap(self) -> None:
        # The pair. A gap raised on every run is a gap nobody reads, and without this the
        # test above would pass on a code that fires unconditionally.
        result = fetch({"region": ["North", "South"]})

        assert result.records == [{"name": "Orly"}]
        assert not [gap for gap in result.meta.gaps if gap.startswith("constraint_dropped")]

    def test_a_request_with_no_constraints_at_all_reports_no_such_gap(self) -> None:
        # The other neighbor: naming a field and failing to fill it is a different fact
        # from never naming one, and only the first is something to report.
        result = fetch({})

        assert result.records == [{"name": "Orly"}]
        assert not [gap for gap in result.meta.gaps if gap.startswith("constraint_dropped")]

    def test_an_empty_filter_prevents_the_wider_request_from_running(self) -> None:
        sent: list[str] = []

        def recording(prompt: str, properties: Any = None) -> str:
            if "Prepare a structured data request" in prompt:
                return analysis({"region": [], "country": "FR"})
            sent.append(prompt)
            return json.dumps([{"name": "Orly"}])

        result = LlmSource().fetch(
            "airports",
            attrs=["name"],
            llm_fn=recording,
            properties={
                "plan.mode": "partitioned",
                "plan.split_policy": "always",
                "plan.max_continuations": 0,
                "plan.planning_order": "planning.combined",
            },
        )

        assert sent == []
        assert result.records == []
        assert any("empty constraint" in gap for gap in result.meta.gaps)
