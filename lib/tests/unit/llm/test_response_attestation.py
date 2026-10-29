"""Keep provider attestations bound to the response they describe.

A leaf can make several calls, so the matrix covers both the provider reason and the response
index. Accumulating one flag across the loop could otherwise attach an earlier response's
attestation to a later recovered prefix.
"""

from __future__ import annotations

import json
from typing import Any, cast

from blue_data_llm_source.execution.mapper import Answer, run_leaf
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.plan import ROOT, Leaf
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import Attr, Capability, Usage

ATTRS = [Attr(name="name")]

WHOLE_A = json.dumps([{"name": "A"}])
WHOLE_B = json.dumps([{"name": "B"}])
CUT_B = '[{"name": "B"}, {"name": "C'
CUT_D = '[{"name": "D"}, {"name": "E'
"""A prefix: one whole record, then a value that never closes."""


def leaf() -> Leaf:
    return Leaf(id=f"{ROOT}.0", parent_id=ROOT, query="airports", constraints={})


def run(*answers: Answer, continuations: int = 3) -> Any:
    """Drive one leaf through a scripted sequence of responses."""
    replies = list(answers)

    def ask(_prompt: str) -> Answer:
        return replies.pop(0) if replies else Answer("[]")

    return run_leaf(
        leaf(),
        ask,
        attrs=ATTRS,
        limits=PlanLimits(max_continuations=continuations),
        key="name",
    )


class TestAnAttestationBelongsToTheResponseThatCarriedIt:
    """Two points on the reason axis crossed with two points on the response axis."""

    def test_a_reason_on_a_complete_response_does_not_attest_a_later_recovery(self) -> None:
        # A complete first response cannot attest recovery of the second.
        attested_first = run(Answer(WHOLE_A, finish_reason="length"), Answer(CUT_B))
        silent_first = run(Answer(WHOLE_A, finish_reason="stop"), Answer(CUT_B))

        assert attested_first.recovered_prefix is silent_first.recovered_prefix is True
        # The first response's reason cannot attest the second response's recovery.
        assert attested_first.recovery_attested is False
        assert silent_first.recovery_attested is False
        # The run-level axis is a different question and does differ, correctly: one run holds
        # a provider attestation that generation was cut and the other does not.
        assert attested_first.provider_output_limit is True
        assert silent_first.provider_output_limit is False

    def test_a_reason_on_the_recovered_response_does_attest_it(self) -> None:
        # The other half, without which the test above would pass on a flag that is never
        # set at all.
        run_ = run(Answer(WHOLE_A, finish_reason="stop"), Answer(CUT_B, finish_reason="length"))

        assert run_.recovered_prefix is True
        assert run_.recovery_attested is True

    def test_two_recoveries_report_whether_any_was_attested(self) -> None:
        # Arity two on the axis the leaf-level flag is derived over. A single boolean
        # accumulated across the loop cannot distinguish this from the case below, which is
        # why the leaf keeps the per-response record and derives the flag from it.
        run_ = run(Answer(CUT_B), Answer(CUT_D, finish_reason="length"))

        assert run_.recovered_prefix is True
        assert run_.recovery_attested is True

    def test_two_unattested_recoveries_attest_nothing(self) -> None:
        run_ = run(Answer(CUT_B), Answer(CUT_D))

        assert run_.recovered_prefix is True
        assert run_.recovery_attested is False

    def test_a_complete_run_carrying_a_length_reason_recovers_nothing(self) -> None:
        # A document can close at the allowance. Nothing was recovered, and the attestation
        # still stands as a fact about the run -- the two are independent and the pairing
        # must not collapse them in either direction.
        run_ = run(Answer(WHOLE_A, finish_reason="length"), Answer(WHOLE_B))

        assert run_.recovered_prefix is False
        assert run_.recovery_attested is False
        assert run_.provider_output_limit is True


class TestThePerResponseRecordIsWhatTheFlagsAreDerivedFrom:
    """The flags are a summary. A caller reasoning about which call to change needs the detail."""

    def test_each_response_is_recorded_with_what_was_observed_about_it(self) -> None:
        run_ = run(Answer(WHOLE_A, finish_reason="stop"), Answer(CUT_B, finish_reason="length"))

        # The scripted answers, then whatever ended the loop. A leaf keeps asking until a
        # response adds nothing, so the trailing empty one is the loop working rather than a
        # third thing this test arranged; the assertion is about the two that were scripted.
        assert len(run_.responses) >= 2
        assert [(r.recovered, r.finish_reason) for r in run_.responses[:2]] == [
            (False, "stop"),
            (True, "length"),
        ]

    def test_the_leaf_flags_agree_with_the_record_they_summarize(self) -> None:
        # The invariant that keeps the summary honest, rather than a second place to get it
        # wrong. Stated over a run where the two facts land on different responses.
        run_ = run(Answer(CUT_B), Answer(WHOLE_A, finish_reason="length"))

        assert run_.recovered_prefix == any(r.recovered for r in run_.responses)
        assert run_.provider_output_limit == any(
            r.finish_reason == "length" for r in run_.responses
        )
        assert run_.recovery_attested == any(
            r.recovered and r.finish_reason == "length" for r in run_.responses
        )


class TestAClientThatWritesNoReceiptAttestsNothing:
    """A call without a receipt cannot inherit the previous call's attestation.

    The client protocol permits zero receipts for a call.
    """

    class SkipsTheSecondReceipt:
        """Writes a receipt carrying ``length`` for the first call and none for the second."""

        def __init__(self, *answers: str) -> None:
            self.answers = list(answers)
            self.calls = 0

        def budget_capabilities(self) -> frozenset[str]:
            return Capability.ALL

        def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            return self._respond(properties)

        async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            return self._respond(properties)

        def _respond(self, properties: dict[str, Any] | None) -> str:
            index = self.calls
            self.calls += 1
            if index == 0:
                sink = (properties or {}).get("usage_sink")
                if isinstance(sink, list):
                    cast("list[Usage]", sink).append(Usage(model="stub", finish_reason="length"))
            return self.answers[index] if index < len(self.answers) else "[]"

    def test_a_missing_receipt_does_not_inherit_the_previous_call_s_reason(self) -> None:
        client = self.SkipsTheSecondReceipt(WHOLE_A, CUT_B)
        result = LlmSource(client=client).fetch(
            "airports",
            attrs=["name"],
            properties={"plan.key": "name", "plan.max_continuations": 2},
        )

        assert client.calls >= 2
        # The first call's `length` is a real attestation and still names the run-level axis.
        # What must not happen is the *second* response's lost prefix inheriting it: that is
        # the cause sentence, and it is the thing `sink[-1]` was getting wrong.
        assert not [gap for gap in result.meta.gaps if "output allowance" in gap]


class TestTheAttestationReachesThePublicResult:
    """Check that a leaf attestation reaches caller-visible classification.

    A leaf-only assertion would miss an attestation lost before ``Coverage._hit_a_ceiling``.
    """

    class AttestsALengthStop:
        """Answers with a complete document and a receipt saying the allowance ended it."""

        def __init__(self, answer: str) -> None:
            self.answer = answer

        def budget_capabilities(self) -> frozenset[str]:
            return Capability.ALL

        def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            return self._respond(properties)

        async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            return self._respond(properties)

        def _respond(self, properties: dict[str, Any] | None) -> str:
            sink = (properties or {}).get("usage_sink")
            if isinstance(sink, list):
                cast("list[Usage]", sink).append(Usage(model="stub", finish_reason="length"))
            return self.answer

    def fetch(self, answer: str) -> Any:
        return LlmSource(client=self.AttestsALengthStop(answer)).fetch("airports", attrs=["name"])

    def test_a_complete_response_the_provider_cut_is_not_reported_as_ok(self) -> None:
        # A `length` finish reason attests that the output allowance fired.
        result = self.fetch(WHOLE_A)

        assert result.records
        assert result.meta.outcome != "ok"
        assert result.meta.stopped_by == "provider_output_limit"

    def test_a_run_the_provider_did_not_cut_still_reports_ok(self) -> None:
        # The pair. An axis that fires on every run is one nobody can act on.
        class Silent(self.AttestsALengthStop):
            def _respond(self, properties: dict[str, Any] | None) -> str:
                return self.answer

        result = LlmSource(client=Silent(WHOLE_A)).fetch("airports", attrs=["name"])

        assert result.meta.outcome == "ok"
        assert result.meta.stopped_by is None
