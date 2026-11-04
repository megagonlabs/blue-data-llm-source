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


def test_the_prose_limits_the_figure_to_a_flat_fetch(
    runner: CliRunner, install_source: Any
) -> None:
    install_source(RecordingSource())
    result = runner.invoke(cli, ["data", "estimate", QUERY, "--format", "nl"])
    assert (
        "One flat fetch of this request is bounded at $0.1200 against the configured rates "
        "for gpt-4o, across 3 attempt(s)." in result.stdout
    )
    assert "does not bound their total cost" in result.stdout


def test_the_help_limits_the_figure_to_a_flat_fetch(runner: CliRunner) -> None:
    text = runner.invoke(cli, ["data", "estimate", "--help"]).stdout
    assert "does not bound their total" in text
    assert "--limit-calls" in text
    assert "--max-retries" in text


def test_an_unbounded_estimate_gives_the_reason(runner: CliRunner) -> None:
    """An unpriced attempt produces an unbounded estimate with a reason."""
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


# ------------------------------------------------------ fit against a ceiling --
class FittingSource(RecordingSource):
    """Reports the fit the library computes, for the ceiling the CLI passed down."""

    def __init__(self, fits: int | None) -> None:
        super().__init__()
        self.fits = fits

    def estimate(self, *args: Any, **kwargs: Any) -> Estimate:
        base = super().estimate(*args, **kwargs)
        base.attempts_within_ceiling = self.fits
        return base


def test_a_ceiling_that_fits_every_attempt_says_so(runner: CliRunner, install_source: Any) -> None:
    source = install_source(FittingSource(3))
    body = json.loads(
        runner.invoke(
            cli, ["data", "estimate", QUERY, "--limit-cost", "100", "--format", "json"]
        ).stdout
    )
    assert source.properties["limits.max_cost"] == 100
    assert body["attempts_within_ceiling"] == 3
    prose = runner.invoke(
        cli, ["data", "estimate", QUERY, "--limit-cost", "100", "--format", "nl"]
    ).stdout
    assert (
        "One flat fetch of this request is bounded at $0.1200 against the configured rates "
        "for gpt-4o, across 3 attempt(s)." in prose
    )
    assert "At this ceiling 3 of 3 attempts fit." in " ".join(prose.split())
    assert "Ceilings applied: max_cost 100." in " ".join(prose.split())


def test_a_ceiling_below_one_attempt_names_the_figure_and_the_remedy(
    runner: CliRunner, install_source: Any
) -> None:
    install_source(FittingSource(0))
    result = runner.invoke(
        cli, ["data", "estimate", QUERY, "--limit-cost", "0.01", "--format", "nl"]
    )
    assert result.exit_code == 0, result.output
    prose = " ".join(result.stdout.split())
    assert (
        "The first attempt needs $0.04, above the $0.01 ceiling; lower --max-tokens or "
        "raise the ceiling." in prose
    )
    table = runner.invoke(cli, ["data", "estimate", QUERY, "--limit-cost", "0.01"]).stdout
    assert "The first attempt needs $0.04" in " ".join(table.split())


def test_no_ceiling_reports_no_fit(runner: CliRunner, install_source: Any) -> None:
    install_source(FittingSource(None))
    result = runner.invoke(cli, ["data", "estimate", QUERY, "--format", "nl"])
    body = json.loads(runner.invoke(cli, ["data", "estimate", QUERY, "--format", "json"]).stdout)
    assert body["attempts_within_ceiling"] is None
    assert "At this ceiling" not in result.stdout


def test_estimate_has_no_verbose_flag(runner: CliRunner) -> None:
    """It reads no response, so there is nothing for it to show verbosely."""
    result = runner.invoke(cli, ["data", "estimate", QUERY, "--verbose"])
    assert result.exit_code == config.EXIT_USAGE


def test_a_query_the_ceiling_refuses_outright_says_what_to_change(runner: CliRunner) -> None:
    result = runner.invoke(
        cli,
        [
            "data",
            "query",
            QUERY,
            "--model",
            "gpt-4o-mini",
            "--max-tokens",
            "512",
            "--limit-cost",
            "0.0000001",
            "--format",
            "table",
        ],
    )
    assert result.exit_code == config.EXIT_BUDGET, result.output
    text = " ".join(result.stdout.split())
    assert "one attempt needs up to $" in text
    assert "stopped by: max_cost. No call fit under the cost ceiling" in text
    assert "Lower --max-tokens" in text


@pytest.mark.parametrize(
    ("bound", "ceiling", "expected"),
    [
        (0.000041, 0.0, ("0.000041", "0")),
        (0.04, 0.01, ("0.04", "0.01")),
        (0.0100001, 0.01, ("0.0100001", "0.01")),
        (12.5, 3.0, ("12.5", "3")),
    ],
)
def test_refused_amounts_are_written_so_they_differ(
    bound: float, ceiling: float, expected: tuple[str, str]
) -> None:
    from blue_data_llm_cli.commands import _distinct_amounts  # pyright: ignore[reportPrivateUsage]

    assert _distinct_amounts(bound, ceiling) == expected


@pytest.mark.parametrize("retries", ["10000", str(10**309)])
@pytest.mark.parametrize("command", ["estimate", "query"])
def test_a_retry_allowance_past_the_cap_is_refused(
    runner: CliRunner, command: str, retries: str
) -> None:
    result = runner.invoke(cli, ["data", command, QUERY, "--max-retries", retries])
    assert result.exit_code == config.EXIT_CONFIG, result.output
    assert "at most 9999" in result.output
    assert "OverflowError" not in result.output


def test_the_largest_retry_allowance_is_accepted(runner: CliRunner, install_source: Any) -> None:
    source = install_source(RecordingSource())
    result = runner.invoke(cli, ["data", "estimate", QUERY, "--max-retries", "9999"])
    assert result.exit_code == 0, result.output
    assert source.properties["limits.max_retries"] == 9999
