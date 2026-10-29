"""Reading a model's text response as records."""

from __future__ import annotations

import json

import pytest

from blue_data_llm_source.reduction.parsing import (
    parse_records,
    read_records,
    salvage_read,
    strip_fence,
)
from blue_data_llm_source.types import ParseError


def test_fenced_json_parses() -> None:
    text = '```json\n[{"name": "Ada"}]\n```'
    assert parse_records(text) == [{"name": "Ada"}]


def test_fence_without_a_language_tag_parses() -> None:
    text = '```\n[{"name": "Ada"}]\n```'
    assert parse_records(text) == [{"name": "Ada"}]


def test_bare_array_parses() -> None:
    assert parse_records('[{"name": "Ada"}, {"name": "Grace"}]') == [
        {"name": "Ada"},
        {"name": "Grace"},
    ]


def test_surrounding_whitespace_is_ignored() -> None:
    assert parse_records('\n\n  [{"name": "Ada"}]  \n') == [{"name": "Ada"}]


def test_a_single_object_is_wrapped_into_a_one_record_list() -> None:
    assert parse_records('{"name": "Ada", "age": 36}') == [{"name": "Ada", "age": 36}]


def test_items_envelope_is_unwrapped() -> None:
    assert parse_records('{"items": [{"name": "Ada"}]}') == [{"name": "Ada"}]


@pytest.mark.parametrize("envelope", ["items", "rows", "data", "results"])
def test_every_supported_envelope_key_is_unwrapped(envelope: str) -> None:
    assert parse_records(f'{{"{envelope}": [{{"name": "Ada"}}]}}') == [{"name": "Ada"}]


def test_an_envelope_key_holding_a_non_list_is_kept_as_one_record() -> None:
    assert parse_records('{"data": "Ada"}') == [{"data": "Ada"}]


def test_a_value_containing_the_word_json_survives_intact() -> None:
    # Strip the Markdown fence marker without changing "json" inside the payload.
    text = '```json\n[{"note": "the json file is at /tmp/json/data.json"}]\n```'
    assert parse_records(text) == [{"note": "the json file is at /tmp/json/data.json"}]


def test_a_key_containing_the_word_json_survives_intact() -> None:
    assert parse_records('[{"json_path": "a.b"}]') == [{"json_path": "a.b"}]


def test_strip_fence_leaves_unfenced_text_alone() -> None:
    assert strip_fence('  [{"a": 1}]  ') == '[{"a": 1}]'


def test_strip_fence_removes_one_surrounding_fence() -> None:
    assert strip_fence('```json\n[{"a": 1}]\n```') == '[{"a": 1}]'


def test_invalid_json_raises_parse_error_carrying_the_raw_text() -> None:
    text = "here you go: [name: Ada]"
    with pytest.raises(ParseError) as caught:
        parse_records(text)
    assert caught.value.raw == text
    assert "not valid JSON" in caught.value.message


def test_a_list_containing_a_non_object_raises_parse_error() -> None:
    with pytest.raises(ParseError, match="record 1 is str"):
        parse_records('[{"name": "Ada"}, "Grace"]')


def test_a_json_scalar_raises_parse_error() -> None:
    with pytest.raises(ParseError, match="not a list of records"):
        parse_records("42")


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_an_empty_response_raises_parse_error(text: str) -> None:
    with pytest.raises(ParseError, match="empty response"):
        parse_records(text)


def test_an_empty_array_parses_to_no_records() -> None:
    assert parse_records("[]") == []


# --------------------------------------------------------- duplicate object keys --
def test_a_repeated_key_in_one_object_is_rejected() -> None:
    """The decoder keeps the last and leaves no trace of the first.

    That is the opposite of the first-wins-and-report rule alignment applies to keys that
    merely normalize alike, and which of two values the model meant is not knowable here.
    """
    with pytest.raises(ParseError, match="more than once"):
        parse_records('[{"name": "Ada", "name": "Grace"}]')


def test_a_repeated_key_nested_inside_a_value_is_rejected_too() -> None:
    """Alignment only ever sees record-level keys, so nesting must not be a way past it."""
    with pytest.raises(ParseError, match="more than once"):
        parse_records('[{"person": {"name": "Ada", "name": "Grace"}}]')


def test_the_same_key_in_two_different_objects_is_fine() -> None:
    records = parse_records('[{"name": "Ada"}, {"name": "Grace"}]')
    assert records == [{"name": "Ada"}, {"name": "Grace"}]


def test_a_wrapper_with_metadata_siblings_is_still_a_wrapper() -> None:
    """A wrapper may carry metadata alongside its record list.

    Attribute matches distinguish it from a record with a list-valued column.
    """
    wanted = ["name"]
    assert parse_records('{"items": [{"name": "Ada"}, {"name": "Grace"}], "count": 2}', wanted) == [
        {"name": "Ada"},
        {"name": "Grace"},
    ]
    assert parse_records('{"results": [{"name": "Ada"}], "total": 1}', wanted) == [{"name": "Ada"}]

    # The record reading still wins when the request points at the outer keys.
    assert parse_records('{"country": "France", "data": [{"year": 2024}]}', ["country"]) == [
        {"country": "France", "data": [{"year": 2024}]}
    ]

    # With nothing requested there is no evidence either way, so nothing is discarded.
    assert parse_records('{"items": [{"name": "Ada"}], "count": 2}') == [
        {"items": [{"name": "Ada"}], "count": 2}
    ]


def test_an_envelope_key_with_siblings_is_a_record_not_a_wrapper() -> None:
    """`data`, `results`, `rows`, and `items` can be ordinary column names.

    Keep the whole record when its requested attributes favor the record reading.
    """
    kept = parse_records('{"country": "France", "data": [{"year": 2024}]}', ["country"])
    assert kept == [{"country": "France", "data": [{"year": 2024}]}]

    # A list of scalars under this key belongs to the record, not a wrapper.
    assert parse_records('{"build": 42, "results": ["pass", "fail"]}', ["build", "results"]) == [
        {"build": 42, "results": ["pass", "fail"]}
    ]

    # An empty list was the worst case: it erased a populated record and reported silence.
    assert parse_records('{"country": "France", "results": []}', ["country"]) == [
        {"country": "France", "results": []}
    ]

    # The sole-key wrapper still unwraps, which is what the shape exists for.
    assert parse_records('{"items": [{"name": "Ada"}]}') == [{"name": "Ada"}]


# ------------------------------------------------ thousands separators in values --


@pytest.mark.parametrize(
    ("text", "expected", "repaired"),
    [
        ('[{"n": 4,470, "m": "x"}]', [{"n": 4470, "m": "x"}], 1),
        ('[{"area": 1,024.56}]', [{"area": 1024.56}], 1),
        ('[{"delta": -12,345,678}]', [{"delta": -12345678}], 1),
        (
            '[{"a": 1,000, "b": 2,500.5, "c": 3}, {"a": 999,999}]',
            [{"a": 1000, "b": 2500.5, "c": 3}, {"a": 999999}],
            3,
        ),
        ('[{"last": 7,000}]', [{"last": 7000}], 1),
        ('[{"outer": {"inner": 1,234, "z": 1}}]', [{"outer": {"inner": 1234, "z": 1}}], 1),
        ('{"n" :  4,470 , "k": 1}', [{"n": 4470, "k": 1}], 1),
        ('[{"n": 4,470}\n]', [{"n": 4470}], 1),
    ],
    ids=[
        "int",
        "decimal",
        "negative-several-groups",
        "several-per-object",
        "last-before-brace",
        "nested",
        "whitespace",
        "before-array-close",
    ],
)
def test_grouped_numbers_in_values_are_read(
    text: str, expected: list[dict[str, object]], repaired: int
) -> None:
    assert read_records(text) == (expected, repaired)
    assert parse_records(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('[{"note": "1,234 people", "n": 1}]', [{"note": "1,234 people", "n": 1}]),
        ('[{"1,234": "key", "v": 2}]', [{"1,234": "key", "v": 2}]),
        ('[{"list": [1,234]}]', [{"list": [1, 234]}]),
        ('[{"quoted": "a \\": 1,234"}]', [{"quoted": 'a ": 1,234'}]),
    ],
    ids=["string-value", "key", "array", "escaped-quote-in-string"],
)
def test_valid_json_is_read_as_it_stands(text: str, expected: list[dict[str, object]]) -> None:
    assert read_records(text) == (expected, 0)


def test_a_grouped_number_beside_a_string_that_looks_like_one_repairs_only_the_value() -> None:
    text = '[{"note": "x: 1,234,", "n": 5,000}]'
    assert read_records(text) == ([{"note": "x: 1,234,", "n": 5000}], 1)


@pytest.mark.parametrize(
    "text",
    [
        '[{"a": 12,34}]',
        '[{"a": 1,2345}]',
        '[{"a": 1,234 garbage}]',
        '[{"a": 1,234, "b": }]',
        '[{"a": 1,234, "a": 2}]',
        '[{"a": 1,234, "b": 1e999}]',
    ],
    ids=[
        "bad-grouping",
        "long-group",
        "garbage-after",
        "still-invalid-after-repair",
        "duplicate-key-after-repair",
        "overflow-after-repair",
    ],
)
def test_a_response_the_repair_cannot_make_valid_raises_the_original_error(text: str) -> None:
    """The error describes what the model wrote, not the text after an attempted repair."""
    with pytest.raises(ValueError) as strict:
        json.loads(text)
    with pytest.raises(ParseError) as caught:
        parse_records(text)
    assert caught.value.raw == text
    assert str(strict.value) in caught.value.message


def test_a_truncated_response_with_grouped_numbers_is_salvaged_and_counted() -> None:
    text = '[{"n": 1,000}, {"n": 2,000}, {"n": 3,0'
    assert salvage_read(text) == ([{"n": 1000}, {"n": 2000}], 2)
