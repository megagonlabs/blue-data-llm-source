"""Reading a response that stopped mid-document.

The defect this closes cost a whole call and returned nothing: a model that reached its
output ceiling partway through the eighteenth record had its entire answer rejected, so
seventeen complete records that were paid for went in the bin and the caller got an
exception.

Two properties matter here and they pull against each other. Salvage has to recover the
whole records from a document no JSON reader will touch, and it has to refuse everything
else — because a response that is unreadable for any other reason is a genuine failure, and
turning that into a short answer would hide it behind rows that look fine.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from blue_data_llm_source.reduction.parsing import parse_records, salvage_records
from blue_data_llm_source.types import ParseError
from tests.doubles import TruncatingModel, cut_inside_record

AIRPORTS: list[dict[str, Any]] = [
    {"name": "Charles de Gaulle", "city": "Paris"},
    {"name": "Orly", "city": "Paris"},
    {"name": "Nice Côte d'Azur", "city": "Nice"},
    {"name": "Lyon-Saint Exupéry", "city": "Lyon"},
]


def truncated(records: list[dict[str, Any]], *, after: int, fence: bool = False) -> str:
    """The document *records* would make, cut partway through the record after *after*."""
    model = TruncatingModel(records, ceiling=cut_inside_record(records, after=after), fence=fence)
    answer = model("")
    assert model.truncations == 1, "the fixture did not truncate, so it proves nothing"
    assert model.delivered == after, "the cut did not land where the test says it did"
    return answer


class TestWholeRecordsSurviveTheCut:
    def test_a_document_cut_mid_record_yields_the_records_above_the_cut(self) -> None:
        answer = truncated(AIRPORTS, after=2)

        with pytest.raises(ParseError):
            parse_records(answer)

        assert salvage_records(answer) == AIRPORTS[:2]

    def test_the_cut_can_land_inside_a_string_value(self) -> None:
        # A ceiling stops at a token, not at a delimiter, so the commonest place for a
        # document to end is halfway through a name.
        answer = '[{"name": "Orly"}, {"name": "Charles de Ga'

        assert salvage_records(answer) == [{"name": "Orly"}]

    def test_the_cut_can_land_inside_an_escape_sequence(self) -> None:
        # The backslash opens an escape that never arrives. A scan that forgot escapes
        # would read the following quote as ending the string and count structure from
        # inside a value.
        answer = '[{"name": "Orly"}, {"name": "a\\'

        assert salvage_records(answer) == [{"name": "Orly"}]

    def test_a_brace_inside_a_value_does_not_move_the_cut(self) -> None:
        records = [{"name": "}{ Airport", "note": '"quoted"'}, {"name": "Orly"}]
        answer = truncated([*records, {"name": "Nice"}], after=2)

        assert salvage_records(answer) == records

    def test_an_unclosed_fence_is_stripped_like_a_closed_one(self) -> None:
        # Truncation removes the closing fence along with the tail of the document, so the
        # opening marker is left with nothing to pair with.
        answer = truncated(AIRPORTS, after=2, fence=True)

        assert answer.startswith("```json")
        assert not answer.endswith("```")
        assert salvage_records(answer) == AIRPORTS[:2]

    def test_a_wrapper_envelope_is_salvaged_and_unwrapped(self) -> None:
        answer = '{"count": 4, "items": [{"name": "Orly"}, {"name": "Nice"}, {"nam'

        assert salvage_records(answer, ["name"]) == [{"name": "Orly"}, {"name": "Nice"}]

    def test_a_nested_array_inside_a_record_does_not_end_it(self) -> None:
        answer = '[{"name": "Orly", "runways": [1, 2]}, {"name": "Ni'

        assert salvage_records(answer) == [{"name": "Orly", "runways": [1, 2]}]


class TestEverythingElseIsStillAFailure:
    """Salvage that accepted more than truncation would hide real failures behind rows."""

    def test_a_complete_document_is_not_salvage_material(self) -> None:
        # It parses. Reaching salvage at all would mean the parser rejected it for a
        # reason salvage must not paper over.
        with pytest.raises(ParseError, match="does not look truncated"):
            salvage_records(json.dumps(AIRPORTS))

    def test_a_closed_but_malformed_document_is_refused(self) -> None:
        with pytest.raises(ParseError, match="does not look truncated"):
            salvage_records('[{"name": "Orly",,}]')

    def test_mismatched_brackets_are_refused(self) -> None:
        # Malformed rather than cut off. A document whose structure disagrees with itself
        # has no prefix anyone can vouch for.
        with pytest.raises(ParseError, match="does not look truncated"):
            salvage_records('[{"name": "Orly"]')

    def test_a_cut_before_the_first_record_closes_yields_nothing(self) -> None:
        with pytest.raises(ParseError, match="does not look truncated"):
            salvage_records('[{"name": "Charles de Ga')

    def test_prose_is_refused(self) -> None:
        with pytest.raises(ParseError, match="does not look truncated"):
            salvage_records("I am sorry, I cannot help with that request.")

    def test_an_empty_response_is_refused(self) -> None:
        with pytest.raises(ParseError, match="empty response"):
            salvage_records("   ")


class TestSalvagedRecordsObeyTheSameRulesAsWholeOnes:
    """A second reader with its own rules is how two parsers drift apart.

    Salvage returns a prefix to :func:`parse_records` rather than reading it, so anything
    the parser refuses in a whole document it also refuses in a recovered one.
    """

    def test_a_duplicate_key_above_the_cut_is_still_rejected(self) -> None:
        answer = '[{"name": "Orly", "name": "Nice"}, {"name": "Ly'

        with pytest.raises(ParseError, match="more than once"):
            salvage_records(answer)

    def test_a_non_finite_number_above_the_cut_is_still_rejected(self) -> None:
        answer = '[{"name": "Orly", "runways": NaN}, {"name": "Ly'

        with pytest.raises(ParseError, match="not valid JSON"):
            salvage_records(answer)

    def test_a_scalar_above_the_cut_is_still_not_a_record(self) -> None:
        answer = '["Orly", {"name": "Nice"}, {"nam'

        with pytest.raises(ParseError, match="expected an object"):
            salvage_records(answer)

    def test_a_list_of_scalars_has_no_record_to_recover(self) -> None:
        # Only a closing brace or bracket ends an element here, so a scalar never completes
        # one. The refusal is right and its wording is the looser of the two available: the
        # document was truncated, and it also holds nothing this package would have
        # accepted whole. Both readings end the attempt, and no row is at stake either way.
        with pytest.raises(ParseError, match="does not look truncated"):
            salvage_records('["Orly", "Ni')
