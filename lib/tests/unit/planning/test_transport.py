"""Versioned leaf task and result messages."""

from __future__ import annotations

import json

import pytest

from blue_data_llm_source.planning.plan import Leaf
from blue_data_llm_source.planning.schema import KEY_FROM_CALLER, Schema
from blue_data_llm_source.planning.transport import (
    SAFETY_WIRE_VERSION,
    ErrorData,
    LeafTask,
    LeafTaskResult,
    LocalLeafExecutor,
)
from blue_data_llm_source.types import Attr, ConfigError, Exact, OneOf, Usage


def task() -> LeafTask:
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
        settings={"max_continuations": 1, "empty_leaf_retry": False},
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
        ("wire_version", SAFETY_WIRE_VERSION + 1),
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
