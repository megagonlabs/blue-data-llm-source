"""The shape of the CLI itself: the tree, the version, the formats, the precedence chain.

These are the claims the compliance checklist makes about every command at once, so they
are checked against every command at once rather than one at a time in the command's own
file.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from blue_data_llm_cli import config
from blue_data_llm_cli.commands import cli
from blue_data_llm_cli.formats import FORMATS

QUERY = "the member states of the European Union"
PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

TREE = {
    "profile": ["add", "remove", "list", "config", "inspect", "select"],
    "data": ["query", "estimate"],
    "model": ["list", "inspect", "select"],
    "metadata": ["inspect"],
}

# Every command that returns a result, and therefore carries `--format`.
RESULT_COMMANDS: list[list[str]] = [
    ["profile", "list"],
    ["profile", "inspect"],
    ["data", "query", QUERY],
    ["data", "estimate", QUERY],
    ["model", "list"],
    ["model", "inspect", "gpt-4o"],
    ["metadata", "inspect"],
]


def leaves(group: click.Group) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name, command in group.commands.items():
        assert isinstance(command, click.Group), f"{name} is not a component group"
        found[name] = sorted(command.commands)
    return found


# ----------------------------------------------------------------- the tree --
def test_the_tree_is_four_components_and_twelve_commands() -> None:
    assert leaves(cli) == {name: sorted(actions) for name, actions in TREE.items()}
    assert sum(len(actions) for actions in TREE.values()) == 12


@pytest.mark.parametrize("component", sorted(TREE))
def test_the_root_help_names_every_component(component: str, runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0, result.output
    assert component in result.stdout


@pytest.mark.parametrize(
    ("component", "action"),
    [(component, action) for component, actions in TREE.items() for action in actions],
)
def test_every_command_has_help_of_its_own(component: str, action: str, runner: CliRunner) -> None:
    result = runner.invoke(cli, [component, action, "--help"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip()


def test_the_root_help_states_the_precedence_chain(runner: CliRunner) -> None:
    text = runner.invoke(cli, ["--help"]).stdout
    for tier in ("command option", "environment", "profile", "default"):
        assert tier in text


@pytest.mark.parametrize("command", [["data", "query"], ["data", "estimate"]], ids=lambda c: c[1])
def test_each_affected_command_states_the_precedence_chain(
    command: list[str], runner: CliRunner
) -> None:
    text = " ".join(runner.invoke(cli, [*command, "--help"]).stdout.split())
    assert "command option first, then the environment, then the profile" in text


def test_the_chain_is_claimed_only_for_the_settings_that_have_one(runner: CliRunner) -> None:
    """`data query --help` promised the chain for "every setting below" it.

    All fourteen `--plan-*` controls sit below it, no profile parameter carries a plan
    setting, and `--effort` is the only planning-adjacent option that reads configuration.
    """
    from blue_data_llm_cli.commands import data_query
    from blue_data_llm_cli.profile import cli_spec

    plan_flags = {
        option
        for parameter in data_query.params
        for option in parameter.opts
        if option.startswith("--plan-")
    }
    assert len(plan_flags) == 14
    assert not [name for name in cli_spec().names() if "PLAN" in name]

    text = " ".join(runner.invoke(cli, ["data", "query", "--help"]).stdout.split())
    assert "Every setting below" not in text
    assert "`--plan-*` controls are not among them" in text


def test_help_is_reachable_by_both_spellings(runner: CliRunner) -> None:
    assert runner.invoke(cli, ["-h"]).exit_code == 0
    assert runner.invoke(cli, ["--help"]).exit_code == 0


# -------------------------------------------------------------- the version --
def test_version_prints_the_program_name_and_the_packaged_version(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0, result.output
    declared = re.search(r'^version = "([^"]+)"', PYPROJECT.read_text(), re.M)
    assert declared is not None
    assert result.stdout.strip() == f"blue-llmdb {declared.group(1)}"


# -------------------------------------------------------------- the formats --
@pytest.mark.parametrize("command", RESULT_COMMANDS, ids=lambda c: "-".join(c[:2]))
@pytest.mark.parametrize("fmt", FORMATS)
def test_every_command_honours_every_format(
    command: list[str], fmt: str, runner: CliRunner
) -> None:
    result = runner.invoke(cli, [*command, "--format", fmt])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip(), f"{command} produced nothing in {fmt}"
    if fmt == "json":
        json.loads(result.stdout)
    if fmt == "csv":
        rows = list(csv.reader(io.StringIO(result.stdout)))
        assert rows and rows[0], "csv produced no header"


@pytest.mark.parametrize("command", RESULT_COMMANDS, ids=lambda c: "-".join(c[:2]))
def test_an_unknown_format_is_refused_before_anything_runs(
    command: list[str], runner: CliRunner
) -> None:
    result = runner.invoke(cli, [*command, "--format", "yaml"])
    assert result.exit_code == config.EXIT_USAGE
    assert "ERROR: USAGE" in result.stderr


@pytest.mark.parametrize("command", RESULT_COMMANDS, ids=lambda c: "-".join(c[:2]))
def test_the_default_format_comes_from_the_profile(command: list[str], runner: CliRunner) -> None:
    assert runner.invoke(cli, ["profile", "config", "BLUE_DATA_CLI_FORMAT", "json"]).exit_code == 0
    result = runner.invoke(cli, command)
    assert result.exit_code == 0, result.output
    json.loads(result.stdout)


def test_a_configured_format_the_cli_does_not_have_is_a_profile_error(runner: CliRunner) -> None:
    assert runner.invoke(cli, ["profile", "config", "BLUE_DATA_CLI_FORMAT", "yaml"]).exit_code == 0
    result = runner.invoke(cli, ["model", "list"])
    assert result.exit_code == config.EXIT_PROFILE
    assert "ERROR: PROFILE" in result.stderr
    assert "BLUE_DATA_CLI_FORMAT" in result.stderr


# ----------------------------------------------------------- the four tiers --
def test_a_command_option_wins_over_everything(runner: CliRunner, default_client: Any) -> None:
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", "from-profile"])
    result = runner.invoke(
        cli,
        ["data", "query", QUERY, "--model", "from-the-flag", "--format", "json"],
        env={"BLUE_DATA_LLM_MODEL": "from-the-environment"},
    )
    assert result.exit_code == 0, result.output
    settings = json.loads(result.stdout)["settings"]
    assert settings["applied"]["llm.model"] == "from-the-flag"
    assert settings["source"]["llm.model"] == "flag"


def test_the_environment_wins_over_the_profile(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", "from-profile"])
    result = runner.invoke(
        cli,
        ["data", "query", QUERY, "--format", "json"],
        env={"BLUE_DATA_LLM_MODEL": "from-the-environment"},
    )
    settings = json.loads(result.stdout)["settings"]
    assert settings["applied"]["llm.model"] == "from-the-environment"
    assert settings["source"]["llm.model"] == "environment"


def test_the_profile_wins_over_the_default(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", "from-profile"])
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])
    settings = json.loads(result.stdout)["settings"]
    assert settings["applied"]["llm.model"] == "from-profile"
    assert settings["source"]["llm.model"] == "profile:default"


def test_the_built_in_default_is_the_last_tier(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", "json"])
    settings = json.loads(result.stdout)["settings"]
    assert settings["applied"]["llm.model"] == "gpt-4o"
    assert settings["source"]["llm.model"] == "default"


def test_the_root_profile_option_reads_blue_data_profile(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    runner.invoke(cli, ["profile", "config", "--profile", "staging", "BLUE_DATA_LLM_MODEL", "s"])
    result = runner.invoke(
        cli, ["data", "query", QUERY, "--format", "json"], env={"BLUE_DATA_PROFILE": "staging"}
    )
    settings = json.loads(result.stdout)["settings"]
    assert settings["source"]["llm.model"] == "profile:staging"


def test_the_root_profile_option_beats_the_variable(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "add", "staging"])
    runner.invoke(cli, ["profile", "config", "--profile", "staging", "BLUE_DATA_LLM_MODEL", "s"])
    result = runner.invoke(
        cli,
        ["--profile", "default", "data", "query", QUERY, "--format", "json"],
        env={"BLUE_DATA_PROFILE": "staging"},
    )
    settings = json.loads(result.stdout)["settings"]
    assert settings["source"]["llm.model"] == "default"


def test_a_named_profile_that_does_not_exist_is_refused(runner: CliRunner) -> None:
    """A misspelled name would run with none of the ceilings the profile holds."""
    result = runner.invoke(cli, ["--profile", "ghost", "data", "query", QUERY])
    assert result.exit_code == config.EXIT_PROFILE
    assert "no such profile: 'ghost'" in result.stderr
    assert "profile add ghost" in result.stderr


def test_the_same_refusal_reaches_the_variable(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["model", "list"], env={"BLUE_DATA_PROFILE": "ghost"})
    assert result.exit_code == config.EXIT_PROFILE


# ------------------------------------------------------ stdout stays clean --
@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_a_log_run_does_not_write_to_stdout(fmt: str, runner: CliRunner) -> None:
    """Both siblings corrupt their JSON under `--log`. This one is checked, not assumed."""
    result = runner.invoke(cli, ["data", "query", QUERY, "--format", fmt, "--log"])
    assert result.exit_code == 0, result.output
    if fmt == "json":
        json.loads(result.stdout)
    assert result.stderr.strip(), "--log wrote nothing at all"


def test_log_lines_are_truncated_to_the_limit(runner: CliRunner) -> None:
    long_query = "why " * 200
    result = runner.invoke(cli, ["data", "query", long_query, "--log", "--log-limit", "40"])
    assert result.exit_code == 0, result.output
    for line in result.stderr.splitlines():
        assert len(line) <= 40, line


def test_the_log_limit_falls_back_to_the_profile(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_CLI_LOG_LIMIT", "20"])
    result = runner.invoke(cli, ["data", "query", "why " * 200, "--log"])
    for line in result.stderr.splitlines():
        assert len(line) <= 20, line


def test_a_run_leaves_the_environment_as_it_found_it(runner: CliRunner) -> None:
    """The profile is exported for the duration of the run, not for the life of the process."""
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini"])
    before = dict(os.environ)
    assert runner.invoke(cli, ["data", "query", QUERY]).exit_code == 0
    assert dict(os.environ) == before


def test_a_configured_log_limit_of_zero_is_a_limit_of_zero(runner: CliRunner) -> None:
    """Not `unset`. The same value read two ways depending on the tier is the bug."""
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_CLI_LOG_LIMIT", "0"])
    result = runner.invoke(cli, ["data", "query", "why " * 200, "--log"])
    assert result.exit_code == 0, result.output
    for line in result.stderr.splitlines():
        assert line.strip() in {"", "..."}, line


@pytest.mark.parametrize("value", ["0", "-5"])
def test_an_allowance_floor_holds_on_every_tier(value: str, runner: CliRunner) -> None:
    """The range on the option only guards the option; the provider is reached either way."""
    result = runner.invoke(
        cli, ["data", "query", QUERY], env={"BLUE_DATA_LLM_SOURCE_MAX_TOKENS": value}
    )
    assert result.exit_code == config.EXIT_PROFILE, result.output
    assert "llm.max_tokens" in result.stderr
    assert "environment" in result.stderr


def test_a_row_ceiling_floor_holds_on_every_tier(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_SOURCE_MAX_RECORDS", "0"])
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert result.exit_code == config.EXIT_PROFILE
    assert "limits.max_rows" in result.stderr


def test_a_configured_effort_is_blamed_on_the_parameter_that_set_it(runner: CliRunner) -> None:
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_LLM_SOURCE_EFFORT", "high"])
    result = runner.invoke(cli, ["data", "query", QUERY])
    assert result.exit_code == config.EXIT_USAGE
    assert "BLUE_DATA_LLM_SOURCE_EFFORT" in result.stderr
    assert "--plan-key" in result.stderr


def test_progress_is_suppressed_under_a_machine_format() -> None:
    assert config.Reporter("json").machine_readable
    assert config.Reporter("csv").machine_readable
    assert not config.Reporter("table").machine_readable
    assert not config.Reporter("nl").machine_readable


def test_a_suppressed_spinner_is_a_no_op_context() -> None:
    with config.Reporter("json").status("Querying…"):
        pass


# ------------------------------------------------------- nothing blocks --
@pytest.mark.parametrize(
    "command",
    [
        ["profile", "list"],
        ["profile", "inspect"],
        ["model", "list"],
        ["model", "inspect", "gpt-4o"],
        ["metadata", "inspect"],
        ["data", "estimate", QUERY],
    ],
    ids=lambda c: "-".join(c[:2]),
)
def test_the_offline_commands_run_with_an_empty_profile(
    command: list[str], runner: CliRunner
) -> None:
    """No parameter gates a command. A missing credential surfaces only at the provider."""
    result = runner.invoke(cli, command)
    assert result.exit_code == 0, result.output


def test_every_resource_beside_the_modules_is_declared_package_data() -> None:
    """A file loaded with `Path(__file__).with_name(...)` has to be declared to ship.

    `profile_spec.json` is read on every invocation, and setuptools ships a non-Python file
    only when `package-data` names it. Without the declaration the source tree and an
    editable install both work and a wheel install raises `FileNotFoundError` on the first
    command, so no test that imports from source can see the gap.
    """
    package = Path(config.__file__).parent
    manifest = (package.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    pattern = (
        r"^\[tool\.setuptools\.package-data\]\s*\n" + package.name + r"\s*=\s*\[(?P<names>[^\]]*)\]"
    )
    block = re.search(pattern, manifest, re.MULTILINE)
    declared: set[str] = set(re.findall(r'"([^"]+)"', block["names"])) if block else set()
    present = {p.name for p in package.iterdir() if p.is_file() and p.suffix != ".py"}
    assert present <= declared, f"undeclared package data: {sorted(present - declared)}"
