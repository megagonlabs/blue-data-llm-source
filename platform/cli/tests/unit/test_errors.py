"""Errors reach the terminal as one line and an exit code, never as a traceback."""

from __future__ import annotations

from typing import Any

import pytest
from blue_data_llm_source import (
    BudgetExceeded,
    ConfigError,
    LlmSourceError,
    ParseError,
    QueryError,
    TransportError,
)
from click.testing import CliRunner

from blue_data_llm_cli import config
from blue_data_llm_cli.commands import cli

QUERY = "the member states of the European Union"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (QueryError("query must not be empty or whitespace"), config.EXIT_QUERY),
        (ConfigError("no model configured (set llm.model)"), config.EXIT_CONFIG),
        (TransportError("provider refused the connection"), config.EXIT_TRANSPORT),
        (ParseError("model response is not valid JSON"), config.EXIT_PARSE),
        (BudgetExceeded("cost limit reached (0.5, spent 0.6)", limit="max_cost"), 7),
        (LlmSourceError("something the hierarchy does not name"), config.EXIT_ERROR),
    ],
)
def test_library_errors_map_to_their_exit_code(
    error: LlmSourceError, code: int, runner: CliRunner, install_raising_source: Any
) -> None:
    install_raising_source(error)
    result = runner.invoke(cli, ["fetch", QUERY])
    assert result.exit_code == code
    assert str(error) in result.output
    assert type(error).__name__ in result.output
    assert "Traceback" not in result.output


def test_the_message_goes_to_stderr(runner: CliRunner, install_raising_source: Any) -> None:
    install_raising_source(QueryError("query must not be empty or whitespace"))
    result = runner.invoke(cli, ["fetch", QUERY])
    assert result.stdout == ""
    assert "query must not be empty or whitespace" in result.stderr


def test_an_empty_query_succeeds_with_no_records(runner: CliRunner, install_client: Any) -> None:
    """An empty ask is an empty answer, not an error, the way the library reports it.

    The command exits zero because nothing went wrong. The reason the result is empty is
    still visible: --verbose surfaces the outcome the library recorded.
    """
    install_client()
    result = runner.invoke(cli, ["fetch", ""])
    assert result.exit_code == config.EXIT_OK
    assert "0 records" in result.output
    assert "Traceback" not in result.output


def test_an_empty_query_reports_why_under_verbose(runner: CliRunner, install_client: Any) -> None:
    install_client()
    result = runner.invoke(cli, ["--verbose", "fetch", ""])
    assert result.exit_code == config.EXIT_OK
    assert "empty_query" in result.output


def test_max_records_below_one_is_a_query_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["fetch", QUERY, "--max-records", "0"])
    assert result.exit_code == config.EXIT_QUERY
    assert "greater than zero" in result.output


def test_a_rejected_setting_is_a_config_error(runner: CliRunner, install_client: Any) -> None:
    install_client(ConfigError("unknown adapter override 'llm.nope'"))
    result = runner.invoke(cli, ["fetch", QUERY])
    assert result.exit_code == config.EXIT_CONFIG
    assert "unknown adapter override" in result.output
    assert "Traceback" not in result.output


def test_a_provider_failure_is_a_transport_error(runner: CliRunner, install_client: Any) -> None:
    install_client(TransportError("connection reset by peer", code="provider_error"))
    result = runner.invoke(cli, ["fetch", QUERY])
    assert result.exit_code == config.EXIT_TRANSPORT
    assert "connection reset by peer" in result.output
    assert "Traceback" not in result.output


def test_an_unreadable_answer_is_a_parse_error(runner: CliRunner, install_client: Any) -> None:
    install_client("I am afraid I cannot answer that.")
    result = runner.invoke(cli, ["fetch", QUERY])
    assert result.exit_code == config.EXIT_PARSE
    assert "not valid JSON" in result.output
    assert "Traceback" not in result.output


def test_an_attr_without_a_name_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["fetch", QUERY, "--attr", ":int"])
    assert result.exit_code == config.EXIT_USAGE
    assert "no attribute name" in result.output


def test_an_unknown_option_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["fetch", QUERY, "--nope"])
    assert result.exit_code == config.EXIT_USAGE


def test_a_missing_query_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["fetch"])
    assert result.exit_code == config.EXIT_USAGE


def test_an_unknown_format_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["fetch", QUERY, "--format", "yaml"])
    assert result.exit_code == config.EXIT_USAGE
