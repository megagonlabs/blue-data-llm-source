"""Estimate a request, decide whether to run it, then reconcile its actual cost.

This example shows the sequence an operator can use when deciding whether to spend.

1. ``estimate`` bounds the request without contacting anything.
2. The bound is checked against a ceiling the caller owns. Too big, and nothing is spent.
3. A cheaper shape of the same request -- a smaller output allowance -- is sized again.
4. The fetch runs under a ``Budget`` carrying that ceiling.
5. The settled cost is reconciled against the reservation the estimate reported.

``estimate`` and the run agree by construction: both ask the client the same question,
``bound_for_attempt``, so the figure a caller sizes against and the figure a ceiling
enforces cannot be computed two different ways. What a caller has to remember is that the
estimate bounds a *flat* fetch -- it reads no ``plan.*`` setting -- so a decomposed
request can cost several times it.

``LedgerClient`` is a local double: it returns canned records and settles an invented
price. Its accounting path is the real one, the same reservation and settlement
``DefaultLlmClient`` uses, so the arithmetic below is the library's rather than this
script's. ``backends/default_client_demo.py`` is the same sequence against a provider.

Run from the repository root:
    python3 examples/integration-demos/estimate_then_fetch_demo.py
"""

import asyncio
import json
from typing import Any

from blue_data_llm_source import Budget, Capability, Estimate, Limits, LlmSource, Usage

QUERY = "commercial airports in metropolitan France"
ATTRS = ["name", "iata_code", "city"]
CEILING = 0.004

RECORDS = json.dumps(
    [
        {"name": "Charles de Gaulle", "iata_code": "CDG", "city": "Paris"},
        {"name": "Orly", "iata_code": "ORY", "city": "Paris"},
        {"name": "Nice Cote d'Azur", "iata_code": "NCE", "city": "Nice"},
    ]
)


class LedgerClient:
    """Prices an attempt from the prompt, then settles at a fraction of that bound.

    A real client's bound comes from the rate table and its settlement from the provider's
    own accounting. Both are stand-ins here; the lifecycle around them is not.
    """

    RATE_PER_BYTE = 0.0000012
    SETTLES_AT = 0.55

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def bound_for_attempt(self, prompt: str, properties: dict[str, Any]) -> float:
        allowance = properties.get("llm.max_tokens", 4096)
        return round(
            len(prompt.encode("utf-8")) * self.RATE_PER_BYTE + int(allowance) * 1e-6, 8
        )

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return asyncio.run(self.acomplete(prompt, properties))

    async def acomplete(
        self, prompt: str, properties: dict[str, Any] | None = None
    ) -> str:
        props = properties or {}
        budget = props.get("budget") or Budget()
        bound = self.bound_for_attempt(prompt, props)
        with budget.attempt(bound) as reservation:
            settled = round(bound * self.SETTLES_AT, 8)
            reservation.settle(settled)
        sink = props.get("usage_sink")
        if isinstance(sink, list):
            sink.append(
                Usage(
                    model="ledger-demo-1",
                    prompt_tokens=196,
                    completion_tokens=64,
                    cost=settled,
                )
            )
        return RECORDS


def size(source: LlmSource, label: str) -> Estimate:
    sized = source.estimate(QUERY, attrs=ATTRS)
    print(f"{label}")
    print("  bounded         ", sized.bounded)
    print("  prompt bytes    ", sized.prompt_bytes)
    print("  output allowance", sized.max_output_tokens)
    print("  attempts allowed", sized.max_attempts)
    print("  cost per attempt", sized.max_cost_per_attempt)
    print("  max cost        ", sized.max_cost)
    return sized


def main() -> None:
    client = LedgerClient()

    print(f"the ceiling this caller is willing to spend on one request: {CEILING}")
    print()

    generous = LlmSource(properties={"llm.max_tokens": 4096}, client=client)
    asked = size(generous, "1. the request as first written")
    print(
        f"  over the ceiling: {asked.max_cost} > {CEILING}, so nothing is spent on it"
    )
    print(
        "  max_cost is the whole fetch, retries included; sizing a ceiling against the"
    )
    print(
        "  per-attempt figure means one transient failure consumes it and the retry is"
    )
    print("  refused.")
    print()

    thrifty = LlmSource(properties={"llm.max_tokens": 256}, client=client)
    affordable = size(
        thrifty, "2. the same request with an output allowance the answer needs"
    )
    print(f"  under the ceiling: {affordable.max_cost} <= {CEILING}, retries and all")
    print()

    print("3. run it under a budget carrying that ceiling")
    run = Budget(Limits(max_cost=CEILING))
    result = thrifty.fetch(QUERY, attrs=ATTRS, budget=run)
    for record in result.records:
        print("    ", record)

    meta = result.meta
    print()
    print("4. reconcile")
    print("  reserved per attempt", affordable.max_cost_per_attempt)
    print("  calls               ", meta.calls)
    print("  settled cost        ", round(meta.cost, 8))
    print("  accounting known    ", meta.accounting_known)
    print("  outcome             ", meta.outcome)
    print("  gaps                ", meta.gaps)
    print("  budget committed    ", round(run.committed, 8))
    print("  budget headroom     ", round(CEILING - run.committed, 8))
    print()
    print(
        "The overshoot between the reservation and the settled cost is released back to"
    )
    print(
        "the budget, so the next request under it is sized against what is really left."
    )


if __name__ == "__main__":
    main()
