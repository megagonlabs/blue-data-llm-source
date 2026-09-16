"""What a split request is weighed against before its first prompt is sent.

Weighing a fan-out up front is right: its earlier parts are paid for whether or not the
later ones fit, so a budget that runs out halfway has already spent money on an answer
nobody receives. What it must be weighed against is the ceiling that will actually bind,
and that is the narrowest one anywhere in the budget chain -- not the one written on this
request.

A caller's budget accumulates across every call it is handed to, and a fetch derives a
child from it. Sizing the demand from the child alone reads a retry allowance the parent
already forbade, refuses a request the parent can comfortably afford, and reports a call
ceiling that was never approached.
"""

from __future__ import annotations

import json
from typing import Any

from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import Outcome
from tests.doubles import ScriptedClient

ANALYSIS = json.dumps(
    {
        "ask": "commercial airports in France",
        "constraints": {"region": ["North"]},
        "row_estimate": 30,
        "schema": {"attrs": [{"name": "name"}], "key": "name"},
    }
)
ROWS = json.dumps([{"name": "Orly"}])


def scripted(*responses: str) -> ScriptedClient:
    """A client that declares every budget axis, so a call ceiling is enforceable here.

    A plain callable becomes a client that enforces rows alone, and the source refuses a
    ``limits.max_calls`` it cannot hold. That refusal is correct and is not what these
    probe, so the double that can hold the ceiling is the one they run against.
    """
    return ScriptedClient(list(responses))


def fetch(client: ScriptedClient, parent: Budget, **props: Any) -> Any:
    merged: dict[str, Any] = {"plan.mode": "partitioned", "plan.max_leaves": 1}
    merged.update(props)
    return LlmSource(client=client).fetch(
        "commercial airports in France", attrs=["name"], budget=parent, properties=merged
    )


def fetch_prompts(client: ScriptedClient) -> list[str]:
    return [p for p in client.prompts if "Prompt contract:" in p or "Attributes:" in p]


def affordable() -> Budget:
    """Exactly the worst case of the plan below: one analysis call, one leaf, no retries."""
    return Budget(Limits(max_calls=2, max_retries=0))


class TestARequestIsWeighedAgainstTheCeilingThatBinds:
    def test_a_plan_whose_worst_case_fits_is_not_refused(self) -> None:
        # One split part, one continuation-free leaf: two calls, and the parent allows two.
        # Refusing it spends nothing and returns nothing, which is the one outcome a caller
        # cannot act on.
        client = scripted(ANALYSIS, ROWS)
        result = fetch(client, affordable())
        assert fetch_prompts(client)
        assert result.meta.outcome != Outcome.STOPPED_BY_LIMIT

    def test_naming_the_parents_retry_allowance_again_changes_nothing(self) -> None:
        # The adversarial twin, and the sharpest statement of the defect: two spellings of
        # one budget. The parent forbids retries in both. Writing that down a second time on
        # the request is redundant, so a request admitted with it must be admitted without
        # it -- and today only the redundant spelling gets through.
        with_it = scripted(ANALYSIS, ROWS)
        without_it = scripted(ANALYSIS, ROWS)
        fetch(with_it, affordable(), **{"limits.max_retries": 0})
        fetch(without_it, affordable())
        assert bool(fetch_prompts(with_it)) == bool(fetch_prompts(without_it))

    def test_a_plan_maximum_that_cannot_fit_still_admits_one_attempt(self) -> None:
        client = scripted(ROWS)
        result = fetch(client, Budget(Limits(max_calls=1, max_retries=0)))
        assert len(fetch_prompts(client)) == 1
        assert result.records == [{"name": "Orly"}]
        assert result.meta.outcome == Outcome.OK

    def test_a_parent_retry_allowance_narrows_what_is_demanded(self) -> None:
        # The chain minimum, not the request's own number. The request below asks for two
        # retries and the parent forbids them, so the worst case is one attempt per call --
        # and reading the request alone demanded three times what could ever be spent.
        client = scripted(ANALYSIS, ROWS)
        result = fetch(client, affordable(), **{"limits.max_retries": 2})
        assert fetch_prompts(client)
        assert result.meta.outcome != Outcome.STOPPED_BY_LIMIT

    def test_the_flat_path_still_runs_on_a_single_call_budget(self) -> None:
        # Flat makes no planning call and is one leaf by construction, so it is weighed one
        # attempt at a time by the client. This is the behaviour that predates splitting and
        # the one an existing caller depends on.
        client = scripted(ROWS)
        fetch(client, Budget(Limits(max_calls=1, max_retries=0)), **{"plan.mode": "flat"})
        assert len(fetch_prompts(client)) == 1
