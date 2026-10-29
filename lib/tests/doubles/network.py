"""The offline guard.

Unit tests here must never reach a provider. A double that forgets to intercept, or an
import that phones home, would otherwise pass quietly on a developer machine and fail in
CI. Blocking the socket layer turns that into an immediate, named failure.
"""

from __future__ import annotations

import importlib
import os
import socket
import subprocess
import sys
from typing import Any

import pytest


class OfflineViolation(RuntimeError):
    """Raised when a test reaches for a boundary the offline guard forbids."""


class NetworkAccessError(OfflineViolation):
    """Raised when a test tries to open a real network connection."""


def _refuse(address: Any) -> NetworkAccessError:
    return NetworkAccessError(
        f"unit tests run offline; refused a network connection to {address!r}"
    )


def install_network_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outbound socket connection raise NetworkAccessError."""

    def blocked_method(self: socket.socket, address: Any, *args: Any, **kwargs: Any) -> None:
        raise _refuse(address)

    def blocked_function(address: Any, *args: Any, **kwargs: Any) -> None:
        raise _refuse(address)

    monkeypatch.setattr(socket.socket, "connect", blocked_method)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked_method)
    monkeypatch.setattr(socket, "create_connection", blocked_function)


class OfflineRecord:
    """Counts the forbidden events a test attempted. All three must stay zero."""

    def __init__(self) -> None:
        self.network = 0
        self.subprocess = 0
        self.database = 0

    @property
    def total(self) -> int:
        return self.network + self.subprocess + self.database

    def as_dict(self) -> dict[str, int]:
        return {
            "network": self.network,
            "subprocess": self.subprocess,
            "database": self.database,
        }


def install_offline_guard(monkeypatch: Any, allow_local_store: bool = False) -> OfflineRecord:
    """Block and record network, subprocess, and database access for one test.

    Track all three event classes so subprocess and database access cannot pass as
    offline work.
    """
    record = OfflineRecord()

    def _describe(args: tuple[Any, ...]) -> str:
        # Name the address that was refused. "something tried to connect" sends the
        # reader hunting; "tried to connect to api.example" does not.
        for arg in args:
            if isinstance(arg, tuple) and arg:
                return f" to {arg[0]}"
            if isinstance(arg, str) and arg:
                return f" to {arg}"
        return ""

    def _socket(*args: Any, **kwargs: Any) -> Any:
        record.network += 1
        raise NetworkAccessError(
            f"network access attempted during an offline test{_describe(args)}"
        )

    def _connect(self: Any, *args: Any, **kwargs: Any) -> Any:
        record.network += 1
        raise NetworkAccessError(
            f"socket.connect attempted during an offline test{_describe(args)}"
        )

    def _connect_ex(self: Any, *args: Any, **kwargs: Any) -> Any:
        record.network += 1
        raise NetworkAccessError(
            f"socket.connect_ex attempted during an offline test{_describe(args)}"
        )

    def _subprocess(*args: Any, **kwargs: Any) -> Any:
        record.subprocess += 1
        raise OfflineViolation("subprocess launch attempted during an offline test")

    monkeypatch.setattr(socket.socket, "connect", _connect, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex, raising=False)
    monkeypatch.setattr(socket, "create_connection", _socket, raising=False)
    monkeypatch.setattr(subprocess, "Popen", _subprocess, raising=False)
    monkeypatch.setattr(subprocess, "run", _subprocess, raising=False)
    monkeypatch.setattr(os, "system", _subprocess, raising=False)

    # sqlite3 is in the standard library, so it is imported here rather than only
    # patched when something else happened to import it first. The third-party drivers
    # are guarded only if already loaded: none is a dependency of this package, and
    # importing one to guard it would be the very thing the guard exists to prevent.
    importlib.import_module("sqlite3")

    drivers: tuple[tuple[str, str], ...] = (
        ("sqlite3", "connect"),
        ("psycopg2", "connect"),
        ("pymongo", "MongoClient"),
        ("redis", "Redis"),
    )
    if allow_local_store:
        # The on-disk cache this package ships is backed by sqlite. It is a local file,
        # not a service, so a test of that cache is not the boundary crossing this guard
        # exists to catch. Remote drivers stay blocked.
        drivers = tuple(d for d in drivers if d[0] != "sqlite3")

    for module_name, attr in drivers:
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, attr):

            def _db(*args: Any, **kwargs: Any) -> Any:
                record.database += 1
                raise OfflineViolation("database connection attempted during an offline test")

            monkeypatch.setattr(module, attr, _db, raising=False)

    return record
