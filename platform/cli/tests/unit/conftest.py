"""Fixtures for the CLI tests.

The model is the only boundary here, so it is the only thing doubled. Every test runs
against a client that returns a canned string, which keeps the whole suite offline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest
from blue_data_llm_source import Capability, LlmSource, Usage
from blue_data_llm_source.llm.budget import Budget
from click.testing import CliRunner

from blue_data_llm_cli import config

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
                model=str(merged.get("llm.model") or self.model),
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


class RaisingSource:
    """A source whose fetch raises, for errors the library raises above the client."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def fetch(self, *args: Any, **kwargs: Any) -> Any:
        raise self.error


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def install_source(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], Any]:
    def install(source: Any) -> Any:
        monkeypatch.setattr(config, "build_source", lambda: source)
        return source

    return install


@pytest.fixture
def install_client(install_source: Callable[[Any], Any]) -> Callable[..., FakeClient]:
    def install(response: str | Exception = EU_RECORDS, **kwargs: Any) -> FakeClient:
        client = FakeClient(response, **kwargs)
        # Pin the model so the footer assertion tests that the requested model is
        # reported, rather than pinning the package's default value.
        install_source(LlmSource(client=client, properties={"llm.model": client.model}))
        return client

    return install


@pytest.fixture
def install_raising_source(install_source: Callable[[Any], Any]) -> Callable[..., RaisingSource]:
    def install(error: Exception) -> RaisingSource:
        source = RaisingSource(error)
        install_source(source)
        return source

    return install


@pytest.fixture(autouse=True)
def default_client(install_client: Callable[..., FakeClient]) -> FakeClient:
    """No test reaches a provider: the default source is already faked."""
    return install_client()
