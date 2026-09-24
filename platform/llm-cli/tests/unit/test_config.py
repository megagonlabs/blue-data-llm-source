"""Option translation, below the commands.

What the command layer is checked for is that the right option reaches the right key. What
is checked here is the translation itself, and the two pieces of CLI vocabulary the
commands share: the settings envelope and the error mapping.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from blue_data_llm_source import (
    Attr,
    BudgetExceeded,
    ConfigError,
    LlmSourceError,
    ParseError,
    QueryError,
    TransportError,
)

from blue_data_llm_cli import config
from blue_data_llm_cli.configstore import ConfigResolver, ProfileStore
from blue_data_llm_cli.profile import cli_spec


# ------------------------------------------------------------------ attrs --
def test_a_bare_name_declares_no_type() -> None:
    assert config.parse_attr("country") == Attr(name="country", type=None)


def test_a_name_and_type_declares_both() -> None:
    assert config.parse_attr("year:int") == Attr(name="year", type="int")


def test_surrounding_space_is_not_part_of_the_name() -> None:
    assert config.parse_attr("  year : int ") == Attr(name="year", type="int")


def test_an_attr_with_no_name_names_the_form_to_use() -> None:
    with pytest.raises(ValueError, match="NAME or NAME:TYPE"):
        config.parse_attr(":int")


def test_attrs_keep_the_order_they_were_given() -> None:
    assert [a.name for a in config.build_attrs(["b", "a", "c"])] == ["b", "a", "c"]


# ----------------------------------------------------------------- effort --
@pytest.mark.parametrize(("effort", "continuations"), [("low", 0), ("medium", 1), ("high", 2)])
def test_effort_maps_to_a_continuation_allowance(effort: str, continuations: int) -> None:
    """A CLI convenience over plan.max_continuations, not library policy."""
    assert config.continuations_for_effort(effort) == continuations


def test_an_unknown_effort_lists_the_ones_there_are() -> None:
    with pytest.raises(ValueError, match="low, medium, high"):
        config.continuations_for_effort("maximum")


# ----------------------------------------------------------------- limits --
def test_unset_ceilings_produce_no_keys() -> None:
    assert config.limit_properties(config.build_limits()) == {}


def test_a_row_ceiling_is_not_reachable_through_the_ceiling_options() -> None:
    """`--max-records` is the one flag for it, and it goes to `fetch` rather than here."""
    assert config.build_limits(1.0, 2.0, 3, 4).max_rows is None


def test_an_invalid_ceiling_is_refused_by_the_library_with_its_own_message() -> None:
    with pytest.raises(ConfigError):
        config.build_limits(limit_cost=-1.0)


# ------------------------------------------------------------------- plan --
def test_no_plan_option_produces_no_plan_keys() -> None:
    assert config.plan_properties() == {}


def test_plan_options_keep_the_names_the_library_reads() -> None:
    assert config.plan_properties(plan_mode="row_by_row", plan_leaves=8) == {
        "plan.mode": "row_by_row",
        "plan.max_leaves": 8,
    }


# --------------------------------------------------------------- the tiers --
@pytest.fixture
def settings(tmp_path: Path) -> config.Settings:
    store = ProfileStore(tmp_path)
    store.initialize()
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "from-profile")
    return config.Settings(ConfigResolver(cli_spec(), store, environment={}))


def test_a_flag_is_recorded_as_a_flag(settings: config.Settings) -> None:
    assert settings.take("llm.model", "from-the-flag", "BLUE_DATA_LLM_MODEL") == "from-the-flag"
    assert settings.source["llm.model"] == "flag"


def test_a_profile_value_is_recorded_with_its_profile(settings: config.Settings) -> None:
    assert settings.take("llm.model", None, "BLUE_DATA_LLM_MODEL") == "from-profile"
    assert settings.source["llm.model"] == "profile:default"


def test_an_unset_property_is_recorded_nowhere(settings: config.Settings) -> None:
    assert settings.take("limits.max_cost", None, "BLUE_DATA_LLM_SOURCE_LIMIT_COST") is None
    assert settings.applied == {}
    assert settings.source == {}


def test_a_property_with_no_parameter_only_ever_comes_from_a_flag(
    settings: config.Settings,
) -> None:
    assert settings.take("plan.mode", None) is None
    assert settings.take("plan.mode", "flat") == "flat"
    assert settings.source["plan.mode"] == "flag"


def test_the_envelope_is_two_maps_over_the_same_keys(settings: config.Settings) -> None:
    settings.take("llm.model", "gpt-4o", "BLUE_DATA_LLM_MODEL")
    settings.take("limits.max_cost", 0.5, "BLUE_DATA_LLM_SOURCE_LIMIT_COST")
    envelope = settings.envelope()
    assert set(envelope) == {"applied", "source"}
    assert set(envelope["applied"]) == set(envelope["source"])


def test_the_footer_names_every_pair_and_its_tier(settings: config.Settings) -> None:
    settings.take("limits.max_cost", 0.5, "BLUE_DATA_LLM_SOURCE_LIMIT_COST")
    assert settings.footer() == "settings: limits.max_cost=0.5 (flag)"


def test_the_prose_form_names_only_the_ceilings(settings: config.Settings) -> None:
    settings.take("llm.model", "gpt-4o", "BLUE_DATA_LLM_MODEL")
    assert settings.ceilings() == "No ceiling was set."
    settings.take("limits.max_calls", 2, "BLUE_DATA_LLM_SOURCE_LIMIT_CALLS")
    assert settings.ceilings() == "Ceilings applied: max_calls 2."


# --------------------------------------------------------------- the codes --
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (QueryError("x"), config.QUERY),
        (ConfigError("x"), config.CONFIG),
        (TransportError("x"), config.TRANSPORT),
        (ParseError("x"), config.PARSE),
        (BudgetExceeded("x", limit="max_cost"), config.BUDGET),
        (LlmSourceError("x"), config.INTERNAL),
    ],
    ids=lambda value: type(value).__name__,
)
def test_every_library_error_has_a_code(error: LlmSourceError, code: str) -> None:
    assert config.code_for(error) == code
    assert config.exit_code_for(error) == config.EXIT_FOR_CODE[code]


def test_the_error_carries_its_own_exit_status() -> None:
    assert config.CliError(config.BUDGET, "x").exit_code == 7
    assert config.CliError(config.USAGE, "x").exit_code == 2


def test_the_error_envelope_is_the_code_and_the_message() -> None:
    assert config.CliError(config.PARSE, "unreadable").envelope() == {
        "error": {"code": "PARSE", "message": "unreadable"}
    }


# ---------------------------------------------------------------- the home --
def test_the_home_is_an_environment_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.HOME_VARIABLE, "/tmp/elsewhere")
    assert config.cli_home() == "/tmp/elsewhere"
    monkeypatch.delenv(config.HOME_VARIABLE)
    assert config.cli_home().endswith("/.blue-llmdb")


def test_an_empty_home_is_refused_rather_than_read_as_the_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Path("")` is `.`, so an empty export wrote the profile store wherever you stood."""
    monkeypatch.setenv(config.HOME_VARIABLE, "")
    with pytest.raises(config.CliError, match=config.HOME_VARIABLE):
        config.cli_home()


def test_truncation_keeps_the_limit() -> None:
    assert config.truncate("abcdef", 10) == "abcdef"
    assert config.truncate("abcdefghij", 6) == "abc..."
    assert len(config.truncate("x" * 500, 40)) == 40


def test_the_library_default_output_allowance_is_read_rather_than_copied() -> None:
    """A second copy of the library's number is a second thing to get wrong."""
    from blue_data_llm_source import LlmSource
    from blue_data_llm_source.llm import DEFAULT_MODEL

    assert config.default_max_tokens() == LlmSource().max_tokens_for_model(DEFAULT_MODEL)
