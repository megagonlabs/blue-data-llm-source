"""Versioned leaf task and result messages."""

from __future__ import annotations

import json

import pytest

from blue_data_llm_source.execution.local import execution_settings
from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.plan import Leaf
from blue_data_llm_source.planning.schema import KEY_FROM_CALLER, Schema
from blue_data_llm_source.planning.transport import (
    REASONING_WIRE_VERSION,
    WIRE_VERSION,
    ErrorData,
    LeafTask,
    LeafTaskResult,
    LocalLeafExecutor,
)
from blue_data_llm_source.types import Attr, ConfigError, Exact, OneOf, Usage


def task(settings: dict[str, object] | None = None) -> LeafTask:
    leaf = Leaf(
        id="0.1.0",
        parent_id="0.1",
        query="retrieval wording",
        original_query="original question",
        constraints={"country": Exact("France"), "kind": OneOf(())},
        depth=2,
    )
    return LeafTask.from_leaf(
        leaf,
        caller_context="only current entries",
        schema=Schema(
            attrs=[Attr("name", "string"), Attr("country", "string")],
            key="name",
            key_from=KEY_FROM_CALLER,
        ),
        settings=settings or {"max_continuations": 1, "empty_leaf_retry": False},
    )


def test_task_json_round_trip_keeps_context_schema_filters_and_identity() -> None:
    original = task()
    restored = LeafTask.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored == original
    assert restored.filters["kind"] == OneOf(())
    assert restored.original_query == "original question"
    assert restored.caller_context == "only current entries"


def test_result_json_round_trip_uses_error_data_and_usage_primitives() -> None:
    result = LeafTaskResult(
        task_id="0.1.0",
        records=[{"name": "Paris", "population": 2.1}],
        usages=[Usage(model="test", prompt_tokens=4, completion_tokens=7, cost=0.01)],
        outcome="partial",
        error=ErrorData(kind="transport", message="late failure", code="timeout"),
    )
    assert LeafTaskResult.from_dict(json.loads(json.dumps(result.to_dict()))) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("wire_version", REASONING_WIRE_VERSION + 1),
        ("wire_version", True),
        ("wire_version", 1.0),
        ("prompt_version", "future"),
    ],
)
def test_unknown_versions_are_rejected(field: str, value: object) -> None:
    payload = task().to_dict()
    payload[field] = value
    with pytest.raises(ConfigError, match="unsupported"):
        LeafTask.from_dict(payload)


def test_live_objects_cannot_cross_in_settings() -> None:
    payload = task().to_dict()
    settings = payload["settings"]
    assert isinstance(settings, dict)
    settings["model"] = object()
    with pytest.raises(ConfigError, match="model"):
        LeafTask.from_dict(payload)


def test_settings_are_allowlisted_and_finite() -> None:
    for name, value in (("password", "secret"), ("temperature", float("nan"))):
        payload = task().to_dict()
        settings = payload["settings"]
        assert isinstance(settings, dict)
        settings[name] = value
        with pytest.raises(ConfigError):
            LeafTask.from_dict(payload)


@pytest.mark.parametrize(
    "name,value",
    [
        ("model", ""),
        ("model", "   "),
        ("max_output_tokens", 0),
        ("max_output_tokens", True),
        ("max_output_tokens", -1),
    ],
)
def test_wire_settings_reject_blank_models_and_nonpositive_token_limits(
    name: str, value: object
) -> None:
    payload = task().to_dict()
    settings = payload["settings"]
    assert isinstance(settings, dict)
    settings[name] = value
    with pytest.raises(ConfigError, match=name):
        LeafTask.from_dict(payload)


def test_wire_settings_preserve_nullable_model_and_token_limit() -> None:
    payload = task().to_dict()
    settings = payload["settings"]
    assert isinstance(settings, dict)
    settings["model"] = None
    settings["max_output_tokens"] = None
    restored = LeafTask.from_dict(payload)
    assert restored.settings["model"] is None
    assert restored.settings["max_output_tokens"] is None


def test_reasoning_effort_round_trips_on_its_wire_version() -> None:
    original = task({"reasoning_effort": "medium"})
    restored = LeafTask.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored.wire_version == REASONING_WIRE_VERSION
    assert restored.settings["reasoning_effort"] == "medium"


def test_gpt_4o_null_reasoning_stays_on_the_legacy_wire() -> None:
    settings = execution_settings(
        {"llm.model": "gpt-4o", "llm.reasoning_effort": None, "llm.temperature": 0},
        PlanLimits(),
        Budget(),
    )
    original = task(settings)
    restored = LeafTask.from_dict(json.loads(json.dumps(original.to_dict())))
    assert "reasoning_effort" not in restored.settings
    assert restored.settings["temperature"] == 0
    assert restored.wire_version == WIRE_VERSION


def test_gpt_6_luna_null_temperature_uses_only_the_reasoning_wire_field() -> None:
    settings = execution_settings(
        {
            "llm.model": "gpt-6-luna",
            "llm.reasoning_effort": "medium",
            "llm.temperature": None,
        },
        PlanLimits(),
        Budget(),
    )
    original = task(settings)
    restored = LeafTask.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored.settings["reasoning_effort"] == "medium"
    assert "temperature" not in restored.settings
    assert restored.wire_version == REASONING_WIRE_VERSION


def test_wire_rejects_sampling_with_active_reasoning() -> None:
    payload = task().to_dict()
    settings = payload["settings"]
    assert isinstance(settings, dict)
    settings["reasoning_effort"] = "medium"
    settings["temperature"] = 0.2
    payload["wire_version"] = REASONING_WIRE_VERSION
    payload["partition_safety_status"] = None
    payload["partition_rule_digest"] = None
    with pytest.raises(ConfigError, match="temperature"):
        LeafTask.from_dict(payload)


def test_usage_numbers_must_be_finite() -> None:
    with pytest.raises(ConfigError, match="non-negative"):
        LeafTaskResult(task_id="0", usages=[Usage(cost=float("nan"))]).to_dict()


@pytest.mark.asyncio
async def test_local_executor_rejects_a_result_for_another_task() -> None:
    async def wrong(_task: LeafTask) -> LeafTaskResult:
        return LeafTaskResult(task_id="other")

    with pytest.raises(ConfigError, match="does not match"):
        await LocalLeafExecutor(wrong).execute(task())


@pytest.mark.asyncio
async def test_local_executor_accepts_an_injected_serializable_result() -> None:
    async def answer(given: LeafTask) -> LeafTaskResult:
        carried = LeafTask.from_dict(json.loads(json.dumps(given.to_dict())))
        return LeafTaskResult(task_id=carried.task_id, records=[{"name": "Paris"}])

    result = await LocalLeafExecutor(answer).execute(task())
    assert result.records == [{"name": "Paris"}]
