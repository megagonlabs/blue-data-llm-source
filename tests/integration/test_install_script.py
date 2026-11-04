"""Check supported install commands without invoking pip."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("explicit", [False, True])
def test_install_preserves_editable_commands(explicit: bool, tmp_path: Path) -> None:
    lib = tmp_path / "source" / "lib"
    scripts = lib / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "install.sh"
    shutil.copyfile(ROOT / "lib/scripts/install.sh", script)
    dependency = tmp_path / ("custom dependency/lib" if explicit else "blue-data/lib")
    dependency.mkdir(parents=True)
    (dependency / "pyproject.toml").touch()
    binary = tmp_path / "bin"
    binary.mkdir()
    python = binary / "python"
    python.write_text(
        '#!/bin/bash\nprintf \'%s\\0\' "$@" >> "$COMMAND_LOG"\n'
        "printf '\\n' >> \"$COMMAND_LOG\"\n"
    )
    python.chmod(0o755)
    log = tmp_path / "commands"
    env = {"PATH": str(binary) + os.pathsep + os.defpath, "COMMAND_LOG": str(log)}
    if explicit:
        env["BLUE_DATA_PATH"] = str(dependency)
    result = subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    commands = [line.split("\0")[:-1] for line in log.read_text().splitlines()]
    assert len(commands) == 2
    assert commands[0][:4] == ["-m", "pip", "install", "-e"]
    assert Path(commands[0][4]).resolve() == dependency.resolve()
    assert commands[1] == ["-m", "pip", "install", "-e", f"{lib}[dev,llm]"]


def test_missing_dependency_stops_before_pip(tmp_path: Path) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    python = binary / "python"
    python.write_text('#!/bin/bash\ntouch "$COMMAND_LOG"\nexit 1\n')
    python.chmod(0o755)
    log = tmp_path / "commands"
    env = {
        "PATH": str(binary) + os.pathsep + os.defpath,
        "COMMAND_LOG": str(log),
        "BLUE_DATA_PATH": str(tmp_path / "missing"),
    }
    result = subprocess.run(
        ["bash", str(ROOT / "lib/scripts/install.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "Set BLUE_DATA_PATH" in result.stderr
    assert not log.exists()
