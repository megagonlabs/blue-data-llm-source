"""The `model` and `metadata` components.

`model` is the real version of the `database` component the family expects: this source has
no databases to choose between, but it does have models, and the price table is the thing a
caller actually has to choose from.
"""

from __future__ import annotations

import json

import pytest
from blue_data_llm_source import priceable_models, rates_for
from click.testing import CliRunner

from blue_data_llm_cli import config
from blue_data_llm_cli.commands import cli


# -------------------------------------------------------------- model list --
def test_model_list_is_the_published_price_table(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["model", "list", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["models"] == list(priceable_models())


def test_model_list_is_sorted_and_not_empty(runner: CliRunner) -> None:
    models = json.loads(runner.invoke(cli, ["model", "list", "--format", "json"]).stdout)["models"]
    assert models == sorted(models)
    assert models


def test_model_list_says_what_the_list_is_for(runner: CliRunner) -> None:
    # Click rewraps the docstring, so the comparison is on words rather than on lines.
    text = " ".join(runner.invoke(cli, ["model", "list", "--help"]).stdout.split())
    assert "cost ceiling" in text
    assert "--price-in" in text


# ----------------------------------------------------------- model inspect --
def test_model_inspect_reports_the_published_rates(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["model", "inspect", "gpt-4o", "--format", "json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    rates = rates_for("gpt-4o")
    assert rates is not None
    assert body["model"] == "gpt-4o"
    assert body["input_price_per_token"] == rates[0]
    assert body["output_price_per_token"] == rates[1]
    assert body["max_tokens"] == 4_096
    assert body["can_bound"] is True


def test_model_inspect_uses_the_named_model_profile_allowance(runner: CliRunner) -> None:
    body = json.loads(
        runner.invoke(cli, ["model", "inspect", "gpt-6-luna-2026-09-22", "--format", "json"]).stdout
    )
    assert body["max_tokens"] == 25_000


def test_an_unpriced_model_reports_nulls_rather_than_dropping_the_keys(
    runner: CliRunner,
) -> None:
    body = json.loads(
        runner.invoke(cli, ["model", "inspect", "llama-9", "--format", "json"]).stdout
    )
    assert body["input_price_per_token"] is None
    assert body["output_price_per_token"] is None
    assert body["can_bound"] is False


def test_the_allowance_the_bound_is_judged_against_comes_from_the_profile(
    runner: CliRunner,
) -> None:
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "128"])
    body = json.loads(runner.invoke(cli, ["model", "inspect", "gpt-4o", "--format", "json"]).stdout)
    assert body["max_tokens"] == 128


def test_a_configured_allowance_of_zero_is_not_read_as_unset(runner: CliRunner) -> None:
    body = json.loads(
        runner.invoke(
            cli,
            ["model", "inspect", "gpt-4o", "--format", "json"],
            env={"BLUE_DATA_LLM_SOURCE_MAX_TOKENS": "0"},
        ).stdout
    )
    assert body["max_tokens"] == 0
    assert body["can_bound"] is False


def test_selecting_a_blank_model_is_refused(runner: CliRunner) -> None:
    """It stored an empty value, reported success, and reverted the model to the default."""
    runner.invoke(cli, ["model", "select", "gpt-4o-mini"])
    result = runner.invoke(cli, ["model", "select", "  "])
    assert result.exit_code == config.EXIT_USAGE
    values = json.loads(runner.invoke(cli, ["profile", "inspect", "--format", "json"]).stdout)
    assert values["values"]["BLUE_DATA_LLM_MODEL"]["value"] == "gpt-4o-mini"


def test_the_prose_names_the_floor_a_cost_ceiling_has_to_clear(runner: CliRunner) -> None:
    out = " ".join(
        runner.invoke(cli, ["model", "inspect", "gpt-4o", "--format", "nl"]).stdout.split()
    )
    assert "cost ceiling has to clear" in out


@pytest.mark.parametrize(
    ("model", "floor"),
    [("gpt-4o", "$0.0410 at 4096"), ("gpt-6-luna", "$0.0138 at 25000")],
)
def test_the_floor_carries_the_regional_uplift_where_the_bound_does(
    runner: CliRunner, model: str, floor: str
) -> None:
    out = " ".join(runner.invoke(cli, ["model", "inspect", model, "--format", "nl"]).stdout.split())
    assert f"has to clear about {floor} output tokens" in out


def test_model_inspect_adds_the_published_defaults(runner: CliRunner) -> None:
    body = json.loads(
        runner.invoke(cli, ["model", "inspect", "gpt-6-sol", "--format", "json"]).stdout
    )
    assert body["max_tokens"] == 25_000
    defaults = body["published_defaults"]
    assert defaults["reasoning_efforts"] == ["none", "low", "medium", "high", "xhigh", "max"]
    assert defaults["default_reasoning_effort"] == "medium"
    assert defaults["temperature"] == "effort_none_only"
    assert defaults["output_token_parameter"] == "max_completion_tokens"
    assert defaults["cached_input_price_per_token"] == 0.20e-6
    assert defaults["cache_write_price_per_token"] == 2.50e-6
    assert defaults["long_context_threshold_tokens"] == 272_000


def test_an_unknown_model_publishes_no_defaults(runner: CliRunner) -> None:
    body = json.loads(
        runner.invoke(cli, ["model", "inspect", "llama-9", "--format", "json"]).stdout
    )
    assert body["published_defaults"] is None


def test_the_table_spells_each_published_default_on_its_own_row(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["model", "inspect", "gpt-5", "--format", "csv"])
    assert result.exit_code == 0, result.output
    rows = dict(line.split(",", 1) for line in result.stdout.splitlines()[1:])
    assert rows["published_defaults.reasoning_efforts"] == '"minimal, low, medium, high"'
    assert rows["published_defaults.temperature"] == "never"


# ------------------------------------------------------------ model select --
def test_model_select_stores_the_choice_in_the_profile(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["model", "select", "gpt-4o-mini"])
    assert result.exit_code == 0, result.output
    assert "Selected model: gpt-4o-mini" in result.stdout
    values = json.loads(runner.invoke(cli, ["profile", "inspect", "--format", "json"]).stdout)
    assert values["values"]["BLUE_DATA_LLM_MODEL"]["value"] == "gpt-4o-mini"
    assert values["values"]["BLUE_DATA_LLM_MODEL"]["source"] == "profile:default"


def test_a_selected_model_is_what_a_query_then_uses(runner: CliRunner) -> None:
    runner.invoke(cli, ["model", "select", "gpt-4o-mini"])
    result = runner.invoke(cli, ["data", "query", "anything", "--format", "json"])
    assert json.loads(result.stdout)["settings"]["applied"]["llm.model"] == "gpt-4o-mini"


def test_a_flag_still_overrides_a_selected_model(runner: CliRunner) -> None:
    runner.invoke(cli, ["model", "select", "gpt-4o-mini"])
    result = runner.invoke(
        cli, ["data", "query", "anything", "--model", "gpt-4", "--format", "json"]
    )
    assert json.loads(result.stdout)["settings"]["applied"]["llm.model"] == "gpt-4"


def test_selecting_an_unpriced_model_warns_and_names_the_price_parameters(
    runner: CliRunner,
) -> None:
    result = runner.invoke(cli, ["model", "select", "llama-9"])
    assert result.exit_code == 0, result.output
    assert "no published price" in result.stderr
    assert "--price-in" in result.stderr
    assert "BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN" in result.stderr


def test_selecting_a_priced_model_warns_about_nothing(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["model", "select", "gpt-4o"])
    assert result.stderr.strip() == ""


def test_model_select_takes_no_format(runner: CliRunner) -> None:
    """`--format` belongs to listings and results, never to a mutation."""
    result = runner.invoke(cli, ["model", "select", "gpt-4o", "--format", "json"])
    assert result.exit_code == config.EXIT_USAGE


# -------------------------------------------------------- metadata inspect --
def test_metadata_inspect_reports_the_protocol_variant_and_version(runner: CliRunner) -> None:
    from blue_data_llm_source import DEFAULT_VARIANT, PROTOCOL
    from blue_data_llm_source import __version__ as library_version

    body = json.loads(runner.invoke(cli, ["metadata", "inspect", "--format", "json"]).stdout)
    assert body == {
        "protocol": PROTOCOL,
        "variant": DEFAULT_VARIANT,
        "version": library_version,
    }


def test_metadata_inspect_takes_no_arguments(runner: CliRunner) -> None:
    assert runner.invoke(cli, ["metadata", "inspect", "extra"]).exit_code == config.EXIT_USAGE
