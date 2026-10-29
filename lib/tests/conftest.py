"""Fixtures shared by the whole suite.

The offline guard is autouse: every test in this tree runs with the socket layer
blocked, so a test that forgets to stub the provider fails by name instead of making a
real call.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.doubles import (
    LitellmStub,
    MemoryCache,
    OfflineRecord,
    ScriptedClient,
    install_litellm,
    install_offline_guard,
)


def _marked(request: pytest.FixtureRequest, name: str) -> bool:
    """Whether the test carries a marker.

    pytest types ``request.node`` through a private module, so a strict checker reads the
    lookup as unknown. Confined to this one helper rather than every call site.
    """
    node: Any = request.node  # pyright: ignore[reportUnknownMemberType]
    return node.get_closest_marker(name) is not None


@pytest.fixture(autouse=True)
def offline(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[OfflineRecord]:
    """Block network, subprocess, and database access, and assert none was attempted.

    Count all three event classes and assert zero attempts. Counting catches a blocked
    call even when the test swallows its exception.
    """
    record = install_offline_guard(
        monkeypatch,
        allow_local_store=_marked(request, "uses_local_store"),
    )
    yield record
    if _marked(request, "expects_offline_violation"):
        # The tests that prove the guard works must trip it. They say so explicitly
        # rather than the assertion being softened for everyone.
        return
    assert record.total == 0, f"offline violations: {record.as_dict()}"


@pytest.fixture
def scripted() -> ScriptedClient:
    """An empty scripted client; queue the responses in the test."""
    return ScriptedClient()


@pytest.fixture
def cache() -> MemoryCache:
    return MemoryCache()


@pytest.fixture
def litellm_stub(monkeypatch: pytest.MonkeyPatch) -> Callable[[LitellmStub], LitellmStub]:
    """Return a factory that installs a LitellmStub in place of the real module."""

    def install(stub: LitellmStub) -> LitellmStub:
        return install_litellm(monkeypatch, stub)

    return install


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "expects_offline_violation: this test deliberately trips the offline guard",
    )
    config.addinivalue_line(
        "markers",
        "uses_local_store: this test exercises the shipped on-disk cache, which is a "
        "local sqlite file rather than a service; remote drivers stay blocked",
    )
