"""The published exception tree and structural protocols."""

from __future__ import annotations

from typing import Any

from blue_data_llm_source.llm.client import DefaultLlmClient, FunctionClient
from blue_data_llm_source.types import (
    BudgetAwareClient,
    BudgetExceeded,
    CacheStore,
    ConfigError,
    ExecMeta,
    FetchResult,
    LlmClientProtocol,
    LlmSourceError,
    Outcome,
    ParseError,
    QueryError,
    TransportError,
)
from tests.doubles import FailingClient, MemoryCache, ScriptedClient


def test_every_error_shares_one_base() -> None:
    for error in (ConfigError, QueryError, TransportError, ParseError, BudgetExceeded):
        assert issubclass(error, LlmSourceError)


def test_a_transport_error_carries_its_code_and_the_provider_error() -> None:
    cause = RuntimeError("upstream")
    error = TransportError("failed", code="provider_error", provider_error=cause)
    assert str(error) == "failed"
    assert error.code == "provider_error"
    assert error.provider_error is cause


def test_a_parse_error_carries_the_raw_response() -> None:
    error = ParseError("bad json", raw="not json at all")
    assert error.raw == "not json at all"


def test_a_budget_error_names_the_limit_it_hit() -> None:
    error = BudgetExceeded("call limit reached (2)", limit="max_calls")
    assert error.limit == "max_calls"


def test_the_scripted_client_satisfies_the_client_protocol() -> None:
    assert isinstance(ScriptedClient(), LlmClientProtocol)


def test_the_failing_client_satisfies_the_client_protocol() -> None:
    assert isinstance(FailingClient(RuntimeError("x")), LlmClientProtocol)


def test_the_shipped_clients_satisfy_the_client_protocol() -> None:
    assert isinstance(DefaultLlmClient(), LlmClientProtocol)
    assert isinstance(FunctionClient(lambda prompt: "[]"), LlmClientProtocol)


def test_the_memory_cache_satisfies_the_cache_protocol() -> None:
    assert isinstance(MemoryCache(), CacheStore)


def test_an_object_missing_acomplete_is_not_a_client() -> None:
    class HalfClient:
        def complete(self, prompt: str, properties: dict[str, object] | None = None) -> str:
            return "[]"

    assert not isinstance(HalfClient(), LlmClientProtocol)


def test_an_empty_fetch_result_reports_no_records() -> None:
    result = FetchResult()
    assert len(result) == 0
    assert list(result) == []
    assert result.meta == ExecMeta()


def test_exec_meta_defaults_are_empty_rather_than_shared() -> None:
    first = ExecMeta()
    second = ExecMeta()
    first.requested_attrs.append("name")
    assert second.requested_attrs == []


def test_the_outcome_vocabulary_is_importable_from_the_package_root() -> None:
    # ExecMeta.outcome is a plain str, so the vocabulary is the only way a caller can
    # compare against it without hardcoding the literals.
    import blue_data_llm_source as pkg

    assert pkg.Outcome is Outcome
    assert "Outcome" in pkg.__all__


def test_every_outcome_value_is_listed_in_all() -> None:
    declared = {
        value
        for name, value in vars(Outcome).items()
        if not name.startswith("_") and isinstance(value, str)
    }
    assert declared == set(Outcome.ALL)


# ------------------------------------------------- the budget-aware client contract --
def test_a_client_holding_a_budget_satisfies_the_protocol() -> None:
    """Holding a budget is the whole contract, and it decides whether a limit can bind."""
    assert isinstance(ScriptedClient(), BudgetAwareClient)
    assert isinstance(DefaultLlmClient(), BudgetAwareClient)
    assert isinstance(FunctionClient(lambda _: ""), BudgetAwareClient)


def test_a_client_without_a_budget_does_not_satisfy_the_protocol() -> None:
    class Opaque:
        def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            return ""

        async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            return ""

    assert not isinstance(Opaque(), BudgetAwareClient)
    # Still a usable client; it just cannot be held to a provider-level ceiling.
    assert isinstance(Opaque(), LlmClientProtocol)


def test_the_budget_surface_is_exported_from_the_package_root() -> None:
    """A caller building a session budget should not reach into a private module."""
    import blue_data_llm_source as pkg

    for name in ("Budget", "Limits", "Reservation", "BudgetAwareClient"):
        assert hasattr(pkg, name), name
        assert name in pkg.__all__, name
