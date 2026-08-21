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


@pytest.mark.parametrize(("pattern", "why"), FALSE_CLAIMS)
def test_no_shipped_file_repeats_a_claim_the_runtime_contradicts(pattern: str, why: str) -> None:
    compiled = re.compile(pattern, re.IGNORECASE)
    hits: list[str] = []
    for path in _shipped_text_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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
