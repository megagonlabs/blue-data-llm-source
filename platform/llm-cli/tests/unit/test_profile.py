"""Click behavior for the six profile subcommands.

The tests cover config forms, messages, confirmation, coded errors, and output formats.
Store rules are covered in ``test_profile_adapter``.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

from blue_data_llm_cli import config
from blue_data_llm_cli.commands import cli
from blue_data_llm_cli.profile_adapter import ProfileError, ProfileStore


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


@pytest.mark.parametrize(
    "command",
    [
        ["data", "query", "q", "--format", "json"],
        ["data", "estimate", "q", "--format", "json"],
        ["model", "list", "--format", "json"],
        ["metadata", "inspect", "--format", "json"],
    ],
)
def test_a_stale_selection_refuses_normal_commands_before_source_construction(
    runner: CliRunner,
    cli_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
) -> None:
    assert runner.invoke(cli, ["profile", "list"]).exit_code == 0
    (cli_home / ".selected.profile").write_text("missing")

    def unexpected_source(*args: object, **kwargs: object) -> None:
        pytest.fail("a missing profile reached source construction")

    monkeypatch.setattr(config, "build_source", unexpected_source)
    result = runner.invoke(cli, command, env={"OPENAI_API_KEY": "test-only"})
    assert result.exit_code == config.EXIT_PROFILE, result.output
    assert json.loads(result.stdout)["error"]["code"] == config.PROFILE
    assert "'missing' from .selected.profile" in result.stderr
    assert "profile list" in result.stderr
    assert "profile select NAME" in result.stderr


def test_a_missing_implicit_default_is_refused_without_reinitializing(
    runner: CliRunner, cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runner.invoke(cli, ["profile", "list"]).exit_code == 0
    (cli_home / ".profiles").write_text("")
    (cli_home / ".selected.profile").unlink()

    def unexpected_source(*args: object, **kwargs: object) -> None:
        pytest.fail("a missing profile reached source construction")

    monkeypatch.setattr(config, "build_source", unexpected_source)
    result = runner.invoke(cli, ["data", "query", "q", "--format", "json"])
    assert result.exit_code == config.EXIT_PROFILE
    assert json.loads(result.stdout)["error"]["code"] == config.PROFILE
    assert "'default' from the implicit default" in result.stderr
    assert not (cli_home / ".selected.profile").exists()
    assert profiles(cli_home) == ""

    repaired = runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", "restored"])
    assert repaired.exit_code == 0, repaired.output
    assert ProfileStore(cli_home).values("default")["BLUE_DATA_LLM_MODEL"] == "restored"


@pytest.mark.parametrize(
    "command",
    [
        ["profile", "list", "--format", "json"],
        ["profile", "select", "staging"],
        ["profile", "add", "new"],
        ["profile", "config", "BLUE_DATA_LLM_MODEL", "repaired"],
        ["profile", "remove", "default", "--confirm"],
    ],
)
def test_profile_recovery_commands_work_with_a_stale_selection(
    runner: CliRunner, cli_home: Path, command: list[str]
) -> None:
    assert runner.invoke(cli, ["profile", "add", "staging"]).exit_code == 0
    (cli_home / ".selected.profile").write_text("missing")

    result = runner.invoke(cli, command)
    assert result.exit_code == 0, result.output
    if command[1] == "config":
        assert ProfileStore(cli_home).values("missing")["BLUE_DATA_LLM_MODEL"] == "repaired"
    if command[1] == "select":
        assert ProfileStore(cli_home).selected() == "staging"


def test_a_valid_override_keeps_its_precedence_over_a_stale_selection(
    runner: CliRunner, cli_home: Path
) -> None:
    assert runner.invoke(cli, ["profile", "add", "staging"]).exit_code == 0
    assert (
        runner.invoke(
            cli, ["profile", "config", "--profile", "staging", "BLUE_DATA_LLM_MODEL", "stored"]
        ).exit_code
        == 0
    )
    (cli_home / ".selected.profile").write_text("missing")

    selected_by_environment = runner.invoke(
        cli, ["data", "query", "q", "--format", "json"], env={"BLUE_DATA_PROFILE": "staging"}
    )
    assert selected_by_environment.exit_code == 0, selected_by_environment.output
    assert json.loads(selected_by_environment.stdout)["settings"]["source"]["llm.model"] == (
        "profile:staging"
    )

    selected_by_flag = runner.invoke(
        cli,
        ["--profile", "default", "data", "query", "q", "--format", "json"],
        env={"BLUE_DATA_PROFILE": "staging", "BLUE_DATA_LLM_MODEL": "from-environment"},
    )
    assert selected_by_flag.exit_code == 0, selected_by_flag.output
    settings = json.loads(selected_by_flag.stdout)["settings"]
    assert settings["source"]["llm.model"] == "environment"
    assert settings["applied"]["llm.model"] == "from-environment"


def test_an_empty_export_is_refused_rather_than_dropped_to_the_profile(runner: CliRunner) -> None:
    """An empty export must be refused, even when the profile has a value."""
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


@pytest.mark.parametrize(
    ("parameter", "value"),
    [("BLUE_DATA_CLI_FORMAT", "xml"), ("BLUE_DATA_LLM_SOURCE_EFFORT", "ultra")],
)
def test_profile_config_refuses_invalid_enums_before_storing(
    runner: CliRunner, cli_home: Path, parameter: str, value: str
) -> None:
    result = runner.invoke(cli, ["profile", "config", parameter, value])
    assert result.exit_code == config.EXIT_PROFILE
    assert parameter in result.stderr
    assert value not in profiles(cli_home)


@pytest.mark.parametrize(
    ("parameter", "bad", "good"),
    [
        ("BLUE_DATA_CLI_FORMAT", "xml", "json"),
        ("BLUE_DATA_LLM_SOURCE_EFFORT", "ultra", "low"),
    ],
)
def test_existing_invalid_enum_can_be_repaired_with_profile_config(
    runner: CliRunner, cli_home: Path, parameter: str, bad: str, good: str
) -> None:
    assert runner.invoke(cli, ["profile", "list"]).exit_code == 0
    ProfileStore(cli_home).set_value("default", parameter, bad)

    broken = runner.invoke(cli, ["data", "query", "q"])
    assert broken.exit_code == config.EXIT_PROFILE
    assert f"profile config {parameter} VALUE" in broken.stderr

    repaired = runner.invoke(cli, ["profile", "config", parameter, good])
    assert repaired.exit_code == 0, repaired.output
    assert ProfileStore(cli_home).values("default")[parameter] == good
    assert runner.invoke(cli, ["data", "estimate", "q", "--format", "json"]).exit_code == 0


def test_profile_inspect_table_shows_full_parameter_names(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["profile", "inspect"])
    assert result.exit_code == 0
    assert "BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN" in result.stdout
    assert "Source" in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        ["metadata", "inspect", "--format", "json"],
        ["model", "list", "--format", "json"],
        ["model", "inspect", "gpt-4o", "--format", "json"],
        ["data", "estimate", "q", "--format", "json"],
    ],
)
def test_read_only_commands_use_defaults_if_absent_home_cannot_be_created(
    runner: CliRunner, tmp_path: Path, command: list[str]
) -> None:
    parent = tmp_path / "read-only"
    parent.mkdir(mode=0o555)
    parent.chmod(0o555)
    home = parent / "home"
    try:
        result = runner.invoke(cli, command, env={config.HOME_VARIABLE: str(home)})
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)
        assert not home.exists()
    finally:
        parent.chmod(0o700)


def test_absent_uncreatable_home_still_refuses_a_query_and_explicit_profile(
    runner: CliRunner, tmp_path: Path
) -> None:
    parent = tmp_path / "read-only"
    parent.mkdir(mode=0o555)
    parent.chmod(0o555)
    home = parent / "home"
    try:
        for command in (
            ["data", "query", "q", "--format", "json"],
            ["--profile", "named", "metadata", "inspect", "--format", "json"],
        ):
            result = runner.invoke(cli, command, env={config.HOME_VARIABLE: str(home)})
            assert result.exit_code == config.EXIT_PROFILE
            assert json.loads(result.stdout)["error"]["code"] == config.PROFILE
        assert not home.exists()
    finally:
        parent.chmod(0o700)


def test_existing_unsafe_home_is_not_treated_as_an_empty_profile(
    runner: CliRunner, tmp_path: Path
) -> None:
    home = tmp_path / "unsafe"
    home.mkdir(mode=0o777)
    home.chmod(0o777)
    try:
        for command in (
            ["metadata", "inspect", "--format", "json"],
            ["model", "inspect", "gpt-4o", "--format", "json"],
        ):
            result = runner.invoke(cli, command, env={config.HOME_VARIABLE: str(home)})
            assert result.exit_code == config.EXIT_PROFILE
            assert json.loads(result.stdout)["error"]["code"] == config.PROFILE
        assert not (home / ".profiles").exists()
    finally:
        home.chmod(0o700)


def test_profile_select_write_failure_has_no_missing_profile_hint(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runner.invoke(cli, ["profile", "add", "staging"]).exit_code == 0

    def refused(_store: ProfileStore, _name: str) -> None:
        raise ProfileError("cannot write selection: Permission denied")

    monkeypatch.setattr(ProfileStore, "select", refused)
    result = runner.invoke(cli, ["profile", "select", "staging"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "cannot write selection" in result.stderr
    assert "profile list" not in result.stderr


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
    assert "blue-llmdb profile select NAME" in result.stderr
    assert runner.invoke(cli, ["profile", "select", "staging"]).exit_code == 0
    selected = json.loads(runner.invoke(cli, ["profile", "list", "--format", "json"]).stdout)
    assert selected["selected"] == "staging"


# ------------------------------------------------------------------ remove --
def test_remove_asks_first(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    result = runner.invoke(cli, ["profile", "remove", "staging"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Removed profile: staging" in result.stdout


def test_declining_removes_nothing_and_is_not_a_failure(runner: CliRunner) -> None:
    """Declining removal leaves the profile in place and exits successfully."""
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
    """The last profile cannot be removed while it is the selected profile."""
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
    assert "blue-llmdb profile inspect --format json" in result.stderr
    inspect = runner.invoke(cli, ["profile", "inspect", "--format", "json"])
    assert inspect.exit_code == 0
    assert "BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN" in json.loads(inspect.stdout)["values"]


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
    """A rejected setting leaves no newly created target profile."""
    result = runner.invoke(cli, ["profile", "config", "--profile", "typo", *argument])
    assert result.exit_code == config.EXIT_PROFILE
    assert "Created profile" not in result.stdout
    payload = json.loads(runner.invoke(cli, ["profile", "list", "--format", "json"]).stdout)
    assert "typo" not in payload["profiles"]


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("BLUE_DATA_LLM_SOURCE_MAX_RECORDS", "many", "a whole number"),
        ("BLUE_DATA_LLM_SOURCE_TEMPERATURE", "warm", "a number"),
        ("BLUE_DATA_LLM_SOURCE_CACHE", "maybe", "true or false"),
    ],
)
def test_a_cast_failure_names_the_parameter(
    runner: CliRunner, name: str, value: str, expected: str
) -> None:
    result = runner.invoke(cli, ["profile", "config", name, value])
    assert result.exit_code == config.EXIT_PROFILE
    assert f"{name} must be {expected}, got {value!r}" in result.stderr


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


def test_the_key_prompt_describes_this_cli(runner: CliRunner) -> None:
    """The shared prompt mentions embeddings, which this CLI does not use."""
    result = runner.invoke(cli, ["profile", "config", "OPENAI_API_KEY"], input="\n")
    assert result.exit_code == 0, result.output
    assert "OpenAI API key (the provider SDK reads it from the environment)" in result.stdout
    assert "embeddings" not in result.stdout


# -------------------------------------------------------------- hand edits --
@pytest.mark.parametrize(
    ("line", "repair", "answer"),
    [
        ("BLUE_DATA_LLM_SOURCE_LIMIT_COST =", ["BLUE_DATA_LLM_SOURCE_LIMIT_COST", "1"], None),
        # Valid base64 that does not decode as UTF-8.
        ("OPENAI_API_KEY = enc:/w==", ["OPENAI_API_KEY"], "replacement-key\n"),
    ],
    ids=["blank-value", "undecodable-secret"],
)
def test_profile_commands_repair_what_data_commands_refuse(
    runner: CliRunner, cli_home: Path, line: str, repair: list[str], answer: str | None
) -> None:
    """A hand-edited value that cannot apply stops a request, not the commands that fix it."""
    assert runner.invoke(cli, ["profile", "list"]).exit_code == 0
    (cli_home / ".profiles").write_text(f"{profiles(cli_home).rstrip()}\n{line}\n")

    refused = runner.invoke(cli, ["data", "estimate", "q", "--format", "json"])
    assert refused.exit_code == config.EXIT_PROFILE
    assert repair[0] in json.loads(refused.stdout)["error"]["message"]

    for command in (
        ["profile", "list"],
        ["profile", "add", "spare"],
        ["profile", "select", "default"],
    ):
        result = runner.invoke(cli, command)
        assert result.exit_code == 0, (command, result.output)
    assert runner.invoke(cli, ["profile", "remove", "spare", "--confirm"]).exit_code == 0
    fixed = runner.invoke(cli, ["profile", "config", *repair], input=answer)
    assert fixed.exit_code == 0, fixed.output

    estimate = runner.invoke(cli, ["data", "estimate", "q", "--format", "json"])
    assert estimate.exit_code == 0, estimate.output


def test_a_blank_stored_value_names_its_profile_and_file(runner: CliRunner, cli_home: Path) -> None:
    assert runner.invoke(cli, ["profile", "add", "staging"]).exit_code == 0
    path = cli_home / ".profiles"
    path.write_text(f"{profiles(cli_home).rstrip()}\nBLUE_DATA_LLM_SOURCE_LIMIT_CALLS =\n")

    result = runner.invoke(cli, ["--profile", "staging", "data", "estimate", "q"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "BLUE_DATA_LLM_SOURCE_LIMIT_CALLS is blank in profile 'staging'" in result.stderr
    assert "/.blue-llmdb/.profiles;" in result.stderr
    assert "profile config BLUE_DATA_LLM_SOURCE_LIMIT_CALLS VALUE --profile NAME" in result.stderr
    # The selected profile holds no blank, so it still runs.
    assert runner.invoke(cli, ["data", "estimate", "q"]).exit_code == 0


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
def test_the_profile_listings_honor_every_format(fmt: str, runner: CliRunner) -> None:
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
