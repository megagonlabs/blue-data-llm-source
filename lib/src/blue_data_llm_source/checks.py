"""Argument validation with messages a model can act on.

An LLM agent calling this library through MCP or as a tool sees only ``str(exc)``
as its tool result. Every message here names the offending value and the accepted
form so the caller can correct itself without reading the source.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from blue_data_llm_source.types import Attr, QueryError


def normalize_query(query: Any) -> str:
    """Return *query* stripped, or empty when there is nothing to ask.

    A wrong type is a caller mistake and raises. An empty or whitespace string is not a
    mistake, it is an empty request, and the caller gets an empty result carrying the
    reason instead of an exception.
    """
    if query is None:
        return ""
    if not isinstance(query, str):
        raise QueryError(f"query must be a string, got {type(query).__name__}")
    return query.strip()


def require_query(query: Any) -> str:
    """Return *query* as a non-empty string, or raise QueryError."""
    text = normalize_query(query)
    if not text:
        raise QueryError("query must not be empty or whitespace")
    return text


def normalize_attrs(attrs: Any) -> list[Attr]:
    """Accept either a list of names or a list of {name, type} dicts."""
    if attrs is None:
        return []
    if not isinstance(attrs, (list, tuple)):
        raise QueryError(
            "attrs must be a list of names or a list of dicts with a 'name' key, "
            f"got {type(attrs).__name__}"
        )
    out: list[Attr] = []
    for i, item in enumerate(cast("Sequence[Any]", attrs)):
        if isinstance(item, str):
            if not item.strip():
                raise QueryError(f"attrs[{i}] is an empty attribute name")
            out.append(Attr(name=item))
        elif isinstance(item, Attr):
            out.append(item)
        elif isinstance(item, dict):
            entry = cast("dict[Any, Any]", item)
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                raise QueryError(f"attrs[{i}] must have a non-empty string 'name', got {item!r}")
            declared = entry.get("type")
            if declared is not None and not isinstance(declared, str):
                raise QueryError(
                    f"attrs[{i}]['type'] must be a string when present, got {declared!r}"
                )
            out.append(Attr(name=name, type=declared))
        else:
            raise QueryError(
                f"attrs[{i}] must be a string or a dict with a 'name' key, "
                f"got {type(item).__name__}"
            )
    # Reject duplicate attributes before contacting the provider. The aligner checks too.
    seen: set[str] = set()
    for attr in out:
        if attr.name in seen:
            raise QueryError(f"requested attribute {attr.name!r} appears twice")
        seen.add(attr.name)
    return out


def normalize_context(context: Any) -> str:
    """Accept a string or a list of strings; return one block of text."""
    if context is None:
        return ""
    if isinstance(context, str):
        return context
    if isinstance(context, (list, tuple)):
        parts = [str(c) for c in cast("Sequence[Any]", context) if str(c).strip()]
        return "\n".join(parts)
    raise QueryError(f"context must be a string or a list of strings, got {type(context).__name__}")


def require_positive_int(value: Any, field: str) -> int | None:
    """Return *value* as a positive int, or None when unset."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise QueryError(f"{field} must be an integer, got {type(value).__name__}")
    if value <= 0:
        raise QueryError(f"{field} must be greater than zero, got {value}")
    return value
