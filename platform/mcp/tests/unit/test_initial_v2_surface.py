"""Initial-v2 planning and result facts at the MCP boundary."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from blue_data_llm_source import ExecMeta, FetchResult, Outcome

from blue_data_llm_mcp import tools

PLAN_ARGUMENTS: dict[str, Any] = {
    "query": "people",
    "plan.mode": "partitioned",
    "plan.max_depth": 3,
    "plan.max_partitions": 6,
    "plan.target_rows_per_leaf": 25,
    "plan.schema_mode": "enrich",
    "plan.partition_key": "continent",
    "plan.planning_order": "planning.schema_first",
    "plan.expose_estimate": False,
}


class RecordingSource:
    def __init__(self, result: FetchResult | None = None) -> None:
        self.properties: dict[str, Any] = {}
        self.result = result if result is not None else FetchResult(records=[], meta=ExecMeta())

    async def afetch(self, *args: Any, **kwargs: Any) -> FetchResult:
        self.properties = dict(kwargs.get("properties") or {})
        return self.result


def test_hierarchy_and_schema_properties_are_declared_and_forwarded() -> None:
    source = RecordingSource()
    asyncio.run(tools.adispatch(tools.FETCH, dict(PLAN_ARGUMENTS), source=source))

    expected = dict(PLAN_ARGUMENTS)
    expected.pop("query")
    assert source.properties == expected
    assert set(expected) <= set(tools.PLAN_KEYS)

    preserve = dict(PLAN_ARGUMENTS)
    preserve["plan.schema_mode"] = "preserve"
    asyncio.run(tools.adispatch(tools.FETCH, preserve, source=source))
    assert source.properties["plan.schema_mode"] == "preserve"


@pytest.mark.parametrize(
    "outcome",
    [Outcome.PARTIAL, Outcome.MODEL_RETURNED_NONE, Outcome.TRUNCATED],
)
def test_result_states_keep_gaps_and_all_stop_reasons(outcome: str) -> None:
    meta = ExecMeta(
        outcome=outcome,
        gaps=["leaf_failed: leaf 0.1 raised"] if outcome == Outcome.PARTIAL else [],
        stop_reasons=["max_calls", "provider_output_limit"],
        stopped_by="max_calls",
        requested_attrs=["name"],
    )
    source = RecordingSource(FetchResult(records=[], meta=meta))

    payload = asyncio.run(tools.adispatch(tools.FETCH, {"query": "people"}, source=source))

    assert payload["meta"]["outcome"] == outcome
    assert payload["meta"]["gaps"] == meta.gaps
    assert payload["meta"]["stop_reasons"] == ["max_calls", "provider_output_limit"]
    assert payload["meta"]["stopped_by"] == "max_calls"
    assert payload["meta"]["requested_attrs"] == ["name"]
    assert payload["meta"]["row_estimate"] is None


@pytest.mark.parametrize("estimate", [120, 0, None])
def test_row_estimate_is_always_returned(estimate: int | None) -> None:
    source = RecordingSource(FetchResult(records=[], meta=ExecMeta(row_estimate=estimate)))

    payload = asyncio.run(tools.adispatch(tools.FETCH, {"query": "people"}, source=source))

    assert payload["meta"]["row_estimate"] == estimate
