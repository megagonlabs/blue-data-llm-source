"""Serialize execution metadata for the CLI and MCP adapters.

Records remain unchanged. Metadata describes the call and may contain caller text, model
text, or values discarded during reduction. Mask credential-shaped strings throughout
that metadata before it enters a terminal or agent transcript. Ordinary diagnostic keys
and values retain their original spelling.
"""

from __future__ import annotations

from typing import Any, cast

from blue_data_llm_source.redaction import redact
from blue_data_llm_source.types import ExecMeta

__all__ = ["meta_view"]


def _mask_metadata(value: Any) -> Any:
    """Mask text recursively, including keys in diagnostic dictionaries."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_mask_metadata(item) for item in cast("list[Any]", value)]
    if isinstance(value, dict):
        return {
            redact(key) if isinstance(key, str) else key: _mask_metadata(item)
            for key, item in cast("dict[Any, Any]", value).items()
        }
    return value


def meta_view(meta: ExecMeta, *, verbose: bool = False) -> dict[str, Any]:
    """*meta* as a JSON-ready dict, under the field policy this module documents.

    *verbose* adds the four alignment diagnostics. An adapter whose surface already
    publishes them passes ``True``; one that gates them behind a flag passes the flag.
    """
    view: dict[str, Any] = {
        "outcome": meta.outcome,
        "model": redact(meta.model),
        "calls": meta.calls,
        "prompt_tokens": meta.prompt_tokens,
        "completion_tokens": meta.completion_tokens,
        "cost": round(meta.cost, 6),
        "elapsed_seconds": round(meta.latency_seconds, 3),
        "row_estimate": meta.row_estimate,
        "cardinality_estimate": meta.cardinality_estimate,
        "primary_keys": list(meta.primary_keys),
        "observed_key_count": meta.observed_key_count,
        "marginal_key_yields": list(meta.marginal_key_yields),
        "incomplete_key_records": meta.incomplete_key_records,
        "filter_contradictions": meta.filter_contradictions,
        "gaps": list(meta.gaps),
        "stop_reasons": list(meta.stop_reasons),
        "requested_attrs": [redact(name) for name in meta.requested_attrs],
    }
    if meta.normalization_collisions:
        view["normalization_collisions"] = list(meta.normalization_collisions)
    # Include only when the parser changed the model's text.
    if meta.repaired_numbers:
        view["repaired_numbers"] = meta.repaired_numbers
    # Include only when a root probe ran, so the default result's shape does not change.
    if meta.split_decision is not None:
        view["split_decision"] = meta.split_decision
    # Report an uncheckable declared type once. A misspelled type would otherwise check
    # nothing without notice.
    if meta.unchecked_types:
        view["unchecked_types"] = [redact(name) for name in meta.unchecked_types]
    if meta.record_origins:
        view["record_origins"] = [list(entry) for entry in meta.record_origins]
    # Include the rewritten question so callers can see which question a short answer covers.
    if meta.asked is not None:
        view["asked"] = redact(meta.asked)
    if meta.stopped_by:
        view["stopped_by"] = meta.stopped_by
    if meta.truncated_to is not None:
        view["truncated_to"] = meta.truncated_to
    if meta.cache_hits:
        view["cache_hits"] = meta.cache_hits
    if not meta.accounting_known:
        view["accounting_known"] = False
    if verbose:
        if meta.unmapped_keys:
            view["unmapped_keys"] = list(meta.unmapped_keys)
        if meta.remapped_keys:
            view["remapped_keys"] = dict(meta.remapped_keys)
        if meta.type_mismatches:
            view["type_mismatches"] = list(meta.type_mismatches)
        if meta.key_collisions:
            view["key_collisions"] = list(meta.key_collisions)
    return _mask_metadata(view)
