"""Errors reach the terminal as one line and an exit code, never as a traceback.

The code vocabulary and the exit table are the contract a script branches on, so every code
is raised here and checked for both halves: the token in the message and the status on the
way out.
"""

from __future__ import annotations

import json
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
        (QueryError("query must not be empty or whitespace"), config.QUERY),
        (ConfigError("no model configured (set llm.model)"), config.CONFIG),
        (TransportError("provider refused the connection"), config.TRANSPORT),
        (ParseError("model response is not valid JSON"), config.PARSE),
        (BudgetExceeded("cost limit reached (0.5, spent 0.6)", limit="max_cost"), config.BUDGET),
        (LlmSourceError("something the hierarchy does not name"), config.INTERNAL),
    ],
    ids=lambda value: type(value).__name__,
)
def test_every_library_error_gets_its_code_and_its_exit_status(
    error: LlmSourceError, code: str, runner: CliRunner, install_raising_source: Any
) -> None:
    install_raising_source(error)
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert result.exit_code == config.EXIT_FOR_CODE[code]
    assert f"ERROR: {code} " in result.stderr
    assert str(error) in result.stderr
    assert "Traceback" not in result.output


def test_an_error_line_cannot_drive_the_terminal(runner: CliRunner) -> None:
    """The message carries a name someone typed and a library message quoting a model."""
    result = runner.invoke(cli, ["profile", "inspect", "ev\x1b[2J\x1b[31m!"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "\x1b" not in result.stderr


def test_the_exit_table_is_the_documented_one() -> None:
    assert config.EXIT_FOR_CODE == {
        "USAGE": 2,
        "QUERY": 3,
        "CONFIG": 4,
        "PROFILE": 4,
        "TRANSPORT": 5,
        "PARSE": 6,
        "BUDGET": 7,
        "PARTIAL": 8,
        "INTERNAL": 1,
    }


def test_config_and_profile_share_a_status_and_are_told_apart_by_the_token(
    runner: CliRunner, install_raising_source: Any
) -> None:
    install_raising_source(ConfigError("unknown adapter override 'llm.nope'"))
    library = runner.invoke(cli, ["data", "query", QUERY])
    local = runner.invoke(cli, ["profile", "config", "NOT_A_PARAM", "x"])

    assert library.exit_code == local.exit_code == 4
    assert "ERROR: CONFIG" in library.stderr
    assert "ERROR: PROFILE" in local.stderr


def test_an_error_goes_to_stderr_and_leaves_stdout_empty(
    runner: CliRunner, install_raising_source: Any
) -> None:
    install_raising_source(QueryError("query must not be empty or whitespace"))
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert result.stdout == ""
    assert "query must not be empty or whitespace" in result.stderr


def test_an_error_is_one_line(runner: CliRunner, install_raising_source: Any) -> None:
    install_raising_source(ParseError("model response is not valid JSON"))
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert len([line for line in result.stderr.splitlines() if line.strip()]) == 1


def test_a_traceback_appears_only_under_log(runner: CliRunner, install_raising_source: Any) -> None:
    install_raising_source(ParseError("model response is not valid JSON"))
    quiet = runner.invoke(cli, ["data", "query", QUERY])
    loud = runner.invoke(cli, ["data", "query", QUERY, "--log"])
    assert "Traceback" not in quiet.stderr
    assert "Traceback" in loud.stderr


# ---------------------------------------------------------- the json shape --
def test_an_error_after_the_body_starts_is_also_an_object_on_stdout(
    runner: CliRunner, install_raising_source: Any
) -> None:
    install_raising_source(TransportError("provider refused the connection"))
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])
    assert result.exit_code == config.EXIT_TRANSPORT
    assert json.loads(result.stdout) == {
        "error": {
            "code": "TRANSPORT",
            "message": "TransportError: provider refused the connection",
        }
    }
    assert "ERROR: TRANSPORT" in result.stderr


def test_a_parse_failure_stays_plain_text_because_the_format_is_not_known_yet(
    runner: CliRunner,
) -> None:
    """Click refuses the flag before any command body runs, so there is no format to honour."""
    result = runner.invoke(cli, ["data", "query", QUERY, "--nope", "--format", "json"])
    assert result.exit_code == config.EXIT_USAGE
    assert result.stdout == ""
    assert "ERROR: USAGE" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        ["--profile", "nope", "data", "estimate", QUERY],
        ["--profile", "nope", "profile", "list"],
        ["--profile", "nope", "model", "list"],
        ["--profile", "nope", "metadata", "inspect"],
    ],
    ids=lambda value: value[3],
)
def test_a_startup_failure_is_an_object_too(command: list[str], runner: CliRunner) -> None:
    """The root callback refuses the profile before Click has parsed `--format`.

    It used to report that itself, in plain text, so the one failure a caller is most
    likely to hit under `--format json` was the one that arrived as prose on stderr.
    """
    result = runner.invoke(cli, [*command, "--format", "json"])
    assert result.exit_code == config.EXIT_PROFILE
    assert json.loads(result.stdout)["error"]["code"] == "PROFILE"
    assert "ERROR: PROFILE" in result.stderr


def test_a_setting_read_before_the_result_is_an_object_too(runner: CliRunner) -> None:
    """The log limit is read to build the reporter, which used to happen outside the contract."""
    result = runner.invoke(
        cli,
        ["data", "estimate", QUERY, "--format", "json"],
        env={"BLUE_DATA_CLI_LOG_LIMIT": "bad"},
    )
    assert result.exit_code == config.EXIT_PROFILE
    assert json.loads(result.stdout) == {
        "error": {
            "code": "PROFILE",
            "message": "BLUE_DATA_CLI_LOG_LIMIT must be a whole number, got 'bad'",
        }
    }


def test_a_configured_format_that_is_not_a_format_is_reported_as_text(runner: CliRunner) -> None:
    """The residue of the boundary: the failure is the lookup that would name the format.

    With `--format json` on the command line there is no lookup and no failure; without one
    there is nothing left to publish in, so this reports as text and says why in the message.
    """
    result = runner.invoke(cli, ["model", "list"], env={"BLUE_DATA_CLI_FORMAT": "nonsense"})
    assert result.exit_code == config.EXIT_PROFILE
    assert result.stdout == ""
    assert "BLUE_DATA_CLI_FORMAT is 'nonsense'" in result.stderr


@pytest.mark.parametrize("flag", ["--plan-mode", "--plan-schema-mode", "--plan-planning-order"])
def test_every_closed_plan_set_refuses_an_unknown_value_the_same_way(
    flag: str, runner: CliRunner
) -> None:
    """Three options over a set the library closes, and one answer between them.

    `--plan-mode` was a bare string, so it travelled to the library and came back as
    `ERROR: CONFIG`, exit 4, with a JSON error object; the other two were `click.Choice`
    and came back as `ERROR: USAGE`, exit 2, with nothing on stdout.
    """
    result = runner.invoke(cli, ["data", "query", QUERY, flag, "bogus", "--format", "json"])
    assert result.exit_code == config.EXIT_USAGE
    assert "ERROR: USAGE" in result.stderr
    assert f"Invalid value for '{flag}'" in result.stderr


def test_the_closed_plan_sets_are_the_library_s_own(runner: CliRunner) -> None:
    """`--help` lists what each one accepts, so the values are discoverable from the CLI."""
    from blue_data_llm_source.planning import MODES, PLANNING_ORDERS, SCHEMA_MODES

    help_text = runner.invoke(cli, ["data", "query", "--help"], terminal_width=200).output
    for values in (MODES, SCHEMA_MODES, PLANNING_ORDERS):
        assert "|".join(values) in help_text


# ----------------------------------------------------- corrective guidance --
def test_a_missing_credential_names_the_command_that_sets_one(
    runner: CliRunner, install_raising_source: Any
) -> None:
    install_raising_source(
        TransportError("litellm.AuthenticationError - Missing credentials", code="provider_error")
    )
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert result.exit_code == config.EXIT_TRANSPORT
    assert "profile config OPENAI_API_KEY" in result.stderr


def test_an_unpriceable_ceiling_names_the_two_price_flags(
    runner: CliRunner, install_raising_source: Any
) -> None:
    install_raising_source(
        ConfigError("no published prices for model 'llama-9', so a cost ceiling cannot be bounded")
    )
    result = runner.invoke(cli, ["data", "query", QUERY, "--limit-cost", "1"])
    assert result.exit_code == config.EXIT_CONFIG
    assert "--price-in" in result.stderr
    assert "--price-out" in result.stderr


def test_an_unknown_option_suggests_what_to_try(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--nope"])
    assert result.exit_code == config.EXIT_USAGE
    assert "--help" in result.stderr


def test_an_unknown_command_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "fetch", QUERY])
    assert result.exit_code == config.EXIT_USAGE
    assert "ERROR: USAGE" in result.stderr


def test_a_missing_argument_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query"])
    assert result.exit_code == config.EXIT_USAGE
    assert "ERROR: USAGE" in result.stderr


def test_a_library_failure_on_the_estimate_path_is_classified_too(
    runner: CliRunner, install_raising_source: Any
) -> None:
    install_raising_source(ConfigError("unknown adapter override 'llm.nope'"))
    result = runner.invoke(cli, ["data", "estimate", QUERY])
    assert result.exit_code == config.EXIT_CONFIG
    assert "ERROR: CONFIG" in result.stderr
