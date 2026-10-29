"""Reading step 1's response, including every way it can fail to be readable."""

from __future__ import annotations

import json

import pytest

from blue_data_llm_source.planning.analysis import Analysis, analysis_from_response
from blue_data_llm_source.planning.cardinality import Cardinality

ASKED = "list the member states of the european union"


def response(**payload: object) -> str:
    return json.dumps(payload)


class TestAnUnusableRewriteFallsBackAndSaysSo:
    """The contract: not a raise, and not a silent passthrough."""

    @pytest.mark.parametrize(
        "text",
        [
            None,
            "",
            "   ",
            "I'm sorry, I can't help with that.",
            "[]",
            "[1, 2, 3]",
            '"a bare string"',
            "42",
            "{not json at all",
            "null",
        ],
        ids=[
            "absent",
            "empty",
            "whitespace",
            "prose-refusal",
            "array-not-object",
            "array-of-numbers",
            "bare-string",
            "bare-number",
            "malformed",
            "json-null",
        ],
    )
    def test_unreadable_responses_keep_the_callers_ask(self, text: str | None) -> None:
        got = analysis_from_response(text, ASKED)
        assert got.ask == ASKED
        assert got.rewritten is False
        assert got.estimate == Cardinality.UNKNOWN

    @pytest.mark.parametrize("bad_ask", [None, "", "   ", 42, [], {}, True])
    def test_an_object_without_a_usable_ask_falls_back(self, bad_ask: object) -> None:
        got = analysis_from_response(response(ask=bad_ask, row_estimate=200), ASKED)
        assert got.ask == ASKED
        assert got.rewritten is False

    def test_the_estimate_still_survives_a_failed_rewrite(self) -> None:
        """The two fields fail independently; one bad field must not discard the other."""
        got = analysis_from_response(response(ask="", row_estimate=200), ASKED)
        assert got.rewritten is False
        assert got.estimate == Cardinality.HUNDREDS

    def test_rewritten_is_never_true_with_the_callers_own_text(self) -> None:
        """The flag is the only way a caller can tell the two apart."""
        for text in (None, "", "not json", "{}"):
            got = analysis_from_response(text, ASKED)
            assert not (got.rewritten and got.ask == ASKED)


class TestAUsableRewrite:
    def test_the_refined_ask_is_taken_and_marked(self) -> None:
        got = analysis_from_response(
            response(ask="list the 27 EU member states", row_estimate=27), ASKED
        )
        assert got.ask == "list the 27 EU member states"
        assert got.rewritten is True
        assert got.estimate == Cardinality.TENS

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        got = analysis_from_response(response(ask="  refined  "), ASKED)
        assert got.ask == "refined"
        assert got.rewritten is True

    def test_a_fenced_response_is_read(self) -> None:
        payload = response(ask="refined", row_estimate=27)
        got = analysis_from_response(f"```json\n{payload}\n```", ASKED)
        assert got.rewritten is True
        assert got.ask == "refined"


class TestConstraints:
    def test_string_and_list_values_are_both_kept(self) -> None:
        got = analysis_from_response(
            response(ask="x", constraints={"year": "2023", "country": ["FR", "DE"]}),
            ASKED,
        )
        assert got.constraints == {"year": "2023", "country": ["FR", "DE"]}

    @pytest.mark.parametrize("value", [None, {"nested": "object"}, [{"a": 1}]])
    def test_a_value_that_cannot_be_represented_is_dropped_not_coerced(self, value: object) -> None:
        """str() on a dict makes a meaningless filter that looks like a real one."""
        got = analysis_from_response(
            response(ask="x", constraints={"bad": value, "good": "kept"}), ASKED
        )
        assert "bad" not in got.constraints
        assert got.constraints == {"good": "kept"}

    def test_finite_json_scalars_keep_their_types(self) -> None:
        got = analysis_from_response(
            response(
                ask="x",
                constraints={"year": 2024, "active": True, "country": ["FR", 7, "DE"]},
            ),
            ASKED,
        )
        assert got.constraints == {
            "year": 2024,
            "active": True,
            "country": ["FR", 7, "DE"],
        }

    def test_an_empty_list_reaches_the_planner_rather_than_being_dropped_here(self) -> None:
        # An empty constraint is unsatisfiable. Preserve it so planning can report the
        # empty constraint and avoid running the wider request; see test_dropped_constraint.py.
        got = analysis_from_response(response(ask="x", constraints={"c": []}), ASKED)
        assert got.constraints == {"c": []}

    @pytest.mark.parametrize("constraints", [None, [], "none", 42, True])
    def test_a_non_object_constraints_field_yields_none(self, constraints: object) -> None:
        got = analysis_from_response(response(ask="x", constraints=constraints), ASKED)
        assert got.constraints == {}

    def test_a_blank_constraint_name_is_dropped(self) -> None:
        got = analysis_from_response(
            response(ask="x", constraints={"": "v", "  ": "v", "ok": "v"}), ASKED
        )
        assert got.constraints == {"ok": "v"}


class TestDefaults:
    def test_the_default_analysis_claims_nothing(self) -> None:
        """A default-constructed value must not read as a successful rewrite."""
        blank = Analysis(ask=ASKED)
        assert blank.rewritten is False
        assert blank.estimate == Cardinality.UNKNOWN
        assert blank.constraints == {}

    def test_constraints_are_not_shared_between_instances(self) -> None:
        a, b = Analysis(ask="one"), Analysis(ask="two")
        a.constraints["year"] = "2023"
        assert b.constraints == {}
