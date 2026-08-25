"""The version is declared twice per package and quoted in three documents.

Six declarations, all maintained by hand. This file checked one pair of them, so the two
interface packages went out declaring 1.1.0 to `pip` and exporting 1.0.0 from `__version__`:
anything introspecting an installed interface reported the previous release while `llm info`
reported the current one. Checking the library alone is what let that happen, so all three
pairs are checked here.

The interface pairs are read as text rather than imported. This suite runs in the library's
environment, where neither interface package is installed, and a check that silently skips
where it is most needed is the drift it was written to stop. The library keeps its imported
check as well, since that is the one environment where the exported value is reachable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import blue_data_llm_source

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = ROOT / "lib" / "pyproject.toml"

# Name, packaging metadata, module holding `__version__`.
PACKAGES = [
    (
        "blue-data-llm-source",
        "lib/pyproject.toml",
        "lib/src/blue_data_llm_source/__init__.py",
    ),
    (
        "blue-data-llm-cli",
        "platform/cli/pyproject.toml",
        "platform/cli/src/blue_data_llm_cli/__init__.py",
    ),
    (
        "blue-data-llm-mcp",
        "platform/mcp/pyproject.toml",
        "platform/mcp/src/blue_data_llm_mcp/__init__.py",
    ),
]

# The two shapes an `llm info` or `llm_info` answer takes in a document: the CLI's aligned
# text block and the server's JSON. Both are read as fact by whoever is checking which
# release they are on.
QUOTED_VERSION = re.compile(r'^version\s{2,}(\d+\.\d+\.\d+)\s*$|"version":\s*"(\d+\.\d+\.\d+)"')

SKIP = {".venv", "build", "__pycache__", ".pytest_cache", ".ruff_cache", "cassettes"}


def _declared(relative: str) -> str:
    path = ROOT / relative
    match = re.search(r'^version\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"{relative} has no version"
    return match.group(1)


def _exported(relative: str) -> str:
    path = ROOT / relative
    match = re.search(
        r'^__version__\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert match, f"{relative} has no __version__"
    return match.group(1)


def test_the_declared_version_matches_the_package() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml has no version"
    assert match.group(1) == blue_data_llm_source.__version__, (
        "pyproject.toml and __version__ disagree. They are maintained by hand, so this "
        "test is what keeps a release from shipping two different numbers."
    )


@pytest.mark.parametrize(("name", "pyproject", "module"), PACKAGES, ids=[p[0] for p in PACKAGES])
def test_every_package_exports_the_version_its_packaging_declares(
    name: str, pyproject: str, module: str
) -> None:
    assert _declared(pyproject) == _exported(module), (
        f"{name} declares {_declared(pyproject)} in {pyproject} and exports "
        f"{_exported(module)} from {module}. A release bump that reaches the packaging and "
        "not the module ships a package that reports one number to pip and another to "
        "import, which is what the interface packages did."
    )


def test_every_documented_version_output_quotes_the_current_release() -> None:
    """The example outputs are the version a reader believes they are running.

    Scanned rather than listed, because a listed set has the same failure as correcting the
    declarations one at a time: the document nobody remembered stays stale.
    """
    current = blue_data_llm_source.__version__
    stale: list[str] = []
    found = 0
    for path in ROOT.rglob("*.md"):
        if SKIP & set(path.relative_to(ROOT).parts):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = QUOTED_VERSION.search(line)
            if match is None:
                continue
            found += 1
            quoted = match.group(1) or match.group(2)
            if quoted != current:
                stale.append(f"{path.relative_to(ROOT)}:{number} quotes {quoted}")
    assert found >= 3, f"the scan found only {found} version outputs; it is not reaching them"
    assert not stale, (
        f"the library exports {current} and these outputs say otherwise:\n  " + "\n  ".join(stale)
    )
