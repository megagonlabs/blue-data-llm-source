"""Fixtures for the CLI tests.

Two boundaries are doubled and nothing else. The model, because every test runs against a
client that returns a canned string, which keeps the whole suite offline. And the CLI home,
because these tests write profiles: every run is pointed at a temporary directory, the
environment is cleared of the variables the CLI reads, and a guard fails the run if the real
`~/.blue-llmdb` appears or disappears.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from inspect import signature
from pathlib import Path
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
    """Return one response per call, repeating the last, or raise an exception entry."""

    def __init__(
        self,
        response: str | Exception | list[str | Exception],
        model: str = "fake-model",
        prompt_tokens: int = 12,
        completion_tokens: int = 34,
        cost: float = 0.0012,
    ) -> None:
        self.responses = response if isinstance(response, list) else [response]
        if not self.responses:
            raise ValueError("FakeClient needs at least one response")
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
        """Every axis: this double claims against the budget it is handed, like the real one."""
        return Capability.ALL

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        merged = dict(properties or {})
        run = merged.get("budget")
        response = self.responses[min(len(self.prompts), len(self.responses) - 1)]
        # `attempt`, like the real client: its cleanup is registered before the claim
        # is taken, so this double models the lifecycle it claims to model.
        with (run if isinstance(run, Budget) else self.budget).attempt(self.cost) as reservation:
            self.prompts.append(prompt)
            self.properties.append(merged)
            if isinstance(response, Exception):
                reservation.keep_unpriced()
                raise response
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
            return response

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self.complete(prompt, properties)


class RaisingSource:
    """A source whose call raises, for errors the library raises above the client."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def fetch(self, *args: Any, **kwargs: Any) -> Any:
        raise self.error

    def estimate(self, *args: Any, **kwargs: Any) -> Any:
        raise self.error


@pytest.fixture(autouse=True)
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at a throwaway home and clear the variables it reads."""
    home = tmp_path / ".blue-llmdb"
    for name in list(os.environ):
        if name.startswith("BLUE_DATA_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(config.HOME_VARIABLE, str(home))
    return home


@pytest.fixture(autouse=True)
def never_the_real_home(cli_home: Path) -> Iterator[None]:
    """A test that writes to the developer's own profiles is a test that has to fail."""
    real = Path(config.CLI_HOME_DEFAULT).expanduser()
    before = real.exists()
    yield
    assert real.exists() == before, f"a test created or removed {real}"


@pytest.fixture
def runner() -> CliRunner:
    options: dict[str, Any] = {}
    if "mix_stderr" in signature(CliRunner).parameters:
        options["mix_stderr"] = False
    return CliRunner(**options)


@pytest.fixture
def install_source(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], Any]:
    def install(source: Any) -> Any:
        def build(variant: str | None = None) -> Any:
            return source

        monkeypatch.setattr(config, "build_source", build)
        return source

    return install


@pytest.fixture
def install_client(install_source: Callable[[Any], Any]) -> Callable[..., FakeClient]:
    def install(
        response: str | Exception | list[str | Exception] = EU_RECORDS,
        **kwargs: Any,
    ) -> FakeClient:
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
