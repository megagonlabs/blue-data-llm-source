"""Public result facts retained across extraction, reduction, and admission."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest

import blue_data_llm_source.source as source_module
from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.planning.analysis import BootstrapAnalysis
from blue_data_llm_source.planning.coordinator import PlanningBundle, PlanningFacts
from blue_data_llm_source.planning.plan import FetchPlan, Leaf
from blue_data_llm_source.planning.schema import KEY_FROM_INFERRED, Schema
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import (
    STOPPED_BY_CONTINUATIONS,
    STOPPED_BY_OUTPUT,
    Attr,
    BudgetExceeded,
    Capability,
    LlmSourceError,
    Outcome,
    Usage,
)

Action = str | BaseException | tuple[str, str]


class BudgetedScript:
    """Run prompt-specific actions through the budget handed down by the facade."""

    def __init__(self, actions: Mapping[str, Sequence[Action]]) -> None:
        self.actions = {marker: list(script) for marker, script in actions.items()}
        self.provider_calls = 0

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._respond(prompt, properties)

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._respond(prompt, properties)

    def _respond(self, prompt: str, properties: dict[str, Any] | None) -> str:
        action = self._next(prompt)
        if isinstance(action, BudgetExceeded):
            raise action

        budget = (properties or {}).get("budget")
        assert isinstance(budget, Budget)
        with budget.attempt() as reservation:
            self.provider_calls += 1
            if isinstance(action, BaseException):
                reservation.keep_unpriced()
                raise action
            reservation.settle(0.0)

        text, finish_reason = action if isinstance(action, tuple) else (action, None)
        usage = Usage(model="scripted", finish_reason=finish_reason)
        sink = (properties or {}).get("usage_sink")
        if isinstance(sink, list):
            cast("list[Usage]", sink).append(usage)
        return text

    def _next(self, prompt: str) -> Action:
        markers = [marker for marker in self.actions if not marker.startswith("__")]
        marker = next((candidate for candidate in markers if candidate in prompt), None)
        planning_prompt = any(
            text in prompt
            for text in (
                "Prepare a structured data request",
                "Propose one useful partition",
                "Enumerate the entities",
                "planning",
            )
        )
        if marker is None and "__analysis__" in self.actions and planning_prompt:
            marker = "__analysis__"
        if marker is None:
            marker = "__default__"
        script = self.actions[marker]
        assert script, f"no action remains for {marker!r}"
        return script.pop(0)


def _analysis() -> str:
    return json.dumps(
        {
            "ask": "airports",
            "constraints": {},
            "row_estimate": 2,
            "schema": {"attrs": [{"name": "name"}]},
        }
    )


def _install_two_leaf_plan(monkeypatch: pytest.MonkeyPatch, order: Sequence[str]) -> None:
    leaves = [
        Leaf(
            id=f"0.{index}",
            parent_id="0",
            query=f"airports in {name}",
            constraints={},
            original_query="airports",
        )
        for index, name in enumerate(order)
    ]
    plan = FetchPlan(mode="partitioned", leaves=leaves)
    schema = Schema(attrs=[Attr("name")], key="name", key_from=KEY_FROM_INFERRED)

    async def fixed_plan(**kwargs: Any) -> PlanningBundle:
        client = kwargs["client"]
        await client.acomplete("planning", kwargs["properties"])
        return PlanningBundle(
            plan=plan,
            analysis=BootstrapAnalysis(ask="airports", constraints={}, row_estimate=2),
            schema=schema,
            facts=PlanningFacts(bootstrap_attempted=True),
        )

    monkeypatch.setattr(source_module, "coordinate_plan", fixed_plan)


def _partitioned(
    monkeypatch: pytest.MonkeyPatch,
    order: Sequence[str],
    actions: Mapping[str, Sequence[Action]],
    **properties: Any,
) -> tuple[Any, BudgetedScript]:
    _install_two_leaf_plan(monkeypatch, order)
    client = BudgetedScript({"__analysis__": [_analysis()], **actions})
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "airports",
        attrs=["name"],
        properties={"plan.mode": "partitioned", **properties},
    )
    return result, client


def test_first_call_failure_and_parsed_empty_are_distinct() -> None:
    shared = RuntimeError("provider failed")
    with pytest.raises(LlmSourceError) as caught:
        LlmSource(
            properties={"plan.planning_order": "planning.combined"},
            client=BudgetedScript({"__default__": [shared]}),
        ).fetch("airports")

    assert type(caught.value) is LlmSourceError
    assert caught.value.__cause__ is shared
    assert shared.args == ("provider failed",)

    empty = LlmSource(
        properties={"plan.planning_order": "planning.combined"},
        client=BudgetedScript({"__default__": ["[]"]}),
    ).fetch("airports")
    assert empty.records == []
    assert empty.meta.outcome == Outcome.MODEL_RETURNED_NONE
    assert empty.meta.gaps == []
    assert empty.meta.stop_reasons == []
    assert empty.meta.stopped_by is None


@pytest.mark.parametrize(
    ("failure", "reason", "calls"),
    [
        (RuntimeError("late failure"), None, 2),
        (BudgetExceeded("call limit reached", limit="max_calls"), "max_calls", 1),
    ],
)
def test_records_survive_a_late_failure_or_budget_denial(
    failure: BaseException, reason: str | None, calls: int
) -> None:
    client = BudgetedScript({"__default__": ['[{"name": "Ada"}]', failure]})
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "people",
        attrs=["name"],
        properties={"plan.key": "name", "plan.max_continuations": 1},
    )

    assert result.records == [{"name": "Ada"}]
    assert result.meta.outcome == Outcome.PARTIAL
    assert result.meta.stop_reasons == ([reason] if reason else [])
    assert result.meta.stopped_by == reason
    assert result.meta.calls == calls
    assert len(result.meta.record_origins) == len(result.records)


@pytest.mark.parametrize("order", [("North", "South"), ("South", "North")])
def test_parsed_empty_plus_failed_sibling_is_partial_without_an_empty_gap(
    monkeypatch: pytest.MonkeyPatch, order: tuple[str, str]
) -> None:
    result, _ = _partitioned(
        monkeypatch,
        order,
        {"North": ["[]"], "South": [RuntimeError("south failed")]},
    )

    assert result.records == []
    assert result.meta.outcome == Outcome.PARTIAL
    assert result.meta.stop_reasons == []
    assert sum("leaf_failed" in gap for gap in result.meta.gaps) == 1
    assert not any("leaf_empty" in gap for gap in result.meta.gaps)


def test_all_failed_leaves_keep_rows_from_their_earlier_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _ = _partitioned(
        monkeypatch,
        ("North", "South"),
        {
            "North": ['[{"name": "North row"}]', RuntimeError("north failed")],
            "South": ['[{"name": "South row"}]', RuntimeError("south failed")],
        },
        **{"plan.key": "name", "plan.max_continuations": 1},
    )

    assert result.records == [{"name": "North row"}, {"name": "South row"}]
    assert result.meta.outcome == Outcome.PARTIAL
    assert result.meta.calls == 5
    assert result.meta.record_origins == [["0.0"], ["0.1"]]
    assert sum("leaf_failed" in gap for gap in result.meta.gaps) == 2


def test_all_budget_denials_return_every_reason_in_precedence_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, client = _partitioned(
        monkeypatch,
        ("North", "South"),
        {
            "North": [BudgetExceeded("calls", limit="max_calls")],
            "South": [BudgetExceeded("cost", limit="max_cost")],
        },
    )

    assert result.records == []
    assert result.meta.outcome == Outcome.STOPPED_BY_LIMIT
    assert result.meta.stop_reasons == ["max_cost", "max_calls"]
    assert result.meta.stopped_by == "max_cost"
    assert result.meta.calls == 1
    assert client.provider_calls == 1


@pytest.mark.parametrize("order", [("North", "South"), ("South", "North")])
def test_mixed_failure_and_budget_denial_raise_with_both_facts_in_either_order(
    monkeypatch: pytest.MonkeyPatch, order: tuple[str, str]
) -> None:
    shared = RuntimeError("provider failed")
    _install_two_leaf_plan(monkeypatch, order)
    client = BudgetedScript(
        {
            "__analysis__": [_analysis()],
            "North": [shared],
            "South": [BudgetExceeded("cost limit reached", limit="max_cost")],
        }
    )

    with pytest.raises(LlmSourceError, match="max_cost") as caught:
        LlmSource(properties={"plan.planning_order": "planning.combined"}, client=client).fetch(
            "airports", attrs=["name"], properties={"plan.mode": "partitioned"}
        )

    assert caught.value.__cause__ is shared
    assert shared.args == ("provider failed",)
    assert not hasattr(shared, "stop_reasons")


def test_provider_output_limit_and_row_cap_both_survive_final_shaping() -> None:
    response = json.dumps(
        [
            {"first_name": "Ada", "First Name": "Grace", "age": "unknown"},
            {"first_name": "Katherine", "age": 25},
        ]
    )
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"},
        client=BudgetedScript({"__default__": [(response, "length")]}),
    ).fetch(
        "people",
        attrs=["first name", {"name": "age", "type": "int"}],
        max_records=1,
    )

    assert result.records == [{"first name": "Ada", "age": "unknown"}]
    assert result.meta.outcome == Outcome.STOPPED_BY_LIMIT
    assert result.meta.stop_reasons == [STOPPED_BY_OUTPUT, "max_rows"]
    assert result.meta.stopped_by == STOPPED_BY_OUTPUT
    assert result.meta.truncated_to == 1
    assert result.meta.record_origins == [["0.0"]]
    assert [item["record_index"] for item in result.meta.type_mismatches] == [0]
    assert [item["record_index"] for item in result.meta.key_collisions] == [0]
    assert any("leaf_stopped_by_limit" in gap for gap in result.meta.gaps)
    assert any("result_rows_discarded" in gap for gap in result.meta.gaps)


def test_unknown_estimate_does_not_create_a_gap_on_clean_execution() -> None:
    client = BudgetedScript(
        {
            "__analysis__": [json.dumps({"ask": "airports", "constraints": {}})],
            "__default__": ['[{"name": "Orly"}]'],
        }
    )
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "airports",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.max_depth": 0},
    )

    assert result.records == [{"name": "Orly"}]
    assert result.meta.outcome == Outcome.OK
    assert not any("estimate_unknown" in gap for gap in result.meta.gaps)


@pytest.mark.parametrize(
    ("planning_action", "expected"),
    [
        ("not json", "bootstrap response was unusable"),
        ((_analysis(), "length"), "bootstrap response was unusable"),
        (RuntimeError("planner unavailable"), "bootstrap response was unusable"),
    ],
)
def test_malformed_or_failing_optional_planning_falls_back_and_keeps_usage(
    planning_action: Action, expected: str
) -> None:
    client = BudgetedScript(
        {
            "__analysis__": [planning_action],
            "__default__": ['[{"name": "Orly"}]'],
        }
    )
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch("airports", attrs=["name"], properties={"plan.mode": "partitioned"})

    assert result.records == [{"name": "Orly"}]
    assert result.meta.outcome == Outcome.OK
    assert result.meta.calls == 2
    assert result.meta.stop_reasons == []
    assert any(expected in gap for gap in result.meta.gaps)


def test_affordable_flat_continuation_is_admitted_one_attempt_at_a_time() -> None:
    client = BudgetedScript({"__default__": ['[{"name": "Ada"}]', '[{"name": "Grace"}]']})
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "people",
        attrs=["name"],
        properties={
            "limits.max_calls": 2,
            "plan.key": "name",
            "plan.max_continuations": 1,
        },
    )

    assert result.records == [{"name": "Ada"}, {"name": "Grace"}]
    assert result.meta.calls == 2
    assert result.meta.stop_reasons == [STOPPED_BY_CONTINUATIONS]
    assert result.meta.stopped_by == STOPPED_BY_CONTINUATIONS


def test_partitioned_work_starts_when_only_part_of_its_worst_case_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, client = _partitioned(
        monkeypatch,
        ("North", "South"),
        {
            "North": ['[{"name": "North row"}]'],
            "South": ['[{"name": "South row"}]'],
        },
        **{"limits.max_calls": 2},
    )

    assert result.records in ([{"name": "North row"}], [{"name": "South row"}])
    assert result.meta.outcome == Outcome.PARTIAL
    assert result.meta.stop_reasons == ["max_calls"]
    assert result.meta.calls == 2
    assert client.provider_calls == 2
