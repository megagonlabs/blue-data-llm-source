"""A stand-in for the litellm module.

``DefaultLlmClient`` imports litellm inside the call so the provider extra stays
optional. That same lazy import is what lets a test replace the module in
``sys.modules`` and be certain no provider is contacted.
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from typing import Any

import pytest


def chat_payload(
    content: str,
    *,
    model: str = "stub-model",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the response shape litellm returns. Omits ``usage`` when none is given."""
    payload: dict[str, Any] = {
        "id": "stub-completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


class LitellmStub:
    """Records the kwargs it is handed and returns a canned payload or raises."""

    def __init__(
        self,
        payload: Any = None,
        *,
        cost: float = 0.0,
        error: Exception | None = None,
        fail_times: int = 0,
        hold: asyncio.Event | None = None,
    ) -> None:
        self.payload = payload
        self.cost = cost
        self.error = error
        # A transient failure: the first *fail_times* attempts raise, the rest answer.
        # `error` alone fails every attempt, which cannot exercise a successful retry.
        self.fail_times = fail_times
        # Set to hold every async call inside the provider until it is released. Without
        # something to await, the async path returns before a second caller can start, so a
        # test that passes two coroutines to gather never actually overlaps them.
        self.hold = hold
        self.entered = 0
        self.expected = 0
        self.all_entered: asyncio.Event | None = None
        self.calls: list[dict[str, Any]] = []

    def completion(self, **kwargs: Any) -> Any:
        return self._respond(kwargs)

    async def acompletion(self, **kwargs: Any) -> Any:
        self.entered += 1
        if self.all_entered is not None and self.entered >= self.expected:
            self.all_entered.set()
        if self.hold is not None:
            # Held inside the provider, so every caller is genuinely in flight at once.
            await self.hold.wait()
        return self._respond(kwargs)

    def completion_cost(self, completion_response: Any = None, **kwargs: Any) -> float:
        return self.cost

    def _respond(self, kwargs: dict[str, Any]) -> Any:
        self.calls.append(kwargs)
        if len(self.calls) <= self.fail_times:
            raise RuntimeError(f"transient failure {len(self.calls)} of {self.fail_times}")
        if self.error is not None:
            raise self.error
        return self.payload

    def as_module(self) -> ModuleType:
        module = ModuleType("litellm")
        # A fresh module declares no attributes, so each assignment is an addition.
        module.completion = self.completion  # pyright: ignore[reportAttributeAccessIssue]
        module.acompletion = self.acompletion  # pyright: ignore[reportAttributeAccessIssue]
        module.completion_cost = self.completion_cost  # pyright: ignore[reportAttributeAccessIssue]
        return module


def install_litellm(monkeypatch: pytest.MonkeyPatch, stub: LitellmStub) -> LitellmStub:
    """Put *stub* in sys.modules under the name litellm and return it."""
    monkeypatch.setitem(sys.modules, "litellm", stub.as_module())
    return stub
