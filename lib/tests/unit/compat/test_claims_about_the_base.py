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
    for anchor in ("README.md", "design/01-overview.md", "lib/README.md"):
        assert anchor in relative, f"scan is not reaching {anchor}"
