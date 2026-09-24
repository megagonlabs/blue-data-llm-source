"""A stand-in for the litellm module.

``DefaultLlmClient`` imports litellm inside the call so the provider extra stays
optional. That same lazy import is what lets a test replace the module in
``sys.modules`` and be certain no provider is contacted.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from types import ModuleType
from typing import Any, cast

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


def stream_chunks(
    content: str,
    *,
    model: str = "stub-model",
    usage: dict[str, Any] | None = None,
    size: int = 4,
) -> list[dict[str, Any]]:
    """Split *content* into the chunk shape litellm streams, *usage* on the last one.

    A provider that sends no accounting with a stream is the default here, because that is
    the case the client has to get right and the one a test would otherwise forget.
    """
    pieces = [content[i : i + size] for i in range(0, len(content), size)] or [""]
    chunks: list[dict[str, Any]] = [
        {
            "id": "stub-completion",
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": piece}}],
        }
        for piece in pieces
    ]
    if usage is not None:
        chunks[-1]["usage"] = usage
    return chunks


async def _as_stream(chunks: list[Any]) -> AsyncIterator[Any]:
    for chunk in chunks:
        yield chunk


def _usable(usage: dict[str, Any] | None) -> bool:
    """Whether a usage block carries counts, rather than merely existing."""
    if not usage:
        return False
    return any(
        isinstance(usage.get(name), int)
        and not isinstance(usage.get(name), bool)
        and cast("int", usage.get(name)) > 0
        for name in ("prompt_tokens", "completion_tokens", "total_tokens")
    )


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
        chunks: list[Any] | None = None,
    ) -> None:
        self.payload = payload
        self.cost = cost
        self.error = error
        # What a call asking for a stream gets back. Left as None, a call asking for one is
        # answered with `payload` instead, which is how a test models a provider that ignored
        # the flag.
        self.chunks = chunks
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
        # Bare names the provider library cannot route, as its bundled price map cannot for a
        # model released after it. A named provider or a `provider/model` name still routes.
        self.unroutable: set[str] = set()
        self.resolved: list[tuple[str, str | None]] = []
        # The chunk lists handed to the assembler, so a test can assert the client read the
        # whole stream rather than the first chunk of it.
        self.assembled: list[list[Any]] = []

    def completion(self, **kwargs: Any) -> Any:
        answer = self._respond(kwargs)
        return iter(answer) if self._streams(kwargs) else answer

    async def acompletion(self, **kwargs: Any) -> Any:
        self.entered += 1
        if self.all_entered is not None and self.entered >= self.expected:
            self.all_entered.set()
        if self.hold is not None:
            # Held inside the provider, so every caller is genuinely in flight at once.
            await self.hold.wait()
        answer = self._respond(kwargs)
        return _as_stream(answer) if self._streams(kwargs) else answer

    def completion_cost(self, completion_response: Any = None, **kwargs: Any) -> float:
        return self.cost

    def get_supported_openai_params(
        self, *, model: str, custom_llm_provider: str | None = None
    ) -> list[str]:
        del model, custom_llm_provider
        return ["reasoning_effort", "max_completion_tokens"]

    def get_llm_provider(
        self, model: str, custom_llm_provider: str | None = None, **kwargs: Any
    ) -> tuple[str, str, str | None, str | None]:
        """Resolve a provider the way litellm does, failing with its missing-provider message."""
        self.resolved.append((model, custom_llm_provider))
        if custom_llm_provider:
            return model, custom_llm_provider, None, None
        if "/" in model:
            prefix, _, rest = model.partition("/")
            return rest, prefix, None, None
        if model in self.unroutable:
            raise RuntimeError(
                f"litellm.BadRequestError: LLM Provider NOT provided. Pass in the LLM provider "
                f"you are trying to call. You passed model={model}"
            )
        return model, "openai", None, None

    def stream_chunk_builder(self, chunks: list[Any], **kwargs: Any) -> Any:
        """Rebuild one response from *chunks*, as the provider library's helper does.

        The parts this client depends on are reproduced rather than simplified: an empty
        chunk list comes back as None, a chunk this cannot read raises, and the assembled
        response always carries a usage block — counted from the text itself when the stream
        sent none. That last one matters. A double that simply omitted usage would let a
        client that priced the assembled block look correct here, when against the real
        helper it would be settling a figure the provider never charged.
        """
        self.assembled.append(list(chunks))
        if not chunks:
            return None
        model = ""
        content = ""
        usage: dict[str, Any] | None = None
        for chunk in chunks:
            if not isinstance(chunk, dict):
                raise TypeError(f"cannot read a chunk of {type(chunk).__name__}")
            entry = cast("dict[str, Any]", chunk)
            model = str(entry.get("model") or model)
            for choice in cast("list[dict[str, Any]]", entry.get("choices") or []):
                delta = cast("dict[str, Any]", choice.get("delta") or {})
                content += str(delta.get("content") or "")
            if entry.get("usage") is not None:
                usage = cast("dict[str, Any]", entry["usage"])
        payload = chat_payload(content, model=model or "stub-model")
        # An empty usage block, or one whose counts are all zero, is filled in from the text
        # exactly as the real helper does. Preserving the empty mapping instead would let a
        # client that reads presence rather than usable counts pass here while settling a
        # locally invented figure against the real helper.
        payload["usage"] = usage if _usable(usage) else _counted(content)
        return payload

    def _streams(self, kwargs: dict[str, Any]) -> bool:
        """Whether this call comes back as a stream rather than as one response."""
        return bool(kwargs.get("stream")) and self.chunks is not None

    def _respond(self, kwargs: dict[str, Any]) -> Any:
        self.calls.append(kwargs)
        if len(self.calls) <= self.fail_times:
            raise RuntimeError(f"transient failure {len(self.calls)} of {self.fail_times}")
        if self.error is not None:
            raise self.error
        return self.chunks if self._streams(kwargs) else self.payload

    def as_module(self) -> ModuleType:
        module = ModuleType("litellm")
        # A fresh module declares no attributes, so each assignment is an addition.
        module.completion = self.completion  # pyright: ignore[reportAttributeAccessIssue]
        module.acompletion = self.acompletion  # pyright: ignore[reportAttributeAccessIssue]
        module.completion_cost = self.completion_cost  # pyright: ignore[reportAttributeAccessIssue]
        module.get_supported_openai_params = self.get_supported_openai_params  # pyright: ignore[reportAttributeAccessIssue]
        module.get_llm_provider = self.get_llm_provider  # pyright: ignore[reportAttributeAccessIssue]
        module.stream_chunk_builder = self.stream_chunk_builder  # pyright: ignore[reportAttributeAccessIssue]
        return module


def _counted(content: str) -> dict[str, Any]:
    """Token counts a tokenizer would produce locally, which is not what a provider bills."""
    return {"prompt_tokens": 0, "completion_tokens": max(1, len(content) // 4)}


def install_litellm(monkeypatch: pytest.MonkeyPatch, stub: LitellmStub) -> LitellmStub:
    """Put *stub* in sys.modules under the name litellm and return it."""
    monkeypatch.setitem(sys.modules, "litellm", stub.as_module())
    return stub
