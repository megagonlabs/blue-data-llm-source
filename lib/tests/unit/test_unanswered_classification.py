"""Classify runs in which every part is unanswered.

Leaf order follows model-produced partition values. The order-invariance cases check both
orderings so the public result is independent of that order. The all-unanswered path returns before
``reduce_leaves`` and therefore has no computed gap list.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import (
    BudgetExceeded,
    Capability,
    LlmSourceError,
    Outcome,
)

FIRST, SECOND = "North", "South"


def analysis(order: list[str]) -> str:
    return json.dumps(
        {
            "ask": "airports",
            "constraints": {"region": order},
            "row_estimate": 200,
            "schema": {"attrs": [{"name": "name"}], "key": "name"},
        }
    )


def proposal(order: list[str]) -> str:
    return json.dumps(
        {
            "key": "region",
            "values": order,
            "ambiguity": 0,
            "child_estimates": [10] * len(order),
        }
    )


class Unanswered:
    """Every part comes back unanswered, each for the reason its name is mapped to.

    Keyed on the value in the prompt rather than on call order, so the double says the same
    thing whichever order the parts run in — otherwise the test would be measuring itself.
    """

    def __init__(self, order: list[str], reasons: Mapping[str, BaseException]) -> None:
        self.order = order
        self.reasons = reasons

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._respond(prompt, properties)

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._respond(prompt, properties)

    def _respond(self, prompt: str, properties: dict[str, Any] | None) -> str:
        if "Prepare a structured data request" in prompt:
            return analysis(self.order)
        if "Propose one useful partition" in prompt:
            return proposal(self.order)
        for value, error in self.reasons.items():
            if value in prompt:
                raise error
        return "[]"


def fetch(order: list[str], reasons: Mapping[str, BaseException]) -> Any:
    return LlmSource(client=Unanswered(order, reasons)).fetch(
        "airports",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.key": "name"},
    )


def outcome_of(order: list[str], reasons: Mapping[str, BaseException]) -> str:
    """What the run reported, as one comparable token in either order."""
    try:
        result = fetch(order, reasons)
    except LlmSourceError as e:
        return f"raised:{type(e.__cause__ or e).__name__}"
    return f"result:{result.meta.outcome}:{result.meta.stopped_by}"


REFUSED = BudgetExceeded("cost limit reached", limit="max_cost")
FAILED = RuntimeError("provider failed")


class TestClassificationDoesNotDependOnLeafOrder:
    """The property no single-ordering test can state."""

    def test_a_mixed_run_reports_the_same_thing_in_either_order(self) -> None:
        # The defect, as the caller met it: reversing two enumerated values turned a raised
        # provider failure into a budget-stop result carrying no gap.
        mixed = {FIRST: FAILED, SECOND: REFUSED}
        assert outcome_of([FIRST, SECOND], mixed) == outcome_of([SECOND, FIRST], mixed)

    def test_every_part_refused_reports_the_ceiling_in_either_order(self) -> None:
        both = {FIRST: REFUSED, SECOND: REFUSED}
        for order in ([FIRST, SECOND], [SECOND, FIRST]):
            assert outcome_of(order, both) == "result:stopped_by_limit:max_cost"

    def test_every_part_failing_raises_in_either_order(self) -> None:
        both = {FIRST: FAILED, SECOND: FAILED}
        for order in ([FIRST, SECOND], [SECOND, FIRST]):
            assert outcome_of(order, both).startswith("raised:")


class TestAMixedRunKeepsBothFacts:
    """Two facts, one channel, and neither is safe to drop."""

    def test_the_failure_is_raised_rather_than_reported_as_a_ceiling(self) -> None:
        # Nothing was answered, so there is no partial result to preserve and the caller is
        # owed the exception the error hierarchy promises. Reporting a ceiling instead would
        # hand back an empty result whose only stated cause is a limit that explains one of
        # the two parts.
        with pytest.raises(LlmSourceError):
            fetch([FIRST, SECOND], {FIRST: FAILED, SECOND: REFUSED})

    def test_the_provider_s_own_exception_is_not_written_to(self) -> None:
        # The provider may reuse an exception instance. Do not attach ceiling state to it;
        # another caller must receive the same object unchanged.
        shared = RuntimeError("provider failed")
        with pytest.raises(LlmSourceError):
            fetch([FIRST, SECOND], {FIRST: shared, SECOND: REFUSED})

        assert not hasattr(shared, "stopped_by")
        assert shared.args == ("provider failed",)


class TestAPartialRunIsStillNotThisCase:
    """The neighbour that must not be swept into it."""

    def test_one_part_answering_keeps_the_run_a_partial_result(self) -> None:
        # `PARTIAL` is defined over the plan and needs something to have come back. Nothing
        # here changes that: a run where one part answered is a result, not an exception.
        class OneAnswers(Unanswered):
            def _respond(self, prompt: str, properties: dict[str, Any] | None) -> str:
                if "Prepare a structured data request" in prompt:
                    return analysis(self.order)
                if "Propose one useful partition" in prompt:
                    return proposal(self.order)
                if FIRST in prompt:
                    raise FAILED
                return json.dumps([{"name": "Orly"}])

        result = LlmSource(client=OneAnswers([FIRST, SECOND], {})).fetch(
            "airports",
            attrs=["name"],
            properties={"plan.mode": "partitioned", "plan.key": "name"},
        )
        assert result.records
        assert result.meta.outcome == Outcome.PARTIAL
