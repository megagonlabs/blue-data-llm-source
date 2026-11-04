"""Suppress provider stdout diagnostics without replacing host handlers."""

from __future__ import annotations

import asyncio
import builtins
import io
import logging
import sys
from collections.abc import Callable
from types import ModuleType
from typing import Any, cast

import pytest

from blue_data_llm_source.llm.client import DefaultLlmClient
from tests.doubles import LitellmStub

NAMES = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy")


@pytest.fixture(autouse=True)
def restore_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    for logger in (logging.getLogger(), *(logging.getLogger(name) for name in NAMES)):
        for attribute in ("level", "propagate", "disabled"):
            monkeypatch.setattr(logger, attribute, getattr(logger, attribute))
        monkeypatch.setattr(logger, "handlers", list(logger.handlers))
        monkeypatch.setattr(logger, "filters", list(logger.filters))


def import_provider() -> object:
    return cast("Callable[[], object]", getattr(DefaultLlmClient, "_import_provider"))()


@pytest.mark.parametrize("root_level", [logging.DEBUG, logging.INFO, logging.ERROR])
@pytest.mark.parametrize(
    "provider_level",
    [logging.NOTSET, logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL],
)
def test_threshold_preserves_stricter_levels_and_host_logging(
    monkeypatch: pytest.MonkeyPatch,
    root_level: int,
    provider_level: int,
) -> None:
    provider = ModuleType("litellm")
    monkeypatch.setitem(sys.modules, "litellm", provider)
    root = logging.getLogger()
    root.setLevel(root_level)
    root_handler = logging.StreamHandler(io.StringIO())
    root.handlers[:] = [root_handler]
    handlers: list[logging.Handler] = []
    filters: list[logging.Filter] = []
    for name in NAMES:
        logger = logging.getLogger(name)
        logger.setLevel(provider_level)
        handler = logging.StreamHandler(io.StringIO())
        logger.handlers[:] = [handler]
        logger_filter = logging.Filter()
        logger.filters[:] = [logger_filter]
        handlers.append(handler)
        filters.append(logger_filter)
    for _ in range(2):
        assert import_provider() is provider
    expected = max(provider_level or root_level, logging.WARNING)
    for index, name in enumerate(NAMES):
        logger = logging.getLogger(name)
        assert logger.getEffectiveLevel() == expected
        assert logger.handlers == [handlers[index]]
        assert logger.filters == [filters[index]]
        assert logger.propagate and not logger.disabled
    assert root.level == root_level and root.handlers == [root_handler]
    assert getattr(provider, "suppress_debug_info") is True


def test_cap_is_applied_before_import(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ModuleType("litellm")
    monkeypatch.setitem(sys.modules, "litellm", provider)
    logging.getLogger().setLevel(logging.DEBUG)
    for name in NAMES:
        logging.getLogger(name).setLevel(logging.NOTSET)
    original = builtins.__import__
    seen: list[str] = []

    def importing(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "litellm":
            seen.append(name)
            assert all(logging.getLogger(n).getEffectiveLevel() == logging.WARNING for n in NAMES)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    import_provider()
    assert seen == ["litellm"]


def test_unrelated_logger_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "litellm", ModuleType("litellm"))
    unrelated = logging.getLogger("provider-output-unrelated")
    monkeypatch.setattr(unrelated, "level", logging.DEBUG)
    handler = logging.StreamHandler(io.StringIO())
    monkeypatch.setattr(unrelated, "handlers", [handler])
    import_provider()
    assert unrelated.level == logging.DEBUG and unrelated.handlers == [handler]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_sync_and_async_calls_suppress_the_banner(
    litellm_stub: Any,
    asynchronous: bool,
) -> None:
    litellm_stub(LitellmStub({"choices": [{"message": {"content": "answer"}}]}))
    instance = DefaultLlmClient(properties={"llm.model": "gpt-4o-mini"})
    if asynchronous:
        assert asyncio.run(instance.acomplete("test")) == "answer"
    else:
        assert instance.complete("test") == "answer"
    assert getattr(sys.modules["litellm"], "suppress_debug_info") is True
