"""The four specified fixtures, replayed through the real pipeline.

The cassettes in ``tests/cassettes/`` are recorded provider responses from real calls,
made once by ``tests/record_cassettes.py`` and committed. Replaying them exercises the
whole path a live call takes, parsing included, without a network connection.

These tests assert **shape and behaviour, never the fact**. The recorded model named a
different president than the specification's example does, because a model answers from
its own knowledge and that knowledge has a date on it. Asserting the name would make the
suite fail on re-record for a reason that has nothing to do with this library, and would
quietly turn a probabilistic source into one the tests pretend is factual.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from blue_data_llm_source import Capability, LlmSource
from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.types import Outcome, Usage

CASSETTES = Path(__file__).resolve().parents[1] / "cassettes"


def load(name: str) -> dict[str, Any]:
    return json.loads((CASSETTES / f"{name}.json").read_text(encoding="utf-8"))


class CassetteClient:
    """Replays one recorded response and reports its recorded usage."""

    def __init__(self, name: str) -> None:
        self.payload = load(name)
        self.budget = Budget()
        self.prompts: list[str] = []
        self.usages: list[Usage] = []

    def _replay(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        budget = (properties or {}).get("budget")
        # `attempt`, like every other double: this one replays a recorded call and
        # must model the lifecycle it claims to replay.
        with (budget if isinstance(budget, Budget) else self.budget).attempt() as reservation:
            reservation.settle(0.0)
            self.prompts.append(prompt)
            usage = self.payload.get("usage", {})
            record = Usage(
                model=str(self.payload.get("model", "")),
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
            )
            self.usages.append(record)
            sink = (properties or {}).get("usage_sink")
            if isinstance(sink, list):
                cast("list[Usage]", sink).append(record)
            return str(self.payload["choices"][0]["message"]["content"])

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._replay(prompt, properties)

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._replay(prompt, properties)


def test_every_cassette_is_present_and_well_formed() -> None:
    names = [
        "president_no_attrs",
        "president_with_attrs",
        "president_with_context",
        "three_presidents",
    ]
    for name in names:
        payload = load(name)
        assert payload["choices"][0]["message"]["content"]
        assert payload["usage"]["completion_tokens"] > 0


def test_the_receipt_records_what_the_recording_cost() -> None:
    receipt = json.loads((CASSETTES / "RECEIPT.json").read_text(encoding="utf-8"))
    assert receipt["total_cost_usd"] <= receipt["ceiling_usd"]
    assert len(receipt["cassettes"]) == 4
    for entry in receipt["cassettes"]:
        assert entry["cost_usd"] >= 0
        assert entry["model"]


def test_case_one_no_attributes_returns_records_with_model_chosen_keys() -> None:
    client = CassetteClient("president_no_attrs")
    result = LlmSource(client=client).fetch("Who is the current US president?")

    assert result.records, "the recorded response contains at least one record"
    assert result.meta.outcome == Outcome.OK
    assert result.meta.observed_keys, "keys the model chose are reported"
    assert result.meta.requested_attrs == []


def test_case_two_requested_attributes_are_the_only_keys() -> None:
    wanted = ["first name", "last name", "age", "party"]
    client = CassetteClient("president_with_attrs")
    result = LlmSource(client=client).fetch("Who is the current US president?", attrs=wanted)

    assert result.records
    for record in result.records:
        assert list(record.keys()) == wanted
    assert result.meta.unmapped_keys == []


def test_case_three_context_disambiguates_an_attribute_name() -> None:
    """The specified use of context: clarifying what an attribute means.

    The instruction was that ``name`` should be the last name. The check is that the
    value is a single token, which is the observable effect, rather than which surname
    the model happened to know.
    """
    client = CassetteClient("president_with_context")
    result = LlmSource(client=client).fetch(
        "Who is the current US president?",
        attrs=["name", "age", "party"],
        context="The name in the output should be the last name.",
    )

    assert result.records
    name = result.records[0]["name"]
    assert isinstance(name, str) and name
    assert " " not in name.strip(), (
        f"context asked for a last name only, got {name!r}. The prompt carries the "
        "context, so a multi-token name means it was not honoured."
    )
    assert "last name" in client.prompts[0], "the context text must reach the model"


def test_case_four_a_count_in_the_query_yields_that_many_records() -> None:
    client = CassetteClient("three_presidents")
    result = LlmSource(client=client).fetch(
        "Show me 3 presidents of the United States",
        attrs=[
            {"name": "name", "type": "string"},
            {"name": "year", "type": "int"},
            {"name": "party", "type": "string"},
        ],
        context="Year in output refers to the year that the person became president.",
    )

    assert len(result.records) == 3
    for record in result.records:
        assert set(record.keys()) == {"name", "year", "party"}
        assert isinstance(record["year"], int), "declared int is coerced from the response"
    assert result.meta.type_mismatches == []


def test_a_cassette_run_reports_the_recorded_usage() -> None:
    client = CassetteClient("three_presidents")
    result = LlmSource(client=client).fetch("Show me 3 presidents of the United States")

    assert result.meta.calls == 1
    assert result.meta.completion_tokens > 0
    assert result.meta.model


@pytest.mark.parametrize(
    "name",
    ["president_no_attrs", "president_with_attrs", "president_with_context", "three_presidents"],
)
def test_no_cassette_carries_a_credential(name: str) -> None:
    """Recorded responses only. A request body or a key would be a leak."""
    text = (CASSETTES / f"{name}.json").read_text(encoding="utf-8").lower()
    for marker in ("sk-", "api_key", "authorization", "bearer "):
        assert marker not in text, f"{name}.json contains {marker!r}"


def test_the_recorder_refuses_a_call_that_could_cross_its_ceiling() -> None:
    """The ceiling must bound spend, not report it after the fact."""
    from tests.record_cassettes import MAX_CALL_COST_USD, fits

    assert fits(0.0, 1.0)
    assert fits(1.0 - MAX_CALL_COST_USD, 1.0)
    # Room for less than one worst-case call is no room at all.
    assert not fits(1.0 - MAX_CALL_COST_USD / 2, 1.0)
    assert not fits(1.0, 1.0)


class _ScriptedProvider:
    """A provider that records what it was asked and never leaves the machine."""

    def __init__(self, cost: Any = 0.0) -> None:
        self.calls: list[dict[str, Any]] = []
        self._cost = cost

    def completion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": "[]"}}], "usage": {}}

    def completion_cost(self, **kwargs: Any) -> Any:
        if self._cost is None:
            raise RuntimeError("cost unavailable")
        return self._cost


def test_the_unaffordable_call_is_never_entered() -> None:
    """Driven through the loop, not just the predicate.

    A ceiling checked only by a helper proves nothing about whether the loop consults it
    before spending.
    """
    from tests.record_cassettes import CASES, MAX_CALL_COST_USD, record

    provider = _ScriptedProvider(cost=MAX_CALL_COST_USD)
    ceiling = MAX_CALL_COST_USD * 2.5
    written, spent = record(ceiling, provider, write=False)

    assert len(provider.calls) == 2, "the third call would not have fitted"
    assert len(written) == 2
    assert spent <= ceiling
    assert len(CASES) > 2, "the fixture set must be large enough for this to bite"


def test_an_unreadable_cost_stops_recording_rather_than_counting_as_free() -> None:
    """None is not zero. Treating it as zero let every remaining call through."""
    from tests.record_cassettes import MAX_CALL_COST_USD, record

    provider = _ScriptedProvider(cost=None)
    written, spent = record(1.0, provider, write=False)

    assert len(provider.calls) == 1, "recording must stop at the first unreadable cost"
    assert written == []
    assert spent == MAX_CALL_COST_USD, "the reservation stays charged; it was committed"
