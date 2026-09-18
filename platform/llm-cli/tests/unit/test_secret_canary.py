"""A sentinel put into every place a string can enter, looked for in every place one leaves.

The entries are a provider exception, a value the environment holds, a configuration value
somebody typed and the query itself. The exits are standard output, standard error, the JSON
error object, the `--log` lines and the traceback `--log` prints. No test here reaches a
provider: the default source is the suite's fake client, and the home is a temporary directory.

Every literal below is a sentinel, not a credential.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from blue_data_llm_source import ConfigError, TransportError
from blue_data_llm_source.redaction import _SECRET_VARIABLE  # pyright: ignore[reportPrivateUsage]
from click.testing import CliRunner, Result

from blue_data_llm_cli import config
from blue_data_llm_cli.commands import cli
from blue_data_llm_cli.profile import cli_spec

QUERY = "the member states of the European Union"

STAMPED = "sk-canary-NOTAKEY"
"""A key literal in the shape a provider stamps one with."""

ENVIRONMENT_VALUE = "canary-environment-value"
"""A key with no shape at all, which only the variable holding it identifies."""


def everything_visible(result: Result) -> str:
    """Both streams as one string, because a leak anywhere in them is a leak."""
    return f"{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize("arguments", [[], ["--format", "json"], ["--log"]])
def test_a_provider_exception_does_not_carry_a_key_to_any_stream(
    arguments: list[str], runner: CliRunner, install_client: Any
) -> None:
    install_client(TransportError(f"AuthenticationError: invalid api key {STAMPED}"))
    result = runner.invoke(cli, ["data", "query", QUERY, *arguments])

    assert result.exit_code == config.EXIT_TRANSPORT
    assert STAMPED not in everything_visible(result)
    assert "***" in result.stderr


def test_the_json_error_object_is_masked_as_well_as_the_line(
    runner: CliRunner, install_client: Any
) -> None:
    """Two doors out of one error, and a consumer reading stdout sees the object."""
    install_client(TransportError(f"AuthenticationError: invalid api key {STAMPED}"))
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])
    envelope = json.loads(result.stdout)

    assert STAMPED not in envelope["error"]["message"]
    assert "***" in envelope["error"]["message"]


def test_the_traceback_under_log_is_masked(runner: CliRunner, install_client: Any) -> None:
    """The frames quote the exception itself, so masking the reported line is not enough."""
    install_client(TransportError(f"AuthenticationError: invalid api key {STAMPED}"))
    result = runner.invoke(cli, ["data", "query", QUERY, "--log"])

    assert "Traceback" in result.stderr
    assert STAMPED not in result.stderr


def test_a_key_held_in_the_environment_is_masked_whatever_it_looks_like(
    runner: CliRunner, install_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The realistic case: a provider SDK reads the variable and quotes what it read."""
    monkeypatch.setenv("OPENAI_API_KEY", ENVIRONMENT_VALUE)
    install_client(TransportError(f"AuthenticationError: {ENVIRONMENT_VALUE} was rejected"))
    result = runner.invoke(cli, ["data", "query", QUERY, "--log", "--format", "json"])

    assert ENVIRONMENT_VALUE not in everything_visible(result)


def test_a_failure_this_cli_never_classified_is_masked_too(
    runner: CliRunner, install_client: Any
) -> None:
    """An unclassified exception used to reach the terminal as an interpreter traceback."""
    install_client(RuntimeError(f"request failed: api_key={STAMPED}"))
    result = runner.invoke(cli, ["data", "query", QUERY])

    assert result.exit_code == config.EXIT_ERROR
    assert f"ERROR: {config.INTERNAL} " in result.stderr
    assert STAMPED not in everything_visible(result)
    assert "Traceback" not in everything_visible(result)


def test_a_refused_configuration_value_is_masked_where_it_is_set(runner: CliRunner) -> None:
    """A value typed under the wrong parameter is still the value somebody typed."""
    result = runner.invoke(cli, ["profile", "config", "BLUE_DATA_CLI_LOG_LIMIT", STAMPED])

    assert result.exit_code == config.EXIT_PROFILE
    assert STAMPED not in everything_visible(result)


def test_a_refused_configuration_value_is_masked_where_it_is_read(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read back a command later, and by a path that runs before the error context exists."""
    monkeypatch.setenv("BLUE_DATA_CLI_LOG_LIMIT", STAMPED)
    result = runner.invoke(cli, ["data", "query", QUERY])

    assert result.exit_code == config.EXIT_PROFILE
    assert STAMPED not in everything_visible(result)


def test_the_query_itself_is_masked_in_the_log(runner: CliRunner, install_client: Any) -> None:
    """`--log` echoes the query, which is the one input a caller writes by hand."""
    install_client()
    result = runner.invoke(cli, ["data", "query", f"anything about {STAMPED}", "--log"])

    assert "query:" in result.stderr
    assert STAMPED not in result.stderr


def test_an_estimate_reason_is_masked(
    runner: CliRunner, install_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`reason` is a library message published as a field rather than raised.

    Nothing else about the call fails, so the error boundary never sees it.
    """
    client = install_client()

    def refuse(prompt: str, properties: dict[str, Any]) -> float:
        raise ConfigError(f"no published prices for {STAMPED!r}")

    monkeypatch.setattr(client, "bound_for_attempt", refuse, raising=False)
    result = runner.invoke(cli, ["data", "estimate", QUERY, "--format", "json"])

    published = json.loads(result.stdout)
    assert published["bounded"] is False
    assert "***" in published["reason"]
    assert STAMPED not in everything_visible(result)


@pytest.mark.parametrize("fmt", ["table", "nl"])
def test_the_reported_model_is_masked_on_the_surfaces_that_read_it_directly(
    fmt: str, runner: CliRunner, install_client: Any
) -> None:
    """`meta.model` echoes the configured `llm.model`, so a credential set there comes back.

    `json` and `csv` inherit the masking from `meta_view`. The table footer and the `nl`
    sentence read the field off the metadata, and the footer prints on every default run.
    """
    install_client(json.dumps([{"country": "France"}]))
    result = runner.invoke(cli, ["data", "query", QUERY, "--model", STAMPED, "--format", fmt])

    assert result.exit_code == 0, result.output
    assert STAMPED not in everything_visible(result)
    assert "***" in result.stdout


@pytest.mark.parametrize("fmt", ["table", "nl", "json", "csv"])
def test_a_model_that_is_only_a_model_reads_the_same_in_every_format(
    fmt: str, runner: CliRunner, install_client: Any
) -> None:
    """Masking reads shape, so an ordinary name costs nothing and the machine formats move."""
    install_client(json.dumps([{"country": "France"}]))
    result = runner.invoke(
        cli, ["data", "query", QUERY, "--model", "gpt-4o-2024-08-06", "--format", fmt]
    )

    assert result.exit_code == 0, result.output
    assert "***" not in result.stdout
    assert "gpt-4o-2024-08-06" in result.stdout or fmt == "csv"


def test_a_record_value_shaped_like_a_key_is_printed_as_it_arrived(
    runner: CliRunner, install_client: Any
) -> None:
    """The other side of the boundary. Records are the answer, and are not masked."""
    install_client(json.dumps([{"country": "France", "code": STAMPED}]))
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])

    assert json.loads(result.stdout)["records"] == [{"country": "France", "code": STAMPED}]


def test_the_masking_rule_agrees_with_the_parameters_this_cli_calls_secret() -> None:
    """One notion of a secret, held together across a boundary the library cannot import.

    The profile engine is a stand-in for the family's shared module and is meant to be
    deleted when that ships, so the library reads a variable's name where this CLI reads a
    declared cast. A parameter declared secret here and unrecognised there would be a value
    masked in `profile inspect` and printed in an error message.
    """
    secrets = [param.name for param in cli_spec() if param.is_secret]
    assert secrets
    for name in secrets:
        assert _SECRET_VARIABLE.search(name), name
