"""Keep the source package independent of the orchestration layer."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = SRC / "blue_data_llm_source"
# blue_data is a declared dependency. blue itself and the operator library are not: a
# source reaching into either is a source that cannot be used outside that runtime.
ORCHESTRATION = ("blue_data_ops", "blue")


def test_nothing_reaches_into_the_orchestration_layer() -> None:
    """A data source that imports the runtime calling it cannot be used anywhere else."""
    offenders: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in ORCHESTRATION:
            # Anchored, because this package's own name starts with "blue_data".
            if re.search(rf"^\s*(?:from|import)\s+{name}\b", text, re.MULTILINE):
                offenders.append(f"{path.relative_to(SRC)} imports {name}")
    assert not offenders, offenders
