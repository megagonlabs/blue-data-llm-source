"""Doubles for the boundaries this package owns: the model, the cache, the network."""

from __future__ import annotations

from tests.doubles.cache import MemoryCache
from tests.doubles.clients import FailingClient, ScriptedClient
from tests.doubles.network import (
    NetworkAccessError,
    OfflineRecord,
    OfflineViolation,
    install_network_guard,
    install_offline_guard,
)
from tests.doubles.provider import (
    LitellmStub,
    chat_payload,
    install_litellm,
    stream_chunks,
)

__all__ = [
    "FailingClient",
    "LitellmStub",
    "MemoryCache",
    "NetworkAccessError",
    "OfflineRecord",
    "OfflineViolation",
    "ScriptedClient",
    "chat_payload",
    "install_litellm",
    "install_network_guard",
    "install_offline_guard",
    "stream_chunks",
]
