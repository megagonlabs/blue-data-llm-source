"""Non-flat planning and result facts at the command-line boundary.

Carried across the CLI replacement unchanged in substance: the command is now
`data query` rather than `fetch`, and the facts it has to keep publishing are the same.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from blue_data_llm_source import ExecMeta, FetchResult, Outcome
from click.testing import CliRunner

from blue_data_llm_cli.commands import cli

QUERY = "people"


class RecordingSource:
    def __init__(self, meta: ExecMeta | None = None) -> None:
        self.properties: dict[str, Any] = {}
        self.meta = meta or ExecMeta()

    def fetch(self, *args: Any, **kwargs: Any) -> FetchResult:
        self.properties = dict(kwargs.get("properties") or {})
        return FetchResult(records=[{"name": "Ada"}], meta=self.meta)


def test_hierarchy_and_schema_flags_reach_the_library(
    runner: CliRunner, install_source: Any
) -> None:
    source = install_source(RecordingSource())
    result = runner.invoke(
        cli,
        [
            "data",
            "query",
            QUERY,
            "--plan-mode",
            "partitioned",
            "--plan-depth",
            "3",
            "--plan-partitions",
            "6",
            "--plan-target-rows",
            "25",
            "--plan-schema-mode",
            "enrich",
            "--plan-partition-key",
            "continent",
            "--plan-planning-order",
            "planning.schema_first",
            "--no-plan-expose-estimate",
        ],
    )

    assert result.exit_code == 0, result.output
    plan = {key: value for key, value in source.properties.items() if key.startswith("plan.")}
    assert plan == {
        "plan.max_continuations": 0,
        "plan.mode": "partitioned",
        "plan.max_depth": 3,
        "plan.max_partitions": 6,
        "plan.target_rows_per_leaf": 25,
        "plan.schema_mode": "enrich",
        "plan.partition_key": "continent",
        "plan.planning_order": "planning.schema_first",
        "plan.expose_estimate": False,
    }


@pytest.mark.parametrize(
    ("outcome", "records", "exit_code"),
    [
        (Outcome.PARTIAL, [{"name": "Ada"}], 8),
        (Outcome.MODEL_RETURNED_NONE, [], 0),
        (Outcome.TRUNCATED, [{"name": "Ada"}], 0),
    ],
)
def test_machine_output_preserves_result_state_and_simultaneous_stop_facts(
    runner: CliRunner,
    install_source: Any,
    outcome: str,
    records: list[dict[str, str]],
    exit_code: int,
) -> None:
    meta = ExecMeta(
        outcome=outcome,
        gaps=["leaf_failed: leaf 0.1 raised"] if outcome == Outcome.PARTIAL else [],
        stop_reasons=["max_calls", "provider_output_limit"],
        stopped_by="max_calls",
        requested_attrs=["name"],
    )

    class ResultSource:
        def fetch(self, *args: Any, **kwargs: Any) -> FetchResult:
            return FetchResult(records=records, meta=meta)

    install_source(ResultSource())
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])

    assert result.exit_code == exit_code, result.output
    payload = json.loads(result.stdout)
    assert payload["meta"]["outcome"] == outcome
    assert payload["meta"]["gaps"] == meta.gaps
    assert payload["meta"]["stop_reasons"] == ["max_calls", "provider_output_limit"]
    assert payload["meta"]["stopped_by"] == "max_calls"
    assert payload["meta"]["requested_attrs"] == ["name"]
    assert payload["meta"]["row_estimate"] is None


def test_human_output_shows_each_stop_fact_without_verbose(
    runner: CliRunner, install_source: Any
) -> None:
    install_source(
        RecordingSource(
            ExecMeta(
                outcome=Outcome.TRUNCATED,
                stop_reasons=["max_rows", "provider_output_limit"],
                stopped_by="max_rows",
            )
        )
    )

    result = runner.invoke(cli, ["data", "query", QUERY])

    assert result.exit_code == 0, result.output
    assert "stopped by: max_rows" in result.stdout
    assert "stopped by: provider_output_limit" in result.stdout


@pytest.mark.parametrize(("estimate", "shown"), [(120, "120"), (0, "0"), (None, "unknown")])
def test_row_estimate_is_unconditional_and_human_readable(
    runner: CliRunner, install_source: Any, estimate: int | None, shown: str
) -> None:
    install_source(RecordingSource(ExecMeta(row_estimate=estimate)))

    machine = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])
    human = runner.invoke(cli, ["data", "query", QUERY])

    assert machine.exit_code == 0, machine.output
    assert json.loads(machine.stdout)["meta"]["row_estimate"] == estimate
    assert human.exit_code == 0, human.output
    assert f"row estimate {shown}" in human.stdout
