"""Check package versions and version strings in documentation."""

from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest

import blue_data_llm_source

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = ROOT / "lib" / "pyproject.toml"

# Distribution name, packaging metadata, and module version declaration.
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

# Documented CLI and MCP version output shapes.
VERSION_OUTPUTS = {
    "the metadata table": re.compile(r"^\s*\u2502\s*version\s*\u2502\s*(\d+\.\d+\.\d+)\s*\u2502"),
    "the metadata JSON": re.compile(r'"version":\s*"(\d+\.\d+\.\d+)"'),
    "the CLI version line": re.compile(r"^blue-llmdb (\d+\.\d+\.\d+)$"),
}

# Exclude local environments and build artifacts from documentation scans.
SKIP = {
    ".venv",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "cassettes",
}

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
        "lib/pyproject.toml and blue_data_llm_source.__version__ disagree"
    )


@pytest.mark.parametrize(("name", "pyproject", "module"), PACKAGES, ids=[p[0] for p in PACKAGES])
def test_every_package_exports_the_version_its_packaging_declares(
    name: str, pyproject: str, module: str
) -> None:
    assert _declared(pyproject) == _exported(module), (
        f"{name} declares {_declared(pyproject)} in {pyproject} but exports "
        f"{_exported(module)} from {module}"
    )


@pytest.mark.parametrize(("name", "pyproject", "_module"), PACKAGES, ids=[p[0] for p in PACKAGES])
def test_every_package_declares_its_readme(name: str, pyproject: str, _module: str) -> None:
    project = ROOT / pyproject
    text = project.read_text(encoding="utf-8")
    assert re.search(r'^readme\s*=\s*"README\.md"$', text, re.MULTILINE), (
        f"{name} does not declare its README as package metadata"
    )
    assert (project.parent / "README.md").is_file()


@pytest.mark.parametrize(("name", "pyproject", "_module"), PACKAGES, ids=[p[0] for p in PACKAGES])
def test_every_package_declares_the_family_python_range(
    name: str, pyproject: str, _module: str
) -> None:
    """The Blue DI 1.0 family installs and runs together on Python 3.12 and 3.13 only."""
    text = (ROOT / pyproject).read_text(encoding="utf-8")
    assert re.search(r'^requires-python = ">=3\.12,<3\.14"$', text, re.MULTILINE), (
        f'{name} does not declare requires-python = ">=3.12,<3.14"'
    )


def test_every_documented_version_output_quotes_the_current_release() -> None:
    """Check each documented output shape against the current version."""
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
        f"scan no longer recognizes it, or it stopped being documented. Both leave every "
        f"version written in that shape unguarded."
    )
    assert not stale, (
        f"the library exports {current} and these outputs say otherwise:\n  " + "\n  ".join(stale)
    )


@pytest.mark.parametrize("relative", RELEASE_SCRIPTS)
def test_release_scripts_are_executable(relative: str) -> None:
    path = ROOT / relative
    assert path.stat().st_mode & stat.S_IXUSR, f"{relative} is not executable by its owner"


# Runtime messages must describe behavior without dating it to a release.
RELEASE_QUALIFIER = re.compile(r"\bin v\d+\.\d+|\bdeferred to v\d+\.\d+")

SRC_ROOTS = (
    "lib/src",
    "platform/llm-cli/src",
    "platform/llm-mcp/src",
)


def test_no_shipped_runtime_string_dates_current_behavior_to_a_release() -> None:
    """Reject release-qualified messages in shipped source."""
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
    assert not stale, "release-qualified runtime strings:\n  " + "\n  ".join(stale)


def test_platforms_require_the_exact_reset_library() -> None:
    from packaging.requirements import Requirement

    for relative in ("platform/llm-cli/pyproject.toml", "platform/llm-mcp/pyproject.toml"):
        text = (ROOT / relative).read_text()
        matches = re.findall(r'"(blue-data-llm-source\[llm\][^\"]+)"', text)
        assert len(matches) == 1
        requirement = Requirement(matches[0])
        assert str(requirement.specifier) == "==1.0.0"
        assert requirement.specifier.contains("1.0.0")
        # Earlier internal builds carry 0.x labels and report their original numbers when
        # installed. The pin refuses both, except an installed 0.1.0.0, which reports 1.0.0.
        for historical in ("0.1.0", "1.1.1", "1.2.0", "2.2.1", "0.1.1.1", "0.1.2.0", "0.2.2.1"):
            assert not requirement.specifier.contains(historical)
    core = (ROOT / "lib/pyproject.toml").read_text()
    assert '"blue-data>=1.0.0,<2"' in core
    cli = (ROOT / "platform/llm-cli/pyproject.toml").read_text()
    assert '"blue-data==1.0.0"' in cli


def test_the_provider_extras_name_no_local_build() -> None:
    """Published extras must resolve without local compatibility builds."""
    from packaging.requirements import Requirement

    text = PYPROJECT.read_text(encoding="utf-8")
    for extra in ("llm", "all"):
        match = re.search(rf"^{extra}\s*=\s*\[([^\]]*)\]", text, re.MULTILINE)
        assert match, f"lib/pyproject.toml has no {extra} extra"
        requirements = [Requirement(item) for item in re.findall(r'"([^"]+)"', match.group(1))]
        assert [r.name for r in requirements] == ["litellm", "diskcache"], extra
        for requirement in requirements:
            assert requirement.marker is None, f"{extra}: {requirement}"
            assert not any("+" in spec.version for spec in requirement.specifier), requirement
