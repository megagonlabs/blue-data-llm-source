"""What ``meta.outcome`` must say when part of a split request did not come back.

``outcome`` exists so that a caller does not have to parse ``gaps`` to find out whether it
holds an answer. A run that lost a third of its parts and reports the same member as a run
that lost nothing has moved that decision back into the gap list, where the field was
introduced to stop it living.

Nothing here asserts a row count. Rows are the wrong oracle for this: a partly-failed run
and a complete one can return the same number of records, and a partial run that returns
none is still partial. The invariant is over the plan -- how many parts were asked for
against how many reported -- so that is what these compare.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import LlmSourceError, Outcome

ANALYSIS = json.dumps(
    {
        "ask": "commercial airports in France",
        "constraints": {"region": ["North", "South", "Corsica"]},
        "row_estimate": 200,
        "schema": {"attrs": [{"name": "name"}], "key": "name"},
    }
)
PROPOSAL = json.dumps(
    {
        "key": "region",
        "values": ["North", "South", "Corsica"],
        "ambiguity": 0,
        "child_estimates": [10, 10, 10],
    }
)


class Planner:
    """Answers the analysis call with a script and every fetch with one record.

    The two are told apart by the attribute list, which only a fetch prompt carries.
    """

    def __init__(self, analysis: str = ANALYSIS) -> None:
        self.analysis = analysis
        self.fetch_prompts: list[str] = []

    def __call__(self, prompt: str, properties: Any = None) -> str:
        if "Prepare a structured data request" in prompt:
            return self.analysis
        if "Propose one useful partition" in prompt:
            return PROPOSAL
        self.fetch_prompts.append(prompt)
        return self.answer(prompt)

    def answer(self, prompt: str) -> str:
        return json.dumps([{"name": f"r{len(self.fetch_prompts)}"}])


class OneLeafRaises(Planner):
    """Every part answers except the one covering *broken*, which raises every attempt."""

    def __init__(self, broken: str = "North", analysis: str = ANALYSIS) -> None:
        super().__init__(analysis)
        self.broken = broken

    def answer(self, prompt: str) -> str:
        if self.broken in prompt:
            raise RuntimeError("provider refused this part")
        return json.dumps([{"name": f"r{len(self.fetch_prompts)}"}])


class OneLeafRaisesAndTheRestAreEmpty(OneLeafRaises):
    """The zero-record partial: one part raised and the others honestly had nothing."""

    def answer(self, prompt: str) -> str:
        if self.broken in prompt:
            raise RuntimeError("provider refused this part")
        return "[]"


class EveryLeafRaises(Planner):
    def answer(self, prompt: str) -> str:
        raise RuntimeError("provider refused this part")


class OneLeafRaisesAndTheRestKeepProducing(OneLeafRaises):
    """One part lost, and the surviving parts still had rows when their cap ran out.

    Every answer carries a name no part has returned before, so a surviving part never runs
    out of records and can only be ended by its continuation cap.
    """

    def answer(self, prompt: str) -> str:
        if self.broken in prompt:
            raise RuntimeError("provider refused this part")
        return json.dumps([{"name": f"r{len(self.fetch_prompts)}"}])


def partitioned(model: Any, **props: Any) -> Any:
    merged = {"plan.mode": "partitioned", "plan.key": "name"}
    merged.update(props)
    return LlmSource().fetch(
        "commercial airports in France", attrs=["name"], llm_fn=model, properties=merged
    )


class TestAPartlyAnsweredRunSaysSo:
    def test_a_run_that_lost_a_part_does_not_report_ok(self) -> None:
        # The consumer-visible invariant, stated without naming which member replaces it:
        # whatever `outcome` becomes, `ok` is the one reading it may not be.
        result = partitioned(OneLeafRaises())
        assert result.records
        assert result.meta.outcome != Outcome.OK

    def test_a_complete_run_and_a_partly_failed_run_do_not_look_alike(self) -> None:
        # The adversarial twin. Two runs over the same plan, differing only in whether one
        # part came back, must be distinguishable from `outcome` alone -- which is the
        # field's entire purpose.
        whole = partitioned(Planner())
        lost_one = partitioned(OneLeafRaises())
        assert whole.meta.outcome != lost_one.meta.outcome

    def test_a_run_that_lost_a_part_reports_partial(self) -> None:
        result = partitioned(OneLeafRaises())
        assert result.meta.outcome == Outcome.PARTIAL

    def test_a_partial_run_holding_no_records_is_still_partial(self) -> None:
        # Defined over the plan, not the row count. A run whose surviving parts honestly
        # returned nothing has still not been told what the lost part held, and `ok` on it
        # claims the model said there are none.
        result = partitioned(OneLeafRaisesAndTheRestAreEmpty())
        assert result.records == []
        assert result.meta.outcome == Outcome.PARTIAL

    def test_partial_is_a_published_member(self) -> None:
        assert Outcome.PARTIAL in Outcome.ALL


class TestTwoShortfallsAtOnce:
    """Which member wins when a run both loses a part and hits a ceiling.

    Every other case in this file is one-dimensional, and a precedence rule with no
    two-dimensional case is a rule nobody tested. The principle these pin is not that one
    shortfall matters more than another. It is that a fact with somewhere else to live keeps
    living there: a continuation cap has ``stopped_by``, a row trim has ``truncated_to``, and
    a lost part has nowhere but ``outcome``. So ``outcome`` goes to the fact that would
    otherwise be erased, and the other two are read where they already are.
    """

    def test_a_lost_part_and_a_capped_part_report_both(self) -> None:
        result = partitioned(
            OneLeafRaisesAndTheRestKeepProducing(), **{"plan.max_continuations": 1}
        )
        assert result.meta.outcome == Outcome.PARTIAL
        assert result.meta.stopped_by == "plan.max_continuations"

    def test_a_lost_part_and_a_trimmed_result_report_both(self) -> None:
        # `truncated` is a statement about the rows a caller holds; `partial` is a statement
        # about the parts that were asked. Reporting the trim in `outcome` would drop the
        # only field saying a part is missing, and the row count cannot show it.
        result = partitioned(OneLeafRaises(), **{"limits.max_rows": 1})
        assert result.meta.truncated_to == 1
        assert result.meta.outcome == Outcome.PARTIAL

    def test_a_capped_run_that_lost_nothing_still_reports_the_cap(self) -> None:
        # The control. `PARTIAL` taking precedence must not make the cap unreachable for a
        # run where every part came back.
        result = partitioned(Planner(), **{"plan.max_continuations": 1})
        assert result.meta.outcome == Outcome.STOPPED_BY_LIMIT
        assert result.meta.stopped_by == "plan.max_continuations"


class TestAFailureIsNotALimit:
    """A part that raised did not cross a ceiling, and must not be filed under one."""

    def test_a_lost_part_does_not_name_a_budget_axis(self) -> None:
        # `stopped_by` names the axis a caller can raise. A provider failure is not one, so
        # pointing at it sends the caller to widen a limit that was never reached, and it
        # corrupts the field for every consumer that switches on it.
        result = partitioned(OneLeafRaises())
        assert result.meta.stopped_by is None

    def test_a_lost_part_is_not_reported_as_stopped_by_limit(self) -> None:
        result = partitioned(OneLeafRaises())
        assert result.meta.outcome != Outcome.STOPPED_BY_LIMIT

    def test_losing_every_part_still_raises(self) -> None:
        # With nothing partial to preserve, the caller is owed the exception the contract
        # promises rather than an empty list with a note attached.
        with pytest.raises(LlmSourceError):
            partitioned(EveryLeafRaises())


class TestTheOutcomeCarriesTheShortfallWithoutTheGapList:
    def test_the_gap_list_still_names_the_lost_part(self) -> None:
        # `outcome` marks an incomplete answer; `gaps` identifies the missing part.
        result = partitioned(OneLeafRaises())
        assert any("leaf_failed" in gap for gap in result.meta.gaps)

    def test_a_whole_run_reports_neither_a_lost_part_nor_a_short_outcome(self) -> None:
        # The other half of the pair. A member that fires on a clean run is a member nobody
        # can act on.
        #
        # Not "no gaps at all": a partitioned run over records with no identifying column
        # reports `dedup_key_absent`, which is a true statement about merging rather than a
        # shortfall in what was asked. The claim under test is narrower.
        result = partitioned(Planner())
        assert result.meta.outcome == Outcome.OK
        assert not [gap for gap in result.meta.gaps if gap.startswith("leaf_")]
