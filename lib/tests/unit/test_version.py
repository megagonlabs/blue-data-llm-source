"""The version is declared twice per package and quoted in three documents.

Six declarations, all maintained by hand. This file checked one pair of them, so the two
interface packages went out declaring 1.1.0 to `pip` and exporting 1.0.0 from `__version__`:
anything introspecting an installed interface reported the previous release while the
metadata that interface printed reported the current one. Checking the library alone is what
let that happen, so all three pairs are checked here.

The interface pairs are read as text rather than imported. This suite runs in the library's
environment, where neither interface package is installed, and a check that silently skips
where it is most needed is the drift it was written to stop. The library keeps its imported
check as well, since that is the one environment where the exported value is reachable.
"""

from __future__ import annotations

import re
import stat
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
        "platform/llm-cli/pyproject.toml",
        "platform/llm-cli/src/blue_data_llm_cli/__init__.py",
    ),
    (
        "blue-data-llm-mcp",
        "platform/llm-mcp/pyproject.toml",
        "platform/llm-mcp/src/blue_data_llm_mcp/__init__.py",
    ),
]

# The shapes a version output reaches a reader in: the table `blue-llmdb metadata inspect`
# renders, and the JSON object it returns under `--format json`, which is also what the
# server's `llmdb_inspect_metadata` returns. Both are read as fact by whoever is checking
# which release they are on.
#
# Kept as a map so each shape is counted separately. The aligned text block that stood here
# in place of the table matched nothing anywhere once the command stopped printing one, and
# the scan went on passing on the strength of the other shape alone.
VERSION_OUTPUTS = {
    "the metadata table": re.compile(r"^\s*\u2502\s*version\s*\u2502\s*(\d+\.\d+\.\d+)\s*\u2502"),
    "the metadata JSON": re.compile(r'"version":\s*"(\d+\.\d+\.\d+)"'),
    "the CLI version line": re.compile(r"^blue-llmdb (\d+\.\d+\.\d+)$"),
}

SKIP = {".venv", "build", "__pycache__", ".pytest_cache", ".ruff_cache", "cassettes"}

RELEASE_SCRIPTS = (
    "lib/scripts/build.sh",
    "lib/scripts/install.sh",
    "lib/scripts/publish.sh",
    "platform/llm-cli/scripts/build.sh",
    "platform/llm-cli/scripts/install.sh",
    "platform/llm-cli/scripts/publish.sh",
    "platform/llm-mcp/scripts/build.sh",
    "platform/llm-mcp/scripts/install.sh",
    "platform/llm-mcp/scripts/publish.sh",
)


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


@pytest.mark.parametrize(("name", "pyproject", "_module"), PACKAGES, ids=[p[0] for p in PACKAGES])
def test_every_package_declares_its_readme(name: str, pyproject: str, _module: str) -> None:
    project = ROOT / pyproject
    text = project.read_text(encoding="utf-8")
    assert re.search(r'^readme\s*=\s*"README\.md"$', text, re.MULTILINE), (
        f"{name} does not declare its README as package metadata"
    )
    assert (project.parent / "README.md").is_file()


def test_every_documented_version_output_quotes_the_current_release() -> None:
    """The example outputs are the version a reader believes they are running.

    Scanned rather than listed, because a listed set has the same failure as correcting the
    declarations one at a time: the document nobody remembered stays stale.

    Every shape has to be found, rather than the total having to clear a number. A total is
    met by whichever shape happens to be common, so a shape that stops matching takes its
    documents out of the scan and the count stays legal — which is how a version bump came
    to leave a documented output behind with nothing failing.
    """
    current = blue_data_llm_source.__version__
    stale: list[str] = []
    found = dict.fromkeys(VERSION_OUTPUTS, 0)
    documents = 0
    for path in ROOT.rglob("*.md"):
        if SKIP & set(path.relative_to(ROOT).parts):
            continue
        documents += 1
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for shape, pattern in VERSION_OUTPUTS.items():
                match = pattern.search(line)
                if match is None:
                    continue
                found[shape] += 1
                if match.group(1) != current:
                    stale.append(f"{path.relative_to(ROOT)}:{number} quotes {match.group(1)}")
    assert documents >= 20, f"the scan read only {documents} documents; it is not reaching them"
    unmatched = sorted(shape for shape, count in found.items() if count == 0)
    assert not unmatched, (
        f"no document shows {', '.join(unmatched)}. Either the output changed shape and this "
        f"scan no longer recognises it, or it stopped being documented. Both leave every "
        f"version written in that shape unguarded."
    )
    assert not stale, (
        f"the library exports {current} and these outputs say otherwise:\n  " + "\n  ".join(stale)
    )


@pytest.mark.parametrize("relative", RELEASE_SCRIPTS)
def test_release_scripts_are_executable(relative: str) -> None:
    path = ROOT / relative
    assert path.stat().st_mode & stat.S_IXUSR, f"{relative} is not executable by its owner"


# A release qualifier attached to current behaviour: "in v1.0", "in v1.1". Forward deferrals
# were once exempt, on the reasoning that a version still ahead cannot go stale — but a
# version only stays ahead until it ships, and the release it named arrived. Whoever reads a
# runtime message is running some release, so naming any release is the fault. A refusal says
# what is unsupported and what to use instead.
RELEASE_QUALIFIER = re.compile(r"\bin v\d+\.\d+|\bdeferred to v\d+\.\d+")

SRC_ROOTS = (
    "lib/src",
    "platform/llm-cli/src",
    "platform/llm-mcp/src",
)


def test_no_shipped_runtime_string_dates_current_behaviour_to_a_release() -> None:
    """A message a caller reads at runtime must not date itself to a release.

    Such a message is correct on the release that ships it and wrong on the next one, and
    nothing re-reads it: a refusal naming the version that will carry a feature sends a
    caller who upgrades to the version they are already running.

    Scoped to shipped source rather than documentation, because version qualifiers belong
    in release history and forward-looking compatibility notes.
    """
    stale: list[str] = []
    scanned = 0
    for root in SRC_ROOTS:
        base = ROOT / root
        assert base.is_dir(), f"{root} is not where this scan thinks it is"
        for path in base.rglob("*.py"):
            if SKIP & set(path.relative_to(ROOT).parts):
                continue
            scanned += 1
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if RELEASE_QUALIFIER.search(line):
                    stale.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")

    assert scanned >= 20, f"the scan reached only {scanned} modules; it is not finding them"
    assert not stale, (
        "these shipped lines date current behaviour to a release, which is wrong on the "
        "next one:\n  " + "\n  ".join(stale)
    )
