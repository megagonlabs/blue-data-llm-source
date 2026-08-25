"""LLM-as-data-source for the Blue data-intelligence layer."""

from __future__ import annotations

from blue_data_llm_source._constants import (
    DEFAULT_VARIANT,
    OP_ESTIMATE,
    OP_FETCH,
    OP_MODELS,
    PROTOCOL,
)
from blue_data_llm_source.cache import FileCache, MemoryCache
from blue_data_llm_source.llm.budget import Budget, Limits, Reservation
from blue_data_llm_source.llm.client import DefaultLlmClient, FunctionClient
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import (
    Attr,
    BudgetAwareClient,
    BudgetExceeded,
    CacheStore,
    Capability,
    ConfigError,
    Estimate,
    ExecMeta,
    FetchResult,
    LlmClientProtocol,
    LlmSourceError,
    Outcome,
    ParseError,
    QueryError,
    TransportError,
    Usage,
)

__version__ = "1.1.0"

__all__ = [
    "Attr",
    "Budget",
    "BudgetAwareClient",
    "Capability",
    "BudgetExceeded",
    "CacheStore",
    "ConfigError",
    "Estimate",
    "DEFAULT_VARIANT",
    "DefaultLlmClient",
    "ExecMeta",
    "FetchResult",
    "FileCache",
    "FunctionClient",
    "Limits",
    "LlmClientProtocol",
    "LlmSource",
    "LlmSourceError",
    "MemoryCache",
    "OP_ESTIMATE",
    "OP_FETCH",
    "OP_MODELS",
    "Outcome",
    "PROTOCOL",
    "ParseError",
    "QueryError",
    "Reservation",
    "TransportError",
    "Usage",
    "__version__",
]
