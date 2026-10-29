"""A provider-backed fetch through DefaultLlmClient.

``DefaultLlmClient`` calls the provider through LiteLLM and bounds each attempt
using rates from ``llm/pricing.py``. Settlement uses LiteLLM's readable cost
figure when supported and a conservative charge otherwise. This example can
incur provider charges. The result prints the model, token counts, cost, and
``accounting_known``; the last field distinguishes measured cost from a
conservative charge.

The provider SDK reads ``OPENAI_API_KEY`` to authenticate. The library also reads
credential values for redaction; it does not use them to authenticate. An
authentication failure may raise ``TransportError`` or return a stopped result
when the call ceiling is reached. The script exits unsuccessfully in either case.

``limits.max_cost`` is set above the floor implied by the output allowance alone.
``llm.max_tokens`` at the model's output rate makes up most of the bound, so a lower
ceiling refuses every call before it starts.

Requires: OPENAI_API_KEY, and the provider extra (pip install 'blue-data-llm-source[llm]').

Run from the repository root:
    python3 examples/backends/default_client_demo.py
"""

import os

from blue_data_llm_source import DefaultLlmClient, LlmSource, LlmSourceError, Outcome

PROPERTIES = {
    "llm.model": "gpt-4o-mini",
    "llm.max_tokens": 512,
    "llm.temperature": 0,
    "limits.max_cost": 0.05,
    "limits.max_calls": 2,
}


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY before running this example.")
    # The context manager closes any cache backends opened by this client.
    with DefaultLlmClient(PROPERTIES) as client:
        source = LlmSource(properties=PROPERTIES, client=client)

        sized = source.estimate(
            "commercial airports in metropolitan France",
            attrs=["name", "iata_code"],
        )
        print("estimate bounded ", sized.bounded)
        print("estimate max cost", sized.max_cost)
        print("estimate model   ", sized.model)

        try:
            result = source.fetch(
                "commercial airports in metropolitan France",
                attrs=[
                    {"name": "name", "type": "string"},
                    {"name": "iata_code", "type": "string"},
                ],
            )
        except LlmSourceError as error:
            print(f"{type(error).__name__}: {error}")
            return 1

        print()
        print("records")
        for record in result.records:
            print(" ", record)

        meta = result.meta
        print()
        print("model            ", meta.model)
        print("calls            ", meta.calls)
        print("prompt tokens    ", meta.prompt_tokens)
        print("completion tokens", meta.completion_tokens)
        print("cost             ", round(meta.cost, 6))
        print("latency seconds  ", round(meta.latency_seconds, 3))
        print("cache hits       ", meta.cache_hits)
        print("accounting known ", meta.accounting_known)
        print("outcome          ", meta.outcome)
        print("gaps             ", meta.gaps)
        print()
        print(
            "settled cost against the estimate:",
            round(meta.cost, 6),
            "of",
            sized.max_cost,
        )
        return 0 if meta.outcome == Outcome.OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
