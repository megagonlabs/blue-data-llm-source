"""A genuine upper bound on what one call can cost, or a refusal to guess.

A cost ceiling claimed before spend is only as good as the number claimed. If the estimate
can be exceeded, the ceiling is crossed by the call that crosses it, which is the behaviour
reserving was introduced to remove. So this module either produces a bound that cannot be
exceeded, or says it cannot and lets the caller be refused.

**Tokens never outnumber UTF-8 bytes.** Byte-pair tokenizers merge byte sequences, so every
token consumes at least one byte of the encoded prompt and ``len(prompt.encode("utf-8"))``
bounds the token count for any of them.

The bound was written against *characters* first, on the reasoning that a token consumes at
least one character. That is false. A tokenizer splits a character whose encoding it has no
merge for, so a family emoji of seven code points can cost eleven tokens; measured, a
1,400-character sequence produced 2,200 tokens against a reservation of fewer. Bytes are the
unit the tokenizer actually consumes, and the claim holds there.

It is loose — English runs three to four bytes per token — and loose in the only safe
direction. A bound that is usually right is a ceiling that is occasionally crossed, and the
difference between the two is the whole reason this module exists.

Two things must be known before a bound exists at all: the model's prices, and a finite cap
on how much output it may produce. Neither can be assumed. A model absent from the table
may be priced above everything in it, and a request with no output cap can produce output
until the provider stops it. Where either is missing, :func:`bound_cost` raises rather than
inventing a figure, and the source refuses the cost ceiling instead of appearing to hold one.

Prices are published list prices, in USD per token, and are a snapshot. They go stale in the
safe direction — providers cut prices far more often than they raise them — but a caller
running a model this table does not know is refused rather than bounded against a guess.
Supply ``llm.price_per_input_token`` and ``llm.price_per_output_token`` to bound a model the
table does not carry.
"""

from __future__ import annotations

import math
import re

from blue_data_llm_source.types import ConfigError

# Chat framing the provider adds around the prompt: role labels, separators, and the
# per-message overhead. Generous, because it is added to a bound and not to an estimate.
FRAMING_TOKENS = 64

# USD per token, (input, output), from published list prices. Matched exactly, or against a
# recognised version suffix. An open prefix match let ``gpt-4o-miniature-premium`` inherit
# ``gpt-4o-mini``'s rates and ``o1-pro`` inherit ``o1``'s, both of which under-price: a
# longer name is a different model, not a variant of the one whose name it starts with.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
    "gpt-4o": (2.50 / 1_000_000, 10.00 / 1_000_000),
    "gpt-4-turbo": (10.00 / 1_000_000, 30.00 / 1_000_000),
    "gpt-4": (30.00 / 1_000_000, 60.00 / 1_000_000),
    "gpt-3.5-turbo": (0.50 / 1_000_000, 1.50 / 1_000_000),
    "o1": (15.00 / 1_000_000, 60.00 / 1_000_000),
    "o3-mini": (1.10 / 1_000_000, 4.40 / 1_000_000),
    "claude-3-5-haiku": (0.80 / 1_000_000, 4.00 / 1_000_000),
    "claude-3-5-sonnet": (3.00 / 1_000_000, 15.00 / 1_000_000),
    "claude-3-opus": (15.00 / 1_000_000, 75.00 / 1_000_000),
}


# A dated or regional variant of a listed model: the exact name, then a hyphen, then only
# digits and hyphens. Anything with letters after the name is a different model.
_VERSION_SUFFIX = re.compile(r"^-[\d-]+$")


def rates_for(model: str) -> tuple[float, float] | None:
    """Published input and output price per token, or None for a model not in the table.

    Exact names and dated variants only. Refusing an unrecognised name is what makes the
    caller-supplied rates worth having; guessing from a prefix is how a dearer model gets
    priced as a cheaper one whose name it happens to extend.
    """
    name = (model or "").lower()
    if name in PRICES:
        return PRICES[name]
    for known, rates in PRICES.items():
        if name.startswith(known) and _VERSION_SUFFIX.match(name[len(known) :]):
            return rates
    return None


def priceable_models() -> tuple[str, ...]:
    """The model names this package carries a published price for, sorted.

    A tuple rather than the table itself: ``PRICES`` is a mutable snapshot of
    time-sensitive list prices, and handing it out as a public surface would make a
    contract this package cannot keep across a price change. What a caller needs is the
    names it can bound a ceiling against, which is this.
    """
    return tuple(sorted(PRICES))


def positive_finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        # An integer too large for a float is not a usable ceiling, and letting the raw
        # OverflowError out would break the promise that every bad value names its field.
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def resolve_rates(model: str, properties: dict[str, object] | None = None) -> tuple[float, float]:
    """The prices to bound *model* against, from the caller or the table.

    Caller-supplied rates win, so a model this table has never heard of can still be
    bounded by someone who knows what it costs.
    """
    supplied = properties or {}
    raw_in = supplied.get("llm.price_per_input_token")
    raw_out = supplied.get("llm.price_per_output_token")
    # Presence, not validity, decides whether the caller meant to override. Falling back to
    # the table when both values happened to be unusable accepted an explicit override and
    # ignored it, which against a stale table makes the bound too low.
    if raw_in is not None or raw_out is not None:
        given_in = positive_finite(raw_in)
        given_out = positive_finite(raw_out)
        if given_in is None or given_out is None:
            raise ConfigError(
                "llm.price_per_input_token and llm.price_per_output_token must both be set "
                "to finite positive numbers, since a bound needs both halves and a rate that "
                "cannot be read is not a rate"
            )
        return given_in, given_out
    published = rates_for(model)
    if published is None:
        raise ConfigError(
            f"no published prices for model {model!r}, so a cost ceiling cannot be bounded "
            f"before the call. Set llm.price_per_input_token and llm.price_per_output_token, "
            f"or drop limits.max_cost and bound the run on calls, time or rows instead"
        )
    return published


def bound_cost(
    prompt: str,
    model: str,
    max_tokens: object,
    properties: dict[str, object] | None = None,
) -> float:
    """The most one call on these arguments can cost. Raises when that cannot be established.

    Raising is the point. A figure invented for a model whose price is unknown, or for a
    request whose output is uncapped, is not a bound, and a ceiling claimed against it is a
    ceiling that gets crossed.
    """
    output_cap = positive_finite(max_tokens)
    if output_cap is None:
        raise ConfigError(
            "limits.max_cost needs a finite positive llm.max_tokens: without a cap on the "
            "output, no figure bounds what the call can cost"
        )
    input_rate, output_rate = resolve_rates(model, properties)
    # Bytes, not characters: a byte-pair tokenizer consumes at least one byte per token, and
    # can emit more tokens than the prompt has characters when it has no merge for them.
    prompt_tokens = len(prompt.encode("utf-8")) + FRAMING_TOKENS
    return prompt_tokens * input_rate + output_cap * output_rate


def can_bound(model: str, max_tokens: object, properties: dict[str, object] | None = None) -> bool:
    """Whether a cost ceiling could be claimed for these arguments."""
    try:
        bound_cost("", model, max_tokens, properties)
    except ConfigError:
        return False
    return True
