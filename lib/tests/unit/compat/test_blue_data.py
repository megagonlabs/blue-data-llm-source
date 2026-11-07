"""The data-layer base class is inherited directly."""

from __future__ import annotations

import inspect

from blue_data.source.source import DataSource  # pyright: ignore[reportMissingTypeStubs]

from blue_data_llm_source import LlmSource
from blue_data_llm_source.compat.blue_data import BlueDataLlmSource
from tests.doubles import ScriptedClient

ONE_RECORD = '[{"name": "Ada"}]'
INHERITED_TRANSACTION_METHODS = frozenset(
    {
        "begin_transaction",
        "commit_transaction",
        "get_transaction_operations",
        "list_transactions",
        "rollback_transaction",
        "transaction",
    }
)


def _unresolved_required_methods(source_type: type[DataSource]) -> list[str]:
    shared = [
        name
        for name, _ in inspect.getmembers(DataSource, inspect.isfunction)
        if not name.startswith("_") and name not in INHERITED_TRANSACTION_METHODS
    ]
    assert "fetch_databases" in shared
    return [
        name for name in shared if getattr(source_type, name, None) is getattr(DataSource, name)
    ]


def test_the_source_is_a_data_source() -> None:
    assert issubclass(LlmSource, DataSource)


def test_the_old_adapter_name_is_the_same_class() -> None:
    """Kept so an existing import does not break, but it adds nothing now."""
    assert BlueDataLlmSource is LlmSource


def test_required_methods_override_the_base() -> None:
    """An inherited DataSource stub can return an empty result without raising."""
    assert _unresolved_required_methods(LlmSource) == []


def test_required_override_check_catches_an_inherited_stub() -> None:
    missing_override = type(
        "MissingFetchDatabases", (LlmSource,), {"fetch_databases": DataSource.fetch_databases}
    )
    assert _unresolved_required_methods(missing_override) == ["fetch_databases"]


def test_the_query_surface_answers_rather_than_inheriting_an_empty_result() -> None:
    source = LlmSource(client=ScriptedClient([ONE_RECORD]))
    assert source.execute_query("who?") == [{"name": "Ada"}]


def test_the_virtual_hierarchy_is_this_class_not_the_base() -> None:
    source = LlmSource()
    assert source.fetch_databases() != []
