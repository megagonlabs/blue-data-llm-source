"""Provide one serialization of :class:`ExecMeta` for every adapter.

The CLI and MCP server previously had separate ``meta_summary`` implementations with
different fields. The CLI omitted ``truncated_to`` and ``cache_hits``; the MCP server
omitted ``cache_hits`` and always published four alignment diagnostics that the CLI exposed
only with ``--verbose``. A field added to ``ExecMeta`` could also be missed by one adapter.

Fields are grouped into three tiers according to when consumers need them.

*Always present.* Accounting and result facts. Consumers branch on their values, so an empty
list is published as an empty list:
``gaps: []`` says nothing was observed to be missing, which is a weaker claim than complete
and the only one this package is allowed to make.

*Present when populated.* Facts that exist only when something happens: a truncation, a
cache hit, or a question rewritten by the model. An absent key means the event did not happen.
``accounting_known`` belongs here in its false form only, so an ordinary result is
unchanged and an unmeasured zero is never mistaken for a measured one.

*Verbose only.* The four alignment diagnostics. They carry arbitrary model output — key
names the model invented and values it returned — so a caller asks for them.

``observed_keys`` is in no tier. It holds one entry per key the run saw, so it grows with
the answer, and ``observed_key_count`` already carries the fact a consumer acts on.

The ``redaction`` module defines what is masked on the way out: text this package narrates
*about* a call passes through ``redact``; the answer itself does not. The
model name, the rewritten ask, the attributes the caller asked for and an uncheckable
declared type are narration. The records are the answer, and the key-level diagnostics
index them — ``primary_keys``, ``unmapped_keys``, ``remapped_keys``, ``type_mismatches``
and ``key_collisions`` name keys the model returned, so a name masked there would describe
a record that no longer contains it.

``requested_attrs`` could be treated as either narration or answer data, so it is masked.
It indexes records, but it is also caller text returned as-is, like ``unchecked_types``.
Masking it can make a diagnostic disagree with a record key when an attribute is named after
a key literal. Leaving it unmasked could return a credential pasted into an argument in a
transcript that outlives the call.

When a field is added, a test checks that every string in this view is masked or has a
documented reason to remain unmasked.
"""

from __future__ import annotations

from typing import Any

from blue_data_llm_source.redaction import redact
from blue_data_llm_source.types import ExecMeta

__all__ = ["meta_view"]


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
        "gaps": list(meta.gaps),
        "stop_reasons": list(meta.stop_reasons),
        "requested_attrs": [redact(name) for name in meta.requested_attrs],
    }
    if meta.normalization_collisions:
        view["normalization_collisions"] = list(meta.normalization_collisions)
    # Include only when the parser changed the model's text.
    if meta.repaired_numbers:
        view["repaired_numbers"] = meta.repaired_numbers
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
    return view
