"""Check shipped descriptions of ``DataSource`` against its runtime contract."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# parents[4] is the repository root; parents[3] would omit top-level documentation.
REPO_ROOT = Path(__file__).resolve().parents[4]

# Directories that are not part of what ships: virtual environments, build output, tool
# caches, and the recorded provider responses, whose stored text is evidence of what a
# provider returned rather than prose this project maintains.
SKIP = {
    ".venv",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "cassettes",
}

# The changelog is part record and part claim about what is about to ship, so it is split
# rather than exempted. A released section describes the release it names and stays true
# about it; scanning one would make rewriting a shipped release the only way to pass, which
# is the opposite of what a changelog is for. An unreleased section is scanned like anything
# else. Between releases there is no unreleased section and the whole file is record, until
# the next section opens and comes back under the scan on its own.
CHANGELOG = "CHANGELOG.md"

FALSE_CLAIMS = [
    (
        r"every method (it defines |on it )?returns an empty result"
        r"|whose every method returns an empty"
        r"|methods all return empty results"
        r"|each of its (methods|implementations) returns an empty result"
        r"|its implementations return empty results",
        "the base surface is not uniformly empty: transaction methods raise "
        "NotImplementedError and the orchestrator-tool methods carry real behavior.",
    ),
    (
        r"no `llm` arm|no ``llm`` arm|hardcoded (list of )?protocol|Unsupported source protocol",
        "the registry reaches this source through the class path recorded at registration, "
        "not through protocol-name dispatch.",
    ),
    (
        r"advertises no tools"
        r"|serves no (orchestrator )?tools"
        r"|no orchestrator tool surface"
        r"|`?get_nl_tools`? returns (nothing|an empty)"
        r"|refuses (every tool|the whole tool path)"
        r"|overrides (those three|them locally) with a refusal",
        "this source advertises five of the base class's eight tools — list_databases, "
        "list_collections, get_schema, execute_query and get_sample — and "
        "execute_nl_tool serves them "
        "by delegating to the base dispatcher. Only execute_query_nl_tools is refused.",
    ),
    (
        r"streaming is deferred"
        r"|`?llm\.stream(=True)?`? is not supported"
        r"|`llm\.stream=True` is refused"
        r"|`True` is refused and deferred"
        r"|a truthy value is refused",
        "llm.stream=True is accepted. The chunks are read to the end, reassembled by the "
        "provider library's helper, and handed to the same path a non-streamed response "
        "takes. It buys compatibility with providers and proxies that require a stream and "
        "makes nothing faster; a stream carrying no usage reports accounting_known=False "
        "rather than being refused.",
    ),
    (
        r"not supported in v\d+\.\d+|deferred to v\d+\.\d+|v\d+\.\d+ deferral",
        "llm.rate_limit is refused. Shipped text should describe current support and "
        "available alternatives without a version-qualified promise.",
    ),
]


def _shipped_text_files() -> list[Path]:
    out: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".md", ".py", ".toml"}:
            continue
        if SKIP & set(path.relative_to(REPO_ROOT).parts):
            continue
        if path.name == Path(__file__).name:
            # This file holds the patterns as literals, so it matches itself.
            continue
        out.append(path)
    return out


def _current_lines(path: Path) -> list[tuple[int, str]]:
    """The lines of *path* that speak about now, numbered from one.

    For the changelog that is the preamble and the newest section only. The newest section
    describes the software as it currently behaves whether or not its heading still says
    unreleased, so it is the one where a false claim would mislead a reader; every section
    below it describes a release that is past and stays true about itself.
    """
    numbered = list(enumerate(path.read_text(encoding="utf-8").splitlines(), 1))
    if path.name != CHANGELOG:
        return numbered
    out: list[tuple[int, str]] = []
    sections = 0
    for number, line in numbered:
        if line.startswith("## "):
            sections += 1
        if sections <= 1:
            out.append((number, line))
    return out


@pytest.mark.parametrize(("pattern", "why"), FALSE_CLAIMS)
def test_no_shipped_file_repeats_a_claim_the_runtime_contradicts(pattern: str, why: str) -> None:
    compiled = re.compile(pattern, re.IGNORECASE)
    hits: list[str] = []
    for path in _shipped_text_files():
        for number, line in _current_lines(path):
            if compiled.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{number}")
    assert not hits, (
        f"{len(hits)} shipped location(s) repeat a claim the runtime contradicts:\n  "
        + "\n  ".join(hits)
        + f"\n\nWhy it is false: {why}\n"
        "If the runtime has changed and the claim is true again, change the pattern here "
        "in the same commit, so the claim and its check move together."
    )


def test_the_scan_actually_reaches_the_repository() -> None:
    """A guard whose file list is empty passes for the wrong reason.

    The scan walks from this test's own location, so a directory move would silently reduce
    it to nothing. This is the sort of quiet pass the rest of this suite exists to catch.
    """
    files = _shipped_text_files()
    assert len(files) > 50, (
        f"scan found only {len(files)} files; it is not reaching the repository root"
    )
    # Anchored on paths that exist ONLY at the repository root, so a scan that reaches
    # `lib/` alone cannot satisfy this.
    relative = {str(p.relative_to(REPO_ROOT)) for p in files}
    for anchor in ("CHANGELOG.md", "design/01-overview.md", "lib/README.md"):
        assert anchor in relative, f"scan is not reaching {anchor}"


def test_the_changelog_carve_out_skips_a_released_section_and_nothing_else(
    tmp_path: Path,
) -> None:
    """Narrowing a scan is only safe if it narrows to what it meant to.

    A carve-out that quietly swallowed the whole file would pass every pattern for the wrong
    reason, so the split is exercised against a changelog holding both kinds of section. It
    is a fixture and not the shipped file because between releases every section of the
    shipped file is released, and the mechanism still has to be checked then.
    """
    both = tmp_path / CHANGELOG
    both.write_text(
        "# Changelog\n\npreamble\n\n"
        "## 1.0.1 — unreleased\n\nabout to ship\n\n"
        "## 0.2.2.0 — released (as 2.2.0)\n\nalready shipped\n",
        encoding="utf-8",
    )
    scanned = {line for _, line in _current_lines(both)}
    assert "about to ship" in scanned, "the newest section must be scanned"
    assert "already shipped" not in scanned, "an older section is a record and is not scanned"
    assert "preamble" in scanned, "the text before the first heading is not part of any section"


def test_every_released_section_of_the_shipped_changelog_is_skipped() -> None:
    """The same carve-out, against the file that ships.

    Which sections it has depends on where the repository is in its release cycle, so this
    reads the headings and requires the split to land on each one correctly.
    """
    path = REPO_ROOT / CHANGELOG
    headings = [
        (number, line)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if line.startswith("## ")
    ]
    assert headings, "the changelog no longer has version sections, so nothing is being split"

    scanned = {number for number, _ in _current_lines(path)}
    newest, *older = headings
    assert newest[0] in scanned, f"{newest[1]!r} is the newest section and must be scanned"
    for number, line in older:
        assert number not in scanned, f"{line!r} is a record and is not scanned"
    assert 1 in scanned, "the changelog's own preamble must still be scanned"
