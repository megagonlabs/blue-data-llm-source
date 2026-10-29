"""Run the actual shared kit with behavioral queries enabled."""

from __future__ import annotations

from typing import Any

import pytest
from blue_data.registry.data_registry import DataRegistry  # pyright: ignore[reportMissingTypeStubs]
from blue_data.source.json.memory_json_source import (
    MemoryJSONSource,  # pyright: ignore[reportMissingTypeStubs]
)
from blue_data.testing.conformance import (
    DataSourceConformance,  # pyright: ignore[reportMissingTypeStubs]
)

from blue_data_llm_source import LlmSource
from tests.doubles import LitellmStub, chat_payload, install_litellm

ROWS = [{"item": "alpha"}]


class TestSharedConformance(DataSourceConformance):
    @pytest.fixture(autouse=True)
    def provider(self, monkeypatch: pytest.MonkeyPatch) -> LitellmStub:
        return install_litellm(monkeypatch, LitellmStub(chat_payload('[{"item":"alpha"}]')))

    @pytest.fixture
    def data_source(self) -> LlmSource:
        return LlmSource("conformance", properties={"limits.max_calls": 1})

    @pytest.fixture
    def sample_query(self) -> Any:
        return "items", {"optional_properties": {"attrs": ["item"]}}

    @pytest.mark.parametrize("via_registry", [False, True])
    @pytest.mark.parametrize("nl", [False, True])
    @pytest.mark.parametrize("commit", [False, True])
    def test_reconstruction_answers_nonempty_reads(
        self,
        data_source: LlmSource,
        via_registry: bool,
        nl: bool,
        commit: bool,
        provider: LitellmStub,
    ) -> None:
        registry: Any = DataRegistry(store=MemoryJSONSource())
        if via_registry:
            registry.register_source(data_source)
            registry.sync_source(data_source.name)
            assert "metadata" in registry.get_source_properties(data_source.name)
            del registry.sources[data_source.name]
            rebuilt = registry.connect_source(data_source.name)
            query = registry.execute_query_nl if nl else registry.execute_query
            assert (
                query(
                    source=data_source.name, query="items", optional_properties={"commit": commit}
                )
                == ROWS
            )
        else:
            rebuilt = LlmSource(data_source.name, properties=data_source.properties)
            query = rebuilt.execute_query_nl if nl else rebuilt.execute_query
            assert query("items", optional_properties={"commit": commit}) == ROWS
        assert rebuilt._client is None  # pyright: ignore[reportPrivateUsage]
        assert len(provider.calls) == 1
