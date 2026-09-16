"""Initial-v2 planning and result facts at the command-line boundary."""

from __future__ import annotations

import json
from typing import Any

import pytest
from blue_data_llm_source import ExecMeta, FetchResult, Outcome
from click.testing import CliRunner

from blue_data_llm_cli import config
from blue_data_llm_cli.commands import cli


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
            "fetch",
            "people",
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
    assert source.properties == {
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

    preserve = runner.invoke(
        cli,
        ["fetch", "people", "--plan-mode", "partitioned", "--plan-schema-mode", "preserve"],
    )
    assert preserve.exit_code == 0, preserve.output
    assert source.properties["plan.schema_mode"] == "preserve"


@pytest.mark.parametrize(
    ("effort", "continuations"),
    [("low", 0), ("medium", 1), ("high", 2)],
)
def test_effort_sets_the_continuation_allowance(
    runner: CliRunner,
    install_source: Any,
    effort: str,
    continuations: int,
) -> None:
    source = install_source(RecordingSource())

    result = runner.invoke(
        cli,
        [
            "fetch",
            "people",
            "--attr",
            "name",
            "--plan-key",
            "name",
            "--effort",
            effort,
        ],
    )

    assert result.exit_code == 0, result.output
    assert source.properties["plan.max_continuations"] == continuations


def test_explicit_low_effort_remains_keyless_and_single_pass(
    runner: CliRunner,
    install_source: Any,
) -> None:
    source = install_source(RecordingSource())
    result = runner.invoke(cli, ["fetch", "people", "--effort", "low"])

    assert result.exit_code == 0, result.output
    assert source.properties["plan.max_continuations"] == 0


@pytest.mark.parametrize("effort", ["medium", "high"])
@pytest.mark.parametrize("mode_args", [[], ["--plan-mode", "flat"]])
def test_effort_that_needs_a_key_fails_before_source_construction(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    effort: str,
    mode_args: list[str],
) -> None:
    def fail_if_called() -> None:
        raise AssertionError("source construction must not run")

    monkeypatch.setattr(config, "build_source", fail_if_called)
    result = runner.invoke(cli, ["fetch", "people", "--effort", effort, *mode_args])

    assert result.exit_code == config.EXIT_USAGE
    assert "Invalid value for --effort" in result.stderr
    assert "needs --plan-key or --plan-keys in flat mode" in result.stderr


def test_nonflat_effort_can_rely_on_planning_to_infer_a_key(
    runner: CliRunner,
    install_source: Any,
) -> None:
    source = install_source(RecordingSource())
    result = runner.invoke(
        cli,
        ["fetch", "people", "--effort", "medium", "--plan-mode", "partitioned"],
    )

    assert result.exit_code == 0, result.output
    assert source.properties["plan.max_continuations"] == 1


def test_effort_accepts_declared_composite_keys(
    runner: CliRunner,
    install_source: Any,
) -> None:
    source = install_source(RecordingSource())
    result = runner.invoke(
        cli,
        [
            "fetch",
            "people",
            "--attr",
            "first",
            "--attr",
            "last",
            "--plan-keys",
            "first",
            "--plan-keys",
            "last",
            "--effort",
            "medium",
        ],
    )

    assert result.exit_code == 0, result.output
    assert source.properties["plan.keys"] == ["first", "last"]
    assert source.properties["plan.max_continuations"] == 1


def test_exact_continuation_option_overrides_effort(
    runner: CliRunner,
    install_source: Any,
) -> None:
    source = install_source(RecordingSource())

    result = runner.invoke(
        cli,
        ["fetch", "people", "--effort", "high", "--plan-continuations", "4"],
    )

    assert result.exit_code == 0, result.output
    assert source.properties["plan.max_continuations"] == 4


def test_provider_stdout_is_redirected_away_from_json(
    runner: CliRunner,
    install_source: Any,
) -> None:
    class NoisySource:
        def fetch(self, *args: Any, **kwargs: Any) -> FetchResult:
            print("provider banner")
            return FetchResult(records=[{"name": "Ada"}], meta=ExecMeta())

    install_source(NoisySource())
    result = runner.invoke(cli, ["fetch", "people", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["records"] == [{"name": "Ada"}]
    assert "provider banner" not in result.stdout
    assert "provider banner" in result.stderr


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
    result = runner.invoke(cli, ["fetch", "people", "--format", "json"])

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

    result = runner.invoke(cli, ["fetch", "people"])

    assert result.exit_code == 0, result.output
    assert "stopped by: max_rows" in result.stdout
    assert "stopped by: provider_output_limit" in result.stdout


@pytest.mark.parametrize(("estimate", "shown"), [(120, "120"), (0, "0"), (None, "unknown")])
def test_row_estimate_is_unconditional_and_human_readable(
    runner: CliRunner, install_source: Any, estimate: int | None, shown: str
) -> None:
    install_source(RecordingSource(ExecMeta(row_estimate=estimate)))

    machine = runner.invoke(cli, ["fetch", "people", "--format", "json"])
    human = runner.invoke(cli, ["fetch", "people"])

    assert machine.exit_code == 0, machine.output
    assert json.loads(machine.stdout)["meta"]["row_estimate"] == estimate
    assert human.exit_code == 0, human.output
    assert f"row estimate {shown}" in human.stdout
