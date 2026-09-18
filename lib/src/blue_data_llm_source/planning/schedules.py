"""Planning fact schedules and their model-stage execution."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import replace
from typing import Any, TypeVar, cast

from blue_data_llm_source import _constants as C
from blue_data_llm_source import prompts
from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.planning.analysis import (
    DataTask,
    QueryAnalysisResult,
    query_analysis_from_payload,
)
from blue_data_llm_source.planning.cardinality import (
    PARSE_INVALID,
    CardinalityEstimate,
    CardinalityEstimator,
    estimate_from_reported,
)
from blue_data_llm_source.planning.context import (
    PLANNING_CARDINALITY_FIRST,
    PLANNING_COMBINED,
    PLANNING_LOCKED_PARALLEL,
    PLANNING_LOCKED_SEQUENTIAL,
    PlanningContext,
    PlanningStage,
    validate_planning_order,
    value_digest,
)
from blue_data_llm_source.planning.schema import (
    SCHEMA_MODES,
    Schema,
    SchemaDefinition,
    SchemaGenerator,
    schema_definition_from_payload,
)
from blue_data_llm_source.reduction.parsing import strip_fence
from blue_data_llm_source.types import (
    BudgetExceeded,
    ConfigError,
    Constraint,
    OneOf,
    Usage,
)

_MAX_RESPONSE_CHARS = 65_536
_T = TypeVar("_T")
_U = TypeVar("_U")


def schema_payload(schema: Schema) -> dict[str, object]:
    return {
        "attrs": [{"name": attr.name, "type": attr.type} for attr in schema.attrs],
        "primary_keys": list(schema.primary_keys),
        "key_from": schema.key_from,
    }


def constraint_payload(constraints: Mapping[str, Constraint]) -> dict[str, object]:
    return {
        name: (
            {"kind": "one_of", "values": list(value.values)}
            if isinstance(value, OneOf)
            else {"kind": "exact", "value": value.value}
        )
        for name, value in constraints.items()
    }


def parse_json_object(text: str) -> dict[str, Any] | None:
    if len(text) > _MAX_RESPONSE_CHARS:
        return None
    try:
        value: object = json.loads(strip_fence(text))
    except (ValueError, RecursionError):
        return None
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _bound(client: object, prompt: str, properties: dict[str, Any], budget: Budget) -> float | None:
    if budget.remaining_cost() is None:
        return 0.0
    method = getattr(client, "bound_for_attempt", None)
    if not callable(method):
        return None
    try:
        return Budget.checked_amount("the attempt bound", method(prompt, properties))
    except Exception:
        return None


def planning_lookahead(
    *,
    client: object,
    budget: Budget,
    properties: dict[str, Any],
    planning_prompt: str,
    fallback_prompt: str,
) -> bool:
    remaining_seconds = budget.remaining_seconds()
    if remaining_seconds is not None and remaining_seconds <= 0:
        return False
    attempts = budget.effective_retries() + 1
    remaining_calls = budget.remaining_calls()
    if remaining_calls is not None and remaining_calls < attempts + 1:
        return False
    planning_cost = _bound(client, planning_prompt, properties, budget)
    extraction_cost = _bound(client, fallback_prompt, properties, budget)
    if planning_cost is None or extraction_cost is None:
        return False
    remaining_cost = budget.remaining_cost()
    return remaining_cost is None or remaining_cost >= attempts * planning_cost + extraction_cost


def extraction_fits(
    client: object, budget: Budget, properties: dict[str, Any], prompt: str
) -> bool:
    remaining_seconds = budget.remaining_seconds()
    if remaining_seconds is not None and remaining_seconds <= 0:
        return False
    remaining_calls = budget.remaining_calls()
    if remaining_calls is not None and remaining_calls < 1:
        return False
    cost = _bound(client, prompt, properties, budget)
    remaining_cost = budget.remaining_cost()
    return cost is not None and (remaining_cost is None or remaining_cost >= cost)


async def call_planning_model(
    client: object,
    prompt: str,
    properties: dict[str, Any],
    usages: list[Usage],
) -> tuple[str | None, bool]:
    method = getattr(client, "acomplete", None)
    if not callable(method):
        raise ConfigError("the planning client must provide acomplete(prompt, properties)")
    complete = cast("Callable[[str, dict[str, Any]], Awaitable[object]]", method)
    before = len(usages)
    try:
        text = await complete(prompt, properties)
    except BudgetExceeded:
        raise
    except ConfigError:
        raise
    except Exception:
        return None, False
    written = usages[before:]
    truncated = bool(written and written[-1].finish_reason == "length")
    return (text if isinstance(text, str) else None), truncated


def planning_usages(sink: list[Usage], start: int) -> list[Usage]:
    """Return this coordinator's receipts from a possibly shared caller sink."""
    return list(sink[start:])


def _request_digest(prompt: str) -> str:
    return value_digest({"prompt": prompt})


async def _call_stage(
    client: object,
    prompt: str,
    properties: dict[str, Any],
    usages: list[Usage],
) -> tuple[str | None, bool, int]:
    """Call one stage with an isolated receipt sink, then fold it into the run sink."""
    local: list[Usage] = []
    stage_properties = {**properties, C.USAGE_SINK_PROPERTY: local}
    try:
        text, truncated = await call_planning_model(client, prompt, stage_properties, local)
    finally:
        usages.extend(local)
    return text, truncated, len(local)


def _parallel_preflight(
    *,
    client: object,
    budget: Budget,
    properties: dict[str, Any],
    prompts_to_run: tuple[str, str],
    extraction_prompt: str,
) -> bool:
    """Check aggregate headroom before either locked sibling starts.

    The client's normal reservations remain authoritative. Another consumer sharing this
    budget may spend after this check and before a sibling reserves its attempt.
    """
    remaining_seconds = budget.remaining_seconds()
    if remaining_seconds is not None and remaining_seconds <= 0:
        return False
    retries = budget.effective_retries() + 1
    remaining_calls = budget.remaining_calls()
    if remaining_calls is not None and remaining_calls < retries * 2 + 1:
        return False
    if budget.remaining_cost() is None:
        return True
    stage_bounds = [_bound(client, prompt, properties, budget) for prompt in prompts_to_run]
    extraction_bound = _bound(client, extraction_prompt, properties, budget)
    if extraction_bound is None or any(bound is None for bound in stage_bounds):
        return False
    required = retries * sum(cast("float", bound) for bound in stage_bounds) + extraction_bound
    remaining_cost = budget.remaining_cost()
    return remaining_cost is not None and remaining_cost >= required


async def gather_siblings(
    first: Coroutine[Any, Any, _T],
    second: Coroutine[Any, Any, _U],
) -> tuple[_T, _U]:
    """Run two siblings and drain both before any exceptional return."""
    first_task = asyncio.create_task(first)
    second_task = asyncio.create_task(second)
    tasks = (first_task, second_task)
    try:
        first_result, second_result = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return first_result, second_result


def fallback_analysis(
    original_query: str,
    caller_constraints: Mapping[str, Constraint],
    diagnostic: str | None = None,
) -> QueryAnalysisResult:
    return QueryAnalysisResult(
        ask=original_query,
        constraints=caller_constraints,
        task=DataTask(
            id="0",
            parent_id=None,
            original_query=original_query,
            query=original_query,
            constraints=caller_constraints,
        ),
        diagnostics=(diagnostic,) if diagnostic else (),
    )


def _context_with_facts(
    *,
    schedule: str,
    analysis: QueryAnalysisResult,
    schema: SchemaDefinition,
    cardinality: CardinalityEstimate,
    stages: list[PlanningStage],
    diagnostics: list[str],
) -> PlanningContext:
    task = analysis.task
    if task is None:  # pragma: no cover - QueryAnalysisResult always creates one
        task = DataTask(
            id="0",
            parent_id=None,
            original_query=analysis.ask,
            query=analysis.ask,
            constraints=analysis.constraints,
        )
    task = replace(task, schema=schema, cardinality=cardinality)
    accepted = replace(
        analysis,
        row_estimate=cardinality.expected,
        cardinality=cardinality,
        task=task,
    )
    return PlanningContext(
        analysis=accepted,
        schema=schema,
        cardinality=cardinality,
        stages=tuple(stages),
        diagnostics=tuple(dict.fromkeys(diagnostics)),
        schedule=schedule,
    )


async def build_planning_context(
    *,
    original_query: str,
    caller_context: str,
    caller_constraints: Mapping[str, Constraint],
    declared_schema: Schema,
    schema_mode: str,
    client: object,
    budget: Budget,
    properties: Mapping[str, Any] | None = None,
    planning_order: str = PLANNING_COMBINED,
    extraction_prompt: str,
) -> tuple[PlanningContext, list[Usage]]:
    """Build independently salvageable planning facts in one supported order."""
    schedule = validate_planning_order(planning_order)
    if schema_mode not in SCHEMA_MODES:
        raise ConfigError(f"unknown schema mode {schema_mode!r}")
    if schedule in {PLANNING_LOCKED_SEQUENTIAL, PLANNING_LOCKED_PARALLEL} and (
        not declared_schema.attrs or not declared_schema.primary_keys
    ):
        raise ConfigError(f"{schedule} requires a non-empty caller schema and ordered plan.keys")

    props = dict(properties or {})
    props[C.BUDGET_PROPERTY] = budget
    supplied_sink = props.get(C.USAGE_SINK_PROPERTY)
    usages = cast("list[Usage]", supplied_sink) if isinstance(supplied_sink, list) else []
    usage_start = len(usages)
    diagnostics: list[str] = []
    stages: list[PlanningStage] = []
    fallback = fallback_analysis(original_query, caller_constraints)
    fallback_schema = SchemaDefinition.from_schema(declared_schema)
    row_unit = "records matching the accepted request and schema"

    if schedule == PLANNING_COMBINED:
        prompt = prompts.build_bootstrap_prompt(
            original_query,
            caller_context=caller_context,
            declared_attrs=declared_schema.attrs,
            declared_key=declared_schema.key,
            declared_keys=declared_schema.primary_keys,
            schema_mode=schema_mode,
        )
        if not planning_lookahead(
            client=client,
            budget=budget,
            properties=props,
            planning_prompt=prompt,
            fallback_prompt=extraction_prompt,
        ):
            diagnostics.append("bootstrap skipped to preserve one extraction attempt")
            stages.append(
                PlanningStage(
                    name="bootstrap",
                    attempted=False,
                    parsed=False,
                    request_digest=_request_digest(prompt),
                )
            )
            context = _context_with_facts(
                schedule=schedule,
                analysis=fallback,
                schema=fallback_schema,
                cardinality=CardinalityEstimate(row_unit=row_unit, source="combined_bootstrap"),
                stages=stages,
                diagnostics=diagnostics,
            )
            return context, planning_usages(usages, usage_start)

        text, truncated, provider_attempts = await _call_stage(client, prompt, props, usages)
        payload = parse_json_object(text) if text is not None and not truncated else None
        if payload is not None and set(payload) != {"ask", "constraints", "row_estimate", "schema"}:
            payload = None
        analysis_candidate = (
            query_analysis_from_payload(
                {"ask": payload.get("ask"), "constraints": payload.get("constraints")},
                asked=original_query,
                original_query=original_query,
                caller_constraints=caller_constraints,
            )
            if payload is not None
            else fallback_analysis(
                original_query,
                caller_constraints,
                "bootstrap analysis was unusable; using the original request",
            )
        )
        analysis_parsed = payload is not None and not analysis_candidate.diagnostics
        analysis = (
            analysis_candidate
            if analysis_parsed
            else replace(fallback, diagnostics=analysis_candidate.diagnostics)
        )
        parsed_schema = (
            schema_definition_from_payload(payload.get("schema"), declared_schema, mode=schema_mode)
            if payload is not None
            else None
        )
        schema = parsed_schema or fallback_schema
        cardinality = (
            estimate_from_reported(
                payload.get("row_estimate"), row_unit=row_unit, source="combined_bootstrap"
            )
            if payload is not None
            else CardinalityEstimate(
                row_unit=row_unit,
                source="combined_bootstrap",
                parse_status=PARSE_INVALID,
                diagnostics=("bootstrap cardinality was unavailable",),
            )
        )
        schema_parsed = parsed_schema is not None
        cardinality_parsed = cardinality.parse_status != PARSE_INVALID
        parsed = any((analysis_parsed, schema_parsed, cardinality_parsed))
        if not parsed:
            diagnostics.append("bootstrap response was unusable; using caller facts")
        elif not all((analysis_parsed, schema_parsed, cardinality_parsed)):
            diagnostics.append("bootstrap response was partially usable; salvaged valid facts")
        diagnostics.extend(analysis.diagnostics)
        if parsed_schema is None:
            diagnostics.append("bootstrap schema was unusable; using the caller schema")
        if cardinality.parse_status == PARSE_INVALID:
            diagnostics.extend(cardinality.diagnostics)
        stages.append(
            PlanningStage(
                name="bootstrap",
                attempted=True,
                parsed=parsed,
                request_digest=_request_digest(prompt),
                raw_digest=value_digest({"raw": text}) if text is not None else None,
                parsed_digest=value_digest(payload) if payload is not None else None,
                provider_attempts=provider_attempts,
            )
        )
        context = _context_with_facts(
            schedule=schedule,
            analysis=analysis,
            schema=schema,
            cardinality=cardinality,
            stages=stages,
            diagnostics=diagnostics,
        )
        return context, planning_usages(usages, usage_start)

    analysis_prompt = prompts.build_analyse_prompt(original_query, caller_context)
    locked = schedule in {PLANNING_LOCKED_SEQUENTIAL, PLANNING_LOCKED_PARALLEL}
    locked_cardinality_prompt = prompts.build_cardinality_prompt(
        original_query=original_query,
        retrieval_query=original_query,
        caller_context=caller_context,
        constraints=constraint_payload(caller_constraints),
        schema=schema_payload(declared_schema),
    )

    async def run_analysis(
        *, preflighted: bool = False
    ) -> tuple[QueryAnalysisResult, PlanningStage]:
        if not preflighted and not planning_lookahead(
            client=client,
            budget=budget,
            properties=props,
            planning_prompt=analysis_prompt,
            fallback_prompt=extraction_prompt,
        ):
            diagnostics.append("analysis skipped to preserve one extraction attempt")
            return fallback, PlanningStage(
                name="analysis",
                attempted=False,
                parsed=False,
                request_digest=_request_digest(analysis_prompt),
            )
        text, truncated, provider_attempts = await _call_stage(
            client, analysis_prompt, props, usages
        )
        payload = parse_json_object(text) if text is not None and not truncated else None
        result = query_analysis_from_payload(
            payload,
            asked=original_query,
            original_query=original_query,
            caller_constraints=caller_constraints,
        )
        diagnostics.extend(result.diagnostics)
        parsed = payload is not None and not result.diagnostics
        return result, PlanningStage(
            name="analysis",
            attempted=True,
            parsed=parsed,
            request_digest=_request_digest(analysis_prompt),
            raw_digest=value_digest({"raw": text}) if text is not None else None,
            parsed_digest=value_digest(result.to_dict()),
            provider_attempts=provider_attempts,
        )

    async def run_cardinality(
        prompt: str,
        *,
        preflighted: bool = False,
    ) -> tuple[CardinalityEstimate, PlanningStage]:
        if not preflighted and not planning_lookahead(
            client=client,
            budget=budget,
            properties=props,
            planning_prompt=prompt,
            fallback_prompt=extraction_prompt,
        ):
            diagnostics.append("cardinality skipped to preserve one extraction attempt")
            estimate = CardinalityEstimate(row_unit=row_unit, source="cardinality_stage")
            return estimate, PlanningStage(
                name="cardinality",
                attempted=False,
                parsed=False,
                request_digest=_request_digest(prompt),
            )
        text, truncated, provider_attempts = await _call_stage(client, prompt, props, usages)
        estimate = CardinalityEstimator.parse(
            None if truncated else text,
            row_unit=row_unit,
            source="cardinality_stage",
        )
        if estimate.parse_status == PARSE_INVALID:
            diagnostics.extend(estimate.diagnostics)
        return estimate, PlanningStage(
            name="cardinality",
            attempted=True,
            parsed=estimate.parse_status != PARSE_INVALID,
            request_digest=_request_digest(prompt),
            raw_digest=value_digest({"raw": text}) if text is not None else None,
            parsed_digest=value_digest(estimate.to_dict()),
            provider_attempts=provider_attempts,
        )

    if locked:
        if schedule == PLANNING_LOCKED_PARALLEL:
            if not _parallel_preflight(
                client=client,
                budget=budget,
                properties=props,
                prompts_to_run=(analysis_prompt, locked_cardinality_prompt),
                extraction_prompt=extraction_prompt,
            ):
                diagnostics.append(
                    "locked parallel preflight refused both planning calls to preserve extraction"
                )
                analysis = fallback
                cardinality = CardinalityEstimate(row_unit=row_unit, source="cardinality_stage")
                stages.extend(
                    [
                        PlanningStage(
                            name="analysis",
                            attempted=False,
                            parsed=False,
                            request_digest=_request_digest(analysis_prompt),
                        ),
                        PlanningStage(
                            name="cardinality",
                            attempted=False,
                            parsed=False,
                            request_digest=_request_digest(locked_cardinality_prompt),
                        ),
                    ]
                )
            else:
                analysis_result, cardinality_result = await gather_siblings(
                    run_analysis(preflighted=True),
                    run_cardinality(locked_cardinality_prompt, preflighted=True),
                )
                analysis, analysis_stage = analysis_result
                cardinality, cardinality_stage = cardinality_result
                stages.extend([analysis_stage, cardinality_stage])
        else:
            analysis, analysis_stage = await run_analysis()
            cardinality, cardinality_stage = await run_cardinality(locked_cardinality_prompt)
            stages.extend([analysis_stage, cardinality_stage])
        schema = fallback_schema
        stages.insert(
            1,
            PlanningStage(
                name="schema",
                attempted=False,
                parsed=False,
                parsed_digest=value_digest(schema.to_dict()),
            ),
        )
    else:
        analysis, analysis_stage = await run_analysis()
        stages.append(analysis_stage)

        async def run_schema(
            cardinality_input: CardinalityEstimate | None,
        ) -> tuple[SchemaDefinition, PlanningStage]:
            prompt = prompts.build_schema_prompt(
                original_query=original_query,
                retrieval_query=analysis.ask,
                caller_context=caller_context,
                constraints=constraint_payload(analysis.constraints),
                declared_schema=schema_payload(declared_schema),
                schema_mode=schema_mode,
                cardinality=(
                    cardinality_input.to_dict() if cardinality_input is not None else None
                ),
            )
            if not planning_lookahead(
                client=client,
                budget=budget,
                properties=props,
                planning_prompt=prompt,
                fallback_prompt=extraction_prompt,
            ):
                diagnostics.append("schema skipped to preserve one extraction attempt")
                return fallback_schema, PlanningStage(
                    name="schema",
                    attempted=False,
                    parsed=False,
                    request_digest=_request_digest(prompt),
                )
            text, truncated, provider_attempts = await _call_stage(client, prompt, props, usages)
            parsed_schema = SchemaGenerator.parse(
                None if truncated else text,
                declared_schema,
                mode=schema_mode,
            )
            if parsed_schema is None:
                diagnostics.append("schema response was unusable; using the caller schema")
            schema = parsed_schema or fallback_schema
            return schema, PlanningStage(
                name="schema",
                attempted=True,
                parsed=parsed_schema is not None,
                request_digest=_request_digest(prompt),
                raw_digest=value_digest({"raw": text}) if text is not None else None,
                parsed_digest=value_digest(schema.to_dict()),
                provider_attempts=provider_attempts,
            )

        if schedule == PLANNING_CARDINALITY_FIRST:
            cardinality_prompt = prompts.build_cardinality_prompt(
                original_query=original_query,
                retrieval_query=analysis.ask,
                caller_context=caller_context,
                constraints=constraint_payload(analysis.constraints),
                schema=schema_payload(declared_schema),
            )
            cardinality, cardinality_stage = await run_cardinality(cardinality_prompt)
            stages.append(cardinality_stage)
            schema, schema_stage = await run_schema(cardinality)
            stages.append(schema_stage)
        else:
            schema, schema_stage = await run_schema(None)
            stages.append(schema_stage)
            cardinality_prompt = prompts.build_cardinality_prompt(
                original_query=original_query,
                retrieval_query=analysis.ask,
                caller_context=caller_context,
                constraints=constraint_payload(analysis.constraints),
                schema=schema.to_dict(),
            )
            cardinality, cardinality_stage = await run_cardinality(cardinality_prompt)
            stages.append(cardinality_stage)

    context = _context_with_facts(
        schedule=schedule,
        analysis=analysis,
        schema=schema,
        cardinality=cardinality,
        stages=stages,
        diagnostics=diagnostics,
    )
    return context, planning_usages(usages, usage_start)
