"""Argument validation: what is accepted, and what the rejection says."""

from __future__ import annotations

import pytest

from blue_data_llm_source import checks
from blue_data_llm_source.types import Attr, QueryError


def test_require_query_returns_the_query_unchanged() -> None:
    assert checks.require_query("who is the president?") == "who is the president?"


@pytest.mark.parametrize("query", ["", "   ", "\n\t "])
def test_require_query_rejects_empty_and_whitespace(query: str) -> None:
    with pytest.raises(QueryError, match="empty or whitespace"):
        checks.require_query(query)


def test_require_query_rejects_a_non_string_and_names_the_type() -> None:
    with pytest.raises(QueryError, match="got list"):
        checks.require_query(["who?"])


def test_normalize_attrs_treats_none_as_no_attributes() -> None:
    assert checks.normalize_attrs(None) == []


def test_normalize_attrs_accepts_a_list_of_names() -> None:
    assert checks.normalize_attrs(["name", "party"]) == [Attr("name"), Attr("party")]


def test_normalize_attrs_accepts_a_list_of_dicts_with_types() -> None:
    attrs = checks.normalize_attrs([{"name": "age", "type": "int"}])
    assert attrs == [Attr(name="age", type="int")]


def test_both_attribute_forms_normalize_to_the_same_attrs() -> None:
    names = checks.normalize_attrs(["first name", "age", "party"])
    dicts = checks.normalize_attrs([{"name": "first name"}, {"name": "age"}, {"name": "party"}])
    assert names == dicts


def test_normalize_attrs_passes_attr_instances_through() -> None:
    attr = Attr(name="party", type="str")
    assert checks.normalize_attrs([attr]) == [attr]


def test_normalize_attrs_rejects_an_empty_name_and_names_the_index() -> None:
    with pytest.raises(QueryError, match=r"attrs\[1\]"):
        checks.normalize_attrs(["name", "  "])


def test_normalize_attrs_rejects_a_dict_without_a_name() -> None:
    with pytest.raises(QueryError, match="non-empty string 'name'"):
        checks.normalize_attrs([{"type": "int"}])


def test_normalize_attrs_rejects_a_non_string_declared_type() -> None:
    with pytest.raises(QueryError, match=r"attrs\[0\]\['type'\]"):
        checks.normalize_attrs([{"name": "age", "type": 3}])


def test_normalize_attrs_rejects_a_non_sequence() -> None:
    with pytest.raises(QueryError, match="got str"):
        checks.normalize_attrs("name")


def test_normalize_attrs_rejects_an_unusable_item_type() -> None:
    with pytest.raises(QueryError, match="got int"):
        checks.normalize_attrs([7])


def test_normalize_context_treats_none_as_empty_text() -> None:
    assert checks.normalize_context(None) == ""


def test_normalize_context_keeps_a_string_verbatim() -> None:
    assert checks.normalize_context("use last names") == "use last names"


def test_normalize_context_joins_a_list_and_drops_blank_parts() -> None:
    assert checks.normalize_context(["first", "  ", "second"]) == "first\nsecond"


def test_normalize_context_rejects_an_unusable_type() -> None:
    with pytest.raises(QueryError, match="got int"):
        checks.normalize_context(7)


def test_require_positive_int_accepts_none_as_unset() -> None:
    assert checks.require_positive_int(None, "max_records") is None


def test_require_positive_int_accepts_a_positive_value() -> None:
    assert checks.require_positive_int(3, "max_records") == 3


def test_require_positive_int_rejects_zero_and_negatives() -> None:
    with pytest.raises(QueryError, match="greater than zero, got 0"):
        checks.require_positive_int(0, "max_records")


def test_require_positive_int_rejects_a_bool() -> None:
    with pytest.raises(QueryError, match="must be an integer, got bool"):
        checks.require_positive_int(True, "max_records")


def test_require_positive_int_names_the_field_it_rejected() -> None:
    with pytest.raises(QueryError, match="max_records"):
        checks.require_positive_int("3", "max_records")
