"""The six `profile` subcommands, which the family requires to behave identically.

The store's own rules are checked in `test_configstore`. What is checked here is the Click
layer over it: the three `config` forms, the messages, the confirmation, and the two things
the checklist adds to the family shape — coded errors and four output formats.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

from blue_data_llm_cli import config
from blue_data_llm_cli.commands import cli
from blue_data_llm_cli.configstore import ProfileError, ProfileStore


def profiles(home: Path) -> str:
    return (home / ".profiles").read_text()


# -------------------------------------------------------------- first run --
def test_a_first_run_creates_and_selects_default(runner: CliRunner, cli_home: Path) -> None:
    result = runner.invoke(cli, ["profile", "list", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == {"selected": "default", "profiles": ["default"]}
    assert (cli_home / ".profiles").exists()


def test_a_deliberately_removed_profile_is_not_resurrected(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    assert runner.invoke(cli, ["profile", "remove", "default", "--confirm"]).exit_code == 0
    payload = json.loads(runner.invoke(cli, ["profile", "list", "--format", "json"]).stdout)
    assert payload["profiles"] == ["staging"]


def test_an_empty_export_is_refused_rather_than_dropped_to_the_profile(runner: CliRunner) -> None:
    """The evidence case: a profile value reported as the winner under an empty export.

    `BLUE_DATA_LLM_MODEL=''` looked like an unset variable to the lookup, so the profile
    answered and the run reported `profile:default` for a setting the caller had blanked.
    """
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", "profile-model"])
    result = runner.invoke(
        cli,
        ["data", "estimate", "q", "--format", "json"],
        env={"BLUE_DATA_LLM_MODEL": ""},
    )
    assert result.exit_code == config.EXIT_PROFILE
    assert json.loads(result.stdout)["error"]["code"] == "PROFILE"
    assert "BLUE_DATA_LLM_MODEL" in result.stderr


def test_a_blank_profile_option_is_refused_rather_than_read_as_none_given(
    runner: CliRunner,
) -> None:
    """`--profile ''` fell through the existence check and ran under the selected profile."""
    result = runner.invoke(cli, ["--profile", "", "profile", "list"])
    assert result.exit_code == config.EXIT_USAGE
    assert "--profile needs a profile name" in result.stderr


def test_the_home_is_taken_from_the_environment(runner: CliRunner, tmp_path: Path) -> None:
    elsewhere = tmp_path / "somewhere-else"
    result = runner.invoke(cli, ["profile", "list"], env={config.HOME_VARIABLE: str(elsewhere)})
    assert result.exit_code == 0, result.output
    assert (elsewhere / ".profiles").exists()


# --------------------------------------------------------------------- add --
def test_add_reports_the_name_and_how_to_configure_it(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "add", "staging"])
    assert result.exit_code == 0, result.output
    assert "Created profile: staging" in result.stdout
    assert "profile config" in result.stdout
    assert "--profile staging" in result.stdout


def test_adding_a_profile_twice_is_a_profile_error(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    result = runner.invoke(cli, ["profile", "add", "staging"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "ERROR: PROFILE" in result.stderr
    assert "already exists" in result.stderr


# ------------------------------------------------------------------ remove --
def test_remove_asks_first(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    result = runner.invoke(cli, ["profile", "remove", "staging"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Removed profile: staging" in result.stdout


def test_declining_removes_nothing_and_is_not_a_failure(runner: CliRunner) -> None:
    """A question asked and answered. It used to exit 1, which this CLI reserves for
    INTERNAL, and print Click's bare `Aborted!` instead of the error vocabulary."""
    runner.invoke(cli, ["profile", "add", "staging"])
    result = runner.invoke(cli, ["profile", "remove", "staging"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "ERROR:" not in result.stderr
    assert "Aborted" not in result.output
    payload = json.loads(runner.invoke(cli, ["profile", "list", "--format", "json"]).stdout)
    assert "staging" in payload["profiles"]


def test_confirm_makes_it_non_interactive(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    result = runner.invoke(cli, ["profile", "remove", "staging", "--confirm"])
    assert result.exit_code == 0, result.output


def test_removing_with_nothing_to_answer_from_says_to_pass_confirm(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    result = runner.invoke(cli, ["profile", "remove", "staging"])
    assert result.exit_code == config.EXIT_USAGE
    assert "--confirm" in result.stderr


def test_removing_an_absent_profile_points_at_the_listing(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "remove", "ghost", "--confirm"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "no such profile" in result.stderr
    assert "profile list" in result.stderr


def test_remove_touches_only_that_section(runner: CliRunner, cli_home: Path) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    runner.invoke(cli, ["profile", "add", "keep"])
    runner.invoke(cli, ["profile", "config", "--profile", "keep", "BLUE_DATA_LLM_MODEL", "kept"])
    runner.invoke(cli, ["profile", "config", "--profile", "staging", "BLUE_DATA_LLM_MODEL", "x"])
    cache = cli_home / "cache"
    cache.mkdir(exist_ok=True)
    (cache / "entry.json").write_text("{}")

    assert runner.invoke(cli, ["profile", "remove", "staging", "--confirm"]).exit_code == 0

    text = profiles(cli_home)
    assert "[staging]" not in text
    assert "kept" in text
    assert (cli_home / ".selected.profile").exists()
    assert (cache / "entry.json").exists()


def test_removing_the_selected_profile_says_the_selection_moved(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    runner.invoke(cli, ["profile", "select", "staging"])
    result = runner.invoke(cli, ["profile", "remove", "staging", "--confirm"])
    assert result.exit_code == 0, result.output
    assert "default" in result.stdout


def test_the_last_profile_cannot_be_removed(runner: CliRunner) -> None:
    """`remove default` used to leave `{"selected": "default", "profiles": []}`.

    The selection fell back to `default` unconditionally, so the store reported a selected
    profile that was not in its own list and every later run resolved against a section
    that was not there.
    """
    result = runner.invoke(cli, ["profile", "remove", "default", "--confirm"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "only profile" in result.stderr
    payload = json.loads(runner.invoke(cli, ["profile", "list", "--format", "json"]).stdout)
    assert payload == {"selected": "default", "profiles": ["default"]}


def test_removing_the_selected_default_moves_the_selection_to_one_that_exists(
    runner: CliRunner,
) -> None:
    result = runner.invoke(cli, ["profile", "add", "staging"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli, ["profile", "remove", "default", "--confirm"])
    assert result.exit_code == 0, result.output
    assert "'staging'" in result.stdout
    payload = json.loads(runner.invoke(cli, ["profile", "list", "--format", "json"]).stdout)
    assert payload == {"selected": "staging", "profiles": ["staging"]}


# -------------------------------------------------------------------- list --
def test_list_marks_the_selected_profile(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    runner.invoke(cli, ["profile", "select", "staging"])
    out = runner.invoke(cli, ["profile", "list"]).stdout
    assert "staging" in out
    assert "*" in out


def test_an_empty_listing_says_what_to_run(runner: CliRunner, cli_home: Path) -> None:
    """`remove` keeps the store non-empty, so an emptied profiles file is the way in."""
    runner.invoke(cli, ["profile", "list"])
    (cli_home / ".profiles").write_text("")
    out = runner.invoke(cli, ["profile", "list"]).stdout
    assert "no profiles" in out
    assert "profile add NAME" in out


# ------------------------------------------------------------------ select --
def test_select_reports_the_choice(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    result = runner.invoke(cli, ["profile", "select", "staging"])
    assert result.exit_code == 0, result.output
    assert "Selected profile: staging" in result.stdout


def test_selecting_an_absent_profile_points_at_the_listing(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "select", "ghost"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "no such profile" in result.stderr
    assert "profile list" in result.stderr


# ----------------------------------------------------------------- inspect --
def test_inspect_reports_the_winning_tier_for_each_parameter(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_SOURCE_LIMIT_COST", "0.5"])
    payload = json.loads(
        runner.invoke(
            cli,
            ["profile", "inspect", "--format", "json"],
            env={"BLUE_DATA_LLM_SOURCE_TIMEOUT": "30"},
        ).stdout
    )
    values = payload["values"]
    assert values["BLUE_DATA_LLM_SOURCE_TIMEOUT"]["source"] == "environment"
    assert values["BLUE_DATA_LLM_SOURCE_LIMIT_COST"]["source"] == "profile:default"
    assert values["BLUE_DATA_LLM_MODEL"]["source"] == "default"
    assert values["BLUE_DATA_LLM_SOURCE_MAX_RECORDS"]["source"] == "unset"


def test_inspect_never_reports_a_flag_tier(runner: CliRunner) -> None:
    """A command option never reaches the resolver, so there is no such tier to report."""
    payload = json.loads(runner.invoke(cli, ["profile", "inspect", "--format", "json"]).stdout)
    assert all(entry["source"] != "flag" for entry in payload["values"].values())


def test_inspect_can_report_on_another_profile(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    runner.invoke(cli, ["profile", "config", "--profile", "staging", "BLUE_DATA_LLM_MODEL", "s"])
    payload = json.loads(
        runner.invoke(cli, ["profile", "inspect", "staging", "--format", "json"]).stdout
    )
    assert payload["profile"] == "staging"
    assert payload["values"]["BLUE_DATA_LLM_MODEL"]["value"] == "s"


# ------------------------------------------------------------------ config --
def test_config_sets_one_value_directly(runner: CliRunner, cli_home: Path) -> None:
    result = runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini"])
    assert result.exit_code == 0, result.output
    assert "Set BLUE_DATA_LLM_MODEL" in result.stdout
    assert "gpt-4o-mini" in profiles(cli_home)


def test_config_prompts_for_one_value(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL"], input="gpt-4o-mini\n")
    assert result.exit_code == 0, result.output
    assert "Updated 1 parameter(s)" in result.stdout


def test_config_walks_the_required_parameters(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "config"], input="\ngpt-4o-mini\n")
    assert result.exit_code == 0, result.output
    assert "2 required parameters" in result.stdout
    assert "Updated 1 parameter(s)" in result.stdout


def test_a_blank_answer_leaves_the_parameter_unset(runner: CliRunner, cli_home: Path) -> None:
    result = runner.invoke(cli, ["profile", "config"], input="\n\n")
    assert result.exit_code == 0, result.output
    assert "No changes." in result.stdout
    assert "BLUE_DATA_LLM_MODEL" not in profiles(cli_home)


def test_a_value_holding_a_line_break_is_refused(runner: CliRunner, cli_home: Path) -> None:
    """A complete write must not leave a profiles file the next run reads differently."""
    payload = "a\r[injected]\rOPENAI_API_KEY = enc:eAo="
    result = runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", payload])
    assert result.exit_code == config.EXIT_PROFILE
    assert "line break" in result.stderr
    assert "injected" not in profiles(cli_home)
    assert runner.invoke(cli, ["profile", "list"]).exit_code == 0


def test_a_blank_value_is_refused(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", "  "])
    assert result.exit_code == config.EXIT_PROFILE
    assert "blank" in result.stderr


def test_inspecting_a_profile_that_does_not_exist_is_refused(runner: CliRunner) -> None:
    """A table of defaults under a name that does not exist reads as `here is that profile`."""
    result = runner.invoke(cli, ["profile", "inspect", "ghost"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "no such profile" in result.stderr


def test_a_store_that_cannot_be_read_fails_cleanly_on_every_command(
    runner: CliRunner, cli_home: Path
) -> None:
    cli_home.mkdir(parents=True)
    (cli_home / ".profiles").mkdir()
    for command in (["profile", "list"], ["model", "list"], ["data", "query", "q"]):
        result = runner.invoke(cli, command)
        assert result.exit_code == config.EXIT_PROFILE, command
        assert "ERROR: PROFILE" in result.stderr
        assert "Traceback" not in result.output


def test_config_targets_and_auto_creates_a_named_profile(runner: CliRunner) -> None:
    result = runner.invoke(
        cli, ["profile", "config", "--profile", "staging", "BLUE_DATA_LLM_MODEL", "s"]
    )
    assert result.exit_code == 0, result.output
    assert "Created profile 'staging'." in result.stdout
    payload = json.loads(runner.invoke(cli, ["profile", "list", "--format", "json"]).stdout)
    assert "staging" in payload["profiles"]


def test_an_unknown_parameter_is_a_profile_error(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "config", "NOT_A_PARAM", "x"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "unknown configuration parameter" in result.stderr


@pytest.mark.parametrize(
    "argument",
    [
        ["NOT_A_PARAM", "x"],
        ["BLUE_DATA_LLM_SOURCE_MAX_RECORDS", "many"],
        ["BLUE_DATA_LLM_MODEL", "two\nlines"],
    ],
    ids=["unknown-parameter", "will-not-cast", "cannot-be-stored"],
)
def test_a_refused_argument_leaves_no_profile_behind(
    argument: list[str], runner: CliRunner
) -> None:
    """It created the target first and refused afterwards, so a typo in `--profile` left one."""
    result = runner.invoke(cli, ["profile", "config", "--profile", "typo", *argument])
    assert result.exit_code == config.EXIT_PROFILE
    assert "Created profile" not in result.stdout
    payload = json.loads(runner.invoke(cli, ["profile", "list", "--format", "json"]).stdout)
    assert "typo" not in payload["profiles"]


def test_a_cast_failure_names_the_parameter(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_SOURCE_MAX_RECORDS", "many"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "BLUE_DATA_LLM_SOURCE_MAX_RECORDS" in result.stderr


def test_config_with_nothing_to_read_says_to_pass_the_value(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "config"])
    assert result.exit_code == config.EXIT_USAGE
    assert "profile config PARAM VALUE" in result.stderr


# ----------------------------------------------------------------- secrets --
def test_a_secret_is_refused_on_the_command_line(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY", "not-in-history-please"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "shell history" in result.stderr
    assert "$VAR" in result.stderr


def test_a_prompted_secret_is_not_stored_in_plain_text(runner: CliRunner, cli_home: Path) -> None:
    typed = "typed-at-the-prompt"
    result = runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY"], input=f"{typed}\n")
    assert result.exit_code == 0, result.output
    stored = profiles(cli_home)
    assert typed not in stored
    assert "enc:" in stored


def test_a_stored_secret_is_masked_everywhere_it_could_be_printed(
    runner: CliRunner, cli_home: Path
) -> None:
    typed = "typed-at-the-prompt"
    runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY"], input=f"{typed}\n")
    for command in (
        ["profile", "inspect"],
        ["profile", "inspect", "--format", "json"],
        ["profile", "list"],
        ["metadata", "inspect"],
        ["data", "query", "anything", "--log"],
    ):
        result = runner.invoke(cli, command)
        assert typed not in result.output, command
    assert "***" in runner.invoke(cli, ["profile", "inspect"]).stdout


def test_a_reference_is_named_with_whether_it_resolves(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY"], input="$MY_KEY\n")
    unset = runner.invoke(cli, ["profile", "inspect", "--format", "json"]).stdout
    assert "$MY_KEY (unset)" in unset
    resolved = runner.invoke(
        cli, ["profile", "inspect", "--format", "json"], env={"MY_KEY": "exported-not-stored"}
    ).stdout
    assert "$MY_KEY (set)" in resolved
    assert "exported-not-stored" not in resolved


def test_the_prompt_hides_the_secret_it_is_reading(runner: CliRunner) -> None:
    typed = "typed-at-the-prompt"
    result = runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY"], input=f"{typed}\n")
    assert typed not in result.stdout


# ----------------------------------------------------------------- storage --
def test_the_profile_files_are_private(runner: CliRunner, cli_home: Path) -> None:
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini"])
    assert stat.S_IMODE(cli_home.stat().st_mode) == 0o700
    assert stat.S_IMODE((cli_home / ".profiles").stat().st_mode) == 0o600
    assert stat.S_IMODE((cli_home / ".selected.profile").stat().st_mode) == 0o600


def test_a_run_leaves_no_temporary_files_in_the_home(runner: CliRunner, cli_home: Path) -> None:
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini"])
    assert sorted(p.name for p in cli_home.iterdir()) == [".profiles", ".selected.profile"]


@pytest.mark.parametrize("fmt", ["table", "json", "nl", "csv"])
def test_the_profile_listings_honour_every_format(fmt: str, runner: CliRunner) -> None:
    for command in (["profile", "list"], ["profile", "inspect"]):
        result = runner.invoke(cli, [*command, "--format", fmt])
        assert result.exit_code == 0, result.output
        assert result.stdout.strip()


@pytest.mark.parametrize("name", ["bad/name", "bad\\name", "../escape"])
def test_a_profile_name_holding_a_separator_is_refused(tmp_path: Path, name: str) -> None:
    """A name is an INI section, never a path component, and it should not read as one.

    Nothing here builds a path from it, so a separator escapes nothing today. It is refused
    so that a later reader who assumes a name is safe to join onto a directory is right.
    """
    store = ProfileStore(tmp_path)
    store.initialize()
    with pytest.raises(ProfileError, match="slashes"):
        store.add(name)
