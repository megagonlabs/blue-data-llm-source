"""Merging leaf results into one record set, and accounting for what the run missed."""

from __future__ import annotations

from typing import Any

import pytest

from blue_data_llm_source.execution.mapper import run_leaf
from blue_data_llm_source.planning.bounds import MODE_FLAT, MODE_PARTITIONED, PlanLimits
from blue_data_llm_source.planning.cardinality import Cardinality
from blue_data_llm_source.planning.plan import ROOT, FetchPlan, Gap, Leaf, Node, Note, build_plan
from blue_data_llm_source.planning.schema import (
    KEY_FROM_CALLER,
    KEY_FROM_NONE,
    Schema,
)
from blue_data_llm_source.reduction.coverage import (
    FAILED,
    Coverage,
    LeafInput,
    LeafOutcome,
    plan_ids,
    reduce_leaves,
)
from blue_data_llm_source.types import Attr, Outcome
from tests.doubles import TruncatingModel, cut_inside_record


def _plan(
    leaf_ids: list[str],
    *,
    mode: str = MODE_PARTITIONED,
    notes: list[Note] | None = None,
    nodes: list[Node] | None = None,
    refused: bool = False,
) -> FetchPlan:
    return FetchPlan(
        mode=mode,
        leaves=[Leaf(id=i, parent_id=ROOT, query="q", constraints={}) for i in leaf_ids],
        nodes=list(nodes or []),
        notes=list(notes or []),
        refused=refused,
    )


def _no_model(prompt: str) -> str:
    """A leaf refused before any call never reaches this."""
    raise AssertionError(f"a provider was contacted: {prompt!r}")


def _keyed() -> Schema:
    return Schema(attrs=[Attr("id"), Attr("name")], key="id", key_from=KEY_FROM_CALLER)


def _unkeyed() -> Schema:
    return Schema(attrs=[Attr("id"), Attr("name")])


def _rows(*records: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(record) for record in records]


def _codes(coverage: Coverage) -> list[str]:
    return [note.code for note in coverage.gaps]


def _targets(coverage: Coverage, code: str) -> list[str | None]:
    return [note.target_id for note in coverage.gaps if note.code == code]


# --- the account exists at all -------------------------------------------------------


def test_a_single_leaf_run_still_gets_an_account() -> None:
    plan = _plan(["0.0"], mode=MODE_FLAT)
    records, coverage = reduce_leaves(
        plan, _keyed(), [("0.0", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.0", rows=1))]
    )
    assert records == [{"id": 1, "name": "Ada"}]
    assert coverage.outcomes == [LeafOutcome("0.0", rows=1)]
    assert coverage.record_origins == [["0.0"]]
    assert coverage.plan is plan


def test_a_run_with_no_results_at_all_still_gets_an_account() -> None:
    plan = _plan(["0.0", "0.1"])
    records, coverage = reduce_leaves(plan, _keyed(), [])
    assert records == []
    assert coverage.record_origins == []
    assert _codes(coverage) == [Gap.LEAF_NOT_RUN, Gap.LEAF_NOT_RUN]


def test_a_single_leaf_stopped_at_its_cap_is_not_reported_as_ok() -> None:
    plan = _plan(["0.0"], mode=MODE_FLAT)
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1, at_cap=True))],
    )
    assert coverage.run_outcome == Outcome.STOPPED_BY_LIMIT
    assert Gap.LEAF_AT_CAP in _codes(coverage)


# --- record_origins ------------------------------------------------------------------


def test_record_origins_are_positionally_parallel_to_the_returned_records() -> None:
    plan = _plan(["0.0", "0.1"])
    records, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            (
                "0.0",
                _rows({"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}),
                LeafOutcome("0.0"),
            ),
            ("0.1", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.1")),
        ],
    )
    assert len(records) == 2
    assert len(coverage.record_origins) == len(records)


def test_record_origins_name_every_leaf_that_produced_a_record() -> None:
    plan = _plan(["0.0", "0.1"])
    records, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            (
                "0.0",
                _rows({"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}),
                LeafOutcome("0.0"),
            ),
            ("0.1", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.1")),
        ],
    )
    assert records == [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}]
    assert coverage.record_origins == [["0.0", "0.1"], ["0.0"]]


def test_a_leaf_returning_the_same_record_twice_is_named_once() -> None:
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [("0.0", _rows({"id": 1, "name": "Ada"}, {"id": 1, "name": "Ada"}), LeafOutcome("0.0"))],
    )
    assert coverage.record_origins == [["0.0"]]


# --- deduplication only on an identified key -----------------------------------------


def test_records_are_not_deduplicated_when_no_key_was_identified() -> None:
    plan = _plan(["0.0", "0.1"])
    records, coverage = reduce_leaves(
        plan,
        _unkeyed(),
        [
            ("0.0", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.0")),
            ("0.1", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.1")),
        ],
    )
    assert records == [{"id": 1, "name": "Ada"}, {"id": 1, "name": "Ada"}]
    assert coverage.record_origins == [["0.0"], ["0.1"]]
    assert Gap.DEDUP_KEY_ABSENT in _codes(coverage)


def test_a_key_with_no_identification_source_does_not_enable_deduplication() -> None:
    schema = Schema(attrs=[Attr("id")], key="id", key_from=KEY_FROM_NONE)
    plan = _plan(["0.0", "0.1"])
    records, coverage = reduce_leaves(
        plan,
        schema,
        [
            ("0.0", _rows({"id": 1}), LeafOutcome("0.0")),
            ("0.1", _rows({"id": 1}), LeafOutcome("0.1")),
        ],
    )
    assert len(records) == 2
    assert Gap.DEDUP_KEY_ABSENT in _codes(coverage)


def test_records_carrying_no_value_for_the_key_are_not_merged_together() -> None:
    plan = _plan(["0.0"])
    records, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            (
                "0.0",
                _rows({"id": None, "name": "Ada"}, {"id": None, "name": "Grace"}),
                LeafOutcome("0.0", rows=2),
            )
        ],
    )
    assert records == [{"id": None, "name": "Ada"}, {"id": None, "name": "Grace"}]
    assert Gap.DEDUP_KEY_ABSENT in _codes(coverage)


def test_an_unhashable_key_value_still_identifies_the_record() -> None:
    """A list is a value, not a missing one. Refusing to merge on it filed a record that
    carries an identity beside the records that carry none — and execution, which never
    had that rule, had already read the two as one row."""
    plan = _plan(["0.0"])
    records, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            (
                "0.0",
                _rows({"id": [1], "name": "Ada"}, {"id": [1], "name": "Grace"}),
                LeafOutcome("0.0", rows=2),
            )
        ],
    )
    assert len(records) == 1
    assert Gap.DEDUP_KEY_ABSENT not in _codes(coverage)


def test_two_different_unhashable_key_values_are_kept_apart() -> None:
    plan = _plan(["0.0"])
    records, _ = reduce_leaves(
        plan,
        _keyed(),
        [
            (
                "0.0",
                _rows({"id": [1], "name": "Ada"}, {"id": [2], "name": "Grace"}),
                LeafOutcome("0.0", rows=2),
            )
        ],
    )
    assert len(records) == 2


def test_a_conflict_note_names_the_key_value_the_record_carried() -> None:
    """Not the form it was filed under. A note reading ``id=('number', 1)`` sends a reader
    looking for a row that is not in the result."""
    plan = _plan(["0.0", "0.1"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.0")),
            ("0.1", _rows({"id": 1, "name": "Grace"}), LeafOutcome("0.1")),
        ],
    )
    note = next(n for n in coverage.gaps if n.code == Gap.DEDUP_CONFLICT)
    assert "id=1" in note.detail


# --- collisions and conflicts --------------------------------------------------------


def test_a_conflict_keeps_the_record_from_the_lowest_leaf() -> None:
    plan = _plan(["0.0", "0.1"])
    records, _ = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.0")),
            ("0.1", _rows({"id": 1, "name": "Ada Lovelace"}), LeafOutcome("0.1")),
        ],
    )
    assert records == [{"id": 1, "name": "Ada"}]


def test_a_conflict_note_names_the_field_and_both_disagreeing_leaves() -> None:
    plan = _plan(["0.0", "0.1"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.0")),
            ("0.1", _rows({"id": 1, "name": "Ada Lovelace"}), LeafOutcome("0.1")),
        ],
    )
    note = next(n for n in coverage.gaps if n.code == Gap.DEDUP_CONFLICT)
    assert "'name'" in note.detail
    assert "0.0" in note.detail
    assert "0.1" in note.detail


def test_the_losing_leaf_of_a_conflict_stays_in_record_origins() -> None:
    plan = _plan(["0.0", "0.1"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.0")),
            ("0.1", _rows({"id": 1, "name": "Ada Lovelace"}), LeafOutcome("0.1")),
        ],
    )
    assert coverage.record_origins == [["0.0", "0.1"]]


def test_the_winner_of_a_collision_follows_plan_order_not_text_order() -> None:
    plan = _plan([f"0.{i}" for i in range(11)])
    records, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.10", _rows({"id": 1, "name": "from ten"}), LeafOutcome("0.10")),
            ("0.2", _rows({"id": 1, "name": "from two"}), LeafOutcome("0.2")),
        ],
    )
    assert records == [{"id": 1, "name": "from two"}]
    assert coverage.record_origins == [["0.2", "0.10"]]


def test_the_first_non_null_value_fills_an_earlier_blank() -> None:
    plan = _plan(["0.0", "0.1"])
    records, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", _rows({"id": 1, "name": None}), LeafOutcome("0.0")),
            ("0.1", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.1")),
        ],
    )
    assert records == [{"id": 1, "name": "Ada"}]
    assert coverage.record_origins == [["0.0", "0.1"]]


def test_a_field_only_one_leaf_answered_is_not_reported_as_a_disagreement() -> None:
    plan = _plan(["0.0", "0.1"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", _rows({"id": 1, "name": None}), LeafOutcome("0.0")),
            ("0.1", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.1")),
        ],
    )
    assert Gap.DEDUP_CONFLICT not in _codes(coverage)


def test_agreeing_copies_of_a_record_produce_no_conflict_note() -> None:
    plan = _plan(["0.0", "0.1"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.0")),
            ("0.1", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.1")),
        ],
    )
    assert Gap.DEDUP_CONFLICT not in _codes(coverage)


# --- gaps ----------------------------------------------------------------------------


def test_a_leaf_that_ran_and_returned_nothing_is_reported_empty() -> None:
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(plan, _keyed(), [("0.0", [], LeafOutcome("0.0"))])
    assert _codes(coverage) == [Gap.LEAF_EMPTY]


def test_a_leaf_reporting_rows_it_did_not_deliver_says_so() -> None:
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(plan, _keyed(), [("0.0", [], LeafOutcome("0.0", rows=3))])
    note = next(n for n in coverage.gaps if n.code == Gap.LEAF_EMPTY)
    assert "3" in note.detail


def test_a_leaf_at_its_cap_is_reported_even_though_it_returned_rows() -> None:
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(
        plan, _keyed(), [("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1, at_cap=True))]
    )
    assert _codes(coverage) == [Gap.LEAF_AT_CAP]


def test_a_truncated_leaf_is_reported_at_its_cap() -> None:
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [("0.0", _rows({"id": 1}), LeafOutcome("0.0", outcome=Outcome.TRUNCATED, rows=1))],
    )
    assert Gap.LEAF_AT_CAP in _codes(coverage)


def test_a_capped_leaf_is_told_which_ceiling_fired() -> None:
    """Naming the row quota for a continuation cap points a caller at the wrong knob: it
    raises its row target, asks again, and gets the same records back."""
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(
        plan, _keyed(), [("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1, at_cap=True))]
    )
    note = next(n for n in coverage.gaps if n.code == Gap.LEAF_AT_CAP)
    assert "continuation cap" in note.detail
    assert "quota" not in note.detail


def test_a_row_cap_trim_is_not_reported_as_a_continuation_cap() -> None:
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [("0.0", _rows({"id": 1}), LeafOutcome("0.0", outcome=Outcome.TRUNCATED, rows=1))],
    )
    note = next(n for n in coverage.gaps if n.code == Gap.LEAF_AT_CAP)
    assert "row cap" in note.detail
    assert "continuation" not in note.detail


def test_a_leaf_refused_for_a_blank_query_gets_its_own_account() -> None:
    """Neither neighbour is true of it. ``LEAF_EMPTY`` credits a model with an answer it
    was never asked for, and ``LEAF_NOT_RUN`` throws away the reason."""
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(
        plan, _keyed(), [("0.0", [], LeafOutcome("0.0", outcome=Outcome.EMPTY_QUERY))]
    )
    assert _codes(coverage) == [Gap.LEAF_EMPTY_QUERY]
    assert "no query" in coverage.gaps[0].detail


def test_a_blank_query_reads_differently_from_a_model_with_nothing() -> None:
    plan = _plan(["0.0", "0.1"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", [], LeafOutcome("0.0", outcome=Outcome.EMPTY_QUERY)),
            ("0.1", [], LeafOutcome("0.1")),
        ],
    )
    assert _codes(coverage) == [Gap.LEAF_EMPTY_QUERY, Gap.LEAF_EMPTY]


def test_what_execution_reports_for_a_blank_query_reaches_the_account_intact() -> None:
    """``ran`` says the leaf was processed, and ``calls`` says whether a provider was
    contacted. An adapter left to infer the first from the second files this leaf as one
    execution never reached, and the account then says nothing about why it is missing.
    """
    run = run_leaf(
        Leaf(id="0.0", parent_id=ROOT, query="   ", constraints={}),
        _no_model,
        attrs=[Attr("id")],
        limits=PlanLimits(),
    )
    reported = LeafOutcome(
        leaf_id=run.leaf_id,
        outcome=run.outcome,
        rows=len(run.records),
        calls=run.attempts,
        at_cap=run.at_cap,
    )
    assert reported.ran is True
    assert reported.calls == 0
    _, coverage = reduce_leaves(_plan(["0.0"]), _keyed(), [("0.0", [], reported)])
    assert _codes(coverage) == [Gap.LEAF_EMPTY_QUERY]


def test_a_leaf_that_raised_is_reported_failed_and_not_also_empty() -> None:
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(plan, _keyed(), [("0.0", [], LeafOutcome("0.0", outcome=FAILED))])
    assert _codes(coverage) == [Gap.LEAF_FAILED]


def test_a_leaf_a_ceiling_stopped_is_reported_stopped_and_not_also_empty() -> None:
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(
        plan, _keyed(), [("0.0", [], LeafOutcome("0.0", outcome=Outcome.STOPPED_BY_LIMIT))]
    )
    assert _codes(coverage) == [Gap.LEAF_STOPPED_BY_LIMIT]


def test_a_leaf_reported_as_not_run_is_reported_once_and_only_as_that() -> None:
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(plan, _keyed(), [("0.0", [], LeafOutcome("0.0", ran=False))])
    assert _codes(coverage) == [Gap.LEAF_NOT_RUN]


def test_a_plan_leaf_execution_never_mentioned_is_reported_as_not_run() -> None:
    plan = _plan(["0.0", "0.1"])
    _, coverage = reduce_leaves(
        plan, _keyed(), [("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1))]
    )
    assert _codes(coverage) == [Gap.LEAF_NOT_RUN]
    assert _targets(coverage, Gap.LEAF_NOT_RUN) == ["0.1"]


def test_leaf_gaps_follow_plan_order() -> None:
    plan = _plan(["0.0", "0.1", "0.2"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.2", [], LeafOutcome("0.2", outcome=FAILED)),
            ("0.0", [], LeafOutcome("0.0")),
        ],
    )
    assert _codes(coverage) == [Gap.LEAF_EMPTY, Gap.LEAF_NOT_RUN, Gap.LEAF_FAILED]


def test_planning_notes_are_carried_through_rather_than_discarded() -> None:
    planned = Note(code=Gap.GUARD_COLLAPSED, target_id=ROOT, detail="no key was proposed")
    plan = _plan(["0.0"], mode=MODE_FLAT, notes=[planned])
    _, coverage = reduce_leaves(
        plan, _keyed(), [("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1))]
    )
    assert coverage.gaps[0] == planned


def test_records_from_a_leaf_the_plan_does_not_contain_are_still_returned() -> None:
    plan = _plan(["0.0"])
    records, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.0", rows=1)),
            ("9.9", _rows({"id": 2, "name": "Grace"}), LeafOutcome("9.9", rows=1)),
        ],
    )
    assert records == [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}]
    assert coverage.record_origins == [["0.0"], ["9.9"]]


# --- the aggregate outcome -----------------------------------------------------------


def test_a_clean_run_reports_ok() -> None:
    plan = _plan(["0.0"])
    _, coverage = reduce_leaves(
        plan, _keyed(), [("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1))]
    )
    assert coverage.run_outcome == Outcome.OK


def test_a_leaf_a_ceiling_stopped_makes_the_whole_run_stopped_by_limit() -> None:
    plan = _plan(["0.0", "0.1"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1)),
            ("0.1", [], LeafOutcome("0.1", outcome=Outcome.STOPPED_BY_LIMIT)),
        ],
    )
    assert coverage.run_outcome == Outcome.STOPPED_BY_LIMIT


def test_a_plan_refused_by_its_caps_reports_stopped_by_limit() -> None:
    plan = _plan(["0.0"], refused=True)
    _, coverage = reduce_leaves(
        plan, _keyed(), [("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1))]
    )
    assert coverage.run_outcome == Outcome.STOPPED_BY_LIMIT


def test_a_run_reports_failed_only_when_every_leaf_failed() -> None:
    plan = _plan(["0.0", "0.1"])
    _, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", [], LeafOutcome("0.0", outcome=FAILED)),
            ("0.1", [], LeafOutcome("0.1", outcome=FAILED)),
        ],
    )
    assert coverage.run_outcome == FAILED


def test_a_partial_answer_says_so_and_carries_the_failure_as_a_gap() -> None:
    # `outcome` exists so a caller does not have to read `gaps` to find out whether it
    # holds an answer. Reporting `ok` here put that decision back in the gap list.
    plan = _plan(["0.0", "0.1"])
    records, coverage = reduce_leaves(
        plan,
        _keyed(),
        [
            ("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1)),
            ("0.1", [], LeafOutcome("0.1", outcome=FAILED)),
        ],
    )
    assert records == [{"id": 1}]
    assert coverage.run_outcome == Outcome.PARTIAL
    assert Gap.LEAF_FAILED in _codes(coverage)


def test_a_failed_leaf_beside_an_unreported_one_is_not_a_whole_run_failure() -> None:
    # Not FAILED, because that is held back for the case where every part raised and the
    # caller is owed the exception. Not `ok` either: one part definitely did not answer.
    plan = _plan(["0.0", "0.1"])
    _, coverage = reduce_leaves(plan, _keyed(), [("0.0", [], LeafOutcome("0.0", outcome=FAILED))])
    assert coverage.run_outcome == Outcome.PARTIAL
    assert set(_codes(coverage)) == {Gap.LEAF_FAILED, Gap.LEAF_NOT_RUN}


def test_the_aggregate_outcome_is_derived_from_the_leaf_outcomes() -> None:
    coverage = Coverage(plan=_plan(["0.0"]))
    assert coverage.run_outcome == Outcome.OK
    coverage.outcomes.append(LeafOutcome("0.0", at_cap=True))
    assert coverage.run_outcome == Outcome.STOPPED_BY_LIMIT


def test_a_lost_part_outranks_a_cap_that_also_fired() -> None:
    # The precedence, stated where the rule lives. Both facts are true of this run and only
    # one of them has another field to live in: the cap is in `stopped_by`, and a part that
    # never came back is nowhere else at all. So `outcome` goes to the one that would
    # otherwise be erased, and neither fact is lost.
    coverage = Coverage(
        plan=_plan(["0.0", "0.1"]),
        outcomes=[LeafOutcome("0.0", at_cap=True), LeafOutcome("0.1", outcome=FAILED)],
    )
    assert coverage.run_outcome == Outcome.PARTIAL


def test_a_capped_run_that_lost_nothing_still_reports_the_cap() -> None:
    coverage = Coverage(
        plan=_plan(["0.0", "0.1"]),
        outcomes=[LeafOutcome("0.0", at_cap=True), LeafOutcome("0.1")],
    )
    assert coverage.run_outcome == Outcome.STOPPED_BY_LIMIT


# --- every note binds to something the plan contains ----------------------------------


def _built_flat_plan() -> FetchPlan:
    """A flat plan with a planning note bound to the root it has no Node for."""
    return build_plan(
        mode=MODE_FLAT,
        ask="who",
        constraints={},
        schema=_keyed(),
        estimate=Cardinality.UNKNOWN,
        limits=PlanLimits(),
    )


def test_a_flat_plan_binds_its_planning_notes_to_something_that_exists() -> None:
    """A flat plan has no partition node, so a note may not point at the root.

    Pointing at an id with no object behind it makes the binding decorative: a caller
    resolving it gets nothing. Planning retargets those notes onto the leaf that runs.
    """
    plan = _built_flat_plan()
    assert plan.nodes == []
    assert plan.notes, "this fixture exists to carry a planning note"
    leaf_ids = {leaf.id for leaf in plan.leaves}
    assert all(n.target_id in leaf_ids for n in plan.notes)
    assert all(n.target_id in plan_ids(plan) for n in plan.notes)


_SHAPES: list[tuple[str, FetchPlan, Schema, list[LeafInput]]] = [
    (
        "flat plan carrying a planning note",
        _built_flat_plan(),
        _keyed(),
        [("0.0", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.0", rows=1))],
    ),
    (
        "every leaf shortfall at once",
        _plan(["0.0", "0.1", "0.2", "0.3"]),
        _keyed(),
        [
            ("0.0", [], LeafOutcome("0.0")),
            ("0.1", [], LeafOutcome("0.1", outcome=FAILED)),
            ("0.2", _rows({"id": 1}), LeafOutcome("0.2", rows=1, at_cap=True)),
            ("0.3", [], LeafOutcome("0.3", ran=False)),
        ],
    ),
    (
        "a conflict between two leaves",
        _plan(["0.0", "0.1"]),
        _keyed(),
        [
            ("0.0", _rows({"id": 1, "name": "Ada"}), LeafOutcome("0.0", rows=1)),
            ("0.1", _rows({"id": 1, "name": "Grace"}), LeafOutcome("0.1", rows=1)),
        ],
    ),
    (
        "no identified key",
        _plan(["0.0"]),
        _unkeyed(),
        [("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1))],
    ),
    (
        "a result from a leaf the plan does not contain",
        _plan(["0.0"]),
        _keyed(),
        [
            ("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1)),
            ("9.9", _rows({"id": 2}), LeafOutcome("9.9", outcome=FAILED)),
        ],
    ),
    ("a plan with no leaves", _plan([]), _keyed(), []),
]


@pytest.mark.parametrize(
    ("plan", "schema", "results"),
    [shape[1:] for shape in _SHAPES],
    ids=[shape[0] for shape in _SHAPES],
)
def test_every_note_target_resolves_to_an_id_in_the_plan(
    plan: FetchPlan, schema: Schema, results: list[LeafInput]
) -> None:
    _, coverage = reduce_leaves(plan, schema, results)
    known = plan_ids(plan)
    unresolved = [n for n in coverage.gaps if n.target_id is not None and n.target_id not in known]
    assert unresolved == []


@pytest.mark.parametrize(
    ("plan", "schema", "results"),
    [shape[1:] for shape in _SHAPES],
    ids=[shape[0] for shape in _SHAPES],
)
def test_record_origins_match_the_record_count_in_every_shape(
    plan: FetchPlan, schema: Schema, results: list[LeafInput]
) -> None:
    records, coverage = reduce_leaves(plan, schema, results)
    assert len(coverage.record_origins) == len(records)
    assert all(origin for origin in coverage.record_origins)


class TestATruncatedResponseIsItsOwnGap:
    """Records recovered from a cut-off answer are rows the caller can use, and the rows
    lost below the cut are rows it will never hear about unless the account says so."""

    def test_a_salvaged_leaf_reports_the_truncation(self) -> None:
        plan = _plan(["0.0"])
        results: list[LeafInput] = [
            (
                "0.0",
                _rows({"id": 1}, {"id": 2}),
                LeafOutcome("0.0", rows=2, calls=1, recovered_prefix=True),
            )
        ]

        _records, coverage = reduce_leaves(plan, _keyed(), results)

        assert Gap.LEAF_RESPONSE_TRUNCATED in _codes(coverage)
        assert _targets(coverage, Gap.LEAF_RESPONSE_TRUNCATED) == ["0.0"]

    def _truncation_detail(self, outcome: LeafOutcome) -> str:
        plan = _plan(["0.0"])
        results: list[LeafInput] = [("0.0", _rows({"id": 1}), outcome)]
        _records, coverage = reduce_leaves(plan, _keyed(), results)
        return next(n.detail for n in coverage.gaps if n.code == Gap.LEAF_RESPONSE_TRUNCATED)

    def test_an_unattested_truncation_does_not_name_a_cause(self) -> None:
        # The default path. A response that ended mid-document and one that was merely
        # written badly are the same text, and a caller's plain callable can never say
        # which. Naming the output allowance here would give the case with the least
        # evidence the explanation that asserts the most.
        detail = self._truncation_detail(LeafOutcome("0.0", rows=1, calls=1, recovered_prefix=True))
        assert "output allowance" not in detail
        assert "completeness was not established" in detail

    def test_an_attested_truncation_names_the_output_allowance(self) -> None:
        # And the pair, which is what makes either half informative. A caller told only
        # that the row target was reached raises it, asks again, and gets an answer cut off
        # in exactly the same place; this is the ceiling that is actually holding.
        detail = self._truncation_detail(
            LeafOutcome(
                "0.0",
                rows=1,
                calls=1,
                recovered_prefix=True,
                provider_output_limit=True,
                # The narrow fact is what the sentence may cite: the attestation was carried
                # by the response that was recovered, not by some other one in the same leaf.
                recovery_attested=True,
            )
        )
        assert "output allowance" in detail

    def test_an_attestation_from_another_response_does_not_explain_this_one(self) -> None:
        # The leaf holds an attestation and a recovery, and they belong to different
        # responses. The run-level axis is still true; the cause sentence about this prefix
        # is not, and saying it would be the defect the per-response ledger exists to stop.
        detail = self._truncation_detail(
            LeafOutcome(
                "0.0",
                rows=1,
                calls=1,
                recovered_prefix=True,
                provider_output_limit=True,
                recovery_attested=False,
            )
        )
        assert "output allowance" not in detail
        assert "completeness was not established" in detail

    def test_a_run_holding_a_salvaged_leaf_is_not_reported_as_whole(self) -> None:
        plan = _plan(["0.0"])
        results: list[LeafInput] = [
            ("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1, calls=1, recovered_prefix=True))
        ]

        _records, coverage = reduce_leaves(plan, _keyed(), results)

        assert coverage.run_outcome == Outcome.STOPPED_BY_LIMIT

    def test_truncation_and_a_continuation_cap_are_both_reported(self) -> None:
        """They are different ceilings with different remedies, and one leaf can hit both."""
        plan = _plan(["0.0"])
        results: list[LeafInput] = [
            (
                "0.0",
                _rows({"id": 1}),
                LeafOutcome("0.0", rows=1, calls=3, at_cap=True, recovered_prefix=True),
            )
        ]

        _records, coverage = reduce_leaves(plan, _keyed(), results)
        codes = _codes(coverage)

        assert Gap.LEAF_RESPONSE_TRUNCATED in codes
        assert Gap.LEAF_AT_CAP in codes

    def test_a_leaf_that_was_not_truncated_gets_no_such_gap(self) -> None:
        plan = _plan(["0.0"])
        results: list[LeafInput] = [("0.0", _rows({"id": 1}), LeafOutcome("0.0", rows=1, calls=1))]

        _records, coverage = reduce_leaves(plan, _keyed(), results)

        assert Gap.LEAF_RESPONSE_TRUNCATED not in _codes(coverage)
        assert coverage.run_outcome == Outcome.OK

    def test_execution_and_the_account_agree_end_to_end(self) -> None:
        """A flag set by one module and read
        by the next, with no test crossing between them."""
        plan = _plan(["0.0"], mode=MODE_FLAT)
        records = [{"id": n} for n in range(1, 6)]
        model = TruncatingModel(records, ceiling=cut_inside_record(records, after=2))

        run = run_leaf(
            plan.leaves[0],
            model,
            attrs=[Attr("id")],
            limits=PlanLimits(max_continuations=0),
            key="id",
        )
        outcome = LeafOutcome(
            run.leaf_id,
            outcome=run.outcome,
            rows=len(run.records),
            calls=run.attempts,
            at_cap=run.at_cap,
            recovered_prefix=run.recovered_prefix,
        )

        merged, coverage = reduce_leaves(plan, _keyed(), [(run.leaf_id, run.records, outcome)])

        assert model.truncations == 1
        assert merged == records[:2]
        assert Gap.LEAF_RESPONSE_TRUNCATED in _codes(coverage)
        assert coverage.run_outcome == Outcome.STOPPED_BY_LIMIT
