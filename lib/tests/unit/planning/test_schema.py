"""Reading step 2's response: the caller's declaration wins, and a key is never guessed."""

from __future__ import annotations

import json

import pytest

from blue_data_llm_source.planning.schema import (
    KEY_FROM_CALLER,
    KEY_FROM_INFERRED,
    KEY_FROM_NONE,
    Schema,
    deduplication_runs,
    schema_from_response,
)
from blue_data_llm_source.types import Attr


def response(**payload: object) -> str:
    return json.dumps(payload)


def attrs(*pairs: tuple[str, str | None]) -> list[Attr]:
    return [Attr(name=n, type=t) for n, t in pairs]


class TestTheCallersDeclarationWins:
    def test_a_declared_type_is_never_changed(self) -> None:
        """v1.0 promises to honour a supplied list. Enrichment is where that breaks."""
        declared = attrs(("country", "string"), ("population", "integer"))
        got = schema_from_response(
            response(
                attrs=[
                    {"name": "country", "type": "integer"},
                    {"name": "population", "type": "string"},
                ]
            ),
            declared,
        )
        assert got.attrs[:2] == tuple(declared)

    def test_a_declared_attribute_is_never_dropped(self) -> None:
        declared = attrs(("country", "string"))
        got = schema_from_response(
            response(attrs=[{"name": "capital", "type": "string"}]), declared
        )
        assert declared[0] in got.attrs

    def test_declared_attributes_come_first_and_in_order(self) -> None:
        declared = attrs(("b", "string"), ("a", "string"))
        got = schema_from_response(response(attrs=[{"name": "c", "type": "string"}]), declared)
        assert [a.name for a in got.attrs] == ["b", "a", "c"]

    def test_inference_may_add_what_the_caller_omitted(self) -> None:
        got = schema_from_response(
            response(
                attrs=[{"name": "country", "type": "string"}, {"name": "capital", "type": "string"}]
            ),
            attrs(("country", "string")),
        )
        assert [a.name for a in got.attrs] == ["country", "capital"]


class TestAKeyIsIdentifiedOrAbsent:
    def test_a_key_naming_no_column_is_refused(self) -> None:
        """Deduplicating on a field no record has groups every record under one absent value."""
        got = schema_from_response(
            response(attrs=[{"name": "country", "type": "string"}], key="iso_code"),
            [],
        )
        assert got.key is None
        assert got.key_from == KEY_FROM_NONE
        assert deduplication_runs(got) is False

    @pytest.mark.parametrize("bad_key", [None, "", "   ", 42, [], {}, True])
    def test_an_unusable_key_field_yields_no_key(self, bad_key: object) -> None:
        got = schema_from_response(
            response(attrs=[{"name": "country", "type": "string"}], key=bad_key), []
        )
        assert got.key is None
        assert got.key_from == KEY_FROM_NONE

    def test_there_is_no_positional_fallback(self) -> None:
        """Taking the first column when inference fails is arbitrary, and loses records."""
        got = schema_from_response(
            response(
                attrs=[{"name": "country", "type": "string"}, {"name": "capital", "type": "string"}]
            ),
            [],
        )
        assert got.key is None
        assert got.attrs[0].name == "country"

    def test_an_identified_key_is_marked_by_where_it_came_from(self) -> None:
        declared = attrs(("country", "string"))
        from_caller = schema_from_response(response(attrs=[], key="country"), declared)
        assert from_caller.key == "country"
        assert from_caller.key_from == KEY_FROM_CALLER

        inferred = schema_from_response(
            response(attrs=[{"name": "isbn", "type": "string"}], key="isbn"), []
        )
        assert inferred.key == "isbn"
        assert inferred.key_from == KEY_FROM_INFERRED

    def test_deduplication_runs_only_on_an_identified_key(self) -> None:
        assert deduplication_runs(schema_from_response(response(attrs=[], key=None), [])) is False
        assert (
            deduplication_runs(
                schema_from_response(
                    response(attrs=[{"name": "id", "type": "string"}], key="id"), []
                )
            )
            is True
        )


class TestUnreadableResponses:
    @pytest.mark.parametrize(
        "text",
        [None, "", "   ", "not json", "[]", '"bare"', "42", "null"],
        ids=["absent", "empty", "whitespace", "malformed", "array", "string", "number", "null"],
    )
    def test_the_callers_attributes_survive(self, text: str | None) -> None:
        declared = attrs(("country", "string"))
        got = schema_from_response(text, declared)
        assert got.attrs == tuple(declared)
        assert got.key is None
        assert got.key_from == KEY_FROM_NONE


class TestMalformedAttrEntries:
    @pytest.mark.parametrize(
        "entry",
        [42, None, "country", [], {"type": "string"}, {"name": ""}, {"name": "   "}, {"name": 42}],
        ids=["int", "null", "string", "list", "no-name", "empty-name", "blank-name", "int-name"],
    )
    def test_an_entry_without_a_usable_name_is_dropped(self, entry: object) -> None:
        got = schema_from_response(response(attrs=[entry, {"name": "ok", "type": "string"}]), [])
        assert [a.name for a in got.attrs] == ["ok"]

    def test_an_unrecognised_type_becomes_untyped_rather_than_invented(self) -> None:
        """v1.0 reports a type it cannot check so a caller is not misled about typing."""
        got = schema_from_response(response(attrs=[{"name": "when", "type": "datetime"}]), [])
        assert got.attrs == (Attr(name="when", type=None),)

    def test_a_duplicated_column_keeps_its_first_occurrence(self) -> None:
        got = schema_from_response(
            response(attrs=[{"name": "id", "type": "string"}, {"name": "id", "type": "integer"}]),
            [],
        )
        assert got.attrs == (Attr(name="id", type="string"),)

    def test_a_name_is_trimmed(self) -> None:
        got = schema_from_response(response(attrs=[{"name": "  country  ", "type": "string"}]), [])
        assert got.attrs[0].name == "country"


class TestDefaults:
    def test_a_blank_schema_does_not_permit_deduplication(self) -> None:
        blank = Schema()
        assert blank.key_from == KEY_FROM_NONE
        assert deduplication_runs(blank) is False

    def test_attrs_are_not_shared_between_instances(self) -> None:
        a, b = Schema(), Schema()
        assert a.attrs == ()
        assert b.attrs == ()
