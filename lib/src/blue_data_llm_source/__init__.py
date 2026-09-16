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
from blue_data_llm_source.planning.analysis import DataTask, QueryAnalysisResult, QueryAnalyzer
from blue_data_llm_source.planning.cardinality import CardinalityEstimate, CardinalityEstimator
from blue_data_llm_source.planning.context import PlanningContext
from blue_data_llm_source.planning.receipt import PlanningReceipt
from blue_data_llm_source.planning.schema import SchemaDefinition, SchemaGenerator
from blue_data_llm_source.reduction.identity import KeyTuple, key_identity
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import (
    Attr,
    BudgetAwareClient,
    BudgetExceeded,
    CacheStore,
    Capability,
    ConfigError,
    Constraint,
    Estimate,
    Exact,
    ExecMeta,
    FetchResult,
    JsonScalar,
    LlmClientProtocol,
    LlmSourceError,
    OneOf,
    Outcome,
    ParseError,
    QueryError,
    TransportError,
    Usage,
)

__version__ = "1.2.0"

__all__ = [
    "Attr",
    "Budget",
    "BudgetAwareClient",
    "Capability",
    "CardinalityEstimate",
    "CardinalityEstimator",
    "BudgetExceeded",
    "CacheStore",
    "ConfigError",
    "Constraint",
    "DataTask",
    "Estimate",
    "Exact",
    "DEFAULT_VARIANT",
    "DefaultLlmClient",
    "ExecMeta",
    "FetchResult",
    "FileCache",
    "FunctionClient",
    "Limits",
    "LlmClientProtocol",
    "KeyTuple",
    "JsonScalar",
    "LlmSource",
    "LlmSourceError",
    "MemoryCache",
    "OP_ESTIMATE",
    "OP_FETCH",
    "OP_MODELS",
    "OneOf",
    "Outcome",
    "PROTOCOL",
    "ParseError",
    "PlanningContext",
    "PlanningReceipt",
    "QueryAnalysisResult",
    "QueryAnalyzer",
    "QueryError",
    "Reservation",
    "SchemaDefinition",
    "SchemaGenerator",
    "TransportError",
    "Usage",
    "key_identity",
    "__version__",
]
