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
        # The defect, stated as the caller sees it. Only the *first* response's reason
        # differs between these two runs, and the first response is complete -- nothing
        # about it was recovered, so it has nothing to attest.
        attested_first = run(Answer(WHOLE_A, finish_reason="length"), Answer(CUT_B))
        silent_first = run(Answer(WHOLE_A, finish_reason="stop"), Answer(CUT_B))

        assert attested_first.recovered_prefix is silent_first.recovered_prefix is True
        # `recovery_attested` is the pairing this file exists for: the reason belongs to the
        # first, complete response and explains nothing about the second one's lost prefix.
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

    def test_the_leaf_flags_agree_with_the_record_they_summarise(self) -> None:
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
    """The other half of the defect, and it is about the double rather than the arity.

    The reason reaches the leaf through the per-call receipts a client writes into the sink
    it was handed. Reading the sink's *last* entry after a call assumed every call leaves
    exactly one, which the client protocol does not require -- and a client that writes none
    for this call hands back the previous call's reason.

    No arity would have caught that, because the axis lives in the client's behaviour rather
    than in ours. A double that always writes exactly one receipt hides it at every response
    count, which is why fidelity to what the protocol *permits* is its own rule.
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

    A leaf-only assertion cannot detect a missing handoff to ``Coverage._hit_a_ceiling``.
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
        # The published contract is the oracle: `Usage.finish_reason` and `STOPPED_BY_OUTPUT`
        # both say `length` is evidence the allowance fired. A run holding that attestation
        # cannot report that nothing cut it short.
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
