"""Claims about the shared base class, checked mechanically rather than by care.

The same false claim was corrected four times, in a different file each time: prose asserting
that every ``DataSource`` method returns an empty result, or that the registry cannot reach
this source by name. Both were true once. Upstream moved, and each correction fixed the copies
that were easy to find and missed the rest — a document, then a second document, then a
docstring, then a test's own explanatory text.

Correcting them one at a time is what failed. These are claims a reader acts on, so they are
gated here: every shipped text file in the repository is scanned, and a phrasing that asserts
a fact the runtime contradicts fails the suite. The point is not the wording but the claim,
so each pattern is paired with the reason it is false and with the test that proves it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# parents[4], not [3]: [3] is `lib/`, and the first version of this guard scanned only that.
# A reintroduced claim in the repository's own README slipped past it, and so did its anchor
# check, because every name it looked for also exists inside `lib/`.
REPO_ROOT = Path(__file__).resolve().parents[4]

# Directories that are not part of what ships: virtual environments, build output, tool
# caches, and the recorded provider responses, whose stored text is evidence of what a
# provider returned rather than prose this project maintains.
SKIP = {".venv", "build", "__pycache__", ".pytest_cache", ".ruff_cache", "cassettes"}

# The decision log records what was decided and when, so a claim inside a dated entry is
# correct as history even once the world moves. Its entry on this subject keeps its original
# wording with a dated update beneath it. The cost of exempting the file is that a genuinely
# new false claim written there would not be caught
# here; that is accepted because the log is append-only history rather than current guidance,
# and anything it says that a reader might act on today is repeated in a document that is
# scanned.
HISTORICAL = {"13-decision-log.md"}

# The changelog is half record and half current guidance, so it is split rather than
# exempted. A released section describes the release it names and stays true about it;
# scanning one would make rewriting a shipped release the only way to pass, which is the
# opposite of what a changelog is for. The unreleased section is a claim about what is
# about to ship, and is scanned like anything else.
CHANGELOG = "CHANGELOG.md"

FALSE_CLAIMS = [
    (
        r"every method (it defines |on it )?returns an empty result"
        r"|whose every method returns an empty"
        r"|methods all return empty results"
        r"|each of its (methods|implementations) returns an empty result"
        r"|its implementations return empty results",
        "the base surface is not uniformly empty: transaction methods raise "
        "NotImplementedError and the orchestrator-tool methods carry real behaviour. "
        "Proven by test_the_transactional_surface_refuses_rather_than_returning_empty.",
    ),
    (
        r"no `llm` arm|no ``llm`` arm|hardcoded (list of )?protocol|Unsupported source protocol",
        "the registry reaches this source through the class path recorded at registration, "
        "not through protocol-name dispatch. Proven by the registry round-trip assertions.",
    ),
    (
        r"advertises no tools"
        r"|serves no (orchestrator )?tools"
        r"|no orchestrator tool surface"
        r"|`?get_nl_tools`? returns (nothing|an empty)"
        r"|refuses (every tool|the whole tool path)"
        r"|overrides (those three|them locally) with a refusal",
        "this source advertises four of the base class's eight tools — list_databases, "
        "list_collections, get_schema and execute_query — and execute_nl_tool serves them "
        "by delegating to the base dispatcher. Only execute_query_nl_tools is refused. "
        "Proven by test_the_advertised_tools_are_the_ones_this_source_can_answer.",
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
        "rather than being refused. Proven by the tests in llm/test_client_stream.py.",
    ),
    (
        r"not supported in v1\.0|deferred to v1\.1|v1\.1 deferral",
        "llm.rate_limit is refused by this release, and this release is 1.1, so a refusal "
        "or a table deferring it to v1.1 sends a caller to the version they are already "
        "running. It is deferred to v2.0, and the setting is unsupported in 1.1. Proven by "
        "test_client_side_rate_limiting_is_refused_by_name. The claim was written in ten "
        "files — two refusal messages, a Click help string, five documents and two READMEs "
        "— which is why it is scanned rather than corrected one file at a time.",
    ),
]


def _shipped_text_files() -> list[Path]:
    out: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".md", ".py", ".toml"}:
            continue
        if SKIP & set(path.relative_to(REPO_ROOT).parts):
            continue
        if path.name in HISTORICAL:
            continue
        if path.name == Path(__file__).name:
            # This file holds the patterns as literals, so it matches itself.
            continue
        out.append(path)
    return out


def _current_lines(path: Path) -> list[tuple[int, str]]:
    """The lines of *path* that speak about now, numbered from one.

    Everything except the changelog's released sections, for the reason recorded above
    the CHANGELOG constant.
    """
    numbered = list(enumerate(path.read_text(encoding="utf-8").splitlines(), 1))
    if path.name != CHANGELOG:
        return numbered
    out: list[tuple[int, str]] = []
    released = False
    for number, line in numbered:
        if line.startswith("## "):
            released = "unreleased" not in line
        if not released:
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
    for anchor in ("CHANGELOG.md", "docs/design/01-overview.md", "lib/README.md"):
        assert anchor in relative, f"scan is not reaching {anchor}"


def test_the_changelog_is_scanned_up_to_the_first_released_section() -> None:
    """Narrowing a scan is only safe if it narrows to what it meant to.

    The released sections are skipped and the unreleased one is not, and a carve-out that
    quietly swallowed the whole file would pass every pattern for the wrong reason.
    """
    path = REPO_ROOT / CHANGELOG
    headings = [
        (number, line)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if line.startswith("## ")
    ]
    unreleased = [number for number, line in headings if "unreleased" in line]
    released = [number for number, line in headings if "unreleased" not in line]
    assert unreleased and released, (
        "the changelog no longer has both an unreleased and a released section, so this "
        "check is no longer testing what it claims to"
    )

    scanned = {number for number, _ in _current_lines(path)}
    assert unreleased[0] in scanned, "the unreleased section must still be scanned"
    assert released[0] not in scanned, "a released section is a record and is not scanned"
