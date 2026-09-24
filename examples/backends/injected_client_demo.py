"""The second client axis: an injected client the source can actually bound.

An ``llm_fn`` is opaque to the source. An injected client can declare its capabilities:
implement ``complete``/``acomplete``, declare which ceilings it supports through ``budget_capabilities()``, claim against the
run's ``Budget`` on every provider attempt, and append a ``Usage`` to the per-call
``usage_sink``. The source can then report a model name, token counts, settled cost, and
``accounting_known=True``. Cost and call ceilings can bind rather than being refused.

``LedgerClient`` below is a local double, not a provider. It returns a canned response
and settles an invented price, so the numbers are specific to this script. The accounting
path is the same one ``DefaultLlmClient`` uses for reservation, settlement, and usage.

Run from the repository root:
    python3 examples/backends/injected_client_demo.py
"""

import asyncio
import json
from typing import Any

from blue_data_llm_source import Budget, Capability, ConfigError, LlmSource, Usage

BOUND_PER_ATTEMPT = 0.0020
ACTUAL_PER_CALL = 0.0011


class LedgerClient:
    """A client that prices itself, so every ceiling this source offers can bind."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def bound_for_attempt(self, prompt: str, properties: dict[str, Any]) -> float:
        """The most one attempt can cost. `estimate` and the reservation both read this."""
        return BOUND_PER_ATTEMPT

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return asyncio.run(self.acomplete(prompt, properties))

    async def acomplete(
        self, prompt: str, properties: dict[str, Any] | None = None
    ) -> str:
        props = properties or {}
        budget = props.get("budget") or Budget()
        with budget.attempt(BOUND_PER_ATTEMPT) as reservation:
            self.prompts.append(prompt)
            reservation.settle(ACTUAL_PER_CALL)
        sink = props.get("usage_sink")
        if isinstance(sink, list):
            sink.append(
                Usage(
                    model="ledger-demo-1",
                    prompt_tokens=188,
                    completion_tokens=41,
                    cost=ACTUAL_PER_CALL,
                )
            )
        return json.dumps([{"country": "France"}, {"country": "Germany"}])


def main() -> None:
    client = LedgerClient()
    source = LlmSource(client=client)

    result = source.fetch("two member states of the European Union", attrs=["country"])
    meta = result.meta
    print("records          ", result.records)
    print("model            ", meta.model)
    print("calls            ", meta.calls)
    print("prompt tokens    ", meta.prompt_tokens)
    print("completion tokens", meta.completion_tokens)
    print("cost             ", round(meta.cost, 6))
    print("accounting known ", meta.accounting_known)

    print()
    print("A cost ceiling binds this client, because it declared the cost axis:")
    capped = source.fetch(
        "two member states of the European Union",
        attrs=["country"],
        properties={"limits.max_cost": 0.001},
    )
    print("  outcome  ", capped.meta.outcome)
    print("  stopped  ", capped.meta.stopped_by)
    print("  records  ", capped.records)
    print("  the reservation is the bound, 0.002, which does not fit under 0.001")

    print()
    print("The same ceiling over an llm_fn is refused rather than reported:")
    try:
        LlmSource().fetch(
            "two member states of the European Union",
            attrs=["country"],
            llm_fn=lambda prompt: '[{"country": "France"}]',
            properties={"limits.max_cost": 0.001},
        )
    except ConfigError as error:
        print(" ", error)


if __name__ == "__main__":
    main()
