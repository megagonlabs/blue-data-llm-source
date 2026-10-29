"""Every `blue-llmdb` command in the README is run here.

A command in a README that no longer works is a false claim, so the claims are read out of
the file itself rather than copied into the test and left to drift. They run in the order
they appear, against one throwaway CLI home, because the README is a walkthrough: a reader
types them in sequence and the sequence has to work.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import click
from blue_data_llm_source import ExecMeta, meta_view
from click.testing import CliRunner

from blue_data_llm_cli import config
from blue_data_llm_cli.commands import cli

README = Path(__file__).resolve().parents[2] / "README.md"


def readme_commands() -> list[str]:
    """Pull every `blue-llmdb ...` line out of the README's bash blocks, continuations joined."""
    commands: list[str] = []
    pending = ""
    in_bash = False
    for raw in README.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_bash = line == "```bash"
            pending = ""
            continue
        if not in_bash or not line:
            continue
        if pending:
            pending = f"{pending} {line}"
        elif line.startswith("blue-llmdb "):
            pending = line
        else:
            continue
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        commands.append(pending)
        pending = ""
    return commands


def test_the_readme_still_has_commands_to_check() -> None:
    assert len(readme_commands()) >= 25


def test_every_readme_command_runs_in_the_order_it_is_shown(runner: CliRunner) -> None:
    for command in readme_commands():
        argv = shlex.split(command)
        assert argv[0] == "blue-llmdb"
        # A blank answer: the two prompting forms keep the current value and change nothing,
        # which is the behavior a reader gets by pressing enter.
        result = runner.invoke(cli, argv[1:], input="\n\n")
        assert result.exit_code == 0, f"{command}\n{result.output}\n{result.stderr}"


def test_every_component_is_exercised_by_the_walkthrough() -> None:
    shown = {shlex.split(command)[1] for command in readme_commands()}
    assert {"profile", "data", "model", "metadata"} <= shown


def test_the_machine_output_sample_matches_the_default_serializer_shape() -> None:
    body = README.read_text(encoding="utf-8")
    blocks = re.findall(r"```text\n(.*?)\n```", body, re.DOTALL)
    sample = next(json.loads(block) for block in blocks if '"records"' in block)

    assert set(sample) == {"records", "meta", "settings"}
    assert set(sample["settings"]) == {"applied", "source"}

    documented = sample["meta"]
    actual = meta_view(ExecMeta())
    assert set(documented) == set(actual)
    for field, value in actual.items():
        assert type(documented[field]) is type(value), field


def test_the_documented_exit_codes_are_the_ones_in_use() -> None:
    """The table in the README is the contract; config.py is where it is implemented."""
    text = README.read_text(encoding="utf-8")
    documented = {
        config.EXIT_OK: "Success",
        config.EXIT_ERROR: "LlmSourceError",
        config.EXIT_USAGE: "Usage error",
        config.EXIT_QUERY: "QueryError",
        config.EXIT_CONFIG: "ConfigError",
        config.EXIT_TRANSPORT: "TransportError",
        config.EXIT_PARSE: "ParseError",
        # Not the exception class. This code is reached from a result whose outcome says an
        # initial refusal left no answer, and never from `code_for`.
        config.EXIT_BUDGET: "A ceiling ended the run",
        # Reached the same way, from the result rather than from an exception.
        config.EXIT_PARTIAL: "Extraction returned an answered result",
    }
    for code, cause in documented.items():
        assert f"| {code} |" in text
        assert cause in text


def test_every_error_code_token_is_documented() -> None:
    text = README.read_text(encoding="utf-8")
    for code in config.EXIT_FOR_CODE:
        assert f"`{code}`" in text, code


def test_the_readme_states_the_cost_ceiling_contract_it_actually_keeps() -> None:
    """Keep README ceiling claims aligned with reservation and settlement."""
    prose = README.read_text(encoding="utf-8")

    assert "claimed" in prose, "the ceiling is claimed before the call, and must say so"
    assert "bound" in prose, "a claim is only possible against a bound, not an estimate"
    assert "reconciled" in prose, "and the bound is reconciled once the real cost is known"
    assert "settled after it" not in prose, "that was the pre-reservation contract"
    assert "three characters per token" not in prose, (
        "that was the estimate the bound replaced, and it could be exceeded"
    )


def test_the_readme_states_the_budget_guarantee_the_profile_changed() -> None:
    """A profile now supplies each request's default, which a reader has to be told."""
    prose = README.read_text(encoding="utf-8")
    assert "A ceiling applies to one request." in prose
    assert "A profile supplies the default for each request." in prose
    assert "Every run prints which ceilings applied and where each came from" in prose


def test_the_readme_states_the_precedence_chain(runner: CliRunner) -> None:
    prose = README.read_text(encoding="utf-8")
    assert "command option > environment variable > profile > built-in default" in prose


def long_options(command: click.Command, seen: set[str] | None = None) -> set[str]:
    """Every `--option` the live tree declares, at any depth."""
    found: set[str] = set() if seen is None else seen
    for parameter in command.params:
        found.update(
            option
            for option in [*parameter.opts, *parameter.secondary_opts]
            if option.startswith("--")
        )
    if isinstance(command, click.Group):
        for child in command.commands.values():
            long_options(child, found)
    return found


def test_the_readme_has_heard_of_every_option_the_cli_declares() -> None:
    """A flag the manual has never heard of is one a reader cannot find.

    Derived from the tree rather than from a list kept beside it, because a list beside it
    is the thing that went out of date: `--variant` and `--plan-leaves` were documented on
    the copy of this page under `docs/` and missing from the page it was copied from.
    """
    prose = README.read_text(encoding="utf-8")
    missing = sorted(
        flag for flag in long_options(cli) if not re.search(re.escape(flag) + r"(?![\w-])", prose)
    )
    assert missing == []


def test_the_readme_names_the_old_entry_point_nowhere() -> None:
    """`llm` is removed, not aliased, so a reader must not find it here."""
    prose = README.read_text(encoding="utf-8")
    assert not re.search(r"^\s*llm (fetch|info)\b", prose, re.M)
