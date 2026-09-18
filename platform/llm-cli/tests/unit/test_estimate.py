"""`data estimate`: what it accepts, what it refuses, and what the number means."""

from __future__ import annotations

import json
from typing import Any

import pytest
from blue_data_llm_source import Estimate
from click.testing import CliRunner

from blue_data_llm_cli import config
from blue_data_llm_cli.commands import cli

QUERY = "the member states of the European Union"


class RecordingSource:
    def __init__(self) -> None:
        self.properties: dict[str, Any] = {}

    def estimate(self, *args: Any, **kwargs: Any) -> Estimate:
        self.properties = dict(kwargs.get("properties") or {})
        return Estimate(
            bounded=True,
            max_cost=0.12,
            max_cost_per_attempt=0.04,
            max_attempts=3,
            reason="",
            model="gpt-4o",
            prompt_bytes=753,
            max_output_tokens=4096,
        )


def test_estimate_publishes_every_field_of_the_bound(
    runner: CliRunner, install_source: Any
) -> None:
    install_source(RecordingSource())
    result = runner.invoke(cli, ["data", "estimate", QUERY, "--format", "json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["bounded"] is True
    assert body["max_cost"] == 0.12
    assert body["max_cost_per_attempt"] == 0.04
    assert body["max_attempts"] == 3
    assert body["model"] == "gpt-4o"
    assert body["prompt_bytes"] == 753
    assert body["max_output_tokens"] == 4096


def test_estimate_reports_its_settings_alongside_the_bound(
    runner: CliRunner, install_source: Any
) -> None:
    install_source(RecordingSource())
    result = runner.invoke(
        cli, ["data", "estimate", QUERY, "--limit-calls", "2", "--format", "json"]
    )
    settings = json.loads(result.stdout)["settings"]
    assert settings["applied"]["limits.max_calls"] == 2
    assert settings["source"]["limits.max_calls"] == "flag"


def test_the_table_carries_the_settings_footer(runner: CliRunner, install_source: Any) -> None:
    install_source(RecordingSource())
    result = runner.invoke(cli, ["data", "estimate", QUERY, "--limit-cost", "0.5"])
    assert "settings:" in result.stdout
    assert "limits.max_cost=0.5 (flag)" in result.stdout


def test_the_prose_says_the_figure_is_per_call(runner: CliRunner, install_source: Any) -> None:
    """Both sentences exist because both are ways to read the number wrongly."""
    install_source(RecordingSource())
    result = runner.invoke(cli, ["data", "estimate", QUERY, "--format", "nl"])
    assert "$0.1200" in result.stdout
    assert "once per call it makes" in result.stdout


def test_the_help_warns_about_both_misreadings(runner: CliRunner) -> None:
    text = runner.invoke(cli, ["data", "estimate", "--help"]).stdout
    assert "once per call it makes" in text
    assert "--limit-calls" in text
    assert "--max-retries" in text


def test_an_unbounded_estimate_gives_the_reason(runner: CliRunner) -> None:
    """The default fake client cannot price an attempt, which is the honest unbounded case."""
    result = runner.invoke(cli, ["data", "estimate", QUERY, "--format", "json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["bounded"] is False
    assert body["reason"]


# -------------------------------------------------------- what it accepts --
@pytest.mark.parametrize(
    "option",
    [
        ["--limit-cost", "0.5"],
        ["--limit-seconds", "30"],
        ["--limit-calls", "2"],
        ["--max-retries", "1"],
        ["--model", "gpt-4o"],
        ["--max-tokens", "128"],
        ["--temperature", "0.2"],
        ["--timeout", "30"],
        ["--price-in", "0.000001"],
        ["--price-out", "0.000002"],
        ["--variant", "openai"],
        ["--attr", "country"],
        ["--context", "founding members only"],
        ["--verbose"],
        ["--log"],
    ],
    ids=lambda o: o[0],
)
def test_estimate_accepts_every_option_that_moves_the_number(
    option: list[str], runner: CliRunner, install_source: Any
) -> None:
    """`estimate` resolves the full Limits and derives its attempt count from them."""
    install_source(RecordingSource())
    result = runner.invoke(cli, ["data", "estimate", QUERY, *option])
    assert result.exit_code == 0, result.output


def test_the_call_and_retry_allowances_reach_the_estimate(
    runner: CliRunner, install_source: Any
) -> None:
    source = install_source(RecordingSource())
    runner.invoke(cli, ["data", "estimate", QUERY, "--limit-calls", "2", "--max-retries", "1"])
    assert source.properties["limits.max_calls"] == 2
    assert source.properties["limits.max_retries"] == 1


# --------------------------------------------------------- what it refuses --
@pytest.mark.parametrize(
    "option",
    [
        ["--max-records", "5"],
        ["--effort", "high"],
        ["--plan-mode", "partitioned"],
        ["--plan-continuations", "2"],
        ["--plan-key", "country"],
        ["--plan-depth", "3"],
    ],
    ids=lambda o: o[0],
)
def test_estimate_refuses_a_setting_it_would_have_to_ignore(
    option: list[str], runner: CliRunner
) -> None:
    """Stricter than the library, which would accept and ignore these.

    A setting accepted and ignored is exactly what the library's own closed key set exists
    to prevent, so the refusal says where the flag does work.
    """
    result = runner.invoke(cli, ["data", "estimate", QUERY, *option])
    assert result.exit_code == config.EXIT_USAGE
    assert "ERROR: USAGE" in result.stderr
    assert option[0] in result.stderr
    assert "data query" in result.stderr


def test_an_ordinary_unknown_option_is_still_refused_without_the_pointer(
    runner: CliRunner,
) -> None:
    result = runner.invoke(cli, ["data", "estimate", QUERY, "--nope"])
    assert result.exit_code == config.EXIT_USAGE
    assert "data query" not in result.stderr


def test_an_option_on_both_commands_means_the_same_thing(runner: CliRunner) -> None:
    """An option that appears on both has the same name, type, default and meaning."""
    from blue_data_llm_cli.commands import data_estimate, data_query

    shared = {p.name for p in data_query.params} & {p.name for p in data_estimate.params}
    on_query = {p.name: p for p in data_query.params}
    on_estimate = {p.name: p for p in data_estimate.params}
    for name in shared - {"query"}:
        left, right = on_query[name], on_estimate[name]
        assert left.opts == right.opts, name
        assert left.default == right.default, name
        assert type(left.type) is type(right.type), name
