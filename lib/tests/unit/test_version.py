"""The version is declared twice and must not drift."""

from __future__ import annotations

import re
from pathlib import Path

import blue_data_llm_source

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_the_declared_version_matches_the_package() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml has no version"
    assert match.group(1) == blue_data_llm_source.__version__, (
        "pyproject.toml and __version__ disagree. They are maintained by hand, so this "
        "test is what keeps a release from shipping two different numbers."
    )
