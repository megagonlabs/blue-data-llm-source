"""One serialization of :class:`ExecMeta`, for every adapter that publishes it.

The CLI and the MCP server each carried their own ``meta_summary``. The two gated
different fields, so one run described itself two ways depending on which door the caller
came through: the CLI dropped ``truncated_to`` and ``cache_hits`` altogether, and the MCP
server dropped ``cache_hits`` and published four alignment diagnostics that the CLI keeps
behind ``--verbose``. Neither list was wrong. There were two of them, and a field added to
``ExecMeta`` reached whichever adapter somebody remembered.

Three tiers, and which tier a field sits in is a decision about who needs it.

*Always present.* The accounting and the result facts. A consumer branches on what these
say rather than on whether they are there, so an empty list is published as an empty list:
``gaps: []`` says nothing was observed to be missing, which is a weaker claim than complete
and the only one this package is allowed to make.

*Present when populated.* Facts that exist only because something happened — a truncation,
a cache hit, a question the model rewrote. An absent key means the thing did not happen.
``accounting_known`` belongs here in its false form only, so an ordinary result is
unchanged and an unmeasured zero is never mistaken for a measured one.

*Verbose only.* The four alignment diagnostics. They carry arbitrary model output — key
names the model invented and values it returned — so a caller asks for them.

``observed_keys`` is in no tier. It holds one entry per key the run saw, so it grows with
the answer, and ``observed_key_count`` already carries the fact a consumer acts on.

What is masked on the way out follows one line, and ``redaction`` holds it: text this
package narrates *about* a call leaves through ``redact``; the answer itself does not. The
model name, the rewritten ask, the attributes the caller asked for and an uncheckable
declared type are narration. The records are the answer, and the key-level diagnostics
index them — ``primary_keys``, ``unmapped_keys``, ``remapped_keys``, ``type_mismatches``
and ``key_collisions`` name keys the model returned, so a name masked there would describe
a record that no longer contains it.

``requested_attrs`` is the one field with a claim on both sides, and it is masked. It does
index the records, but it is also the caller's own text handed straight back, which is the
class ``unchecked_types`` is masked for. The cost of masking it is a diagnostic that
disagrees with a record key, and only where a caller named an attribute after a key
literal; the cost of not masking it is a credential pasted into an argument and handed back
into a transcript that outlives the call.

Which side a new field falls on is not left to whoever adds it: a test walks what this
view produces and requires every string in it to be masked or to carry a written reason
for not being.
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
    # An uncheckable declared type is a fact about the request rather than about the
    # answer, and reporting it once is the whole reason the field exists: a misspelled type
    # otherwise checks nothing in silence.
    if meta.unchecked_types:
        view["unchecked_types"] = [redact(name) for name in meta.unchecked_types]
    if meta.record_origins:
        view["record_origins"] = [list(entry) for entry in meta.record_origins]
    # Only when a model changed the question. A caller reading a short answer needs to know
    # which question it is short of, and that is not always the one they typed.
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
