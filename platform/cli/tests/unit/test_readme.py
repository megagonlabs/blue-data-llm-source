"""Every `llm` command in the README is run here.

A command in a README that no longer works is a false claim, so the claims are read out
of the file itself rather than copied into the test and left to drift.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest
from blue_data_llm_source import ExecMeta
from click.testing import CliRunner

from blue_data_llm_cli import config
from blue_data_llm_cli.commands import cli

README = Path(__file__).resolve().parents[2] / "README.md"


def readme_commands() -> list[str]:
    """Pull every `llm ...` line out of the README's bash blocks, continuations joined."""
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
        elif line.startswith("llm "):
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
    assert len(readme_commands()) >= 8


def test_the_machine_output_sample_matches_the_default_serializer_shape() -> None:
    body = README.read_text(encoding="utf-8")
    blocks = re.findall(r"```text\n(.*?)\n```", body, re.DOTALL)
    sample = next(json.loads(block) for block in blocks if '"records"' in block)
    documented = sample["meta"]
    actual = config.meta_summary(ExecMeta())

    assert set(documented) == set(actual)
    for field, value in actual.items():
        assert type(documented[field]) is type(value), field


@pytest.mark.parametrize("command", readme_commands(), ids=lambda c: c[:60])
def test_readme_command_runs(command: str, runner: CliRunner) -> None:
    argv = shlex.split(command)
    assert argv[0] == "llm"
    result = runner.invoke(cli, argv[1:])
    assert result.exit_code == 0, f"{command}\n{result.output}"


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
        # initial refusal left no answer, and never from `exit_code_for`.
        config.EXIT_BUDGET: "A ceiling ended the run",
        # Reached the same way, from the result rather than from an exception.
        config.EXIT_PARTIAL: "Extraction returned an answered result",
    }
    for code, cause in documented.items():
        assert f"| {code} |" in text
        assert cause in text


def test_the_readme_states_the_cost_ceiling_contract_it_actually_keeps() -> None:
    """Product-facing spend-control text is a claim, not a wording nit.

    A caller sizing a ceiling against this paragraph sizes it wrong if it is out of date,
    and it has been out of date twice: once promising to refuse the crossing call before
    the code could, and once still describing the settle-afterwards behaviour after the
    code began reserving. Both directions are pinned here.
    """
    prose = README.read_text(encoding="utf-8")

    assert "claimed" in prose, "the ceiling is claimed before the call, and must say so"
    assert "bound" in prose, "a claim is only possible against a bound, not an estimate"
    assert "reconciled" in prose, "and the bound is reconciled once the real cost is known"
    assert "settled after it" not in prose, "that was the pre-reservation contract"
    assert "three characters per token" not in prose, (
        "that was the estimate the bound replaced, and it could be exceeded"
    )
