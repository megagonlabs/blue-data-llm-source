"""Local execution for versioned leaf tasks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from blue_data_llm_source import _constants as C
from blue_data_llm_source.execution.mapper import Answer, LeafRun, arun_leaf
from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.plan import Leaf
from blue_data_llm_source.planning.transport import (
    ErrorData,
    LeafTask,
    LeafTaskResult,
    LocalLeafExecutor,
)
from blue_data_llm_source.types import Usage


def execution_settings(
    properties: Mapping[str, Any],
    limits: PlanLimits,
    budget: Budget,
    *,
    exact_rows: int | None = None,
) -> dict[str, Any]:
    """Return the task settings that may cross the executor boundary."""
    return {
        "model": properties.get("llm.model"),
        "temperature": properties.get("llm.temperature"),
        "max_output_tokens": properties.get("llm.max_tokens"),
        "max_retries": budget.effective_retries(),
        "max_continuations": limits.max_continuations,
        "max_excluded_keys": limits.max_excluded_keys,
        "max_exclusion_bytes": limits.max_exclusion_bytes,
        "empty_leaf_retry": limits.empty_leaf_retry,
        "exact_rows": exact_rows,
        "expose_estimate": properties.get("plan.expose_estimate", True),
    }


def _task_limits(settings: Mapping[str, Any]) -> PlanLimits:
    return PlanLimits(
        max_continuations=cast("int", settings.get("max_continuations", 0)),
        max_excluded_keys=cast("int", settings.get("max_excluded_keys", 50)),
        max_exclusion_bytes=cast("int", settings.get("max_exclusion_bytes", 16_384)),
        empty_leaf_retry=cast("bool", settings.get("empty_leaf_retry", False)),
    )


async def run_local_leaf(
    task: LeafTask,
    *,
    client: object,
    properties: Mapping[str, Any],
    usage_sink: list[Usage],
) -> LeafRun:
    """Execute one task while keeping live objects on the local side of the wire."""
    run: LeafRun | None = None
    props = dict(properties)
    props[C.USAGE_SINK_PROPERTY] = usage_sink

    async def handle(received: LeafTask) -> LeafTaskResult:
        nonlocal run
        leaf = Leaf(
            id=received.task_id,
            parent_id=received.parent_id,
            query=received.retrieval_query,
            constraints=dict(received.filters),
            original_query=received.original_query,
            estimate=received.estimate.expected,
            cardinality=received.estimate,
        )

        async def ask(prompt: str) -> Answer:
            before = len(usage_sink)
            method = getattr(client, "acomplete")
            text = await method(prompt, props)
            written = usage_sink[before:]
            reason = written[-1].finish_reason if written else None
            return Answer(text, finish_reason=reason)

        run = await arun_leaf(
            leaf,
            ask,
            attrs=list(received.schema.attrs),
            limits=_task_limits(received.settings),
            primary_keys=received.primary_keys,
            estimate=received.estimate,
            exact_rows=cast("int | None", received.settings.get("exact_rows")),
            context=received.caller_context,
            prompt_version=received.prompt_version,
            expose_estimate=cast("bool", received.settings.get("expose_estimate", True)),
        )
        error = None
        if run.error is not None:
            code = getattr(run.exception, "code", None)
            error = ErrorData(
                kind=type(run.exception).__name__ if run.exception is not None else "error",
                message=run.error,
                code=code if isinstance(code, str) else None,
            )
        return LeafTaskResult(
            task_id=received.task_id,
            records=run.records,
            usages=list(usage_sink),
            outcome=run.outcome,
            error=error,
        )

    await LocalLeafExecutor(handle).execute(task)
    if run is None:  # pragma: no cover - the executor always awaits its handler
        raise RuntimeError("local leaf handler returned no run")
    return run
