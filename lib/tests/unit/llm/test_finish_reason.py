"""What the provider said about why it stopped, and what may be claimed from it.

A response that ends mid-structure and one that was merely written badly are the same
document. Only the provider knows which happened, and it says so in ``finish_reason`` --
a field the client reads past today, because ``complete`` returns text and the reason has
nowhere to travel.

Read asymmetrically, and the asymmetry is the whole design. ``length`` is evidence the
output allowance fired. ``stop`` and an absent reason are *absence of evidence*, not
evidence of absence: litellm defaults a provider reason it does not recognise to ``stop``,
so treating ``stop`` as proof the model finished would convert an unrecognised truncation
into a claim of completeness -- which is the one claim this package may not make.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from blue_data_llm_source.execution.mapper import run_leaf
from blue_data_llm_source.llm.client import DefaultLlmClient
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.plan import ROOT, Leaf
from blue_data_llm_source.types import Attr
from tests.doubles import LitellmStub, chat_payload

InstallStub = Callable[[LitellmStub], LitellmStub]

ATTRS = [Attr(name="name")]

WHOLE = '[{"name": "A"}, {"name": "B"}]'
CUT = '[{"name": "A"}, {"name": "B'
"""A prefix of a longer document. One whole record, then a value that never closes.

Indistinguishable from a malformed answer by inspection, which is the point: what tells
the two apart arrives beside the text, not inside it.
"""


def answered(content: str, reason: str | None) -> dict[str, Any]:
    payload = chat_payload(content)
    if reason is not None:
        payload["choices"][0]["finish_reason"] = reason
    return payload


def leaf() -> Leaf:
    return Leaf(id=f"{ROOT}.0", parent_id=ROOT, query="airports", constraints={})


class TestTheClientRecordsWhatTheProviderSaid:
    """The reason reaches the per-call receipt, which already crosses this boundary.

    On the receipt rather than in the return value: ``complete`` returns ``str`` and is a
    published protocol other clients implement, including one wrapping a caller's plain
    callable. Widening it would make every implementer answer a question most of them
    cannot, and the honest answer -- "no reason available" -- is what the receipt's default
    already says.
    """

    @pytest.mark.parametrize("reason", ["length", "stop", "content_filter", "tool_calls"])
    def test_the_reason_is_recorded_as_the_provider_gave_it(
        self, litellm_stub: InstallStub, reason: str
    ) -> None:
        litellm_stub(LitellmStub(answered(WHOLE, reason)))
        client = DefaultLlmClient()

        client.complete("who?")

        assert client.last_usage is not None
        assert client.last_usage.finish_reason == reason

    def test_a_provider_that_says_nothing_records_nothing(self, litellm_stub: InstallStub) -> None:
        # Not "stop". A missing reason and a reported stop are different facts, and
        # defaulting one to the other invents an attestation the provider never made.
        litellm_stub(LitellmStub(answered(WHOLE, None)))
        client = DefaultLlmClient()

        client.complete("who?")

        assert client.last_usage is not None
        assert client.last_usage.finish_reason is None

    def test_a_malformed_reason_is_recorded_as_nothing_rather_than_as_a_stop(
        self, litellm_stub: InstallStub
    ) -> None:
        payload = chat_payload(WHOLE)
        payload["choices"][0]["finish_reason"] = {"unexpected": "shape"}
        litellm_stub(LitellmStub(payload))
        client = DefaultLlmClient()

        client.complete("who?")

        assert client.last_usage is not None
        assert client.last_usage.finish_reason is None


class TestARecoveredPrefixIsNotAnAttestedCeiling:
    """The two facts that were one flag, separated.

    ``recovered_prefix`` is observable here: the document did not parse whole and its
    complete records were kept. Whether the provider's output allowance caused it is not
    observable here, and a run driven through a plain callable can never observe it.
    """

    def run(self, answer: str) -> Any:
        return run_leaf(leaf(), lambda _prompt: answer, attrs=ATTRS, limits=PlanLimits())

    def test_a_recovered_prefix_is_reported_as_a_recovered_prefix(self) -> None:
        run = self.run(CUT)
        assert run.recovered_prefix is True
        assert [r["name"] for r in run.records] == ["A"]

    def test_a_whole_document_recovers_nothing(self) -> None:
        run = self.run(WHOLE)
        assert run.recovered_prefix is False

    def test_an_unattested_recovery_does_not_claim_the_provider_ceiling(self) -> None:
        # The adversarial twin for this pair. A callable model has no reason to give, so a
        # recovery through it is the case where the claim is least supportable -- and it is
        # also the default path, which is where an overclaim does the most damage.
        run = self.run(CUT)
        assert run.provider_output_limit is False

    def test_the_shortfall_is_still_reported(self) -> None:
        # Weakening the cause must not delete the effect. Records were lost inside an
        # answer that was already paid for, and a caller has to be told that much.
        run = self.run(CUT)
        assert run.outcome != "ok"


class TestAnAttestedCeilingIsClaimed:
    """The other half, without which this file only proves the claim is never made.

    Weakening a claim is easy to test badly: assert it is absent everywhere and the code
    that never makes it passes. These are the cases where the claim is *owed*, and they are
    the reason the reason has to cross the execution seam at all rather than stopping at the
    client.

    The seam is internal. ``run_leaf`` and ``arun_leaf`` take the model as a callable and are
    not exported from the package; what stays exactly as published is
    ``LlmClientProtocol.complete() -> str``, which no caller-supplied client has to change.
    A callable that answers with bare text keeps working and reports no reason, which is the
    honest answer for a model that cannot give one.
    """

    def run(self, answer: Any) -> Any:
        return run_leaf(leaf(), lambda _prompt: answer, attrs=ATTRS, limits=PlanLimits())

    def test_a_prefix_the_provider_attested_is_reported_as_a_provider_limit(self) -> None:
        from blue_data_llm_source.execution.mapper import Answer

        run = self.run(Answer(CUT, finish_reason="length"))
        assert run.recovered_prefix is True
        assert run.provider_output_limit is True

    def test_the_same_prefix_without_an_attestation_claims_nothing(self) -> None:
        # The adversarial twin: one document, two runs, differing only in what arrived
        # beside it. The pair is the whole point -- a flag that reads the same either way is
        # a flag carrying no information.
        from blue_data_llm_source.execution.mapper import Answer

        attested = self.run(Answer(CUT, finish_reason="length"))
        silent = self.run(Answer(CUT, finish_reason=None))
        assert attested.recovered_prefix == silent.recovered_prefix
        assert attested.provider_output_limit != silent.provider_output_limit

    def test_a_reported_stop_is_not_evidence_the_model_finished(self) -> None:
        # litellm defaults an unrecognised provider reason to `stop`, so a stop beside a
        # document that did not parse whole is exactly the case where believing it would
        # convert an unrecognised truncation into a claim of completeness.
        from blue_data_llm_source.execution.mapper import Answer

        run = self.run(Answer(CUT, finish_reason="stop"))
        assert run.recovered_prefix is True
        assert run.provider_output_limit is False

    def test_a_reason_on_a_document_that_closed_still_names_the_axis(self) -> None:
        # A closed document proves its emitted tokens form a document. It does not negate the
        # provider's word that generation stopped because the allowance ran out, so a run
        # holding that attestation is not one nothing cut short.
        #
        # A closed document with finish_reason="length" still reports the provider's
        # output-limit signal; prefix recovery remains a separate observation.
        from blue_data_llm_source.execution.mapper import Answer

        run = self.run(Answer(WHOLE, finish_reason="length"))
        assert run.recovered_prefix is False
        assert run.provider_output_limit is True
        # And the narrower fact, which is what a sentence about a lost prefix may cite: this
        # response lost nothing, so nothing about it was attested.
        assert run.recovery_attested is False

    def test_a_bare_string_answer_still_works_and_attests_nothing(self) -> None:
        # The compatibility case. A caller's plain callable has no reason to give, and the
        # seam must not require it to invent one.
        run = self.run(WHOLE)
        assert run.records
        assert run.provider_output_limit is False
