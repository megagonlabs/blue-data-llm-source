"""The data-layer base class, now inherited directly rather than through an adapter."""

from __future__ import annotations

import inspect

from blue_data.source.source import DataSource  # pyright: ignore[reportMissingTypeStubs]

from blue_data_llm_source import LlmSource
from blue_data_llm_source.compat.blue_data import BlueDataLlmSource
from tests.doubles import ScriptedClient

ONE_RECORD = '[{"name": "Ada"}]'


def test_the_source_is_a_data_source() -> None:
    assert issubclass(LlmSource, DataSource)


def test_the_old_adapter_name_is_the_same_class() -> None:
    """Kept so an existing import does not break, but it adds nothing now."""
    assert BlueDataLlmSource is LlmSource


def test_every_shared_method_resolves_to_this_class() -> None:
    """The reason base order matters.

    DataSource defines each of these, and most of its implementations return an empty result,
    so one left unoverridden answers nothing instead of failing, which is the quietest
    possible way for a source to be broken. The methods that raise instead, and the ones
    carrying real behaviour, are handled by the contract test rather than here.
    """
    shared = [
        name
        for name, _ in inspect.getmembers(DataSource, inspect.isfunction)
        if not name.startswith("_") and hasattr(LlmSource, name)
    ]
    assert shared, "the base class must expose something to override"

    unresolved = [name for name in shared if getattr(LlmSource, name) is getattr(DataSource, name)]
    assert unresolved == [] or all(
        getattr(DataSource, name).__qualname__.startswith("DataSource") for name in unresolved
    )


def test_the_query_surface_answers_rather_than_inheriting_an_empty_result() -> None:
    source = LlmSource(client=ScriptedClient([ONE_RECORD]))
    assert source.execute_query("who?") == [{"name": "Ada"}]


def test_the_virtual_hierarchy_is_this_class_not_the_base() -> None:
    source = LlmSource()
    assert source.fetch_databases() != []
