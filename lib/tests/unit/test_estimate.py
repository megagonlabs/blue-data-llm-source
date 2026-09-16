"""What a fetch could cost, answered before anything is spent.

For a caller deciding whether to run a request at all: a planner sizing a fan-out, or an
operator checking a request against a ceiling. The figure is an upper bound on the prompt
this source would actually send, not an average of requests like it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from blue_data_llm_source import LlmSource
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.types import TransportError
from tests.doubles import LitellmStub, ScriptedClient, chat_payload

InstallStub = Callable[[LitellmStub], LitellmStub]
ONE_RECORD = '[{"name": "Ada"}]'


def test_an_estimate_bounds_a_priced_model() -> None:
    source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 256})
    estimate = source.estimate("the member states of the European Union", attrs=["country"])

    assert estimate.bounded
    assert estimate.max_cost is not None and estimate.max_cost > 0
    assert estimate.model == "gpt-4o-mini"
    assert estimate.max_output_tokens == 256
    assert estimate.prompt_bytes > 0


def test_an_estimate_contacts_nothing() -> None:
    """The whole point: a caller asks before deciding whether to spend."""
    client = ScriptedClient([])
    source = LlmSource(client=client)

    source.estimate("who?", attrs=["name"])

    assert client.prompts == [], "estimating must not call the model"


def test_an_unpriceable_model_is_reported_rather_than_raised() -> None:
    """Asking what a request might cost is reasonable even where the answer is unknown."""
    source = LlmSource(properties={"llm.model": "brand-new-model"})
    estimate = source.estimate("who?")

    assert not estimate.bounded
    assert estimate.max_cost is None
    assert estimate.reason and "no published prices" in estimate.reason


def test_caller_supplied_rates_make_an_unlisted_model_estimable() -> None:
    source = LlmSource(
        properties={
            "llm.model": "brand-new-model",
            "llm.max_tokens": 64,
            "llm.price_per_input_token": 1e-6,
            "llm.price_per_output_token": 2e-6,
        }
    )
    assert source.estimate("who?").bounded


def test_an_empty_request_costs_nothing() -> None:
    """It never reaches the model, so there is nothing to bound."""
    estimate = LlmSource().estimate("   ")

    assert estimate.bounded
    assert estimate.max_cost == 0.0


def test_a_longer_request_estimates_higher() -> None:
    source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 16})
    small = source.estimate("who?")
    large = source.estimate("who? " * 400)

    assert small.max_cost is not None and large.max_cost is not None
    assert large.max_cost > small.max_cost


def test_the_estimate_bounds_what_a_fetch_actually_spends(litellm_stub: InstallStub) -> None:
    """The property the whole thing rests on, driven through a real fetch.

    Comparing the estimate against another call of the same pricing function proves only
    that an implementation matches itself. What matters is that a fetch, retries included,
    never spends more than the figure a caller was given — and it did, by exactly the
    retry multiplier, until the estimate started counting attempts.
    """
    litellm_stub(LitellmStub(error=RuntimeError("boom")))
    source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64})
    estimate = source.estimate("who?")

    budget = Budget(Limits(max_cost=10.0))
    with pytest.raises(TransportError):
        source.fetch("who?", budget=budget)

    assert estimate.max_cost is not None
    assert budget.cost <= estimate.max_cost, (
        f"a fetch spent {budget.cost} against an estimate of {estimate.max_cost}"
    )
    assert estimate.max_attempts == 3, "the shipped retry default permits three attempts"
    assert estimate.max_cost_per_attempt is not None
    assert estimate.max_cost == pytest.approx(estimate.max_cost_per_attempt * 3)


def test_a_request_sized_against_its_estimate_is_not_refused_when_it_runs(
    litellm_stub: InstallStub,
) -> None:
    """The guarantee a caller actually relies on.

    A ceiling set to exactly the estimate used to be consumed by one transient failure,
    so the retry was refused and the fetch stopped on a limit the caller had sized for.
    """
    litellm_stub(LitellmStub(chat_payload(ONE_RECORD), cost=0.0, fail_times=1))
    source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64})
    estimate = source.estimate("who?")
    assert estimate.max_cost is not None

    result = source.fetch("who?", budget=Budget(Limits(max_cost=estimate.max_cost)))

    assert result.meta.stopped_by is None, "a request sized against its estimate must run"


def test_a_call_ceiling_narrows_the_attempts_the_estimate_counts() -> None:
    """One allowed call cannot make three attempts, however many retries are configured.

    The estimate bounds what a run can spend, so every ceiling that clamps it belongs in the
    figure. Admission asks the opposite question — whether a fan-out fits inside the call cap
    — and there the cap is the capacity under test rather than a bound on the demand, which
    is why the two sit behind separate accessors."""
    source = LlmSource(
        properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64, "limits.max_calls": 1}
    )
    estimate = source.estimate("who?")

    assert estimate.max_attempts == 1
    assert estimate.max_cost == pytest.approx(estimate.max_cost_per_attempt)


def test_an_opaque_client_cannot_be_estimated() -> None:
    """Only a client that drives the provider knows the model and rates a call will use.

    Pricing the source's own properties instead produced a figure three orders of magnitude
    below what an injected client actually reserved.
    """
    source = LlmSource(client=ScriptedClient([ONE_RECORD]))
    estimate = source.estimate("who?")

    assert not estimate.bounded
    assert estimate.reason and "does not report what an attempt will cost" in estimate.reason


def _a_callable(prompt: str) -> str:
    return ONE_RECORD


def test_a_callable_cannot_be_estimated_either() -> None:
    estimate = LlmSource().estimate("who?", llm_fn=_a_callable)
    assert not estimate.bounded


def test_the_estimate_is_reachable_through_the_query_surface() -> None:
    """A caller reaching this source through the registry can ask too."""
    source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 32})
    records = source.execute_query({"op": "LLM.ESTIMATE", "query": "who?", "attrs": ["name"]})

    assert len(records) == 1
    assert records[0]["bounded"] is True
    assert records[0]["max_cost"] > 0


def test_a_missing_model_estimate_does_not_report_a_model_it_did_not_select() -> None:
    """An unbounded estimate naming the fallback contradicts its own reason.

    The suite that missed the original contradiction could not have detected its return,
    so the case is pinned on all three fields rather than on the outcome alone.
    """
    estimate = LlmSource(properties={"llm.model": None}).estimate("who?")

    assert not estimate.bounded
    assert estimate.model == ""
    assert estimate.reason == "no model configured (set llm.model)"


def test_the_readme_client_example_produces_a_bounded_estimate() -> None:
    """The copyable client in `lib/README.md`, executed as written.

    It declares `Capability.ALL`, which includes ESTIMATE, so a reader implementing it
    exactly must get the bounded figure the README prints. Before `bound_for_attempt` was
    added to the example, that client declared the capability and returned `bounded=False`,
    and the reader had no documented way past it.
    """
    import re
    from pathlib import Path
    from typing import cast

    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    block = next(
        b
        for b in re.findall(r"```python\n(.*?)```", readme, re.S)
        if "class MyClient" in b and "bound_for_attempt" in b
    )
    namespace: dict[str, Any] = {}
    exec(compile(block, "lib/README.md", "exec"), namespace)  # noqa: S102 - the example is the test
    client_type = cast("type[Any]", namespace["MyClient"])
    per_attempt = cast("float", namespace["PER_ATTEMPT_BOUND"])

    source = LlmSource(
        properties={"limits.max_cost": 0.05, "limits.max_calls": 4},
        client=client_type(),
    )
    sized = source.estimate("the member states of the European Union", attrs=["country"])

    assert sized.bounded, sized.reason
    # Accumulated once per attempt, the way the ledger accumulates it — not multiplied.
    # The two agree exactly at three attempts and diverge above five, so asserting the
    # accumulation is what pins the rule rather than a value both happen to produce here.
    accumulated = 0.0
    for _ in range(3):
        accumulated += per_attempt
    assert sized.max_cost == accumulated


def test_a_configured_injected_client_estimates_what_it_will_admit() -> None:
    """The estimate and the admission must read the same configuration.

    `bound_for_attempt` took only the per-call dict, so a client constructed with its own
    rates estimated against the defaults and admitted against its own: the estimate landed
    inside the ceiling and the fetch was then refused before the provider was contacted,
    which is exactly the disagreement the shared figure exists to prevent.
    """
    from blue_data_llm_source.llm.client import DefaultLlmClient

    client = DefaultLlmClient(
        properties={
            "llm.model": "a-model-with-no-published-price",
            "llm.max_tokens": 64,
            "llm.price_per_input_token": 1.0,
            "llm.price_per_output_token": 1.0,
        }
    )
    source = LlmSource(properties={"limits.max_cost": 1.0}, client=client)

    query = "the member states of the European Union"
    sized = source.estimate(query)
    assert sized.bounded, sized.reason

    # At one unit per token these rates put a single attempt far above the 1.0 ceiling, so
    # the run must refuse — and the estimate must say so first. Reading only the per-call
    # dict, it priced against the default model instead and reported 0.043, comfortably
    # inside a ceiling the fetch then refused before contacting anything.
    result = source.fetch(query)
    assert result.meta.stopped_by == "max_cost"
    assert result.meta.calls == 0

    assert sized.max_cost is not None
    assert sized.max_cost > 1.0, (
        f"the estimate reported {sized.max_cost}, inside a ceiling the run refuses; an "
        "estimate a caller sizes against and the bound the run enforces must agree"
    )


def test_an_injected_clients_configuration_outranks_the_sources_defaults() -> None:
    """A default may fill a gap; it may not overwrite a deliberate choice.

    The source's `gpt-4o` replaced an injected client's private model while that client's
    rate overrides survived, so the call was priced at one model's rates and made against
    another — and a cost ceiling was crossed by the difference.
    """
    from blue_data_llm_source.llm.client import DefaultLlmClient

    client = DefaultLlmClient(
        properties={
            "llm.model": "private-model",
            "llm.max_tokens": 256,
            "llm.price_per_input_token": 1e-9,
            "llm.price_per_output_token": 1e-9,
        }
    )
    sized = LlmSource(properties={"limits.max_cost": 0.01}, client=client).estimate("who?")

    assert sized.model == "private-model", "a source default overwrote the client's model"
    assert sized.max_output_tokens == 256
    assert sized.bounded

    # What the caller sets still wins over both.
    chosen = LlmSource(properties={"llm.model": "caller-choice"}, client=client)
    assert chosen.estimate("who?").model == "caller-choice"


def test_the_ledger_admits_every_charge_a_summed_ceiling_accounted_for() -> None:
    """Six charges of 0.0001233 sum to more than they do added one at a time.

    The ledger accumulated `cost` with repeated addition, so after five unpriced attempts it
    sat an ulp above where the sum says it should, and the sixth attempt — the last one the
    ceiling had accounted for — was refused.
    """
    from blue_data_llm_source.llm.budget import Budget, Limits

    per_attempt = 0.0001233
    attempts = 6
    accumulated = 0.0
    for _ in range(attempts):
        accumulated += per_attempt
    # An ulp above both `per_attempt * 6` and `fsum([per_attempt] * 6)`, which is the whole
    # point: the ledger reaches this figure, so the ceiling has to be this figure.
    assert accumulated > math.fsum([per_attempt] * attempts)
    budget = Budget(Limits(max_cost=accumulated))

    for _ in range(attempts - 1):
        budget.reserve(per_attempt).keep_unpriced()

    budget.reserve(per_attempt)  # the last one the ceiling accounted for


def test_the_estimate_aggregates_the_way_the_ledger_does() -> None:
    """One aggregation for the published figure and the running total, or they disagree.

    `estimate` multiplied the per-attempt bound by the attempt count while the ledger summed
    the charges. For 0.0001233 over six attempts the product is an ulp below the sum, so a
    ceiling set to exactly the published figure refused the final attempt it had accounted
    for. This pins the choice; the drift it prevents is measured above.
    """
    sized = LlmSource(
        properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 64, "limits.max_retries": 5}
    ).estimate("who?")
    assert sized.max_cost is not None and sized.max_cost_per_attempt is not None
    assert sized.max_attempts == 6
    accumulated = 0.0
    for _ in range(sized.max_attempts):
        accumulated += sized.max_cost_per_attempt
    assert sized.max_cost == accumulated, (
        "the published figure must be the one the ledger will actually reach"
    )


def test_a_client_that_will_not_respect_the_attempt_allowance_is_unestimable() -> None:
    """A whole-fetch bound cannot be built from a per-attempt figure alone.

    `estimate` accumulates the per-attempt bound once per permitted attempt, which is only an
    upper bound if the client makes at most that many attempts. A client with its own retry
    policy correctly reserving and settling four attempts against a three-attempt allowance
    spent past a figure the caller had sized a ceiling from. Declaring `Capability.ESTIMATE`
    is the promise not to; omitting it is the honest alternative.
    """
    from blue_data_llm_source.types import Capability

    answer = '[{"name": "Ada"}]'

    class WithoutTheClaim:
        """Prices an attempt, and does not promise how many it will make."""

        def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            return answer

        async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            return answer

        def budget_capabilities(self) -> frozenset[str]:
            return frozenset({Capability.CALLS, Capability.COST})

        def bound_for_attempt(self, prompt: str, properties: dict[str, Any]) -> float:
            return 0.1

    sized = LlmSource(client=WithoutTheClaim()).estimate("who?")
    assert sized.bounded is False
    assert sized.reason


def test_no_shipped_file_describes_the_whole_fetch_bound_as_a_product() -> None:
    """The bound accumulates; nothing may tell a caller to multiply.

    Six files said "times the attempts" after one of them was corrected to say
    "accumulated". Both phrasings name the same quantity and agree numerically below six
    attempts, so nothing failed and no reader could tell which to size a ceiling against.
    Fixing them one at a time is what let that happen twice, so the rule is a test.
    """
    import re

    root = Path(__file__).resolve().parents[3]
    wrong = re.compile(
        r"max_cost_per_attempt \* max_attempts"
        r"|per-attempt (?:bound|figure) times"
        r"|multipl(?:y|ies|ied) it by the attempts"
        r"|times the attempts",
        re.IGNORECASE,
    )
    offenders: list[str] = []
    for path in root.rglob("*"):
        if path.suffix not in {".py", ".md", ".toml"} or ".venv" in path.parts:
            continue
        if path.resolve() == Path(__file__).resolve():
            continue  # this file names the phrasings in order to forbid them
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if wrong.search(line):
                offenders.append(f"{path.relative_to(root)}:{number}")
    assert not offenders, "the whole-fetch bound accumulates, it is not a product: " + ", ".join(
        offenders
    )
