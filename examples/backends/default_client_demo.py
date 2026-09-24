"""A provider-backed fetch through DefaultLlmClient, with real accounting.

This is the only backend that spends money. ``DefaultLlmClient`` drives the provider
through litellm, declares every budget axis, and prices each attempt from the table in
``llm/pricing.py``. The result carries the model name, provider token counts, settled
cost, and ``accounting_known=True``. The offline demos cannot produce these values.

The library reads no credential itself. The provider SDK reads ``OPENAI_API_KEY`` from
the environment. If the key is missing or invalid, the call raises a ``TransportError``
that mentions credentials rather than a configuration error.

``limits.max_cost`` is set above the floor implied by the output allowance alone.
``llm.max_tokens`` at the model's output rate makes up most of the bound, so a lower
ceiling refuses every call before it starts.

Requires: OPENAI_API_KEY, and the provider extra (pip install 'blue-data-llm-source[llm]').

Run from the repository root:
    python3 examples/backends/default_client_demo.py
"""

from blue_data_llm_source import DefaultLlmClient, LlmSource, LlmSourceError

PROPERTIES = {
    "llm.model": "gpt-4o-mini",
    "llm.max_tokens": 512,
    "llm.temperature": 0,
    "limits.max_cost": 0.05,
    "limits.max_calls": 2,
}


def main() -> None:
    # Injected rather than left to the source to build, so `close()` releases the cache
    # backends this client opens. A short script does not need it; a long-lived service
    # that rotates cache directories does, and nothing else releases those handles.
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
            return

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


if __name__ == "__main__":
    main()
