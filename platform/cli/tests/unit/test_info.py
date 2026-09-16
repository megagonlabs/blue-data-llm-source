"""``llm info`` and the help surface."""

from __future__ import annotations

from blue_data_llm_source import DEFAULT_VARIANT, PROTOCOL
from blue_data_llm_source import __version__ as library_version
from click.testing import CliRunner

from blue_data_llm_cli.commands import cli


def test_info_prints_protocol_variant_and_version(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0, result.output
    assert f"protocol  {PROTOCOL}" in result.stdout
    assert f"variant   {DEFAULT_VARIANT}" in result.stdout
    assert f"version   {library_version}" in result.stdout


def test_info_takes_no_arguments(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["info", "extra"])
    assert result.exit_code == 2


def test_help_lists_both_commands(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0, result.output
    assert "fetch" in result.stdout
    assert "info" in result.stdout
    assert "--verbose" in result.stdout
    assert "-v" in result.stdout


def test_fetch_help_lists_every_option(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["fetch", "--help"])
    assert result.exit_code == 0, result.output
    for option in (
        "--attr",
        "--context",
        "--max-records",
        "--model",
        "--effort",
        "--limit-cost",
        "--limit-seconds",
        "--limit-calls",
        "--format",
    ):
        assert option in result.stdout
