"""Planning facts, schedule controls, and estimate exposure."""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any, cast

import pytest

from blue_data_llm_source import prompts
from blue_data_llm_source.execution.local import execution_settings, run_local_leaf
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.planning.analysis import DataTask, QueryAnalysisResult, QueryAnalyzer
from blue_data_llm_source.planning.bounds import MODE_PARTITIONED, PlanLimits
from blue_data_llm_source.planning.cardinality import (
    PARSE_INVALID,
    CardinalityEstimate,
    CardinalityEstimator,
)
from blue_data_llm_source.planning.context import (
    PLANNING_CARDINALITY_FIRST,
    PLANNING_COMBINED,
    PLANNING_LOCKED_PARALLEL,
    PLANNING_LOCKED_SEQUENTIAL,
    PLANNING_SCHEMA_FIRST,
    PlanningContext,
    PlanningStage,
)
from blue_data_llm_source.planning.coordinator import coordinate_plan
from blue_data_llm_source.planning.schedules import build_planning_context, gather_siblings
from blue_data_llm_source.planning.schema import (
    KEY_FROM_CALLER,
    SCHEMA_ENRICH,
    Schema,
    SchemaDefinition,
    SchemaGenerator,
)
from blue_data_llm_source.planning.transport import LeafTask
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import Attr, BudgetExceeded, ConfigError, Exact, Usage


def analysis_response() -> str:
    return json.dumps({"ask": "precise places", "constraints": {"active": True}})


def schema_response() -> str:
    return json.dumps(
        {
            "attrs": [
                {"name": "name", "type": "string"},
                {"name": "country", "type": "string"},
            ],
            "primary_keys": ["name"],
        }
    )


def cardinality_response() -> str:
    return json.dumps({"expected": 120, "lower": 100, "upper": 140, "row_unit": "places"})


class PlanningClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def bound_for_attempt(self, _prompt: str, _properties: dict[str, Any]) -> float:
        return 0.0

    async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
        self.prompts.append(prompt)
        sink = properties.get("usage_sink")
        if isinstance(sink, list):
            cast("list[Usage]", sink).append(Usage(model="planning"))
        if "prepare a data request" in prompt:
            return analysis_response()
        if "Resolve the output schema" in prompt:
            return schema_response()
        if "Estimate the number of records" in prompt:
            return cardinality_response()
        if "Prepare a structured data request" in prompt:
            return json.dumps(
                {
                    "ask": "precise places",
                    "constraints": {"active": True},
                    "row_estimate": json.loads(cardinality_response()),
                    "schema": {"attrs": "bad", "primary_keys": ["name"]},
                }
            )
        return json.dumps({"key": "country", "values": [], "ambiguity": 0})


async def context_for(
    order: str,
    client: PlanningClient,
    *,
    declared: Schema | None = None,
    budget: Budget | None = None,
) -> PlanningContext:
    context, _ = await build_planning_context(
        original_query="places",
        caller_context="current",
        caller_constraints={},
        declared_schema=declared or Schema(attrs=[Attr("name", "string")]),
        schema_mode=SCHEMA_ENRICH,
        client=client,
        budget=budget or Budget(Limits(max_calls=20, max_retries=0)),
        planning_order=order,
        extraction_prompt="extract",
    )
    return context


def test_domain_objects_snapshot_mutable_inputs() -> None:
    constraints = {"country": Exact("France")}
    attrs = [Attr("name", "string")]
    task = DataTask(
        id="0",
        parent_id=None,
        original_query="places",
        query="places",
        constraints=constraints,
    )
    schema = SchemaDefinition(attrs=attrs, primary_keys=("name",), key_from=KEY_FROM_CALLER)
    analysis = QueryAnalyzer.parse(
        analysis_response(), asked="places", caller_constraints=constraints
    )
    context = PlanningContext(
        analysis=analysis,
        schema=schema,
        cardinality=CardinalityEstimate(expected=1, parse_status="parsed"),
    )

    constraints["country"] = Exact("Germany")
    attrs.append(Attr("country", "string"))
    assert task.constraints["country"] == Exact("France")
    assert schema.attrs == (Attr("name", "string"),)
    assert context.schema.attrs == (Attr("name", "string"),)
    with pytest.raises(TypeError):
        task.constraints["year"] = Exact(2024)  # type: ignore[index]


@pytest.mark.parametrize("value", [None, True, 1, 1.5, "France"])
def test_data_task_accepts_finite_scalar_partition_values(value: object) -> None:
    task = DataTask(
        id="0",
        parent_id=None,
        original_query="places",
        query="places",
        partition_value=value,  # pyright: ignore[reportArgumentType]
    )
    assert task.partition_value == value


@pytest.mark.parametrize("value", [[], {}, math.nan, math.inf, "  "])
def test_data_task_rejects_mutable_or_invalid_partition_values(value: object) -> None:
    with pytest.raises(ConfigError, match="partition_value"):
        DataTask(
            id="0",
            parent_id=None,
            original_query="places",
            query="places",
            partition_value=value,  # pyright: ignore[reportArgumentType]
        )


def _digest_context(*, constraint: object = 1, partition_value: object = 1) -> PlanningContext:
    task = DataTask(
        id="0",
        parent_id=None,
        original_query="places",
        query="places",
        constraints={"key": Exact(constraint)},  # pyright: ignore[reportArgumentType]
        partition_value=partition_value,  # pyright: ignore[reportArgumentType]
    )
    return PlanningContext(
        analysis=QueryAnalysisResult(
            ask="places",
            constraints=task.constraints,
            task=task,
        ),
        schema=SchemaDefinition(attrs=[Attr("key")]),
        cardinality=CardinalityEstimate(),
    )


def test_context_digest_uses_json_scalar_identity_recursively() -> None:
    assert _digest_context(constraint=1).digest == _digest_context(constraint=1.0).digest
    assert _digest_context(constraint=True).digest != _digest_context(constraint=1).digest
    assert _digest_context(partition_value=1).digest == _digest_context(partition_value=1.0).digest
    assert _digest_context(partition_value=True).digest != _digest_context(partition_value=1).digest


def test_planning_stage_rejects_inconsistent_state() -> None:
    digest = "0" * 64
    with pytest.raises(ConfigError, match="provider_attempts"):
        PlanningStage("analysis", True, False, digest, provider_attempts=True)
    with pytest.raises(ConfigError, match="unattempted"):
        PlanningStage("analysis", False, True, digest)
    with pytest.raises(ConfigError, match="SHA-256"):
        PlanningStage("analysis", True, False, "not-a-digest")


def test_each_independent_parser_has_its_own_contract() -> None:
    analysis = QueryAnalyzer.parse(analysis_response(), asked="places")
    schema = SchemaGenerator.parse(
        schema_response(), Schema(attrs=[Attr("name", "string")]), mode=SCHEMA_ENRICH
    )
    estimate = CardinalityEstimator.parse(cardinality_response(), row_unit="records")

    assert analysis.ask == "precise places"
    assert schema is not None and schema.primary_keys == ("name",)
    assert estimate.expected == 120
    assert (
        CardinalityEstimator.parse('"between 2 and 300"', row_unit="records").parse_status
        == PARSE_INVALID
    )


def test_analysis_intersects_overlapping_caller_and_model_constraints() -> None:
    analysis = QueryAnalyzer.parse(
        json.dumps({"ask": "places", "constraints": {"country": ["France", "Germany"]}}),
        asked="places",
        caller_constraints={"country": Exact("France")},
    )
    assert analysis.constraints["country"] == Exact("France")


@pytest.mark.asyncio
async def test_cardinality_first_and_schema_first_pass_only_prior_stage_facts() -> None:
    cardinality_client = PlanningClient()
    schema_client = PlanningClient()
    cardinality_first = await context_for(PLANNING_CARDINALITY_FIRST, cardinality_client)
    schema_first = await context_for(PLANNING_SCHEMA_FIRST, schema_client)

    assert [
        "analysis"
        if "prepare a data request" in prompt
        else "cardinality"
        if "Estimate the number of records" in prompt
        else "schema"
        for prompt in cardinality_client.prompts
    ] == ["analysis", "cardinality", "schema"]
    assert [
        "analysis"
        if "prepare a data request" in prompt
        else "schema"
        if "Resolve the output schema" in prompt
        else "cardinality"
        for prompt in schema_client.prompts
    ] == ["analysis", "schema", "cardinality"]
    assert '"expected": 120' in cardinality_client.prompts[2]
    assert '"country"' in schema_client.prompts[2]
    assert cardinality_first.cardinality.expected == schema_first.cardinality.expected == 120
    assert all(stage.request_digest for stage in schema_first.stages)
    assert all(stage.raw_digest for stage in schema_first.stages)
    assert all(stage.parsed_digest for stage in schema_first.stages)


class BarrierClient(PlanningClient):
    def __init__(self) -> None:
        super().__init__()
        self.both_started = asyncio.Event()

    async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
        self.prompts.append(prompt)
        sink = properties.get("usage_sink")
        if isinstance(sink, list):
            cast("list[Usage]", sink).append(Usage(model="planning"))
        if len(self.prompts) == 2:
            self.both_started.set()
        await asyncio.wait_for(self.both_started.wait(), timeout=1)
        if "prepare a data request" in prompt:
            return analysis_response()
        return cardinality_response()


@pytest.mark.asyncio
async def test_locked_schedules_use_identical_inputs_and_equal_facts() -> None:
    declared = Schema(
        attrs=[Attr("name", "string")],
        primary_keys=("name",),
        key_from=KEY_FROM_CALLER,
    )
    sequential_client = PlanningClient()
    parallel_client = BarrierClient()
    sequential = await context_for(PLANNING_LOCKED_SEQUENTIAL, sequential_client, declared=declared)
    parallel = await context_for(PLANNING_LOCKED_PARALLEL, parallel_client, declared=declared)

    assert sequential_client.prompts == parallel_client.prompts
    assert sequential == parallel
    assert sequential.digest == parallel.digest
    assert [stage.name for stage in sequential.stages] == [
        "analysis",
        "schema",
        "cardinality",
    ]


@pytest.mark.asyncio
async def test_locked_parallel_preflight_starts_neither_sibling_when_group_cannot_fit() -> None:
    declared = Schema(
        attrs=[Attr("name", "string")],
        primary_keys=("name",),
        key_from=KEY_FROM_CALLER,
    )
    client = PlanningClient()
    context = await context_for(
        PLANNING_LOCKED_PARALLEL,
        client,
        declared=declared,
        budget=Budget(Limits(max_calls=2, max_retries=0)),
    )
    assert client.prompts == []
    assert any("preflight refused both" in item for item in context.diagnostics)
    assert not any(stage.attempted for stage in context.stages)


@pytest.mark.parametrize(
    "error",
    [BudgetExceeded("stopped", limit="max_calls"), RuntimeError("failed")],
)
@pytest.mark.asyncio
async def test_parallel_sibling_failure_cancels_and_drains_peer(error: BaseException) -> None:
    peer_started = asyncio.Event()
    receipts: list[Usage] = []
    drained: list[str] = []

    async def fail() -> str:
        await peer_started.wait()
        raise error

    async def sleep() -> str:
        receipts.append(Usage(model="started"))
        peer_started.set()
        try:
            await asyncio.sleep(10)
            receipts.append(Usage(model="late"))
            return "late"
        finally:
            drained.append("peer")

    with pytest.raises(type(error)):
        await gather_siblings(fail(), sleep())
    snapshot = list(receipts)
    await asyncio.sleep(0)

    assert drained == ["peer"]
    assert [item.model for item in receipts] == ["started"]
    assert receipts == snapshot


@pytest.mark.asyncio
async def test_external_parallel_cancellation_drains_receipts_before_return() -> None:
    class SleepingClient(PlanningClient):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.drained: list[str] = []

        async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
            self.prompts.append(prompt)
            sink = properties.get("usage_sink")
            if isinstance(sink, list):
                cast("list[Usage]", sink).append(Usage(model="started"))
            if len(self.prompts) == 2:
                self.started.set()
            try:
                await self.started.wait()
                await asyncio.sleep(10)
                if isinstance(sink, list):
                    cast("list[Usage]", sink).append(Usage(model="late"))
                return analysis_response()
            finally:
                self.drained.append(prompt)

    client = SleepingClient()
    usage_sink: list[Usage] = []
    declared = Schema(
        attrs=[Attr("name", "string")],
        primary_keys=("name",),
        key_from=KEY_FROM_CALLER,
    )
    running = asyncio.create_task(
        build_planning_context(
            original_query="places",
            caller_context="current",
            caller_constraints={},
            declared_schema=declared,
            schema_mode=SCHEMA_ENRICH,
            client=client,
            budget=Budget(Limits(max_calls=10, max_retries=0)),
            properties={"usage_sink": usage_sink},
            planning_order=PLANNING_LOCKED_PARALLEL,
            extraction_prompt="extract",
        )
    )
    await asyncio.wait_for(client.started.wait(), timeout=1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    snapshot = list(usage_sink)
    await asyncio.sleep(0)

    assert len(client.drained) == 2
    assert [item.model for item in usage_sink] == ["started", "started"]
    assert usage_sink == snapshot


@pytest.mark.asyncio
async def test_locked_modes_require_caller_schema_and_ordered_keys() -> None:
    with pytest.raises(ConfigError, match="requires a non-empty caller schema"):
        await context_for(PLANNING_LOCKED_SEQUENTIAL, PlanningClient())


@pytest.mark.asyncio
async def test_combined_projects_good_facts_when_one_member_is_bad() -> None:
    context = await context_for(PLANNING_COMBINED, PlanningClient())
    assert context.analysis.ask == "precise places"
    assert context.cardinality.expected == 120
    assert context.schema.attrs == (Attr("name", "string"),)
    assert context.stages[0].attempted is True
    assert context.stages[0].parsed is True
    assert context.stages[0].request_digest is not None
    assert context.stages[0].raw_digest is not None
    assert context.stages[0].parsed_digest is not None


@pytest.mark.asyncio
async def test_combined_partial_salvage_reaches_the_executable_leaf() -> None:
    client = PlanningClient()
    bundle = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="current",
        caller_constraints={},
        declared_schema=Schema(attrs=[Attr("name", "string")]),
        schema_mode=SCHEMA_ENRICH,
        limits=PlanLimits(max_depth=0),
        client=client,
        budget=Budget(Limits(max_calls=3, max_retries=0)),
        planning_order=PLANNING_COMBINED,
    )

    assert len(client.prompts) == 1
    assert bundle.analysis.ask == "precise places"
    assert bundle.plan.leaves[0].query == "precise places"
    assert bundle.plan.leaves[0].estimate == 120
    assert bundle.schema.attrs == (Attr("name", "string"),)
    assert any("partially usable" in item for item in bundle.diagnostics)


@pytest.mark.parametrize(
    "valid_schema,valid_cardinality",
    [(True, True), (True, False), (False, True)],
)
@pytest.mark.asyncio
async def test_combined_invalid_analysis_keeps_valid_sibling_facts(
    valid_schema: bool, valid_cardinality: bool
) -> None:
    class InvalidAnalysisClient(PlanningClient):
        async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
            self.prompts.append(prompt)
            sink = properties.get("usage_sink")
            if isinstance(sink, list):
                cast("list[Usage]", sink).append(Usage(model="planning"))
            return json.dumps(
                {
                    "ask": "",
                    "constraints": ["invalid"],
                    "row_estimate": (
                        json.loads(cardinality_response()) if valid_cardinality else "many"
                    ),
                    "schema": (
                        {
                            "attrs": [
                                {"name": "name", "type": "string"},
                                {"name": "country", "type": "string"},
                            ],
                            "key": "name",
                        }
                        if valid_schema
                        else {"attrs": "invalid", "key": "name"}
                    ),
                }
            )

    client = InvalidAnalysisClient()
    caller_constraints = {"country": Exact("France")}
    bundle = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="current",
        caller_constraints=caller_constraints,
        declared_schema=Schema(attrs=[Attr("name", "string")]),
        schema_mode=SCHEMA_ENRICH,
        limits=PlanLimits(max_depth=0),
        client=client,
        budget=Budget(Limits(max_calls=3, max_retries=0)),
        planning_order=PLANNING_COMBINED,
    )

    assert len(client.prompts) == 1
    assert bundle.analysis.ask == "places"
    assert bundle.analysis.constraints == caller_constraints
    assert bundle.plan.leaves[0].query == "places"
    assert bundle.plan.leaves[0].constraints == caller_constraints
    assert bundle.plan.leaves[0].estimate == (120 if valid_cardinality else None)
    expected_attrs = (
        (Attr("name", "string"), Attr("country", "string"))
        if valid_schema
        else (Attr("name", "string"),)
    )
    assert bundle.schema.attrs == expected_attrs
    assert bundle.context is not None
    assert bundle.context.stages[0].parsed is True


@pytest.mark.asyncio
async def test_combined_all_invalid_falls_back_without_another_call() -> None:
    class InvalidCombinedClient(PlanningClient):
        async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
            self.prompts.append(prompt)
            return "not json"

    client = InvalidCombinedClient()
    bundle = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="current",
        caller_constraints={},
        declared_schema=Schema(attrs=[Attr("name", "string")]),
        schema_mode=SCHEMA_ENRICH,
        limits=PlanLimits(max_depth=2),
        client=client,
        budget=Budget(Limits(max_calls=3, max_retries=0)),
        planning_order=PLANNING_COMBINED,
    )

    assert len(client.prompts) == 1
    assert bundle.analysis.ask == "places"
    assert bundle.plan.leaves[0].query == "places"
    assert bundle.plan.accepted_internal_nodes == 0
    assert any("bootstrap response was unusable" in item for item in bundle.diagnostics)


@pytest.mark.asyncio
async def test_estimate_exposure_changes_only_the_leaf_prompt() -> None:
    bundle = await coordinate_plan(
        mode=MODE_PARTITIONED,
        original_query="places",
        caller_context="current",
        caller_constraints={},
        declared_schema=Schema(attrs=[Attr("name", "string")]),
        schema_mode=SCHEMA_ENRICH,
        limits=PlanLimits(max_depth=0),
        client=PlanningClient(),
        budget=Budget(Limits(max_calls=10, max_retries=0)),
        planning_order=PLANNING_SCHEMA_FIRST,
    )
    assert bundle.facts.analysis_calls == bundle.facts.schema_calls == 1
    assert bundle.facts.cardinality_calls == 1
    assert bundle.facts.combined_calls == 0
    assert bundle.context is not None

    class FetchClient:
        def __init__(self) -> None:
            self.prompt = ""

        async def acomplete(self, prompt: str, _properties: dict[str, Any]) -> str:
            self.prompt = prompt
            return '[{"name": "Paris"}]'

    prompts: list[str] = []
    for expose in (False, True):
        client = FetchClient()
        settings = execution_settings(
            {"plan.expose_estimate": expose}, PlanLimits(), Budget(), exact_rows=None
        )
        task = LeafTask.from_leaf(
            bundle.plan.leaves[0],
            caller_context="current",
            schema=bundle.schema,
            settings=settings,
        )
        assert task.estimate.expected == 120
        await run_local_leaf(task, client=client, properties={}, usage_sink=[])
        prompts.append(client.prompt)
    assert "Estimated rows for this leaf:" not in prompts[0]
    assert '"expected": 120' in prompts[1]


@pytest.mark.asyncio
async def test_planning_prices_the_estimate_bearing_leaf_prompt() -> None:
    long_values = ("A" * 900, "B" * 900)

    class ByteBoundClient(PlanningClient):
        def bound_for_attempt(self, prompt: str, _properties: dict[str, Any]) -> float:
            divisor = 1_000_000 if "Your task is to answer a query" in prompt else 1_000_000_000
            return len(prompt.encode("utf-8")) / divisor

        async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
            if "Propose one useful partition" in prompt:
                self.prompts.append(prompt)
                return json.dumps(
                    {
                        "key": "region",
                        "values": list(long_values),
                        "ambiguity": 0,
                        "child_estimates": [60, 60],
                    }
                )
            return await super().acomplete(prompt, properties)

    async def run(max_cost: float):
        client = ByteBoundClient()
        bundle = await coordinate_plan(
            mode=MODE_PARTITIONED,
            original_query="places",
            caller_context="current",
            caller_constraints={},
            declared_schema=Schema(attrs=[Attr("name", "string")]),
            schema_mode=SCHEMA_ENRICH,
            limits=PlanLimits(max_depth=1, max_partitions=2),
            client=client,
            budget=Budget(Limits(max_cost=max_cost, max_calls=10, max_retries=0)),
            planning_order=PLANNING_SCHEMA_FIRST,
        )
        return bundle, client

    probe, _ = await run(1.0)
    child = next(leaf for leaf in probe.plan.leaves if long_values[0] in str(leaf.constraints))
    assert child.cardinality is not None
    exposed_prompt = prompts.build_leaf_fetch_prompt(
        original_query=child.original_query,
        retrieval_query=child.query,
        attrs=probe.schema.attrs,
        caller_context="current",
        constraints=child.constraints,
        primary_keys=probe.schema.primary_keys,
        estimate=child.cardinality.to_dict(),
    )
    hidden_prompt = prompts.build_leaf_fetch_prompt(
        original_query=child.original_query,
        retrieval_query=child.query,
        attrs=probe.schema.attrs,
        caller_context="current",
        constraints=child.constraints,
        primary_keys=probe.schema.primary_keys,
        estimate=None,
    )
    exposed_cost = len(exposed_prompt.encode("utf-8")) / 1_000_000
    hidden_cost = len(hidden_prompt.encode("utf-8")) / 1_000_000
    threshold = (hidden_cost + exposed_cost) / 2
    assert 0 < hidden_cost < threshold < exposed_cost

    rejected, client = await run(threshold)

    assert rejected.plan.accepted_internal_nodes == 0
    assert rejected.context is not None
    assert any("expansion rejected after repricing" in gap for gap in rejected.diagnostics)
    assert any("Propose one useful partition" in prompt for prompt in client.prompts)


def test_source_defaults_nonflat_planning_to_schema_first() -> None:
    sent: list[str] = []

    def model(prompt: str) -> str:
        sent.append(prompt)
        if "prepare a data request" in prompt:
            return analysis_response()
        if "Resolve the output schema" in prompt:
            return schema_response()
        if "Estimate the number of records" in prompt:
            return cardinality_response()
        return '[{"name": "Paris"}]'

    result = LlmSource().fetch(
        "places",
        attrs=["name"],
        llm_fn=model,
        properties={"plan.mode": "partitioned", "plan.max_depth": 0},
    )
    assert result.records == [{"name": "Paris"}]
    assert "prepare a data request" in sent[0]
    assert "Resolve the output schema" in sent[1]
    assert "Estimate the number of records" in sent[2]


@pytest.mark.asyncio
async def test_unusable_independent_stage_is_salvaged_without_hidden_calls() -> None:
    class MalformedAnalysisClient(PlanningClient):
        async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
            if "prepare a data request" in prompt:
                self.prompts.append(prompt)
                return "not json"
            return await super().acomplete(prompt, properties)

    client = MalformedAnalysisClient()
    context = await context_for(PLANNING_SCHEMA_FIRST, client)

    assert len(client.prompts) == 3
    assert [stage.attempted for stage in context.stages] == [True, True, True]
    assert [stage.parsed for stage in context.stages] == [False, True, True]
    assert context.analysis.ask == "places"
    assert context.schema.primary_keys == ("name",)
    assert context.cardinality.expected == 120
