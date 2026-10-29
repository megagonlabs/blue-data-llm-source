"""CLI redaction tests for errors, settings, logs, and metadata."""

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
ENVIRONMENT_VALUE = "canary-environment-value"


def everything_visible(result: Result) -> str:
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
    install_client(TransportError(f"AuthenticationError: invalid api key {STAMPED}"))
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])
    envelope = json.loads(result.stdout)

    assert STAMPED not in envelope["error"]["message"]
    assert "***" in envelope["error"]["message"]


def test_the_traceback_under_log_is_masked(runner: CliRunner, install_client: Any) -> None:
    install_client(TransportError(f"AuthenticationError: invalid api key {STAMPED}"))
    result = runner.invoke(cli, ["data", "query", QUERY, "--log"])

    assert "Traceback" in result.stderr
    assert STAMPED not in result.stderr


def test_a_key_held_in_the_environment_is_masked_whatever_it_looks_like(
    runner: CliRunner, install_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", ENVIRONMENT_VALUE)
    install_client(TransportError(f"AuthenticationError: {ENVIRONMENT_VALUE} was rejected"))
    result = runner.invoke(cli, ["data", "query", QUERY, "--log", "--format", "json"])

    assert ENVIRONMENT_VALUE not in everything_visible(result)


def test_a_failure_this_cli_never_classified_is_masked_too(
    runner: CliRunner, install_client: Any
) -> None:
    install_client(RuntimeError(f"request failed: api_key={STAMPED}"))
    result = runner.invoke(cli, ["data", "query", QUERY])

    assert result.exit_code == config.EXIT_ERROR
    assert f"ERROR: {config.INTERNAL} " in result.stderr
    assert STAMPED not in everything_visible(result)
    assert "Traceback" not in everything_visible(result)


def test_a_refused_configuration_value_is_masked_where_it_is_set(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "config", "BLUE_DATA_CLI_LOG_LIMIT", STAMPED])

    assert result.exit_code == config.EXIT_PROFILE
    assert STAMPED not in everything_visible(result)


def test_a_refused_configuration_value_is_masked_where_it_is_read(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BLUE_DATA_CLI_LOG_LIMIT", STAMPED)
    result = runner.invoke(cli, ["data", "query", QUERY])

    assert result.exit_code == config.EXIT_PROFILE
    assert STAMPED not in everything_visible(result)


def test_the_query_itself_is_masked_in_the_log(runner: CliRunner, install_client: Any) -> None:
    install_client()
    result = runner.invoke(cli, ["data", "query", f"anything about {STAMPED}", "--log"])

    assert "query:" in result.stderr
    assert STAMPED not in result.stderr


def test_an_estimate_reason_is_masked(
    runner: CliRunner, install_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Estimate reasons bypass the exception reporter."""
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
    install_client(json.dumps([{"country": "France"}]))
    result = runner.invoke(cli, ["data", "query", QUERY, "--model", STAMPED, "--format", fmt])

    assert result.exit_code == 0, result.output
    assert STAMPED not in everything_visible(result)
    assert "***" in result.stdout


@pytest.mark.parametrize("fmt", ["table", "nl", "json", "csv"])
def test_a_model_that_is_only_a_model_reads_the_same_in_every_format(
    fmt: str, runner: CliRunner, install_client: Any
) -> None:
    install_client(json.dumps([{"country": "France"}]))
    result = runner.invoke(
        cli, ["data", "query", QUERY, "--model", "gpt-4o-2024-08-06", "--format", fmt]
    )

    assert result.exit_code == 0, result.output
    assert "gpt-4o-2024-08-06" in result.stdout or fmt == "csv"
    if fmt == "json":
        envelope = json.loads(result.stdout)
        assert envelope["meta"]["model"] == "gpt-4o-2024-08-06"
        assert envelope["settings"]["applied"]["llm.model"] == "gpt-4o-2024-08-06"


def test_cache_path_with_credential_text_is_masked_in_settings(
    runner: CliRunner, install_client: Any
) -> None:
    install_client(json.dumps([{"country": "France"}]))
    result = runner.invoke(
        cli,
        ["data", "query", QUERY, "--cache-dir", f"/tmp/{STAMPED}/cache", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["settings"]["applied"]["llm.cache_dir"] == "/tmp/***/cache"
    assert STAMPED not in everything_visible(result)


def test_a_record_value_shaped_like_a_key_is_printed_as_it_arrived(
    runner: CliRunner, install_client: Any
) -> None:
    install_client(json.dumps([{"country": "France", "code": STAMPED}]))
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])

    assert json.loads(result.stdout)["records"] == [{"country": "France", "code": STAMPED}]


def test_discarded_record_text_is_masked_in_table_metadata(
    runner: CliRunner, install_client: Any
) -> None:
    install_client(json.dumps([{"id": "one", "v": "safe"}, {"id": "one", "v": STAMPED}]))
    result = runner.invoke(
        cli,
        ["data", "query", QUERY, "--attr", "id", "--attr", "v", "--plan-key", "id"],
    )

    assert result.exit_code == 0, result.output
    assert STAMPED not in everything_visible(result)
    assert "gap:" in result.stdout


def test_primary_key_metadata_is_masked_in_verbose_output(
    runner: CliRunner, install_client: Any
) -> None:
    install_client("[]")
    result = runner.invoke(
        cli,
        ["data", "query", QUERY, "--attr", STAMPED, "--plan-key", STAMPED, "--verbose"],
    )

    assert result.exit_code == 0, result.output
    assert STAMPED not in everything_visible(result)
    assert "primary keys: ***" in result.stdout


def test_discarded_unmapped_key_is_masked_in_verbose_output(
    runner: CliRunner, install_client: Any
) -> None:
    install_client(json.dumps([{"id": "safe", STAMPED: "discarded"}]))
    result = runner.invoke(cli, ["data", "query", QUERY, "--attr", "id", "--verbose"])

    assert result.exit_code == 0, result.output
    assert STAMPED not in everything_visible(result)
    assert "unmapped keys: ***" in result.stdout


def test_the_masking_rule_agrees_with_the_parameters_this_cli_calls_secret() -> None:
    """CLI secret parameters must match the library's variable-name matcher."""
    secrets = [param.name for param in cli_spec() if param.is_secret]
    assert secrets
    for name in secrets:
        assert _SECRET_VARIABLE.search(name), name
