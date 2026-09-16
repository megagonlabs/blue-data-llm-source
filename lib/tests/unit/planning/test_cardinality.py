"""The bucketed estimate, and the one property the whole module exists for.

The regression that matters here is not any single boundary. It is that a *failed*
estimate never lands in a bucket that reads as small. Returning ``0`` for a digit-free
response would put it below every threshold, selecting the cheapest plan for the queries
least able to afford it.
"""

from __future__ import annotations

import pytest

from blue_data_llm_source import prompts
from blue_data_llm_source.planning.cardinality import (
    PARSE_INVALID,
    PARSE_PARSED,
    PARSE_UNKNOWN,
    Cardinality,
    bucket_of,
    cardinality_from_response,
    for_sizing,
    from_reported,
)

SMALL = (Cardinality.TENS,)
"""Buckets that would route a request to the cheapest plan."""


class TestBoundaries:
    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (0, Cardinality.TENS),
            (1, Cardinality.TENS),
            (50, Cardinality.TENS),
            (51, Cardinality.HUNDREDS),
            (500, Cardinality.HUNDREDS),
            (501, Cardinality.THOUSANDS),
            (5_000, Cardinality.THOUSANDS),
            (5_001, Cardinality.UNBOUNDED),
            (10**9, Cardinality.UNBOUNDED),
        ],
    )
    def test_each_boundary(self, count: int, expected: str) -> None:
        assert bucket_of(count) == expected

    def test_a_negative_count_is_not_a_count(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            bucket_of(-1)

    def test_every_bucket_is_reachable_from_some_count(self) -> None:
        """A member no count produces would be vocabulary nothing can populate."""
        produced = {bucket_of(n) for n in (0, 100, 1_000, 100_000)}
        assert produced == set(Cardinality.ALL) - {Cardinality.UNKNOWN}


class TestFailedEstimatesNeverReadAsSmall:
    """The regression. Each of these is a way the estimation call can fail to answer."""

    @pytest.mark.parametrize(
        "reported",
        [
            None,
            "",
            "   ",
            "I cannot estimate this reliably",
            "it depends on how you count",
            "unknown",
            "many",
            [],
            {},
            object(),
        ],
        ids=[
            "absent",
            "empty",
            "whitespace",
            "declined-prose",
            "hedged-prose",
            "the-word-unknown",
            "vague-quantity",
            "wrong-type-list",
            "wrong-type-dict",
            "wrong-type-object",
        ],
    )
    def test_unreadable_answers_are_unknown_not_small(self, reported: object) -> None:
        got = from_reported(reported)
        assert got == Cardinality.UNKNOWN
        assert got not in SMALL

    def test_a_number_inside_prose_is_not_an_estimate(self) -> None:
        """Scanning for the first run of digits reads this as two rows, and plans for two."""
        assert from_reported("between 2 and 300 depending on the year") == Cardinality.UNKNOWN

    def test_true_is_not_an_estimate_of_one(self) -> None:
        """``isinstance(True, int)`` holds, so a bool must be refused before the int branch.

        A provider answering "yes I can estimate that" with ``true`` would otherwise be
        read as one row — wrong, and wrong in the cheap direction.
        """
        assert from_reported(True) == Cardinality.UNKNOWN
        assert from_reported(False) == Cardinality.UNKNOWN

    @pytest.mark.parametrize("reported", [float("nan"), float("inf"), float("-inf"), -5, -1.0])
    def test_values_that_are_not_counts_are_unknown(self, reported: object) -> None:
        assert from_reported(reported) == Cardinality.UNKNOWN


class TestReadableAnswers:
    @pytest.mark.parametrize(
        ("reported", "expected"),
        [
            (12, Cardinality.TENS),
            (250, Cardinality.HUNDREDS),
            (2_500, Cardinality.THOUSANDS),
            (50_000, Cardinality.UNBOUNDED),
            ("12", Cardinality.TENS),
            ("  250  ", Cardinality.HUNDREDS),
            ("1,200", Cardinality.THOUSANDS),
            (12.0, Cardinality.TENS),
            (250.7, Cardinality.HUNDREDS),
        ],
    )
    def test_a_figure_is_read(self, reported: object, expected: str) -> None:
        assert from_reported(reported) == expected


class TestSizing:
    def test_unknown_is_sized_conservatively(self) -> None:
        """Not as TENS. Under-collection is the failure this package exists to fix."""
        assert for_sizing(Cardinality.UNKNOWN) == Cardinality.HUNDREDS

    @pytest.mark.parametrize(
        "estimate",
        [Cardinality.TENS, Cardinality.HUNDREDS, Cardinality.THOUSANDS, Cardinality.UNBOUNDED],
    )
    def test_a_real_estimate_is_used_as_given(self, estimate: str) -> None:
        assert for_sizing(estimate) == estimate

    def test_an_unrecognised_bucket_is_refused(self) -> None:
        """Silently sizing an unknown string would be the failure class this package removes."""
        with pytest.raises(ValueError, match="not a cardinality"):
            for_sizing("enormous")

    def test_sizing_never_returns_unknown(self) -> None:
        """Downstream sizes depth and quota from this, so it must be a real bucket."""
        for estimate in Cardinality.ALL:
            assert for_sizing(estimate) != Cardinality.UNKNOWN


def test_cardinality_prompt_requests_a_non_null_point_estimate_with_uncertainty_bounds() -> None:
    prompt = prompts.build_cardinality_prompt(
        original_query="countries",
        retrieval_query="countries",
        caller_context="",
        constraints={},
        schema={"attrs": [{"name": "country"}], "primary_keys": ["country"]},
    )

    assert (
        '"expected": a best-effort non-null non-negative integer point estimate for the accepted '
        "row unit" in prompt
    )
    assert '"lower": a rough plausible non-negative integer lower bound, or null' in prompt
    assert '"upper": a rough plausible non-negative integer upper bound, or null' in prompt
    assert "widening the lower and upper bounds rather than suppressing the point" in prompt
    assert "does not assert completeness or correctness" in prompt
    assert "does not set a hard stopping condition" in prompt
    assert "Return only the JSON object. Do not return prose or\ncode fences" in prompt
    assert "Use null for all three counts" not in prompt


def test_cardinality_prompt_ignores_numbers_that_do_not_define_row_count() -> None:
    prompt = prompts.build_cardinality_prompt(
        original_query="In 2024, list products above 70% with ID 12 and price 99",
        retrieval_query="products above the threshold",
        caller_context="",
        constraints={"year": 2024, "score": 70, "id": 12, "price": 99},
        schema=None,
    )

    assert "ignore values that do not define how many rows to return" in prompt
    for category in ("years", "thresholds", "percentages", "IDs", "attribute values"):
        assert category in prompt


def test_cardinality_prompt_uses_numbers_that_bound_the_row_count() -> None:
    prompt = prompts.build_cardinality_prompt(
        original_query="Return at most 10 rows, one for each year from 2020 through 2024",
        retrieval_query="one row for each requested year",
        caller_context="",
        constraints={"regions": ["north", "south"]},
        schema=None,
    )

    assert "explicit result limits" in prompt
    assert "row-defining ranges or bounded sets" in prompt
    assert "when they\nconstrain the row count" in prompt


@pytest.mark.parametrize(
    ("schema", "rendered_schema"),
    [
        (None, "null"),
        (
            {"attrs": [{"name": "country"}], "primary_keys": ["country"]},
            '{"attrs": [{"name": "country"}], "primary_keys": ["country"]}',
        ),
    ],
    ids=["no-schema", "declared-schema"],
)
def test_cardinality_prompt_describes_its_schema_input_without_assuming_schedule_order(
    schema: object, rendered_schema: str
) -> None:
    prompt = prompts.build_cardinality_prompt(
        original_query="countries",
        retrieval_query="countries",
        caller_context="",
        constraints={},
        schema=schema,
    )

    assert prompt.lower().count("schema available at this stage") == 2
    assert "resolved schema" not in prompt.lower()
    assert f"Schema available at this stage: {rendered_schema}" in prompt


def test_all_null_cardinality_response_remains_unknown() -> None:
    estimate = cardinality_from_response(
        '{"expected": null, "lower": null, "upper": null, "row_unit": "countries"}',
        row_unit="records",
    )

    assert estimate.parse_status == PARSE_UNKNOWN
    assert estimate.expected is None
    assert estimate.lower is None
    assert estimate.upper is None
    assert estimate.row_unit == "countries"
    assert estimate.diagnostics == ()


def test_malformed_cardinality_response_remains_invalid() -> None:
    estimate = cardinality_from_response("not JSON", row_unit="countries")

    assert estimate.parse_status == PARSE_INVALID
    assert estimate.expected is None
    assert estimate.diagnostics == ("cardinality response was not a JSON object",)


def test_zero_cardinality_response_remains_a_valid_estimate() -> None:
    estimate = cardinality_from_response(
        '{"expected": 0, "lower": 0, "upper": 0, "row_unit": "countries"}',
        row_unit="records",
    )

    assert estimate.parse_status == PARSE_PARSED
    assert estimate.expected == 0
    assert estimate.lower == 0
    assert estimate.upper == 0


@pytest.mark.parametrize(
    ("response", "diagnostic"),
    [
        (
            '{"expected": null, "lower": 9, "upper": 4, "row_unit": "countries"}',
            "cardinality lower must not exceed upper",
        ),
        (
            '{"expected": 4, "lower": 5, "upper": 9, "row_unit": "countries"}',
            "cardinality expected must not be below lower",
        ),
        (
            '{"expected": 10, "lower": 5, "upper": 9, "row_unit": "countries"}',
            "cardinality expected must not exceed upper",
        ),
    ],
)
def test_cardinality_response_still_rejects_unordered_bounds(
    response: str, diagnostic: str
) -> None:
    estimate = cardinality_from_response(response, row_unit="records")

    assert estimate.parse_status == PARSE_INVALID
    assert estimate.expected is None
    assert estimate.lower is None
    assert estimate.upper is None
    assert estimate.diagnostics == (diagnostic,)
