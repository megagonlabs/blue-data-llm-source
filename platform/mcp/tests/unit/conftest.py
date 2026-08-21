"""Fixtures for the MCP tests.

The model is the only boundary, so it is the only thing doubled. Nothing here starts a
server or opens a transport: the tools are called as functions, which is what the server
does to them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest
from blue_data_llm_source import Capability, LlmSource, Usage
from blue_data_llm_source.llm.budget import Budget

EU_RECORDS = json.dumps(
    [
        {"country": "France", "accession_year": "1958"},
        {"country": "Germany", "accession_year": "1958"},
    ]
)

MESSY_RECORDS = json.dumps(
    [
        {"Country": "France", "capital": "Paris"},
        {"Country": "Germany", "capital": "Berlin"},
    ]
)


class FakeClient:
    """Returns *response*, or raises it when it is an exception."""

    def __init__(
        self,
        response: str | Exception,
        model: str = "fake-model",
        prompt_tokens: int = 12,
        completion_tokens: int = 34,
        cost: float = 0.0012,
    ) -> None:
        self.response = response
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost = cost
        # Budget-aware, like the client it stands in for: it claims against the run's
        # budget so the ceilings the interface exposes actually bind in these tests.
        self.budget = Budget()
        self.prompts: list[str] = []
        self.properties: list[dict[str, Any]] = []
        self.usages: list[Usage] = []

    def budget_capabilities(self) -> frozenset[str]:
        """Every axis: this double claims against the budget it is handed, like the real client."""
        return Capability.ALL

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        merged = dict(properties or {})
        run = merged.get("budget")
        # `attempt`, like the real client: its cleanup is registered before the claim
        # is taken, so this double models the lifecycle it claims to model.
        with (run if isinstance(run, Budget) else self.budget).attempt(self.cost) as reservation:
            self.prompts.append(prompt)
            self.properties.append(merged)
            if isinstance(self.response, Exception):
                reservation.keep_unpriced()
                raise self.response
            reservation.settle(self.cost)
            record = Usage(
                model=self.model,
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                cost=self.cost,
            )
            self.usages.append(record)
            sink = merged.get("usage_sink")
            if isinstance(sink, list):
                cast("list[Usage]", sink).append(record)
            return self.response

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self.complete(prompt, properties)


@pytest.fixture
def make_source() -> Callable[..., tuple[LlmSource, FakeClient]]:
    """Build a source over a fake client, and hand back both."""

    def make(response: str | Exception = EU_RECORDS, **kwargs: Any) -> tuple[LlmSource, FakeClient]:
        client = FakeClient(response, **kwargs)
        return LlmSource(client=client), client

    return make


@pytest.fixture
def source(make_source: Callable[..., tuple[LlmSource, FakeClient]]) -> LlmSource:
    return make_source()[0]
