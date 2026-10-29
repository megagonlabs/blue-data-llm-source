"""Run the advertised no-key examples against their captured output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = (
    "backends/function_client_quickstart",
    "backends/injected_client_demo",
    "backends/cache_stores_demo",
    "feature-demos/attrs_and_types_demo",
    "feature-demos/limits_demo",
    "feature-demos/estimate_demo",
    "feature-demos/budget_demo",
    "feature-demos/gaps_and_outcome_demo",
    "feature-demos/planning_modes_demo",
    "feature-demos/tool_surface_demo",
    "integration-demos/register_source_demo",
    "integration-demos/estimate_then_fetch_demo",
    "scenario-demos/bounded_enumeration_demo",
    "scenario-demos/partitioned_wikitabgen_demo",
)
BOOTSTRAP = """
import os, runpy, socket, subprocess, sys
script, paths = sys.argv[1:]
import json
sys.path[:0] = json.loads(paths)
def deny(*args, **kwargs):
    raise AssertionError("offline example attempted network or child-process access")
socket.create_connection = deny
socket.getaddrinfo = deny
for name in ("connect", "connect_ex", "sendto", "sendmsg", "bind", "listen", "accept"):
    if hasattr(socket.socket, name):
        setattr(socket.socket, name, deny)
subprocess.run = deny
subprocess.Popen = deny
os.system = deny
sys.argv = [script]
runpy.run_path(script, run_name="__main__")
"""


@pytest.mark.parametrize("example", EXAMPLES)
def test_offline_example_matches_capture(example: str, tmp_path: Path) -> None:
    script = ROOT / "examples" / (example + ".py")
    capture = script.with_suffix(".output.txt")
    assert script.is_file() and capture.is_file()
    if example == "backends/cache_stores_demo":
        pytest.importorskip("diskcache")

    env = {
        key: os.environ[key]
        for key in (
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
            "DYLD_FRAMEWORK_PATH",
            "DYLD_LIBRARY_PATH",
        )
        if key in os.environ
    }
    for name in ("home", "temp", "cache"):
        (tmp_path / name).mkdir()
    (tmp_path / "cache" / "tiktoken").mkdir()
    env.update(
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
        TMPDIR=str(tmp_path / "temp"),
        TMP=str(tmp_path / "temp"),
        TEMP=str(tmp_path / "temp"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        TIKTOKEN_CACHE_DIR=str(tmp_path / "cache" / "tiktoken"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        LITELLM_TELEMETRY="False",
    )
    paths = [str((ROOT / "lib/src").resolve())]
    paths.extend(str(Path(path or os.getcwd()).resolve()) for path in sys.path)
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", BOOTSTRAP, str(script), json.dumps(paths)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == capture.read_text(encoding="utf-8")
