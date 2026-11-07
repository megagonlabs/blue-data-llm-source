"""Fixtures for the MCP tests.

The model is the only boundary, so it is the only thing doubled. The tools are called as
functions, which is what the server does to them; the few tests that start the server do so
as a subprocess of their own.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest
from blue_data_llm_source import Capability, LlmSource, Usage
from blue_data_llm_source.llm.budget import Budget

from blue_data_llm_mcp import tools

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
        capabilities: frozenset[str] = Capability.ALL,
    ) -> None:
        self.response = response
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost = cost
        # Narrowed by a test that needs a client which cannot price an attempt in advance,
        # which is a real client shape rather than a hypothetical one: only a client driving
        # the provider itself knows the model and the rates a call will use.
        self.capabilities = capabilities
        # Budget-aware, like the client it stands in for: it claims against the run's
        # budget so the ceilings the interface exposes actually bind in these tests.
        self.budget = Budget()
        self.prompts: list[str] = []
        self.properties: list[dict[str, Any]] = []
        self.usages: list[Usage] = []

    def budget_capabilities(self) -> frozenset[str]:
        """Every axis by default: this double claims against the budget it is handed."""
        return self.capabilities

    def bound_for_attempt(self, prompt: str, properties: dict[str, Any]) -> float:
        """What one attempt could cost at most, which for this double is what it charges.

        Declaring ``Capability.ESTIMATE`` commits a client to answering this, so a double
        that declares it and cannot answer would report no bound at all — and every test of
        the estimate surface would be a test of the answer given when there is none.
        """
        return self.cost

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


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with none of the deployment settings set, whatever the shell exported.

    The runtime reads these variables on every call and honors them, as it should, so a
    caller who exported an operator ceiling would otherwise change what the tests observe.
    The names come from the runtime's own tables, so a setting added later is cleared too.
    A test that needs a setting sets it itself, after this runs.
    """
    settings = (*tools._ENVIRONMENT_SETTINGS, *tools._OPERATOR_LIMITS)  # pyright: ignore[reportPrivateUsage]
    for variable, _, _ in settings:
        monkeypatch.delenv(variable, raising=False)


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
