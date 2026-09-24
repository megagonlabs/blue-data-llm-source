"""estimate(): what one flat fetch could cost, answered without contacting anything.

``estimate`` builds and measures the prompt this source would send, then calculates a
bound using the published rates in ``llm/pricing.py``. It does not contact a provider,
so this script needs no credential. A planner can use it before deciding how widely to
fan out, and an operator can use it before allowing a request.

It has two limitations. It bounds a *flat* fetch, so a request with parts
or continuations can cost several times the figure; pass any ``plan.*`` setting and the
same numbers come back. The estimate does not predict the provider's invoice: the rate
table is a static snapshot of list prices with no effective date.

Run from the repository root:
    python3 examples/feature-demos/estimate_demo.py
"""

from blue_data_llm_source import LlmSource

QUERY = "commercial airports in metropolitan France"
ATTRS = ["name", "iata_code", "city"]


def show(label: str, source: LlmSource, llm_fn: object = None) -> None:
    sized = source.estimate(QUERY, attrs=ATTRS, llm_fn=llm_fn)
    print(label)
    print("  bounded            ", sized.bounded)
    print("  model              ", sized.model)
    print("  prompt bytes       ", sized.prompt_bytes)
    print("  max output tokens  ", sized.max_output_tokens)
    print("  max attempts       ", sized.max_attempts)
    print("  cost per attempt   ", sized.max_cost_per_attempt)
    print("  max cost           ", sized.max_cost)
    if sized.reason:
        print("  reason             ", sized.reason)
    print()


def main() -> None:
    show(
        "a priced model, the shipped retry allowance of three attempts",
        LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}),
    )

    print("the floor is the output allowance alone; the prompt contributes little")
    for allowance in (256, 512, 4096):
        sized = LlmSource(
            properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": allowance}
        ).estimate(QUERY, attrs=ATTRS)
        print(f"  llm.max_tokens={allowance:<5} max_cost={sized.max_cost}")
    print("  a limits.max_cost under that floor refuses every call before one is made")
    print()

    show(
        "a model the rate table has never heard of: a reason, not a guess",
        LlmSource(properties={"llm.model": "some-private-deployment"}),
    )

    show(
        "the same model, with rates the caller maintains",
        LlmSource(
            properties={
                "llm.model": "some-private-deployment",
                "llm.max_tokens": 512,
                "llm.price_per_input_token": 0.0000004,
                "llm.price_per_output_token": 0.0000016,
            }
        ),
    )

    show(
        "an llm_fn cannot price itself, so the request against one is unestimable",
        LlmSource(),
        llm_fn=lambda prompt: "[]",
    )

    print("plan.* settings are not read: a decomposed request is not what this bounds")
    source = LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 512})
    flat = source.estimate(QUERY, attrs=ATTRS)
    split = source.estimate(
        QUERY,
        attrs=ATTRS,
        properties={
            "plan.mode": "partitioned",
            "plan.key": "name",
            "plan.max_leaves": 12,
        },
    )
    print("  flat       ", flat.max_cost)
    print("  partitioned", split.max_cost)
    print("  identical, and the partitioned run can cost many times it")
    print()

    empty = source.estimate("", attrs=ATTRS)
    print("an empty request never reaches a model, so it is bounded at zero")
    print("  bounded", empty.bounded, " max cost", empty.max_cost)


if __name__ == "__main__":
    main()
