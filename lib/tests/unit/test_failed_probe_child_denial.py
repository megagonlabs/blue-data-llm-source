from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from blue_data.registry.data_registry import DataRegistry  # pyright: ignore[reportMissingTypeStubs]
from blue_data.source.json.memory_json_source import (
    MemoryJSONSource,  # pyright: ignore[reportMissingTypeStubs]
)

from blue_data_llm_source import BudgetExceeded, FetchResult, LlmSource, TransportError
from blue_data_llm_source.llm.client import FunctionClient

ROUTES = ["fetch", "nl", "dict", "json", "tool", "registry"]
RECOVERED = [{"state": "alpha", "region": "A"}]


def invoke(route: str, model: Callable[[str], str]) -> FetchResult | list[dict[str, Any]]:
    properties: dict[str, Any] = {
        "plan.mode": "partitioned",
        "plan.key": "state",
        "plan.max_continuations": 0,
        "plan.max_depth": 1,
        "plan.max_partitions": 2,
        "plan.max_leaves": 2,
    }
    source = LlmSource(client=FunctionClient(model))
    if route == "fetch":
        return source.fetch(
            "states by region", attrs=["state", "region"], properties=properties, llm_fn=model
        )
    options = {**properties, "attrs": ["state", "region"]}
    if route == "nl":
        return source.execute_query_nl(
            "states by region", optional_properties=options, llm_fn=model
        )
    operation = {"op": "LLM.FETCH", "query": "states by region"}
    if route in ("dict", "json"):
        return source.execute_query(
            json.dumps(operation) if route == "json" else operation,
            optional_properties=options,
        )
    if route == "tool":
        return source.execute_nl_tool(
            "execute_query", {"query": "states by region"}, optional_properties=options
        )
    registry: Any = DataRegistry(store=MemoryJSONSource())
    registry.register_source(source)
    return registry.execute_query(source.name, operation, optional_properties=options)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("child_answer", [None, "[]", json.dumps(RECOVERED)])
def test_failed_probe_and_denied_children_preserve_failure_or_answer(
    route: str, child_answer: str | None
) -> None:
    trace: list[str] = []
    extractions = 0
    failure = TransportError("synthetic root transport failure", code="synthetic")

    def model(prompt: str) -> str:
        nonlocal extractions
        if prompt.startswith("Your task is to prepare a data request"):
            trace.append("analysis")
            return json.dumps({"ask": "states by region", "constraints": {}})
        if prompt.startswith("Resolve the output schema"):
            trace.append("schema")
            return json.dumps(
                {"attrs": [{"name": "state"}, {"name": "region"}], "primary_keys": ["state"]}
            )
        if prompt.startswith("Estimate the number of records"):
            trace.append("estimate")
            return json.dumps({"expected": 50, "lower": 50, "upper": 50, "row_unit": "states"})
        if prompt.startswith("Propose one useful partition"):
            trace.append("proposal")
            return json.dumps(
                {"key": "region", "values": ["A", "B"], "ambiguity": 0, "child_estimates": [25, 25]}
            )
        extractions += 1
        trace.append(f"extraction{extractions}")
        if extractions == 1:
            raise failure
        if extractions == 2 and child_answer is not None:
            return child_answer
        raise BudgetExceeded("child refused", limit="max_calls")

    if child_answer is None:
        with pytest.raises(TransportError) as caught:
            invoke(route, model)
        assert str(caught.value) == (
            "synthetic root transport failure; limits also stopped work: max_calls"
        )
        assert caught.value.__cause__ is failure
        assert caught.value is not failure
        assert caught.value.code == "synthetic"
    else:
        result = invoke(route, model)
        expected = json.loads(child_answer)
        if isinstance(result, FetchResult):
            assert result.records == expected
            assert result.meta.stop_reasons == ["max_calls"]
            assert "probe_failed: TransportError" in result.meta.gaps
            assert result.meta.record_origins == ([["0.0"]] if expected else [])
        else:
            assert result == expected
    assert trace == [
        "analysis",
        "schema",
        "estimate",
        "extraction1",
        "proposal",
        "extraction2",
        "extraction3",
    ]
