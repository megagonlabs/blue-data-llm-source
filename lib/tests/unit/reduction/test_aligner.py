"""Mapping returned keys onto requested attributes, and reporting what did not fit."""

from __future__ import annotations

import pytest

from blue_data_llm_source.reduction.aligner import align
from blue_data_llm_source.types import Attr, QueryError


def test_an_exact_key_match_maps_and_is_not_reported_as_remapped() -> None:
    result = align([{"name": "Ada"}], [Attr("name")])
    assert result.records == [{"name": "Ada"}]
    assert result.remapped == {}
    assert result.unmapped == []


def test_a_key_differing_only_in_case_maps() -> None:
    result = align([{"Name": "Ada"}], [Attr("name")])
    assert result.records == [{"name": "Ada"}]
    assert result.remapped == {"Name": "name"}


def test_a_key_differing_only_in_punctuation_maps() -> None:
    result = align([{"first_name": "Ada"}], [Attr("first name")])
    assert result.records == [{"first name": "Ada"}]
    assert result.remapped == {"first_name": "first name"}


def test_normalization_ignores_spaces_hyphens_and_case() -> None:
    result = align([{"First-Name": "Ada"}], [Attr("first name")])
    assert result.records == [{"first name": "Ada"}]


def test_an_unmappable_key_is_reported_and_not_silently_dropped() -> None:
    result = align([{"name": "Ada", "nickname": "Countess"}], [Attr("name")])
    assert result.unmapped == ["nickname"]
    assert result.records == [{"name": "Ada"}]


def test_an_unmappable_key_is_reported_once_across_records() -> None:
    records = [{"nickname": "a"}, {"nickname": "b"}]
    assert align(records, [Attr("name")]).unmapped == ["nickname"]


def test_a_requested_attribute_missing_from_the_response_becomes_none() -> None:
    result = align([{"name": "Ada"}], [Attr("name"), Attr("party")])
    assert result.records == [{"name": "Ada", "party": None}]


def test_aligned_records_follow_the_requested_attribute_order() -> None:
    result = align([{"party": "x", "name": "Ada"}], [Attr("name"), Attr("party")])
    assert list(result.records[0]) == ["name", "party"]


def test_records_pass_through_unchanged_when_nothing_is_requested() -> None:
    records = [{"name": "Ada", "nickname": "Countess"}]
    result = align(records, [])
    assert result.records == records
    assert result.unmapped == []
    assert result.remapped == {}


def test_observed_keys_report_every_key_the_model_returned() -> None:
    records = [{"name": "Ada", "age": 36}, {"name": "Grace", "party": "None"}]
    result = align(records, [Attr("name")])
    assert result.observed_keys == ["name", "age", "party"]


def test_observed_keys_are_reported_even_with_no_requested_attributes() -> None:
    result = align([{"name": "Ada"}], [])
    assert result.observed_keys == ["name"]


def test_alignment_of_several_records_maps_each_one() -> None:
    records = [{"Name": "Ada"}, {"name": "Grace"}, {"first_name": "Katherine"}]
    result = align(records, [Attr("name")])
    assert result.records == [{"name": "Ada"}, {"name": "Grace"}, {"name": None}]
    assert result.unmapped == ["first_name"]


# ---------------------------------------------------- normalized key collisions --
def test_two_keys_matching_one_attribute_keep_the_first_and_report_the_second() -> None:
    """Two spellings that normalize alike, which the parser passes through unchanged.

    Two *identical* keys are a different case and are rejected at the parser.
    """
    result = align([{"First Name": "Ada", "first_name": "Grace"}], [Attr("first name")])

    assert result.records == [{"first name": "Ada"}]
    assert len(result.collisions) == 1
    assert result.collisions[0]["attribute"] == "first name"
    assert result.collisions[0]["ignored_value"] == "Grace"


def test_key_order_selects_survivor_and_reports_discarded_value() -> None:
    forward = align([{"First Name": "Ada", "first_name": "Grace"}], [Attr("first name")])
    reverse = align([{"first_name": "Grace", "First Name": "Ada"}], [Attr("first name")])

    assert forward.records[0]["first name"] == "Ada"
    assert reverse.records[0]["first name"] == "Grace"
    assert len(forward.collisions) == len(reverse.collisions) == 1
    assert forward.collisions[0]["ignored_value"] == "Grace"
    assert reverse.collisions[0]["ignored_value"] == "Ada"


def test_a_collision_still_counts_as_answered() -> None:
    result = align([{"First Name": "Ada", "first_name": "Grace"}], [Attr("first name")])
    assert result.matched == [{"first name"}]


def test_two_requested_attributes_that_normalize_alike_are_still_usable() -> None:
    """The fuzzy index cannot separate them, so it is not consulted for them.

    Refusing the whole request was too strong: two names whose normalized forms coincide are
    still distinct names, and a model returning them exactly needs no fuzzy matching at all.
    A key that only matches fuzzily is reported unmapped rather than assigned by guess.
    """
    exact = align([{"first name": 1, "first_name": 2}], [Attr("first name"), Attr("first_name")])
    assert exact.records == [{"first name": 1, "first_name": 2}]
    assert exact.unmapped == []

    # `firstname` matches both by normalization and neither exactly, so it is not guessed.
    ambiguous = align([{"firstname": 1}], [Attr("first name"), Attr("first_name")])
    assert ambiguous.unmapped == ["firstname"]
    assert ambiguous.records == [{"first name": None, "first_name": None}]


def test_the_same_attribute_requested_twice_is_refused() -> None:
    """Distinct names are fine; the same name twice is a malformed request."""
    with pytest.raises(QueryError, match="appears twice"):
        align([{"a": 1}], [Attr("name"), Attr("name")])
