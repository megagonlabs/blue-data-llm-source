"""Format coercion against declared types, and duplicate removal."""

from __future__ import annotations

import pytest

from blue_data_llm_source.reduction.verifier import dedupe, drop_empty, verify
from blue_data_llm_source.types import Attr


def test_an_int_string_is_coerced_to_an_int() -> None:
    assert verify([{"age": "36"}], [Attr("age", "int")])[0] == [{"age": 36}]


def test_an_int_string_with_thousands_separators_is_coerced() -> None:
    assert verify([{"votes": "1,234"}], [Attr("votes", "int")])[0] == [{"votes": 1234}]


def test_a_float_string_is_coerced_to_a_float() -> None:
    assert verify([{"share": "51.4"}], [Attr("share", "float")])[0] == [{"share": 51.4}]


@pytest.mark.parametrize(("text", "expected"), [("yes", True), ("no", False), ("1", True)])
def test_a_bool_string_is_coerced_to_a_bool(text: str, expected: bool) -> None:
    assert verify([{"incumbent": text}], [Attr("incumbent", "bool")])[0] == [
        {"incumbent": expected}
    ]


@pytest.mark.parametrize("text", ["2024-11-15", "2024/11/15", "15-11-2024", "November 15, 2024"])
def test_a_date_string_is_coerced_to_an_iso_date(text: str) -> None:
    assert verify([{"sworn_in": text}], [Attr("sworn_in", "date")])[0] == [
        {"sworn_in": "2024-11-15"}
    ]


@pytest.mark.parametrize(
    ("declared", "raw", "expected"),
    [("INT", "7", 7), (" Integer ", "7", 7), ("number", "7", 7.0), ("Boolean", "yes", True)],
)
def test_a_declared_type_is_matched_case_and_space_insensitively(
    declared: str, raw: str, expected: object
) -> None:
    assert verify([{"value": raw}], [Attr("value", declared)])[0] == [{"value": expected}]


def test_a_non_coercible_int_is_left_unchanged_rather_than_dropped() -> None:
    assert verify([{"age": "about forty"}], [Attr("age", "int")])[0] == [{"age": "about forty"}]


def test_a_non_coercible_float_is_left_unchanged() -> None:
    assert verify([{"share": "most"}], [Attr("share", "float")])[0] == [{"share": "most"}]


def test_a_non_coercible_bool_is_left_unchanged() -> None:
    assert verify([{"incumbent": "maybe"}], [Attr("incumbent", "bool")])[0] == [
        {"incumbent": "maybe"}
    ]


def test_a_non_coercible_date_is_left_unchanged() -> None:
    assert verify([{"sworn_in": "some time in 2024"}], [Attr("sworn_in", "date")])[0] == [
        {"sworn_in": "some time in 2024"}
    ]


def test_an_unknown_declared_type_leaves_the_value_alone() -> None:
    assert verify([{"name": "Ada"}], [Attr("name", "citizen")])[0] == [{"name": "Ada"}]


def test_a_none_value_is_not_coerced() -> None:
    assert verify([{"age": None}], [Attr("age", "int")])[0] == [{"age": None}]


def test_records_pass_through_when_no_type_is_declared() -> None:
    records = [{"age": "36"}]
    assert verify(records, [Attr("age")])[0] == records


def test_untyped_attributes_leave_other_fields_alone() -> None:
    records = [{"age": "36", "name": "Ada"}]
    assert verify(records, [Attr("age", "int"), Attr("name")])[0] == [{"age": 36, "name": "Ada"}]


def test_exact_duplicate_records_collapse() -> None:
    records = [{"name": "Ada"}, {"name": "Ada"}]
    assert dedupe(records)[0] == [{"name": "Ada"}]


def test_key_order_does_not_make_two_records_distinct() -> None:
    records = [{"name": "Ada", "age": 36}, {"age": 36, "name": "Ada"}]
    assert dedupe(records)[0] == [{"name": "Ada", "age": 36}]


def test_distinct_records_are_all_kept_in_order() -> None:
    records = [{"name": "Ada"}, {"name": "Grace"}, {"name": "Katherine"}]
    assert dedupe(records)[0] == records


def test_records_differing_in_one_field_are_distinct() -> None:
    records = [{"name": "Ada", "age": 36}, {"name": "Ada", "age": 37}]
    assert dedupe(records)[0] == records


def test_dedupe_on_a_key_collapses_records_sharing_that_key() -> None:
    records = [{"name": "Ada", "age": 36}, {"name": "Ada", "age": 37}]
    assert dedupe(records, key="name")[0] == [{"name": "Ada", "age": 36}]


def test_dedupe_handles_unhashable_values() -> None:
    records = [{"terms": [2016, 2024]}, {"terms": [2016, 2024]}, {"terms": [2020]}]
    assert dedupe(records)[0] == [{"terms": [2016, 2024]}, {"terms": [2020]}]


def test_a_record_two_parts_produced_keeps_both_as_its_origin() -> None:
    """This pass drops where the merge above it appends.

    The merge records that two parts both returned a record by extending that record's
    origin list. Dropping the second copy here without folding its origin in leaves the
    survivor naming one part — not a smaller claim than naming both, but a different one.
    """
    records = [{"name": "Ada"}, {"name": "Ada"}, {"name": "Grace"}]
    kept, origins = dedupe(records, origins=[["0.0"], ["0.1"], ["0.1"]])
    assert kept == [{"name": "Ada"}, {"name": "Grace"}]
    assert origins == [["0.0", "0.1"], ["0.1"]]


def test_dedupe_without_origins_reports_none() -> None:
    kept, origins = dedupe([{"name": "Ada"}, {"name": "Ada"}])
    assert kept == [{"name": "Ada"}]
    assert origins == [[]]


# ------------------------------------------------ presence versus a null answer --
def test_a_record_whose_only_answer_is_null_is_kept() -> None:
    """A JSON null is an answer. Only an unanswered attribute is an absence.

    Alignment fills a missing attribute with None and a null is also None, so deciding
    this from the values would discard a real answer with no way to tell afterwards.
    """
    kept = drop_empty([{"party": None}], [{"party"}], [Attr("party")])
    assert kept == [{"party": None}]


def test_a_record_that_answered_nothing_is_dropped() -> None:
    dropped = drop_empty([{"party": None}], [set()], [Attr("party")])
    assert dropped == []


def test_a_falsy_answer_is_kept() -> None:
    kept = drop_empty(
        [{"count": 0}, {"count": ""}, {"count": False}],
        [{"count"}, {"count"}, {"count"}],
        [Attr("count")],
    )
    assert len(kept) == 3


def test_records_pass_through_when_nothing_was_requested() -> None:
    records = [{"anything": 1}]
    assert drop_empty(records, [{"anything"}], []) == records


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2,746,388", 2746388),  # a thousands grouping, which is what the strip is for
        ("1,234", 1234),
        ("3,80", "3,80"),  # a decimal comma: left alone and reported, not turned into 380
        ("1.234,56", "1.234,56"),  # European grouping and decimal together
        ("1,2345", "1,2345"),  # not a three-digit grouping
        ("12,34,567", "12,34,567"),  # Indian grouping is not this shape either
    ],
)
def test_a_comma_is_only_stripped_when_it_is_a_thousands_grouping(
    text: str, expected: str | int
) -> None:
    """Stripping every comma made a decimal comma a number three orders of magnitude wrong.

    `"3,80"` became `380.0` with `outcome=ok` and an empty mismatch list, and the original
    string survives nowhere in the result, so the caller could not reconcile it. A model
    answers this way whenever the query or the figure is European.
    """
    records, mismatches = verify([{"v": text}], [Attr("v", type="float")])
    got = records[0]["v"]
    if isinstance(expected, str):
        assert got == expected, "left as it arrived"
        assert [m["attribute"] for m in mismatches] == ["v"], "and reported"
    else:
        assert got == float(expected)
        assert mismatches == []
