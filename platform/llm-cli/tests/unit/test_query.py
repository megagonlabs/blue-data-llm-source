"""`data query`: what each option does to the call, and what the result looks like."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import pytest
from blue_data_llm_source import BudgetExceeded, ExecMeta, FetchResult, Outcome
from click.testing import CliRunner
from conftest import MESSY_RECORDS

from blue_data_llm_cli import commands, config
from blue_data_llm_cli.commands import cli

QUERY = "the member states of the European Union"


def payload(stdout: str) -> dict[str, Any]:
    return json.loads(stdout)


def sent(client: Any) -> dict[str, Any]:
    return client.properties[0]


class RecordingSource:
    """Captures the arguments a command hands the library."""

    def __init__(self, meta: ExecMeta | None = None) -> None:
        self.properties: dict[str, Any] = {}
        self.arguments: dict[str, Any] = {}
        self.meta = meta or ExecMeta()

    def fetch(self, *args: Any, **kwargs: Any) -> FetchResult:
        self.properties = dict(kwargs.get("properties") or {})
        self.arguments = dict(kwargs)
        return FetchResult(records=[{"name": "Ada"}], meta=self.meta)


# ------------------------------------------------------------- the records --
def test_query_prints_a_table_and_a_footer(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--attr", "country"])
    assert result.exit_code == 0, result.output
    assert "France" in result.stdout
    assert "Germany" in result.stdout
    assert "2 records" in result.stdout
    assert "1 call" in result.stdout


def test_attr_names_select_the_columns(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--attr", "country"])
    assert "country" in result.stdout
    assert "accession_year" not in result.stdout


def test_attr_types_coerce_values(runner: CliRunner) -> None:
    result = runner.invoke(
        cli,
        [
            "data",
            "query",
            QUERY,
            "--attr",
            "country",
            "--attr",
            "accession_year:int",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert payload(result.stdout)["records"][0]["accession_year"] == 1958


def test_an_attr_without_a_name_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--attr", ":int"])
    assert result.exit_code == config.EXIT_USAGE
    assert "no attribute name" in result.stderr


def test_context_reaches_the_model(runner: CliRunner, default_client: Any) -> None:
    runner.invoke(cli, ["data", "query", QUERY, "--context", "founding members only"])
    assert "founding members only" in default_client.prompts[0]


def test_an_empty_result_says_so(runner: CliRunner, install_client: Any) -> None:
    install_client("[]")
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert result.exit_code == 0, result.output
    assert "(no records)" in result.stdout
    assert "0 records" in result.stdout


def test_values_that_are_not_strings_still_render(runner: CliRunner, install_client: Any) -> None:
    install_client(json.dumps([{"country": "France", "cities": ["Paris"], "founding": True}]))
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert result.exit_code == 0, result.output
    assert "Paris" in result.stdout
    assert "True" in result.stdout


# ------------------------------------------------------------- the options --
def test_the_adapter_options_reach_the_call(runner: CliRunner, default_client: Any) -> None:
    result = runner.invoke(
        cli,
        [
            "data",
            "query",
            QUERY,
            "--model",
            "gpt-4o",
            "--max-tokens",
            "128",
            "--temperature",
            "0.5",
            "--timeout",
            "30",
            "--max-parallel",
            "3",
            "--cache",
            "--cache-dir",
            "/tmp/somewhere",
            "--price-in",
            "0.000001",
            "--price-out",
            "0.000002",
        ],
    )
    assert result.exit_code == 0, result.output
    assert sent(default_client) | {
        "llm.model": "gpt-4o",
        "llm.max_tokens": 128,
        "llm.temperature": 0.5,
        "llm.timeout": 30.0,
        "llm.max_parallel": 3,
        "llm.cache": True,
        "llm.cache_dir": "/tmp/somewhere",
        "llm.price_per_input_token": 0.000001,
        "llm.price_per_output_token": 0.000002,
    } == sent(default_client)


def test_no_cache_is_passed_as_a_refusal_rather_than_left_out(
    runner: CliRunner, default_client: Any
) -> None:
    runner.invoke(cli, ["data", "query", QUERY, "--no-cache"])
    assert sent(default_client)["llm.cache"] is False


def test_the_ceilings_reach_the_call(runner: CliRunner, default_client: Any) -> None:
    result = runner.invoke(
        cli,
        [
            "data",
            "query",
            QUERY,
            "--limit-cost",
            "0.50",
            "--limit-seconds",
            "30",
            "--limit-calls",
            "2",
            "--max-retries",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert sent(default_client)["limits.max_cost"] == 0.5
    assert sent(default_client)["limits.max_seconds"] == 30.0
    assert sent(default_client)["limits.max_calls"] == 2
    assert sent(default_client)["limits.max_retries"] == 1


def test_max_records_is_the_row_ceiling(runner: CliRunner, install_source: Any) -> None:
    """One flag for this knob: `fetch` assigns it to limits.max_rows itself."""
    source = install_source(RecordingSource())
    result = runner.invoke(cli, ["data", "query", QUERY, "--max-records", "5"])
    assert result.exit_code == 0, result.output
    assert source.arguments["max_records"] == 5
    assert "limits.max_rows" not in source.properties


def test_max_records_caps_the_result(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--max-records", "1", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert len(payload(result.stdout)["records"]) == 1


@pytest.mark.parametrize("value", ["0", "-1", "notanumber"])
def test_max_records_refuses_a_ceiling_below_one(value: str, runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--max-records", value])
    assert result.exit_code == config.EXIT_USAGE, value
    assert "--max-records" in result.stderr


@pytest.mark.parametrize("value", ["0", "-1", "notanumber"])
def test_max_tokens_refuses_an_allowance_a_provider_could_not_use(
    value: str, runner: CliRunner
) -> None:
    """Only the pricing path checks the allowance, and it does not run without a ceiling."""
    result = runner.invoke(cli, ["data", "query", QUERY, "--max-tokens", value])
    assert result.exit_code == config.EXIT_USAGE, value
    assert "--max-tokens" in result.stderr


def test_the_variant_reaches_the_source(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    original = config.build_source

    def watching(variant: str | None = None) -> Any:
        seen.append(variant)
        return original(variant)

    monkeypatch.setattr(config, "build_source", watching)
    result = runner.invoke(cli, ["data", "query", QUERY, "--variant", "openai"])
    assert result.exit_code == 0, result.output
    assert seen == ["openai"]


def test_the_plan_options_reach_the_call(runner: CliRunner, install_source: Any) -> None:
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
            "--plan-leaves",
            "8",
            "--plan-key",
            "name",
            "--plan-keys",
            "name",
            "--plan-exact-rows",
            "10",
            "--plan-exclusion-bytes",
            "4096",
            "--plan-leaf-prompt",
            "keyed-leaf-fetch-2",
            "--plan-child-wording",
            "canonical",
            "--plan-filter-enforcement",
            "drop",
            "--plan-range-filters",
            "--plan-partition-guardrails",
            "--plan-split-policy",
            "evidence",
            "--plan-split-threshold-rows",
            "200",
        ],
    )
    assert result.exit_code == 0, result.output
    assert source.properties == {
        "plan.mode": "partitioned",
        "plan.max_depth": 3,
        "plan.max_partitions": 6,
        "plan.target_rows_per_leaf": 25,
        "plan.schema_mode": "enrich",
        "plan.partition_key": "continent",
        "plan.planning_order": "planning.schema_first",
        "plan.expose_estimate": False,
        "plan.max_leaves": 8,
        "plan.key": "name",
        "plan.keys": ["name"],
        "plan.exact_rows": 10,
        "plan.max_exclusion_bytes": 4096,
        "plan.leaf_prompt": "keyed-leaf-fetch-2",
        "plan.child_wording": "canonical",
        "plan.filter_enforcement": "drop",
        "plan.range_filters": True,
        "plan.partition_guardrails": True,
        "plan.split_policy": "evidence",
        "plan.split_threshold_rows": 200,
        "llm.model": "gpt-6-luna",
        "llm.cache_dir": source.properties["llm.cache_dir"],
    }


# -------------------------------------------------------------- the effort --
def test_medium_effort_allows_one_continuation_with_a_primary_key(
    runner: CliRunner, default_client: Any
) -> None:
    result = runner.invoke(
        cli,
        [
            "data",
            "query",
            QUERY,
            "--attr",
            "country",
            "--plan-key",
            "country",
            "--effort",
            "medium",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert payload(result.stdout)["meta"]["calls"] == 2
    assert len(default_client.prompts) == 2


def test_explicit_low_effort_stays_single_pass(runner: CliRunner, install_source: Any) -> None:
    source = install_source(RecordingSource())
    runner.invoke(cli, ["data", "query", QUERY, "--effort", "low"])
    assert source.properties["plan.max_continuations"] == 0


@pytest.mark.parametrize("effort", ["medium", "high"])
def test_effort_that_needs_a_key_fails_before_the_source_is_built(
    effort: str, runner: CliRunner
) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--effort", effort])
    assert result.exit_code == config.EXIT_USAGE
    assert "--effort" in result.stderr
    assert "--plan-key" in result.stderr


def test_non_flat_effort_can_rely_on_planning_to_infer_a_key(
    runner: CliRunner, install_source: Any
) -> None:
    source = install_source(RecordingSource())
    result = runner.invoke(
        cli, ["data", "query", QUERY, "--effort", "high", "--plan-mode", "partitioned"]
    )
    assert result.exit_code == 0, result.output
    assert source.properties["plan.max_continuations"] == 2


def test_an_explicit_continuation_count_overrides_the_effort(
    runner: CliRunner, install_source: Any
) -> None:
    source = install_source(RecordingSource())
    runner.invoke(cli, ["data", "query", QUERY, "--effort", "low", "--plan-continuations", "2"])
    assert source.properties["plan.max_continuations"] == 2


def test_the_effort_default_comes_from_the_profile(runner: CliRunner, install_source: Any) -> None:
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_SOURCE_EFFORT", "high"])
    source = install_source(RecordingSource())
    result = runner.invoke(
        cli, ["data", "query", QUERY, "--plan-mode", "partitioned", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    assert source.properties["plan.max_continuations"] == 2
    settings = payload(result.stdout)["settings"]
    assert settings["source"]["plan.max_continuations"] == "profile:default"


def test_an_effort_the_cli_does_not_have_is_a_profile_error(runner: CliRunner) -> None:
    refused = runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_SOURCE_EFFORT", "maximum"])
    assert refused.exit_code == config.EXIT_PROFILE
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert result.exit_code == 0


def test_plan_key_must_match_a_requested_attr(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--attr", "name", "--plan-key", "country"])
    assert result.exit_code == config.EXIT_USAGE
    assert "--plan-key" in result.stderr
    assert "--attr" in result.stderr


# ------------------------------------------------------------ the envelope --
def test_json_output_carries_records_meta_and_settings(runner: CliRunner) -> None:
    """This replaces the shipped two-key contract; the MCP result shape is unchanged."""
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])
    assert result.exit_code == 0, result.output
    body = payload(result.stdout)
    assert set(body) == {"records", "meta", "settings"}
    assert body["meta"]["calls"] == 1
    assert body["meta"]["prompt_tokens"] == 12
    assert body["meta"]["completion_tokens"] == 34
    assert body["meta"]["cost"] == 0.0012
    assert "elapsed_seconds" in body["meta"]


def test_the_settings_envelope_has_a_fixed_two_map_schema(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--limit-cost", "0.5", "--format", "json"])
    settings = payload(result.stdout)["settings"]
    assert set(settings) == {"applied", "source"}
    assert set(settings["applied"]) == set(settings["source"])
    assert settings["applied"]["limits.max_cost"] == 0.5
    assert settings["source"]["limits.max_cost"] == "flag"


def test_a_property_this_run_did_not_set_is_in_neither_map(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])
    settings = payload(result.stdout)["settings"]
    assert "limits.max_cost" not in settings["applied"]
    assert "limits.max_cost" not in settings["source"]


def test_the_settings_keys_are_the_librarys_own_dotted_names(runner: CliRunner) -> None:
    result = runner.invoke(
        cli, ["data", "query", QUERY, "--model", "gpt-4o", "--limit-calls", "2", "--format", "json"]
    )
    applied = payload(result.stdout)["settings"]["applied"]
    assert "llm.model" in applied
    assert "limits.max_calls" in applied
    assert "plan.max_continuations" in applied


def test_the_table_prints_the_same_pairs_as_a_settings_footer(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--limit-cost", "0.5"])
    assert "settings:" in result.stdout
    assert "limits.max_cost=0.5 (flag)" in result.stdout


def test_the_prose_names_only_the_ceilings(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--limit-cost", "0.5", "--format", "nl"])
    assert "Ceilings applied: max_cost 0.5." in result.stdout
    assert "llm.model" not in result.stdout


# ------------------------------------------------------------- the formats --
def test_table_and_json_agree_on_the_records(runner: CliRunner) -> None:
    table = runner.invoke(cli, ["data", "query", QUERY, "--attr", "country"])
    as_json = runner.invoke(cli, ["data", "query", QUERY, "--attr", "country", "--format", "json"])
    names = [record["country"] for record in payload(as_json.stdout)["records"]]
    assert names == ["France", "Germany"]
    for name in names:
        assert name in table.stdout


def test_csv_carries_the_same_columns_as_the_table(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--attr", "country", "--format", "csv"])
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows == [["country"], ["France"], ["Germany"]]


def test_the_prose_answer_names_the_count_model_cost_and_stop(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "nl"])
    assert "2 records" in result.stdout
    assert "gpt-6-luna" in result.stdout
    assert "$0.0012" in result.stdout
    assert "Nothing stopped the run early." in result.stdout


# ------------------------------------------------------------ the outcomes --
def test_an_initial_budget_stop_returns_no_records_and_names_the_limit(
    runner: CliRunner, install_client: Any
) -> None:
    install_client(BudgetExceeded("call limit reached (2)", limit="max_calls"))
    result = runner.invoke(cli, ["data", "query", QUERY, "--limit-calls", "2", "--verbose"])
    assert result.exit_code == config.EXIT_BUDGET, result.output
    assert "0 records" in result.stdout
    assert "stopped by: max_calls" in result.stdout


def test_a_partly_answered_run_exits_on_its_own_code(
    runner: CliRunner, install_source: Any
) -> None:
    """Not 7, which names a budget ceiling, and not 0, which would look complete."""
    install_source(
        RecordingSource(ExecMeta(outcome=Outcome.PARTIAL, gaps=["leaf_failed: leaf 0.1 raised"]))
    )
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert result.exit_code == config.EXIT_PARTIAL, result.output


def test_csv_keeps_non_ok_diagnostics_off_the_data_stream(
    runner: CliRunner, install_source: Any
) -> None:
    install_source(
        RecordingSource(
            ExecMeta(
                outcome=Outcome.STOPPED_BY_LIMIT,
                stopped_by="max_cost",
                gaps=["cost limit reached"],
            )
        )
    )
    result = runner.invoke(cli, ["data", "query", QUERY, "--attr", "name", "--format", "csv"])
    assert result.exit_code == config.EXIT_BUDGET
    assert result.stdout_bytes == b"name\r\nAda\r\n"
    assert "outcome: stopped_by_limit" in result.stderr
    assert "gap: cost limit reached" in result.stderr
    assert "stopped by: max_cost" in result.stderr


def test_a_row_cap_is_not_a_failure(runner: CliRunner, install_client: Any) -> None:
    """The one ceiling that leaves a usable result, so it stays exit 0."""
    install_client(MESSY_RECORDS)
    result = runner.invoke(cli, ["data", "query", QUERY, "--max-records", "1", "--verbose"])
    assert result.exit_code == 0, result.output
    assert "stopped by: max_rows" in result.stdout


def test_an_empty_query_is_an_empty_answer_rather_than_an_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", "", "--verbose"])
    assert result.exit_code == config.EXIT_OK, result.output
    assert "0 records" in result.stdout
    assert "empty_query" in result.stdout


def test_the_default_table_names_the_outcome_without_being_asked(
    runner: CliRunner, install_client: Any
) -> None:
    """An empty table said `(no records)` and `0 records` and nothing else.

    The library separates `empty_query`, `model_returned_none` and `filtered_to_empty`
    precisely so a reader can tell them apart, and under the default format the distinction
    was reachable only through `--verbose` or `--format json`.
    """
    install_client("[]")
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert result.exit_code == 0, result.output
    assert "outcome: model_returned_none" in result.stdout


def test_an_empty_query_names_its_outcome_under_the_default_format(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", ""])
    assert result.exit_code == config.EXIT_OK, result.output
    assert "outcome: empty_query" in result.stdout


def test_an_ok_result_gains_no_outcome_line(runner: CliRunner, default_client: Any) -> None:
    """`ok` is the absence of a reason, so it says nothing the row count has not said.

    Printing it on every successful query would put a line under output a reader already
    understands, which is how the stop reasons and the gaps behave: present when there is
    something to report.
    """
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert result.exit_code == 0, result.output
    assert "outcome:" not in result.stdout


def test_the_outcome_is_named_once_under_verbose(runner: CliRunner, install_client: Any) -> None:
    """The ungated line owns it; `--verbose` must not repeat it."""
    install_client("[]")
    result = runner.invoke(cli, ["data", "query", QUERY, "--verbose"])
    assert result.stdout.count("outcome: model_returned_none") == 1


def test_a_row_cap_names_the_outcome_and_the_axis(runner: CliRunner, install_client: Any) -> None:
    install_client(MESSY_RECORDS)
    result = runner.invoke(cli, ["data", "query", QUERY, "--max-records", "1"])
    assert result.exit_code == 0, result.output
    assert "outcome: truncated" in result.stdout
    assert "stopped by: max_rows" in result.stdout


def test_the_nl_format_states_the_outcome_in_words(runner: CliRunner, install_client: Any) -> None:
    install_client("[]")
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "nl"])
    assert result.exit_code == 0, result.output
    assert "The model returned no records." in result.stdout


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_the_machine_formats_gain_no_outcome_line(
    runner: CliRunner, install_client: Any, fmt: str
) -> None:
    """A note is a table footer. Neither machine format may grow one.

    `json` already carries `meta.outcome` as a field, and a stray line in either would break
    the one promise both formats make: that stdout parses.
    """
    install_client("[]")
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", fmt])
    assert result.exit_code == 0, result.output
    assert "outcome: model_returned_none" not in result.stdout
    if fmt == "json":
        assert json.loads(result.stdout)["meta"]["outcome"] == "model_returned_none"
    else:
        assert result.stdout.strip() == ""


# ------------------------------------------------------------- the verbose --
def test_verbose_reports_alignment_and_stop_facts(runner: CliRunner, install_client: Any) -> None:
    install_client(MESSY_RECORDS)
    result = runner.invoke(
        cli, ["data", "query", QUERY, "--attr", "country", "--max-records", "1", "--verbose"]
    )
    assert result.exit_code == 0, result.output
    assert "unmapped keys: capital" in result.stdout
    assert "remapped keys: Country -> country" in result.stdout
    assert "stopped by: max_rows" in result.stdout
    assert "truncated to: 1" in result.stdout


def test_without_verbose_the_stop_facts_still_show(runner: CliRunner, install_client: Any) -> None:
    install_client(MESSY_RECORDS)
    result = runner.invoke(cli, ["data", "query", QUERY, "--attr", "country", "--max-records", "1"])
    assert "stopped by: max_rows" in result.stdout
    assert "unmapped keys" not in result.stdout


def test_verbose_json_adds_the_alignment_diagnostics(
    runner: CliRunner, install_client: Any
) -> None:
    install_client(MESSY_RECORDS)
    result = runner.invoke(
        cli,
        [
            "data",
            "query",
            QUERY,
            "--attr",
            "country",
            "--max-records",
            "1",
            "--verbose",
            "--format",
            "json",
        ],
    )
    meta = payload(result.stdout)["meta"]
    assert meta["unmapped_keys"] == ["capital"]
    assert meta["remapped_keys"] == {"Country": "country"}
    assert meta["stopped_by"] == "max_rows"
    assert meta["truncated_to"] == 1


def test_the_cli_now_publishes_truncated_to_without_verbose(
    runner: CliRunner, install_client: Any
) -> None:
    """Gained from the shared serializer. The MCP server has always emitted it."""
    install_client(MESSY_RECORDS)
    result = runner.invoke(
        cli, ["data", "query", QUERY, "--attr", "country", "--max-records", "1", "--format", "json"]
    )
    assert payload(result.stdout)["meta"]["truncated_to"] == 1


# -------------------------------------------------------------- the guards --
def test_the_withdrawn_rate_option_is_refused_with_an_alternative(runner: CliRunner) -> None:
    """Declared so the refusal can be useful, not because the option works."""
    for value in ("10", "fast", ""):
        result = runner.invoke(cli, ["data", "query", QUERY, "--rate-limit", value])
        assert result.exit_code == config.EXIT_USAGE, value
        assert "ERROR: USAGE" in result.stderr, value
        assert "--rate-limit" in result.stderr, value
        assert "does not do it" in result.stderr, value
        assert "--max-parallel" in result.stderr, value
        # Not a version: a refusal naming the release that will carry a feature sends a
        # caller who upgrades to the release they are already running.
        assert "v2.0" not in result.stderr, value


def test_provider_stdout_is_redirected_away_from_the_result(
    runner: CliRunner, install_source: Any
) -> None:
    class Noisy:
        def fetch(self, *args: Any, **kwargs: Any) -> FetchResult:
            print("provider banner")
            return FetchResult(records=[{"name": "Ada"}], meta=ExecMeta())

    install_source(Noisy())
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])
    assert result.exit_code == 0, result.output
    assert payload(result.stdout)["records"] == [{"name": "Ada"}]
    assert "provider banner" not in result.stderr


def test_model_text_cannot_drive_the_terminal(runner: CliRunner, install_client: Any) -> None:
    """A record value is data, and the terminal must not execute it."""
    install_client(json.dumps([{"country": "France\x1b[2K\x1b[1A gone"}]))
    for fmt in ("table", "json", "nl", "csv"):
        result = runner.invoke(cli, ["data", "query", QUERY, "--format", fmt])
        assert result.exit_code == 0, result.output
        assert "\x1b" not in result.stdout, fmt


def test_a_callable_result_marks_its_accounting_unknown(runner: CliRunner) -> None:
    """The zero this prints must not read as a measured zero."""
    unknown = ExecMeta(calls=1, cost=0.0, accounting_known=False)
    measured = ExecMeta(calls=1, cost=0.25, accounting_known=True)
    assert "(unknown)" in commands._footer(1, unknown)  # pyright: ignore[reportPrivateUsage]
    assert "(unknown)" not in commands._footer(1, measured)  # pyright: ignore[reportPrivateUsage]
