"""Check package versions and version strings in documentation."""

from __future__ import annotations

import hashlib
import importlib.util
import re
import stat
import zipfile
from pathlib import Path
from types import ModuleType

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

# The last four are the Python 3.15 build directories docs/installation.md creates here.
SKIP = {
    ".venv",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "cassettes",
    ".build315",
    ".native315",
    ".venv315",
    ".wheels315",
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
        # Earlier releases carry 0.x labels in the changelog and report their original
        # numbers when installed. The pin refuses both, except an installed 0.1.0.0, which
        # reports 1.0.0.
        for historical in ("0.1.0", "1.1.1", "1.2.0", "2.2.1", "0.1.1.1", "0.1.2.0", "0.2.2.1"):
            assert not requirement.specifier.contains(historical)
    core = (ROOT / "lib/pyproject.toml").read_text()
    assert '"blue-data>=1.0.0,<2"' in core
    cli = (ROOT / "platform/llm-cli/pyproject.toml").read_text()
    assert '"blue-data==1.0.0"' in cli


# The Python 3.15 compatibility builds are chosen at install time, never by metadata.
SCRIPTS = ROOT / "lib" / "scripts"


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_provider_extras_name_no_local_build() -> None:
    """A local-version pin in published metadata breaks universal lockers on every Python.

    It would also hold Python 3.15 users to that build until another library release.
    """
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


def test_the_python315_constraints_pin_the_builds_the_scripts_make() -> None:
    from packaging.requirements import Requirement

    path = SCRIPTS / "python315-constraints.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    constraints = [Requirement(line) for line in lines if line and not line.startswith("#")]
    assert {r.name: str(r.specifier) for r in constraints} == {
        "litellm": f"=={_script('patch-litellm-python315-wheel').VERSION}",
        "fastuuid": f"=={_script('build-python315-wheel').VERSION}",
    }
    for requirement in constraints:
        assert requirement.marker is not None
        assert requirement.marker.evaluate({"python_version": "3.15"})
        assert not requirement.marker.evaluate({"python_version": "3.14"})
    manifest = (ROOT / "lib" / "MANIFEST.in").read_text(encoding="utf-8")
    assert f"include scripts/{path.name}" in manifest.splitlines()


def test_the_patched_litellm_wheel_admits_only_python_315(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its patched imports fail on 3.10, so an older Python seeing the wheelhouse must skip it."""
    patch = _script("patch-litellm-python315-wheel")
    source = tmp_path / "litellm-1.106.5-cp310-abi3-macosx_11_0_arm64.whl"
    info = "litellm-1.106.5.dist-info/"
    with zipfile.ZipFile(source, "w") as wheel:
        wheel.writestr(patch.CONFIG, b"def check():\n" + patch.OLD_IMPORTS)
        wheel.writestr(
            info + "METADATA",
            "Metadata-Version: 2.1\nName: litellm\nVersion: 1.106.5\n"
            "Requires-Python: >=3.10, <3.15\n\n",
        )
        wheel.writestr(info + "RECORD", "")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setitem(patch.UPSTREAM_SHA256, "macosx_11_0_arm64", digest)

    target = patch.transform(source, tmp_path / "wheelhouse")

    with zipfile.ZipFile(target) as wheel:
        metadata = wheel.read(f"litellm-{patch.VERSION}.dist-info/METADATA").decode()
    assert "\nRequires-Python: >=3.15, <3.16\n" in metadata
