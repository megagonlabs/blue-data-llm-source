"""Exercise the installed provider in a fresh process with outbound access blocked."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = r"""
import asyncio, contextlib, importlib.metadata, io, json, logging, socket, sys
sys.path[:0] = json.loads(sys.argv[2])
attempts = {"socket": 0, "http": 0}
def deny_socket(*args, **kwargs):
    attempts["socket"] += 1
    raise AssertionError("outbound socket blocked")
socket.create_connection = deny_socket
socket.getaddrinfo = deny_socket
for name in ("connect", "connect_ex", "sendto", "sendmsg"):
    if hasattr(socket.socket, name):
        setattr(socket.socket, name, deny_socket)
import httpx
def deny_http(*args, **kwargs):
    attempts["http"] += 1
    raise AssertionError("outbound HTTP blocked")
async def deny_async_http(*args, **kwargs):
    return deny_http(*args, **kwargs)
httpx.Client.send = deny_http
httpx.AsyncClient.send = deny_async_http
mode = sys.argv[1]
logging.basicConfig(level=logging.DEBUG if mode.startswith("debug-") else logging.INFO, stream=sys.stderr)
from blue_data_llm_source import LlmSource, TransportError
from blue_data_llm_source.llm.client import DefaultLlmClient
assert "litellm" not in sys.modules
source = LlmSource(properties={"llm.max_tokens": 64, "limits.max_retries": 0})
assert "litellm" not in sys.modules
if mode == "lazy":
    print(json.dumps(attempts), file=sys.stderr)
    raise SystemExit(0)
if "preimport" in mode:
    # The host owns output from imports it performs before calling this client.
    with contextlib.redirect_stdout(io.StringIO()):
        import litellm
    litellm.turn_on_debug()
if "explicit" in mode:
    for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
        logging.getLogger(name).setLevel(logging.DEBUG)

try:
    if mode.endswith("async"):
        asyncio.run(source.afetch("test", attrs=["item"]))
    else:
        source.fetch("test", attrs=["item"])
except TransportError as error:
    assert isinstance(error.provider_error, Exception)
    print("TransportError", file=sys.stderr)
else:
    raise AssertionError("missing key did not fail")
print("litellm=" + importlib.metadata.version("litellm"), file=sys.stderr)
for _ in range(3):
    DefaultLlmClient._import_provider()
for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
    logging.getLogger(name).warning("retained warning")
assert attempts == {"socket": 0, "http": 0}, attempts
print(json.dumps(attempts), file=sys.stderr)
"""


@pytest.mark.parametrize(
    "mode",
    [
        "lazy",
        "sync",
        "async",
        "debug-sync",
        "debug-async",
        "preimport-sync",
        "preimport-async",
        "explicit-sync",
        "explicit-async",
    ],
)
def test_provider_stdout_is_empty_offline(mode: str, tmp_path: Path) -> None:
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR")
        if key in os.environ
    }
    env.update(
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        LITELLM_DISABLE_DOTENV="True",
        PYTHONDONTWRITEBYTECODE="1",
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    paths = [
        str(Path(path).resolve())
        for path in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if path
    ]
    paths.append(str(ROOT / "lib/src"))
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", BOOTSTRAP, mode, json.dumps(paths)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", result.stdout
    assert '"socket": 0, "http": 0' in result.stderr
    assert "Logging error" not in result.stderr
    if mode != "lazy":
        assert "TransportError" in result.stderr
        assert "retained warning" in result.stderr
        assert "LiteLLM completion()" not in result.stdout
