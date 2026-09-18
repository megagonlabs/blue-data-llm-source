"""The ``mcp`` package is a server-only dependency.

Tool definitions and translation have to be importable and testable without it, so this
checks the boundary directly rather than trusting that nobody adds an import later.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import blue_data_llm_mcp
from blue_data_llm_mcp import tools

PACKAGE = Path(tools.__file__).parent


class _BlockMcp:
    """A finder that makes ``import mcp`` fail, installed or not."""

    def find_spec(self, name: str, path: Any = None, target: Any = None) -> None:
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError(f"{name} is not installed")
        return None


def test_tools_imports_with_mcp_unavailable() -> None:
    blocker = _BlockMcp()
    hidden = {k: v for k, v in sys.modules.items() if k == "mcp" or k.startswith("mcp.")}
    for key in hidden:
        del sys.modules[key]
    sys.meta_path.insert(0, blocker)
    try:
        reloaded: ModuleType = importlib.reload(tools)
        assert [spec.name for spec in reloaded.TOOLS] == [
            "llmdb_query",
            "llmdb_estimate",
            "llmdb_list_models",
            "llmdb_inspect_model",
            "llmdb_inspect_metadata",
        ]
        assert reloaded.llmdb_inspect_metadata()["protocol"] == "llm"
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(hidden)
        importlib.reload(tools)
        importlib.reload(blue_data_llm_mcp)


def test_the_package_root_does_not_pull_in_the_server() -> None:
    assert blue_data_llm_mcp.TOOLS is tools.TOOLS
    assert "import mcp" not in (PACKAGE / "__init__.py").read_text(encoding="utf-8")


def test_only_the_server_module_imports_mcp() -> None:
    assert "import mcp" not in (PACKAGE / "tools.py").read_text(encoding="utf-8")
    assert "import mcp" in (PACKAGE / "server.py").read_text(encoding="utf-8")


def test_the_published_install_brings_the_provider_transport() -> None:
    """A documented `pip install` must produce a package that can fetch.

    The declared dependency asked for the library without its `llm` extra, so a clean
    resolver had no reason to install the provider transport: the info command worked and
    the first model-backed fetch raised `ConfigError: the llm provider is not installed`.

    Read from the shipped `pyproject.toml` rather than from installed metadata, because an
    editable install in this environment carries whatever it was built with.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / "pyproject.toml").read_text()
    declared = re.findall(r'"(blue-data-llm-source[^"]*)"', text)

    assert declared, "the library is not declared as a dependency at all"
    assert all("[llm]" in d for d in declared), (
        f"the published install does not request the provider extra: {declared}"
    )
