"""The field policy `meta_view` publishes, pinned field by field.

The policy is the contract two adapters now share, so it is checked here rather than in
either of them. A field moved between tiers by accident is a field that silently appears
in or disappears from a shipped surface.
"""

from __future__ import annotations

from dataclasses import fields

import blue_data_llm_source as pkg
from blue_data_llm_source import ExecMeta, meta_view

ALWAYS = {
    "outcome",
    "model",
    "calls",
    "prompt_tokens",
    "completion_tokens",
    "cost",
    "elapsed_seconds",
    "row_estimate",
    "cardinality_estimate",
    "primary_keys",
    "observed_key_count",
    "marginal_key_yields",
    "incomplete_key_records",
    "gaps",
    "stop_reasons",
    "requested_attrs",
}

WHEN_POPULATED = {
    "normalization_collisions",
    "repaired_numbers",
    "unchecked_types",
    "record_origins",
    "asked",
    "stopped_by",
    "truncated_to",
    "cache_hits",
    "accounting_known",
}

VERBOSE_ONLY = {"unmapped_keys", "remapped_keys", "type_mismatches", "key_collisions"}


def populated() -> ExecMeta:
    """An ExecMeta with every field this policy can publish carrying a value."""
    return ExecMeta(
        gaps=["leaf_empty: leaf 0.0 returned nothing"],
        outcome="partial",
        model="gpt-4o",
        calls=3,
        prompt_tokens=12,
        completion_tokens=34,
        cost=0.0012,
        latency_seconds=0.31,
        cache_hits=2,
        requested_attrs=["country"],
        row_estimate=27,
        cardinality_estimate={"expected": 27},
        primary_keys=["country"],
        observed_key_count=9,
        marginal_key_yields=[7, 2],
        incomplete_key_records=1,
        normalization_collisions=[{"kept": "France"}],
        repaired_numbers=2,
        observed_keys=["France", "Germany"],
        unmapped_keys=["capital"],
        remapped_keys={"Country": "country"},
        asked="which countries are in the EU?",
        record_origins=[["0.0"], ["0.1"]],
        type_mismatches=[{"record_index": 0, "attribute": "year", "value": "x"}],
        key_collisions=[{"record_index": 0, "attribute": "country"}],
        unchecked_types=["money"],
        truncated_to=2,
        stop_reasons=["max_rows"],
        stopped_by="max_rows",
        accounting_known=False,
    )


def test_the_always_present_tier_is_present_for_an_empty_result() -> None:
    assert set(meta_view(ExecMeta())) == ALWAYS


def test_an_empty_list_is_published_rather_than_omitted() -> None:
    """A consumer branches on what these say, not on whether the key arrived."""
    view = meta_view(ExecMeta())
    assert view["gaps"] == []
    assert view["stop_reasons"] == []
    assert view["requested_attrs"] == []
    assert view["primary_keys"] == []


def test_the_populated_tier_appears_only_when_there_is_something_to_report() -> None:
    quiet = meta_view(populated().__class__())
    assert WHEN_POPULATED.isdisjoint(quiet)
    loud = meta_view(populated())
    assert WHEN_POPULATED <= set(loud)


def test_accounting_known_is_published_only_in_its_false_form() -> None:
    assert "accounting_known" not in meta_view(ExecMeta())
    assert meta_view(ExecMeta(accounting_known=False))["accounting_known"] is False


def test_an_unhit_cache_is_not_reported_as_a_fact() -> None:
    assert "cache_hits" not in meta_view(ExecMeta(cache_hits=0))
    assert meta_view(ExecMeta(cache_hits=4))["cache_hits"] == 4


def test_the_verbose_tier_needs_the_flag() -> None:
    loud = populated()
    assert VERBOSE_ONLY.isdisjoint(meta_view(loud))
    assert VERBOSE_ONLY <= set(meta_view(loud, verbose=True))


def test_verbose_adds_nothing_else() -> None:
    plain = set(meta_view(populated()))
    verbose = set(meta_view(populated(), verbose=True))
    assert verbose - plain == VERBOSE_ONLY


def test_observed_keys_is_in_no_tier() -> None:
    """Unbounded — one entry per key the run saw — and `observed_key_count` carries the fact.

    Publishing it would grow every payload by the size of the answer.
    """
    assert "observed_keys" not in meta_view(populated(), verbose=True)


def test_every_exec_meta_field_is_either_published_or_deliberately_held_back() -> None:
    """A new field lands in a tier here or fails this test, rather than being forgotten."""
    held_back = {"observed_keys", "latency_seconds"}
    declared = {f.name for f in fields(ExecMeta)}
    covered = ALWAYS | WHEN_POPULATED | VERBOSE_ONLY | held_back
    # `elapsed_seconds` is `latency_seconds` under the name the adapters publish.
    assert declared - covered == set()
    assert covered - declared == {"elapsed_seconds"}


def test_the_serializer_is_exported_from_the_package_root() -> None:
    """Two adapters call it, so it is a supported surface rather than an internal helper."""
    assert pkg.meta_view is meta_view
    assert "meta_view" in pkg.__all__
