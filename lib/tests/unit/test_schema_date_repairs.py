"""Date coercion and key identity agree across execution, reduction, and fetch."""

from __future__ import annotations

import json
from typing import Any

import pytest

from blue_data_llm_source import LlmSource
from blue_data_llm_source.execution.mapper import run_leaf
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.plan import ROOT, FetchPlan, Leaf
from blue_data_llm_source.planning.schema import KEY_FROM_CALLER, Schema
from blue_data_llm_source.reduction.coverage import LeafOutcome, reduce_leaves
from blue_data_llm_source.reduction.identity import key_identity, typed_identity
from blue_data_llm_source.reduction.verifier import verify
from blue_data_llm_source.types import Attr


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-07-03", "2026-07-03"),
        ("2026/07/03", "2026-07-03"),
        ("2026-7-3", "2026-07-03"),
        ("July 3, 2026", "2026-07-03"),
        ("19-01-2026", "2026-01-19"),
        ("19-1-2026", "2026-01-19"),
        (" 19-01-2026 ", "2026-01-19"),
        ("2024-02-29", "2024-02-29"),
        ("29-02-2024", "2024-02-29"),
        ("07-03-2026", None),
        ("09-07-2026", None),
        ("10-12-2026", None),
        ("05-11-2024", None),
        ("03-03-2026", "2026-03-03"),
        ("3-3-2026", "2026-03-03"),
        ("05-05-2026", "2026-05-05"),
        ("12-12-2026", "2026-12-12"),
        ("7-3-2026", None),
        ("07-3-2026", None),
        ("7-03-2026", None),
        (" 07-03-2026 ", None),
        ("01-19-2026", None),
        ("2026-02-30", None),
        ("2026-02-29", None),
        ("29-02-2026", None),
        ("31-04-2026", None),
        ("19-13-2026", None),
        ("00-03-2026", None),
        ("19-00-2026", None),
        ("", None),
        (20260703, None),
        (True, None),
        ({"day": 3}, None),
        (None, None),
    ],
)
def test_date_value_and_mismatch_metadata(value: Any, expected: str | None) -> None:
    original = [{"day": None}, {"day": value}, {}]
    records, mismatches = verify(original, [Attr("day", " DATE ")])
    assert original == [{"day": None}, {"day": value}, {}]
    assert records == [{"day": None}, {"day": value if expected is None else expected}, {}]
    if expected is None:
        assert typed_identity(value, "date") is None
        assert mismatches == (
            []
            if value is None
            else [
                {
                    "record_index": 1,
                    "attribute": "day",
                    "declared_type": "date",
                    "value": repr(value),
                }
            ]
        )
    else:
        assert mismatches == []
        assert typed_identity(value, "date") == typed_identity(expected, "date")


@pytest.mark.parametrize("composite", [False, True])
def test_date_keys_in_execution_continuation_reduction_and_fetch(composite: bool) -> None:
    attrs = [Attr("day", "date"), Attr("group", "string"), Attr("detail", "string")]
    keys = ("group", "day") if composite else ("day",)
    values = [
        "07-03-2026",
        "2026-03-07",
        "2026-07-03",
        "07-03-2026",
        "19-01-2026",
        "2026/01/19",
        "2026-02-30",
        "2026-02-30",
    ]
    rows = [{"day": value, "group": "a", "detail": str(i)} for i, value in enumerate(values)]
    survivors = [rows[i] for i in (0, 1, 2, 4, 6)]
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(rows if len(prompts) == 1 else [])

    leaf = Leaf(id="0.0", parent_id=ROOT, query="events", constraints={})
    run = run_leaf(
        leaf,
        answer,
        attrs=attrs,
        limits=PlanLimits(max_continuations=1),
        primary_keys=keys,
    )
    assert run.records == rows
    assert run.observed_key_count == 5
    assert run.attempts == 2
    for row in survivors:
        identity = key_identity(row, keys, attrs)
        assert identity is not None
        assert identity.exclusion() == {key: row[key] for key in keys}
        assert json.dumps(identity.exclusion(), sort_keys=True) in prompts[1]

    other = Leaf(id="0.1", parent_id=ROOT, query="more events", constraints={})
    plan = FetchPlan(mode="partitioned", leaves=[leaf, other])
    schema = Schema(attrs=attrs, primary_keys=keys, key_from=KEY_FROM_CALLER)
    reduced, _ = reduce_leaves(
        plan,
        schema,
        [
            (leaf.id, [rows[i] for i in (0, 1, 2, 4, 6)], LeafOutcome(leaf.id)),
            (other.id, [rows[i] for i in (3, 5, 7)], LeafOutcome(other.id)),
        ],
    )
    assert [row["day"] for row in reduced] == [row["day"] for row in survivors]

    def fetch_answer(_prompt: str) -> str:
        return json.dumps(rows)

    result = LlmSource().fetch(
        "events",
        attrs=[{"name": a.name, "type": a.type} for a in attrs],
        llm_fn=fetch_answer,
        properties={"plan.keys": list(keys)},
    )
    assert [row["day"] for row in result.records] == [
        "07-03-2026",
        "2026-03-07",
        "2026-07-03",
        "2026-01-19",
        "2026-02-30",
    ]
    assert result.meta.primary_keys == list(keys)
    assert result.meta.type_mismatches == [
        {"record_index": index, "attribute": "day", "declared_type": "date", "value": repr(value)}
        for index, value in ((0, "07-03-2026"), (4, "2026-02-30"))
    ]


def test_equal_day_and_month_key_merges_with_its_iso_form() -> None:
    attrs = [Attr("day", "date"), Attr("detail", "string")]
    rows = [
        {"day": "05-05-2026", "detail": "a"},
        {"day": "2026-05-05", "detail": "a"},
        {"day": "07-03-2026", "detail": "b"},
        {"day": "2026-03-07", "detail": "c"},
    ]
    assert key_identity(rows[0], ("day",), attrs) == key_identity(rows[1], ("day",), attrs)
    assert key_identity(rows[2], ("day",), attrs) != key_identity(rows[3], ("day",), attrs)

    def answer(_prompt: str) -> str:
        return json.dumps(rows)

    result = LlmSource().fetch(
        "events",
        attrs=[{"name": a.name, "type": a.type} for a in attrs],
        llm_fn=answer,
        properties={"plan.keys": ["day"]},
    )
    assert [row["day"] for row in result.records] == ["2026-05-05", "07-03-2026", "2026-03-07"]
    assert [m["value"] for m in result.meta.type_mismatches] == [repr("07-03-2026")]
