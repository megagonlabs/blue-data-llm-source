"""One Budget across a sequence of fetches, and what happens when it runs out.

A ceiling in ``properties`` bounds one request. A ``Budget`` you build and hand to
``fetch`` bounds the whole sequence: each fetch derives a child from it, spends against
what the parent has left, and the effective ceiling on every axis is the smaller of the
two. When the parent is spent, the next fetch comes back empty with ``stopped_by`` naming
the axis, rather than raising.

Two clients answer here, because which axes bind depends on the client. The row axis is
the one an ``llm_fn`` can be held to; the call and cost axes need a client that declares
them, so the second half uses a small budget-aware double. Neither contacts a provider.

A child does not inherit its parent's ``stopped_by``, so a later affordable request under
the same budget does not report an earlier refusal as its own.

Run from the repository root:
    python3 examples/feature-demos/budget_demo.py
"""

import asyncio
import json
from typing import Any

from blue_data_llm_source import Budget, Capability, Limits, LlmSource, Usage

BOUND_PER_ATTEMPT = 0.0020
ACTUAL_PER_CALL = 0.0012


def one_row(prompt: str) -> str:
    return '[{"name": "Charles de Gaulle", "iata_code": "CDG"}]'


class LedgerClient:
    """Declares every axis and settles a fixed price, so cost and calls can bind."""

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def bound_for_attempt(self, prompt: str, properties: dict[str, Any]) -> float:
        return BOUND_PER_ATTEMPT

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return asyncio.run(self.acomplete(prompt, properties))

    async def acomplete(
        self, prompt: str, properties: dict[str, Any] | None = None
    ) -> str:
        props = properties or {}
        budget = props.get("budget") or Budget()
        with budget.attempt(BOUND_PER_ATTEMPT) as reservation:
            reservation.settle(ACTUAL_PER_CALL)
        sink = props.get("usage_sink")
        if isinstance(sink, list):
            sink.append(Usage(model="ledger-demo-1", cost=ACTUAL_PER_CALL))
        return json.dumps([{"name": "Orly", "iata_code": "ORY"}])


def rows_across_calls() -> None:
    print("a row ceiling shared by three fetches")
    source = LlmSource()
    run = Budget(Limits(max_rows=2))
    for country in ("France", "Spain", "Italy"):
        result = source.fetch(
            f"commercial airports in {country}", llm_fn=one_row, budget=run
        )
        print(
            f"  {country:7} {len(result)} record(s)"
            f"  outcome={result.meta.outcome}"
            f"  stopped_by={result.meta.stopped_by}"
            f"  rows spent={run.rows}"
        )


def calls_and_cost_across_calls() -> None:
    print()
    print("a call ceiling shared by three fetches, against a client that declares it")
    source = LlmSource(client=LedgerClient())
    run = Budget(Limits(max_calls=2))
    for country in ("France", "Spain", "Italy"):
        result = source.fetch(
            f"commercial airports in {country}", attrs=["name"], budget=run
        )
        print(
            f"  {country:7} {len(result)} record(s)"
            f"  outcome={result.meta.outcome}"
            f"  stopped_by={result.meta.stopped_by}"
        )
    print(
        f"  budget: calls={run.calls}"
        f" cost={round(run.cost, 6)}"
        f" committed={round(run.committed, 6)}"
    )

    print()
    print("a cost ceiling on the same client, spent over three fetches")
    priced = Budget(Limits(max_cost=0.004))
    for country in ("France", "Spain", "Italy"):
        result = LlmSource(client=LedgerClient()).fetch(
            f"commercial airports in {country}", attrs=["name"], budget=priced
        )
        print(
            f"  {country:7} {len(result)} record(s)"
            f"  outcome={result.meta.outcome}"
            f"  stopped_by={result.meta.stopped_by}"
            f"  spent={round(priced.cost, 6)}"
        )
    print(
        "  0.0024 settled after two calls, and the third reservation of 0.002 does not fit"
    )


def main() -> None:
    rows_across_calls()
    calls_and_cost_across_calls()


if __name__ == "__main__":
    main()
